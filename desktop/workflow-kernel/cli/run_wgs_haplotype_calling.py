#!/usr/bin/env python3
"""Run Stage 06 per-sample GVCF calling from authoritative WGS Manifests."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
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

from gfa_core.wgs_scientific_identity import build_wgs_analysis_id, sha256_file
from gfa_core.wgs_upstream_manifest import (
    resolve_wgs_artifacts,
    validate_wgs_bam_qc_completion,
)


STAGE = "wgs_haplotype_calling"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Call one indexed GVCF for every authoritative WGS BAM"
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--markdup-manifest", required=True)
    parser.add_argument("--bam-qc-manifest", required=True)
    parser.add_argument("--index-manifest", required=True)
    parser.add_argument("--gatk", required=True)
    parser.add_argument("--native-pair-hmm-threads", required=True, type=int)
    parser.add_argument("--max-jobs", required=True, type=int)
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
        raise ValueError(f"{stage} authority Manifest is not a successful producer")
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
    environment["PATH"] = executable_parent + os.pathsep + environment.get("PATH", "")
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


def _validate_gvcf(path: Path, index: Path, sample_id: str) -> str:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"sample {sample_id}: GVCF is missing or empty")
    if not index.is_file() or index.stat().st_size <= 0:
        raise RuntimeError(f"sample {sample_id}: GVCF index is missing or empty")
    header_samples: list[str] | None = None
    non_ref_declared = False
    data_rows = 0
    first_interval = ""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for raw in handle:
                line = raw.rstrip("\n")
                if line.startswith("##ALT=<ID=NON_REF,"):
                    non_ref_declared = True
                elif line.startswith("#CHROM\t"):
                    fields = line.split("\t")
                    if len(fields) < 10:
                        raise RuntimeError(
                            f"sample {sample_id}: GVCF has no sample column"
                        )
                    header_samples = fields[9:]
                elif line and not line.startswith("#"):
                    fields = line.split("\t")
                    if len(fields) < 10 or "<NON_REF>" not in fields[4].split(","):
                        raise RuntimeError(
                            f"sample {sample_id}: GVCF record is invalid"
                        )
                    try:
                        position = int(fields[1])
                    except ValueError as exc:
                        raise RuntimeError(
                            f"sample {sample_id}: GVCF record position is invalid"
                        ) from exc
                    if not fields[0] or position < 1:
                        raise RuntimeError(
                            f"sample {sample_id}: GVCF record position is invalid"
                        )
                    if not first_interval:
                        first_interval = f"{fields[0]}:{position}-{position}"
                    data_rows += 1
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"sample {sample_id}: GVCF is unreadable") from exc
    if header_samples != [sample_id]:
        raise RuntimeError(
            f"sample {sample_id}: GVCF internal sample identity mismatch"
        )
    if not non_ref_declared or data_rows < 1 or not first_interval:
        raise RuntimeError(f"sample {sample_id}: GVCF content is incomplete")
    return first_interval


def _run_sample(
    sample_id: str,
    input_bam: Path,
    reference: Path,
    bundle: Path,
    args: argparse.Namespace,
) -> dict[str, Path]:
    base = f"{args.prefix}.{STAGE}.{sample_id}"
    gvcf = bundle / f"{base}.g.vcf.gz"
    gvcf_index = Path(f"{gvcf}.tbi")
    log_path = bundle / f"{base}.haplotype.log"
    haplotype = [
        args.gatk,
        "HaplotypeCaller",
        "-R",
        str(reference),
        "-I",
        str(input_bam),
        "-O",
        str(gvcf),
        "-ERC",
        "GVCF",
        "--native-pair-hmm-threads",
        str(args.native_pair_hmm_threads),
        "--create-output-variant-index",
        "false",
    ]
    index = [args.gatk, "IndexFeatureFile", "-I", str(gvcf)]
    with log_path.open("w", encoding="utf-8") as log:
        _run_logged(haplotype, log, "HaplotypeCaller")
        _run_logged(index, log, "IndexFeatureFile")
        interval = _validate_gvcf(gvcf, gvcf_index, sample_id)
        validate = [
            args.gatk,
            "ValidateVariants",
            "-R",
            str(reference),
            "-V",
            str(gvcf),
            "-L",
            interval,
            "--validation-type-to-exclude",
            "ALLELES",
        ]
        _run_logged(validate, log, "ValidateVariants")
    return {
        "gvcf": gvcf,
        "gvcf_index": gvcf_index,
        "haplotype_log": log_path,
    }


def _artifact(
    workspace: Path,
    source: Path,
    destination: Path,
    role: str,
    sample_id: str,
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
        "sample_id": sample_id,
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
                    and str(current.get("start_time") or "") > started_at
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
        _atomic_json(failure_path, _failure(args, run_id, started_at, error))


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
        if args.native_pair_hmm_threads < 1 or args.max_jobs < 1:
            raise ValueError(
                "native-pair-hmm-threads and max-jobs must be positive"
            )
        markdup_header = _authority_header(
            args.markdup_manifest,
            stage="wgs_markdup",
            workspace=workspace,
            prefix=args.prefix,
            job_id=args.job_id,
        )
        bam_qc_header = _authority_header(
            args.bam_qc_manifest,
            stage="wgs_bam_qc",
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
        sample_set_id = str(markdup_header.get("wgs_sample_set_id") or "")
        reference_id = str(markdup_header.get("wgs_reference_id") or "")
        upstream_ids = {
            "wgs_markdup": str(markdup_header.get("wgs_analysis_id") or ""),
            "wgs_bam_qc": str(bam_qc_header.get("wgs_analysis_id") or ""),
            "wgs_index": str(index_header.get("wgs_analysis_id") or ""),
        }
        if not sample_set_id or not reference_id or not all(upstream_ids.values()):
            raise ValueError("upstream scientific identities are missing")
        if (
            bam_qc_header.get("wgs_sample_set_id") != sample_set_id
            or bam_qc_header.get("wgs_reference_id") != reference_id
            or index_header.get("wgs_reference_id") != reference_id
            or bam_qc_header.get("upstream_wgs_analysis_ids")
            != {
                "wgs_markdup": upstream_ids["wgs_markdup"],
                "wgs_index": upstream_ids["wgs_index"],
            }
        ):
            raise ValueError("three-way upstream scientific lineage mismatch")
        markdup = resolve_wgs_artifacts(
            args.markdup_manifest,
            "wgs_markdup",
            ("markdup_bam", "markdup_bam_index"),
            sample_set_id,
            reference_id,
            upstream_ids["wgs_markdup"],
        )
        bam_qc = resolve_wgs_artifacts(
            args.bam_qc_manifest,
            "wgs_bam_qc",
            (
                "bam_flagstat",
                "bam_depth_summary",
                "bam_qc_summary",
                "bam_qc_report",
            ),
            sample_set_id,
            reference_id,
            upstream_ids["wgs_bam_qc"],
        )
        index = resolve_wgs_artifacts(
            args.index_manifest,
            "wgs_index",
            ("reference_fasta",),
            expected_reference_id=reference_id,
            expected_analysis_id=upstream_ids["wgs_index"],
        )
        if len(index["reference_fasta"]) != 1:
            raise RuntimeError(
                "authoritative index must contain one reference FASTA"
            )
        bam_by_sample = {
            item.sample_id: item for item in markdup["markdup_bam"]
        }
        bai_samples = {
            item.sample_id for item in markdup["markdup_bam_index"]
        }
        qc_samples = {
            item.sample_id
            for role in ("bam_flagstat", "bam_depth_summary")
            for item in bam_qc[role]
        }
        expected_samples = set(bam_by_sample)
        validate_wgs_bam_qc_completion(
            bam_qc_header, sorted(expected_samples)
        )
        qc_metrics = bam_qc_header.get("metrics") or {}
        if (
            not expected_samples
            or bai_samples != expected_samples
            or qc_samples != expected_samples
            or set(qc_metrics.get("input_sample_ids") or []) != expected_samples
            or set(qc_metrics.get("output_sample_ids") or []) != expected_samples
        ):
            raise RuntimeError("upstream input sample sets do not match")
        version = _tool_version(args.gatk)
        parameters = {
            "gatk": args.gatk,
            "native_pair_hmm_threads": args.native_pair_hmm_threads,
            "max_jobs": args.max_jobs,
            "emit_reference_confidence": "GVCF",
            "create_output_variant_index": False,
        }
        consumed = [
            item.sha256
            for group in (markdup, bam_qc, index)
            for values in group.values()
            for item in values
        ]
        analysis_id = build_wgs_analysis_id(
            STAGE,
            consumed,
            parameters,
            {"gatk": version},
            method="gatk_haplotypecaller_gvcf",
        )
        attempt = workspace / ".attempts" / run_id
        bundle = attempt / "bundle"
        bundle.mkdir(parents=True, exist_ok=False)
        completed: dict[str, dict[str, Path]] = {}
        failures: list[str] = []
        with ThreadPoolExecutor(max_workers=args.max_jobs) as pool:
            futures = {
                pool.submit(
                    _run_sample,
                    sample,
                    bam_by_sample[sample].path,
                    index["reference_fasta"][0].path,
                    bundle,
                    args,
                ): sample
                for sample in sorted(expected_samples)
            }
            for future in as_completed(futures):
                try:
                    sample = futures[future]
                    completed[sample] = future.result()
                except Exception as exc:
                    failures.append(str(exc))
        if failures or set(completed) != expected_samples:
            raise RuntimeError(
                "; ".join(failures)
                or "haplotype input/output sample-set conservation failed"
            )
        destination = (
            final / ".wgs_haplotype_calling_runs" / args.prefix / run_id
        )
        artifacts = {}
        for sample in sorted(completed):
            for role, source in completed[sample].items():
                artifacts[f"{role}.{sample}"] = _artifact(
                    workspace,
                    source,
                    destination / source.name,
                    role,
                    sample,
                )
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
                "input_sample_ids": sorted(expected_samples),
                "output_sample_ids": sorted(completed),
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
                "stale haplotype attempt cannot replace a newer successful run"
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
