"""Collect execution facts without interpreting scientific semantics."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
from typing import Any, Callable

from .process_control import ProcessIdentity, start_managed_process, stop_process_group


def _files_below(root: Path) -> dict[str, Path]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory_files(root: Path) -> dict[str, dict[str, Any]]:
    """Return a JSON-ready snapshot of every file visible below ``root``."""

    inventory: dict[str, dict[str, Any]] = {}
    for relative, path in _files_below(root).items():
        inventory[relative] = {
            "kind": "symlink" if path.is_symlink() else "file",
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
    return inventory


def stage_package(*, raw_code: Path, raw_data: Path, work_dir: Path) -> dict[str, list[str]]:
    """Merge code and data into one work directory without changing the sources."""

    raw_code = Path(raw_code).resolve()
    raw_data = Path(raw_data).resolve()
    code_files = _files_below(raw_code)
    data_files = _files_below(raw_data)
    collisions = sorted(set(code_files) & set(data_files))
    if collisions:
        raise ValueError(f"Code/data path collision: {', '.join(collisions)}")

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    for relative, source in code_files.items():
        destination = work_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            raise ValueError(f"Work directory path already exists: {relative}")
        shutil.copy2(source, destination)

    for relative, source in data_files.items():
        destination = work_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            raise ValueError(f"Work directory path already exists: {relative}")
        destination.symlink_to(source)

    return {
        "code_files": sorted(code_files),
        "data_files": sorted(data_files),
    }


def _stdout_fact(stdout_path: Path) -> dict[str, Any]:
    size = stdout_path.stat().st_size
    if not size:
        return {"present": False, "path": str(stdout_path), "size": 0, "preview": ""}
    text = stdout_path.read_text(encoding="utf-8", errors="replace").strip()
    return {
        "present": True,
        "path": str(stdout_path),
        "size": size,
        "preview": text[-4000:],
    }


def run_raw_baseline(
    *,
    command: str,
    raw_code: Path,
    raw_data: Path,
    run_dir: Path,
    env: dict[str, str] | None = None,
    input_overrides: dict[str, Path] | None = None,
    on_process_start: Callable[[ProcessIdentity], None] | None = None,
) -> dict[str, Any]:
    """Run the exact entry command once and capture before/after evidence.

    Deliberately no timeout is supplied: long-running bioinformatics processes are
    not failures merely because they exceed an arbitrary wall-clock duration.
    """

    run_dir = Path(run_dir)
    work_dir = run_dir / "work"
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    staged = stage_package(raw_code=raw_code, raw_data=raw_data, work_dir=work_dir)
    rebound: dict[str, str] = {}
    for relative, source in sorted((input_overrides or {}).items()):
        source = Path(source).resolve()
        if not source.is_file():
            raise ValueError(f"Input override does not exist: {source}")
        destination = work_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_dir() and not destination.is_symlink():
            raise ValueError(f"Input override destination is a directory: {relative}")
        if destination.exists() or destination.is_symlink():
            destination.unlink()
        destination.symlink_to(source)
        rebound[relative] = str(source)
    staged["input_overrides"] = rebound
    before = inventory_files(work_dir)

    stdout_path = logs_dir / "stdout.log"
    stderr_path = logs_dir / "stderr.log"
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
        process, process_identity = start_managed_process(
            ["bash", "-c", command],
            cwd=work_dir,
            env=process_env,
            stdout=stdout_handle,
            stderr=stderr_handle,
        )
        try:
            if on_process_start is not None:
                on_process_start(dict(process_identity))
        except BaseException:
            stop_process_group(process_identity)
            process.wait()
            raise
        returncode = process.wait()

    after = inventory_files(work_dir)
    new_files = sorted(set(after) - set(before))
    modified_files = sorted(
        relative
        for relative in set(after) & set(before)
        if after[relative]["sha256"] != before[relative]["sha256"]
    )
    return {
        "command": command,
        "returncode": returncode,
        "success": returncode == 0,
        "process": process_identity,
        "run_dir": str(run_dir),
        "work_dir": str(work_dir),
        "staged": staged,
        "before_files": before,
        "after_files": after,
        "outputs": {
            "new_files": new_files,
            "modified_files": modified_files,
            "stdout": _stdout_fact(stdout_path),
        },
        "logs": {
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
        },
    }
