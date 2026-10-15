#!/usr/bin/env python3
"""Run Stage 05 WGS BAM quality metrics from authoritative Manifests."""

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


STAGE = "wgs_bam_qc"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure coverage and alignment counts for authoritative WGS BAMs"
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--markdup-manifest", required=True)
    parser.add_argument("--index-manifest", required=True)
    parser.add_argument("--samtools", required=True)
    parser.add_argument("--coverage-threshold", type=int, default=1, choices=(1,))
    parser.add_argument("--threads", required=True, type=int)
    parser.add_argument("--max-jobs", required=True, type=int)
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


def _checked(command: list[str], name: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(
            f"{name} failed with exit code {result.returncode}: {result.stderr.strip()}"
        )
    return result


def _tool_version(samtools: str) -> str:
    result = _checked([samtools, "--version"], "samtools version check")
    lines = result.stdout.strip().splitlines()
    if not lines:
        raise RuntimeError("samtools version check produced no version")
    return lines[0]


def _reference_lengths(path: Path) -> dict[str, int]:
    lengths: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("reference FAI is unreadable") from exc
    for line_number, line in enumerate(lines, 1):
        fields = line.split("\t")
        if len(fields) < 2 or not fields[0]:
            raise RuntimeError(f"reference FAI line {line_number} is invalid")
        try:
            length = int(fields[1])
        except ValueError as exc:
            raise RuntimeError(
                f"reference FAI line {line_number} has invalid length"
            ) from exc
        if length <= 0 or fields[0] in lengths:
            raise RuntimeError(
                f"reference FAI line {line_number} has duplicate/invalid contig"
            )
        lengths[fields[0]] = length
    if not lengths:
        raise RuntimeError("reference FAI contains no contigs")
    return lengths


def _parse_depth(
    path: Path, sample_id: str, lengths: dict[str, int], threshold: int
) -> dict[str, int | float | str]:
    depth_sum = 0
    bases_ge_threshold = 0
    observed_positions: set[tuple[str, int]] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"sample {sample_id}: samtools depth output is unreadable") from exc
    for line_number, line in enumerate(lines, 1):
        fields = line.split("\t")
        if len(fields) != 3 or fields[0] not in lengths:
            raise RuntimeError(
                f"sample {sample_id}: invalid depth row {line_number}"
            )
        try:
            position, depth = int(fields[1]), int(fields[2])
        except ValueError as exc:
            raise RuntimeError(
                f"sample {sample_id}: invalid depth row {line_number}"
            ) from exc
        identity = (fields[0], position)
        if (
            position < 1
            or position > lengths[fields[0]]
            or depth < 0
            or identity in observed_positions
        ):
            raise RuntimeError(
                f"sample {sample_id}: invalid depth row {line_number}"
            )
        observed_positions.add(identity)
        depth_sum += depth
        if depth >= threshold:
            bases_ge_threshold += 1
    total_reference_bases = sum(lengths.values())
    return {
        "sample_id": sample_id,
        "total_reference_bases": total_reference_bases,
        "depth_sum": depth_sum,
        "mean_depth": depth_sum / total_reference_bases,
        "bases_ge_1x": bases_ge_threshold,
        "coverage_ge_1x": bases_ge_threshold / total_reference_bases,
    }


