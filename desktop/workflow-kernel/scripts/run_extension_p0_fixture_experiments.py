#!/usr/bin/env python3
"""No-API P0 fixture validation for extension-stage publish/template split."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.extension_stage.manager import ExtensionStageManager  # noqa: E402


DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "项目记录" / "拓展分析阶段_P0_fixture_validation_20260622"


@dataclass(frozen=True)
class FixtureCase:
    case_id: str
    description: str
    script_name: str
    script_content: str
    data_files: dict[str, bytes]
    original_command: str
    command_template: str
    output_contract: list[dict[str, Any]]
    input_roles: dict[str, Any] = field(default_factory=dict)
    input_bindings: dict[str, Any] = field(default_factory=dict)
    comparator_plan: dict[str, Any] = field(default_factory=dict)
    requirements: dict[str, Any] = field(default_factory=dict)
    expected_status: str = "published"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    out = args.output_root.resolve()
    if args.clean and out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    if args.run or not args.report:
        run_all(out)
    if args.report:
        write_summary(out)
    return 0


def run_all(out: Path) -> None:
    manager = ExtensionStageManager(
        project_root=PROJECT_ROOT,
        store_root=out / "extension_stages",
        workspace_root=out / "workspace",
        input_root=out / "input",
    )
    rows = []
    for case in cases():
        row = run_case(manager, out, case)
        rows.append(row)
    write_json(out / "summary.json", summarize(rows))
    write_csv(out / "summary.csv", rows)


def run_case(manager: ExtensionStageManager, out: Path, case: FixtureCase) -> dict[str, Any]:
    case_root = out / "cases" / case.case_id
    if case_root.exists():
        shutil.rmtree(case_root)
    case_root.mkdir(parents=True, exist_ok=True)
    extension = prepare_extension(manager, case)
    extension_id = extension["extension_id"]
    row: dict[str, Any] = {
        "schema_version": "extension_p0_fixture_result_v1",
        "created_at": now(),
        "case_id": case.case_id,
        "description": case.description,
        "extension_id": extension_id,
        "expected_status": case.expected_status,
        "trial_status": "",
        "confirm_success": False,
        "validate_success": False,
        "golden_status": "",
        "template_gate_status": "",
        "publish_success": False,
        "published_run_success": False,
        "published_runnable": False,
        "template_eligible": False,
        "environment_failed": False,
        "stdout_captured": False,
        "first_failure_phase": "",
        "last_error": "",
        "success": False,
    }
    try:
        trial = manager.trial_run(extension_id)
        write_json(case_root / "trial_run.json", trial)
        trial_run = trial.get("run") or {}
        row["trial_status"] = str(trial_run.get("status") or "")
        row["environment_failed"] = row["trial_status"] == "environment_failed"
        row["last_error"] = str(trial_run.get("error") or "")
        row["stdout_captured"] = stdout_nonempty(trial_run)
        if case.expected_status == "environment_failed":
            row["success"] = row["environment_failed"]
            if not row["success"]:
                row["first_failure_phase"] = "trial"
            write_case_outputs(manager, extension_id, case_root, row)
            return row
        if case.expected_status == "stdout_only_failed":
            row["success"] = row["trial_status"] == "failed" and row["stdout_captured"]
            if not row["success"]:
                row["first_failure_phase"] = "trial"
            write_case_outputs(manager, extension_id, case_root, row)
            return row
        if not trial.get("success"):
            row["first_failure_phase"] = "trial"
            write_case_outputs(manager, extension_id, case_root, row)
            return row

        outputs = (trial.get("extension") or {}).get("outputs_detected") or trial_run.get("outputs") or []
        confirm = manager.confirm(extension_id, outputs=outputs, input_bindings=case.input_bindings, nl_triggers=[case.case_id])
        write_json(case_root / "confirm_response.json", confirm)
        row["confirm_success"] = bool(confirm.get("success"))

        validation = manager.validate(extension_id)
        write_json(case_root / "validate_response.json", validation)
        validation_run = validation.get("run") or {}
        golden = validation_run.get("golden_comparison") or {}
        gate = validation_run.get("template_gate") or {}
        row["validate_success"] = bool(validation.get("success"))
        row["golden_status"] = str(golden.get("status") or "")
        row["template_gate_status"] = str(gate.get("status") or "")
        if not row["validate_success"]:
            row["first_failure_phase"] = "validation"
            row["last_error"] = str(validation_run.get("error") or "")
            write_case_outputs(manager, extension_id, case_root, row)
            return row

        publish = manager.publish(extension_id)
        write_json(case_root / "publish_response.json", publish)
        published_extension = publish.get("extension") or {}
        checks = published_extension.get("publish_checks") or {}
        row["publish_success"] = bool(publish.get("success"))
        row["published_runnable"] = bool(checks.get("published_runnable"))
        row["template_eligible"] = bool(checks.get("template_eligible"))
        row["template_gate_status"] = str(checks.get("template_gate_status") or row["template_gate_status"])
        if not row["publish_success"]:
            row["first_failure_phase"] = "publish"
            row["last_error"] = "; ".join(checks.get("errors") or [])
            write_case_outputs(manager, extension_id, case_root, row)
            return row

        job_id = f"{case.case_id}_job"
        materialize_job_input(manager, job_id, case.data_files)
        published_run = manager.run(extension_id=extension_id, job_id=job_id)
        write_json(case_root / "published_run_response.json", published_run)
        row["published_run_success"] = bool(published_run.get("success"))
        if not row["published_run_success"]:
            row["first_failure_phase"] = "published_run"
            row["last_error"] = str((published_run.get("run") or {}).get("error") or "")
        row["success"] = row["published_run_success"] and row["published_runnable"]
        if case.case_id == "generic_no_archetype":
            row["success"] = row["success"] and not row["template_eligible"] and row["template_gate_status"] == "not_applicable"
        write_case_outputs(manager, extension_id, case_root, row)
        return row
    except Exception as exc:
        row["last_error"] = str(exc)
        row["first_failure_phase"] = row.get("first_failure_phase") or "exception"
        write_json(case_root / "error.json", {"error": str(exc), "row": row})
        write_case_outputs(manager, extension_id, case_root, row)
        return row


def prepare_extension(manager: ExtensionStageManager, case: FixtureCase) -> dict[str, Any]:
    extension_id = "p0_" + safe_token(case.case_id)
    root = manager.store_root / extension_id
    if root.exists():
        shutil.rmtree(root)
    raw_code = root / "raw_code"
    raw_data = root / "raw_test_data"
    converted = root / "converted_code"
    for path in (raw_code, raw_data, converted):
        path.mkdir(parents=True, exist_ok=True)
    (raw_code / case.script_name).write_text(case.script_content, encoding="utf-8")
    (converted / case.script_name).write_text(case.script_content, encoding="utf-8")
    (raw_code / case.script_name).chmod(0o755)
    (converted / case.script_name).chmod(0o755)
    for name, payload in case.data_files.items():
        target = raw_data / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    extension = {
        "schema_version": "extension_stage_v2",
        "extension_id": extension_id,
        "name": safe_token(case.case_id),
        "display_name": case.case_id,
        "description": case.description,
        "created_at": now(),
        "status": "draft",
        "runtime": {"type": "conda", "env": "test4py311", "language": "shell"},
        "paths": {"root": str(root), "raw_code": "raw_code", "raw_test_data": "raw_test_data", "converted_code": "converted_code"},
        "entrypoint": {"original_command": case.original_command, "command_template": case.command_template},
        "command_template": case.command_template,
        "analysis_type": "generic_cli",
        "tool_family": "generic",
        "converter": {"type": "deterministic_fixture", "api_independent": False},
        "input_roles": case.input_roles,
        "input_bindings": case.input_bindings,
        "parameters": {},
        "confirmed_parameters": {},
        "outputs": {"directory": "outputs", "patterns": [item.get("name_pattern", "*") for item in case.output_contract]},
        "output_contract": case.output_contract,
        "comparator_plan": case.comparator_plan,
        "generated_code_artifacts": [{"path": case.script_name, "generated_by": "p0_fixture"}],
        "build_record": "deterministic_p0_fixture",
        "requirements": case.requirements,
        "source_files": {"code": [f"raw_code/{case.script_name}"], "test_data": [f"raw_test_data/{name}" for name in case.data_files]},
        "build_status": "analyzed",
        "outputs_confirmed": [],
        "outputs_detected": [],
        "validation_runs": [],
        "publish_checks": {},
        "template_gate": {},
        "nl_triggers": [case.case_id],
    }
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / "extension.json", extension)
    return extension


def cases() -> list[FixtureCase]:
    copy_script = """#!/usr/bin/env bash
