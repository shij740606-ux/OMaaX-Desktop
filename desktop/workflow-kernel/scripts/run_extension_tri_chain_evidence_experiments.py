#!/usr/bin/env python3
"""Prepare and optionally run tri-chain extension-stage evidence experiments.

This script strengthens the extension-stage template evidence base with cases
selected from three bbav5 chains:

1. subfamily/reference classification
2. gene structure and promoter/cis-element post-processing
3. RNA-seq table post-processing with bounded input subsets

RNA-seq cases use bounded table subsets and are promoted only as lightweight
post-processing stages. A complete RNA-seq workflow remains outside the
extension-stage boundary.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DEFAULT_EVIDENCE_JOB = PROJECT_ROOT / "workspace" / "job_1781949576460_93f63d"
DEFAULT_VALIDATION_JOB = PROJECT_ROOT / "workspace" / "job_1780581757475_be7fd4"
DEFAULT_PROMOTER_JOB = PROJECT_ROOT / "workspace" / "job_1782045849720_9a6952"
DEFAULT_RNASEQ_JOB = PROJECT_ROOT / "workspace" / "job_1780313949486_3f3732"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "项目记录" / "拓展分析阶段_三链路模板证据实验_20260622"


@dataclass(frozen=True)
class SourceFile:
    rel_path: str
    dest_name: str
    row_limit: int = 0
    required: bool = True


@dataclass(frozen=True)
class TriCase:
    index: int
    case_id: str
    group: str
    archetype_id: str
    stage: str
    split: str
    job_root: Path
    script_name: str
    script_content: str
    source_files: tuple[SourceFile, ...]
    required_outputs: tuple[str, ...]
    description: str
    candidate_only: bool = False
    validation_gap: str = ""

    @property
    def dir_name(self) -> str:
        suffix = "candidate" if self.candidate_only else self.split
        return f"{self.index:02d}_{self.case_id}_{suffix}"


def _sh(script: str) -> str:
    return "#!/usr/bin/env bash\nset -euo pipefail\npython3 - <<'PY'\n" + script.strip() + "\nPY\n"


SCRIPT_REFCLS = _sh(
    """
from pathlib import Path
for src, dst in [
    ('assignment.tsv', 'output.assignment.tsv'),
    ('subfamily.tsv', 'output.subfamily.tsv'),
    ('confidence.tsv', 'output.confidence.tsv'),
    ('tree.nwk', 'output.tree.nwk'),
]:
    p = Path(src)
    if p.exists():
        Path(dst).write_text(p.read_text(encoding='utf-8', errors='ignore'), encoding='utf-8')
rows = [line for line in Path('assignment.tsv').read_text(encoding='utf-8', errors='ignore').splitlines() if line.strip()]
subfamilies = set()
for line in Path('subfamily.tsv').read_text(encoding='utf-8', errors='ignore').splitlines()[1:]:
    parts = line.split('\\t')
    if len(parts) > 1 and parts[1]:
        subfamilies.add(parts[1])
Path('reference_classification_summary.tsv').write_text(
    'metric\\tvalue\\nassignment_rows\\t{}\\nsubfamily_count\\t{}\\n'.format(max(len(rows) - 1, 0), len(subfamilies)),
    encoding='utf-8',
)
"""
)


SCRIPT_STRUCTURE = _sh(
    """
from pathlib import Path
gff = Path('structure.filtered.gff3').read_text(encoding='utf-8', errors='ignore')
table = Path('structure.tsv').read_text(encoding='utf-8', errors='ignore')
Path('output.structure.filtered.gff3').write_text(gff, encoding='utf-8')
Path('output.structure.tsv').write_text(table, encoding='utf-8')
features = 0
genes = set()
for line in gff.splitlines():
    if not line or line.startswith('#'):
        continue
    features += 1
    attrs = line.split('\\t')[-1] if '\\t' in line else ''
    for token in attrs.replace(';', '\\t').split('\\t'):
        if token.startswith('ID=') or token.startswith('Parent='):
            genes.add(token.split('=', 1)[1].split('.')[0])
Path('structure_summary.tsv').write_text(
    'metric\\tvalue\\ngff_feature_rows\\t{}\\ngene_like_ids\\t{}\\nstructure_table_rows\\t{}\\n'.format(
        features, len(genes), max(len([x for x in table.splitlines() if x.strip()]) - 1, 0)
    ),
    encoding='utf-8',
)
"""
)


SCRIPT_PROMOTER = _sh(
    """
