#!/usr/bin/env python3
"""Run Stage 01 WGS paired-end quality control with fastp."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

from gfa_core.wgs_samples import WgsSample, load_wgs_samples
from gfa_core.wgs_scientific_identity import build_wgs_analysis_id, build_wgs_sample_set_id, sha256_file


STAGE = "wgs_qc"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run fastp for every declared WGS sample")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--wgs-samples", required=True)
    parser.add_argument("--fastp", default="fastp")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--max-jobs", type=int, default=1)
    parser.add_argument("-q", type=int, default=5)
    parser.add_argument("-u", type=int, default=50)
    parser.add_argument("-n", type=int, default=10)
    parser.add_argument("-l", type=int, default=50)
    return parser


def _sha_artifact(workspace: Path, path: Path, role: str, sample_id: str = "") -> dict[str, Any]:
    item: dict[str, Any] = {
        "artifact": role,
        "role": role,
        "path": str(path.relative_to(workspace)),
        "full_path": str(path.resolve()),
        "type": path.suffix.lstrip(".") or "file",
        "size": path.stat().st_size,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if sample_id:
        item["sample_id"] = sample_id
    return item


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _publish_transaction(bundle_work: Path, bundle_destination: Path, manifest_work: Path, manifest_path: Path, run_id: str, started_at: str) -> bool:
    """Publish an immutable run bundle, then atomically switch the authority Manifest."""
    lock_path = manifest_path.parent.parent / ".locks" / f"{manifest_path.stem}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if manifest_path.is_file():
            try:
                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
                if existing.get("status") == "success" and str(existing.get("start_time") or "") > started_at:
                    return False
            except Exception:
                pass
        try:
            bundle_destination.parent.mkdir(parents=True, exist_ok=True)
            # A whole, validated directory becomes visible in one rename.  The
            # authority manifest is switched only after that immutable bundle exists.
            os.replace(bundle_work, bundle_destination)
            os.replace(manifest_work, manifest_path)
        except Exception:
            # Do not alter the previous authority manifest.  A renamed bundle is an
            # unreachable orphan until a later explicit cleanup policy handles it.
            raise
    return True


def _tool_version(fastp: str) -> str:
    result = subprocess.run([fastp, "--version"], text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"fastp version check failed with exit code {result.returncode}: {result.stderr.strip()}")
    version = (result.stdout or result.stderr).strip().splitlines()
    if not version:
        raise RuntimeError("fastp version check produced no version")
    return version[0]


def _sample_paths(attempt: Path, prefix: str, sample: WgsSample) -> dict[str, Path]:
    base = f"{prefix}.{STAGE}.{sample.sample_id}"
    return {
        "clean_reads_r1": attempt / f"{base}.clean.R1.fastq.gz",
        "clean_reads_r2": attempt / f"{base}.clean.R2.fastq.gz",
        "fastp_html": attempt / f"{base}.fastp.html",
        "fastp_json": attempt / f"{base}.fastp.json",
    }


def _run_sample(sample: WgsSample, attempt: Path, args: argparse.Namespace) -> tuple[str, dict[str, Path]]:
    attempt.mkdir(parents=True, exist_ok=False)
    output = _sample_paths(attempt, args.prefix, sample)
    command = [
        args.fastp, "-i", str(sample.r1), "-I", str(sample.r2),
        "-o", str(output["clean_reads_r1"]), "-O", str(output["clean_reads_r2"]),
        "-h", str(output["fastp_html"]), "-j", str(output["fastp_json"]),
        "-w", str(args.threads), "-q", str(args.q), "-u", str(args.u),
        "-n", str(args.n), "-l", str(args.l),
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"sample {sample.sample_id}: fastp exit code {result.returncode}: {result.stderr.strip()}")
    missing = [role for role, path in output.items() if not path.is_file() or path.stat().st_size <= 0]
    if missing:
        raise RuntimeError(f"sample {sample.sample_id}: missing or empty required output(s): {', '.join(missing)}")
    return sample.sample_id, output


def _failure_manifest(args: argparse.Namespace, error: str, run_id: str = "") -> dict[str, Any]:
    return {
        "manifest_version": "1.0",
        "stage": STAGE,
        "status": "failed",
        "prefix": args.prefix,
        "job_id": args.job_id,
        "run_id": run_id,
        "artifacts": {},
        "errors": [error],
    }


def run(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    final_dir = workspace / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = final_dir / f"{args.prefix}.{STAGE}.manifest.json"
    run_id = uuid.uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        if args.threads < 1 or args.max_jobs < 1:
            raise ValueError("threads and max-jobs must be positive")
        samples = load_wgs_samples(args.wgs_samples)
        fastp_version = _tool_version(args.fastp)
        sample_records = [{"sample_id": sample.sample_id, "r1": str(sample.r1), "r2": str(sample.r2)} for sample in samples]
        sample_set_id = build_wgs_sample_set_id(sample_records)
        parameters = {
            "fastp": args.fastp, "threads": args.threads, "max_jobs": args.max_jobs,
            "q": args.q, "u": args.u, "n": args.n, "l": args.l,
        }
        input_hashes = [sha256_file(Path(args.wgs_samples))]
        input_hashes.extend(sha256_file(path) for sample in samples for path in (sample.r1, sample.r2))
        analysis_id = build_wgs_analysis_id(STAGE, input_hashes, parameters, {"fastp": fastp_version}, method="fastp")
        attempt_root = workspace / ".attempts" / run_id
        attempt_root.mkdir(parents=True, exist_ok=False)
        bundle_work = attempt_root / "bundle"
        bundle_work.mkdir()
        attempt_config = bundle_work / "wgs_samples.yaml"
        shutil.copy2(args.wgs_samples, attempt_config)
        completed: dict[str, dict[str, Path]] = {}
        failures: list[str] = []
        with ThreadPoolExecutor(max_workers=args.max_jobs) as pool:
            futures = {pool.submit(_run_sample, sample, attempt_root / sample.sample_id, args): sample.sample_id for sample in samples}
            for future in as_completed(futures):
                sample_id = futures[future]
                try:
                    returned_id, outputs = future.result()
                    completed[returned_id] = outputs
                except Exception as exc:
                    failures.append(f"{sample_id}: {exc}")
        expected_ids = {sample.sample_id for sample in samples}
        if failures or set(completed) != expected_ids:
            details = "; ".join(failures) or "input/output sample IDs differ"
            raise RuntimeError(details)
        artifacts: dict[str, dict[str, Any]] = {}
        bundle_destination = final_dir / ".wgs_qc_runs" / args.prefix / run_id
        for sample_id in sorted(completed):
            for role, source in completed[sample_id].items():
                bundle_source = bundle_work / source.name
                os.replace(source, bundle_source)
                completed[sample_id][role] = bundle_source
        config_destination = bundle_destination / attempt_config.name
        artifacts["wgs_sample_config"] = _sha_artifact(workspace, attempt_config, "wgs_sample_config")
        artifacts["wgs_sample_config"].update({"path": str(config_destination.relative_to(workspace)), "full_path": str(config_destination.resolve())})
        for sample_id in sorted(completed):
            for role, source in completed[sample_id].items():
                destination = bundle_destination / source.name
                artifact = _sha_artifact(workspace, source, role, sample_id)
                artifact.update({"path": str(destination.relative_to(workspace)), "full_path": str(destination.resolve())})
                artifacts[f"{role}.{sample_id}"] = artifact
        manifest = {
            "manifest_version": "1.0", "stage": STAGE, "status": "success", "prefix": args.prefix,
            "job_id": args.job_id, "run_id": run_id, "start_time": started_at, "workspace": str(workspace),
            "wgs_sample_set_id": sample_set_id, "wgs_analysis_id": analysis_id,
            "parameters": parameters, "software": {"fastp": {"path": args.fastp, "version": fastp_version}},
            "metrics": {"input_sample_ids": sorted(expected_ids), "output_sample_ids": sorted(completed), "total_samples": len(samples)},
            "artifacts": artifacts,
        }
        manifest_work = attempt_root / "manifest.json"
        _atomic_json(manifest_work, manifest)
        if not _publish_transaction(bundle_work, bundle_destination, manifest_work, manifest_path, run_id, started_at):
            raise RuntimeError("stale WGS QC attempt cannot replace a newer successful run")
        return 0
    except Exception as exc:
        failed = _failure_manifest(args, str(exc), run_id)
        failed["start_time"] = started_at
        failure_path = manifest_path if not manifest_path.exists() else final_dir / f"{args.prefix}.{STAGE}.failed.{run_id}.manifest.json"
        _atomic_json(failure_path, failed)
        print(f"wgs_qc failed: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