set -euo pipefail
cp "$1" "$2"
"""
    vcf_trait_script = """#!/usr/bin/env bash
set -euo pipefail
vcf="$1"
trait="$2"
metrics="$3"
report="$4"
python3 - "$vcf" "$trait" "$metrics" "$report" <<'PY'
import json, sys
vcf, trait, metrics, report = sys.argv[1:5]
samples = []
for line in open(vcf, encoding='utf-8', errors='ignore'):
    if line.startswith('#CHROM'):
        samples = line.rstrip('\\n').split('\\t')[9:]
trait_rows = [x for x in open(trait, encoding='utf-8', errors='ignore').read().splitlines()[1:] if x.strip()]
open(metrics, 'w', encoding='utf-8').write(json.dumps({'sample_count': len(samples), 'trait_rows': len(trait_rows)}, sort_keys=True) + '\\n')
open(report, 'w', encoding='utf-8').write('sample_count\\ttrait_rows\\n%d\\t%d\\n' % (len(samples), len(trait_rows)))
PY
"""
    stdout_script = """#!/usr/bin/env bash
set -euo pipefail
echo "rows $(wc -l < "$1")"
"""
    binary_script = """#!/usr/bin/env bash
set -euo pipefail
python3 - "$1" "$2" <<'PY'
import sys
open(sys.argv[2], 'wb').write(open(sys.argv[1], 'rb').read())
PY
"""
    generic_script = """#!/usr/bin/env bash