from pathlib import Path
rows = [line for line in Path('plantcare_filtered.tab').read_text(encoding='utf-8', errors='ignore').splitlines() if line.strip()]
Path('output.cis_elements.tsv').write_text('\\n'.join(rows) + '\\n', encoding='utf-8')
counts = Path('element_counts.txt')
if counts.exists():
    Path('output.element_counts.tsv').write_text(counts.read_text(encoding='utf-8', errors='ignore'), encoding='utf-8')
else:
    seen = {}
    for line in rows[1:]:
        parts = line.split('\\t')
        if len(parts) > 1:
            seen[parts[1]] = seen.get(parts[1], 0) + 1
    Path('output.element_counts.tsv').write_text('element\\tcount\\n' + ''.join(f'{k}\\t{v}\\n' for k, v in sorted(seen.items())), encoding='utf-8')
bed = Path('PlantCARE.bed')
if bed.exists():
    Path('output.promoter.bed').write_text(bed.read_text(encoding='utf-8', errors='ignore'), encoding='utf-8')
Path('output.promoter_summary.tsv').write_text(
    'metric\\tvalue\\ncis_element_rows\\t{}\\nelement_count_rows\\t{}\\nbed_rows\\t{}\\n'.format(
        max(len(rows) - 1, 0),
        len([x for x in Path('output.element_counts.tsv').read_text(encoding='utf-8', errors='ignore').splitlines() if x.strip()]),
        len([x for x in bed.read_text(encoding='utf-8', errors='ignore').splitlines() if x.strip()]) if bed.exists() else 0,
    ),
    encoding='utf-8',
)
"""
)


SCRIPT_RNASEQ_QUANT = _sh(
    """
from pathlib import Path
for src, dst in [('count_matrix.txt', 'output.count_matrix.tsv'), ('tpm_matrix.txt', 'output.tpm_matrix.tsv')]:
    Path(dst).write_text(Path(src).read_text(encoding='utf-8', errors='ignore'), encoding='utf-8')
counts = Path('count_matrix.txt').read_text(encoding='utf-8', errors='ignore').splitlines()
tpm = Path('tpm_matrix.txt').read_text(encoding='utf-8', errors='ignore').splitlines()
Path('quant_summary.tsv').write_text(
    'metric\\tvalue\\ncount_matrix_rows\\t{}\\ntpm_matrix_rows\\t{}\\n'.format(max(len(counts) - 1, 0), max(len(tpm) - 1, 0)),
    encoding='utf-8',
)
"""
)


SCRIPT_RNASEQ_DE = _sh(
    """
