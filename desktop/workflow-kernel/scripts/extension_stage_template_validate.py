#!/usr/bin/env python3
"""Run deterministic wrapper + golden validation for extension template cases."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.extension_stage.template_validation import validate_template_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate reusable extension template cases through wrapper/golden gates")
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--max-cases", type=int, default=None)
    args = parser.parse_args()
    report = validate_template_bundle(args.bundle, output_root=args.output_root, max_cases=args.max_cases)
    print(json.dumps({"success": report["status"] == "passed", "status": report["status"], "case_count": report["case_count"], "passed": report["passed"], "failed": report["failed"], "run_root": report["run_root"]}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
