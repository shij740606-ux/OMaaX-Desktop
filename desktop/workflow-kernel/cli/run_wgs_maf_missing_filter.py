#!/usr/bin/env python3
"""Run Stage 12 MAF/missing filtering from one authoritative imputed SNP VCF."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any
import uuid

from gfa_core.wgs_scientific_identity import (
    build_wgs_analysis_id,
    sha256_file,
)
from gfa_core.wgs_upstream_manifest import resolve_wgs_artifacts


STAGE = "wgs_maf_missing_filter"
ROLES = (
    "final_snp_vcf",
    "final_snp_vcf_index",
    "maf_missing_report",
    "maf_missing_stats",
)
UPSTREAM_ROLES = (
    "imputed_snp_vcf",
    "imputed_snp_vcf_index",
    "impute_report",
    "impute_log",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply final MAF and missing-fraction filters to Stage11 SNPs"
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--impute-manifest", required=True)
    parser.add_argument("--bcftools", default="bcftools")
    parser.add_argument("--maf", type=float, default=0.05)
    parser.add_argument("--max-missing", type=float, default=0.5)
    return parser


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _safe_component(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value or ""):
        raise ValueError(f"{label} must be a non-empty safe path component")


def _invocation_path(command: str) -> Path:
    resolved = shutil.which(command)
    if not resolved:
        raise RuntimeError(f"executable is unavailable: {command}")
    return Path(resolved).absolute()


def _environment(command: str) -> dict[str, str]:
    return {
        **os.environ,
        "PATH": str(_invocation_path(command).parent)
        + os.pathsep
        + os.environ.get("PATH", ""),
    }


def _run_command(command: list[str], label: str) -> str:
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=_environment(command[0]),
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(
            f"{label} failed with exit code {result.returncode}{suffix}"
        )
    return result.stdout


def _bcftools_version(command: str) -> str:
    output = _run_command([command, "--version"], "bcftools version check")
    lines = output.strip().splitlines()
    if not lines:
        raise RuntimeError("bcftools version check produced no version")
    return lines[0]


def _validate_fill_tags(command: str) -> None:
    try:
        _run_command(
            [command, "+fill-tags", "--help"],
            "bcftools +fill-tags capability check",
        )
    except RuntimeError as exc:
        raise RuntimeError(f"bcftools does not support +fill-tags: {exc}") from exc


def _authority_header(
    path: str | Path,
    *,
    workspace: Path,
    prefix: str,
    job_id: str,
) -> dict[str, Any]:
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("wgs_impute authority Manifest is unreadable") from exc
    if not isinstance(manifest, dict):
        raise ValueError("wgs_impute authority Manifest root is invalid")
    if manifest.get("stage") != "wgs_impute":
        raise ValueError("wgs_impute authority producer stage mismatch")
    if (
        manifest.get("status") != "success"
        or manifest.get("method") != "beagle"
        or (manifest.get("parameters") or {}).get("method") != "beagle"
    ):
        raise ValueError("wgs_impute authority Manifest is not a legal success")
    if (
        manifest.get("prefix") != prefix
        or manifest.get("job_id") != job_id
        or manifest.get("workspace") != str(workspace)
    ):
        raise ValueError("wgs_impute authority Manifest identity mismatch")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(UPSTREAM_ROLES):
        raise ValueError("wgs_impute authority artifact roles are incomplete")
    return manifest


def _site_key(record: Any) -> tuple[str, int, str, str]:
    return (
        str(record.contig),
        int(record.pos),
        str(record.ref),
        ",".join(record.alts or ()),
    )


def _record_frequencies(
    record: Any, sample_count: int
) -> tuple[float, float, list[float]]:
    allele_counts: dict[int, int] = {}
    missing_genotypes = 0
    for sample in record.samples.values():
        genotype = sample.get("GT")
        if genotype is None or any(allele is None for allele in genotype):
            missing_genotypes += 1
        for allele in genotype or ():
            if allele is not None:
                allele_counts[int(allele)] = (
                    allele_counts.get(int(allele), 0) + 1
                )
    ordered_counts = sorted(allele_counts.values(), reverse=True)
    allele_number = sum(ordered_counts)
    frequencies = (
        [count / allele_number for count in ordered_counts]
        if allele_number
        else []
    )
    maf = frequencies[1] if len(frequencies) >= 2 else 0.0
    missing_fraction = missing_genotypes / sample_count
    return maf, missing_fraction, frequencies


def _is_snp(record: Any) -> bool:
    return bool(record.alts) and all(
        len(record.ref) == 1 and len(alt) == 1 for alt in record.alts
    )


def _is_pass(record: Any) -> bool:
    filters = set(record.filter.keys())
    return filters == {"PASS"}


def _vcf_profile(
    path: Path,
    *,
    require_index: bool,
) -> tuple[list[str], list[dict[str, Any]]]:
    import pysam

    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("VCF is missing or empty")
    try:
        with pysam.VariantFile(str(path)) as vcf:
            samples = list(vcf.header.samples)
            if not samples or len(samples) != len(set(samples)):
                raise RuntimeError("VCF sample header is invalid")
            records = []
            for record in vcf:
                if not _is_snp(record) or not _is_pass(record):
                    raise RuntimeError(
                        "Stage11 imputed input must contain only PASS SNP records"
                    )
                maf, missing, frequencies = _record_frequencies(
                    record, len(samples)
                )
                records.append(
                    {
                        "key": _site_key(record),
                        "maf": maf,
                        "missing_fraction": missing,
                        "allele_frequencies": frequencies,
                    }
                )
            if not records:
                raise RuntimeError("VCF has no variant records")
            if len({item["key"] for item in records}) != len(records):
                raise RuntimeError("VCF has duplicate variant site keys")
            contig = records[0]["key"][0]
        if require_index:
            with pysam.VariantFile(str(path)) as indexed:
                next(indexed.fetch(contig, 0, None), None)
    except RuntimeError:
        raise
    except Exception as exc:
        qualifier = " or its TBI is not queryable" if require_index else ""
        raise RuntimeError(f"VCF is invalid{qualifier}: {exc}") from exc
    return samples, records


def _canonical_site_keys(raw: Any, label: str) -> list[tuple[str, int, str, str]]:
    if not isinstance(raw, list) or not raw:
        raise RuntimeError(f"wgs_impute {label} site identity is incomplete")
    keys: list[tuple[str, int, str, str]] = []
    for value in raw:
        if (
            not isinstance(value, list)
            or len(value) != 4
            or not isinstance(value[0], str)
            or not isinstance(value[1], int)
            or not isinstance(value[2], str)
            or not isinstance(value[3], str)
        ):
            raise RuntimeError(f"wgs_impute {label} site identity is invalid")
        keys.append((value[0], value[1], value[2], value[3]))
    return keys


def _site_digest(keys: list[tuple[str, int, str, str]]) -> str:
    payload = json.dumps(
        [list(key) for key in keys],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _artifact(
    workspace: Path, source: Path, destination: Path, role: str
) -> dict[str, Any]:
    size = source.stat().st_size
    return {
        "artifact": role,
        "role": role,
        "path": str(destination.relative_to(workspace)),
        "full_path": str(destination.resolve()),
        "type": source.suffix.lstrip(".") or "file",
        "size": size,
        "size_bytes": size,
        "sha256": sha256_file(source),
    }


def _publish(
    bundle: Path,
    destination: Path,
    manifest_work: Path,
    authority: Path,
    started_at: str,
) -> bool:
    lock_path = authority.parent.parent / ".locks" / f"{authority.stem}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if authority.is_file():
            try:
                current = json.loads(authority.read_text(encoding="utf-8"))
                if (
                    current.get("status") == "success"
                    and str(current.get("start_time") or "") >= started_at
                ):
                    return False
            except Exception:
                pass
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(bundle, destination)
        os.replace(manifest_work, authority)
    return True


def _failure(
    args: argparse.Namespace, run_id: str, started_at: str, error: str
) -> dict[str, Any]:
    return {
        "manifest_version": "1.0",
        "stage": STAGE,
        "status": "failed",
        "prefix": args.prefix,
        "job_id": args.job_id,
        "run_id": run_id,
        "start_time": started_at,
        "artifacts": {},
        "errors": [error],
    }


def _publish_failure(
    authority: Path,
    args: argparse.Namespace,
    run_id: str,
    started_at: str,
    error: str,
) -> None:
    lock_path = authority.parent.parent / ".locks" / f"{authority.stem}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        failure_path = (
            authority
            if not authority.exists()
            else authority.parent
            / f"{args.prefix}.{STAGE}.failed.{run_id}.manifest.json"
        )
        _atomic_json(
            failure_path, _failure(args, run_id, started_at, error)
        )


def _validated_parameters(args: argparse.Namespace) -> dict[str, Any]:
    if not 0.0 <= args.maf <= 0.5:
        raise ValueError("maf must be between 0 and 0.5")
    if not 0.0 <= args.max_missing <= 1.0:
        raise ValueError("max-missing must be between 0 and 1")
    if args.maf != 0.05 or args.max_missing != 0.5:
        raise ValueError(
            "approved thresholds are fixed at maf=0.05 and max-missing=0.5"
        )
    return {
        "method": "bcftools",
        "bcftools": args.bcftools,
        "maf": args.maf,
        "max_missing": args.max_missing,
        "max_missing_semantics": "maximum_missing_fraction",
        "maf_semantics": "second_most_common_observed_allele_frequency",
    }


def run(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    run_id = uuid.uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()
    final = workspace / "final"
    final.mkdir(parents=True, exist_ok=True)
    try:
        _safe_component(args.prefix, "prefix")
    except ValueError as exc:
        _atomic_json(
            final / f"{STAGE}.failed.{run_id}.manifest.json",
            _failure(args, run_id, started_at, str(exc)),
        )
        print(f"{STAGE} failed: {exc}", file=sys.stderr)
        return 1
    authority = final / f"{args.prefix}.{STAGE}.manifest.json"
    try:
        parameters = _validated_parameters(args)
        header = _authority_header(
            args.impute_manifest,
            workspace=workspace,
            prefix=args.prefix,
            job_id=args.job_id,
        )
        sample_set_id = str(header.get("wgs_sample_set_id") or "")
        reference_id = str(header.get("wgs_reference_id") or "")
        upstream_analysis_id = str(header.get("wgs_analysis_id") or "")
        if not sample_set_id or not reference_id or not upstream_analysis_id:
            raise ValueError("wgs_impute scientific lineage is incomplete")
        resolved = resolve_wgs_artifacts(
            args.impute_manifest,
            "wgs_impute",
            UPSTREAM_ROLES,
            expected_sample_set_id=sample_set_id,
            expected_reference_id=reference_id,
            expected_analysis_id=upstream_analysis_id,
            expected_method="",
        )
        if any(len(resolved[role]) != 1 for role in resolved):
            raise RuntimeError("authoritative imputed SNP VCF and TBI must be singletons")
        source = resolved["imputed_snp_vcf"][0].path
        source_index = resolved["imputed_snp_vcf_index"][0].path
        if source_index != Path(f"{source}.tbi"):
            raise RuntimeError("imputed SNP VCF index is not its declared sidecar")
        metrics = header.get("metrics")
        if not isinstance(metrics, dict):
            raise RuntimeError("wgs_impute metrics are incomplete")
        expected_samples = metrics.get("output_sample_ids")
        if (
            not isinstance(expected_samples, list)
            or not expected_samples
            or len(expected_samples) != len(set(expected_samples))
            or metrics.get("input_sample_ids") != expected_samples
        ):
            raise RuntimeError("wgs_impute sample set is incomplete")
        input_samples, input_records = _vcf_profile(
            source, require_index=True
        )
        if input_samples != expected_samples:
            raise RuntimeError("wgs_impute sample header mismatch")
        input_keys = [item["key"] for item in input_records]
        declared_input_keys = _canonical_site_keys(
            metrics.get("input_site_keys"), "input"
        )
        declared_output_keys = _canonical_site_keys(
            metrics.get("output_site_keys"), "output"
        )
        if input_keys != declared_input_keys or input_keys != declared_output_keys:
            raise RuntimeError("wgs_impute site identity mismatch")
        expected_records = [
            item
            for item in input_records
            if item["maf"] >= args.maf
            and item["missing_fraction"] <= args.max_missing
        ]
        if not expected_records:
            raise RuntimeError("no usable GWAS SNP variants satisfy the thresholds")
        expected_keys = [item["key"] for item in expected_records]
        bcftools_version = _bcftools_version(args.bcftools)
        _validate_fill_tags(args.bcftools)
        attempt = workspace / ".attempts" / run_id
        bundle = attempt / "bundle"
        bundle.mkdir(parents=True, exist_ok=False)
        refreshed = attempt / "refreshed.tags.vcf.gz"
        base = f"{args.prefix}.{STAGE}.cohort"
        output = bundle / f"{base}.final.snp.vcf.gz"
        output_index = Path(f"{output}.tbi")
        report = bundle / f"{base}.report.md"
        stats = bundle / f"{base}.stats.tsv"
        _run_command(
            [
                args.bcftools,
                "+fill-tags",
                str(source),
                "-Oz",
                "-o",
                str(refreshed),
                "--",
                "-t",
                "MAF",
            ],
            "bcftools +fill-tags",
        )
        expression = (
            'TYPE="snp" && FILTER="PASS" && '
            f"MAF>={args.maf} && N_SAMPLES>0 && "
            f"N_MISSING/N_SAMPLES<={args.max_missing}"
        )
        _run_command(
            [
                args.bcftools,
                "view",
                "-i",
                expression,
                "-Oz",
                "-o",
                str(output),
                str(refreshed),
            ],
            "bcftools view filter",
        )
        _run_command(
            [args.bcftools, "index", "-t", str(output)],
            "bcftools index",
        )
        if not output_index.is_file() or output_index.stat().st_size <= 0:
            raise RuntimeError("final SNP VCF TBI sidecar is missing or empty")
        sample_output = _run_command(
            [args.bcftools, "query", "-l", str(output)],
            "bcftools query samples",
        )
        queried_samples = [
            line for line in sample_output.splitlines() if line
        ]
        if queried_samples != input_samples:
            raise RuntimeError("bcftools query reported a sample set mismatch")
        record_output = _run_command(
            [
                args.bcftools,
                "query",
                "-f",
                r"%CHROM\t%POS\t%REF\t%ALT\t%MAF\n",
                str(output),
            ],
            "bcftools query records",
        )
        bcftools_stats = _run_command(
            [args.bcftools, "stats", str(output)],
            "bcftools stats",
        )
        if not record_output.strip():
            raise RuntimeError("bcftools query found zero usable GWAS variants")
        if not bcftools_stats.strip():
            raise RuntimeError("bcftools stats produced no evidence")
        output_samples, output_records = _vcf_profile(
            output, require_index=True
        )
        output_keys = [item["key"] for item in output_records]
        if output_samples != input_samples:
            raise RuntimeError("final SNP VCF sample header mismatch")
        if output_keys != expected_keys:
            raise RuntimeError(
                "final SNP VCF site keys do not exactly match GT-derived thresholds"
            )
        for item in output_records:
            if (
                item["maf"] < args.maf
                or item["missing_fraction"] > args.max_missing
            ):
                raise RuntimeError(
                    "final SNP VCF record violates MAF/missing thresholds"
                )
        stats.write_text(
            "metric\tvalue\n"
            f"input_sites\t{len(input_records)}\n"
            f"retained_sites\t{len(output_records)}\n"
            f"removed_sites\t{len(input_records) - len(output_records)}\n"
            f"maf_minimum\t{args.maf}\n"
            f"maximum_missing_fraction\t{args.max_missing}\n"
            "\n# bcftools stats\n"
            f"{bcftools_stats.rstrip()}\n",
            encoding="utf-8",
        )
        report.write_text(
            "# WGS final MAF/missing filter\n\n"
            "- input: authoritative Stage11 imputed PASS SNP VCF\n"
            "- MAF: second most common observed allele frequency, recomputed "
            "from called GT alleles\n"
            "- missing fraction: samples whose GT contains any missing allele "
            "divided by all samples\n"
            f"- retained rule: MAF >= {args.maf} and missing fraction <= "
            f"{args.max_missing}\n"
            f"- input sites: {len(input_records)}\n"
            f"- retained sites: {len(output_records)}\n"
            f"- removed sites: {len(input_records) - len(output_records)}\n",
            encoding="utf-8",
        )
        software = {
            "bcftools": {
                "path": str(_invocation_path(args.bcftools).resolve()),
                "version": bcftools_version,
            }
        }
        analysis_id = build_wgs_analysis_id(
            STAGE,
            [
                item.sha256
                for role in sorted(resolved)
                for item in resolved[role]
            ],
            parameters,
            {"bcftools": bcftools_version},
            method="bcftools",
        )
        destination = (
            final / ".wgs_maf_missing_filter_runs" / args.prefix / run_id
        )
        sources = {
            "final_snp_vcf": output,
            "final_snp_vcf_index": output_index,
            "maf_missing_report": report,
            "maf_missing_stats": stats,
        }
        artifacts = {
            role: _artifact(
                workspace, source_path, destination / source_path.name, role
            )
            for role, source_path in sources.items()
        }
        manifest = {
            "manifest_version": "1.0",
            "stage": STAGE,
            "status": "success",
            "prefix": args.prefix,
            "job_id": args.job_id,
            "run_id": run_id,
            "start_time": started_at,
            "workspace": str(workspace),
            "method": "bcftools",
            "wgs_sample_set_id": sample_set_id,
            "wgs_reference_id": reference_id,
            "upstream_wgs_analysis_ids": {
                "wgs_impute": upstream_analysis_id
            },
            "wgs_analysis_id": analysis_id,
            "parameters": parameters,
            "software": software,
            "metrics": {
                "input_sample_ids": input_samples,
                "output_sample_ids": output_samples,
                "total_samples": len(output_samples),
                "input_sites": len(input_records),
                "retained_sites": len(output_records),
                "removed_sites": len(input_records) - len(output_records),
                "input_site_digest": _site_digest(input_keys),
                "retained_site_digest": _site_digest(output_keys),
                "retained_site_keys": [list(key) for key in output_keys],
                "retained_records": [
                    {
                        "chromosome": item["key"][0],
                        "position": item["key"][1],
                        "reference": item["key"][2],
                        "alternate": item["key"][3],
                        "maf": item["maf"],
                        "missing_fraction": item["missing_fraction"],
                        "allele_frequencies": item["allele_frequencies"],
                    }
                    for item in output_records
                ],
            },
            "artifacts": artifacts,
        }
        manifest_work = attempt / "manifest.json"
        _atomic_json(manifest_work, manifest)
        if not _publish(
            bundle, destination, manifest_work, authority, started_at
        ):
            raise RuntimeError(
                "stale MAF/missing attempt cannot replace newer run"
            )
        return 0
    except Exception as exc:
        _publish_failure(authority, args, run_id, started_at, str(exc))
        print(f"{STAGE} failed: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
