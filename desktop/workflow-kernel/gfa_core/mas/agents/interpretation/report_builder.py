"""Build claim-level layers for an evidence report."""
from __future__ import annotations

from typing import Any, Dict, List

from .claim_compiler import compile_claims
from .evidence_audit import build_evidence_audit
from .narrative_composer import compose_narrative, select_major_claims
from .pattern_library import load_builtin_patterns
from .pattern_matcher import match_claim_patterns


RAG_BINDING_TERMS = {
    "family_size": ["gene family", "基因家族", "expansion", "contraction", "扩张", "收缩"],
    "phylogenetic_grouping": ["phylogeny", "phylogenetic", "subfamily", "系统发育", "亚家族"],
    "conserved_architecture": ["motif", "domain", "conserved", "structure", "保守", "结构域"],
    "duplication_retention": ["synteny", "collinearity", "duplication", "wgd", "共线性", "复制"],
    "cross_species_conservation": ["ortholog", "comparative", "species", "同源", "比较基因组"],
    "selection_pressure": ["ka/ks", "kaks", "selection", "purifying", "positive selection", "选择压力", "正选择", "纯化选择"],
    "subfamily_prediction_reference": ["phylogeny", "subfamily", "classification", "clade", "domain", "motif", "亚家族", "分类", "系统发育"],
    "subfamily_assignment": ["phylogeny", "subfamily", "classification", "clade", "亚家族", "分类", "系统发育"],
    "protein_property": ["protein", "physicochemical", "isoelectric", "蛋白", "理化性质"],
    "interaction_context": ["ppi", "protein interaction", "string", "蛋白互作"],
    "expression_response": ["expression", "rna-seq", "differential expression", "表达", "差异表达"],
    "functional_enrichment": ["go", "kegg", "enrichment", "pathway", "富集", "通路"],
}


def _as_reference_list(stage: str, value: Any) -> List[Dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, dict):
        nested = value.get("references") or value.get("sources") or value.get("items")
        if isinstance(nested, list):
            raw_items = nested
        else:
            raw_items = [value]
    else:
        raw_items = [{"text": str(value)}]

    items: List[Dict[str, Any]] = []
    for raw in raw_items:
        if not raw:
            continue
        if isinstance(raw, dict):
            item = dict(raw)
        else:
            item = {"text": str(raw)}
        item.setdefault("stage", stage)
        items.append(item)
    return items


