"""Status adapter for extension_stage virtual stage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gfa_core.extension_stage.runtime_view import check_output_artifacts, runtime_view_for_run_record


def latest_extension_status(job_id: str, workspace_root: Path) -> dict[str, Any]:
    run_root = Path(workspace_root) / job_id / "extension_runs"
    if not run_root.exists():
        return {"stage": "extension_stage", "status": "not_found", "completed": False, "errors": [f"Extension run directory not found: {run_root}"]}
    manifests = sorted(run_root.glob("*/*/extension_run.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not manifests:
        return {"stage": "extension_stage", "status": "not_found", "completed": False, "errors": [f"No extension_run.json found under {run_root}"]}
    path = manifests[0]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"stage": "extension_stage", "status": "invalid_manifest", "completed": False, "manifest_path": str(path), "errors": [str(exc)]}
    status = str(data.get("status") or "unknown")
    input_root = Path(workspace_root).parent / "input"
    view = runtime_view_for_run_record(data, path, workspace_root=workspace_root, input_root=input_root)
    diagnosis = view.get("diagnosis_report") or {}
    binding = view.get("binding_report") or {}
    artifacts_checked, missing_artifacts = check_output_artifacts(view, path)
    errors = []
    if view.get("error"):
        errors.append(str(view.get("error")))
    if diagnosis.get("user_message") and diagnosis.get("user_message") not in errors:
        errors.append(str(diagnosis.get("user_message")))
    if missing_artifacts:
        errors.append("Missing extension output artifact(s): " + ", ".join(missing_artifacts[:8]))
    return {
        "stage": "extension_stage",
        "status": status,
        "completed": status == "success" and not missing_artifacts,
        "manifest_path": str(path.absolute()),
        "manifest": view,
        "artifacts_checked": artifacts_checked,
        "missing_artifacts": missing_artifacts,
        "errors": errors,
        "diagnosis_report": diagnosis,
        "binding_report": binding,
        "manifest_quality": {
            "has_binding_report": bool(binding),
            "has_diagnosis_report": bool(diagnosis),
            "has_input_checksums": bool(view.get("input_file_checksums")),
            "has_output_checksums": bool(view.get("output_file_checksums")),
            "has_command_template": bool(view.get("command_template")),
            "archetype_id": view.get("archetype_id", ""),
            "template_gate_status": view.get("template_gate_status", ""),
            "has_template_gate": bool(view.get("template_gate_status")),
            "has_outputs": artifacts_checked > 0,
            "all_outputs_present": not missing_artifacts,
        },
    }
