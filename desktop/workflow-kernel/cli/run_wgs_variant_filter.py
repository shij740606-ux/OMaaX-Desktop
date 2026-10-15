#!/usr/bin/env python3
"""Run Stage 10 SNP/INDEL filtering from the authoritative Stage09 VCFs."""

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


STAGE = "wgs_variant_filter"
ROLES = (
    "filtered_snp_vcf",
    "filtered_snp_vcf_index",
    "pass_snp_vcf",
    "pass_snp_vcf_index",
    "filtered_indel_vcf",
    "filtered_indel_vcf_index",
    "pass_indel_vcf",
    "pass_indel_vcf_index",
    "variant_filter_report",
)
GATK_EXPRESSIONS = {
    "SNP": (
        ("QD < 2.0", "QD2"),
        ("QUAL < 30.0", "QUAL30"),
        ("SOR > 3.0", "SOR3"),
        ("FS > 60.0", "FS60"),
        ("MQ < 40.0", "MQ40"),
        ("MQRankSum < -12.5", "MQRankSum-12.5"),
        ("ReadPosRankSum < -8.0", "ReadPosRankSum-8"),
    ),
    "INDEL": (
        ("QD < 2.0", "QD2"),
        ("QUAL < 30.0", "QUAL30"),
        ("FS > 200.0", "FS200"),
        ("ReadPosRankSum < -20.0", "ReadPosRankSum-20"),
    ),
}
BCFTOOLS_EXPRESSIONS = {
    "SNP": (
        "INFO/QD < 2.0 || QUAL < 30.0 || INFO/SOR > 3.0 || "
        "INFO/FS > 60.0 || INFO/MQ < 40.0 || INFO/MQRankSum < -12.5 || "
        "INFO/ReadPosRankSum < -8.0"
    ),
    "INDEL": (
        "INFO/QD < 2.0 || QUAL < 30.0 || INFO/FS > 200.0 || "
        "INFO/ReadPosRankSum < -20.0"
    ),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Filter authoritative Stage09 SNP and INDEL VCFs"
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--select-manifest", required=True)
    parser.add_argument(
        "--method", choices=("gatk", "bcftools"), default="gatk"
    )
    parser.add_argument("--gatk", default="gatk")
    parser.add_argument("--bcftools", default="bcftools")
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
    workspace: Path,
    prefix: str,
    job_id: str,
) -> dict[str, Any]:
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "wgs_variant_select authority Manifest is unreadable"
        ) from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("stage") != "wgs_variant_select"
        or manifest.get("status") != "success"
    ):
        raise ValueError(
            "wgs_variant_select authority Manifest is not successful"
        )
    if (
        manifest.get("prefix") != prefix
        or manifest.get("job_id") != job_id
        or manifest.get("workspace") != str(workspace)
    ):
        raise ValueError(
            "wgs_variant_select authority Manifest identity mismatch"
        )
    return manifest


def _invocation_path(command: str) -> Path:
    resolved = shutil.which(command)
    if not resolved:
        raise RuntimeError(f"executable is unavailable: {command}")
    return Path(resolved).absolute()


def _executable_path(command: str) -> Path:
    return _invocation_path(command).resolve()


def _environment(command: str) -> dict[str, str]:
    return {
        **os.environ,
        "PATH": str(_invocation_path(command).parent)
        + os.pathsep
        + os.environ.get("PATH", ""),
    }


def _checked(
    command: list[str], label: str
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        env=_environment(command[0]),
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            f"{label} failed with exit code {result.returncode}: {detail}"
        )
    return result


def _run_logged(command: list[str], log: Any, label: str) -> None:
    log.write(f"{label} argv: {json.dumps(command)}\n")
    log.flush()
    result = subprocess.run(
        command,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        env=_environment(command[0]),
    )
    log.write(f"{label} exit={result.returncode}\n")
    log.flush()
    if result.returncode:
        raise RuntimeError(f"{label} failed with exit code {result.returncode}")


def _tool_version(command: str, label: str) -> str:
    result = _checked([command, "--version"], f"{label} version check")
    lines = (result.stdout or result.stderr).strip().splitlines()
    if not lines:
        raise RuntimeError(f"{label} version check produced no version")
    return lines[0]


