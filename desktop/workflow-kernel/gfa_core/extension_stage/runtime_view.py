"""Runtime view helpers for extension-stage run records.

Stored records may come from a symlinked workspace or from historical backup
copies. These helpers keep API/status payloads tied to the current job path
without mutating provenance files on disk.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any


PREVIEW_LIMIT = 1200


def runtime_view_for_run_record(
    run: dict[str, Any],
    record_path: Path,
    *,
    workspace_root: Path | None = None,
    input_root: Path | None = None,
) -> dict[str, Any]:
    view = _transform(copy.deepcopy(run), record_path, workspace_root=workspace_root, input_root=input_root)
    _sanitize_output_previews(view)
    return view


def check_output_artifacts(run: dict[str, Any], record_path: Path) -> tuple[int, list[str]]:
    run_dir = Path(record_path).parent
    outputs = run.get("outputs") or []
    missing: list[str] = []
    checked = 0
    for output in outputs:
        if not isinstance(output, dict):
            continue
        rel_path = str(output.get("path") or output.get("name") or "").strip()
        if not rel_path:
            continue
        checked += 1
        target = Path(rel_path)
        if not target.is_absolute():
            target = run_dir / target
        try:
            target_resolved = target.resolve()
            run_resolved = run_dir.resolve()
            target_resolved.relative_to(run_resolved)
        except ValueError:
            missing.append(rel_path)
            continue
        if not target.exists() or not target.is_file():
            missing.append(rel_path)
    return checked, missing


def _transform(
    value: Any,
    record_path: Path,
    *,
    workspace_root: Path | None,
    input_root: Path | None,
) -> Any:
    if isinstance(value, dict):
        return {key: _transform(item, record_path, workspace_root=workspace_root, input_root=input_root) for key, item in value.items()}
    if isinstance(value, list):
        return [_transform(item, record_path, workspace_root=workspace_root, input_root=input_root) for item in value]
    if isinstance(value, str):
        return _rebase_path_string(value, record_path, workspace_root=workspace_root, input_root=input_root)
    return value


def _rebase_path_string(
    text: str,
    record_path: Path,
    *,
    workspace_root: Path | None,
    input_root: Path | None,
) -> str:
    run_dir = Path(record_path).parent.absolute()
    try:
        extension_id = run_dir.parent.name
        job_id = run_dir.parents[2].name
    except Exception:
        return text
    current_workspace_job = (Path(workspace_root).absolute() / job_id) if workspace_root else run_dir.parents[2].absolute()
    current_input_job = (Path(input_root).absolute() / job_id) if input_root else (current_workspace_job.parent.parent / "input" / job_id)
    current_project_root = current_workspace_job.parent.parent.absolute()

    replacements = [
        (
            rf"/[^\s\"']*/workspace/{re.escape(job_id)}/extension_runs/{re.escape(extension_id)}/{re.escape(run_dir.name)}",
            str(run_dir),
        ),
        (
            rf"/[^\s\"']*/workspace/{re.escape(job_id)}",
            str(current_workspace_job),
        ),
        (
            rf"/[^\s\"']*/input/{re.escape(job_id)}",
            str(current_input_job),
        ),
        (
            r"/[^\s\"']*/bbav5_0502_Yang_Tihuan\.7880(?:\.bak)?",
            str(current_project_root),
        ),
    ]
    result = text
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result)
    return result


def _sanitize_output_previews(run: dict[str, Any]) -> None:
    outputs = run.get("outputs") or []
    for output in outputs:
        if not isinstance(output, dict):
            continue
        preview = output.get("preview")
        if not isinstance(preview, str) or not preview:
            continue
        if len(preview) > PREVIEW_LIMIT or _looks_binary_text(preview):
            name = str(output.get("name") or output.get("path") or "output")
            size = output.get("size")
            if size is None:
                output["preview"] = f"[preview omitted: {name}]"
            else:
                output["preview"] = f"[binary preview omitted: {name}, {size} bytes]"


def _looks_binary_text(text: str) -> bool:
    if "\x00" in text:
        return True
    if text.startswith("\x1f\x08"):
        return True
    sample = text[:4096]
    control = sum(1 for char in sample if ord(char) < 32 and char not in "\t\n\r")
    replacement = sample.count("\ufffd")
    return ((control + replacement) / max(len(sample), 1)) > 0.02
