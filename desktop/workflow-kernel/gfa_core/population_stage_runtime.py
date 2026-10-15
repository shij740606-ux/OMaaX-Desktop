"""Small shared runtime guarantees for formal population-genomics stages."""

from __future__ import annotations

import json
import os
import re
import shlex
import uuid
import fcntl
from pathlib import Path
from typing import Any, Mapping, Sequence

from gfa_core.genotype_context import sha256_file


def population_manifest_base(
    *,
    workspace: Path,
    stage: str,
    prefix: str,
    job_id: str,
    run_id: str,
    started: Any,
    runner_command: Sequence[str],
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    """Create the core manifest_v1 fields shared by population stages."""
    timestamp = started.isoformat()
    return {
        "manifest_version": "1.0",
        "job_id": job_id,
        "stage": stage,
        "run_id": run_id,
        "prefix": prefix,
        "timestamp": timestamp,
        "status": "failed",
        "start_time": timestamp,
        "end_time": timestamp,
        "elapsed_sec": 0.0,
        "cmd": [str(item) for item in runner_command],
        "workspace_root": str(workspace),
        "workspace": str(workspace / "final"),
        "input_dir": str(workspace.parent.parent / "input" / job_id),
        "artifacts": {},
        "parameters": dict(parameters),
        "metrics": {},
    }


def _safe_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._")
    return token or "population"


def acquire_identity_lock(workspace: Path, stage: str, prefix: str, identity12: str):
    """Serialize attempts that would publish the same scientific identity."""
    lock_path = workspace / ".locks" / f"{_safe_token(prefix)}.{_safe_token(stage)}.{_safe_token(identity12)}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def release_identity_lock(handle) -> None:
    if handle is None:
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def write_reproducibility_bundle(
    *,
    workspace: Path,
    stage: str,
    prefix: str,
    identity12: str,
    run_id: str,
    runner_command: Sequence[str],
    manifest_source: Path,
    public_manifest: Path,
    manifest: Mapping[str, Any],
    environment: str,
    artifact_sources: Mapping[str, Path] | None = None,
) -> Path:
    """Write the five-file stage-local bundle, publishing the directory atomically."""
    parent = workspace / "reproducibility" / stage / f"{_safe_token(prefix)}.{_safe_token(identity12)}"
    destination = parent / run_id
    temporary = parent / f".{run_id}.{uuid.uuid4().hex}.publishing"
    temporary.mkdir(parents=True, exist_ok=False)
    canonical = shlex.join([str(item) for item in runner_command])
    commands = temporary / "commands.sh"
    commands.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + canonical + "\n", encoding="utf-8")
    commands.chmod(0o755)
    (temporary / "environment.txt").write_text(environment.rstrip() + "\n", encoding="utf-8")
    (temporary / "progress.json").write_text(json.dumps({
        "stage": stage, "status": manifest.get("status"), "run_id": run_id,
        "manifest_path": str(public_manifest.relative_to(workspace)),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (temporary / "provenance.json").write_text(json.dumps({
        "stage": stage, "run_id": run_id, "runner_command": canonical,
        "method_version": manifest.get("method_version"),
        "contract_version": manifest.get("contract_version"),
        "tool_versions": manifest.get("tool_versions", {}),
        "parameters": manifest.get("parameters", {}),
        "inputs": manifest.get("inputs", []),
        "artifacts": manifest.get("artifacts", {}),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksum_entries = [(manifest_source, str(public_manifest.relative_to(workspace)))]
    for role, artifact in (manifest.get("artifacts") or {}).items():
        if not isinstance(artifact, Mapping):
            continue
        source = (artifact_sources or {}).get(str(role))
        if source is None and artifact.get("path"):
            source = workspace / str(artifact["path"])
        if source is not None and source.is_file():
            checksum_entries.append((source, str(artifact.get("path") or source.name)))
    (temporary / "checksums.sha256").write_text("".join(
        f"{sha256_file(path)}  {label}\n" for path, label in sorted(checksum_entries, key=lambda item: item[1])
    ), encoding="utf-8")
    parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"Reproducibility bundle already exists: {destination}")
    os.replace(temporary, destination)
    return destination
