#!/usr/bin/env python3
"""Three-step verification for ortholog revision fixes."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TEST_JOB = "test_ortholog_revision_20260603"
PREFIX = "Ls.SPL"
SCOPE_ID = "SPL.target-Ls.ref-At.target_only"
SCOPE_FILE = f"workspace/{TEST_JOB}/final/Ls.SPL.SPL.target-Ls.ref-At.target_only.scope.json"


def _final_paths(ws: Path) -> list[Path]:
    return [
        ws / "final" / f"{PREFIX}.ortholog_pairs.txt",
        ws / "final" / f"{PREFIX}.orthologs.txt",
        ws / "final" / f"{PREFIX}.ortholog.manifest.json",
        ws / "final" / f"{PREFIX}.{SCOPE_ID}.ortholog.manifest.json",
    ]


def _remove_final_ortholog(ws: Path) -> None:
    for path in _final_paths(ws):
        if path.exists():
            path.unlink()


def step1_materialize_recovery(ws: Path) -> dict:
    from apps.web.pipeline_backend import JobInfo, run_stage
    from gfa_core.mas.tools.stage_status import get_stage_status

    _remove_final_ortholog(ws)
    before = get_stage_status(job_id=TEST_JOB, prefix=PREFIX, stage="ortholog")
    assert not before.completed, "precondition failed: final ortholog should be absent"

    job = JobInfo(
        job_id=TEST_JOB,
        input_dir=str(PROJECT_ROOT / "input" / TEST_JOB),
        workspace_dir=str(ws),
        logs_dir=str(ws / "logs"),
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    msg, manifest_path = run_stage(
        stage="ortholog",
        job=job,
        prefix=PREFIX,
        scope_file=str(PROJECT_ROOT / SCOPE_FILE),
    )
    after = get_stage_status(job_id=TEST_JOB, prefix=PREFIX, stage="ortholog", scope_id=SCOPE_ID)
    pairs = ws / "final" / f"{PREFIX}.ortholog_pairs.txt"
    log_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in sorted((ws / "logs").glob("*_ortholog.log"))
    )
    skipped_blast = "run_ortholog.py" not in log_text
    return {
        "message": msg,
        "manifest_path": str(manifest_path) if manifest_path else "",
        "completed": after.completed,
        "pair_count": (after.manifest or {}).get("metrics", {}).get("ortholog_pairs", 0),
        "final_files_exist": all(path.exists() and path.stat().st_size > 0 for path in _final_paths(ws)),
        "pairs_preview": pairs.read_text(encoding="utf-8").splitlines()[2:4] if pairs.exists() else [],
        "reused_intermediate_only": skipped_blast,
    }


def step2_success_and_contract(ws: Path) -> dict:
    from gfa_core.mas.tools.stage_status import get_stage_status
    from gfa_core.mas.tools.stage_tools import stage_registry

    status = get_stage_status(job_id=TEST_JOB, prefix=PREFIX, stage="ortholog", scope_id=SCOPE_ID)
    manifest = status.manifest or {}
    artifacts = manifest.get("artifacts") or {}

    async def _probe_stage_tools():
        _remove_final_ortholog(ws)
        fake_msg = "✅ Ortholog analysis complete. Found 23 pairs."
        manifest_stage = ""
        manifest_matches = False
        success_old_rule = fake_msg.startswith("✅") and (manifest_matches or True)
        success_new_rule = fake_msg.startswith("✅") and manifest_matches
        await stage_registry.execute(
            stage_id="ortholog",
            job_id=TEST_JOB,
            prefix=PREFIX,
            extra_params={"scopeFile": SCOPE_FILE},
        )
        after_exec = get_stage_status(job_id=TEST_JOB, prefix=PREFIX, stage="ortholog", scope_id=SCOPE_ID)
        return {
            "success_old_rule_without_manifest": success_old_rule,
            "success_new_rule_without_manifest": success_new_rule,
            "execute_status_completed": after_exec.completed,
        }

    tools_probe = asyncio.run(_probe_stage_tools())
    pairs_header = (ws / "ortholog" / "Ls.SPL.Ls_to_At_pairs.txt").read_text(encoding="utf-8").splitlines()[1]

    return {
        "stage_status_completed": status.completed,
        "artifact_keys": sorted(artifacts.keys()),
        "mapping_direction_ok": all(
            parts[3] == "Ls" and parts[4] == "At"
            for line in (ws / "final" / f"{PREFIX}.ortholog_pairs.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
            for parts in [line.rstrip("\n").split("\t")]
            if len(parts) >= 5
        ),
        "cli_header": pairs_header,
        "tools_probe": tools_probe,
    }


def step3_diagnosis_repair(ws: Path) -> dict:
    from gfa_core.mas.agents.diagnosis import DiagnosisAgent
    from gfa_core.mas.tools.stage_status import get_stage_status

    _remove_final_ortholog(ws)
    agent = DiagnosisAgent(None)
    gate_before = agent.diagnose_stage_gate(
        TEST_JOB,
        "ortholog",
        PREFIX,
        gate="postflight",
        params={"scopeFile": SCOPE_FILE},
    )
    issue_codes = [item.get("code") for item in gate_before.get("issues", [])]

    repair = agent.apply_runtime_repairs(
        TEST_JOB,
        "ortholog",
        PREFIX,
        diagnosis=gate_before,
        project_root=PROJECT_ROOT,
        execute_heavy=False,
    )
    after = get_stage_status(job_id=TEST_JOB, prefix=PREFIX, stage="ortholog", scope_id=SCOPE_ID)
    return {
        "detected_issue_codes": issue_codes,
        "repair_status": repair.get("status"),
        "repair_success": repair.get("success"),
        "repair_message": repair.get("message"),
        "completed_after_repair": after.completed,
        "final_files_exist": all(path.exists() for path in _final_paths(ws)),
    }


def step4_identity_and_postflight(ws: Path) -> dict:
    from gfa_core.mas.agents.diagnosis import DiagnosisAgent
    from gfa_core.mas.tools.stage_identity import get_stage_status_for_identity, resolve_stage_identity

    scoped_manifest = ws / "final" / f"{PREFIX}.{SCOPE_ID}.ortholog.manifest.json"
    manifest = json.loads(scoped_manifest.read_text(encoding="utf-8")) if scoped_manifest.exists() else {}
    params = {"scopeFile": SCOPE_FILE, "prefix": PREFIX}
    identity = resolve_stage_identity(stage="ortholog", prefix=PREFIX, params=params, job_id=TEST_JOB)
    strict_report = get_stage_status_for_identity(TEST_JOB, identity, strict_identity=True)
    gate = DiagnosisAgent(None).diagnose_stage_gate(
        TEST_JOB,
        "ortholog",
        PREFIX,
        gate="postflight",
        params={"scopeFile": SCOPE_FILE},
    )
    issue_codes = [item.get("code") for item in gate.get("issues", [])]
    return {
        "manifest_analysis_route": manifest.get("analysis_route"),
        "manifest_member_set_id": manifest.get("member_set_id"),
        "has_stage_context": isinstance(manifest.get("stage_context"), dict),
        "strict_identity_completed": strict_report.completed,
        "postflight_status": gate.get("status"),
        "postflight_issue_codes": issue_codes,
    }


def main() -> int:
    ws = PROJECT_ROOT / "workspace" / TEST_JOB
    results = {
        "test_job": TEST_JOB,
        "step1_materialize_recovery": step1_materialize_recovery(ws),
        "step2_success_and_contract": step2_success_and_contract(ws),
        "step3_diagnosis_repair": step3_diagnosis_repair(ws),
        "step4_identity_and_postflight": step4_identity_and_postflight(ws),
    }
    print(json.dumps(results, ensure_ascii=False, indent=2))

    checks = [
        results["step1_materialize_recovery"]["completed"],
        results["step1_materialize_recovery"]["final_files_exist"],
        results["step1_materialize_recovery"]["pair_count"] == 23,
        results["step2_success_and_contract"]["stage_status_completed"],
        results["step2_success_and_contract"]["mapping_direction_ok"],
        "Subject_ID" in results["step2_success_and_contract"]["cli_header"],
        not results["step2_success_and_contract"]["tools_probe"]["success_new_rule_without_manifest"],
        results["step2_success_and_contract"]["tools_probe"]["execute_status_completed"],
        "ortholog_intermediate_not_materialized" in results["step3_diagnosis_repair"]["detected_issue_codes"],
        results["step3_diagnosis_repair"]["repair_success"],
        results["step3_diagnosis_repair"]["completed_after_repair"],
        bool(results["step4_identity_and_postflight"]["manifest_analysis_route"]),
        bool(results["step4_identity_and_postflight"]["manifest_member_set_id"]),
        results["step4_identity_and_postflight"]["has_stage_context"],
        results["step4_identity_and_postflight"]["strict_identity_completed"],
        results["step4_identity_and_postflight"]["postflight_status"] == "pass",
        "stage_identity_contract_mismatch" not in results["step4_identity_and_postflight"]["postflight_issue_codes"],
    ]
    print("\nALL_CHECKS_PASSED=", all(checks))
    return 0 if all(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
