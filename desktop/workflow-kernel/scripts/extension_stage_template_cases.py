#!/usr/bin/env python3
"""Build reusable extension-stage template cases from a completed bbav5 job."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.extension_stage.template_cases import build_template_case_bundle, write_template_case_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover extension-stage template cases from a completed job workspace")
    parser.add_argument("--job-workspace", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--max-per-archetype", type=int, default=1)
    args = parser.parse_args()

    bundle = build_template_case_bundle(
        args.job_workspace,
        project_root=args.project_root,
        max_per_archetype=max(1, args.max_per_archetype),
    )
    output_dir = args.output_dir or (args.job_workspace / "extension_template_cases")
    paths = write_template_case_bundle(bundle, output_dir)
    print(json.dumps({"success": bundle["validation"]["status"] == "passed", "paths": paths, "validation": bundle["validation"], "case_count": bundle["case_count"]}, ensure_ascii=False))
    return 0 if bundle["validation"]["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
