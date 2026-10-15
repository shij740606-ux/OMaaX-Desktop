#!/usr/bin/env python3
"""Generate publication evidence for extension-stage build/run quality."""

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

from gfa_core.extension_stage.benchmark import collect_extension_benchmark


def _load_runs(workspace_root: Path) -> list[dict[str, Any]]:
    runs = []
    for path in sorted(workspace_root.glob("*/extension_runs/*/*/extension_run.json")):
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        run["manifest_path"] = str(path)
        runs.append(run)
    return runs


def build_report(project_root: Path) -> dict[str, Any]:
    store_root = project_root / "extension_stages"
    workspace_root = project_root / "workspace"
    benchmark_rows = collect_extension_benchmark(store_root)
    runs = _load_runs(workspace_root)
    run_status = Counter(str(run.get("status") or "unknown") for run in runs)
    diagnosis_codes = Counter(str((run.get("diagnosis_report") or {}).get("code") or "none") for run in runs)
    manifest_quality = []
    required_fields = [
        "spec_version",
        "analysis_type",
        "tool_family",
        "command_template",
        "rendered_command",
        "binding_report",
        "diagnosis_report",
        "input_file_checksums",
        "converted_code_checksums",
        "output_file_checksums",
        "prefix_consistency",
    ]
    for run in runs:
        present = [field for field in required_fields if field in run and run.get(field) not in (None, "")]
        manifest_quality.append({
            "run_id": run.get("run_id", ""),
            "extension_id": run.get("extension_id", ""),
            "status": run.get("status", ""),
            "score": len(present),
            "total": len(required_fields),
            "missing": [field for field in required_fields if field not in present],
            "manifest_path": run.get("manifest_path", ""),
        })
    return {
        "schema_version": "extension_stage_benchmark_evidence_v1",
        "generated_at": datetime.now().isoformat(),
        "project_root": str(project_root),
        "benchmark_rows": benchmark_rows,
        "run_count": len(runs),
        "run_status_counts": dict(sorted(run_status.items())),
        "diagnosis_code_counts": dict(sorted(diagnosis_codes.items())),
        "manifest_quality": manifest_quality,
    }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Extension Stage Benchmark Evidence",
        "",
        f"Generated at: {report['generated_at']}",
        f"Project root: `{report['project_root']}`",
        "",
        "## Build And Publish Matrix",
        "",
        "| Extension | Status | Analysis | Tool | Validation | Published | Gate | Manifest |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in report.get("benchmark_rows", []):
        manifest = f"{row.get('manifest_completeness_score', 0)}/{row.get('manifest_completeness_total', 0)}"
        lines.append(
            f"| `{row.get('extension_id', '')}` | `{row.get('status', '')}` | `{row.get('analysis_type', '')}` | "
            f"`{row.get('tool_family', '')}` | `{row.get('validation_status', '')}` | `{row.get('published', '')}` | "
            f"`{row.get('publish_gate_passed', '')}` | `{manifest}` |"
        )
    lines.extend(["", "## Real Run Summary", ""])
    lines.append(f"- run_count: {report.get('run_count', 0)}")
    for key, value in report.get("run_status_counts", {}).items():
        lines.append(f"- status `{key}`: {value}")
    lines.extend(["", "## Diagnosis Codes", ""])
    for key, value in report.get("diagnosis_code_counts", {}).items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Manifest Quality", ""])
    for item in report.get("manifest_quality", [])[:100]:
        lines.append(
            f"- `{item.get('extension_id', '')}` / `{item.get('run_id', '')}`: "
            f"{item.get('score')}/{item.get('total')} status=`{item.get('status', '')}`"
        )
        if item.get("missing"):
            lines.append(f"  - missing: {', '.join(item['missing'])}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()
    report = build_report(project_root)
    json_path = project_root / "extension_stages" / "_benchmark_evidence.json"
    md_path = project_root / "docs" / f"extension_stage_benchmark_evidence_{args.date}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_markdown(report, md_path)
    print(json.dumps({"success": True, "json": str(json_path), "markdown": str(md_path), "run_count": report["run_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