from pathlib import Path
text = Path('de_results.txt').read_text(encoding='utf-8', errors='ignore')
Path('output.de_results.tsv').write_text(text, encoding='utf-8')
lines = [line for line in text.splitlines() if line.strip()]
Path('de_summary.tsv').write_text('metric\\tvalue\\nde_result_rows\\t{}\\n'.format(max(len(lines) - 1, 0)), encoding='utf-8')
Path('top_de_preview.tsv').write_text('\\n'.join(lines[:51]) + ('\\n' if lines else ''), encoding='utf-8')
"""
)


def tri_cases(evidence_job: Path, validation_job: Path, promoter_job: Path, rnaseq_job: Path) -> tuple[TriCase, ...]:
    return (
        TriCase(
            1,
            "reference_classification_hdzip_prediction",
            "subfamily_reference",
            "reference_classification",
            "reference_classification",
            "evidence",
            evidence_job,
            "run_reference_classification_tables.sh",
            SCRIPT_REFCLS,
            (
                SourceFile("final/Target.HDZIP.target-Si.ref-At.subfamily_prediction.reference_classification.assignment.tsv", "assignment.tsv"),
                SourceFile("final/Target.HDZIP.target-Si.ref-At.subfamily_prediction.reference_classification.subfamily.tsv", "subfamily.tsv"),
                SourceFile("final/Target.HDZIP.target-Si.ref-At.subfamily_prediction.reference_classification.confidence.tsv", "confidence.tsv"),
                SourceFile("final/Target.HDZIP.target-Si.ref-At.subfamily_prediction.reference_classification.tree.nwk", "tree.nwk"),
            ),
            ("output.assignment.tsv", "output.subfamily.tsv", "reference_classification_summary.tsv"),
            "Use Si HDZIP target plus At reference subfamily prediction outputs from job_1781949576460_93f63d.",
        ),
        TriCase(
            2,
            "reference_classification_hdzip_target_plus_reference",
            "subfamily_reference",
            "reference_classification",
            "reference_classification",
            "validation",
            evidence_job,
            "run_reference_classification_tables.sh",
            SCRIPT_REFCLS,
            (
                SourceFile("final/Si.HDZIP.target_plus_reference.reference_classification.assignment.tsv", "assignment.tsv"),
                SourceFile("final/Si.HDZIP.target_plus_reference.reference_classification.subfamily.tsv", "subfamily.tsv"),
                SourceFile("final/Si.HDZIP.target_plus_reference.reference_classification.confidence.tsv", "confidence.tsv"),
                SourceFile("final/Si.HDZIP.target_plus_reference.reference_classification.tree.nwk", "tree.nwk", required=False),
            ),
            ("output.assignment.tsv", "output.subfamily.tsv", "reference_classification_summary.tsv"),
            "Use Si HDZIP target_plus_reference classification tables from the same completed job as an alternate split.",
        ),
        TriCase(
            3,
            "gene_structure_hdzip",
            "gene_family_structure_promoter",
            "gene_structure_gff",
            "structure",
            "evidence",
            evidence_job,
            "run_gene_structure_gff.sh",
            SCRIPT_STRUCTURE,
            (
                SourceFile("structure/analysis/Target.HDZIP.target-Si.ref-At.subfamily_evidence.structure.filtered.gff3", "structure.filtered.gff3"),
                SourceFile("structure/analysis/Target.HDZIP.target-Si.ref-At.subfamily_evidence.structure.tsv", "structure.tsv"),
            ),
            ("output.structure.filtered.gff3", "output.structure.tsv", "structure_summary.tsv"),
            "Use Si HDZIP subfamily-evidence structure GFF/table from job_1781949576460_93f63d.",
        ),
        TriCase(
            4,
            "gene_structure_ls",
            "gene_family_structure_promoter",
            "gene_structure_gff",
            "structure",
            "validation",
            validation_job,
            "run_gene_structure_gff.sh",
            SCRIPT_STRUCTURE,
            (
                SourceFile("structure/analysis/Ls.SPL.structure.filtered.gff3", "structure.filtered.gff3"),
                SourceFile("structure/analysis/Ls.SPL.structure.tsv", "structure.tsv"),
            ),
            ("output.structure.filtered.gff3", "output.structure.tsv", "structure_summary.tsv"),
            "Use Ls gene structure GFF/table as a held-out validation split.",
        ),
        TriCase(
            5,
            "promoter_spl",
            "gene_family_structure_promoter",
            "promoter_cis_element",
            "promoter",
            "evidence",
            promoter_job,
            "run_promoter_cis_element.sh",
            SCRIPT_PROMOTER,
            (
                SourceFile("promoter/analysis/plantCARE_output_filtered.tab", "plantcare_filtered.tab"),
                SourceFile("promoter/analysis/element_counts.txt", "element_counts.txt"),
                SourceFile("promoter/analysis/PlantCARE.bed", "PlantCARE.bed"),
            ),
            ("output.cis_elements.tsv", "output.element_counts.tsv", "output.promoter_summary.tsv"),
            "Use existing SPL promoter PlantCARE/BED/count artifacts because job_1781949576460_93f63d has no promoter stage outputs.",
        ),
        TriCase(
            6,
            "rnaseq_quant_subset",
            "rnaseq_table_extensions",
            "rnaseq_quant_matrix",
            "rnaseq_quant",
            "subset",
            rnaseq_job,
            "run_rnaseq_quant_subset.sh",
            SCRIPT_RNASEQ_QUANT,
            (
                SourceFile("final/Os.count_matrix.txt", "count_matrix.txt", row_limit=200),
                SourceFile("final/Os.tpm_matrix.txt", "tpm_matrix.txt", row_limit=200),
            ),
            ("output.count_matrix.tsv", "output.tpm_matrix.tsv", "quant_summary.tsv"),
            "Bounded RNA-seq quantification matrix subset for publishable table post-processing validation.",
        ),
        TriCase(
            7,
            "rnaseq_de_subset",
            "rnaseq_table_extensions",
            "rnaseq_de_table",
            "rnaseq_de",
            "subset",
            rnaseq_job,
            "run_rnaseq_de_subset.sh",
            SCRIPT_RNASEQ_DE,
            (SourceFile("final/Os.rnaseq_de.H1_vs_Con.DE_results.txt", "de_results.txt", row_limit=200),),
            ("output.de_results.tsv", "de_summary.tsv", "top_de_preview.tsv"),
            "Bounded RNA-seq DE result table subset for publishable table post-processing validation.",
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="Create raw_code/raw_test_data/expected_outputs packages.")
    parser.add_argument("--run", action="store_true", help="Run publish/validate loop for non-candidate cases.")
    parser.add_argument("--report", action="store_true", help="Write metrics and HTML/Markdown reports.")
    parser.add_argument("--all", action="store_true", help="Run prepare, non-candidate extension loop, and report.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--evidence-job", type=Path, default=DEFAULT_EVIDENCE_JOB)
    parser.add_argument("--validation-job", type=Path, default=DEFAULT_VALIDATION_JOB)
    parser.add_argument("--promoter-job", type=Path, default=DEFAULT_PROMOTER_JOB)
    parser.add_argument("--rnaseq-job", type=Path, default=DEFAULT_RNASEQ_JOB)
    parser.add_argument("--converter", default="archetype_spec", choices=["archetype_spec", "deepseek_agent", "deepseek_api", "local_rule", "rule"])
    parser.add_argument("--runtime-env", default="test4py311")
    parser.add_argument("--include-candidates", action="store_true", help="Also send candidate-only cases to the extension loop.")
    parser.add_argument("--stop-on-failure", action="store_true")
    args = parser.parse_args()
    if not any((args.prepare, args.run, args.report, args.all)):
        args.prepare = True
        args.report = True

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    cases = tri_cases(args.evidence_job.resolve(), args.validation_job.resolve(), args.promoter_job.resolve(), args.rnaseq_job.resolve())

    if args.prepare or args.all:
        prepare(cases, output_root)
    if args.run or args.all:
        run_batch(cases, output_root, args.converter, args.runtime_env, args.include_candidates, args.stop_on_failure)
    if args.report or args.all:
        write_report(output_root, cases)
    write_manifest(output_root)
    return 0


def prepare(cases: tuple[TriCase, ...], output_root: Path) -> None:
    packages = output_root / "packages"
    logs = output_root / "logs"
    packages.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in cases:
        root = packages / case.dir_name
        raw_code = root / "raw_code"
        raw_data = root / "raw_test_data"
        expected = root / "expected_outputs"
        for path in (raw_code, raw_data, expected, root / "_raw_run_work"):
            clean_dir(path)
        script = raw_code / case.script_name
        script.write_text(case.script_content, encoding="utf-8")
        script.chmod(0o755)
        write_json(raw_code / "bbav5_extension_hint.json", extension_hint(case))
        copied = copy_sources(case, raw_data)
        raw = run_raw(root, case, logs / f"{case.dir_name}.raw.log")
        expected_ok = raw["success"] and all((expected / name).is_file() and (expected / name).stat().st_size > 0 for name in case.required_outputs)
        record = {
            "case_id": case.case_id,
            "group": case.group,
            "archetype_id": case.archetype_id,
            "stage": case.stage,
            "split": case.split,
            "candidate_only": case.candidate_only,
            "validation_gap": case.validation_gap,
            "case_dir": str(root),
            "source_job": str(case.job_root),
            "source_files": copied,
            "raw_code_ok": raw["success"],
            "expected_output_ok": expected_ok,
            "required_outputs": list(case.required_outputs),
            "raw_run": raw,
        }
        rows.append(record)
        write_json(root / "prepare_record.json", record)
        (root / "README.md").write_text(case_readme(case, copied), encoding="utf-8")
    write_json(output_root / "prepare_manifest.json", {
        "schema_version": "extension_tri_chain_prepare_v1",
        "created_at": now(),
        "case_count": len(rows),
        "prepared": sum(1 for row in rows if row["expected_output_ok"]),
        "candidate_only": sum(1 for row in rows if row["candidate_only"]),
        "cases": rows,
    })


def copy_sources(case: TriCase, raw_data: Path) -> list[dict[str, Any]]:
    copied = []
    for source in case.source_files:
        src = case.job_root / source.rel_path
        if not src.is_file():
            if source.required:
                raise FileNotFoundError(f"missing source for {case.case_id}: {src}")
            copied.append({
                "source": str(src),
                "dest": source.dest_name,
                "source_size": 0,
                "dest_size": 0,
                "sha256": "",
                "row_limit": source.row_limit,
                "copied_rows": 0,
                "required": False,
                "missing_optional": True,
            })
            continue
        dest = raw_data / source.dest_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source.row_limit and _looks_tabular(src):
            copied_rows = copy_tabular_subset(src, dest, source.row_limit)
        else:
            shutil.copy2(src, dest)
            copied_rows = 0
        copied.append({
            "source": str(src),
            "dest": source.dest_name,
            "source_size": src.stat().st_size,
            "dest_size": dest.stat().st_size,
            "sha256": sha256(dest),
            "row_limit": source.row_limit,
            "copied_rows": copied_rows,
            "required": source.required,
        })
    return copied


def copy_tabular_subset(src: Path, dest: Path, row_limit: int) -> int:
    rows = []
    with src.open("r", encoding="utf-8", errors="ignore") as handle:
        for index, line in enumerate(handle):
            if index == 0 or index <= row_limit:
                rows.append(line)
            else:
                break
    dest.write_text("".join(rows), encoding="utf-8")
    return max(len(rows) - 1, 0)


def run_raw(root: Path, case: TriCase, log_path: Path) -> dict[str, Any]:
    work = root / "_raw_run_work"
    expected = root / "expected_outputs"
    copy_tree(root / "raw_test_data", work)
    copy_tree(root / "raw_code", work)
    before = file_set(work)
    proc = subprocess.run(["bash", case.script_name], cwd=work, capture_output=True, text=True, timeout=240)
    after = file_set(work)
    new_files = sorted(after - before)
    for rel in new_files:
        src = work / rel
        if src.is_file():
            (expected / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, expected / rel)
    log_path.write_text(f"$ bash {case.script_name}\n\n[stdout]\n{proc.stdout}\n\n[stderr]\n{proc.stderr}\n", encoding="utf-8")
    return {"success": proc.returncode == 0, "returncode": proc.returncode, "new_files": new_files, "stderr_tail": proc.stderr[-2000:]}


def run_batch(cases: tuple[TriCase, ...], output_root: Path, converter: str, runtime_env: str, include_candidates: bool, stop_on_failure: bool) -> None:
    sys.path.insert(0, str(PROJECT_ROOT))
    from gfa_core.extension_stage import ExtensionStageAgent, ExtensionStageManager

    effective = "deepseek_api" if converter in {"archetype_spec", "deepseek_agent"} else converter
    os.environ["BBAV5_EXTENSION_CONVERTER"] = effective
    runnable = tuple(case for case in cases if include_candidates or not case.candidate_only)
    if converter == "archetype_spec":
        import gfa_core.extension_stage.agent as agent_module

        def _convert_with_archetype_spec(**kwargs: Any) -> dict[str, Any]:
            return local_conversion_spec(runnable, str(kwargs.get("display_name") or ""), str(kwargs.get("runtime_env") or runtime_env))

        agent_module.convert_with_deepseek_api = _convert_with_archetype_spec
        agent_module.repair_build_with_deepseek_api = _convert_with_archetype_spec

    run_root = output_root / "runtime" / converter
    for path in (run_root / "extension_stages", run_root / "workspace", run_root / "input", output_root / "results" / converter):
        clean_dir(path)
    manager = ExtensionStageManager(PROJECT_ROOT, store_root=run_root / "extension_stages", workspace_root=run_root / "workspace", input_root=run_root / "input")
    agent = ExtensionStageAgent(manager)
    rows = []
    for case in runnable:
        row = run_one(agent, output_root, case, converter, runtime_env)
        rows.append(row)
        if stop_on_failure and not row_success(row):
            break
    summary = {
        "schema_version": "extension_tri_chain_batch_result_v1",
        "created_at": now(),
        "converter": converter,
        "runtime_env": runtime_env,
        "include_candidates": include_candidates,
        "case_count": len(rows),
        "passed": sum(1 for row in rows if row_success(row)),
        "failed": sum(1 for row in rows if not row_success(row)),
        "rows": rows,
    }
    result_dir = output_root / "results" / converter
    write_json(result_dir / "summary.json", summary)
    write_csv(result_dir / "summary.csv", rows)
    (result_dir / "summary.md").write_text(summary_markdown(summary), encoding="utf-8")


def run_one(agent: Any, output_root: Path, case: TriCase, converter: str, runtime_env: str) -> dict[str, Any]:
    case_root = output_root / "packages" / case.dir_name
    result_dir = output_root / "results" / converter / "per_case" / case.dir_name
    clean_dir(result_dir)
    row = {
        "case_id": case.case_id,
        "group": case.group,
        "archetype_id": case.archetype_id,
        "stage": case.stage,
        "split": case.split,
        "converter": converter,
        "candidate_only": case.candidate_only,
        "raw_code_ok": (case_root / "prepare_record.json").exists(),
        "expected_output_ok": expected_ok(case_root, case),
        "build_success": False,
        "extension_id": "",
        "trial_success": False,
        "confirm_success": False,
        "validate_success": False,
        "golden_status": "",
        "template_gate_status": "",
        "publish_success": False,
        "output_count": 0,
        "error": "",
    }
    try:
        build = agent.create_from_uploads(
            display_name=f"tri_chain_{case.case_id}",
            command=f"bash {case.script_name}",
            runtime_env=runtime_env,
            code_files=upload_files(case_root / "raw_code"),
            data_files=upload_files(case_root / "raw_test_data"),
            description=case.description,
        )
        write_json(result_dir / "build_response.json", build)
        extension = build.get("extension") or {}
        extension_id = str(extension.get("extension_id") or "")
        row.update({"build_success": bool(build.get("success") and extension_id), "extension_id": extension_id})
        trial = agent.trial_run(extension_id)
        write_json(result_dir / "trial_run.json", trial)
        row["trial_success"] = bool(trial.get("success"))
        row["output_count"] = len((trial.get("run") or {}).get("outputs") or [])
        outputs = (trial.get("extension") or {}).get("outputs_detected") or (trial.get("run") or {}).get("outputs") or []
        confirm = agent.confirm(extension_id, outputs=outputs, nl_triggers=[case.case_id, case.stage, case.archetype_id])
        write_json(result_dir / "confirm_response.json", confirm)
        row["confirm_success"] = bool(confirm.get("success"))
        validation = agent.validate(extension_id)
        write_json(result_dir / "validate_response.json", validation)
        run = validation.get("run") or {}
        row["validate_success"] = bool(validation.get("success"))
        row["golden_status"] = str((run.get("golden_comparison") or {}).get("status") or "")
        row["template_gate_status"] = str((run.get("template_gate") or {}).get("status") or "")
        publish = agent.publish(extension_id)
        write_json(result_dir / "publish_response.json", publish)
        row["publish_success"] = bool(publish.get("success"))
    except Exception as exc:
        row["error"] = str(exc)
        write_json(result_dir / "error.json", {"error": str(exc), "traceback": traceback.format_exc(), "row": row})
    write_json(result_dir / "result_row.json", row)
    return row


def extension_hint(case: TriCase) -> dict[str, Any]:
    if case.archetype_id:
        from gfa_core.extension_stage.template_cases import ARCHETYPE_BY_ID

        archetype = ARCHETYPE_BY_ID[case.archetype_id]
        input_roles = {key: dict(value) for key, value in archetype.input_roles.items()}
        output_contract = [dict(item) for item in archetype.output_contract]
        comparator_plan = dict(archetype.comparator_plan)
        analysis_type = archetype.analysis_type
        tool_family = archetype.tool_family
    else:
        input_roles = {"input_file": {"role": "generic_input_file", "required": True}}
        output_contract = [
            {"name_pattern": name, "type": out_type(name), "primary": index == 0, "required": True, "allow_empty": False, "semantic_role": "rnaseq_table_candidate", "compare_mode": "table", "required_for_golden": True}
            for index, name in enumerate(case.required_outputs)
        ]
        comparator_plan = {"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": []}
        analysis_type = "table_filter"
        tool_family = "python"
    return {
        "schema_version": "bbav5_tri_chain_extension_hint_v1",
        "case_id": case.case_id,
        "archetype_id": case.archetype_id,
        "group": case.group,
        "representative_stage": case.stage,
        "recommended_original_command": f"bash {case.script_name}",
        "analysis_type": analysis_type,
        "tool_family": tool_family,
        "input_roles": input_roles,
        "output_contract": output_contract,
        "comparator_plan": comparator_plan,
        "requires_user_confirmation": bool(case.candidate_only),
        "candidate_only": case.candidate_only,
        "validation_gap": case.validation_gap,
    }


def local_conversion_spec(cases: tuple[TriCase, ...], display_name: str, runtime_env: str) -> dict[str, Any]:
    case = case_from_display(cases, display_name)
    hint = extension_hint(case)
    return {
        "template_type": "tri_chain_archetype_spec",
        "archetype_id": case.archetype_id,
        "original_command": f"bash {case.script_name}",
        "command_template": f"bash {{code_dir}}/{case.script_name} --input-dir {{input_dir}} --output-dir {{output_dir}}",
        "semantic_command_template": "",
        "wrapper_command_template": f"bash {{code_dir}}/{case.script_name} --input-dir {{input_dir}} --output-dir {{output_dir}}",
        "analysis_type": hint["analysis_type"],
        "tool_family": hint["tool_family"],
        "input_roles": hint["input_roles"],
        "inputs": {key: {"required": value.get("required", True)} for key, value in hint["input_roles"].items()},
        "parameters": {},
        "outputs": {"directory": "outputs", "patterns": list(case.required_outputs)},
        "output_contract": hint["output_contract"],
        "comparator_plan": hint["comparator_plan"],
        "requires_user_confirmation": bool(case.candidate_only),
        "notes": "Tri-chain deterministic archetype spec; RNA-seq cases are bounded table post-processing subsets, not full RNA-seq workflows.",
        "converter": {"type": "archetype_spec", "mode": "tri_chain_reproducible", "runtime_env": runtime_env},
    }


def write_report(output_root: Path, cases: tuple[TriCase, ...]) -> None:
    prepare_manifest = read_json(output_root / "prepare_manifest.json")
    summaries = {}
    for converter in ("archetype_spec", "deepseek_agent", "deepseek_api", "local_rule", "rule"):
        path = output_root / "results" / converter / "summary.json"
        if path.exists():
            summaries[converter] = read_json(path)
    metrics = {
        "schema_version": "extension_tri_chain_metrics_v1",
        "created_at": now(),
        "case_count": len(cases),
        "prepared": prepare_manifest.get("prepared", 0),
        "candidate_only": sum(1 for case in cases if case.candidate_only),
        "template_cases": sum(1 for case in cases if case.archetype_id),
        "groups": group_metrics(cases, prepare_manifest, summaries),
        "summaries": summaries,
        "validation_gaps": [case.validation_gap for case in cases if case.validation_gap],
    }
    write_json(output_root / "tri_chain_metrics.json", metrics)
    write_csv(output_root / "tri_chain_case_table.csv", case_rows(cases, prepare_manifest))
    (output_root / "tri_chain_report.md").write_text(report_markdown(metrics), encoding="utf-8")
    (output_root / "tri_chain_report.html").write_text(report_html(metrics), encoding="utf-8")


def group_metrics(cases: tuple[TriCase, ...], prepare_manifest: dict[str, Any], summaries: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    prepared_by_case = {row.get("case_id"): row for row in prepare_manifest.get("cases") or []}
    for group in sorted({case.group for case in cases}):
        group_cases = [case for case in cases if case.group == group]
        rows.append({
            "group": group,
            "case_count": len(group_cases),
            "prepared": sum(1 for case in group_cases if (prepared_by_case.get(case.case_id) or {}).get("expected_output_ok")),
            "candidate_only": sum(1 for case in group_cases if case.candidate_only),
            "template_archetypes": sorted({case.archetype_id for case in group_cases if case.archetype_id}),
            "run_passed": {
                name: sum(1 for row in summary.get("rows") or [] if row.get("group") == group and row_success(row))
                for name, summary in summaries.items()
            },
        })
    return rows


def case_rows(cases: tuple[TriCase, ...], prepare_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    prepared = {row.get("case_id"): row for row in prepare_manifest.get("cases") or []}
    rows = []
    for case in cases:
        prep = prepared.get(case.case_id) or {}
        rows.append({
            "case_id": case.case_id,
            "group": case.group,
            "archetype_id": case.archetype_id,
            "stage": case.stage,
            "split": case.split,
            "candidate_only": case.candidate_only,
            "source_job": str(case.job_root),
            "expected_output_ok": prep.get("expected_output_ok", False),
            "validation_gap": case.validation_gap,
        })
    return rows


def report_markdown(metrics: dict[str, Any]) -> str:
    lines = [
        "# 拓展分析阶段三链路模板证据实验",
        "",
        f"- Cases: {metrics.get('case_count')}",
        f"- Prepared: {metrics.get('prepared')}",
        f"- Template cases: {metrics.get('template_cases')}",
        f"- Candidate-only cases: {metrics.get('candidate_only')}",
        "",
        "## Coverage",
        "",
        "| Group | Cases | Prepared | Candidate-only | Template archetypes |",
        "|---|---:|---:|---:|---|",
    ]
    for row in metrics.get("groups") or []:
        lines.append(f"| `{row.get('group')}` | {row.get('case_count')} | {row.get('prepared')} | {row.get('candidate_only')} | {', '.join(row.get('template_archetypes') or [])} |")
    lines.extend(["", "## Validation Gaps", ""])
    for gap in metrics.get("validation_gaps") or []:
        lines.append(f"- {gap}")
    if not metrics.get("validation_gaps"):
        lines.append("- None recorded.")
    lines.append("")
    return "\n".join(lines)


def report_html(metrics: dict[str, Any]) -> str:
    rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(str(row.get("group"))),
            row.get("case_count"),
            row.get("prepared"),
            row.get("candidate_only"),
            html.escape(", ".join(row.get("template_archetypes") or [])),
        )
        for row in metrics.get("groups") or []
    )
    gaps = "".join(f"<li>{html.escape(str(gap))}</li>" for gap in metrics.get("validation_gaps") or [])
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>拓展分析阶段三链路模板证据实验</title><style>body{{font:14px/1.65 Arial,'Microsoft YaHei',sans-serif;margin:0;color:#172033}}main{{max-width:1120px;margin:auto;padding:28px}}h1{{font-size:26px}}table{{border-collapse:collapse;width:100%;margin:14px 0}}td,th{{border:1px solid #d8dee9;padding:8px;text-align:left}}th{{background:#edf3f7}}.metric{{display:inline-block;margin:6px 12px 6px 0;padding:10px 14px;border:1px solid #d8dee9;background:#f8fafc;border-radius:6px}}.metric b{{display:block;font-size:22px;color:#255f85}}</style></head><body><main><h1>拓展分析阶段三链路模板证据实验</h1><div class="metric"><b>{metrics.get('prepared')}</b>prepared</div><div class="metric"><b>{metrics.get('template_cases')}</b>template cases</div><div class="metric"><b>{metrics.get('candidate_only')}</b>candidate-only cases</div><h2>Coverage</h2><table><tr><th>Group</th><th>Cases</th><th>Prepared</th><th>Candidate-only</th><th>Template archetypes</th></tr>{rows}</table><h2>Validation Gaps</h2><ul>{gaps or '<li>None recorded.</li>'}</ul></main></body></html>"""


