#!/usr/bin/env python3
"""Run Stage 04 WGS duplicate removal from an authoritative alignment Manifest."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
from typing import Any

from gfa_core.wgs_scientific_identity import build_wgs_analysis_id, sha256_file
from gfa_core.wgs_upstream_manifest import resolve_wgs_artifacts


STAGE = "wgs_markdup"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Remove duplicates from authoritative WGS alignments"
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--align-manifest", required=True)
    parser.add_argument("--gatk", required=True)
    parser.add_argument("--samtools", required=True)
    parser.add_argument("--max-jobs", required=True, type=int)
    parser.add_argument("--remove-duplicates", required=True, choices=("true",))
    return parser


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _safe_component(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value or ""):
        raise ValueError(f"{label} must be a non-empty safe path component")


def _authority_header(
    path: str | Path, *, workspace: Path, prefix: str, job_id: str
) -> dict[str, Any]:
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("wgs_align authority Manifest is unreadable") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("stage") != "wgs_align"
        or manifest.get("status") != "success"
    ):
        raise ValueError("wgs_align authority Manifest is not a successful producer")
    if (
        manifest.get("prefix") != prefix
        or manifest.get("job_id") != job_id
        or manifest.get("workspace") != str(workspace)
    ):
        raise ValueError("wgs_align authority Manifest identity mismatch")
    return manifest


def _checked(command: list[str], name: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(
            f"{name} failed with exit code {result.returncode}: {result.stderr.strip()}"
        )
    return result


def _tool_version(gatk: str) -> str:
    result = _checked([gatk, "--version"], "GATK version check")
    lines = (result.stdout or result.stderr).strip().splitlines()
    if not lines:
        raise RuntimeError("GATK version check produced no version")
    return lines[0]


def _samtools_version(samtools: str) -> str:
    result = _checked([samtools, "--version"], "samtools version check")
    lines = (result.stdout or result.stderr).strip().splitlines()
    if not lines:
        raise RuntimeError("samtools version check produced no version")
    return lines[0]


def _read_groups(header: str, sample_id: str) -> tuple[tuple[tuple[str, str], ...], ...]:
    groups = []
    for line in header.splitlines():
        if not line.startswith("@RG\t"):
            continue
        fields = {}
        for field in line.split("\t")[1:]:
            if ":" in field:
                key, value = field.split(":", 1)
                fields[key] = value
        groups.append(tuple(sorted(fields.items())))
    if not groups or any(dict(group).get("SM") != sample_id for group in groups):
        raise RuntimeError(f"sample {sample_id}: BAM read group sample identity mismatch")
    return tuple(sorted(groups))


def _validate_metrics(path: Path, sample_id: str) -> None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"sample {sample_id}: markdup metrics are missing or empty")
    lines = [
        line
        for line in path.read_text(encoding="utf-8", errors="strict").splitlines()
        if line and not line.startswith("#")
    ]
    required = {
        "LIBRARY",
        "UNPAIRED_READS_EXAMINED",
        "READ_PAIRS_EXAMINED",
        "PERCENT_DUPLICATION",
    }
    if len(lines) < 2:
        raise RuntimeError(f"sample {sample_id}: markdup metrics content is invalid")
    header = lines[0].split("\t")
    values = lines[1].split("\t")
    if not required.issubset(header) or len(values) != len(header):
        raise RuntimeError(f"sample {sample_id}: markdup metrics content is invalid")
    row = dict(zip(header, values))
    try:
        int(row["UNPAIRED_READS_EXAMINED"])
        int(row["READ_PAIRS_EXAMINED"])
        float(row["PERCENT_DUPLICATION"])
    except (KeyError, ValueError) as exc:
        raise RuntimeError(
            f"sample {sample_id}: markdup metrics content is invalid"
        ) from exc


def _run_sample(
    sample_id: str, input_bam: Path, bundle: Path, args: argparse.Namespace
) -> dict[str, Path]:
    base = f"{args.prefix}.{STAGE}.{sample_id}"
    bam = bundle / f"{base}.markdup.bam"
    bai = bundle / f"{base}.markdup.bam.bai"
    metrics = bundle / f"{base}.markdup.metrics.txt"
    log_path = bundle / f"{base}.markdup.log"
    input_header = _checked(
        [args.samtools, "view", "-H", str(input_bam)],
        f"sample {sample_id} input BAM header",
    ).stdout
    input_groups = _read_groups(input_header, sample_id)
    command = [
        args.gatk,
        "MarkDuplicates",
        f"INPUT={input_bam}",
        f"OUTPUT={bam}",
        f"METRICS_FILE={metrics}",
        "REMOVE_DUPLICATES=true",
        "CREATE_INDEX=true",
        "VALIDATION_STRINGENCY=LENIENT",
    ]
    with log_path.open("w", encoding="utf-8") as log:
        log.write("GATK argv: " + json.dumps(command) + "\n")
        log.flush()
        result = subprocess.run(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        log.write(f"GATK MarkDuplicates exit={result.returncode}\n")
    if result.returncode:
        raise RuntimeError(
            f"sample {sample_id}: GATK MarkDuplicates failed with exit code "
            f"{result.returncode}; see {log_path}"
        )
    alternate_bai = bam.with_suffix(".bai")
    if alternate_bai != bai and alternate_bai.is_file() and not bai.exists():
        os.replace(alternate_bai, bai)
    if not bam.is_file() or bam.stat().st_size <= 0:
        raise RuntimeError(f"sample {sample_id}: markdup BAM is missing or empty")
    _checked([args.samtools, "quickcheck", str(bam)], f"sample {sample_id} quickcheck")
    if not bai.is_file() or bai.stat().st_size <= 0:
        raise RuntimeError(f"sample {sample_id}: markdup BAM index is missing or empty")
    _checked([args.samtools, "idxstats", str(bam)], f"sample {sample_id} index validation")
    output_header = _checked(
        [args.samtools, "view", "-H", str(bam)], f"sample {sample_id} output BAM header"
    ).stdout
    if _read_groups(output_header, sample_id) != input_groups:
        raise RuntimeError(f"sample {sample_id}: BAM read group conservation failed")
    _validate_metrics(metrics, sample_id)
    return {
        "markdup_bam": bam,
        "markdup_bam_index": bai,
        "markdup_metrics": metrics,
        "markdup_log": log_path,
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


def _publish_failure(
    authority: Path, args: argparse.Namespace, run_id: str, started_at: str, error: str
) -> None:
    lock_path = authority.parent.parent / ".locks" / f"{authority.stem}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        failure_path = (
            authority
            if not authority.exists()
            else authority.parent / f"{args.prefix}.{STAGE}.failed.{run_id}.manifest.json"
        )
        _atomic_json(failure_path, _failure(args, run_id, started_at, error))


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


def run(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    run_id = uuid.uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        _safe_component(args.prefix, "prefix")
    except ValueError as exc:
        print(f"{STAGE} failed: {exc}", file=sys.stderr)
        return 1
    final = workspace / "final"
    final.mkdir(parents=True, exist_ok=True)
    authority = final / f"{args.prefix}.{STAGE}.manifest.json"
    try:
        if args.max_jobs < 1:
            raise ValueError("max-jobs must be positive")
        header = _authority_header(
            args.align_manifest,
            workspace=workspace,
            prefix=args.prefix,
            job_id=args.job_id,
        )
        sample_set_id = str(header.get("wgs_sample_set_id") or "")
        reference_id = str(header.get("wgs_reference_id") or "")
        upstream_analysis_id = str(header.get("wgs_analysis_id") or "")
        if not sample_set_id or not reference_id or not upstream_analysis_id:
            raise ValueError("upstream scientific identities are missing")
        resolved = resolve_wgs_artifacts(
            args.align_manifest,
            "wgs_align",
            ("sorted_bam", "sorted_bam_index"),
            sample_set_id,
            reference_id,
            upstream_analysis_id,
        )
        bam_by_sample = {item.sample_id: item for item in resolved["sorted_bam"]}
        index_by_sample = {
            item.sample_id: item for item in resolved["sorted_bam_index"]
        }
        expected_samples = set(bam_by_sample)
        if not expected_samples or expected_samples != set(index_by_sample):
            raise RuntimeError("authoritative alignment input sample sets do not match")
        version = _tool_version(args.gatk)
        samtools_version = _samtools_version(args.samtools)
        parameters = {
            "gatk": args.gatk,
            "samtools": args.samtools,
            "max_jobs": args.max_jobs,
            "remove_duplicates": True,
            "create_index": True,
            "validation_stringency": "LENIENT",
        }
        consumed = [
            item.sha256 for group in resolved.values() for item in group
        ]
        analysis_id = build_wgs_analysis_id(
            STAGE,
            consumed,
            parameters,
            {"gatk": version, "samtools": samtools_version},
            method="gatk_markduplicates",
        )
        attempt = workspace / ".attempts" / run_id
        bundle = attempt / "bundle"
        bundle.mkdir(parents=True, exist_ok=False)
        completed: dict[str, dict[str, Path]] = {}
        failures: list[str] = []
        with ThreadPoolExecutor(max_workers=args.max_jobs) as pool:
            futures = {
                pool.submit(
                    _run_sample, sample, bam_by_sample[sample].path, bundle, args
                ): sample
                for sample in sorted(expected_samples)
            }
            for future in as_completed(futures):
                sample = futures[future]
                try:
                    completed[sample] = future.result()
                except Exception as exc:
                    failures.append(str(exc))
        if failures or set(completed) != expected_samples:
            raise RuntimeError(
                "; ".join(failures)
                or "markdup input/output sample-set conservation failed"
            )
        destination = final / ".wgs_markdup_runs" / args.prefix / run_id
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
            "upstream_wgs_analysis_id": upstream_analysis_id,
            "wgs_analysis_id": analysis_id,
            "parameters": parameters,
            "software": {
                "gatk": {"path": args.gatk, "version": version},
                "samtools": {
                    "path": args.samtools,
                    "version": samtools_version,
                },
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
        if not _publish(bundle, destination, manifest_work, authority, started_at):
            raise RuntimeError(
                "stale WGS markdup attempt cannot replace a newer successful run"
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
