"""Resolve one existing file inside the current PlantAI job."""

from __future__ import annotations

import os
import re
import json
from pathlib import Path, PurePosixPath


JOB_AUTHORIZED_DATA_ROOTS = ".authorized_data_roots.json"


def configured_allowed_data_roots() -> tuple[Path, ...]:
    """Return existing directories configured for server-path loading."""
    roots: list[Path] = []
    for raw in os.environ.get("BBAV5_ALLOWED_DATA_ROOTS", "").split(os.pathsep):
        if not raw.strip():
            continue
        try:
            root = Path(raw.strip()).expanduser().resolve(strict=True)
        except OSError:
            continue
        if root.is_dir() and root not in roots:
            roots.append(root)
    return tuple(roots)


def job_authorized_data_roots(job_input_dir: Path) -> tuple[Path, ...]:
    manifest = Path(job_input_dir) / JOB_AUTHORIZED_DATA_ROOTS
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    roots: list[Path] = []
    for raw in payload.get("roots", []) if isinstance(payload, dict) else []:
        try:
            root = Path(str(raw)).expanduser().resolve(strict=True)
        except OSError:
            continue
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def record_job_authorized_data_roots(job_input_dir: Path, roots: tuple[Path, ...]) -> None:
    job_input_dir = Path(job_input_dir)
    job_input_dir.mkdir(parents=True, exist_ok=True)
    existing = list(job_authorized_data_roots(job_input_dir))
    for root in roots:
        resolved = Path(root).expanduser().resolve(strict=True)
        if resolved not in existing:
            existing.append(resolved)
    manifest = job_input_dir / JOB_AUTHORIZED_DATA_ROOTS
    pending = manifest.with_suffix(".tmp")
    pending.write_text(
        json.dumps({"roots": [str(root) for root in existing]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    pending.replace(manifest)


def resolve_allowed_external_path(
    requested_path: str | Path,
    *,
    additional_roots: tuple[Path, ...] = (),
) -> Path:
    """Resolve one existing path inside a configured server data root."""
    try:
        path = Path(requested_path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"路径不存在: {requested_path}") from exc
    for root in (*configured_allowed_data_roots(), *additional_roots):
        try:
            path.relative_to(root)
            return path
        except ValueError:
            continue
    raise ValueError("路径不在 BBAV5_ALLOWED_DATA_ROOTS 授权目录内。")


def _safe_job_id(job_id: str) -> str:
    value = str(job_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("A safe current job id is required.")
    return value


def _is_allowed_suffix(path: Path, allowed_suffixes: set[str]) -> bool:
    lower = path.name.lower()
    return any(lower.endswith(suffix) or lower.endswith(f"{suffix}.gz") for suffix in allowed_suffixes)


def _validate_candidate(
    path: Path,
    root: Path,
    allowed_suffixes: set[str],
    *,
    authorized_external_roots: tuple[Path, ...] = (),
) -> Path:
    if root.is_symlink():
        raise ValueError("The current job root must not be a symlink.")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError("File must stay inside the current job.") from exc
    current = root
    for index, part in enumerate(relative.parts):
        current = current / part
        if current.is_symlink():
            if index != len(relative.parts) - 1:
                raise ValueError("Current-job directory symlinks are not allowed.")
            try:
                target = resolve_allowed_external_path(
                    current,
                    additional_roots=authorized_external_roots,
                )
            except ValueError as exc:
                raise ValueError("Current-job file symlink target is not authorized.") from exc
            if not target.is_file():
                raise ValueError("Current-job file symlink target was not found.")
    if not path.is_file():
        raise ValueError("Current-job file was not found.")
    if not _is_allowed_suffix(path, allowed_suffixes):
        raise ValueError("Current-job file type is not allowed.")
    return path


def resolve_job_file(
    project_root: Path,
    job_id: str,
    workspace: Path,
    requested_path: str,
    allowed_suffixes: set[str],
) -> Path:
    """Resolve a qualified or uniquely named file under input/workspace/final."""
    job_id = _safe_job_id(job_id)
    project_root = Path(project_root)
    workspace = Path(workspace)
    roots = {
        "input": project_root / "input" / job_id,
        "workspace": workspace,
        "final": workspace / "final",
    }
    authorized_external_roots = job_authorized_data_roots(project_root / "input" / job_id)
    raw = str(requested_path or "").strip().replace("\\", "/")
    pure = PurePosixPath(raw)
    if not raw or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("File must be a safe path inside the current job.")

    if pure.parts[0] in roots:
        if len(pure.parts) < 2:
            raise ValueError("File must be a safe path inside the current job.")
        root = roots[pure.parts[0]]
        return _validate_candidate(
            root.joinpath(*pure.parts[1:]),
            root,
            allowed_suffixes,
            authorized_external_roots=authorized_external_roots,
        )

    if len(pure.parts) != 1:
        raise ValueError("File must be a safe path inside the current job.")
    matches: list[Path] = []
    for root in roots.values():
        if not root.is_dir() or root.is_symlink():
            continue
        for candidate in root.rglob(pure.name):
            try:
                matches.append(
                    _validate_candidate(
                        candidate,
                        root,
                        allowed_suffixes,
                        authorized_external_roots=authorized_external_roots,
                    )
                )
            except ValueError:
                continue
    matches = list(dict.fromkeys(matches))
    if not matches:
        raise ValueError("Current-job file was not found.")
    if len(matches) != 1:
        raise ValueError("Current-job file name is ambiguous; use input/, workspace/, or final/.")
    return matches[0]
