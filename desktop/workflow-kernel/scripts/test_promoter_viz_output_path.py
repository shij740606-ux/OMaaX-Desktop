#!/usr/bin/env python3
"""Verify promoter R viz writes to workspace/final, not project root."""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

JOB_ID = "job_1780471268646_fc6c66"
PREFIX = "Ls.SPL"
OUTPUT_PREFIX = f"{PREFIX}_promoter"


def _root_strays() -> list[Path]:
    return sorted(
        path
        for path in PROJECT_ROOT.glob(f"{OUTPUT_PREFIX}*")
        if path.is_file() and path.suffix.lower() in {".pdf", ".png", ".csv"}
    )


def main() -> int:
    from cli.run_promoter import collect_promoter_viz_files, run_r_visualization

    workspace = PROJECT_ROOT / "workspace" / JOB_ID
    final_dir = workspace / "final"
    analysis_dir = workspace / "promoter" / "analysis"
    tree_file = final_dir / f"{PREFIX}.tree.nwk"
    subfamily_file = final_dir / f"{PREFIX}.confirmed_subfamily.tsv"
    promoter_file = analysis_dir / "plantCARE_output_filtered.tab"
    element_desc = analysis_dir / f"{PREFIX}_element_desc.csv"

    missing = [path for path in (tree_file, promoter_file) if not path.exists()]
    if missing:
        print(json.dumps({"error": "missing_inputs", "paths": [str(p) for p in missing]}, ensure_ascii=False, indent=2))
        return 2

    before_root = _root_strays()
    ok, files = run_r_visualization(
        tree_file=tree_file,
        subfamily_file=subfamily_file if subfamily_file.exists() else None,
        promoter_file=promoter_file,
        element_desc_file=element_desc if element_desc.exists() else None,
        output_prefix=OUTPUT_PREFIX,
        output_dir=final_dir,
        project_root=PROJECT_ROOT,
    )
    after_root = _root_strays()
    final_files = collect_promoter_viz_files(final_dir, OUTPUT_PREFIX)
    result = {
        "job_id": JOB_ID,
        "prefix": PREFIX,
        "viz_success": ok,
        "files_in_final": [path.name for path in final_files],
        "final_count": len(final_files),
        "root_strays_before": [path.name for path in before_root],
        "root_strays_after": [path.name for path in after_root],
        "root_clean": len(after_root) == 0,
        "all_in_final_dir": all(path.parent.resolve() == final_dir.resolve() for path in final_files),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    checks = [
        ok,
        result["final_count"] > 0,
        result["root_clean"],
        result["all_in_final_dir"],
    ]
    print("\nALL_CHECKS_PASSED=", all(checks))
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
