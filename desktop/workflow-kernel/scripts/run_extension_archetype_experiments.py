#!/usr/bin/env python3
"""Prepare and run eight extension-stage archetype experiments.

The experiments exercise the extension-stage builder exactly as a user upload:
raw analysis code plus test data are submitted to ExtensionStageManager, then
trial-run, confirm, validate, and publish gates are executed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import traceback
import string
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = PROJECT_ROOT / "temp" / "template_gate_validation_20260615_impl_abs" / "template_chain_20260615_222814"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "项目记录" / "拓展分析阶段_8类构建实验包_20260615"


@dataclass(frozen=True)
class ExperimentSpec:
    index: int
    archetype_id: str
    stage: str
    script_name: str
    script_content: str
    required_outputs: tuple[str, ...]
    description: str

    @property
    def dir_name(self) -> str:
        return f"{self.index:02d}_{self.archetype_id}"


EXPERIMENTS: tuple[ExperimentSpec, ...] = (
    ExperimentSpec(
        1,
        "sequence_extraction",
        "identify",
        "run_sequence_extraction.sh",
        """#!/usr/bin/env bash
set -euo pipefail
# bbav5 extension archetype: sequence_extraction
# Conversion hint: archetype_id=sequence_extraction; input_roles=input_pep_fasta,input_id_file.
# Input: input.fa and ids.id. Output: extracted.fa.
awk 'BEGIN{while((getline<"ids.id")>0){ids[$1]=1}} /^>/{id=substr($0,2); sub(/[ \\t].*/,"",id); keep=(id in ids)} keep' input.fa > extracted.fa
cp ids.id extracted.id
""",
        ("extracted.fa", "extracted.id"),
        "Extract FASTA records by a target ID list.",
    ),
    ExperimentSpec(
        2,
        "hmmer_domain",
        "domain",
        "run_hmmer_domain.sh",
        """#!/usr/bin/env bash
set -euo pipefail
# bbav5 extension archetype: hmmer_domain
# Conversion hint: archetype_id=hmmer_domain; input_roles=input_fasta,input_file; never use input.fa or input.domtblout as role keys.
# Input: input.domtblout and input.fa. Output: filtered.domtblout.
awk '$1 !~ /^#/ {print}' input.domtblout > filtered.domtblout
awk 'BEGIN{n=0} /^>/{n++} END{print "sequence_count\\t" n}' input.fa > domain_input_summary.tsv
""",
        ("filtered.domtblout", "domain_input_summary.tsv"),
        "Filter HMMER domtblout records and summarize the FASTA input.",
    ),
    ExperimentSpec(
        3,
        "motif_meme",
        "motif",
        "run_motif_meme.sh",
        """#!/usr/bin/env bash
set -euo pipefail
# bbav5 extension archetype: motif_meme
# Conversion hint: archetype_id=motif_meme; input_roles=input_fasta.
# Input: MEME XML/TXT output files. Output: copied MEME result files.
cp meme.xml copied.meme.xml
cp meme.txt copied.meme.txt
awk 'BEGIN{n=0} /^>/{n++} END{print "sequence_count\\t" n}' input.fa > motif_input_summary.tsv
""",
        ("copied.meme.xml", "copied.meme.txt", "motif_input_summary.tsv"),
        "Preserve MEME motif XML/TXT outputs as a directory-style motif result.",
    ),
    ExperimentSpec(
        4,
        "phylogeny_tree",
        "phylogeny",
        "run_phylogeny_tree.sh",
        """#!/usr/bin/env bash
set -euo pipefail
# bbav5 extension archetype: phylogeny_tree
# Conversion hint: archetype_id=phylogeny_tree; input_roles=input_fasta.
# Input: input.msa.fa and tree.nwk. Output: output MSA and Newick tree.
cp input.msa.fa output.msa.fa
cp tree.nwk output.nwk
awk 'BEGIN{n=0} /^>/{n++} END{print "alignment_sequence_count\\t" n}' input.msa.fa > phylogeny_summary.tsv
""",
        ("output.msa.fa", "output.nwk", "phylogeny_summary.tsv"),
        "Preserve multiple sequence alignment and Newick tree outputs.",
    ),
    ExperimentSpec(
        5,
        "reference_classification",
        "reference_classification",
        "run_reference_classification.sh",
        """#!/usr/bin/env bash
