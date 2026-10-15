"""Shared schema helpers for claim-level interpretation."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List


CONFIDENCE_ORDER = ["rejected", "background", "low", "medium", "high"]


def downgrade_confidence(confidence: str, steps: int = 1) -> str:
    value = confidence if confidence in CONFIDENCE_ORDER else "medium"
    idx = max(0, CONFIDENCE_ORDER.index(value) - max(0, steps))
    return CONFIDENCE_ORDER[idx]


def normalize_evidence_cards(cards: Iterable[Dict[str, Any]], *, limit: int = 6) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for idx, card in enumerate(cards, start=1):
        if len(normalized) >= limit:
            break
        if not isinstance(card, dict):
            continue
        normalized.append({
            "evidence_id": card.get("evidence_id") or f"evidence.{idx:03d}",
            "stage": card.get("stage", ""),
            "claim": card.get("claim", ""),
            "statement": card.get("evidence", ""),
            "source_type": card.get("source_type", ""),
            "source_file": card.get("source_file", ""),
            "confidence": card.get("confidence", ""),
            "interpretation_level": card.get("interpretation_level", "fact"),
        })
    return normalized


def evidence_for_stages(
    evidence_cards: Iterable[Dict[str, Any]],
    stages: Iterable[str],
    *,
    limit: int = 6,
) -> List[Dict[str, Any]]:
    wanted = {stage for stage in stages if stage}
    return normalize_evidence_cards(
        (card for card in evidence_cards if isinstance(card, dict) and card.get("stage") in wanted),
        limit=limit,
    )


def risks_for_stages(risk_flags: Iterable[Dict[str, Any]], stages: Iterable[str]) -> List[Dict[str, Any]]:
    wanted = {stage for stage in stages if stage}
    risks: List[Dict[str, Any]] = []
    for risk in risk_flags:
        if isinstance(risk, dict) and risk.get("stage") in wanted:
            risks.append({
                "stage": risk.get("stage", ""),
                "severity": risk.get("severity", ""),
                "message": risk.get("message", ""),
                "source_file": risk.get("source_file", ""),
            })
    return risks


def make_claim(
    *,
    claim_id: str,
    claim_group: str,
    claim_type: str,
    claim: str,
    stages: List[str],
    local_evidence: List[Dict[str, Any]],
    matched_patterns: List[str],
    confidence: str = "medium",
    confidence_reason: str = "",
    limitations: List[str] | None = None,
    next_validation: List[str] | None = None,
    counter_evidence: List[Dict[str, Any]] | None = None,
    interpretation_level: str = "candidate_hypothesis",
) -> Dict[str, Any]:
    return {
        "claim_id": claim_id,
        "claim_group": claim_group,
        "claim_type": claim_type,
        "claim": claim,
        "stages": stages,
        "created_from": stages,
        "local_evidence": local_evidence,
        "counter_evidence": counter_evidence or [],
        "matched_patterns": matched_patterns,
        "rag_support": [],
        "limitations": limitations or [],
        "confidence": confidence,
        "confidence_reason": confidence_reason,
        "interpretation_level": interpretation_level,
        "next_validation": next_validation or [],
        "status": "active",
    }
