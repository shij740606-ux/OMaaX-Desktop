"""Match local stage evidence to literature-logic claim patterns."""
from __future__ import annotations

from typing import Any, Dict, List


def _positive(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _stage(bundles: Dict[str, Dict[str, Any]], stage: str) -> Dict[str, Any]:
    return bundles.get(stage) or {}


def _parsed(bundles: Dict[str, Dict[str, Any]], stage: str) -> Dict[str, Any]:
    parsed = _stage(bundles, stage).get("parsed")
    return parsed if isinstance(parsed, dict) else {}


def _metrics(bundles: Dict[str, Dict[str, Any]], stage: str) -> Dict[str, Any]:
    metrics = _stage(bundles, stage).get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def _value(bundles: Dict[str, Dict[str, Any]], stage: str, key: str) -> Any:
    parsed = _parsed(bundles, stage)
    metrics = _metrics(bundles, stage)
    return parsed.get(key) if parsed.get(key) not in (None, "") else metrics.get(key)


def evidence_token_available(token: str, bundles: Dict[str, Dict[str, Any]]) -> bool:
    identify = _parsed(bundles, "identify")
    domain = _parsed(bundles, "domain")
    motif = _parsed(bundles, "motif")
    phylogeny = _parsed(bundles, "phylogeny")
    promoter = _parsed(bundles, "promoter")
    expression = _parsed(bundles, "expression")
    rnaseq_de = _metrics(bundles, "rnaseq_de")
    rnaseq_wgcna = _metrics(bundles, "rnaseq_wgcna")

    if token == "identify_member_count":
        return _positive(identify.get("final_members")) or bool(_stage(bundles, "identify").get("evidence"))
    if token == "member_file_consistency":
        risks = _stage(bundles, "identify").get("risks") or []
        return not any(risk.get("severity") == "high" for risk in risks if isinstance(risk, dict))
    if token == "hmm_blast_support":
        return _positive(identify.get("hmm_hits")) or _positive(identify.get("blast_hits"))
    if token == "significant_domain_hits":
        return _positive(domain.get("significant_domain_hits")) or _positive(domain.get("sbp_protein_count"))
    if token == "sbp_domain_coverage":
        return _positive(domain.get("sbp_protein_count"))
    if token == "motif_count":
        return _positive(motif.get("motif_count"))
    if token == "motif_core_coverage":
        conserved = motif.get("most_conserved_motif") if isinstance(motif.get("most_conserved_motif"), dict) else {}
        return _positive(conserved.get("sites"))
    if token == "phylogeny_input":
        return _positive(phylogeny.get("sequence_count")) or _positive(phylogeny.get("msa_sequence_count")) or _positive(phylogeny.get("tree_tip_estimate"))
    if token == "promoter_elements":
        return _positive(promoter.get("genes_with_elements")) or bool(promoter.get("top_elements"))
    if token == "expression_response":
        tables = expression.get("tables") if isinstance(expression.get("tables"), dict) else {}
        gene_exp = tables.get("gene_exp") if isinstance(tables.get("gene_exp"), dict) else {}
        return _positive(gene_exp.get("rows")) or bool(expression.get("top_expression_genes")) or _positive(rnaseq_de.get("de_genes_total"))
    if token == "rnaseq_de_response":
        return _positive(rnaseq_de.get("de_genes_total")) or _positive(rnaseq_de.get("de_genes_up")) or _positive(rnaseq_de.get("de_genes_down"))
    if token == "rnaseq_enrichment_theme":
        for stage in ("rnaseq_go_enrich", "rnaseq_kegg_enrich", "rnaseq_fgsea", "rnaseq_enrich"):
            metrics = _metrics(bundles, stage)
            if any(_positive(value) for value in metrics.values()):
                return True
        return False
    if token == "wgcna_module":
        return _positive(rnaseq_wgcna.get("modules")) or _positive(rnaseq_wgcna.get("genes_used"))
    if token == "leading_genes":
        parsed = _parsed(bundles, "rnaseq_fgsea")
        return bool(parsed.get("top_fgsea_pathways"))
    if token == "family_synteny_pairs":
        return _positive(_value(bundles, "synteny", "family_pair_count"))
    if token == "interspecies_anchor_pairs":
        return _positive(_value(bundles, "interspecies_synteny", "family_ref_pair_count")) or _positive(_value(bundles, "interspecies_synteny", "pairwise_family_highlight_links"))
    if token == "ortholog_pairs":
        return _positive(_value(bundles, "ortholog", "ortholog_pairs"))
    if token == "kaks_pairs":
        return _positive(_value(bundles, "kaks", "successful_pairs")) or _positive(_value(bundles, "kaks", "total_pairs"))
    if token == "kaks_positive_candidates":
        return _positive(_value(bundles, "kaks", "positive_selection_count")) or _positive(_value(bundles, "kaks", "max_ka_ks"))
    if token == "subfamily_prediction_attempt":
        reference_classification = _stage(bundles, "reference_classification")
        metrics = _metrics(bundles, "reference_classification")
        parsed = _parsed(bundles, "reference_classification")
        return bool(reference_classification) and (
            bool(metrics)
            or bool(parsed)
            or bool(reference_classification.get("evidence"))
            or str(reference_classification.get("status") or "") == "failed"
        )
    if token == "subfamily_assignment":
        return _positive(_value(bundles, "reference_classification", "target_gene_count")) or _positive(_value(bundles, "subfamily_confirm", "confirmed_genes"))
    if token == "subfamily_confidence":
        counts = _value(bundles, "reference_classification", "confidence_counts")
        if isinstance(counts, dict):
            return _positive(counts.get("high")) or _positive(counts.get("medium"))
        return False
    if token == "pep_property_profile":
        return _positive(_value(bundles, "pep", "protein_count"))
    if token == "ppi_network_nonempty":
        return _positive(_value(bundles, "ppi", "total_interactions")) and _positive(_value(bundles, "ppi", "genes_in_network"))
    return False


def match_claim_patterns(
    stages: List[str],
    bundles: Dict[str, Dict[str, Any]],
    patterns: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    stage_set = set(stages)
    matches: List[Dict[str, Any]] = []
    for pattern in patterns:
        applicable = [stage for stage in pattern.get("applicable_stages", []) if stage in stage_set]
        if not applicable:
            continue
        required = list(pattern.get("required_evidence") or [])
        optional = list(pattern.get("optional_evidence") or [])
        satisfied = [token for token in required if evidence_token_available(token, bundles)]
        missing = [token for token in required if token not in satisfied]
        optional_satisfied = [token for token in optional if evidence_token_available(token, bundles)]
        status = "matched" if not missing else ("partial" if satisfied or optional_satisfied else "unmatched")
        matches.append({
            "pattern_id": pattern.get("pattern_id", ""),
            "name": pattern.get("name", ""),
            "status": status,
            "applicable_stages": applicable,
            "required_evidence": required,
            "required_satisfied": satisfied,
            "missing_required": missing,
            "optional_evidence": optional,
            "optional_satisfied": optional_satisfied,
            "allowed_claim": pattern.get("allowed_claim", ""),
            "forbidden_claim": pattern.get("forbidden_claim", ""),
            "default_confidence": pattern.get("default_confidence", "medium"),
            "claim_group": pattern.get("claim_group", ""),
            "claim_type": pattern.get("claim_type", "candidate_hypothesis"),
            "validation_needed": pattern.get("validation_needed", []),
        })
    return matches