def _vcf_summary(
    path: Path,
    expected_kind: str | None = None,
    *,
    allow_empty: bool = False,
) -> tuple[list[str], str, int, str]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("VCF is missing or empty")
    samples: list[str] | None = None
    interval = ""
    fallback_interval = ""
    region_row = ""
    records = 0
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.rstrip("\n")
                if line.startswith("#CHROM\t"):
                    fields = line.split("\t")
                    if len(fields) < 10:
                        raise RuntimeError("VCF has no sample columns")
                    samples = fields[9:]
                elif line.startswith("##contig=<ID=") and not region_row:
                    contig = line[len("##contig=<ID="):].split(",", 1)[0]
                    if contig:
                        fallback_interval = f"{contig}:1-1"
                        region_row = f"{contig}\t1\t1\n"
                elif line and not line.startswith("#"):
                    fields = line.split("\t")
                    if len(fields) < 10:
                        raise RuntimeError("VCF record is invalid")
                    ref = fields[3]
                    alts = fields[4].split(",")
                    is_snp = (
                        len(ref) == 1
                        and bool(alts)
                        and all(len(alt) == 1 for alt in alts)
                    )
                    is_indel = any(len(alt) != len(ref) for alt in alts)
                    if expected_kind == "SNP" and not is_snp:
                        raise RuntimeError("selected SNP VCF contains non-SNP")
                    if expected_kind == "INDEL" and not is_indel:
                        raise RuntimeError(
                            "selected INDEL VCF contains non-INDEL"
                        )
                    try:
                        position = int(fields[1])
                    except ValueError as exc:
                        raise RuntimeError(
                            "VCF record position is invalid"
                        ) from exc
                    if not fields[0] or position < 1:
                        raise RuntimeError("VCF record position is invalid")
                    if not interval:
                        interval = f"{fields[0]}:{position}-{position}"
                        region_row = (
                            f"{fields[0]}\t{position}\t{position}\n"
                        )
                    records += 1
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("VCF is unreadable") from exc
    if not interval and allow_empty:
        interval = fallback_interval
    if (
        samples is None
        or not samples
        or len(samples) != len(set(samples))
        or (records < 1 and not allow_empty)
        or not interval
        or not region_row
    ):
        raise RuntimeError("VCF content is incomplete")
    return samples, interval, records, region_row


def _query_index(
    bcftools: str,
    vcf: Path,
    region_row: str,
    scratch: Path,
    log: Any,
    label: str,
) -> None:
    regions = scratch / f".{label}.{uuid.uuid4().hex}.regions.tsv"
    regions.write_text(region_row, encoding="utf-8")
    try:
        _run_logged(
            [bcftools, "view", "-H", "-R", str(regions), str(vcf)],
            log,
            f"{label} indexed query",
        )
    finally:
        regions.unlink(missing_ok=True)


def _index_gatk(gatk: str, output: Path, kind: str, phase: str, log: Any) -> None:
    _run_logged(
        [gatk, "IndexFeatureFile", "-I", str(output)],
        log,
        f"GATK IndexFeatureFile {phase} {kind}",
    )


def _filter_gatk(
    gatk: str,
    source: Path,
    filtered: Path,
    passed: Path,
    kind: str,
    log: Any,
) -> None:
    command = [gatk, "VariantFiltration", "-V", str(source), "-O", str(filtered)]
    for expression, name in GATK_EXPRESSIONS[kind]:
        command.extend(
            ["--filter-expression", expression, "--filter-name", name]
        )
    _run_logged(command, log, f"GATK VariantFiltration {kind}")
    _index_gatk(gatk, filtered, kind, "filtered", log)
    _run_logged(
        [
            gatk,
            "SelectVariants",
            "-V",
            str(filtered),
            "-O",
            str(passed),
            "--exclude-filtered",
        ],
        log,
        f"GATK PASS extraction {kind}",
    )
    _index_gatk(gatk, passed, kind, "PASS", log)


