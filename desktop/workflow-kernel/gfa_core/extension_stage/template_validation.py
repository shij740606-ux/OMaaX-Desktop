"""Executable validation for reusable extension template cases.

The template-case bundle is evidence mined from completed bbav5 jobs.  This
module turns those cases into small fixture extensions using real completed
artifacts, then runs the same deterministic wrapper and golden comparison gates
used by API-converted extensions.  The goal is to validate the shared template
machinery without writing one-off validators for every stage.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from gfa_core.extension_stage.code_artifacts import save_code_artifacts
from gfa_core.extension_stage.golden_runner import run_golden_validation
from gfa_core.extension_stage.schema_validator import validate_conversion_spec
from gfa_core.extension_stage.template_cases import ARCHETYPE_BY_ID, case_to_candidate_spec
from gfa_core.extension_stage.wrapper_builder import build_deterministic_wrapper_spec


class TemplateValidationError(RuntimeError):
    """Raised when a template case cannot be materialized for validation."""


def validate_template_bundle(bundle_path: Path, *, output_root: Path, max_cases: int | None = None) -> dict[str, Any]:
    bundle_path = Path(bundle_path)
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    run_root = Path(output_root).resolve() / f"template_chain_{_timestamp()}"
    run_root.mkdir(parents=True, exist_ok=True)
    cases = bundle.get("cases") or []
    if max_cases is not None:
        cases = cases[:max_cases]
    results = []
    for case in cases:
        results.append(validate_template_case(case, run_root=run_root))
    failures = [item for item in results if item.get("status") != "passed"]
    report = {
        "schema_version": "extension_template_chain_validation_v1",
        "status": "passed" if not failures else "failed",
        "bundle_path": str(bundle_path),
        "run_root": str(run_root),
        "case_count": len(results),
        "passed": len(results) - len(failures),
        "failed": len(failures),
        "results": results,
    }
    (run_root / "template_chain_validation.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (run_root / "template_chain_validation.md").write_text(_report_markdown(report), encoding="utf-8")
    return report


def validate_template_case(case: dict[str, Any], *, run_root: Path) -> dict[str, Any]:
    archetype_id = str(case.get("archetype_id") or "")
    case_root = Path(run_root) / _safe_name(archetype_id)
    extension_root = case_root / "extension"
    raw_code = extension_root / "raw_code"
    raw_data = extension_root / "raw_test_data"
    converted_code = extension_root / "converted_code"
    for path in (raw_code, raw_data, converted_code):
        path.mkdir(parents=True, exist_ok=True)
    try:
        fixture = _materialize_fixture(case, raw_data)
        spec = case_to_candidate_spec(_case_object(case))
        spec["original_command"] = fixture["command"]
        spec["output_contract"] = fixture.get("output_contract") or spec.get("output_contract") or []
        spec["comparator_plan"] = fixture.get("comparator_plan") or spec.get("comparator_plan") or {}
        spec = validate_conversion_spec(spec, require_code_artifacts=False)
        spec = build_deterministic_wrapper_spec(spec, raw_code=raw_code, raw_data=raw_data)
        spec = validate_conversion_spec(spec, require_code_artifacts=True)
        artifacts = save_code_artifacts(spec, converted_code)
        extension = _extension_from_spec(case, spec, extension_root, artifacts)
        (extension_root / "extension.json").write_text(json.dumps(extension, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        golden = run_golden_validation(extension, extension_root=extension_root, run_id="template_validation")
        status = "passed" if golden.get("status") == "passed" else "failed"
        return {
            "archetype_id": archetype_id,
            "stage": case.get("stage", ""),
            "status": status,
            "fixture_command": fixture["command"],
            "extension_root": str(extension_root),
            "golden_status": golden.get("status"),
            "golden_workspace": golden.get("workspace", ""),
            "error": golden.get("error", "") if status != "passed" else "",
            "generated_code_artifacts": artifacts,
        }
    except Exception as exc:
        return {
            "archetype_id": archetype_id,
            "stage": case.get("stage", ""),
            "status": "failed",
            "extension_root": str(extension_root),
            "error": str(exc),
        }


def _materialize_fixture(case: dict[str, Any], raw_data: Path) -> dict[str, Any]:
    archetype_id = str(case.get("archetype_id") or "")
    files = [item for item in (case.get("output_files") or []) if item.get("exists")]
    copied: dict[str, str] = {}

    def copy_named(predicate: Any, dest_name: str) -> str:
        for item in files:
            path = Path(str(item.get("full_path") or ""))
            if path.is_file() and predicate(item, path):
                dest = raw_data / dest_name
                shutil.copy2(path, dest)
                copied[dest_name] = str(path)
                return dest_name
        raise TemplateValidationError(f"{archetype_id}: cannot find fixture source for {dest_name}")

    if archetype_id == "sequence_extraction":
        fasta = copy_named(lambda item, p: p.suffix.lower() in {".fa", ".fasta", ".faa"}, "input.fa")
        id_file = copy_named(lambda item, p: p.suffix.lower() in {".id", ".ids"}, "ids.id")
        command = "awk 'BEGIN{while((getline<\"ids.id\")>0){ids[$1]=1}} /^>/{id=substr($0,2); sub(/[ \\t].*/,\"\",id); keep=(id in ids)} keep' input.fa > extracted.fa"
        contract = [{"name_pattern": "extracted.fa", "semantic_role": "extracted_fasta", "compare_mode": "fasta", "required": True}]
    elif archetype_id == "hmmer_domain":
        source = copy_named(lambda item, p: p.name.endswith(".domtblout"), "input.domtblout")
        command = "awk '$1 !~ /^#/ {print}' input.domtblout > filtered.domtblout"
        contract = [{"name_pattern": "filtered.domtblout", "semantic_role": "hmmer_domtblout", "compare_mode": "normalized_text", "required": True}]
    elif archetype_id == "motif_meme":
        copy_named(lambda item, p: p.name.endswith(".xml"), "meme.xml")
        txt = _try_copy(files, raw_data, lambda item, p: p.name.endswith(".txt"), "meme.txt")
        command = "cp meme.xml copied.meme.xml"
        if txt:
            command += " && cp meme.txt copied.meme.txt"
        contract = [{"name_pattern": "copied.meme.xml", "semantic_role": "meme_xml", "compare_mode": "normalized_text", "required": True}]
    elif archetype_id == "phylogeny_tree":
        copy_named(lambda item, p: p.name.endswith(".msa.fa") or p.name.endswith(".mafft.msa.fa") or p.name.endswith("pep.msa.fa"), "input.msa.fa")
        copy_named(lambda item, p: p.name.endswith(".nwk"), "tree.nwk")
        command = "cp input.msa.fa output.msa.fa && cp tree.nwk output.nwk"
        contract = [
            {"name_pattern": "output.msa.fa", "semantic_role": "multiple_sequence_alignment", "compare_mode": "fasta", "required": True},
            {"name_pattern": "output.nwk", "semantic_role": "newick_tree", "compare_mode": "normalized_text", "required": True},
        ]
    elif archetype_id == "reference_classification":
        copy_named(lambda item, p: p.name.endswith("assignment.tsv"), "case.assignment.tsv")
        copy_named(lambda item, p: p.name.endswith("subfamily.tsv"), "case.subfamily.tsv")
        _try_copy(files, raw_data, lambda item, p: p.name.endswith("confidence.tsv"), "case.confidence.tsv")
        command = "cp case.assignment.tsv output.assignment.tsv && cp case.subfamily.tsv output.subfamily.tsv"
        contract = [
            {"name_pattern": "output.assignment.tsv", "semantic_role": "assignment_table", "compare_mode": "normalized_text", "required": True},
            {"name_pattern": "output.subfamily.tsv", "semantic_role": "subfamily_table", "compare_mode": "normalized_text", "required": True},
        ]
    elif archetype_id == "synteny_collinearity":
        copy_named(lambda item, p: p.name.endswith(".blast") or p.name.endswith("blastp.txt"), "links.blast")
        extra = _try_copy(files, raw_data, lambda item, p: p.name.endswith(".collinearity"), "links.collinearity")
        command = "cp links.blast output.blast"
        if extra:
            command += " && cp links.collinearity output.collinearity"
        contract = [{"name_pattern": "output.blast", "semantic_role": "blast_outfmt6", "compare_mode": "normalized_text", "required": True}]
    elif archetype_id == "gene_structure_gff":
        copy_named(lambda item, p: p.name.endswith(".structure.filtered.gff3") or p.name.endswith(".filtered.gff3"), "input.structure.filtered.gff3")
        copy_named(lambda item, p: p.name.endswith(".structure.tsv"), "input.structure.tsv")
        command = "cp input.structure.filtered.gff3 output.structure.filtered.gff3 && cp input.structure.tsv output.structure.tsv"
        contract = [
            {"name_pattern": "output.structure.filtered.gff3", "semantic_role": "gene_structure_gff", "compare_mode": "gff", "required": True},
            {"name_pattern": "output.structure.tsv", "semantic_role": "gene_structure_table", "compare_mode": "normalized_text", "required": True},
        ]
    elif archetype_id == "promoter_cis_element":
        copy_named(lambda item, p: p.name == "plantCARE_output_filtered.tab" or p.name.endswith("cis_elements.tsv"), "cis_elements.tsv")
        _try_copy(files, raw_data, lambda item, p: p.name == "element_counts.txt" or p.name.endswith("element_counts.tsv"), "element_counts.tsv")
        _try_copy(files, raw_data, lambda item, p: p.suffix.lower() == ".bed", "promoter.bed")
        command = "cp cis_elements.tsv output.cis_elements.tsv"
        if (raw_data / "element_counts.tsv").exists():
            command += " && cp element_counts.tsv output.element_counts.tsv"
        else:
            command += " && awk 'BEGIN{FS=OFS=\"\\t\"} NR>1 && NF{count[$2]++} END{print \"element\",\"count\"; for(k in count) print k,count[k]}' cis_elements.tsv > output.element_counts.tsv"
        command += " && awk 'BEGIN{print \"metric\\tvalue\"} NR>1{rows++} END{print \"cis_element_rows\\t\" rows+0}' cis_elements.tsv > output.promoter_summary.tsv"
        if (raw_data / "promoter.bed").exists():
            command += " && cp promoter.bed output.promoter.bed"
        contract = [
            {"name_pattern": "output.cis_elements.tsv", "semantic_role": "cis_element_table", "compare_mode": "normalized_text", "required": True},
            {"name_pattern": "output.element_counts.tsv", "semantic_role": "element_counts", "compare_mode": "normalized_text", "required": True},
            {"name_pattern": "output.promoter_summary.tsv", "semantic_role": "promoter_summary", "compare_mode": "normalized_text", "required": True},
        ]
    elif archetype_id == "kaks_pairwise":
        copy_named(lambda item, p: p.name.endswith("kaks_results.csv"), "kaks_results.csv")
        command = "cp kaks_results.csv output.kaks_results.csv"
        contract = [{"name_pattern": "output.kaks_results.csv", "semantic_role": "kaks_results", "compare_mode": "normalized_text", "required": True}]
    elif archetype_id == "pep_properties":
        copy_named(lambda item, p: p.name.endswith("properties.tsv") or p.name == "protparam_full.tsv", "properties.tsv")
        _try_copy(files, raw_data, lambda item, p: "subcellular" in p.name and p.suffix.lower() == ".tsv", "subcellular.tsv")
        command = "cp properties.tsv output.properties.tsv"
        if (raw_data / "subcellular.tsv").exists():
            command += " && cp subcellular.tsv output.subcellular.tsv"
        contract = [{"name_pattern": "output.properties.tsv", "semantic_role": "protein_properties", "compare_mode": "normalized_text", "required": True}]
    else:
        raise TemplateValidationError(f"unsupported archetype: {archetype_id}")

    _ensure_required_fixture_inputs(archetype_id, raw_data)

    return {
        "command": command,
        "copied_sources": copied,
        "output_contract": contract,
        "comparator_plan": {"mode": "pipeline_golden_diff", "default_compare": "structured"},
    }



def _ensure_required_fixture_inputs(archetype_id: str, raw_data: Path) -> None:
    def write_fasta(name: str) -> None:
        path = raw_data / name
        if not path.exists():
            path.write_text(">fixture_gene\nMA\n", encoding="utf-8")

    if archetype_id in {"hmmer_domain", "motif_meme", "pep_properties"}:
        write_fasta("input.fa")
    elif archetype_id == "synteny_collinearity":
        write_fasta("query.fa")
        write_fasta("reference.fa")
    elif archetype_id == "phylogeny_tree" and not any(p.suffix.lower() in {".fa", ".fasta", ".faa"} for p in raw_data.iterdir() if p.is_file()):
        write_fasta("input.fa")

def _try_copy(files: list[dict[str, Any]], raw_data: Path, predicate: Any, dest_name: str) -> str:
    for item in files:
        path = Path(str(item.get("full_path") or ""))
        if path.is_file() and predicate(item, path):
            dest = raw_data / dest_name
            shutil.copy2(path, dest)
            return dest_name
    return ""


def _case_object(case: dict[str, Any]) -> Any:
    from gfa_core.extension_stage.template_cases import TemplateCase

    return TemplateCase(
        archetype_id=str(case.get("archetype_id") or ""),
        stage=str(case.get("stage") or ""),
        manifest_path=str(case.get("manifest_path") or ""),
        prefix=str(case.get("prefix") or ""),
        scope_id=str(case.get("scope_id") or ""),
        status=str(case.get("status") or ""),
        command=str(case.get("command") or ""),
        code_files=list(case.get("code_files") or []),
        input_files=list(case.get("input_files") or []),
        output_files=list(case.get("output_files") or []),
        artifact_types=list(case.get("artifact_types") or []),
        risk_checks=list(case.get("risk_checks") or []),
        output_contract=list(case.get("output_contract") or []),
        comparator_plan=dict(case.get("comparator_plan") or {}),
    )


def _extension_from_spec(case: dict[str, Any], spec: dict[str, Any], extension_root: Path, artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    archetype_id = str(case.get("archetype_id") or "template_case")
    return {
        "schema_version": "extension_stage_v2",
        "extension_id": f"template_{_safe_name(archetype_id)}",
        "name": f"template_{_safe_name(archetype_id)}",
        "display_name": f"Template validation: {archetype_id}",
        "status": "validated",
        "runtime": {"type": "conda", "env": "test4py311", "language": "shell"},
        "paths": {"root": str(extension_root), "raw_code": "raw_code", "raw_test_data": "raw_test_data", "converted_code": "converted_code"},
        "entrypoint": spec.get("entrypoint") or {"original_command": spec.get("original_command"), "command_template": spec.get("command_template")},
        "command_template": spec.get("command_template"),
        "analysis_type": spec.get("analysis_type"),
        "tool_family": spec.get("tool_family"),
        "input_roles": spec.get("input_roles") or {},
        "parameters": spec.get("parameters") or {},
        "confirmed_parameters": spec.get("parameters") or {},
        "outputs": spec.get("outputs") or {},
        "output_contract": spec.get("output_contract") or [],
        "comparator_plan": spec.get("comparator_plan") or {},
        "converter": spec.get("converter") or {"type": "deepseek_api"},
        "generated_code_artifacts": artifacts,
        "template_case": case,
    }


def _safe_name(value: str) -> str:
    import re

    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-")
    return text or "case"


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _report_markdown(report: dict[str, Any]) -> str:
    lines = ["# Extension Template Chain Validation", "", f"Status: `{report.get('status')}`", "", f"Cases: {report.get('case_count')} passed={report.get('passed')} failed={report.get('failed')}", ""]
    lines.append("| Archetype | Stage | Status | Error |")
    lines.append("|---|---|---|---|")
    for item in report.get("results") or []:
        lines.append(f"| `{item.get('archetype_id')}` | `{item.get('stage')}` | `{item.get('status')}` | {str(item.get('error') or '')[:160]} |")
    lines.append("")
    return "\n".join(lines)
