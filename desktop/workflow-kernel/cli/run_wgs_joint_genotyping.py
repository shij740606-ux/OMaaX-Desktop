#!/usr/bin/env python3
"""Run Stage 08 joint genotyping from authoritative WGS Manifests."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import gzip
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


STAGE = "wgs_joint_genotyping"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Joint-genotype the authoritative Stage07 cohort GVCF"
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--combine-manifest", required=True)
    parser.add_argument("--index-manifest", required=True)
    parser.add_argument("--gatk", default="gatk")
    parser.add_argument("--max-alternate-alleles", type=int, default=6)
    parser.add_argument("--calling-threshold", type=int, default=10)
    parser.add_argument(
        "--include-non-variant-sites",
        type=lambda value: str(value).strip().lower() == "true",
        default=False,
    )
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


def _authority_header(
    path: str | Path,
    *,
    stage: str,
    workspace: Path,
    prefix: str,
    job_id: str,
) -> dict[str, Any]:
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{stage} authority Manifest is unreadable") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("stage") != stage
        or manifest.get("status") != "success"
    ):
        raise ValueError(
            f"{stage} authority Manifest is not a successful producer"
        )
    if (
        manifest.get("prefix") != prefix
        or manifest.get("job_id") != job_id
        or manifest.get("workspace") != str(workspace)
    ):
        raise ValueError(f"{stage} authority Manifest identity mismatch")
    return manifest


def _invocation_path(command: str) -> Path:
    resolved = shutil.which(command)
    if not resolved:
        raise RuntimeError(f"GATK executable is unavailable: {command}")
    return Path(resolved).absolute()


def _executable_path(command: str) -> Path:
    return _invocation_path(command).resolve()


def _checked(command: list[str], name: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    executable_parent = str(_invocation_path(command[0]).parent)
    environment["PATH"] = (
        executable_parent + os.pathsep + environment.get("PATH", "")
    )
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    if result.returncode:
        raise RuntimeError(
            f"{name} failed with exit code {result.returncode}: "
            f"{result.stderr.strip()}"
        )
    return result


def _tool_version(gatk: str) -> str:
    result = _checked([gatk, "--version"], "GATK version check")
    lines = (result.stdout or result.stderr).strip().splitlines()
    if not lines:
        raise RuntimeError("GATK version check produced no version")
    return lines[0]


def _run_logged(command: list[str], log: Any, label: str) -> None:
    log.write(f"{label} argv: {json.dumps(command)}\n")
    log.flush()
    result = subprocess.run(
        command,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        env={
            **os.environ,
            "PATH": str(_invocation_path(command[0]).parent)
            + os.pathsep
            + os.environ.get("PATH", ""),
        },
    )
    log.write(f"{label} exit={result.returncode}\n")
    log.flush()
    if result.returncode:
        raise RuntimeError(f"{label} failed with exit code {result.returncode}")


def _vcf_header(
    path: Path, *, require_non_ref: bool = False
) -> tuple[list[str], str]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("GVCF is missing or empty")
    samples: list[str] | None = None
    interval = ""
    non_ref_declared = False
    records = 0
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.rstrip("\n")
                if line.startswith("##ALT=<ID=NON_REF,"):
                    non_ref_declared = True
                elif line.startswith("#CHROM\t"):
                    fields = line.split("\t")
                    if len(fields) < 10:
                        raise RuntimeError("GVCF has no sample columns")
                    samples = fields[9:]
                elif line and not line.startswith("#"):
                    fields = line.split("\t")
                    if len(fields) < 10:
                        raise RuntimeError("VCF record is invalid")
                    if require_non_ref and "<NON_REF>" not in fields[4].split(","):
                        raise RuntimeError("combined GVCF record is invalid")
                    try:
                        position = int(fields[1])
                    except ValueError as exc:
                        raise RuntimeError(
                            "GVCF record position is invalid"
                        ) from exc
                    if not fields[0] or position < 1:
                        raise RuntimeError("GVCF record position is invalid")
                    if not interval:
                        interval = f"{fields[0]}:{position}-{position}"
                    records += 1
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("GVCF is unreadable") from exc
    if (
        samples is None
        or not samples
        or len(samples) != len(set(samples))
        or (require_non_ref and not non_ref_declared)
        or records < 1
        or not interval
    ):
        raise RuntimeError("VCF content is incomplete")
    return samples, interval


def _validate_with_index(
    gatk: str,
    reference: Path,
    gvcf: Path,
    index: Path,
    expected_samples: list[str],
    log: Any,
    label: str,
    *,
    require_non_ref: bool = False,
) -> None:
    if index != Path(f"{gvcf}.tbi"):
        raise RuntimeError(f"{label} index is not the declared VCF sidecar")
    if not index.is_file() or index.stat().st_size <= 0:
        raise RuntimeError(f"{label} index is missing or empty")
    samples, interval = _vcf_header(
        gvcf, require_non_ref=require_non_ref
    )
    if samples != expected_samples:
        raise RuntimeError(f"{label} sample header mismatch")
    _run_logged(
        [
            gatk,
            "ValidateVariants",
            "-R",
            str(reference),
            "-V",
            str(gvcf),
            "-L",
            interval,
            "--validation-type-to-exclude",
            "ALLELES",
        ],
        log,
        label,
    )


def _artifact(
    workspace: Path,
    source: Path,
    destination: Path,
    role: str,
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
        combine_header = _authority_header(
            args.combine_manifest,
            stage="wgs_gvcf_combine",
            workspace=workspace,
            prefix=args.prefix,
            job_id=args.job_id,
        )
        index_header = _authority_header(
            args.index_manifest,
            stage="wgs_index",
            workspace=workspace,
            prefix=args.prefix,
            job_id=args.job_id,
        )
        sample_set_id = str(
            combine_header.get("wgs_sample_set_id") or ""
        )
        reference_id = str(combine_header.get("wgs_reference_id") or "")
        upstream_ids = {
            "wgs_gvcf_combine": str(
                combine_header.get("wgs_analysis_id") or ""
            ),
            "wgs_index": str(index_header.get("wgs_analysis_id") or ""),
        }
        if (
            not sample_set_id
            or not reference_id
            or not all(upstream_ids.values())
            or index_header.get("wgs_reference_id") != reference_id
        ):
            raise ValueError("two-way upstream scientific lineage mismatch")
        producer_upstream = combine_header.get("upstream_wgs_analysis_ids")
        if (
            not isinstance(producer_upstream, dict)
            or producer_upstream.get("wgs_index")
            != upstream_ids["wgs_index"]
        ):
            raise ValueError(
                "wgs_gvcf_combine index lineage does not match "
                "the current wgs_index authority"
            )
        combine = resolve_wgs_artifacts(
            args.combine_manifest,
            "wgs_gvcf_combine",
            ("combined_gvcf", "combined_gvcf_index"),
            sample_set_id,
            reference_id,
            upstream_ids["wgs_gvcf_combine"],
        )
        index = resolve_wgs_artifacts(
            args.index_manifest,
            "wgs_index",
            ("reference_fasta", "reference_fai", "reference_dict"),
            expected_reference_id=reference_id,
            expected_analysis_id=upstream_ids["wgs_index"],
        )
        if any(len(index[role]) != 1 for role in index):
            raise RuntimeError(
                "authoritative index must contain one FASTA, FAI and dictionary"
            )
        if any(len(combine[role]) != 1 for role in combine):
            raise RuntimeError(
                "authoritative combine must contain one GVCF and index"
            )
        metrics = combine_header.get("metrics") or {}
        expected_samples = metrics.get("output_sample_ids")
        if (
            not isinstance(expected_samples, list)
            or not expected_samples
            or any(
                not isinstance(sample, str) or not sample
                for sample in expected_samples
            )
            or expected_samples != sorted(set(expected_samples))
            or metrics.get("input_sample_ids") != expected_samples
        ):
            raise RuntimeError("upstream combined GVCF sample set is incomplete")
        if (
            args.max_alternate_alleles != 6
            or args.calling_threshold != 10
            or args.include_non_variant_sites is not False
        ):
            raise ValueError(
                "joint genotyping parameters must be exactly 6, 10 and false"
            )
        version = _tool_version(args.gatk)
        parameters = {
            "gatk": args.gatk,
            "operation": "GenotypeGVCFs",
            "max_alternate_alleles": args.max_alternate_alleles,
            "calling_threshold": args.calling_threshold,
            "include_non_variant_sites": args.include_non_variant_sites,
        }
        consumed = [
            item.sha256
            for group in (combine, index)
            for values in group.values()
            for item in values
        ]
        analysis_id = build_wgs_analysis_id(
            STAGE,
            consumed,
            parameters,
            {"gatk": version},
            method="gatk_genotypegvcfs",
        )
        attempt = workspace / ".attempts" / run_id
        bundle = attempt / "bundle"
        bundle.mkdir(parents=True, exist_ok=False)
        base = f"{args.prefix}.{STAGE}.cohort"
        joint_vcf = bundle / f"{base}.joint.vcf.gz"
        joint_vcf_index = Path(f"{joint_vcf}.tbi")
        summary = bundle / f"{base}.summary.tsv"
        log_path = bundle / f"{base}.joint.log"
        reference = index["reference_fasta"][0].path
        combined = combine["combined_gvcf"][0].path
        combined_index = combine["combined_gvcf_index"][0].path
        with log_path.open("w", encoding="utf-8") as log:
            _validate_with_index(
                args.gatk,
                reference,
                combined,
                combined_index,
                expected_samples,
                log,
                "ValidateVariants input",
                require_non_ref=True,
            )
            genotype = [
                args.gatk,
                "GenotypeGVCFs",
                "-R",
                str(reference),
                "-V",
                str(combined),
                "-O",
                str(joint_vcf),
                "--max-alternate-alleles",
                str(args.max_alternate_alleles),
                "--standard-min-confidence-threshold-for-calling",
                str(args.calling_threshold),
                "--include-non-variant-sites",
                str(args.include_non_variant_sites).lower(),
            ]
            _run_logged(genotype, log, "GenotypeGVCFs")
            _run_logged(
                [args.gatk, "IndexFeatureFile", "-I", str(joint_vcf)],
                log,
                "IndexFeatureFile",
            )
            _validate_with_index(
                args.gatk,
                reference,
                joint_vcf,
                joint_vcf_index,
                expected_samples,
                log,
                "ValidateVariants",
            )
            _run_logged(
                [
                    args.gatk,
                    "VariantsToTable",
                    "-V",
                    str(joint_vcf),
                    "-F",
                    "CHROM",
                    "-F",
                    "POS",
                    "-F",
                    "REF",
                    "-F",
                    "ALT",
                    "-F",
                    "QUAL",
                    "-O",
                    str(summary),
                ],
                log,
                "VariantsToTable",
            )
        try:
            summary_lines = summary.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise RuntimeError("joint summary is unreadable") from exc
        if (
            len(summary_lines) < 2
            or summary_lines[0] != "CHROM\tPOS\tREF\tALT\tQUAL"
            or any(len(line.split("\t")) != 5 for line in summary_lines[1:])
        ):
            raise RuntimeError("joint summary content is invalid")
        destination = (
            final / ".wgs_joint_genotyping_runs" / args.prefix / run_id
        )
        artifacts = {
            "joint_vcf": _artifact(
                workspace,
                joint_vcf,
                destination / joint_vcf.name,
                "joint_vcf",
            ),
            "joint_vcf_index": _artifact(
                workspace,
                joint_vcf_index,
                destination / joint_vcf_index.name,
                "joint_vcf_index",
            ),
            "joint_summary": _artifact(
                workspace,
                summary,
                destination / summary.name,
                "joint_summary",
            ),
            "joint_log": _artifact(
                workspace,
                log_path,
                destination / log_path.name,
                "joint_log",
            ),
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
            "wgs_sample_set_id": sample_set_id,
            "wgs_reference_id": reference_id,
            "upstream_wgs_analysis_ids": upstream_ids,
            "wgs_analysis_id": analysis_id,
            "parameters": parameters,
            "software": {
                "gatk": {
                    "path": str(_executable_path(args.gatk)),
                    "version": version,
                }
            },
            "metrics": {
                "input_sample_ids": expected_samples,
                "output_sample_ids": expected_samples,
                "total_samples": len(expected_samples),
            },
            "artifacts": artifacts,
        }
        manifest_work = attempt / "manifest.json"
        _atomic_json(manifest_work, manifest)
        if not _publish(
            bundle, destination, manifest_work, authority, started_at
        ):
            raise RuntimeError(
                "stale combine attempt cannot replace a newer successful run"
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