def _filter_bcftools(
    bcftools: str,
    source: Path,
    filtered: Path,
    passed: Path,
    kind: str,
    log: Any,
) -> None:
    _run_logged(
        [
            bcftools,
            "filter",
            "-e",
            BCFTOOLS_EXPRESSIONS[kind],
            "-s",
            "HARD_FILTER",
            "-Oz",
            "-o",
            str(filtered),
            str(source),
        ],
        log,
        f"bcftools filter {kind}",
    )
    _run_logged(
        [bcftools, "index", "--tbi", str(filtered)],
        log,
        f"bcftools index filtered {kind}",
    )
    _run_logged(
        [
            bcftools,
            "view",
            "-f",
            "PASS",
            "-Oz",
            "-o",
            str(passed),
            str(filtered),
        ],
        log,
        f"bcftools PASS extraction {kind}",
    )
    _run_logged(
        [bcftools, "index", "--tbi", str(passed)],
        log,
        f"bcftools index PASS {kind}",
    )


def _postflight(
    *,
    method: str,
    gatk: str,
    bcftools: str,
    output: Path,
    index: Path,
    kind: str,
    expected_samples: list[str],
    pass_only: bool,
    log: Any,
) -> int:
    if index != Path(f"{output}.tbi"):
        raise RuntimeError(f"{kind} index is not its VCF sidecar")
    if not index.is_file() or index.stat().st_size <= 0:
        raise RuntimeError(f"{kind} index is missing or empty")
    samples, interval, records, region_row = _vcf_summary(
        output, kind, allow_empty=True
    )
    if samples != expected_samples:
        raise RuntimeError(f"{kind} sample header mismatch")
    if pass_only:
        with gzip.open(output, "rt", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("#"):
                    continue
                if line.rstrip("\n").split("\t")[6] != "PASS":
                    raise RuntimeError(f"{kind} PASS VCF contains non-PASS record")
    if method == "gatk":
        command = [
            gatk,
            "ValidateVariants",
            "-V",
            str(output),
            "-L",
            interval,
        ]
        command.extend(
            [
                "--validation-type-to-exclude", "ALLELES",
                "--validation-type-to-exclude", "REF",
            ]
        )
        _run_logged(
            command,
            log,
            f"GATK ValidateVariants {'PASS' if pass_only else 'filtered'} {kind}",
        )
    else:
        _query_index(
            bcftools, output, region_row, output.parent, log, kind
        )
        _run_logged(
            [bcftools, "stats", str(output)],
            log,
            f"bcftools stats {'PASS' if pass_only else 'filtered'} {kind}",
        )
    return records


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
    payload = {
        "manifest_version": "1.0",
        "stage": STAGE,
        "status": "failed",
        "prefix": args.prefix,
        "job_id": args.job_id,
        "run_id": run_id,
        "start_time": started_at,
        "method": args.method,
        "artifacts": {},
        "errors": [error],
    }
    if (
        args.method == "gatk"
        and bool(getattr(args, "_fallback_eligible", False))
    ):
        payload["fallback_method"] = "bcftools"
    return payload


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
            / (
                f"{args.prefix}.{STAGE}.{args.method}.failed."
                f"{run_id}.manifest.json"
            )
        )
        _atomic_json(
            failure_path, _failure(args, run_id, started_at, error)
        )


