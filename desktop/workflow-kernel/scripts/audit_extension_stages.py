#!/usr/bin/env python3
"""Audit extension-stage metadata and classify legacy migration status."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
import sys

PROJECT_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_FOR_IMPORTS))

from gfa_core.extension_stage.publish_gate import publish_checks
from gfa_core.extension_stage.spec_v2 import ExtensionSpecV2Error, normalize_spec_v2


def _safe_parameter_name(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith("param_"):
        text = text[6:]
    return text


def _merged_parameter_values(extension: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for raw_name, spec in (extension.get("confirmed_parameters") or extension.get("parameters") or {}).items():
        name = _safe_parameter_name(raw_name)
        if isinstance(spec, dict):
            values[name] = spec.get("value", spec.get("default", ""))
        else:
            values[name] = spec
    return values


def classify_extension(extension: dict[str, Any]) -> tuple[str, list[str], dict[str, Any]]:
    reasons: list[str] = []
    normalized: dict[str, Any] = {}
    try:
        normalized = normalize_spec_v2(
            extension,
            require_code_artifacts=(extension.get("converter") or {}).get("type") == "deepseek_api",
        )
    except ExtensionSpecV2Error as exc:
        reasons.append(f"spec_v2: {exc}")
    checks = publish_checks(extension, _merged_parameter_values(extension))
    if checks.get("errors"):
        reasons.extend(str(x) for x in checks.get("errors") or [])
    status = str(extension.get("status") or "")
    if status == "published" and checks.get("passed"):
        return "runnable", reasons, normalized
    if normalized and status in {"draft", "confirmed", "validated", "validation_failed", "trial_succeeded", "published"}:
        return "migratable", reasons, normalized
    if status in {"archived", "deprecated", "disabled"}:
        return "archived", reasons, normalized
    return "blocked", reasons, normalized


def audit(project_root: Path) -> dict[str, Any]:
    store_root = project_root / "extension_stages"
    rows = []
    for ext_path in sorted(store_root.glob("*/extension.json")):
        try:
            extension = json.loads(ext_path.read_text(encoding="utf-8"))
        except Exception as exc:
            rows.append({
                "extension_id": ext_path.parent.name,
                "classification": "blocked",
                "status": "unreadable",
                "reasons": [str(exc)],
                "path": str(ext_path),
            })
            continue
        classification, reasons, normalized = classify_extension(extension)
        rows.append({
            "extension_id": extension.get("extension_id", ext_path.parent.name),
            "display_name": extension.get("display_name", ext_path.parent.name),
            "status": extension.get("status", ""),
            "classification": classification,
            "analysis_type": normalized.get("analysis_type") or extension.get("analysis_type", ""),
            "tool_family": normalized.get("tool_family") or extension.get("tool_family", ""),
            "converter_type": (extension.get("converter") or {}).get("type", ""),
            "reasons": reasons,
            "path": str(ext_path.relative_to(project_root)),
        })
    counts = Counter(row["classification"] for row in rows)
    return {
        "schema_version": "extension_stage_legacy_audit_v1",
        "generated_at": datetime.now().isoformat(),
        "project_root": str(project_root),
        "counts": dict(sorted(counts.items())),
        "rows": rows,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Extension Stage Legacy Audit",
        "",
        f"Generated at: {report['generated_at']}",
        f"Project root: `{report['project_root']}`",
        "",
        "## Summary",
        "",
    ]
    for key, value in report.get("counts", {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Extensions", ""])
    for row in report.get("rows", []):
        lines.append(f"### {row['extension_id']}")
        lines.append(f"- classification: `{row['classification']}`")
        lines.append(f"- status: `{row.get('status', '')}`")
        lines.append(f"- analysis_type: `{row.get('analysis_type', '')}`")
        lines.append(f"- tool_family: `{row.get('tool_family', '')}`")
        lines.append(f"- converter_type: `{row.get('converter_type', '')}`")
        lines.append(f"- path: `{row.get('path', '')}`")
        reasons = row.get("reasons") or []
        if reasons:
            lines.append("- reasons:")
            for reason in reasons[:20]:
                lines.append(f"  - {reason}")
        else:
            lines.append("- reasons: none")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    report = audit(project_root)
    json_path = project_root / "extension_stages" / "_migration_report.json"
    md_path = project_root / "docs" / f"extension_stage_legacy_audit_{args.date}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_markdown(report, md_path)
    print(json.dumps({"success": True, "json": str(json_path), "markdown": str(md_path), "counts": report["counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
