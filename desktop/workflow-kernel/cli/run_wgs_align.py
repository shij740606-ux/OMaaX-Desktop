#!/usr/bin/env python3
"""Run Stage 03 WGS paired-end alignment from authoritative Manifests."""

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
from gfa_core.wgs_upstream_manifest import (
    ResolvedWgsArtifact,
    resolve_wgs_artifacts,
    wgs_workspace_identity_matches,
)


STAGE = "wgs_align"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Align authoritative WGS clean reads with BWA-MEM2")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--qc-manifest", required=True)
    parser.add_argument("--index-manifest", required=True)
    parser.add_argument("--bwa-mem2", default="bwa-mem2")
    parser.add_argument("--samtools", default="samtools")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--max-jobs", type=int, default=1)
    parser.add_argument("--platform", default="illumina")
    return parser


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _safe_component(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value or ""):
        raise ValueError(f"{label} must be a non-empty safe path component")


def _authority_header(path: str | Path, *, stage: str, workspace: Path, prefix: str, job_id: str) -> dict[str, Any]:
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{stage} authority Manifest is unreadable") from exc
    if not isinstance(manifest, dict) or manifest.get("stage") != stage or manifest.get("status") != "success":
        raise ValueError(f"{stage} authority Manifest is not a successful producer")
    if (
        manifest.get("prefix") != prefix
        or manifest.get("job_id") != job_id
        or not wgs_workspace_identity_matches(manifest.get("workspace", ""), workspace)
    ):
        raise ValueError(f"{stage} authority Manifest identity mismatch")
    return manifest


def _tool_version(command: list[str], name: str) -> str:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"{name} version check failed with exit code {result.returncode}: {result.stderr.strip()}")
    lines = (result.stdout or result.stderr).strip().splitlines()
    if not lines:
        raise RuntimeError(f"{name} version check produced no version")
    return lines[0]


def _checked(command: list[str], name: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"{name} failed with exit code {result.returncode}: {result.stderr.strip()}")
    return result


def _validate_header(header: str, sample_id: str, platform: str) -> None:
    read_groups = []
    for line in header.splitlines():
        if line.startswith("@RG\t"):
            fields = {}
            for field in line.split("\t")[1:]:
                if ":" in field:
                    key, value = field.split(":", 1)
                    fields[key] = value
            read_groups.append(fields)
    expected = {"ID": sample_id, "SM": sample_id, "PL": platform}
    if len(read_groups) != 1 or any(read_groups[0].get(key) != value for key, value in expected.items()):
        raise RuntimeError(f"sample {sample_id}: BAM read group identity mismatch")


def _run_sample(
    sample_id: str, r1: Path, r2: Path, reference: Path, bundle: Path, args: argparse.Namespace,
) -> dict[str, Path]:
    base = f"{args.prefix}.{STAGE}.{sample_id}"
    bam = bundle / f"{base}.sorted.bam"
    bai = bundle / f"{base}.sorted.bam.bai"
    log_path = bundle / f"{base}.align.log"
    rg = f"@RG\tID:{sample_id}\tSM:{sample_id}\tPL:{args.platform}"
    bwa_command = [args.bwa_mem2, "mem", "-t", str(args.threads), "-R", rg, str(reference), str(r1), str(r2)]
    sort_command = [args.samtools, "sort", "-@", str(args.threads), "-o", str(bam), "-"]
    with log_path.open("w", encoding="utf-8") as log:
        log.write("bwa-mem2 argv: " + json.dumps(bwa_command) + "\n")
        log.write("samtools sort argv: " + json.dumps(sort_command) + "\n")
        log.flush()
        bwa = subprocess.Popen(bwa_command, stdout=subprocess.PIPE, stderr=log, text=True)
        assert bwa.stdout is not None
        sorter = subprocess.Popen(sort_command, stdin=bwa.stdout, stdout=subprocess.DEVNULL, stderr=log, text=True)
        bwa.stdout.close()
        sort_returncode = sorter.wait()
        bwa_returncode = bwa.wait()
        log.write(f"bwa-mem2 exit={bwa_returncode}; samtools sort exit={sort_returncode}\n")
    if bwa_returncode:
        raise RuntimeError(f"sample {sample_id}: bwa-mem2 failed with exit code {bwa_returncode}; see {log_path}")
    if sort_returncode:
        raise RuntimeError(f"sample {sample_id}: samtools sort failed with exit code {sort_returncode}; see {log_path}")
    if not bam.is_file() or bam.stat().st_size <= 0:
        raise RuntimeError(f"sample {sample_id}: sorted BAM is missing or empty")
    _checked([args.samtools, "quickcheck", str(bam)], f"sample {sample_id} samtools quickcheck")
    _checked([args.samtools, "index", str(bam), str(bai)], f"sample {sample_id} samtools index")
    if not bai.is_file() or bai.stat().st_size <= 0:
        raise RuntimeError(f"sample {sample_id}: BAM index is missing or empty")
    _checked([args.samtools, "idxstats", str(bam)], f"sample {sample_id} indexed BAM validation")
    header = _checked([args.samtools, "view", "-H", str(bam)], f"sample {sample_id} BAM header").stdout
    _validate_header(header, sample_id, args.platform)
    return {"sorted_bam": bam, "sorted_bam_index": bai, "align_log": log_path}


def _artifact(workspace: Path, source: Path, destination: Path, role: str, sample_id: str) -> dict[str, Any]:
    size = source.stat().st_size
    return {"artifact": role, "role": role, "path": str(destination.relative_to(workspace)),
            "full_path": str(destination.resolve()), "type": source.suffix.lstrip(".") or "file",
            "size": size, "size_bytes": size, "sha256": sha256_file(source), "sample_id": sample_id}