set -euo pipefail
# bbav5 extension archetype: reference_classification
# Conversion hint: archetype_id=reference_classification; input_roles=input_file.
# Input: assignment/confidence/subfamily tables. Output: normalized tables.
cp case.assignment.tsv output.assignment.tsv
cp case.subfamily.tsv output.subfamily.tsv
if [ -f case.confidence.tsv ]; then
  cp case.confidence.tsv output.confidence.tsv
fi
awk 'END{print "assignment_rows\\t" NR}' case.assignment.tsv > reference_classification_summary.tsv
""",
        ("output.assignment.tsv", "output.subfamily.tsv", "reference_classification_summary.tsv"),
        "Preserve target/reference subfamily assignment tables.",
    ),
    ExperimentSpec(
        6,
        "synteny_collinearity",
        "interspecies_synteny",
        "run_synteny_collinearity.sh",
        """#!/usr/bin/env bash
set -euo pipefail
# bbav5 extension archetype: synteny_collinearity
# Conversion hint: archetype_id=synteny_collinearity; input_roles=input_query_fasta,input_reference_fasta,input_file.
# Input: query/reference FASTA plus BLAST and collinearity files.
cp links.blast output.blast
cp links.collinearity output.collinearity
awk 'BEGIN{q=0} /^>/{q++} END{print "query_sequence_count\\t" q}' query.fa > synteny_summary.tsv
awk 'BEGIN{r=0} /^>/{r++} END{print "reference_sequence_count\\t" r}' reference.fa >> synteny_summary.tsv
""",
        ("output.blast", "output.collinearity", "synteny_summary.tsv"),
        "Preserve BLAST outfmt6 and collinearity evidence with query/reference roles.",
    ),
    ExperimentSpec(
        7,
        "kaks_pairwise",
        "kaks",
        "run_kaks_pairwise.sh",
        """#!/usr/bin/env bash
set -euo pipefail
# bbav5 extension archetype: kaks_pairwise
# Conversion hint: archetype_id=kaks_pairwise; input_roles=input_file.
# Input: kaks_results.csv. Output: normalized Ka/Ks result CSV.
cp kaks_results.csv output.kaks_results.csv
awk 'END{print "kaks_rows," NR}' kaks_results.csv > kaks_summary.csv
""",
        ("output.kaks_results.csv", "kaks_summary.csv"),
        "Preserve pairwise Ka/Ks result tables.",
    ),
    ExperimentSpec(
        8,
        "pep_properties",
        "pep",
        "run_pep_properties.sh",
        """#!/usr/bin/env bash
set -euo pipefail
# bbav5 extension archetype: pep_properties
# Conversion hint: archetype_id=pep_properties; input_roles=input_fasta.
# Input: protein FASTA and properties/subcellular tables.
cp properties.tsv output.properties.tsv
if [ -f subcellular.tsv ]; then
  cp subcellular.tsv output.subcellular.tsv
