"""Benchmark matrix collection for extension-stage publication evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def collect_extension_benchmark(store_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for build_path in sorted(Path(store_root).glob("*/extension_build.json")):
        root = build_path.parent
        try:
            build = json.loads(build_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        ext_path = root / "extension.json"
        extension: dict[str, Any] = {}
        if ext_path.exists():
            try:
                extension = json.loads(ext_path.read_text(encoding="utf-8"))
            except Exception:
                extension = {}
        last_validation = extension.get("last_validation") or {}
        golden = extension.get("golden_comparison") or last_validation.get("golden_comparison") or {}
        diagnosis = last_validation.get("diagnosis_report") or {}
        manifest_score_fields = [
            bool(extension.get("input_roles")),
            bool(extension.get("output_contract")),
            bool(extension.get("generated_code_artifacts") or build.get("generated_code_artifacts")),
            bool(extension.get("build_record")),
            bool(last_validation.get("command") or last_validation.get("rendered_command")),
        ]
        rows.append({
            "extension_id": extension.get("extension_id", root.name),
            "display_name": extension.get("display_name", root.name),
            "status": extension.get("status", build.get("status", "")),
            "analysis_type": extension.get("analysis_type", ""),
            "tool_family": extension.get("tool_family", ""),
            "api_attempts": len(build.get("api_attempts") or []),
            "generated_code_artifacts": len(extension.get("generated_code_artifacts") or build.get("generated_code_artifacts") or []),
            "trial_status": (extension.get("trial_run") or {}).get("status", ""),
            "validation_status": last_validation.get("status", ""),
            "golden_status": golden.get("status", ""),
            "golden_workspace": golden.get("workspace", ""),
            "validation_failure_code": (last_validation.get("failure_classification") or {}).get("code", ""),
            "validation_diagnosis_code": diagnosis.get("code", ""),
            "published": extension.get("status") == "published",
            "publish_gate_passed": (extension.get("publish_checks") or {}).get("passed", ""),
            "manifest_completeness_score": sum(1 for item in manifest_score_fields if item),
            "manifest_completeness_total": len(manifest_score_fields),
            "build_record": str(build_path),
        })
    return rows
