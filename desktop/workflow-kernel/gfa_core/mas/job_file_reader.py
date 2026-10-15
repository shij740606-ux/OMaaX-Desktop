from __future__ import annotations

import json
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from gfa_core.mas.job_path_resolver import (
    job_authorized_data_roots,
    resolve_allowed_external_path,
)


MAX_PREVIEW_BYTES = 65_536
DEFAULT_MAX_LINES = 200
MAX_PREVIEW_LINES = 500
MAX_LINE_CHARS = 16_384
DEFAULT_LIST_LIMIT = 200
MAX_LIST_LIMIT = 500
DEFAULT_LIST_ROOTS = ("input", "final", "logs")
_LIST_ROOTS = {"input", "final", "logs", "workspace"}

_ALLOWED_SUFFIXES = {
    ".bed", ".csv", ".fa", ".faa", ".fasta", ".fna", ".gff", ".gff3",
    ".json", ".jsonl", ".log", ".md", ".newick", ".nwk", ".tab", ".text",
    ".tsv", ".txt", ".yaml", ".yml",
}
_DENIED_SUFFIXES = {".key", ".p12", ".pem", ".pfx"}
_DENIED_PREFIXES = ("credentials", "id_rsa", "secrets")
_DENIED_SUBSTRINGS = ("private_key", "token")


def _error(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "error_code": code, "message": message}


def _valid_job_id(job_id: str) -> bool:
    value = str(job_id or "")
    return bool(value) and value not in {".", ".."} and not Path(value).is_absolute() and "/" not in value and "\\" not in value


def _path_is_sensitive(parts: tuple[str, ...]) -> bool:
    for part in parts:
        lower = part.lower()
        suffix = Path(lower).suffix
        if not part or part in {".", ".."} or part.startswith("."):
            return True
        if lower == ".env" or suffix in _DENIED_SUFFIXES:
            return True
        if lower.startswith(_DENIED_PREFIXES) or any(token in lower for token in _DENIED_SUBSTRINGS):
            return True
    return False


def _parse_qualified_path(requested_path: str) -> tuple[str, tuple[str, ...]] | None:
    raw = str(requested_path or "").strip().replace("\\", "/")
    pure = PurePosixPath(raw)
    if not raw or pure.is_absolute():
        return None
    parts = pure.parts
    if len(parts) < 2 or parts[0] not in {"input", "workspace", "final"}:
        return None
    children = tuple(parts[1:])
    if _path_is_sensitive(children):
        return None
    if parts[0] == "workspace" and children and children[0] == "final":
        return "final", tuple(children[1:])
    return parts[0], children


def _parse_requested_path(
    project_root: Path,
    job_id: str,
    requested_path: str,
) -> tuple[str, tuple[str, ...]] | None:
    raw = str(requested_path or "").strip()
    if not Path(raw).is_absolute():
        return _parse_qualified_path(raw)
    logical_path = Path(os.path.abspath(raw))
    roots = (
        ("input", _root_path(project_root, job_id, "input")),
        ("final", _root_path(project_root, job_id, "final")),
        ("workspace", _root_path(project_root, job_id, "workspace")),
    )
    for root_name, root in roots:
        try:
            relative = logical_path.relative_to(Path(os.path.abspath(root)))
        except ValueError:
            continue
        parts = tuple(relative.parts)
        if parts and not _path_is_sensitive(parts):
            return root_name, parts
    return None


def _root_path(project_root: Path, job_id: str, root_name: str) -> Path:
    if root_name == "input":
        return project_root / "input" / job_id
    if root_name == "final":
        return project_root / "workspace" / job_id / "final"
    return project_root / "workspace" / job_id


def _unique_basename(project_root: Path, job_id: str, basename: str) -> tuple[str, tuple[str, ...]] | dict[str, Any]:
    if "/" in basename or "\\" in basename or _path_is_sensitive((basename,)):
        return _error("path_not_allowed", "Use a safe current-job relative file path.")
    matches: list[tuple[str, tuple[str, ...]]] = []
    for root_name in ("input", "workspace"):
        root = _root_path(project_root, job_id, root_name)
        if not root.is_dir() or root.is_symlink():
            continue
        for current, directories, files in os.walk(root, followlinks=False):
            directories[:] = [name for name in directories if not (Path(current) / name).is_symlink()]
            if basename not in files:
                continue
            relative = (Path(current) / basename).relative_to(root).parts
            if root_name == "workspace" and relative and relative[0] == "final":
                matches.append(("final", tuple(relative[1:])))
            else:
                matches.append((root_name, tuple(relative)))
    unique = list(dict.fromkeys(matches))
    if not unique:
        return _error("file_not_found", "No current-job file matches that path.")
    if len(unique) > 1:
        return _error(
            "ambiguous_path",
            "Multiple current-job files match that basename; use a root-qualified path.",
        )
    return unique[0]