fi
awk 'BEGIN{n=0} /^>/{n++} END{print "protein_count\\t" n}' input.fa > pep_properties_summary.tsv
""",
        ("output.properties.tsv", "pep_properties_summary.tsv"),
        "Preserve protein property and subcellular localization result tables.",
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="prepare uploadable experiment packages")
    parser.add_argument("--run", action="store_true", help="run the extension-stage build/validation batch")
    parser.add_argument("--all", action="store_true", help="prepare and run")
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--converter", default="deepseek_api", choices=["deepseek_api", "deepseek_agent", "local_rule", "rule", "archetype_spec"])
    parser.add_argument("--runtime-env", default="test4py311")
    parser.add_argument("--stop-on-failure", action="store_true")
    args = parser.parse_args()

    if not args.prepare and not args.run and not args.all:
        args.all = True

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    _write_json(output_root / "experiment_config.json", {
        "created_at": _now(),
        "source_root": str(args.source_root.resolve()),
        "output_root": str(output_root),
        "converter": args.converter,
        "runtime_env": args.runtime_env,
        "experiments": [spec.__dict__ for spec in EXPERIMENTS],
    })

    if args.prepare or args.all:
        prepare_experiments(source_root=args.source_root.resolve(), output_root=output_root)
    if args.run or args.all:
        run_experiments(output_root=output_root, converter=args.converter, runtime_env=args.runtime_env, stop_on_failure=args.stop_on_failure)
    write_manifest(output_root)
    return 0


def prepare_experiments(*, source_root: Path, output_root: Path) -> None:
    if not source_root.exists():
        raise FileNotFoundError(f"Template validation source root not found: {source_root}")
    packages_root = output_root / "packages"
    logs_root = output_root / "logs"
    packages_root.mkdir(parents=True, exist_ok=True)
    logs_root.mkdir(parents=True, exist_ok=True)
    prepare_rows: list[dict[str, Any]] = []

    for spec in EXPERIMENTS:
        case_root = packages_root / spec.dir_name
        raw_code = case_root / "raw_code"
        raw_data = case_root / "raw_test_data"
        expected = case_root / "expected_outputs"
        for path in (raw_code, raw_data, expected):
            _clean_dir(path)

        source_data = source_root / spec.archetype_id / "extension" / "raw_test_data"
        if not source_data.exists():
            raise FileNotFoundError(f"raw_test_data source not found for {spec.archetype_id}: {source_data}")
        _copy_tree_contents(source_data, raw_data)

        script_path = raw_code / spec.script_name
        script_path.write_text(spec.script_content, encoding="utf-8")
        script_path.chmod(script_path.stat().st_mode | 0o755)
        (raw_code / "bbav5_extension_hint.json").write_text(
            json.dumps(_extension_hint(spec), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        readme = _case_readme(spec)
        (case_root / "README.md").write_text(readme, encoding="utf-8")

        raw_result = run_raw_code(case_root, spec, logs_root / f"{spec.dir_name}.raw.log")
        expected_ok = all((expected / name).is_file() and (expected / name).stat().st_size > 0 for name in spec.required_outputs)
        row = {
            "archetype_id": spec.archetype_id,
            "stage": spec.stage,
            "case_dir": str(case_root),
            "raw_code_ok": raw_result["success"],
            "expected_output_ok": expected_ok,
            "required_outputs": list(spec.required_outputs),
            "error": raw_result.get("error", ""),
        }
        prepare_rows.append(row)
        _write_json(case_root / "prepare_record.json", row)

    _write_json(output_root / "prepare_manifest.json", {
        "schema_version": "extension_archetype_prepare_v1",
        "created_at": _now(),
        "source_root": str(source_root),
        "package_root": str(packages_root),
        "case_count": len(prepare_rows),
        "prepared": sum(1 for row in prepare_rows if row["raw_code_ok"] and row["expected_output_ok"]),
        "cases": prepare_rows,
    })


def run_raw_code(case_root: Path, spec: ExperimentSpec, log_path: Path) -> dict[str, Any]:
    work = case_root / "_raw_run_work"
    expected = case_root / "expected_outputs"
    _clean_dir(work)
    _clean_dir(expected)
    _copy_tree_contents(case_root / "raw_test_data", work)
    _copy_tree_contents(case_root / "raw_code", work)
    before = _file_set(work)
    proc = subprocess.run(
        ["bash", spec.script_name],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=120,
    )
    log_path.write_text(
        f"$ bash {spec.script_name}\n\n[stdout]\n{proc.stdout}\n\n[stderr]\n{proc.stderr}\n",
        encoding="utf-8",
    )
    after = _file_set(work)
    new_files = sorted(after - before)
    for rel in new_files:
        src = work / rel
        if src.is_file():
            dest = expected / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    return {
        "success": proc.returncode == 0,
        "returncode": proc.returncode,
        "new_files": new_files,
        "error": "" if proc.returncode == 0 else (proc.stderr or proc.stdout)[-2000:],
    }


def run_experiments(*, output_root: Path, converter: str, runtime_env: str, stop_on_failure: bool) -> None:
    sys.path.insert(0, str(PROJECT_ROOT))
    from gfa_core.extension_stage import ExtensionStageAgent, ExtensionStageError, ExtensionStageManager

    effective_converter = "deepseek_api" if converter in {"archetype_spec", "deepseek_agent"} else converter
    os.environ["BBAV5_EXTENSION_CONVERTER"] = effective_converter
    if converter == "archetype_spec":
        import gfa_core.extension_stage.agent as agent_module

        def _convert_with_archetype_spec(*, display_name: str, raw_code: Path, raw_data: Path, runtime_env: str, lessons: list[dict[str, Any]] | None = None) -> dict[str, Any]:
            return _local_conversion_spec(display_name=display_name, raw_code=raw_code, raw_data=raw_data, runtime_env=runtime_env)

        agent_module.convert_with_deepseek_api = _convert_with_archetype_spec
        agent_module.repair_build_with_deepseek_api = lambda **kwargs: _convert_with_archetype_spec(
            display_name=str(kwargs.get("display_name") or ""),
            raw_code=Path(kwargs.get("raw_code")),
            raw_data=Path(kwargs.get("raw_data")),
            runtime_env=str(kwargs.get("runtime_env") or runtime_env),
            lessons=kwargs.get("lessons") or [],
        )

    _clean_dir(output_root / "runtime_extension_stages")
    _clean_dir(output_root / "runtime_workspace")
    _clean_dir(output_root / "runtime_input")
    _clean_dir(output_root / "results")
    manager = ExtensionStageManager(
        project_root=PROJECT_ROOT,
        store_root=output_root / "runtime_extension_stages",
        workspace_root=output_root / "runtime_workspace",
        input_root=output_root / "runtime_input",
    )
    agent = ExtensionStageAgent(manager)
    results_root = output_root / "results"
    per_case_root = results_root / "per_archetype"
    logs_root = output_root / "logs"
    per_case_root.mkdir(parents=True, exist_ok=True)
    logs_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for spec in EXPERIMENTS:
        row = _run_one_experiment(
            manager=agent,
            output_root=output_root,
            spec=spec,
            runtime_env=runtime_env,
            per_case_root=per_case_root,
            logs_root=logs_root,
        )
        rows.append(row)
        if stop_on_failure and not _row_success(row):
            break

    summary = {
        "schema_version": "extension_archetype_batch_result_v1",
        "created_at": _now(),
        "converter": converter,
        "runtime_env": runtime_env,
        "case_count": len(rows),
        "passed": sum(1 for row in rows if _row_success(row)),
        "failed": sum(1 for row in rows if not _row_success(row)),
        "rows": rows,
    }
    _write_json(results_root / "summary.json", summary)
    _write_summary_csv(results_root / "summary.csv", rows)
    (results_root / "summary.md").write_text(_summary_markdown(summary), encoding="utf-8")


def _run_one_experiment(
    *,
    manager: Any,
    output_root: Path,
    spec: ExperimentSpec,
    runtime_env: str,
    per_case_root: Path,
    logs_root: Path,
) -> dict[str, Any]:
    case_root = output_root / "packages" / spec.dir_name
    case_result_dir = per_case_root / spec.dir_name
    _clean_dir(case_result_dir)
    log_path = logs_root / f"{spec.dir_name}.batch.log"
    log_lines: list[str] = []
    row: dict[str, Any] = {
        "archetype_id": spec.archetype_id,
        "display_name": f"archetype_{spec.archetype_id}",
        "raw_code_ok": bool((case_root / "prepare_record.json").exists()),
        "expected_output_ok": _expected_outputs_ok(case_root, spec),
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
        code_files = _upload_files(case_root / "raw_code")
        data_files = _upload_files(case_root / "raw_test_data")
        log_lines.append(f"[{_now()}] build {spec.archetype_id}")
        build = manager.create_from_uploads(
            display_name=f"archetype_{spec.archetype_id}",
            command=f"bash {spec.script_name}",
            runtime_env=runtime_env,
            code_files=code_files,
            data_files=data_files,
            description=f"Batch experiment for {spec.archetype_id}: {spec.description}",
        )
        _write_json(case_result_dir / "build_response.json", build)
        extension_id = str((build.get("extension") or {}).get("extension_id") or "")
        row["extension_id"] = extension_id
        row["build_success"] = bool(build.get("success")) and bool(extension_id)
        log_lines.append(f"[{_now()}] trial-run {extension_id}")
        trial = manager.trial_run(extension_id)
        _write_json(case_result_dir / "trial_run.json", trial)
        trial_run = trial.get("run") or {}
        row["trial_success"] = bool(trial.get("success"))
        row["output_count"] = len(trial_run.get("outputs") or [])

        log_lines.append(f"[{_now()}] confirm {extension_id}")
        outputs = (trial.get("extension") or {}).get("outputs_detected") or trial_run.get("outputs") or []
        confirm = manager.confirm(extension_id, outputs=outputs, nl_triggers=[spec.archetype_id, spec.stage])
        _write_json(case_result_dir / "confirm_response.json", confirm)
        row["confirm_success"] = bool(confirm.get("success"))

        log_lines.append(f"[{_now()}] validate {extension_id}")
        validate = manager.validate(extension_id)
        _write_json(case_result_dir / "validate_response.json", validate)
        validation_run = validate.get("run") or {}
        row["validate_success"] = bool(validate.get("success"))
        row["golden_status"] = str((validation_run.get("golden_comparison") or {}).get("status") or "")
        row["template_gate_status"] = str((validation_run.get("template_gate") or {}).get("status") or "")

        log_lines.append(f"[{_now()}] publish {extension_id}")
        publish = manager.publish(extension_id)
        _write_json(case_result_dir / "publish_response.json", publish)
        row["publish_success"] = bool(publish.get("success"))
    except Exception as exc:
        row["error"] = str(exc)
        _write_json(case_result_dir / "error.json", {
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "row": row,
        })
        log_lines.append(f"[{_now()}] ERROR {type(exc).__name__}: {exc}")
        if isinstance(exc, KeyboardInterrupt):
            raise
    finally:
        log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
        _write_json(case_result_dir / "result_row.json", row)
    return row


def _extension_hint(spec: ExperimentSpec) -> dict[str, Any]:
    common = {
        "schema_version": "bbav5_extension_upload_hint_v1",
        "archetype_id": spec.archetype_id,
        "representative_stage": spec.stage,
        "recommended_original_command": f"bash {spec.script_name}",
        "avoid_semantic_command_template": True,
        "notes": "Use only allowed bbav5 placeholders for input_roles. Do not use raw filenames such as input.fa or links.blast as input role keys.",
    }
    by_archetype: dict[str, dict[str, Any]] = {
        "sequence_extraction": {
            "analysis_type": "sequence_extraction",
            "tool_family": "seqtk",
            "input_roles": {
                "input_pep_fasta": {"role": "target_pep_fasta", "required": True, "resolve_by": "uploaded_test_data_validation_only"},
                "input_id_file": {"role": "target_id_list", "required": True, "resolve_by": "uploaded_test_data_validation_only"},
            },
            "output_contract": [
                {"name_pattern": "extracted.fa", "type": "fasta", "primary": True, "required": True, "allow_empty": False, "semantic_role": "extracted_fasta", "compare_mode": "fasta", "required_for_golden": True},
                {"name_pattern": "extracted.id", "type": "text", "primary": False, "required": False, "allow_empty": False, "semantic_role": "id_list", "compare_mode": "record_set", "required_for_golden": False},
            ],
        },
        "hmmer_domain": {
            "analysis_type": "hmmer_search",
            "tool_family": "hmmer",
            "input_roles": {
                "input_fasta": {"role": "target_pep_fasta", "required": True, "resolve_by": "uploaded_test_data_validation_only"},
                "input_file": {"role": "generic_input_file", "required": True, "resolve_by": "uploaded_test_data_validation_only"},
            },
            "output_contract": [
                {"name_pattern": "filtered.domtblout", "type": "text", "primary": True, "required": True, "allow_empty": False, "semantic_role": "hmmer_domtblout", "compare_mode": "normalized_text", "required_for_golden": True},
            ],
        },
        "motif_meme": {
            "analysis_type": "generic_cli",
            "tool_family": "generic",
            "input_roles": {"input_fasta": {"role": "target_pep_fasta", "required": True, "resolve_by": "uploaded_test_data_validation_only"}},
            "output_contract": [
                {"name_pattern": "copied.meme.xml", "type": "xml", "primary": True, "required": True, "allow_empty": False, "semantic_role": "meme_xml", "compare_mode": "normalized_text", "required_for_golden": True},
                {"name_pattern": "copied.meme.txt", "type": "text", "primary": False, "required": True, "allow_empty": False, "semantic_role": "meme_txt", "compare_mode": "normalized_text", "required_for_golden": True},
            ],
        },
        "phylogeny_tree": {
            "analysis_type": "generic_cli",
            "tool_family": "generic",
            "input_roles": {"input_fasta": {"role": "target_pep_fasta", "required": True, "resolve_by": "uploaded_test_data_validation_only"}},
            "output_contract": [
                {"name_pattern": "output.msa.fa", "type": "fasta", "primary": True, "required": True, "allow_empty": False, "semantic_role": "multiple_sequence_alignment", "compare_mode": "fasta", "required_for_golden": True},
                {"name_pattern": "output.nwk", "type": "text", "primary": False, "required": True, "allow_empty": False, "semantic_role": "newick_tree", "compare_mode": "newick", "required_for_golden": True},
            ],
        },
        "reference_classification": {
            "analysis_type": "table_filter",
            "tool_family": "python",
            "input_roles": {"input_file": {"role": "generic_input_file", "required": True, "resolve_by": "uploaded_test_data_validation_only"}},
            "output_contract": [
                {"name_pattern": "output.assignment.tsv", "type": "table", "primary": True, "required": True, "allow_empty": False, "semantic_role": "assignment_table", "compare_mode": "table", "required_for_golden": True},
                {"name_pattern": "output.subfamily.tsv", "type": "table", "primary": False, "required": True, "allow_empty": False, "semantic_role": "subfamily_table", "compare_mode": "table", "required_for_golden": True},
            ],
        },
        "synteny_collinearity": {
            "analysis_type": "blast_search",
            "tool_family": "blast",
            "input_roles": {
                "input_query_fasta": {"role": "target_pep_fasta", "required": True, "resolve_by": "uploaded_test_data_validation_only"},
                "input_reference_fasta": {"role": "reference_pep_fasta", "required": True, "resolve_by": "uploaded_test_data_validation_only"},
                "input_file": {"role": "generic_input_file", "required": False, "resolve_by": "uploaded_test_data_validation_only"},
            },
            "output_contract": [
                {"name_pattern": "output.blast", "type": "blast", "primary": True, "required": True, "allow_empty": False, "semantic_role": "blast_outfmt6", "compare_mode": "blast_outfmt6", "required_for_golden": True},
                {"name_pattern": "output.collinearity", "type": "text", "primary": False, "required": False, "allow_empty": False, "semantic_role": "collinearity", "compare_mode": "normalized_text", "required_for_golden": False},
            ],
        },
        "kaks_pairwise": {
            "analysis_type": "generic_cli",
            "tool_family": "generic",
            "input_roles": {"input_file": {"role": "generic_input_file", "required": True, "resolve_by": "uploaded_test_data_validation_only"}},
            "output_contract": [
                {"name_pattern": "output.kaks_results.csv", "type": "table", "primary": True, "required": True, "allow_empty": False, "semantic_role": "kaks_results", "compare_mode": "table", "required_for_golden": True},
            ],
        },
        "pep_properties": {
            "analysis_type": "enrichment_or_stats",
            "tool_family": "python",
            "input_roles": {"input_fasta": {"role": "target_pep_fasta", "required": True, "resolve_by": "uploaded_test_data_validation_only"}},
            "output_contract": [
                {"name_pattern": "output.properties.tsv", "type": "table", "primary": True, "required": True, "allow_empty": False, "semantic_role": "protein_properties", "compare_mode": "table", "required_for_golden": True},
                {"name_pattern": "output.subcellular.tsv", "type": "table", "primary": False, "required": False, "allow_empty": False, "semantic_role": "subcellular_table", "compare_mode": "table", "required_for_golden": False},
            ],
        },
    }
    detail = by_archetype[spec.archetype_id]
    return {**common, **detail}


def _local_conversion_spec(*, display_name: str, raw_code: Path, raw_data: Path, runtime_env: str) -> dict[str, Any]:
    spec = _spec_from_display_name(display_name)
    hint = _extension_hint(spec)
    role_keys = list((hint.get("input_roles") or {}).keys())
    command_parts = [f"bash {{code_dir}}/{spec.script_name}"]
    for key in role_keys:
        command_parts.append(f"--{key.replace('_', '-')} {{{key}}}")
    command_parts.append("--output-dir {output_dir}")
    return {
        "template_type": "archetype_spec",
        "archetype_id": spec.archetype_id,
        "original_command": f"bash {spec.script_name}",
        "command_template": " ".join(command_parts),
        "semantic_command_template": "",
        "wrapper_command_template": " ".join(command_parts),
        "analysis_type": hint.get("analysis_type"),
        "tool_family": hint.get("tool_family"),
        "input_roles": hint.get("input_roles") or {},
        "inputs": {key: {"required": bool(value.get("required", True))} for key, value in (hint.get("input_roles") or {}).items()},
        "parameters": {},
        "outputs": {"directory": "outputs", "patterns": [item.get("name_pattern") for item in hint.get("output_contract") or [] if item.get("name_pattern")]},
        "output_contract": hint.get("output_contract") or [],
        "comparator_plan": {
            "mode": "pipeline_golden_diff",
            "default_compare": "structured",
            "steps": [
                {
                    "name_pattern": item.get("name_pattern"),
                    "semantic_role": item.get("semantic_role", ""),
                    "compare_mode": item.get("compare_mode", "auto"),
                    "required": bool(item.get("required", True)),
                }
                for item in hint.get("output_contract") or []
                if item.get("name_pattern")
            ],
        },
        "requires_user_confirmation": False,
        "notes": "Batch archetype_spec converter used for reproducible extension-stage archetype experiment.",
        "converter": {"type": "archetype_spec", "mode": "batch_reproducible", "runtime_env": runtime_env},
    }


def _spec_from_display_name(display_name: str) -> ExperimentSpec:
    text = str(display_name or "")
    for spec in EXPERIMENTS:
        if spec.archetype_id in text:
            return spec
    raise ValueError(f"Cannot infer archetype from display_name: {display_name}")


def _sanitize_unsafe_semantic_template(manager: Any, extension_id: str) -> dict[str, Any]:
    """Drop semantic templates that contain unescaped shell/awk braces.

    This experiment validates original uploaded code against the deterministic
    wrapper. LLM semantic sketches are not the experimental endpoint, and an
    unescaped shell/awk brace should not make wrapper validation fail.
    """
    extension = manager.load(extension_id)
    semantic = str(extension.get("semantic_command_template") or (extension.get("entrypoint") or {}).get("semantic_command_template") or "")
    if not semantic:
        return {}
    converter_type = str((extension.get("converter") or {}).get("type") or "")
    if converter_type == "archetype_spec":
        reason = "archetype_spec validates original behavior against deterministic wrapper only"
    else:
        reason = _semantic_template_problem(semantic)
    if not reason:
        return {}
    entrypoint = dict(extension.get("entrypoint") or {})
    entrypoint["semantic_command_template"] = ""
    extension["entrypoint"] = entrypoint
    extension["semantic_command_template"] = ""
    notes = str(extension.get("conversion_notes") or "").strip()
    suffix = f" Batch experiment disabled unsafe semantic_command_template before golden validation: {reason}."
    extension["conversion_notes"] = (notes + suffix).strip()
    extension.setdefault("batch_experiment_adjustments", []).append({
        "adjustment": "disabled_semantic_command_template",
        "reason": reason,
        "template_preview": semantic[:500],
        "applied_at": _now(),
    })
    root = manager._extension_root(extension_id)
    manager._write_extension(root, extension)
    return extension["batch_experiment_adjustments"][-1]


def _semantic_template_problem(template: str) -> str:
    allowed = {
        "code_dir",
        "input_dir",
        "input_file",
        "input_hmm",
        "input_fasta",
        "input_query_fasta",
        "input_reference_fasta",
        "input_pep_fasta",
        "input_cds_fasta",
        "input_id_file",
        "output_dir",
        "work_dir",
    }
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as exc:
        return f"format parse failed: {exc}"
    bad_fields = sorted(
        str(field)
        for _, field, _, _ in parsed
        if field and field not in allowed and not str(field).startswith("param_")
    )
    if bad_fields:
        return "unsupported format field(s): " + ", ".join(bad_fields)
    return ""


def _case_readme(spec: ExperimentSpec) -> str:
    outputs = "\n".join(f"- `{name}`" for name in spec.required_outputs)
    return f"""# {spec.archetype_id}