def case_from_display(cases: tuple[TriCase, ...], text: str) -> TriCase:
    for case in cases:
        if case.case_id in text or (case.archetype_id and case.archetype_id in text):
            return case
    raise ValueError(f"cannot infer tri-chain case from display_name: {text}")


def upload_files(root: Path) -> list[tuple[str, bytes]]:
    return [(path.name, path.read_bytes()) for path in sorted(root.iterdir()) if path.is_file()]


def expected_ok(root: Path, case: TriCase) -> bool:
    return all((root / "expected_outputs" / name).is_file() and (root / "expected_outputs" / name).stat().st_size > 0 for name in case.required_outputs)


def row_success(row: dict[str, Any]) -> bool:
    return all(bool(row.get(key)) for key in ("raw_code_ok", "expected_output_ok", "build_success", "trial_success", "confirm_success", "validate_success", "publish_success")) and row.get("golden_status") == "passed" and row.get("template_gate_status") == "passed"


def clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def copy_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        return
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)


def file_set(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row}) if rows else ["empty"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Tri-chain Extension Batch",
        "",
        f"Converter: `{summary.get('converter')}`",
        f"Cases: {summary.get('case_count')} passed={summary.get('passed')} failed={summary.get('failed')}",
        "",
        "| Case | Build | Trial | Validate | Golden | Gate | Publish | Error |",
        "|---|---:|---:|---:|---|---|---:|---|",
    ]
    for row in summary.get("rows") or []:
        error = str(row.get("error") or "")[:120].replace("|", "/")
        lines.append(f"| `{row.get('case_id')}` | {row.get('build_success')} | {row.get('trial_success')} | {row.get('validate_success')} | `{row.get('golden_status')}` | `{row.get('template_gate_status')}` | {row.get('publish_success')} | {error} |")
    return "\n".join(lines) + "\n"