set -euo pipefail
awk 'BEGIN{print "metric\\tvalue"} NR>1{n++} END{print "rows\\t" n+0}' "$1" > "$2"
"""
    return [
        FixtureCase(
            "positional_args_shell",
            "Validate positional $1/$2 shell interface through original_command and command_template.",
            "run_positional.sh",
            copy_script,
            {"input.tsv": b"gene\tvalue\nA\t1\n"},
            "bash run_positional.sh input.tsv x.txt",
            "bash {code_dir}/run_positional.sh {input_file} {output_dir}/x.txt",
            [{"name_pattern": "x.txt", "semantic_role": "copied_table", "required": True}],
            input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        ),
        FixtureCase(
            "vcf_trait_bindings",
            "Preserve open GWAS-style VCF plus trait table input_bindings.",
            "run_vcf_trait.sh",
            vcf_trait_script,
            {
                "subset.clean.vcf.gz": b"##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\n1\t10\trs1\tA\tG\t.\tPASS\t.\tGT\t0/1\t0/0\n",
                "trait.tsv": b"sample\ttrait\nS1\t1.2\nS2\t0.7\n",
            },
            "bash run_vcf_trait.sh subset.clean.vcf.gz trait.tsv gwas_metrics.json sample_match_report.tsv",
            "bash {code_dir}/run_vcf_trait.sh {input_dir}/subset.clean.vcf.gz {input_dir}/trait.tsv {output_dir}/gwas_metrics.json {output_dir}/sample_match_report.tsv",
            [
                {"name_pattern": "gwas_metrics.json", "semantic_role": "gwas_metrics", "required": True},
                {"name_pattern": "sample_match_report.tsv", "semantic_role": "gwas_sample_match_report", "required": True},
            ],
            input_bindings={
                "input_vcf": {"placeholder": "input_file", "format_hint": "vcf.gz", "semantic_hint": "variant_calls", "required": True},
                "trait_table": {"placeholder": "input_file", "format_hint": "tsv", "semantic_hint": "phenotype_table", "required": True},
            },
        ),
        FixtureCase(
            "missing_system_command",
            "Classify absent external command as environment_failed instead of API/spec failure.",
            "run_missing.sh",
            "#!/usr/bin/env bash\nset -euo pipefail\ndefinitely_missing_p0_tool \"$1\" > \"$2\"\n",
            {"input.txt": b"hello\n"},
            "definitely_missing_p0_tool input.txt > x.txt",
            "definitely_missing_p0_tool {input_file} > {output_dir}/x.txt",
            [{"name_pattern": "x.txt", "required": True}],
            requirements={"system": ["definitely_missing_p0_tool"]},
            expected_status="environment_failed",
        ),
        FixtureCase(
            "json_metrics",
            "Compare JSON metric files as publishable output.",
            "run_json.sh",
            "#!/usr/bin/env bash\nset -euo pipefail\ncp \"$1\" \"$2\"\n",
            {"metrics.json": b"{\"a\": 1, \"b\": 2}\n"},
            "bash run_json.sh metrics.json metrics.out.json",
            "bash {code_dir}/run_json.sh {input_file} {output_dir}/metrics.out.json",
            [{"name_pattern": "metrics.out.json", "semantic_role": "json_metrics", "required": True, "compare_mode": "auto"}],
            input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        ),
        FixtureCase(
            "stdout_only",
            "Capture stdout-only behavior and classify no-file output as a fixture-level expected failure.",
            "run_stdout.sh",
            stdout_script,
            {"input.txt": b"a\nb\n"},
            "bash run_stdout.sh input.txt",
            "bash {code_dir}/run_stdout.sh {input_file}",
            [{"name_pattern": "stdout.txt", "required": True}],
            input_roles={"input_file": {"role": "generic_input_file", "required": True}},
            expected_status="stdout_only_failed",
        ),
        FixtureCase(
            "binary_compressed_presence",
            "Validate binary/compressed-like file presence and byte-identical golden comparison.",
            "run_binary.sh",
            binary_script,
            {"input.bin": b"\x1f\x8bP0-fixture-binary\n"},
            "bash run_binary.sh input.bin binary.dat",
            "bash {code_dir}/run_binary.sh {input_file} {output_dir}/binary.dat",
            [{"name_pattern": "binary.dat", "semantic_role": "binary_artifact", "required": True, "compare_mode": "binary_sha256"}],
            input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        ),
        FixtureCase(
            "generic_no_archetype",
            "Publish runnable generic extension while keeping template_eligible=false.",
            "run_generic.sh",
            generic_script,
            {"table.tsv": b"id\tvalue\nA\t1\nB\t2\n"},
            "bash run_generic.sh table.tsv summary.tsv",
            "bash {code_dir}/run_generic.sh {input_file} {output_dir}/summary.tsv",
            [{"name_pattern": "summary.tsv", "semantic_role": "summary_table", "required": True}],
            input_bindings={"input_table": {"placeholder": "input_file", "format_hint": "tsv", "semantic_hint": "generic_table", "required": True}},
        ),
    ]


def materialize_job_input(manager: ExtensionStageManager, job_id: str, data_files: dict[str, bytes]) -> None:
    input_dir = manager.input_root / safe_token(job_id)
    if input_dir.exists():
        shutil.rmtree(input_dir)
    input_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in data_files.items():
        target = input_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def write_case_outputs(manager: ExtensionStageManager, extension_id: str, case_root: Path, row: dict[str, Any]) -> None:
    try:
        extension = manager.load(extension_id)
        write_json(case_root / "extension.json", extension)
    except Exception:
        pass
    write_json(case_root / "result_row.json", row)


def stdout_nonempty(run: dict[str, Any]) -> bool:
    workspace = Path(str(run.get("workspace") or ""))
    rel = str((run.get("logs") or {}).get("stdout") or "logs/stdout.log")
    path = workspace / rel
    if path.exists():
        return path.stat().st_size > 0
    try:
        extension_root = workspace.parents[1]
        store_root = extension_root.parent
        relative_workspace = workspace.relative_to(extension_root)
        for status_dir in ("failed", "success"):
            relocated = store_root / status_dir / extension_root.name / relative_workspace / rel
            if relocated.exists():
                return relocated.stat().st_size > 0
    except Exception:
        pass
    return False


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "extension_p0_fixture_summary_v1",
        "created_at": now(),
        "case_count": len(rows),
        "passed": sum(1 for row in rows if row.get("success")),
        "failed": sum(1 for row in rows if not row.get("success")),
        "published_runnable": sum(1 for row in rows if row.get("published_runnable")),
        "template_eligible": sum(1 for row in rows if row.get("template_eligible")),
        "environment_failed": sum(1 for row in rows if row.get("environment_failed")),
        "rows": rows,
    }


def write_summary(out: Path) -> None:
    rows = [read_json(path) for path in sorted((out / "cases").glob("*/result_row.json"))]
    write_json(out / "summary.json", summarize(rows))
    write_csv(out / "summary.csv", rows)


def safe_token(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value)).strip("._") or "unknown"


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
