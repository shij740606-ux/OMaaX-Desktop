#!/usr/bin/env python3
"""Run Stage 02 WGS reference indexing with samtools, GATK and BWA-MEM2."""

from __future__ import annotations

import argparse
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

from gfa_core.wgs_scientific_identity import build_wgs_analysis_id, build_wgs_reference_id, sha256_file


STAGE = "wgs_index"
COMPONENTS = (".0123", ".amb", ".ann", ".bwt.2bit.64", ".pac")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the required WGS reference indices")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--reference-fasta", required=True)
    parser.add_argument("--samtools", default="samtools")
    parser.add_argument("--bwa-mem2", default="bwa-mem2")
    parser.add_argument("--gatk", default="gatk")
    parser.add_argument("--threads", type=int, default=1)
    return parser


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _authority_lock_path(manifest_path: Path) -> Path:
    return manifest_path.parent.parent / ".locks" / f"{manifest_path.stem}.lock"


def _is_safe_path_component(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _is_generated_run_id(value: Any) -> bool:
    return (
        _is_safe_path_component(value)
        and len(value) == 32
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_prefix(prefix: Any) -> None:
    if not _is_safe_path_component(prefix):
        raise ValueError("prefix must be a non-empty safe path component")


def _publish_transaction(bundle_work: Path, bundle_destination: Path, manifest_work: Path, manifest_path: Path, started_at: str) -> bool:
    """Publish a complete run bundle before switching the authority Manifest."""
    lock_path = _authority_lock_path(manifest_path)
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
        bundle_destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(bundle_work, bundle_destination)
        os.replace(manifest_work, manifest_path)
    return True


def _tool_version(tool: str, *, bwa_mem2: bool = False) -> str:
    result = subprocess.run([tool, "version" if bwa_mem2 else "--version"], text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"{tool} version check failed with exit code {result.returncode}: {result.stderr.strip()}")
    lines = (result.stdout or result.stderr).strip().splitlines()
    if not lines:
        raise RuntimeError(f"{tool} version check produced no version")
    return lines[0]


def _validate_reference(path: Path) -> list[tuple[str, int]]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError("reference FASTA must be an existing non-empty regular file")
    seen: set[str] = set()
    contigs: list[tuple[str, int]] = []
    current = ""
    length = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            text = line.strip()
            if not text:
                continue
            if text.startswith(">"):
                if current:
                    if length <= 0:
                        raise ValueError(f"reference FASTA contig {current!r} has no sequence")
                    contigs.append((current, length))
                current = text[1:].split(maxsplit=1)[0]
                if not current:
                    raise ValueError(f"reference FASTA header at line {line_number} has no contig name")
                if current in seen:
                    raise ValueError(f"reference FASTA contig names must be unique: {current}")
                seen.add(current)
                length = 0
            else:
                if not current:
                    raise ValueError(f"reference FASTA sequence at line {line_number} precedes a header")
                length += len("".join(text.split()))
    if not current or length <= 0:
        raise ValueError("reference FASTA must contain at least one header with sequence")
    contigs.append((current, length))
    return contigs


def _validate_fai(path: Path, expected: list[tuple[str, int]]) -> None:
    observed: list[tuple[str, int]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                fields = line.rstrip("\n").split("\t")
                if len(fields) < 5 or not fields[0]:
                    raise ValueError(f"invalid FAI record at line {line_number}")
                observed.append((fields[0], int(fields[1])))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError(f"FAI content validation failed: {exc}") from exc
    if observed != expected:
        raise RuntimeError(f"FAI contig/length mismatch: expected {expected}, observed {observed}")


def _validate_sequence_dictionary(path: Path, expected: list[tuple[str, int]]) -> None:
    observed: list[tuple[str, int]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.startswith("@SQ\t"):
                    continue
                fields = line.rstrip("\n").split("\t")[1:]
                names = [field[3:] for field in fields if field.startswith("SN:")]
                lengths = [field[3:] for field in fields if field.startswith("LN:")]
                if len(names) != 1 or len(lengths) != 1:
                    raise ValueError(f"invalid dictionary @SQ record at line {line_number}")
                observed.append((names[0], int(lengths[0])))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError(f"sequence dictionary content validation failed: {exc}") from exc
    if observed != expected:
        raise RuntimeError(f"sequence dictionary contig/length mismatch: expected {expected}, observed {observed}")


def _run_tool(command: list[str], name: str) -> None:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"{name} failed with exit code {result.returncode}: {result.stderr.strip()}")


def _artifact(workspace: Path, source: Path, destination: Path, role: str, component: str = "") -> dict[str, Any]:
    item: dict[str, Any] = {
        "artifact": role, "role": role, "path": str(destination.relative_to(workspace)),
        "full_path": str(destination.resolve()), "type": source.suffix.lstrip(".") or "file",
        "size": source.stat().st_size, "size_bytes": source.stat().st_size, "sha256": sha256_file(source),
    }
    if component:
        item["component"] = component
    return item


def _required_paths(reference: Path, dictionary: Path) -> dict[str, Path]:
    output = {"reference_fai": Path(f"{reference}.fai"), "reference_dict": dictionary}
    output.update({f"bwa_mem2_index_component.{component}": Path(f"{reference}{component}") for component in COMPONENTS})
    return output


def _has_symlink_component(path: Path, boundary: Path) -> bool:
    try:
        relative = path.relative_to(boundary)
    except ValueError:
        return True
    current = boundary
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _valid_reuse(manifest_path: Path, *, workspace: Path, job_id: str, prefix: str, reference_id: str, analysis_id: str, parameters: dict[str, Any]) -> bool:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    run_id = manifest.get("run_id")
    if not all((manifest.get("stage") == STAGE, manifest.get("status") == "success", manifest.get("job_id") == job_id,
                manifest.get("prefix") == prefix, manifest.get("wgs_reference_id") == reference_id,
                manifest.get("wgs_analysis_id") == analysis_id, manifest.get("parameters") == parameters,
                _is_safe_path_component(prefix), _is_generated_run_id(run_id))):
        return False
    workspace = workspace.resolve()
    bundle_root = workspace / "final" / ".wgs_index_runs" / prefix / run_id
    if _has_symlink_component(bundle_root, workspace):
        return False
    bundle_root_resolved = bundle_root.resolve()
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    expected = {"reference_fasta", "reference_fai", "reference_dict", *{f"bwa_mem2_index_component.{component}" for component in COMPONENTS}}
    if set(artifacts) != expected:
        return False
    for key, item in artifacts.items():
        if not isinstance(item, dict):
            return False
        expected_role = "bwa_mem2_index_component" if key.startswith("bwa_mem2_index_component.") else key
        if str(item.get("role") or item.get("artifact") or "") != expected_role:
            return False
        expected_component = key.removeprefix("bwa_mem2_index_component.") if expected_role == "bwa_mem2_index_component" else ""
        if str(item.get("component") or "") != expected_component:
            return False
        relative_path = Path(str(item.get("path") or ""))
        full_path = Path(str(item.get("full_path") or ""))
        if not str(relative_path) or relative_path.is_absolute() or ".." in relative_path.parts or not full_path.is_absolute() or ".." in full_path.parts:
            return False
        path_from_relative = workspace / relative_path
        if _has_symlink_component(path_from_relative, workspace) or _has_symlink_component(full_path, workspace):
            return False
        resolved_path = path_from_relative.resolve()
        resolved_full_path = full_path.resolve()
        if resolved_path != resolved_full_path:
            return False
        try:
            resolved_path.relative_to(bundle_root_resolved)
        except ValueError:
            return False
        if not resolved_path.is_file() or resolved_path.stat().st_size <= 0:
            return False
        actual_size = resolved_path.stat().st_size
        if item.get("size") != actual_size or item.get("size_bytes") != actual_size:
            return False
        declared_hash = str(item.get("sha256") or "").lower()
        if len(declared_hash) != 64 or any(char not in "0123456789abcdef" for char in declared_hash) or sha256_file(resolved_path) != declared_hash:
            return False
    return True


def _refresh_reuse_authority(
    manifest_path: Path, *, workspace: Path, job_id: str, prefix: str, reference_id: str,
    analysis_id: str, parameters: dict[str, Any], reused_at: str, reuse_attempt_id: str,
) -> bool:
    lock_path = _authority_lock_path(manifest_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if not _valid_reuse(
            manifest_path, workspace=workspace, job_id=job_id, prefix=prefix,
            reference_id=reference_id, analysis_id=analysis_id, parameters=parameters,
        ):
            return False
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        previous = manifest.get("reuse") if isinstance(manifest.get("reuse"), dict) else {}
        manifest["reuse"] = {
            "count": int(previous.get("count") or 0) + 1,
            "last_reused_at": reused_at,
            "last_attempt_id": reuse_attempt_id,
        }
        _atomic_json(manifest_path, manifest)
    return True


def _failure_manifest(args: argparse.Namespace, error: str, run_id: str, started_at: str) -> dict[str, Any]:
    return {"manifest_version": "1.0", "stage": STAGE, "status": "failed", "prefix": args.prefix,
            "job_id": args.job_id, "run_id": run_id, "start_time": started_at, "artifacts": {}, "errors": [error]}


def run(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    try:
        _validate_prefix(args.prefix)
    except ValueError as exc:
        print(f"wgs_index failed: {exc}", file=sys.stderr)
        return 1
    final_dir = workspace / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = final_dir / f"{args.prefix}.{STAGE}.manifest.json"
    run_id, started_at = uuid.uuid4().hex, datetime.now(timezone.utc).isoformat()
    try:
        if args.threads < 1:
            raise ValueError("threads must be positive")
        reference_input = Path(args.reference_fasta).resolve()
        reference_contigs = _validate_reference(reference_input)
        reference_sha256 = sha256_file(reference_input)
        reference_id = build_wgs_reference_id(reference_sha256)
        versions = {"samtools": _tool_version(args.samtools), "bwa_mem2": _tool_version(args.bwa_mem2, bwa_mem2=True), "gatk": _tool_version(args.gatk)}
        parameters = {"samtools": args.samtools, "bwa_mem2": args.bwa_mem2, "gatk": args.gatk, "threads": args.threads, "components": list(COMPONENTS)}
        analysis_id = build_wgs_analysis_id(STAGE, [reference_sha256], parameters, versions, method="samtools_gatk_bwa_mem2")
        if _refresh_reuse_authority(
            manifest_path, workspace=workspace, job_id=args.job_id, prefix=args.prefix,
            reference_id=reference_id, analysis_id=analysis_id, parameters=parameters,
            reused_at=started_at, reuse_attempt_id=run_id,
        ):
            return 0

        attempt = workspace / ".attempts" / run_id
        bundle_work = attempt / "bundle"
        bundle_work.mkdir(parents=True, exist_ok=False)
        reference = bundle_work / f"{args.prefix}.{STAGE}.reference.fasta"
        shutil.copy2(reference_input, reference)
        dictionary = bundle_work / f"{args.prefix}.{STAGE}.reference.dict"
        _run_tool([args.samtools, "faidx", str(reference)], "samtools faidx")
        _run_tool([args.gatk, "CreateSequenceDictionary", "-R", str(reference), "-O", str(dictionary)], "GATK CreateSequenceDictionary")
        _run_tool([args.bwa_mem2, "index", str(reference)], "bwa-mem2 index")
        outputs = _required_paths(reference, dictionary)
        missing = [key for key, path in outputs.items() if not path.is_file() or path.stat().st_size <= 0]
        if missing:
            raise RuntimeError(f"missing or empty required index output(s): {', '.join(missing)}")
        _validate_fai(outputs["reference_fai"], reference_contigs)
        _validate_sequence_dictionary(outputs["reference_dict"], reference_contigs)
        destination = final_dir / ".wgs_index_runs" / args.prefix / run_id
        artifacts = {"reference_fasta": _artifact(workspace, reference, destination / reference.name, "reference_fasta")}
        for key, source in outputs.items():
            role = "bwa_mem2_index_component" if key.startswith("bwa_mem2_index_component.") else key
            component = key.removeprefix("bwa_mem2_index_component.") if role == "bwa_mem2_index_component" else ""
            artifacts[key] = _artifact(workspace, source, destination / source.name, role, component)
        manifest = {"manifest_version": "1.0", "stage": STAGE, "status": "success", "prefix": args.prefix, "job_id": args.job_id,
                    "run_id": run_id, "start_time": started_at, "workspace": str(workspace), "wgs_reference_id": reference_id,
                    "wgs_analysis_id": analysis_id, "parameters": parameters,
                    "software": {name: {"path": getattr(args, name if name != "bwa_mem2" else "bwa_mem2"), "version": version} for name, version in versions.items()},
                    "metrics": {"required_index_outputs": 7, "components": list(COMPONENTS)}, "artifacts": artifacts}
        manifest_work = attempt / "manifest.json"
        _atomic_json(manifest_work, manifest)
        if not _publish_transaction(bundle_work, destination, manifest_work, manifest_path, started_at):
            raise RuntimeError("stale WGS index attempt cannot replace a newer successful run")
        return 0
    except Exception as exc:
        failure = _failure_manifest(args, str(exc), run_id, started_at)
        failure_path = manifest_path if not manifest_path.exists() else final_dir / f"{args.prefix}.{STAGE}.failed.{run_id}.manifest.json"
        _atomic_json(failure_path, failure)
        print(f"wgs_index failed: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
