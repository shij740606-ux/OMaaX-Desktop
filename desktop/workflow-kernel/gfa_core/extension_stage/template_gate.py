"""Template-archetype gate for extension stages."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from gfa_core.extension_stage.template_cases import ARCHETYPE_BY_ID


TEMPLATE_GATE_VERSION = "extension_template_gate_v1"
LEGACY_TEMPLATE_GATE_STATUS = "legacy_unchecked"
TEMPLATE_NOT_APPLICABLE_STATUS = "not_applicable"


def infer_archetype_id(extension: dict[str, Any]) -> str:
    """Return the declared or best-effort archetype id for an extension."""
    declared = str(extension.get("archetype_id") or "").strip()
    if declared:
        return declared
    template_case = extension.get("template_case") or {}
    if isinstance(template_case, dict) and template_case.get("archetype_id"):
        return str(template_case.get("archetype_id") or "").strip()
    analysis_type = str(extension.get("analysis_type") or "").strip()
    tool_family = str(extension.get("tool_family") or "").strip()
    input_roles = extension.get("input_roles") or {}
    name_text = " ".join(str(extension.get(k, "")) for k in ("extension_id", "name", "display_name", "description")).lower()
    if analysis_type == "sequence_extraction":
        return "sequence_extraction"
    if analysis_type == "hmmer_search" or tool_family == "hmmer":
        return "hmmer_domain"
    if analysis_type == "blast_search" or tool_family == "blast":
        return "synteny_collinearity"
    if "kaks" in name_text:
        return "kaks_pairwise"
    for gwas_archetype in (
        "gwas_trait_vcf_qc",
        "gwas_vcf_filter_index",
        "gwas_trait_prepare_match",
        "gwas_plink_pca",
        "gwas_trait_blup_format",
        "gwas_plink_impute",
        "gwas_tassel_compatible",
        "gwas_gemma_assoc",
    ):
        if gwas_archetype in name_text:
            return gwas_archetype
    for vcf_archetype in (
        "vcf_fastp_clean",
        "vcf_reference_index",
        "vcf_bwa_align_sort",
        "vcf_markduplicates",
        "vcf_bam_stats",
        "vcf_haplotypecaller_gvcf",
        "vcf_combine_gvcf",
        "vcf_genotype_gvcf",
        "vcf_select_variants",
        "vcf_hard_filter_clean",
    ):
        if vcf_archetype in name_text:
            return vcf_archetype
    if any(token in name_text for token in ("pep", "protein properties", "protparam", "subcellular")) and analysis_type == "enrichment_or_stats":
        return "pep_properties"
    if "input_id_file" in input_roles and any(key in input_roles for key in ("input_pep_fasta", "input_cds_fasta", "input_fasta")):
        return "sequence_extraction"
    return ""


def evaluate_template_gate(extension: dict[str, Any], *, legacy_compat: bool = False) -> dict[str, Any]:
    """Evaluate whether an extension has enough template evidence to publish/run."""
    archetype_id = infer_archetype_id(extension)
    errors: list[str] = []
    warnings: list[str] = []
    archetype = ARCHETYPE_BY_ID.get(archetype_id)
    golden = extension.get("golden_comparison") or (extension.get("last_validation") or {}).get("golden_comparison") or {}
    golden_status = str(golden.get("status") or "")

    if legacy_compat:
        status = LEGACY_TEMPLATE_GATE_STATUS
        warnings.append("published before template gate was enforced")
    else:
        if not archetype_id:
            status = TEMPLATE_NOT_APPLICABLE_STATUS
            warnings.append("no archetype_id declared; runnable publication is allowed but template-library eligibility is false")
        elif not archetype:
            errors.append(f"unknown archetype_id: {archetype_id}")
            if golden_status != "passed":
                errors.append("golden comparison did not pass")
            status = "failed"
        else:
            if golden_status != "passed":
                errors.append("golden comparison did not pass")
            errors.extend(_contract_errors(extension, archetype))
            status = "passed" if not errors else "failed"

    return {
        "schema_version": TEMPLATE_GATE_VERSION,
        "status": status,
        "template_status": status,
        "template_eligible": status == "passed",
        "archetype_id": archetype_id,
        "checked_at": _now(),
        "errors": errors,
        "warnings": warnings,
        "risk_checks": list(archetype.risk_checks) if archetype else [],
        "gate_version": TEMPLATE_GATE_VERSION,
        "golden_status": golden_status,
    }


def template_gate_publish_status(extension: dict[str, Any]) -> str:
    gate = extension.get("template_gate") or {}
    status = str(gate.get("status") or "")
    if status:
        return status
    if extension.get("status") == "published" and (extension.get("golden_comparison") or {}).get("status") == "passed":
        return LEGACY_TEMPLATE_GATE_STATUS
    return ""


def is_template_gate_acceptable(extension: dict[str, Any]) -> bool:
    return template_gate_publish_status(extension) in {"passed", LEGACY_TEMPLATE_GATE_STATUS, TEMPLATE_NOT_APPLICABLE_STATUS}


def _contract_errors(extension: dict[str, Any], archetype: Any) -> list[str]:
    errors: list[str] = []
    roles = extension.get("input_roles") or {}
    contract = extension.get("output_contract") or []
    if not isinstance(roles, dict):
        roles = {}
    if not isinstance(contract, list):
        contract = []
    required_roles = {
        key
        for key, spec in (archetype.input_roles or {}).items()
        if isinstance(spec, dict) and bool(spec.get("required", True))
    }
    missing_roles = sorted(role for role in required_roles if role not in roles)
    if missing_roles:
        errors.append("missing required archetype input role(s): " + ", ".join(missing_roles))
    required_outputs = [
        item for item in archetype.output_contract
        if isinstance(item, dict) and bool(item.get("required", True))
    ]
    if required_outputs and not contract:
        errors.append("missing output_contract for archetype")
    for item in required_outputs:
        semantic_role = str(item.get("semantic_role") or "")
        pattern = str(item.get("name_pattern") or "")
        if semantic_role and any(str(row.get("semantic_role") or "") == semantic_role for row in contract if isinstance(row, dict)):
            continue
        if pattern and any(_pattern_compatible(pattern, str(row.get("name_pattern") or "")) for row in contract if isinstance(row, dict)):
            continue
        errors.append(f"output_contract missing required archetype output: {semantic_role or pattern}")
    return errors


def _pattern_compatible(expected: str, actual: str) -> bool:
    if not actual:
        return False
    if actual == expected or actual == "*":
        return True
    expected_suffix = expected.lstrip("*")
    actual_suffix = actual.lstrip("*")
    return bool(expected_suffix and actual.endswith(expected_suffix)) or bool(actual_suffix and expected.endswith(actual_suffix))


def _now() -> str:
    return datetime.now().isoformat()