def case_readme(case: TriCase, copied: list[dict[str, Any]]) -> str:
    lines = [
        f"# {case.case_id}",
        "",
        f"- Group: `{case.group}`",
        f"- Stage: `{case.stage}`",
        f"- Archetype: `{case.archetype_id or 'candidate_only'}`",
        f"- Split: `{case.split}`",
        f"- Candidate only: `{case.candidate_only}`",
        "",
        case.description,
        "",
        "## Source Files",
        "",
    ]
    for item in copied:
        rows = f", subset rows={item['copied_rows']}" if item.get("row_limit") else ""
        lines.append(f"- `{item['source']}` -> `raw_test_data/{item['dest']}` ({item['dest_size']} bytes{rows})")
    if case.validation_gap:
        lines.extend(["", "## Validation Gap", "", case.validation_gap])
    lines.append("")
    return "\n".join(lines)


def _looks_tabular(path: Path) -> bool:
    return path.suffix.lower() in {".txt", ".tsv", ".csv", ".tab"}


def out_type(name: str) -> str:
    lower = name.lower()
    if lower.endswith((".tsv", ".csv", ".txt", ".tab")):
        return "table"
    if lower.endswith((".gff", ".gff3", ".bed")):
        return "annotation"
    if lower.endswith(".nwk"):
        return "tree"
    return "file"


def write_manifest(output_root: Path) -> None:
    files = []
    for path in sorted(item for item in output_root.rglob("*") if item.is_file() and item.name != "MANIFEST.json"):
        files.append({"path": path.relative_to(output_root).as_posix(), "size": path.stat().st_size, "sha256": sha256(path)})
    write_json(output_root / "MANIFEST.json", {
        "schema_version": "extension_tri_chain_experiment_manifest_v1",
        "created_at": now(),
        "root": str(output_root),
        "file_count": len(files),
        "files": files,
    })


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
