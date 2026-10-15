"""Evidence support audit for stage bundles."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List


def _positive(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _risk_severity(risks: Iterable[Dict[str, Any]], severity: str) -> bool:
    return any(isinstance(risk, dict) and risk.get("severity") == severity for risk in risks)


def _base_entry(stage: str, bundle: Dict[str, Any]) -> Dict[str, Any]:
    parsed = bundle.get("parsed") if isinstance(bundle.get("parsed"), dict) else {}
    metrics = bundle.get("metrics") if isinstance(bundle.get("metrics"), dict) else {}
    risks = bundle.get("risks") if isinstance(bundle.get("risks"), list) else []
    evidence = bundle.get("evidence") if isinstance(bundle.get("evidence"), list) else []
    available = sorted(set(parsed) | {f"metric:{key}" for key in metrics})
    return {
        "stage": stage,
        "status": bundle.get("status", "unknown"),
        "available_evidence": available,
        "evidence_count": len(evidence),
        "risk_count": len(risks),
        "has_high_risk": _risk_severity(risks, "high"),
        "allowed_claim_types": [],
        "blocked_claim_types": [],
        "missing_for_upgrade": [],
    }


def build_evidence_audit(stages: List[str], bundles: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    stage_audits: List[Dict[str, Any]] = []
    for stage in stages:
        bundle = bundles.get(stage) or {}
        parsed = bundle.get("parsed") if isinstance(bundle.get("parsed"), dict) else {}
        entry = _base_entry(stage, bundle)

        if stage == "identify":
            if _positive(parsed.get("final_members")) or entry["evidence_count"]:
                entry["allowed_claim_types"].extend(["family_member_count", "member_file_consistency"])
            entry["blocked_claim_types"].append("family_expansion_or_contraction")
            entry["missing_for_upgrade"].append("reference-scale family size comparison")
        elif stage == "domain":
            if _positive(parsed.get("significant_domain_hits")) or _positive(parsed.get("sbp_protein_count")):
                entry["allowed_claim_types"].append("core_domain_support")
            else:
                entry["blocked_claim_types"].append("core_domain_support")
                entry["missing_for_upgrade"].append("significant domtblout hits")
        elif stage == "motif":
            if _positive(parsed.get("motif_count")):
                entry["allowed_claim_types"].append("motif_conservation")
            else:
                entry["blocked_claim_types"].append("motif_conservation")
                entry["missing_for_upgrade"].append("non-empty motif summary")
            entry["blocked_claim_types"].append("gene-level motif arrangement")
            entry["missing_for_upgrade"].append("per-gene motif position table")
        elif stage == "phylogeny":
            if _positive(parsed.get("sequence_count")) or _positive(parsed.get("msa_sequence_count")) or _positive(parsed.get("tree_tip_estimate")):
                entry["allowed_claim_types"].extend(["phylogeny_input_consistency", "tree_quality_caution"])
            entry["blocked_claim_types"].extend(["subfamily_expansion", "branch_support_inference"])
            entry["missing_for_upgrade"].extend(["subfamily annotation", "bootstrap/support values", "species tip mapping"])
        elif stage == "promoter":
            if _positive(parsed.get("genes_with_elements")) or parsed.get("top_elements"):
                entry["allowed_claim_types"].append("promoter_regulatory_potential")
            entry["blocked_claim_types"].append("regulatory_mechanism")
            entry["missing_for_upgrade"].append("expression or experimental validation")
        elif stage == "expression":
            tables = parsed.get("tables") if isinstance(parsed.get("tables"), dict) else {}
            if tables or parsed.get("top_expression_genes"):
                entry["allowed_claim_types"].append("expression_candidate")
            entry["blocked_claim_types"].append("causal_regulation")
            entry["missing_for_upgrade"].append("treatment statistics or validation experiment")
        elif stage == "rnaseq_de":
            entry["allowed_claim_types"].append("statistical_expression_response")
            entry["blocked_claim_types"].append("causal_mechanism")
            entry["missing_for_upgrade"].append("functional validation")
        elif stage in {"rnaseq_go_enrich", "rnaseq_kegg_enrich", "rnaseq_fgsea", "rnaseq_enrich"}:
            entry["allowed_claim_types"].append("functional_theme")
            entry["blocked_claim_types"].append("direct_pathway_causality")
            entry["missing_for_upgrade"].append("leading genes and independent validation")
        elif stage == "rnaseq_wgcna":
            entry["allowed_claim_types"].append("candidate_coexpression_module")
            entry["blocked_claim_types"].append("causal_network")
            entry["missing_for_upgrade"].append("adequate sample size and hub validation")
        elif stage == "synteny":
            if _positive(parsed.get("family_pair_count")):
                entry["allowed_claim_types"].append("family_duplication_candidate")
            entry["blocked_claim_types"].append("whole-genome block equals family expansion")
            entry["missing_for_upgrade"].append("member-level duplicated pair table and duplication mode labels")
        elif stage == "interspecies_synteny":
            if _positive(parsed.get("family_ref_pair_count")) or _positive(parsed.get("pairwise_family_highlight_links")):
                entry["allowed_claim_types"].append("cross_species_conservation_candidate")
            entry["blocked_claim_types"].append("direct conserved synteny without family-level pair support")
            entry["missing_for_upgrade"].append("target-reference family pair table")
        elif stage == "ortholog":
            metrics = bundle.get("metrics") if isinstance(bundle.get("metrics"), dict) else {}
            if _positive(metrics.get("ortholog_pairs")):
                entry["allowed_claim_types"].append("orthology_support")
            entry["blocked_claim_types"].append("functional equivalence")
            entry["missing_for_upgrade"].append("reciprocal best-hit details and phylogenetic placement")
        elif stage == "kaks":
            if _positive(parsed.get("successful_pairs")) or _positive((bundle.get("metrics") or {}).get("successful_pairs")):
                entry["allowed_claim_types"].append("selection_pressure_candidate")
            entry["blocked_claim_types"].append("proven positive selection")
            entry["missing_for_upgrade"].append("CDS alignment QC and saturated Ks filtering")
        elif stage in {"reference_classification", "subfamily_confirm"}:
            metrics = bundle.get("metrics") if isinstance(bundle.get("metrics"), dict) else {}
            if _positive(parsed.get("target_gene_count")) or _positive(parsed.get("confirmed_genes")) or _positive(metrics.get("target_gene_count")) or _positive(metrics.get("confirmed_genes")):
                entry["allowed_claim_types"].append("subfamily_assignment")
            entry["blocked_claim_types"].append("stable subfamily evolution without support values")
            entry["missing_for_upgrade"].append("support values and uncertain assignment audit")
        elif stage == "pep":
            if _positive(parsed.get("protein_count")):
                entry["allowed_claim_types"].append("protein_property_profile")
            entry["blocked_claim_types"].append("protein function inferred from properties alone")
            entry["missing_for_upgrade"].append("experimental localization or biochemical validation")
        elif stage == "ppi":
            if _positive(parsed.get("total_interactions")) and _positive(parsed.get("genes_in_network")):
                entry["allowed_claim_types"].append("interaction_network_candidate")
            entry["blocked_claim_types"].append("validated physical interaction")
            entry["missing_for_upgrade"].append("species mapping audit and experimental PPI validation")
        else:
            entry["blocked_claim_types"].append("stage_specific_biological_inference")
            entry["missing_for_upgrade"].append("stage-specific claim rule")

        stage_audits.append(entry)

    return {
        "schema": "evidence_audit_v1",
        "stage_count": len(stage_audits),
        "stages": stage_audits,
        "claim_gate": "claims require local evidence and must stay within allowed_claim_types",
    }


def stage_allows_claim(audit: Dict[str, Any], stage: str, claim_type: str) -> bool:
    for entry in audit.get("stages", []):
        if entry.get("stage") == stage:
            return claim_type in set(entry.get("allowed_claim_types") or [])
    return False
