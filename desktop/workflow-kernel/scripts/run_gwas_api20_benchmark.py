#!/usr/bin/env python3
"""API-only GWAS extension-stage benchmark runner.

This runner deliberately avoids archetype_spec monkey patches. Codex may use it
to submit and record experiments, but API conversion/repair must remain the only
source of spec changes during benchmark runs.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import os
import shutil
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_BASELINE_ROOT = PROJECT_ROOT / "项目记录" / "GWAS拓展分析阶段_发表级完整实验_20260622"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "项目记录" / "GWAS拓展分析阶段_API20_Benchmark_20260622"


@dataclass(frozen=True)
class ApiCase:
    dir_name: str
    case_id: str
    archetype_id: str
    script_name: str


CASES: tuple[ApiCase, ...] = (
    ApiCase("01_gwas_trait_vcf_qc", "gwas_trait_vcf_qc", "gwas_trait_vcf_qc", "run_gwas_trait_vcf_qc.sh"),
    ApiCase("02_gwas_vcf_filter_index", "gwas_vcf_filter_index", "gwas_vcf_filter_index", "run_gwas_vcf_filter_index.sh"),
    ApiCase("03_gwas_trait_prepare_match", "gwas_trait_prepare_match", "gwas_trait_prepare_match", "run_gwas_trait_prepare_match.sh"),
    ApiCase("04_gwas_plink_pca", "gwas_plink_pca", "gwas_plink_pca", "run_gwas_plink_pca.sh"),
    ApiCase("05_gwas_trait_blup_format", "gwas_trait_blup_format", "gwas_trait_blup_format", "run_gwas_trait_blup_format.sh"),
    ApiCase("06_gwas_plink_impute", "gwas_plink_impute", "gwas_plink_impute", "run_gwas_plink_impute.sh"),
    ApiCase("07_gwas_tassel_compatible", "gwas_tassel_compatible", "gwas_tassel_compatible", "run_gwas_tassel_compatible.sh"),
    ApiCase("08_gwas_gemma_assoc", "gwas_gemma_assoc", "gwas_gemma_assoc", "run_gwas_gemma_assoc.sh"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-root", type=Path, default=DEFAULT_BASELINE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--runtime-env", default="ASAW")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--start-run", type=int, default=1, help="First run index to execute; use for resuming an interrupted benchmark.")
    parser.add_argument("--jobs", type=int, default=1, help="Number of independent run_id/case tasks to execute concurrently.")
    parser.add_argument("--case", action="append", dest="case_ids", help="Run only selected case_id; repeatable.")
    parser.add_argument("--freeze-baseline", action="store_true")
    parser.add_argument("--init-fixture", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()

    if args.freeze_baseline:
        freeze_baseline(args.baseline_root)
    if args.init_fixture:
        init_fixture(args.baseline_root, args.output_root)
    if args.dry_run:
        dry_run(args.output_root, selected_cases(args.case_ids))
    if args.run:
        run_api20(args.output_root, selected_cases(args.case_ids), runs=args.runs, runtime_env=args.runtime_env, start_run=args.start_run, jobs=args.jobs)
    if args.report:
        write_report(args.output_root)
    if not any((args.freeze_baseline, args.init_fixture, args.dry_run, args.run, args.report)):
        parser.print_help()
    return 0


def freeze_baseline(baseline_root: Path) -> None:
    baseline_root.mkdir(parents=True, exist_ok=True)
    code_backup = baseline_root / "code_backup"
    code_backup.mkdir(parents=True, exist_ok=True)
    src = PROJECT_ROOT / "scripts" / "run_gwas_publication_experiments.py"
    if src.exists():
        shutil.copy2(src, code_backup / "run_gwas_publication_experiments.codex_adjusted.py")
    classification = baseline_root / "EXPERIMENT_CLASSIFICATION.md"
    classification.write_text(
        "\n".join(
            [
                "# GWAS Extension Experiment Classification",
                "",
                f"- frozen_at: `{now()}`",
                "- classification: `Codex-adjusted deterministic baseline`",
                "- benchmark_status: `not_api20_benchmark`",
                "- template_eligibility: `not_eligible`",
                "- reason: this directory contains archetype_spec/deterministic baseline runs and Codex-adjusted harness logic.",
                "- allowed_use: fixture design, tool preflight evidence, raw step sanity checks, GWAS archetype design reference.",
                "- disallowed_use: API autonomous success, API20 success-rate denominator/numerator, direct template admission evidence.",
                "",
                "Formal API-only benchmark results must be written to a separate `GWAS拓展分析阶段_API20_Benchmark_*` directory.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    write_json(
        baseline_root / "experiment_classification.json",
        {
            "schema_version": "gwas_experiment_classification_v1",
            "created_at": now(),
            "classification": "codex_adjusted_deterministic_baseline",
            "benchmark_status": "not_api20_benchmark",
            "template_eligibility": "not_eligible",
            "code_backup": str(code_backup / "run_gwas_publication_experiments.codex_adjusted.py"),
        },
    )


def init_fixture(baseline_root: Path, output_root: Path) -> None:
    fixture_root = output_root / "fixtures" / "packages"
    fixture_root.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in CASES:
        src = baseline_root / "packages" / case.dir_name
        dst = fixture_root / case.dir_name
        if not src.exists():
            rows.append({"case_id": case.case_id, "status": "missing_source", "source": str(src), "target": str(dst)})
            continue
        if not dst.exists():
            shutil.copytree(src, dst)
        rows.append(
            {
                "case_id": case.case_id,
                "archetype_id": case.archetype_id,
                "status": "ready" if dst.exists() else "failed",
                "source": str(src),
                "target": str(dst),
                "script": case.script_name,
                "raw_code_files": inventory(dst / "raw_code"),
                "raw_test_data_files": inventory(dst / "raw_test_data"),
                "expected_output_files": inventory(dst / "expected_outputs"),
            }
        )
    write_json(
        output_root / "fixture_manifest.json",
        {
            "schema_version": "gwas_api20_fixture_manifest_v1",
            "created_at": now(),
            "source_baseline_root": str(baseline_root),
            "fixture_root": str(fixture_root),
            "codex_prepared_fixture": True,
            "fixture_policy": "frozen_before_api20; do not modify during API benchmark runs",
            "cases": rows,
        },
    )
    (output_root / "README.md").write_text(
        "\n".join(
            [
                "# GWAS API20 Benchmark",
                "",
                "This directory is reserved for pure API extension-stage benchmark runs.",
                "",
                "- Codex submits runs and collects metrics only.",
                "- API conversion/repair is the only source of spec changes.",
                "- Deterministic archetype_spec results are not counted here.",
                "- Fixture files are copied from the frozen Codex-adjusted baseline and must not be modified during API20.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def dry_run(output_root: Path, cases: tuple[ApiCase, ...]) -> None:
    from gfa_core.extension_stage.template_gate import evaluate_template_gate

    fixture_root = output_root / "fixtures" / "packages"
    rows = []
    for case in cases:
        root = fixture_root / case.dir_name
        raw_code = root / "raw_code" / case.script_name
        raw_data = root / "raw_test_data"
        expected = root / "expected_outputs"
        extension = {
            "extension_id": f"dry_run_{case.case_id}",
            "archetype_id": case.archetype_id,
            "analysis_type": "generic_cli",
            "tool_family": "generic",
            "input_roles": {"input_file": {"role": "generic_input_file", "required": True}},
            "output_contract": output_contract_from_expected(expected),
            "golden_comparison": {"status": "passed"},
        }
        gate = evaluate_template_gate(extension)
        rows.append(
            {
                "case_id": case.case_id,
                "fixture_exists": root.exists(),
                "raw_code_exists": raw_code.exists(),
                "raw_data_file_count": len(inventory(raw_data)),
                "expected_output_count": len(inventory(expected)),
                "template_gate_status": gate.get("status", ""),
                "template_gate_errors": "; ".join(gate.get("errors") or []),
            }
        )
    write_json(output_root / "dry_run.json", {"schema_version": "gwas_api20_dry_run_v1", "created_at": now(), "rows": rows})
    write_csv(output_root / "dry_run.csv", rows)


def run_api20(output_root: Path, cases: tuple[ApiCase, ...], *, runs: int, runtime_env: str, start_run: int = 1, jobs: int = 1) -> None:
    from gfa_core.extension_stage import ExtensionStageAgent, ExtensionStageManager

    os.environ["BBAV5_EXTENSION_CONVERTER"] = "deepseek_api"
    jobs = max(1, int(jobs or 1))
    if jobs > 1:
        run_api20_parallel(output_root, cases, runs=runs, runtime_env=runtime_env, start_run=start_run, jobs=jobs)
        return
    rows = existing_result_rows(output_root)
    for run_index in range(start_run, runs + 1):
        run_id = f"api_run_{run_index:03d}"
        for case in cases:
            run_case_root = output_root / "runs" / run_id / case.case_id
            existing_row = run_case_root / "result_row.json"
            if existing_row.exists():
                continue
            run_case_root.mkdir(parents=True, exist_ok=True)
            manager = ExtensionStageManager(
                PROJECT_ROOT,
                store_root=run_case_root / "extension_stages",
                workspace_root=run_case_root / "workspace",
                input_root=run_case_root / "input",
            )
            row = run_one_case(ExtensionStageAgent(manager), output_root, run_case_root, run_id, case, runtime_env)
            rows.append(row)
            write_json(run_case_root / "result_row.json", row)
        rows = existing_result_rows(output_root)
        write_partial_summary(output_root, rows)
    write_summary(output_root, existing_result_rows(output_root))


def run_api20_parallel(output_root: Path, cases: tuple[ApiCase, ...], *, runs: int, runtime_env: str, start_run: int, jobs: int) -> None:
    os.environ["BBAV5_EXTENSION_CONVERTER"] = "deepseek_api"
    tasks: list[tuple[str, ApiCase]] = []
    for run_index in range(start_run, runs + 1):
        run_id = f"api_run_{run_index:03d}"
        for case in cases:
            run_case_root = output_root / "runs" / run_id / case.case_id
            if (run_case_root / "result_row.json").exists():
                continue
            tasks.append((run_id, case))
    if not tasks:
        write_summary(output_root, existing_result_rows(output_root))
        return
    with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as pool:
        future_to_task = {
            pool.submit(run_one_case_worker, output_root, run_id, case, runtime_env): (run_id, case)
            for run_id, case in tasks
        }
        for future in concurrent.futures.as_completed(future_to_task):
            run_id, case = future_to_task[future]
            run_case_root = output_root / "runs" / run_id / case.case_id
            try:
                row = future.result()
            except Exception as exc:
                row = base_row(run_id, case, runtime_env)
                row["error"] = str(exc)
                row["first_failure_phase"] = infer_failure_phase(row)
                row = finalize_row(row, time.time())
                run_case_root.mkdir(parents=True, exist_ok=True)
                write_json(run_case_root / "result_row.json", row)
            write_partial_summary(output_root, existing_result_rows(output_root))
    write_summary(output_root, existing_result_rows(output_root))


def run_one_case_worker(output_root: Path, run_id: str, case: ApiCase, runtime_env: str) -> dict[str, Any]:
    from gfa_core.extension_stage import ExtensionStageAgent, ExtensionStageManager

    os.environ["BBAV5_EXTENSION_CONVERTER"] = "deepseek_api"
    run_case_root = output_root / "runs" / run_id / case.case_id
    existing_row = run_case_root / "result_row.json"
    if existing_row.exists():
        return read_json(existing_row)
    run_case_root.mkdir(parents=True, exist_ok=True)
    manager = ExtensionStageManager(
        PROJECT_ROOT,
        store_root=run_case_root / "extension_stages",
        workspace_root=run_case_root / "workspace",
        input_root=run_case_root / "input",
    )
    row = run_one_case(ExtensionStageAgent(manager), output_root, run_case_root, run_id, case, runtime_env)
    write_json(run_case_root / "result_row.json", row)
    return row


def run_one_case(agent: Any, output_root: Path, run_case_root: Path, run_id: str, case: ApiCase, runtime_env: str) -> dict[str, Any]:
    fixture_case = output_root / "fixtures" / "packages" / case.dir_name
    row = base_row(run_id, case, runtime_env)
    started = time.time()
    try:
        build = agent.create_from_uploads(
            display_name=f"gwas_api20_{case.case_id}_{run_id}",
            command=f"bash {case.script_name}",
            runtime_env=runtime_env,
            code_files=upload_files(fixture_case / "raw_code"),
            data_files=upload_files(fixture_case / "raw_test_data"),
            description=f"API-only GWAS benchmark case {case.case_id}",
        )
        write_json(run_case_root / "build_response.json", build)
        ext = build.get("extension") or {}
        eid = str(ext.get("extension_id") or "")
        row["extension_id"] = eid
        row["build_success"] = bool(build.get("success") and eid)
        row["sanitize_success"] = row["build_success"]
        row["spec_validate_success"] = row["build_success"]
        row["wrapper_success"] = row["build_success"]
        row["build_api_attempt_count"] = len(ext.get("build_attempts") or [])
        row["sanitizer_action_count"] = len(ext.get("sanitizer_actions") or [])
        if not row["build_success"]:
            row["first_failure_phase"] = "build"
            return finalize_row(row, started)

        trial = agent.trial_run(eid)
        write_json(run_case_root / "trial_run.json", trial)
        if not trial.get("success"):
            repair = agent.repair_from_context(eid, phase="trial", run_context=trial.get("run") or {}, max_attempts=20, auto_publish=False)
            write_json(run_case_root / "trial_repair_response.json", repair)
            row["trial_repair_attempt_count"] = int(repair.get("attempts") or 0)
            if repair.get("success"):
                trial = {"success": True, "extension": repair.get("extension") or {}, "run": repair.get("run") or {}}
            else:
                row["first_failure_phase"] = "trial"
                row["last_error"] = str(repair.get("last_error") or "")
                return finalize_row(row, started)
        row["trial_success"] = bool(trial.get("success"))
        row["output_count"] = len((trial.get("run") or {}).get("outputs") or [])

        outputs = (trial.get("extension") or {}).get("outputs_detected") or (trial.get("run") or {}).get("outputs") or []
        confirm = agent.confirm(eid, outputs=outputs, nl_triggers=[case.case_id, case.archetype_id, "gwas"])
        write_json(run_case_root / "confirm_response.json", confirm)
        row["confirm_success"] = bool(confirm.get("success"))
        if not row["confirm_success"]:
            row["first_failure_phase"] = "confirm"
            return finalize_row(row, started)

        val = agent.validate(eid)
        write_json(run_case_root / "validate_response.json", val)
        run = val.get("run") or {}
        row.update(validation_status(val))
        if not val.get("success"):
            repair = agent.repair_from_context(eid, phase="validation", run_context=run, max_attempts=20, auto_publish=False)
            write_json(run_case_root / "validation_repair_response.json", repair)
            row["validation_repair_attempt_count"] = int(repair.get("attempts") or 0)
            if repair.get("success"):
                val = {"success": True, "extension": repair.get("extension") or {}, "run": repair.get("run") or {}}
                row.update(validation_status(val))
            else:
                row["first_failure_phase"] = "validation"
                row["last_error"] = str(repair.get("last_error") or "")
                return finalize_row(row, started)

        try:
            pub = agent.publish(eid)
            write_json(run_case_root / "publish_response.json", pub)
            row["publish_gate_status"] = "passed"
            row["publish_success"] = bool(pub.get("success"))
            row["final_publish_status"] = str((pub.get("extension") or {}).get("status") or "")
        except Exception as pub_exc:
            publish_context = {
                "repair_phase": "publish_gate",
                "status": "failed",
                "error": str(pub_exc),
                "publish_checks": (agent.load(eid).get("publish_checks") or {}) if hasattr(agent, "load") else {},
                "last_validation": (val.get("run") or {}),
                "golden_comparison": (val.get("run") or {}).get("golden_comparison") or {},
                "template_gate": (val.get("run") or {}).get("template_gate") or {},
            }
            row["publish_gate_status"] = "failed"
            repair = agent.repair_from_context(eid, phase="publish_gate", run_context=publish_context, max_attempts=20, auto_publish=True)
            write_json(run_case_root / "publish_repair_response.json", repair)
            row["publish_gate_repair_attempt_count"] = int(repair.get("attempts") or 0)
            if repair.get("success") and repair.get("published"):
                row["publish_gate_status"] = "passed"
                row["publish_success"] = True
                row["final_publish_status"] = "published"
            else:
                row["first_failure_phase"] = "publish_gate"
                row["last_error"] = str(repair.get("last_error") or str(pub_exc))
                return finalize_row(row, started)

        if row["publish_success"]:
            job_id = f"{run_id}_{case.case_id}_published"
            materialize_job_inputs(agent, eid, job_id, fixture_case / "raw_test_data")
            published = agent.run(extension_id=eid, job_id=job_id)
            write_json(run_case_root / "published_run_response.json", published)
            row["published_run_success"] = bool(published.get("success"))
            if not row["published_run_success"]:
                row["first_failure_phase"] = "published_run"
                row["last_error"] = str((published.get("run") or {}).get("error") or "")
    except Exception as exc:
        row["error"] = str(exc)
        row["traceback"] = traceback.format_exc()
        if not row["first_failure_phase"]:
            row["first_failure_phase"] = infer_failure_phase(row)
        write_json(run_case_root / "error.json", {"error": str(exc), "traceback": row["traceback"], "row": row})
    return finalize_row(row, started)


def materialize_job_inputs(agent: Any, extension_id: str, job_id: str, raw_data: Path) -> None:
    extension = agent.load(extension_id)
    root = Path(str((extension.get("paths") or {}).get("root") or ""))
    manager = agent.manager
    input_dir = manager.input_root / job_id
    input_dir.mkdir(parents=True, exist_ok=True)
    copy_tree_contents(raw_data, input_dir)


def validation_status(val: dict[str, Any]) -> dict[str, Any]:
    run = val.get("run") or {}
    golden = run.get("golden_comparison") or {}
    gate = run.get("template_gate") or {}
    return {
        "validate_success": bool(val.get("success")),
        "golden_status": str(golden.get("status") or ""),
        "template_gate_status": str(gate.get("status") or ""),
        "final_validation_status": str(run.get("status") or ""),
    }


def base_row(run_id: str, case: ApiCase, runtime_env: str) -> dict[str, Any]:
    return {
        "schema_version": "gwas_api20_case_result_v1",
        "created_at": now(),
        "run_id": run_id,
        "case_id": case.case_id,
        "archetype_id": case.archetype_id,
        "converter": "deepseek_api",
        "runtime_env": runtime_env,
        "codex_manual_spec_edit": False,
        "extension_id": "",
        "raw_code_ok": True,
        "build_success": False,
        "sanitize_success": False,
        "spec_validate_success": False,
        "wrapper_success": False,
        "trial_success": False,
        "confirm_success": False,
        "validate_success": False,
        "golden_status": "",
        "template_gate_status": "",
        "publish_gate_status": "",
        "publish_success": False,
        "published_run_success": False,
        "api_autonomous_success": False,
        "published_runnable": False,
        "template_eligible": False,
        "api_case_success_after_framework_fix": False,
        "codex_framework_fix_required": True,
        "environment_failed": False,
        "repair_stalled": False,
        "stall_error_signature": "",
        "output_count": 0,
        "build_api_attempt_count": 0,
        "trial_repair_attempt_count": 0,
        "validation_repair_attempt_count": 0,
        "publish_gate_repair_attempt_count": 0,
        "total_api_attempt_count": 0,
        "sanitizer_action_count": 0,
        "final_validation_status": "",
        "final_publish_status": "",
        "first_failure_phase": "",
        "last_error": "",
        "error": "",
        "traceback": "",
        "wall_sec": 0,
    }


def finalize_row(row: dict[str, Any], started: float) -> dict[str, Any]:
    row["total_api_attempt_count"] = (
        int(row.get("build_api_attempt_count") or 0)
        + int(row.get("trial_repair_attempt_count") or 0)
        + int(row.get("validation_repair_attempt_count") or 0)
        + int(row.get("publish_gate_repair_attempt_count") or 0)
    )
    row["published_runnable"] = all(
        [
            row.get("build_success"),
            row.get("sanitize_success"),
            row.get("spec_validate_success"),
            row.get("wrapper_success"),
            row.get("trial_success"),
            row.get("confirm_success"),
            row.get("validate_success"),
            row.get("golden_status") == "passed",
            row.get("publish_gate_status") == "passed",
            row.get("publish_success"),
            row.get("published_run_success"),
        ]
    )
    row["template_eligible"] = bool(row.get("published_runnable") and row.get("template_gate_status") == "passed")
    row["api_autonomous_success"] = bool(row.get("published_runnable") and not row.get("codex_manual_spec_edit"))
    row["api_case_success_after_framework_fix"] = bool(row.get("api_autonomous_success") and row.get("codex_framework_fix_required"))
    if not row.get("first_failure_phase") and not row["api_autonomous_success"]:
        row["first_failure_phase"] = infer_failure_phase(row)
    row["wall_sec"] = round(time.time() - started, 3)
    return row


def infer_failure_phase(row: dict[str, Any]) -> str:
    checks = [
        ("build", "build_success"),
        ("sanitize", "sanitize_success"),
        ("spec_validate", "spec_validate_success"),
        ("wrapper", "wrapper_success"),
        ("trial", "trial_success"),
        ("confirm", "confirm_success"),
        ("validate", "validate_success"),
        ("golden", "golden_status"),
        ("publish_gate", "publish_gate_status"),
        ("publish", "publish_success"),
        ("published_run", "published_run_success"),
    ]
    for phase, key in checks:
        value = row.get(key)
        if key.endswith("_status"):
            if value != "passed":
                return phase
        elif not value:
            return phase
    return ""


def output_contract_from_expected(expected: Path) -> list[dict[str, Any]]:
    contract = []
    for path in sorted(expected.glob("*")):
        if not path.is_file():
            continue
        contract.append({"name_pattern": path.name, "semantic_role": semantic_role(path.name), "required": required_output(path.name)})
    return contract


def semantic_role(name: str) -> str:
    mapping = {
        "gwas_metrics.json": "gwas_metrics",
        "sample_match_report.tsv": "gwas_sample_match_report",
        "trait.clean.tsv": "gwas_clean_trait_table",
        "trait.prepared.tsv": "gwas_prepared_trait_table",
        "filtered.vcf.gz": "gwas_filtered_vcf_gz",
        "filtered.vcf.gz.tbi": "gwas_filtered_vcf_index",
        "plink_pca.eigenvec": "gwas_plink_pca_eigenvec",
        "plink_pca.eigenval": "gwas_plink_pca_eigenval",
        "heading_days_BLUP_value.tsv": "gwas_blup_trait_table",
        "heading_days_BLUP_value.tassel.tsv": "gwas_tassel_trait_table",
        "all.impute.vcf.gz": "gwas_imputed_vcf_gz",
        "all.impute.vcf.gz.tbi": "gwas_imputed_vcf_index",
        "imputed_bed.bed": "gwas_imputed_plink_bed",
        "imputed_bed.bim": "gwas_imputed_plink_bim",
        "imputed_bed.fam": "gwas_imputed_plink_fam",
        "hapmap.hmp.txt": "gwas_tassel_hapmap",
        "tassel_trait.tsv": "gwas_tassel_trait_table",
        "glm_pvalue.txt": "gwas_tassel_glm_pvalue_table",
        "tassel_probe.json": "gwas_tassel_probe_record",
        "lm.assoc.txt": "gwas_gemma_assoc_table",
    }
    return mapping.get(name, "gwas_auxiliary_output")


def required_output(name: str) -> bool:
    return name in {
        "gwas_metrics.json",
        "sample_match_report.tsv",
        "trait.clean.tsv",
        "trait.prepared.tsv",
        "filtered.vcf.gz",
        "filtered.vcf.gz.tbi",
        "plink_pca.eigenvec",
        "plink_pca.eigenval",
        "heading_days_BLUP_value.tsv",
        "heading_days_BLUP_value.tassel.tsv",
        "all.impute.vcf.gz",
        "all.impute.vcf.gz.tbi",
        "imputed_bed.bed",
        "imputed_bed.bim",
        "imputed_bed.fam",
        "hapmap.hmp.txt",
        "tassel_trait.tsv",
        "glm_pvalue.txt",
        "tassel_probe.json",
        "lm.assoc.txt",
    }


def selected_cases(case_ids: list[str] | None) -> tuple[ApiCase, ...]:
    if not case_ids:
        return CASES
    wanted = set(case_ids)
    return tuple(case for case in CASES if case.case_id in wanted or case.archetype_id in wanted)


def write_partial_summary(output_root: Path, rows: list[dict[str, Any]]) -> None:
    write_json(output_root / "partial_summary.json", summarize(rows))
    write_csv(output_root / "partial_summary.csv", rows)


def write_summary(output_root: Path, rows: list[dict[str, Any]]) -> None:
    summary = summarize(rows)
    write_json(output_root / "summary.json", summary)
    write_csv(output_root / "summary.csv", rows)
    write_json(output_root / "template_eligibility.json", {"schema_version": "gwas_api20_template_eligibility_v1", "created_at": now(), "eligible": [row for row in rows if row.get("template_eligible")]})
    write_failure_matrix(output_root, rows)


def write_report(output_root: Path) -> None:
    rows = existing_result_rows(output_root)
    write_summary(output_root, rows)
    write_json(
        output_root / "publication_metrics.json",
        {
            "schema_version": "gwas_api20_publication_metrics_v1",
            "created_at": now(),
            "benchmark_type": "api_only",
            "codex_manual_spec_edit": False,
            "summary": summarize(rows),
        },
    )


def existing_result_rows(output_root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((output_root / "runs").glob("api_run_*/*/result_row.json")):
        rows.append(read_json(path))
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "gwas_api20_summary_v1",
        "created_at": now(),
        "benchmark_type": "api_only",
        "case_result_count": len(rows),
        "api_autonomous_success": sum(1 for row in rows if row.get("api_autonomous_success")),
        "published_runnable": sum(1 for row in rows if row.get("published_runnable")),
        "template_eligible": sum(1 for row in rows if row.get("template_eligible")),
        "failed": sum(1 for row in rows if not row.get("api_autonomous_success")),
        "rows": rows,
    }


def write_failure_matrix(output_root: Path, rows: list[dict[str, Any]]) -> None:
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (str(row.get("case_id") or ""), str(row.get("first_failure_phase") or "none"))
        counts[key] = counts.get(key, 0) + 1
    matrix = [{"case_id": case_id, "first_failure_phase": phase, "count": count} for (case_id, phase), count in sorted(counts.items())]
    write_csv(output_root / "failure_matrix.csv", matrix)


def upload_files(root: Path) -> list[tuple[str, bytes]]:
    return [(path.name, path.read_bytes()) for path in sorted(root.iterdir()) if path.is_file()]


def inventory(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rows.append({"path": path.relative_to(root).as_posix(), "size": path.stat().st_size})
    return rows


def copy_tree_contents(src: Path, dst: Path) -> None:
    for item in sorted(src.iterdir()):
        target = dst / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)


def now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value for key, value in row.items()})


if __name__ == "__main__":
    raise SystemExit(main())
