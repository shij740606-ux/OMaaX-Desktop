#!/usr/bin/env python3
"""Run Stage 09 SNP/INDEL selection from the authoritative Stage08 VCF."""

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


STAGE = "wgs_variant_select"
ROLES = (
    "selected_snp_vcf",
    "selected_snp_vcf_index",
    "selected_indel_vcf",
    "selected_indel_vcf_index",
    "variant_select_report",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Select SNPs and INDELs from authoritative Stage08 VCF"
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--joint-manifest", required=True)
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
            "wgs_joint_genotyping authority Manifest is unreadable"
        ) from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("stage") != "wgs_joint_genotyping"
        or manifest.get("status") != "success"
    ):
        raise ValueError(
            "wgs_joint_genotyping authority Manifest is not successful"
        )
    if (
        manifest.get("prefix") != prefix
        or manifest.get("job_id") != job_id
        or manifest.get("workspace") != str(workspace)
    ):
        raise ValueError(
            "wgs_joint_genotyping authority Manifest identity mismatch"
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


def _select_gatk(
    gatk: str,
    source: Path,
    output: Path,
    kind: str,
    log: Any,
) -> None:
    _run_logged(
        [
            gatk,
            "SelectVariants",
            "-V",
            str(source),
            "-O",
            str(output),
            "-select-type-to-include",
            kind,
        ],
        log,
        f"GATK SelectVariants {kind}",
    )
    _run_logged(
        [gatk, "IndexFeatureFile", "-I", str(output)],
        log,
        f"GATK IndexFeatureFile {kind}",
    )


def _select_bcftools(
    bcftools: str,
    source: Path,
    output: Path,
    kind: str,
    log: Any,
) -> None:
    variant_type = "snps" if kind == "SNP" else "indels"
    _run_logged(
        [
            bcftools,
            "view",
            "-v",
            variant_type,
            "-Oz",
            "-o",
            str(output),
            str(source),
        ],
        log,
        f"bcftools select {kind}",
    )
    _run_logged(
        [bcftools, "index", "--tbi", str(output)],
        log,
        f"bcftools index {kind}",
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
            f"GATK ValidateVariants {kind}",
        )
    else:
        _query_index(
            bcftools, output, region_row, output.parent, log, kind
        )
        _run_logged(
            [bcftools, "stats", str(output)],
            log,
            f"bcftools stats {kind}",
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
            args.joint_manifest,
            workspace=workspace,
            prefix=args.prefix,
            job_id=args.job_id,
        )
        sample_set_id = str(header.get("wgs_sample_set_id") or "")
        reference_id = str(header.get("wgs_reference_id") or "")
        upstream_analysis_id = str(header.get("wgs_analysis_id") or "")
        if not sample_set_id or not reference_id or not upstream_analysis_id:
            raise ValueError(
                "wgs_joint_genotyping scientific lineage is incomplete"
            )
        resolved = resolve_wgs_artifacts(
            args.joint_manifest,
            "wgs_joint_genotyping",
            ("joint_vcf", "joint_vcf_index"),
            sample_set_id,
            reference_id,
            upstream_analysis_id,
        )
        if any(len(resolved[role]) != 1 for role in resolved):
            raise RuntimeError(
                "authoritative joint VCF and TBI must be singletons"
            )
        joint_vcf = resolved["joint_vcf"][0].path
        joint_index = resolved["joint_vcf_index"][0].path
        if joint_index != Path(f"{joint_vcf}.tbi"):
            raise RuntimeError("joint VCF index is not its declared sidecar")
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
            raise RuntimeError("joint VCF sample set is incomplete")
        (
            observed_samples,
            joint_interval,
            _,
            joint_region_row,
        ) = _vcf_summary(joint_vcf)
        if observed_samples != expected_samples:
            raise RuntimeError("joint VCF sample header mismatch")
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
            "type_rules": (
                {"SNP": "GATK SNP", "INDEL": "GATK INDEL"}
                if args.method == "gatk"
                else {"SNP": "bcftools snps", "INDEL": "bcftools indels"}
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
        base = f"{args.prefix}.{STAGE}.cohort.selected"
        snp = bundle / f"{base}.snp.vcf.gz"
        indel = bundle / f"{base}.indel.vcf.gz"
        snp_index = Path(f"{snp}.tbi")
        indel_index = Path(f"{indel}.tbi")
        report = bundle / f"{args.prefix}.{STAGE}.cohort.report.md"
        log_path = attempt / "variant-select.log"
        with log_path.open("w", encoding="utf-8") as log:
            args._fallback_eligible = args.method == "gatk"
            if args.method == "gatk":
                _run_logged(
                    [
                        args.gatk,
                        "ValidateVariants",
                        "-V",
                        str(joint_vcf),
                        "-L",
                        joint_interval,
                        "--validation-type-to-exclude",
                        "ALLELES",
                        "--validation-type-to-exclude",
                        "REF",
                    ],
                    log,
                    "GATK ValidateVariants input",
                )
            else:
                _query_index(
                    args.bcftools,
                    joint_vcf,
                    joint_region_row,
                    attempt,
                    log,
                    "input",
                )
            for output, kind in ((snp, "SNP"), (indel, "INDEL")):
                if args.method == "gatk":
                    _select_gatk(
                        args.gatk, joint_vcf, output, kind, log
                    )
                else:
                    _select_bcftools(
                        args.bcftools, joint_vcf, output, kind, log
                    )
            snp_records = _postflight(
                method=args.method,
                gatk=args.gatk,
                bcftools=args.bcftools,
                output=snp,
                index=snp_index,
                kind="SNP",
                expected_samples=expected_samples,
                log=log,
            )
            indel_records = _postflight(
                method=args.method,
                gatk=args.gatk,
                bcftools=args.bcftools,
                output=indel,
                index=indel_index,
                kind="INDEL",
                expected_samples=expected_samples,
                log=log,
            )
        args._fallback_eligible = False
        report.write_text(
            "# WGS variant selection\n\n"
            f"- method: {args.method}\n"
            f"- SNP records: {snp_records}\n"
            f"- INDEL records: {indel_records}\n"
            "- Methods are recorded as executed; scientific equivalence is "
            "not asserted.\n",
            encoding="utf-8",
        )
        destination = (
            final
            / ".wgs_variant_select_runs"
            / args.prefix
            / args.method
            / run_id
        )
        sources = {
            "selected_snp_vcf": snp,
            "selected_snp_vcf_index": snp_index,
            "selected_indel_vcf": indel,
            "selected_indel_vcf_index": indel_index,
            "variant_select_report": report,
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
                "wgs_joint_genotyping": upstream_analysis_id
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
                "snp_records": snp_records,
                "indel_records": indel_records,
            },
            "artifacts": artifacts,
        }
        manifest_work = attempt / "manifest.json"
        _atomic_json(manifest_work, manifest)
        if not _publish(
            bundle, destination, manifest_work, authority, started_at
        ):
            raise RuntimeError(
                "stale variant-select attempt cannot replace newer method run"
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
