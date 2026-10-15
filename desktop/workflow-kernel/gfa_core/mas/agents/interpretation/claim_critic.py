"""Claim confidence and boundary checks."""
from __future__ import annotations

from typing import Any, Dict

from .claim_schema import downgrade_confidence


def critique_claim(claim: Dict[str, Any]) -> Dict[str, Any]:
    claim = dict(claim)
    local_evidence = claim.get("local_evidence") or []
    counter_evidence = claim.get("counter_evidence") or []
    limitations = list(claim.get("limitations") or [])
    confidence = claim.get("confidence", "medium")
    status = claim.get("status", "active")

    if not local_evidence:
        status = "rejected"
        confidence = "rejected"
        limitations.append("未绑定本地证据，不能进入本项目主要发现。")

    if any(risk.get("severity") == "high" for risk in counter_evidence if isinstance(risk, dict)):
        confidence = downgrade_confidence(confidence, 1)
        limitations.append("存在high级风险，结论可信度已自动降级。")

    weak_levels = {"derived_stat", "candidate_hypothesis", "statistical_finding"}
    text = str(claim.get("claim", ""))
    if any(word in text for word in ("证明", "调控", "导致")) and claim.get("interpretation_level") in weak_levels:
        confidence = downgrade_confidence(confidence, 1)
        limitations.append("当前证据不足以支撑强机制动词，需改为候选或一致性表述。")

    claim["confidence"] = confidence
    claim["status"] = status
    claim["limitations"] = list(dict.fromkeys(item for item in limitations if item))
    return claim