def _run_sample(
    sample_id: str,
    input_bam: Path,
    bundle: Path,
    args: argparse.Namespace,
    lengths: dict[str, int],
) -> tuple[dict[str, Path], dict[str, int | float | str]]:
    base = f"{args.prefix}.{STAGE}.{sample_id}"
    flagstat_path = bundle / f"{base}.flagstat.txt"
    depth_summary_path = bundle / f"{base}.depth.tsv"
    depth_raw = bundle / f".{base}.depth.raw.tsv"

    flagstat = _checked(
        [args.samtools, "flagstat", "-@", str(args.threads), str(input_bam)],
        f"sample {sample_id} flagstat",
    )
    if " in total " not in f" {flagstat.stdout} ":
        raise RuntimeError(f"sample {sample_id}: flagstat content is invalid")
    flagstat_path.write_text(flagstat.stdout, encoding="utf-8")

    with depth_raw.open("w", encoding="utf-8") as output:
        depth = subprocess.run(
            [
                args.samtools,
                "depth",
                "-d",
                "0",
                "-@",
                str(args.threads),
                str(input_bam),
            ],
            text=True,
            stdout=output,
            stderr=subprocess.PIPE,
            check=False,
        )
    if depth.returncode:
        raise RuntimeError(
            f"sample {sample_id} depth failed with exit code "
            f"{depth.returncode}: {depth.stderr.strip()}"
        )
    metrics = _parse_depth(
        depth_raw, sample_id, lengths, args.coverage_threshold
    )
    depth_raw.unlink()
    columns = (
        "sample_id",
        "total_reference_bases",
        "depth_sum",
        "mean_depth",
        "bases_ge_1x",
        "coverage_ge_1x",
    )
    depth_summary_path.write_text(
        "\t".join(columns)
        + "\n"
        + "\t".join(
            (
                str(metrics["sample_id"]),
                str(metrics["total_reference_bases"]),
                str(metrics["depth_sum"]),
                f"{float(metrics['mean_depth']):.12g}",
                str(metrics["bases_ge_1x"]),
                f"{float(metrics['coverage_ge_1x']):.12g}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return (
        {
            "bam_flagstat": flagstat_path,
            "bam_depth_summary": depth_summary_path,
        },
        metrics,
    )


def _artifact(
    workspace: Path,
    source: Path,
    destination: Path,
    role: str,
    sample_id: str = "",
) -> dict[str, Any]:
    size = source.stat().st_size
    record = {
        "artifact": role,
        "role": role,
        "path": str(destination.relative_to(workspace)),
        "full_path": str(destination.resolve()),
        "type": source.suffix.lstrip(".") or "file",
        "size": size,
        "size_bytes": size,
        "sha256": sha256_file(source),
    }
    if sample_id:
        record["sample_id"] = sample_id
    return record


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
            else authority.parent / f"{args.prefix}.{STAGE}.failed.{run_id}.manifest.json"
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
        if args.threads < 1 or args.max_jobs < 1:
            raise ValueError("threads and max-jobs must be positive")
        markdup_header = _authority_header(
            args.markdup_manifest,
            stage="wgs_markdup",
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
        markdup_analysis_id = str(markdup_header.get("wgs_analysis_id") or "")
        index_analysis_id = str(index_header.get("wgs_analysis_id") or "")
        if not all(
            (sample_set_id, reference_id, markdup_analysis_id, index_analysis_id)
        ):
            raise ValueError("upstream scientific identities are missing")
        if index_header.get("wgs_reference_id") != reference_id:
            raise ValueError("upstream reference identities do not match")
        markdup = resolve_wgs_artifacts(
            args.markdup_manifest,
            "wgs_markdup",
            ("markdup_bam", "markdup_bam_index"),
            sample_set_id,
            reference_id,
            markdup_analysis_id,
        )
        index = resolve_wgs_artifacts(
            args.index_manifest,
            "wgs_index",
            ("reference_fai",),
            expected_reference_id=reference_id,
            expected_analysis_id=index_analysis_id,
        )
        if len(index["reference_fai"]) != 1:
            raise RuntimeError("authoritative index must contain one reference FAI")
        bam_by_sample = {item.sample_id: item for item in markdup["markdup_bam"]}
        bai_by_sample = {
            item.sample_id: item for item in markdup["markdup_bam_index"]
        }
        expected_samples = set(bam_by_sample)
        if not expected_samples or expected_samples != set(bai_by_sample):
            raise RuntimeError("authoritative markdup input sample sets do not match")
        lengths = _reference_lengths(index["reference_fai"][0].path)
        version = _tool_version(args.samtools)
        parameters = {
            "samtools": args.samtools,
            "coverage_threshold": args.coverage_threshold,
            "metric_name": "coverage_ge_1x",
            "threads": args.threads,
            "max_jobs": args.max_jobs,
        }
        consumed = [
            item.sha256
            for group in (markdup, index)
            for values in group.values()
            for item in values
        ]
        analysis_id = build_wgs_analysis_id(
            STAGE,
            consumed,
            parameters,
            {"samtools": version},
            method="samtools_flagstat_depth",
        )
        attempt = workspace / ".attempts" / run_id
        bundle = attempt / "bundle"
        bundle.mkdir(parents=True, exist_ok=False)
        completed: dict[str, dict[str, Path]] = {}
        sample_metrics: dict[str, dict[str, int | float | str]] = {}
        failures: list[str] = []
        with ThreadPoolExecutor(max_workers=args.max_jobs) as pool:
            futures = {
                pool.submit(
                    _run_sample,
                    sample,
                    bam_by_sample[sample].path,
                    bundle,
                    args,
                    lengths,
                ): sample
                for sample in sorted(expected_samples)
            }
            for future in as_completed(futures):
                sample = futures[future]
                try:
                    completed[sample], sample_metrics[sample] = future.result()
                except Exception as exc:
                    failures.append(str(exc))
        if failures or set(completed) != expected_samples:
            raise RuntimeError(
                "; ".join(failures)
                or "BAM QC input/output sample-set conservation failed"
            )

        summary = bundle / f"{args.prefix}.{STAGE}.cohort.summary.tsv"
        summary_columns = (
            "sample_id",
            "total_reference_bases",
            "depth_sum",
            "mean_depth",
            "bases_ge_1x",
            "coverage_ge_1x",
        )
        summary.write_text(
            "\t".join(summary_columns)
            + "\n"
            + "".join(
                "\t".join(
                    (
                        sample,
                        str(sample_metrics[sample]["total_reference_bases"]),
                        str(sample_metrics[sample]["depth_sum"]),
                        f"{float(sample_metrics[sample]['mean_depth']):.12g}",
                        str(sample_metrics[sample]["bases_ge_1x"]),
                        f"{float(sample_metrics[sample]['coverage_ge_1x']):.12g}",
                    )
                )
                + "\n"
                for sample in sorted(sample_metrics)
            ),
            encoding="utf-8",
        )
        cohort_coverage = sum(
            float(value["coverage_ge_1x"]) for value in sample_metrics.values()
        ) / len(sample_metrics)
        report = bundle / f"{args.prefix}.{STAGE}.cohort.report.md"
        report.write_text(
            "# WGS BAM QC\n\n"
            f"- Samples: {len(sample_metrics)}\n"
            f"- Reference bases per sample: {sum(lengths.values())}\n"
            f"- coverage_ge_1x: {cohort_coverage:.12g}\n"
            "- Coverage denominator includes every reference base, including "
            "zero-coverage positions.\n"
            "- No sample was excluded based on coverage or depth.\n",
            encoding="utf-8",
        )

        destination = final / ".wgs_bam_qc_runs" / args.prefix / run_id
        artifacts: dict[str, dict[str, Any]] = {}
        for sample in sorted(completed):
            for role, source in completed[sample].items():
                artifacts[f"{role}.{sample}"] = _artifact(
                    workspace,
                    source,
                    destination / source.name,
                    role,
                    sample,
                )
        for role, source in (
            ("bam_qc_summary", summary),
            ("bam_qc_report", report),
        ):
            artifacts[role] = _artifact(
                workspace, source, destination / source.name, role
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
            "upstream_wgs_analysis_ids": {
                "wgs_markdup": markdup_analysis_id,
                "wgs_index": index_analysis_id,
            },
            "wgs_analysis_id": analysis_id,
            "parameters": parameters,
            "software": {
                "samtools": {"path": args.samtools, "version": version}
            },
            "metrics": {
                "input_sample_ids": sorted(expected_samples),
                "output_sample_ids": sorted(completed),
                "total_samples": len(expected_samples),
                "total_reference_bases": sum(lengths.values()),
                "coverage_ge_1x": cohort_coverage,
                "per_sample": [
                    sample_metrics[sample] for sample in sorted(sample_metrics)
                ],
            },
            "artifacts": artifacts,
        }
        manifest_work = attempt / "manifest.json"
        _atomic_json(manifest_work, manifest)
        if not _publish(bundle, destination, manifest_work, authority, started_at):
            raise RuntimeError(
                "stale WGS BAM QC attempt cannot replace a newer successful run"
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
