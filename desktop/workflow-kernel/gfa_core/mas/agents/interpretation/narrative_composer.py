"""Compose claim-level narrative sections."""
from __future__ import annotations

from typing import Any, Dict, List


def select_major_claims(claims: List[Dict[str, Any]], *, limit: int = 5) -> List[Dict[str, Any]]:
    rank = {"high": 3, "medium": 2, "low": 1}
    active = [claim for claim in claims if claim.get("status") == "active" and claim.get("local_evidence")]
    return sorted(active, key=lambda claim: rank.get(claim.get("confidence"), 0), reverse=True)[:limit]


def compose_narrative(
    claims: List[Dict[str, Any]],
    major_claims: List[Dict[str, Any]],
    evidence_audit: Dict[str, Any],
) -> Dict[str, Any]:
    blocked = []
    for stage in evidence_audit.get("stages", []):
        for item in stage.get("blocked_claim_types") or []:
            blocked.append({"stage": stage.get("stage", ""), "claim_type": item})
    paragraphs = []
    for claim in major_claims[:5]:
        evidence = claim.get("local_evidence") or []
        evidence_text = "；".join(str(item.get("statement", "")) for item in evidence[:2] if isinstance(item, dict) and item.get("statement"))
        limitation = "；".join(str(item) for item in (claim.get("limitations") or [])[:1])
        next_step = "；".join(str(item) for item in (claim.get("next_validation") or [])[:1])
        text = str(claim.get("claim") or "")
        if evidence_text:
            text += f" 本地证据：{evidence_text}。"
        if limitation:
            text += f" 边界：{limitation}。"
        if next_step:
            text += f" 下一步：{next_step}。"
        paragraphs.append(text)
    return {
        "schema": "claim_narrative_v1",
        "executive_summary": paragraphs,
        "claim_count": len(claims),
        "major_claim_count": len(major_claims),
        "boundary_summary": blocked[:12],
        "report_order": [
            "major_claims",
            "local_evidence",
            "limitations",
            "rag_support",
            "confidence",
            "next_validation",
        ],
    }


def claims_to_scientific_discoveries(major_claims: List[Dict[str, Any]], *, limit: int = 5) -> List[Dict[str, Any]]:
    discoveries: List[Dict[str, Any]] = []
    for claim in major_claims:
        if len(discoveries) >= limit:
            break
        if claim.get("status") != "active":
            continue
        evidence_chain = claim.get("evidence_chain") or []
        evidence_text = claim.get("evidence") or "；".join(
            str(item.get("statement") or "")
            for item in evidence_chain[:3]
            if isinstance(item, dict) and item.get("statement")
        )
        caveat = claim.get("caveat") or "当前结论必须限制在本地证据支持范围内。"
        discoveries.append({
            "id": len(discoveries) + 1,
            "type": claim.get("claim_group", "local_claim"),
            "claim_id": claim.get("claim_id", ""),
            "title": claim.get("title") or claim.get("claim_group") or "候选科学发现",
            "claim": claim.get("claim", ""),
            "evidence": evidence_text,
            "evidence_chain": evidence_chain[:6],
            "interpretation": claim.get("interpretation", ""),
            "confidence": claim.get("confidence", "medium"),
            "confidence_reason": claim.get("confidence_reason", ""),
            "interpretation_level": claim.get("interpretation_level", ""),
            "caveat": caveat,
            "next_validation": claim.get("next_validation", [])[:3],
            "rag_binding_status": claim.get("rag_binding_status", "not_available"),
            "rag_support": (claim.get("rag_support") or [])[:3],
            "rag_interpretation": claim.get("rag_interpretation", {}),
        })
    return discoveries


def _reference_label(item: Dict[str, Any]) -> str:
    return str(item.get("title") or item.get("source") or item.get("paper_id") or item.get("stage") or "reference")


def render_major_claims_markdown(major_claims: List[Dict[str, Any]]) -> List[str]:
    if not major_claims:
        return []
    lines = ["", "## Major Claims"]
    for idx, claim in enumerate(major_claims, start=1):
        lines.extend([
            "",
            f"### {idx}. {claim.get('title') or claim.get('claim', '')}",
            f"Claim ID: {claim.get('claim_id', '')}",
            f"Claim: {claim.get('claim', '')}",
            f"Confidence: {claim.get('confidence', '')}",
            f"Confidence reason: {claim.get('confidence_reason', '')}",
            f"Interpretation level: {claim.get('interpretation_level', '')}",
        ])
        if claim.get("interpretation"):
            lines.append(f"Interpretation: {claim.get('interpretation')}")
        if claim.get("caveat"):
            lines.append(f"Caveat: {claim.get('caveat')}")
        evidence = claim.get("local_evidence") or []
        if evidence:
            lines.append("Local evidence:")
            for item in evidence[:4]:
                lines.append(f"- [{item.get('stage', '')}] {item.get('statement', '')}")
        limitations = claim.get("limitations") or []
        if limitations:
            lines.append("Limitations:")
            for item in limitations[:3]:
                lines.append(f"- {item}")
        rag_interpretation = claim.get("rag_interpretation") or {}
        claim_support = rag_interpretation.get("claim_support") or []
        method_reference = rag_interpretation.get("method_reference") or []
        biological_context = rag_interpretation.get("biological_context") or []
        if claim_support or method_reference or biological_context:
            lines.append("RAG interpretation enhancement:")
            if claim_support:
                lines.append("Literature support for this claim:")
                for item in claim_support[:3]:
                    lines.append(f"- {_reference_label(item)}")
            if method_reference:
                lines.append("Methodology reference:")
                for item in method_reference[:3]:
                    lines.append(f"- {_reference_label(item)}")
            if biological_context:
                lines.append("Biological background:")
                for item in biological_context[:3]:
                    lines.append(f"- {_reference_label(item)}")
            if rag_interpretation.get("boundary_note"):
                lines.append(f"RAG boundary: {rag_interpretation.get('boundary_note')}")
            if rag_interpretation.get("suggested_wording"):
                lines.append(f"Suggested wording: {rag_interpretation.get('suggested_wording')}")
        next_validation = claim.get("next_validation") or []
        if next_validation:
            lines.append("Next validation:")
            for item in next_validation[:3]:
                lines.append(f"- {item}")
    return lines