def _publish(bundle: Path, destination: Path, manifest_work: Path, authority: Path, started_at: str) -> bool:
    lock_path = authority.parent.parent / ".locks" / f"{authority.stem}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if authority.is_file():
            try:
                current = json.loads(authority.read_text(encoding="utf-8"))
                if current.get("status") == "success" and str(current.get("start_time") or "") > started_at:
                    return False
            except Exception:
                pass
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(bundle, destination)
        os.replace(manifest_work, authority)
    return True


def _failure(args: argparse.Namespace, run_id: str, started_at: str, error: str) -> dict[str, Any]:
    return {"manifest_version": "1.0", "stage": STAGE, "status": "failed", "prefix": args.prefix,
            "job_id": args.job_id, "run_id": run_id, "start_time": started_at, "artifacts": {}, "errors": [error]}


def run(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    run_id = uuid.uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        _safe_component(args.prefix, "prefix")
    except ValueError as exc:
        print(f"wgs_align failed: {exc}", file=sys.stderr)
        return 1
    final = workspace / "final"; final.mkdir(parents=True, exist_ok=True)
    authority = final / f"{args.prefix}.{STAGE}.manifest.json"
    try:
        if args.threads < 1 or args.max_jobs < 1:
            raise ValueError("threads and max-jobs must be positive")
        _safe_component(args.platform, "platform")
        qc_header = _authority_header(args.qc_manifest, stage="wgs_qc", workspace=workspace, prefix=args.prefix, job_id=args.job_id)
        index_header = _authority_header(args.index_manifest, stage="wgs_index", workspace=workspace, prefix=args.prefix, job_id=args.job_id)
        sample_set_id = str(qc_header.get("wgs_sample_set_id") or "")
        reference_id = str(index_header.get("wgs_reference_id") or "")
        if not sample_set_id or not reference_id:
            raise ValueError("upstream scientific identities are missing")
        qc = resolve_wgs_artifacts(args.qc_manifest, "wgs_qc", ("clean_reads_r1", "clean_reads_r2"), sample_set_id)
        index = resolve_wgs_artifacts(
            args.index_manifest, "wgs_index",
            ("reference_fasta", "reference_fai", "reference_dict", "bwa_mem2_index_component"),
            expected_reference_id=reference_id,
        )
        r1_by_sample = {item.sample_id: item for item in qc["clean_reads_r1"]}
        r2_by_sample = {item.sample_id: item for item in qc["clean_reads_r2"]}
        expected_samples = set(r1_by_sample)
        if expected_samples != set(r2_by_sample) or not expected_samples:
            raise RuntimeError("authoritative QC input sample sets do not match")
        reference = index["reference_fasta"][0].path
        versions = {"bwa_mem2": _tool_version([args.bwa_mem2, "version"], "bwa-mem2"),
                    "samtools": _tool_version([args.samtools, "--version"], "samtools")}
        parameters = {"bwa_mem2": args.bwa_mem2, "samtools": args.samtools, "threads": args.threads,
                      "max_jobs": args.max_jobs, "platform": args.platform}
        consumed = [item.sha256 for group in (*qc.values(), *index.values()) for item in group]
        analysis_id = build_wgs_analysis_id(STAGE, consumed, parameters, versions, method="bwa_mem2_samtools")
        attempt = workspace / ".attempts" / run_id; bundle = attempt / "bundle"
        bundle.mkdir(parents=True, exist_ok=False)
        completed: dict[str, dict[str, Path]] = {}
        failures: list[str] = []
        with ThreadPoolExecutor(max_workers=args.max_jobs) as pool:
            futures = {pool.submit(_run_sample, sample, r1_by_sample[sample].path, r2_by_sample[sample].path,
                                   reference, bundle, args): sample for sample in sorted(expected_samples)}
            for future in as_completed(futures):
                sample = futures[future]
                try:
                    completed[sample] = future.result()
                except Exception as exc:
                    failures.append(str(exc))
        if failures or set(completed) != expected_samples:
            raise RuntimeError("; ".join(failures) or "alignment input/output sample-set conservation failed")
        destination = final / ".wgs_align_runs" / args.prefix / run_id
        artifacts = {}
        for sample in sorted(completed):
            for role, source in completed[sample].items():
                artifacts[f"{role}.{sample}"] = _artifact(workspace, source, destination / source.name, role, sample)
        manifest = {"manifest_version": "1.0", "stage": STAGE, "status": "success", "prefix": args.prefix,
                    "job_id": args.job_id, "run_id": run_id, "start_time": started_at, "workspace": str(workspace),
                    "wgs_sample_set_id": sample_set_id, "wgs_reference_id": reference_id, "wgs_analysis_id": analysis_id,
                    "parameters": parameters, "software": {name: {"path": getattr(args, name), "version": version} for name, version in versions.items()},
                    "metrics": {"input_sample_ids": sorted(expected_samples), "output_sample_ids": sorted(completed),
                                "total_samples": len(expected_samples)}, "artifacts": artifacts}
        manifest_work = attempt / "manifest.json"; _atomic_json(manifest_work, manifest)
        if not _publish(bundle, destination, manifest_work, authority, started_at):
            raise RuntimeError("stale WGS alignment attempt cannot replace a newer successful run")
        return 0
    except Exception as exc:
        failure_path = authority if not authority.exists() else final / f"{args.prefix}.{STAGE}.failed.{run_id}.manifest.json"
        _atomic_json(failure_path, _failure(args, run_id, started_at, str(exc)))
        print(f"wgs_align failed: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