def _reference_items(rag_references: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for stage, value in rag_references.items():
        for item in _as_reference_list(stage, value):
            haystack = " ".join(
                str(item.get(key, ""))
                for key in ("title", "paper_title", "text", "abstract", "summary", "keywords", "snippet", "excerpt", "evidence", "answer_preview")
            ).lower()
            item["_haystack"] = haystack
            items.append(item)
    return items


def _claim_terms(claim: Dict[str, Any]) -> List[str]:
    terms = set(str(item).lower() for item in claim.get("stages", []) + claim.get("matched_patterns", []))
    claim_group = str(claim.get("claim_group", "")).lower()
    terms.update(part for part in claim_group.split("_") if part)
    terms.update(term.lower() for term in RAG_BINDING_TERMS.get(claim_group, []))
    text = " ".join(str(claim.get(key, "")) for key in ("claim", "interpretation", "caveat")).lower()
    for group, group_terms in RAG_BINDING_TERMS.items():
        if group in claim_group or any(term in text for term in group_terms):
            terms.update(term.lower() for term in group_terms)
    return sorted(term for term in terms if len(term) >= 2)


def _ref_label(ref: Dict[str, Any]) -> str:
    return str(
        ref.get("title")
        or ref.get("paper_title")
        or ref.get("source")
        or ref.get("url")
        or ref.get("stage")
        or "reference"
    )


def _ref_evidence(ref: Dict[str, Any]) -> str:
    return str(
        ref.get("evidence")
        or ref.get("excerpt")
        or ref.get("snippet")
        or ref.get("summary")
        or ref.get("answer_preview")
        or ref.get("text")
        or ""
    )


def _compact_reference(ref: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "title": _ref_label(ref),
        "evidence": _ref_evidence(ref),
        "support_level": str(ref.get("support_level") or ""),
        "source": str(ref.get("source") or ""),
        "paper_id": str(ref.get("paper_id") or ref.get("pmcid") or ref.get("pmid") or ref.get("doi") or ""),
        "query": str(ref.get("query") or ""),
        "rejected_reason": str(ref.get("rejected_reason") or ""),
    }


def _suggested_wording(claim: Dict[str, Any], method_refs: List[Dict[str, Any]], background_refs: List[Dict[str, Any]], claim_refs: List[Dict[str, Any]]) -> str:
    claim_text = str(claim.get("claim") or "")
    if claim_refs:
        return f"可表述为：{claim_text}；该表述由本地证据支撑，并有相关文献作为当前结论背景支撑。"
    if method_refs:
        return f"可表述为：{claim_text}；该结论应写成候选或筛查线索，文献只支持同类分析逻辑，不替代本地验证。"
    if background_refs:
        return f"可表述为：{claim_text}；相关文献可用于交代生物学背景，但不能作为当前结果的直接证据。"
    return ""


def _rag_interpretation_for_claim(claim: Dict[str, Any], refs: List[Dict[str, Any]]) -> Dict[str, Any]:
    claim_support = [_compact_reference(ref) for ref in refs if ref.get("support_level") == "claim_bound"][:3]
    method_reference = [_compact_reference(ref) for ref in refs if ref.get("support_level") == "method_support"][:3]
    biological_context = [_compact_reference(ref) for ref in refs if ref.get("support_level") == "biological_background"][:3]
    boundary_parts = []
    if method_reference:
        boundary_parts.append("方法学参考只说明同类分析逻辑可借鉴，不能证明本次样本中的生物学结论。")
    if biological_context:
        boundary_parts.append("生物学背景只解释家族、结构域或功能语境，不能替代本地manifest、原始文件和统计结果。")
    if claim_support:
        boundary_parts.append("文献支撑仍需与本地证据链同时满足，不能覆盖本地风险标记。")
    next_validation = list(claim.get("next_validation") or [])[:2]
    if method_reference and not next_validation:
        next_validation.append("补充分支支持率、成员级证据矩阵或实验验证后再升级结论强度。")
    return {
        "claim_support": claim_support,
        "method_reference": method_reference,
        "biological_context": biological_context,
        "boundary_note": " ".join(boundary_parts),
        "suggested_wording": _suggested_wording(claim, method_reference, biological_context, claim_support),
        "next_validation_from_literature": next_validation,
    }


def _bind_rag_support(claims: List[Dict[str, Any]], rag_references: Dict[str, Any]) -> List[Dict[str, Any]]:
    refs = _reference_items(rag_references)
    bound: List[Dict[str, Any]] = []
    support_rank = {
        "claim_bound": 4,
        "method_support": 3,
        "biological_background": 2,
        "not_supported": 1,
        "not_available": 0,
    }
    for claim in claims:
        claim = dict(claim)
        explicit_refs = [
            {k: v for k, v in ref.items() if not k.startswith("_")}
            for ref in refs
            if str(ref.get("claim_id") or "") == str(claim.get("claim_id") or "")
        ]
        if explicit_refs:
            explicit_refs.sort(key=lambda item: support_rank.get(str(item.get("support_level") or ""), -1), reverse=True)
            visible_support = [item for item in explicit_refs if item.get("support_level") != "not_supported"]
            best_level = str((explicit_refs[0] or {}).get("support_level") or "not_supported")
            claim["rag_support"] = visible_support[:3]
            claim["rag_binding_status"] = best_level
            claim["rag_interpretation"] = _rag_interpretation_for_claim(claim, visible_support)
            best_reason = (explicit_refs[0] or {}).get("rejected_reason")
            if best_reason:
                claim["rag_rejected_reason"] = best_reason
            bound.append(claim)
            continue

        terms = _claim_terms(claim)
        scored_support = []
        for ref in refs:
            haystack = ref.get("_haystack", "")
            matched_terms = [term for term in terms if term and term in haystack]
            if matched_terms:
                item = {k: v for k, v in ref.items() if not k.startswith("_")}
                item["binding_terms"] = matched_terms[:6]
                item["binding_score"] = len(matched_terms)
                scored_support.append(item)
        scored_support.sort(key=lambda item: item.get("binding_score", 0), reverse=True)
        support = scored_support[:3]
        for item in support:
            item.setdefault("support_level", "claim_bound")
        claim["rag_support"] = support
        claim["rag_binding_status"] = "claim_bound" if support else ("unbound" if refs else "not_available")
        claim["rag_interpretation"] = _rag_interpretation_for_claim(claim, support)
        bound.append(claim)
    return bound


def _rag_audit(rag_references: Dict[str, Any], claims: List[Dict[str, Any]]) -> Dict[str, Any]:
    refs = _reference_items(rag_references)
    reference_count = len(refs)
    claim_binding_count = sum(1 for claim in claims if claim.get("rag_binding_status") == "claim_bound")
    method_reference_count = sum(len(((claim.get("rag_interpretation") or {}).get("method_reference") or [])) for claim in claims)
    background_count = sum(len(((claim.get("rag_interpretation") or {}).get("biological_context") or [])) for claim in claims)
    active_claims = [claim for claim in claims if claim.get("status") == "active"]
    unbound_active_claims = [
        claim.get("claim_id", "")
        for claim in active_claims
        if claim.get("rag_binding_status") != "claim_bound"
    ]
    status_counts: Dict[str, int] = {}
    for claim in claims:
        status = str(claim.get("rag_binding_status") or "not_available")
        status_counts[status] = status_counts.get(status, 0) + 1
    if any(claim.get("rag_binding_status") == "claim_bound" for claim in claims):
        status = "claim_bound"
    elif any(claim.get("rag_binding_status") == "method_support" for claim in claims):
        status = "method_support"
    elif reference_count:
        status = "background_only"
    else:
        status = "not_available"
    return {
        "schema": "rag_audit_v1",
        "mode": "external_support_only",
        "reference_stage_count": len(rag_references),
        "reference_count": reference_count,
        "claim_binding_count": claim_binding_count,
        "method_reference_count": method_reference_count,
        "background_claim_count": background_count,
        "biological_context_count": background_count,
        "status_counts": status_counts,
        "unbound_active_claim_count": len(unbound_active_claims),
        "status": status,
        "message": (
            "RAG contains literature that directly supports one or more current claims; local evidence remains authoritative."
            if claim_binding_count
            else "RAG provides methodology/background interpretation for active claims; it does not replace local evidence."
            if reference_count
            else "No RAG references were available for claim-level support."
        ),
        "claims_requiring_external_review": unbound_active_claims,
    }


def build_claim_layers(
    stages: List[str],
    bundles: Dict[str, Dict[str, Any]],
    *,
    evidence_cards: List[Dict[str, Any]],
    risk_flags: List[Dict[str, Any]],
    rag_references: Dict[str, Any] | None = None,
    scope: Dict[str, Any] | None = None,
    prefix: str = "",
) -> Dict[str, Any]:
    evidence_audit = build_evidence_audit(stages, bundles)
    patterns = load_builtin_patterns()
    pattern_matches = match_claim_patterns(stages, bundles, patterns)
    claims = compile_claims(
        pattern_matches,
        bundles=bundles,
        evidence_cards=evidence_cards,
        risk_flags=risk_flags,
        prefix=prefix,
    )
    claims = _bind_rag_support(claims, rag_references or {})
    major_claims = select_major_claims(claims)
    rag_audit = _rag_audit(rag_references or {}, claims)
    narrative = compose_narrative(claims, major_claims, evidence_audit)
    return {
        "schema": "claim_layers_v1",
        "scope": scope or {},
        "evidence_audit": evidence_audit,
        "claims": claims,
        "major_claims": major_claims,
        "claim_patterns": pattern_matches,
        "rag_audit": rag_audit,
        "narrative": narrative,
    }
