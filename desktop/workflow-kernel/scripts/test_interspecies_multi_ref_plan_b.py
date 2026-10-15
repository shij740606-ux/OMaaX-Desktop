#!/usr/bin/env python3
"""Plan B verification: interspecies_synteny multi ref-side species (At, Ha, Os)."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

REAL_JOB = "job_1780471268646_fc6c66"
PREFIX = "Ls.SPL"
TARGET = "Ls"
REFS = ["At", "Ha", "Os"]


def step1_scope_normalization() -> dict:
    from apps.web.vue3.main import _ensure_gene_family_scope_file

    params = {
        "scopeMode": "target_plus_reference",
        "geneFamily": "SPL",
        "targetSpecies": [TARGET],
        "referenceSpecies": ["At"],
        "refSpeciesList": REFS,
        "comparativeSpecies": [],
    }
    scope_file, scope_id = _ensure_gene_family_scope_file(REAL_JOB, PREFIX, "interspecies_synteny", params)
    scope_path = Path(scope_file)
    data = json.loads(scope_path.read_text(encoding="utf-8")) if scope_path.exists() else {}
    selected_refs = [sp for sp in (data.get("selected_species") or []) if sp != TARGET]
    return {
        "scope_file": scope_file,
        "scope_id": scope_id,
        "reference_species": data.get("reference_species"),
        "comparative_species": data.get("comparative_species"),
        "selected_species": data.get("selected_species"),
        "scope_mode": data.get("scope_mode"),
        "selected_ref_side": selected_refs,
        "scope_ok": set(REFS).issubset(set(selected_refs)) and set(data.get("comparative_species") or []) >= {"Ha", "Os"},
    }


def step2_cli_run(scope_file: str) -> dict:
    from apps.web.pipeline_backend import JobInfo, run_stage

    ws = PROJECT_ROOT / "workspace" / REAL_JOB
    job = JobInfo(
        job_id=REAL_JOB,
        input_dir=str(PROJECT_ROOT / "input" / REAL_JOB),
        workspace_dir=str(ws),
        logs_dir=str(ws / "logs"),
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    msg, manifest_path = run_stage(
        stage="interspecies_synteny",
        job=job,
        prefix=PREFIX,
        scope_file=scope_file,
        target_species=[TARGET],
        interspecies_ref_species=REFS,
        interspecies_blast_engine="diamond",
    )
    manifest = {}
    if manifest_path and Path(manifest_path).exists():
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    metrics = manifest.get("metrics") or {}
    params = manifest.get("parameters") or manifest.get("params") or {}
    pairwise = metrics.get("pairwise_synteny") or metrics.get("pairs") or []
    pair_keys = []
    if isinstance(pairwise, dict):
        pair_keys = list(pairwise.keys())
    elif isinstance(pairwise, list):
        pair_keys = [str(item.get("pair") or item) for item in pairwise if item]
    log_files = sorted((ws / "logs").glob("*interspecies*"))
    log_text = log_files[-1].read_text(encoding="utf-8", errors="ignore") if log_files else ""
    cmd_line = next((line for line in log_text.splitlines() if "--ref-species" in line), "")
    expected_pairs = {f"{TARGET}.{ref}" for ref in REFS}
    return {
        "message": msg,
        "manifest_path": str(manifest_path or ""),
        "status": manifest.get("status"),
        "pair_total_count": metrics.get("pair_total_count"),
        "pair_keys": pair_keys,
        "selected_ref_species": params.get("selected_ref_species") or params.get("ref_species"),
        "cmd_has_three_refs": all(ref in cmd_line for ref in REFS),
        "cmd_line": cmd_line[:500],
        "pairs_ok": metrics.get("pair_total_count", 0) >= 3 and expected_pairs.issubset(set(pair_keys) | set(metrics.get("pair_ids") or [])),
    }


def main() -> int:
    results = {"job": REAL_JOB, "prefix": PREFIX, "refs": REFS}
    s1 = step1_scope_normalization()
    results["step1_scope"] = s1
    if not s1.get("scope_ok"):
        print(json.dumps(results, indent=2, ensure_ascii=False))
        print("ALL_CHECKS_PASSED=False")
        return 1

    s2 = step2_cli_run(s1["scope_file"])
    results["step2_run"] = s2
    passed = bool(s1.get("scope_ok") and s2.get("cmd_has_three_refs") and s2.get("pair_total_count", 0) >= 3)
    results["ALL_CHECKS_PASSED"] = passed
    print(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"ALL_CHECKS_PASSED={passed}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
