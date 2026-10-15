#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.web.vue3 import main as web_main


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="bbav5_subfamily_scope_") as tmp:
        root = Path(tmp)
        job_id = "job_scope_evidence"
        final_dir = root / "workspace" / job_id / "final"
        final_dir.mkdir(parents=True)
        web_main.WORKSPACE_ROOT = root / "workspace"
        web_main.INPUT_ROOT = root / "input"
        web_main.INPUT_ROOT.mkdir(parents=True, exist_ok=True)

        combo_png = final_dir / "Lc.SPL.target_plus_reference.combo.png"
        combo_png.write_bytes(b"fake png bytes")
        tree_png = final_dir / "Target.SPL.target-Lc.ref-At.subfamily_prediction.reference_classification.tree.png"
        tree_png.write_bytes(b"fake tree png")

        aggregate_manifest = {
            "manifest_version": "1.0",
            "stage": "reference_classification",
            "status": "success",
            "prefix": "Lc.SPL",
            "output_prefix": "Target.SPL.target-Lc.ref-At.subfamily_prediction",
            "scope_id": "SPL.target-Lc.ref-At.target_plus_reference",
            "analysis_route": "subfamily_prediction",
            "per_species_results": [
                {
                    "species": "Lc",
                    "prefix": "Lc.SPL",
                    "combo": str(combo_png),
                    "tree": str(tree_png),
                }
            ],
            "artifacts": {},
        }
        (final_dir / "Target.SPL.target-Lc.ref-At.subfamily_prediction.reference_classification.manifest.json").write_text(
            json.dumps(aggregate_manifest), encoding="utf-8"
        )

        assets, missing = web_main._subfamily_evidence_assets(job_id, "Target.SPL.target-Lc.ref-At.subfamily_prediction")
        combo_assets = [a for a in assets if a.get("group") == "rect_combo"]
        require(combo_assets, f"rect_combo missing; assets={assets}, missing={missing}")
        require(
            combo_assets[0].get("name") == "Lc.SPL.target_plus_reference.combo.png",
            f"expected same-scope combo asset, got {combo_assets}",
        )
        require("rect_combo" not in missing, f"rect_combo incorrectly marked missing: {missing}")
        print("PASS subfamily evidence resolves same-scope combo from manifest per_species_results")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
