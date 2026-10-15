#!/usr/bin/env python3
"""Run one API conversion test for the VCF-10 threshold adapter."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_CODE = PROJECT_ROOT / "scripts" / "vcf" / "07_10_gvcf_to_clean_vcf" / "run_10_hard_filter_clean_api_adapter.sh"
DEFAULT_DATA = Path("/tmp/vcf_07_10_extracted_test/09.selected")
DEFAULT_OUTPUT = PROJECT_ROOT / "项目记录" / "VCF_10_threshold_parameter_test_20260623" / "api_adapter_conversion_test"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", type=Path, default=DEFAULT_CODE)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runtime-env", default="ASAW")
    parser.add_argument("--display-name", default="vcf_10_threshold_adapter_relaxed_api_test")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    from gfa_core.extension_stage import ExtensionStageAgent, ExtensionStageManager

    os.environ["BBAV5_EXTENSION_CONVERTER"] = "deepseek_api"
    output_root = args.output_root.resolve()
    if args.overwrite and output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    row = base_row(args)
    started = time.time()
    manager = ExtensionStageManager(
        PROJECT_ROOT,
        store_root=output_root / "extension_stages",
        workspace_root=output_root / "workspace",
        input_root=output_root / "input",
    )
    agent = ExtensionStageAgent(manager)

    try:
        build = agent.create_from_uploads(
            display_name=args.display_name,
            command="bash run_10_hard_filter_clean_api_adapter.sh --input-dir {input_dir} --output-dir {output_dir}",
            runtime_env=args.runtime_env,
            code_files=[(args.code.name, args.code.read_bytes())],
            data_files=upload_data_files(args.data_dir),
            description="VCF-10 hard-filter threshold adapter API conversion test using validated partial 09.selected VCF inputs.",
        )
        write_json(output_root / "build_response.json", build)
        ext = build.get("extension") or {}
        eid = str(ext.get("extension_id") or "")
        row.update(
            {
                "extension_id": eid,
                "archetype_id": str(ext.get("archetype_id") or ""),
                "analysis_type": str(ext.get("analysis_type") or ""),
                "tool_family": str(ext.get("tool_family") or ""),
                "build_success": bool(build.get("success") and eid),
                "build_api_attempt_count": len(ext.get("build_attempts") or []),
                "command_template": str((ext.get("entrypoint") or {}).get("command_template") or ""),
            }
        )
        if not row["build_success"]:
            row["first_failure_phase"] = "build"
            return finish(output_root, row, started)

        trial = agent.trial_run(eid)
        write_json(output_root / "trial_run.json", trial)
        row["trial_success"] = bool(trial.get("success"))
        row["trial_output_count"] = len((trial.get("run") or {}).get("outputs") or [])
        row["trial_error"] = str((trial.get("run") or {}).get("error") or "")
        if not row["trial_success"]:
            row["first_failure_phase"] = "trial"
            return finish(output_root, row, started)

        outputs = (trial.get("extension") or {}).get("outputs_detected") or (trial.get("run") or {}).get("outputs") or []
        confirm = agent.confirm(
            eid,
            outputs=outputs,
            nl_triggers=[
                "vcf hard filter clean",
                "vcf threshold profile",
                "VCF拓展分析第十步过滤",
            ],
        )
        write_json(output_root / "confirm_response.json", confirm)
        row["confirm_success"] = bool(confirm.get("success"))
        if not row["confirm_success"]:
            row["first_failure_phase"] = "confirm"
            return finish(output_root, row, started)

        validation = agent.validate(eid)
        write_json(output_root / "validate_response.json", validation)
        row.update(validation_status(validation))
        if not row["validate_success"]:
            row["first_failure_phase"] = "validation"
            row["validation_error"] = str((validation.get("run") or {}).get("error") or "")
            return finish(output_root, row, started)

        publish = agent.publish(eid)
        write_json(output_root / "publish_response.json", publish)
        row["publish_success"] = bool(publish.get("success"))
        row["publish_status"] = str((publish.get("extension") or {}).get("status") or "")
        if not row["publish_success"]:
            row["first_failure_phase"] = "publish"
            return finish(output_root, row, started)

        job_id = "vcf10_threshold_adapter_published_run"
        materialize_job_inputs(agent, job_id, args.data_dir)
        published_run = agent.run(extension_id=eid, job_id=job_id)
        write_json(output_root / "published_run_response.json", published_run)
        row["published_run_success"] = bool(published_run.get("success"))
        row["published_run_error"] = str((published_run.get("run") or {}).get("error") or "")
        row["published_output_count"] = len((published_run.get("run") or {}).get("outputs") or [])
        if not row["published_run_success"]:
            row["first_failure_phase"] = "published_run"
    except Exception as exc:
        row["error"] = str(exc)
        row["traceback"] = traceback.format_exc()
        if not row.get("first_failure_phase"):
            row["first_failure_phase"] = infer_failure_phase(row)
        write_json(output_root / "error.json", {"error": row["error"], "traceback": row["traceback"], "row": row})

    return finish(output_root, row, started)


def upload_data_files(data_dir: Path) -> list[tuple[str, bytes]]:
    names = [
        "variants.snp.vcf.gz",
        "variants.snp.vcf.gz.tbi",
        "variants.indel.vcf.gz",
        "variants.indel.vcf.gz.tbi",
    ]
    files = []
    for name in names:
        path = data_dir / name
        if not path.exists():
            raise FileNotFoundError(f"missing required input: {path}")
        files.append((name, path.read_bytes()))
    return files


def materialize_job_inputs(agent: Any, job_id: str, raw_data: Path) -> None:
    input_dir = agent.manager.input_root / job_id
    input_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in upload_data_files(raw_data):
        (input_dir / name).write_bytes(payload)


def validation_status(response: dict[str, Any]) -> dict[str, Any]:
    run = response.get("run") or {}
    golden = run.get("golden_comparison") or {}
    gate = run.get("template_gate") or {}
    return {
        "validate_success": bool(response.get("success")),
        "validation_status": str(run.get("status") or ""),
        "validation_error": str(run.get("error") or ""),
        "golden_status": str(golden.get("status") or ""),
        "template_gate_status": str(gate.get("status") or ""),
    }


def base_row(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "schema_version": "vcf10_threshold_adapter_api_conversion_result_v1",
        "created_at": now(),
        "converter": "deepseek_api",
        "codex_manual_spec_edit": False,
        "runtime_env": args.runtime_env,
        "code_file": str(args.code),
        "data_dir": str(args.data_dir),
        "validation_scope": "partial real VCF inputs from 09.selected",
        "extension_id": "",
        "archetype_id": "",
        "analysis_type": "",
        "tool_family": "",
        "build_success": False,
        "trial_success": False,
        "confirm_success": False,
        "validate_success": False,
        "golden_status": "",
        "template_gate_status": "",
        "publish_success": False,
        "publish_status": "",
        "published_run_success": False,
        "api_autonomous_success": False,
        "first_failure_phase": "",
        "error": "",
        "traceback": "",
        "wall_sec": 0,
    }


def infer_failure_phase(row: dict[str, Any]) -> str:
    for phase, key in [
        ("build", "build_success"),
        ("trial", "trial_success"),
        ("confirm", "confirm_success"),
        ("validation", "validate_success"),
        ("golden", "golden_status"),
        ("template_gate", "template_gate_status"),
        ("publish", "publish_success"),
        ("published_run", "published_run_success"),
    ]:
        value = row.get(key)
        if key.endswith("_status"):
            if value != "passed":
                return phase
        elif not value:
            return phase
    return ""


def finish(output_root: Path, row: dict[str, Any], started: float) -> int:
    row["api_autonomous_success"] = all(
        [
            row.get("build_success"),
            row.get("trial_success"),
            row.get("confirm_success"),
            row.get("validate_success"),
            row.get("golden_status") == "passed",
            row.get("template_gate_status") == "passed",
            row.get("publish_success"),
            row.get("published_run_success"),
            not row.get("codex_manual_spec_edit"),
        ]
    )
    if not row.get("first_failure_phase") and not row["api_autonomous_success"]:
        row["first_failure_phase"] = infer_failure_phase(row)
    row["wall_sec"] = round(time.time() - started, 3)
    write_json(output_root / "result_row.json", row)
    return 0 if row["api_autonomous_success"] else 1


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