def run(args: argparse.Namespace) -> int:
    args._fallback_eligible = False
    workspace = Path(args.workspace).resolve()
    run_id = uuid.uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()
    final = workspace / "final"
    final.mkdir(parents=True, exist_ok=True)
    try:
        _safe_component(args.prefix, "prefix")
    except ValueError as exc:
        _atomic_json(
            final / f"{STAGE}.{args.method}.failed.{run_id}.manifest.json",
            _failure(args, run_id, started_at, str(exc)),
        )
        print(f"{STAGE} failed: {exc}", file=sys.stderr)
        return 1
    authority = (
        final / f"{args.prefix}.{STAGE}.{args.method}.manifest.json"
    )
    try:
        header = _authority_header(
            args.select_manifest,
            workspace=workspace,
            prefix=args.prefix,
            job_id=args.job_id,
        )
        sample_set_id = str(header.get("wgs_sample_set_id") or "")
        reference_id = str(header.get("wgs_reference_id") or "")
        upstream_analysis_id = str(header.get("wgs_analysis_id") or "")
        if not sample_set_id or not reference_id or not upstream_analysis_id:
            raise ValueError(
                "wgs_variant_select scientific lineage is incomplete"
            )
        upstream_method = str(header.get("method") or "")
        if upstream_method not in {"gatk", "bcftools"}:
            raise ValueError("wgs_variant_select method identity is incomplete")
        resolved = resolve_wgs_artifacts(
            args.select_manifest,
            "wgs_variant_select",
            (
                "selected_snp_vcf",
                "selected_snp_vcf_index",
                "selected_indel_vcf",
                "selected_indel_vcf_index",
            ),
            expected_sample_set_id=sample_set_id,
            expected_reference_id=reference_id,
            expected_analysis_id=upstream_analysis_id,
            expected_method=upstream_method,
        )
        if any(len(resolved[role]) != 1 for role in resolved):
            raise RuntimeError(
                "authoritative selected VCFs and TBI must be singletons"
            )
        selected_snp_vcf = resolved["selected_snp_vcf"][0].path
        selected_snp_index = resolved["selected_snp_vcf_index"][0].path
        selected_indel_vcf = resolved["selected_indel_vcf"][0].path
        selected_indel_index = resolved["selected_indel_vcf_index"][0].path
        if (
            selected_snp_index != Path(f"{selected_snp_vcf}.tbi")
            or selected_indel_index != Path(f"{selected_indel_vcf}.tbi")
        ):
            raise RuntimeError("selected VCF index is not its declared sidecar")
        expected_samples = (header.get("metrics") or {}).get(
            "output_sample_ids"
        )
        if (
            not isinstance(expected_samples, list)
            or not expected_samples
            or expected_samples != sorted(set(expected_samples))
            or (header.get("metrics") or {}).get("input_sample_ids")
            != expected_samples
        ):
            raise RuntimeError("selected VCFs sample set is incomplete")
        input_summaries = {
            "SNP": _vcf_summary(selected_snp_vcf, "SNP", allow_empty=True),
            "INDEL": _vcf_summary(
                selected_indel_vcf, "INDEL", allow_empty=True
            ),
        }
        if any(
            summary[0] != expected_samples
            for summary in input_summaries.values()
        ):
            raise RuntimeError("selected VCF sample header mismatch")
        executable = args.gatk if args.method == "gatk" else args.bcftools
        label = "GATK" if args.method == "gatk" else "bcftools"
        try:
            version = _tool_version(executable, label)
        except Exception:
            args._fallback_eligible = args.method == "gatk"
            raise
        parameters = {
            "method": args.method,
            "executable": executable,
            "filter_expressions": (
                {
                    kind: [expression for expression, _ in expressions]
                    for kind, expressions in GATK_EXPRESSIONS.items()
                }
                if args.method == "gatk"
                else BCFTOOLS_EXPRESSIONS
            ),
        }
        analysis_id = build_wgs_analysis_id(
            STAGE,
            [item.sha256 for values in resolved.values() for item in values],
            parameters,
            {args.method: version},
            method=args.method,
        )
        attempt = workspace / ".attempts" / run_id
        bundle = attempt / "bundle"
        bundle.mkdir(parents=True, exist_ok=False)
        base = f"{args.prefix}.{STAGE}.cohort"
        filtered_snp = bundle / f"{base}.filtered.snp.vcf.gz"
        pass_snp = bundle / f"{base}.pass.snp.vcf.gz"
        filtered_indel = bundle / f"{base}.filtered.indel.vcf.gz"
        pass_indel = bundle / f"{base}.pass.indel.vcf.gz"
        report = bundle / f"{args.prefix}.{STAGE}.cohort.report.md"
        log_path = attempt / "variant-filter.log"
        with log_path.open("w", encoding="utf-8") as log:
            args._fallback_eligible = args.method == "gatk"
            inputs = {"SNP": selected_snp_vcf, "INDEL": selected_indel_vcf}
            outputs = {
                "SNP": (filtered_snp, pass_snp),
                "INDEL": (filtered_indel, pass_indel),
            }
            for kind, source in inputs.items():
                interval, region_row = (
                    input_summaries[kind][1],
                    input_summaries[kind][3],
                )
                if args.method == "gatk":
                    _run_logged(
                        [
                            args.gatk,
                            "ValidateVariants",
                            "-V",
                            str(source),
                            "-L",
                            interval,
                            "--validation-type-to-exclude",
                            "ALLELES",
                            "--validation-type-to-exclude",
                            "REF",
                        ],
                        log,
                        f"GATK ValidateVariants input {kind}",
                    )
                else:
                    _query_index(
                        args.bcftools,
                        source,
                        region_row,
                        attempt,
                        log,
                        f"input-{kind}",
                    )
                filtered, passed = outputs[kind]
                if args.method == "gatk":
                    _filter_gatk(
                        args.gatk, source, filtered, passed, kind, log
                    )
                else:
                    _filter_bcftools(
                        args.bcftools, source, filtered, passed, kind, log
                    )
            record_counts: dict[str, int] = {}
            for kind, (filtered, passed) in outputs.items():
                for phase, output in (
                    ("filtered", filtered),
                    ("pass", passed),
                ):
                    record_counts[f"{phase}_{kind.lower()}_records"] = (
                        _postflight(
                            method=args.method,
                            gatk=args.gatk,
                            bcftools=args.bcftools,
                            output=output,
                            index=Path(f"{output}.tbi"),
                            kind=kind,
                            expected_samples=expected_samples,
                            pass_only=phase == "pass",
                            log=log,
                        )
                    )
        args._fallback_eligible = False
        report.write_text(
            "# WGS variant filtering\n\n"
            f"- method: {args.method}\n"
            f"- filter expressions: {json.dumps(parameters['filter_expressions'], sort_keys=True)}\n"
            f"- record counts: {json.dumps(record_counts, sort_keys=True)}\n"
            "- Methods are recorded as executed; scientific equivalence is "
            "not asserted.\n",
            encoding="utf-8",
        )
        destination = (
            final
            / ".wgs_variant_filter_runs"
            / args.prefix
            / args.method
            / run_id
        )
        sources = {
            "filtered_snp_vcf": filtered_snp,
            "filtered_snp_vcf_index": Path(f"{filtered_snp}.tbi"),
            "pass_snp_vcf": pass_snp,
            "pass_snp_vcf_index": Path(f"{pass_snp}.tbi"),
            "filtered_indel_vcf": filtered_indel,
            "filtered_indel_vcf_index": Path(f"{filtered_indel}.tbi"),
            "pass_indel_vcf": pass_indel,
            "pass_indel_vcf_index": Path(f"{pass_indel}.tbi"),
            "variant_filter_report": report,
        }
        artifacts = {
            role: _artifact(
                workspace,
                source,
                destination / source.name,
                role,
            )
            for role, source in sources.items()
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
            "method": args.method,
            "wgs_sample_set_id": sample_set_id,
            "wgs_reference_id": reference_id,
            "upstream_wgs_analysis_ids": {
                "wgs_variant_select": upstream_analysis_id
            },
            "wgs_analysis_id": analysis_id,
            "parameters": parameters,
            "software": {
                args.method: {
                    "path": str(_executable_path(executable)),
                    "version": version,
                }
            },
            "metrics": {
                "input_sample_ids": expected_samples,
                "output_sample_ids": expected_samples,
                "total_samples": len(expected_samples),
                **record_counts,
            },
            "artifacts": artifacts,
        }
        manifest_work = attempt / "manifest.json"
        _atomic_json(manifest_work, manifest)
        if not _publish(
            bundle, destination, manifest_work, authority, started_at
        ):
            raise RuntimeError(
                "stale variant-filter attempt cannot replace newer method run"
            )
        return 0
    except Exception as exc:
        _publish_failure(
            authority, args, run_id, started_at, str(exc)
        )
        print(f"{STAGE} failed: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