Representative stage: `{spec.stage}`

Purpose: {spec.description}

Upload to the extension-stage builder:

- Code directory: `raw_code/`
- Test data directory: `raw_test_data/`

Expected outputs from the original code:

{outputs}

This package is intended for the extension-stage archetype coverage experiment.
It validates whether user-style code and data can be converted into a reusable
bbav5 extension stage and pass trial-run, golden comparison, template gate, and
publish gate checks.
"""


def _upload_files(root: Path) -> list[tuple[str, bytes]]:
    files = []
    for path in sorted(root.iterdir()):
        if not path.is_file():
            continue
        if path.name == "bbav5_extension_hint.json":
            continue
        files.append((path.name, path.read_bytes()))
    return files


def _expected_outputs_ok(case_root: Path, spec: ExperimentSpec) -> bool:
    expected = case_root / "expected_outputs"
    return all((expected / name).is_file() and (expected / name).stat().st_size > 0 for name in spec.required_outputs)


def _row_success(row: dict[str, Any]) -> bool:
    return all(
        bool(row.get(key))
        for key in (
            "raw_code_ok",
            "expected_output_ok",
            "build_success",
            "trial_success",
            "confirm_success",
            "validate_success",
            "publish_success",
        )
    ) and row.get("golden_status") == "passed" and row.get("template_gate_status") == "passed"


def write_manifest(output_root: Path) -> None:
    files = []
    for path in sorted(p for p in output_root.rglob("*") if p.is_file()):
        if path.name == "MANIFEST.json":
            continue
        rel = path.relative_to(output_root).as_posix()
        files.append({
            "path": rel,
            "size": path.stat().st_size,
            "sha256": _sha256(path),
            "role": _manifest_role(rel),
        })
    _write_json(output_root / "MANIFEST.json", {
        "schema_version": "extension_archetype_experiment_manifest_v1",
        "created_at": _now(),
        "root": str(output_root),
        "file_count": len(files),
        "files": files,
    })


def _manifest_role(rel: str) -> str:
    if "/raw_code/" in rel:
        return "raw_user_code"
    if "/raw_test_data/" in rel:
        return "raw_test_data"
    if "/expected_outputs/" in rel:
        return "expected_output"
    if rel.startswith("results/"):
        return "result_record"
    if rel.startswith("logs/"):
        return "execution_log"
    if "extension.json" in rel:
        return "extension_metadata"
    return "supporting_file"


def _write_summary_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "archetype_id",
        "display_name",
        "raw_code_ok",
        "expected_output_ok",
        "build_success",
        "extension_id",
        "trial_success",
        "confirm_success",
        "validate_success",
        "golden_status",
        "template_gate_status",
        "publish_success",
        "output_count",
        "error",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Extension Archetype Batch Experiments",
        "",
        f"Created at: `{summary.get('created_at')}`",
        f"Converter: `{summary.get('converter')}`",
        f"Cases: `{summary.get('case_count')}` passed=`{summary.get('passed')}` failed=`{summary.get('failed')}`",
        "",
        "| Archetype | Build | Trial | Validate | Golden | Template gate | Publish | Error |",
        "|---|---:|---:|---:|---|---|---:|---|",
    ]
    for row in summary.get("rows") or []:
        lines.append(
            "| `{archetype}` | {build} | {trial} | {validate} | `{golden}` | `{gate}` | {publish} | {error} |".format(
                archetype=row.get("archetype_id", ""),
                build=_yes(row.get("build_success")),
                trial=_yes(row.get("trial_success")),
                validate=_yes(row.get("validate_success")),
                golden=row.get("golden_status", ""),
                gate=row.get("template_gate_status", ""),
                publish=_yes(row.get("publish_success")),
                error=str(row.get("error") or "").replace("|", "/")[:160],
            )
        )
    lines.append("")
    return "\n".join(lines)


def _yes(value: Any) -> str:
    return "yes" if bool(value) else "no"


def _copy_tree_contents(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in sorted(src.iterdir()):
        target = dest / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target)
        elif item.is_file():
            shutil.copy2(item, target)


def _clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _file_set(root: Path) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob("*") if path.is_file()}


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