def _open_root_descriptor(project_root: Path, job_id: str, root_name: str) -> int:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    if root_name == "input":
        parent = project_root / "input"
        tail = (job_id,)
    else:
        parent = project_root / "workspace"
        tail = (job_id,) + (("final",) if root_name == "final" else ())
    descriptor = os.open(parent, directory_flags)
    try:
        for component in tail:
            next_descriptor = os.open(component, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_file_descriptor(project_root: Path, job_id: str, root_name: str, parts: tuple[str, ...]) -> int:
    if not parts or _path_is_sensitive(parts):
        raise PermissionError("unsafe path")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = _open_root_descriptor(project_root, job_id, root_name)
    try:
        for component in parts[:-1]:
            next_descriptor = os.open(component, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        file_descriptor = os.open(parts[-1], file_flags, dir_fd=descriptor)
        info = os.fstat(file_descriptor)
        if not stat.S_ISREG(info.st_mode):
            os.close(file_descriptor)
            raise PermissionError("not a regular file")
        return file_descriptor
    finally:
        os.close(descriptor)


def _open_authorized_link(project_root: Path, job_id: str, root_name: str, parts: tuple[str, ...]) -> int:
    logical_path = _root_path(project_root, job_id, root_name).joinpath(*parts)
    if not logical_path.is_symlink():
        raise PermissionError("not an authorized file link")
    try:
        target = resolve_allowed_external_path(
            logical_path,
            additional_roots=job_authorized_data_roots(
                Path(project_root) / "input" / job_id
            ),
        )
    except ValueError as exc:
        raise PermissionError("external target is not authorized") from exc
    file_descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    info = os.fstat(file_descriptor)
    if not stat.S_ISREG(info.st_mode):
        os.close(file_descriptor)
        raise PermissionError("not a regular file")
    return file_descriptor


def _read_prefix(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    remaining = MAX_PREVIEW_BYTES + 1
    while remaining > 0:
        chunk = os.read(descriptor, min(16_384, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _artifact_bindings(final_dir: Path, workspace_dir: Path) -> dict[Path, dict[str, str]]:
    bindings: dict[Path, dict[str, str]] = {}
    if not final_dir.is_dir():
        return bindings
    for manifest_path in sorted(final_dir.glob("*.manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        stage = str(manifest.get("stage") or "")
        status = str(manifest.get("status") or "")
        bindings[manifest_path.absolute()] = {
            "stage": stage,
            "status": status,
            "artifact_key": "manifest",
        }
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict):
            continue
        for artifact_key, artifact in artifacts.items():
            if not isinstance(artifact, dict):
                continue
            raw_path = str(artifact.get("full_path") or artifact.get("path") or "").strip()
            if not raw_path:
                continue
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = workspace_dir / candidate
            bindings[candidate.absolute()] = {
                "stage": stage,
                "status": status,
                "artifact_key": str(artifact_key),
            }
    return bindings


def _inventory_root(
    project_root: Path,
    job_id: str,
    root_name: str,
) -> tuple[Path, str]:
    if root_name == "input":
        return _root_path(project_root, job_id, "input"), "input"
    if root_name == "final":
        return _root_path(project_root, job_id, "final"), "final"
    if root_name == "logs":
        return _root_path(project_root, job_id, "workspace") / "logs", "workspace/logs"
    return _root_path(project_root, job_id, "workspace"), "workspace"


def list_job_files(
    project_root: Path,
    job_id: str,
    *,
    roots: tuple[str, ...] = DEFAULT_LIST_ROOTS,
    limit: int = DEFAULT_LIST_LIMIT,
    cursor: int = 0,
) -> dict[str, Any]:
    project_root = Path(project_root)
    if not _valid_job_id(job_id):
        return _error("invalid_job_id", "A valid current job is required.")
    selected_roots = tuple(dict.fromkeys(str(root) for root in roots))
    if not selected_roots or any(root not in _LIST_ROOTS for root in selected_roots):
        return _error("invalid_root", "Use input, final, logs, or workspace.")
    bounded_limit = min(max(int(limit), 1), MAX_LIST_LIMIT)
    bounded_cursor = max(int(cursor), 0)
    workspace_dir = _root_path(project_root, job_id, "workspace")
    bindings = _artifact_bindings(workspace_dir / "final", workspace_dir)
    items: list[dict[str, Any]] = []

    for root_name in selected_roots:
        root, handle_prefix = _inventory_root(project_root, job_id, root_name)
        if not root.is_dir() or root.is_symlink():
            continue
        for current, directories, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            directories[:] = sorted(
                name
                for name in directories
                if not (current_path / name).is_symlink()
                and not (
                    root_name == "workspace"
                    and current_path == root
                    and name in {"final", "logs"}
                )
            )
            for filename in sorted(files):
                path = current_path / filename
                relative_parts = path.relative_to(root).parts
                if _path_is_sensitive(tuple(relative_parts)):
                    continue
                try:
                    if path.is_symlink():
                        target = resolve_allowed_external_path(
                            path,
                            additional_roots=job_authorized_data_roots(
                                project_root / "input" / job_id
                            ),
                        )
                        size = target.stat().st_size
                    else:
                        size = path.stat().st_size
                except (OSError, ValueError):
                    continue
                handle = f"{handle_prefix}/{'/'.join(relative_parts)}"
                binding = bindings.get(path.absolute(), {})
                items.append(
                    {
                        "file_handle": handle,
                        "root": root_name,
                        "name": filename,
                        "suffix": path.suffix.lower(),
                        "size": size,
                        "stage": binding.get("stage", ""),
                        "status": binding.get("status", ""),
                        "artifact_key": binding.get("artifact_key", ""),
                    }
                )

    items.sort(key=lambda item: item["file_handle"])
    page = items[bounded_cursor : bounded_cursor + bounded_limit]
    next_cursor = bounded_cursor + len(page)
    if next_cursor >= len(items):
        next_cursor = None
    return {
        "success": True,
        "roots": list(selected_roots),
        "files": page,
        "total": len(items),
        "cursor": bounded_cursor,
        "next_cursor": next_cursor,
        "truncated": next_cursor is not None,
    }


def read_job_file_preview(
    project_root: Path,
    job_id: str,
    requested_path: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
) -> dict[str, Any]:
    project_root = Path(project_root)
    if not _valid_job_id(job_id):
        return _error("invalid_job_id", "A valid current job is required.")
    raw = str(requested_path or "").strip()
    parsed: tuple[str, tuple[str, ...]] | dict[str, Any] | None
    if "/" not in raw and "\\" not in raw:
        parsed = _unique_basename(project_root, job_id, raw)
    else:
        parsed = _parse_requested_path(project_root, job_id, raw)
    if isinstance(parsed, dict):
        return parsed
    if parsed is None or not parsed[1]:
        return _error("path_not_allowed", "Use a safe current-job relative file path.")
    root_name, parts = parsed
    suffix = Path(parts[-1]).suffix.lower()
    try:
        descriptor = _open_file_descriptor(project_root, job_id, root_name, parts)
    except FileNotFoundError:
        return _error("file_not_found", "No current-job file matches that path.")
    except (NotADirectoryError, OSError, PermissionError):
        try:
            descriptor = _open_authorized_link(project_root, job_id, root_name, parts)
        except (NotADirectoryError, OSError, PermissionError):
            return _error("path_not_allowed", "Use a safe current-job relative file path.")
    try:
        info = os.fstat(descriptor)
        payload = _read_prefix(descriptor)
    finally:
        os.close(descriptor)
    if b"\x00" in payload:
        return _error("binary_file", "Binary files cannot be previewed in conversation.")
    if suffix not in _ALLOWED_SUFFIXES:
        return _error("unsupported_file_type", "That file type is not allowed for conversation preview.")
    try:
        text = payload.decode("utf-8")
        encoding_warning = False
    except UnicodeDecodeError:
        text = payload.decode("utf-8", errors="replace")
        encoding_warning = True
    requested_lines = min(max(int(max_lines), 1), MAX_PREVIEW_LINES)
    observed_lines = text.splitlines()
    selected = observed_lines[:requested_lines]
    rendered = [f"{index}: {line[: MAX_LINE_CHARS - 12]}" for index, line in enumerate(selected, 1)]
    preview = "\n".join(rendered)
    encoded_preview = preview.encode("utf-8")
    if len(encoded_preview) > MAX_PREVIEW_BYTES:
        encoded_preview = encoded_preview[:MAX_PREVIEW_BYTES]
        preview = encoded_preview.decode("utf-8", errors="ignore")
    truncated = (
        len(payload) > MAX_PREVIEW_BYTES
        or len(observed_lines) > requested_lines
        or any(len(line) > MAX_LINE_CHARS - 12 for line in selected)
        or len(preview.encode("utf-8")) < len("\n".join(rendered).encode("utf-8"))
    )
    handle = f"{root_name}/{'/'.join(parts)}"
    return {
        "success": True,
        "file_handle": handle,
        "size": info.st_size,
        "line_count": len(observed_lines),
        "returned_lines": len(preview.splitlines()),
        "preview": preview,
        "preview_bytes": len(preview.encode("utf-8")),
        "truncated": truncated,
        "encoding_warning": encoding_warning,
    }
