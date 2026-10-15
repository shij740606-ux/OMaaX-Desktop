#!/usr/bin/env python3
"""Run standalone golden validation for extension stages."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def _load_runner(project_root: Path):
    sys.path.insert(0, str(project_root))
    from gfa_core.extension_stage.golden_runner import run_golden_validation

    return run_golden_validation


def main() -> int:
    parser = argparse.ArgumentParser(description="Run original-vs-converted golden validation for extension stages.")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--extension-id", action="append", default=[], help="Extension ID to validate; may be repeated.")
    parser.add_argument("--all-published", action="store_true", help="Validate all currently published extensions.")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    project = args.project_root.resolve()
    run_golden_validation = _load_runner(project)
    store = project / "extension_stages"
    extension_ids = list(args.extension_id)
    if args.all_published:
        for path in sorted(store.glob("*/extension.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("status") == "published":
                extension_ids.append(data.get("extension_id") or path.parent.name)
    extension_ids = sorted(set(x for x in extension_ids if x))
    rows = []
    for extension_id in extension_ids:
        root = store / extension_id
        data = json.loads((root / "extension.json").read_text(encoding="utf-8"))
        run_id = args.run_id or f"manual_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        report = run_golden_validation(data, extension_root=root, run_id=run_id)
        rows.append({
            "extension_id": extension_id,
            "status": report.get("status", ""),
            "error": report.get("error", ""),
            "workspace": report.get("workspace", ""),
        })
        print(f"{extension_id}\t{report.get('status', '')}\t{str(report.get('error', '')).splitlines()[0] if report.get('error') else ''}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if all(row["status"] == "passed" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
