"""Bind user confirmation to one locked fact set and semantic candidate."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .semantic_model import SemanticCandidateError, validate_semantic_candidate


SEMANTIC_REVISION_TARGETS = {
    "semantic",
    "function_summary",
    "inputs.role",
    "inputs.description",
    "inputs.replaceability",
    "parameters.display_name",
    "parameters.description",
    "outputs.description",
    "outputs.result_role",
}
FACT_DISPUTE_TARGETS = {
    "execution_facts.original_command",
    "execution_facts.command",
    "execution_facts.path",
    "execution_facts.flag",
    "execution_facts.value",
    "execution_facts.default_value",
    "execution_facts.observed_output",
}


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _applied_revision_diff(
    repair_history: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    return [
        deepcopy(change)
        for attempt in repair_history or []
        if attempt.get("status") == "success"
        for change in attempt.get("diff", [])
    ]


def create_confirmation(
    *,
    execution_facts: dict[str, Any],
    candidate: dict[str, Any],
    repair_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a pending decision bound to the current facts and candidate."""

    errors = validate_semantic_candidate(execution_facts, candidate)
    if errors:
        raise SemanticCandidateError(errors)
    facts_sha256 = execution_facts["facts_sha256"]
    candidate_sha256 = _sha256_json(candidate)
    binding_sha256 = _sha256_json(
        {
            "facts_sha256": facts_sha256,
            "candidate_sha256": candidate_sha256,
        }
    )
    return {
        "confirmation_id": f"confirmation_{binding_sha256[:16]}",
        "status": "pending",
        "accepted": False,
        "facts_sha256": facts_sha256,
        "candidate_sha256": candidate_sha256,
        "candidate_version": f"semantic_{candidate_sha256[:16]}",
        "summary": {
            "function_summary": candidate["function_summary"],
            "input_count": len(candidate["inputs"]),
            "parameter_count": len(candidate["parameters"]),
            "output_count": len(candidate["outputs"]),
            "revision_diff": _applied_revision_diff(repair_history),
        },
    }


def _stale_reason(
    *,
    confirmation: dict[str, Any],
    execution_facts: dict[str, Any],
    candidate: dict[str, Any],
) -> str | None:
    if confirmation.get("facts_sha256") != execution_facts.get("facts_sha256"):
        return "facts_changed"
    if confirmation.get("candidate_sha256") != _sha256_json(candidate):
        return "candidate_changed"
    return None


def apply_confirmation_action(
    *,
    confirmation: dict[str, Any],
    action: str,
    execution_facts: dict[str, Any],
    candidate: dict[str, Any],
    feedback: str | None = None,
    revision_target: str | None = None,
) -> dict[str, Any]:
    """Apply one user decision without mutating the supplied confirmation."""

    result = deepcopy(confirmation)
    result["accepted"] = False
    stale_reason = _stale_reason(
        confirmation=confirmation,
        execution_facts=execution_facts,
        candidate=candidate,
    )
    if stale_reason:
        result["status"] = "stale"
        result["stale_reason"] = stale_reason
        return result

    if action == "confirm":
        result["status"] = "confirmed"
        result["accepted"] = True
        return result
    if action == "cancel":
        result["status"] = "cancelled"
        return result
    if action != "revise":
        raise ValueError(f"unknown confirmation action: {action}")

    normalized_feedback = (feedback or "").strip()
    if not normalized_feedback:
        raise ValueError("revise requires non-empty feedback")
    if revision_target in SEMANTIC_REVISION_TARGETS:
        result.update(
            {
                "status": "revision_requested",
                "route": "semantic_repair",
                "feedback": normalized_feedback,
                "revision_target": revision_target,
            }
        )
        return result
    if revision_target in FACT_DISPUTE_TARGETS:
        result.update(
            {
                "status": "fact_dispute",
                "route": "recollect_execution_evidence",
                "feedback": normalized_feedback,
                "revision_target": revision_target,
            }
        )
        return result
    raise ValueError(f"unknown revision target: {revision_target}")


def is_candidate_accepted(
    *,
    confirmation: dict[str, Any],
    execution_facts: dict[str, Any],
    candidate: dict[str, Any],
) -> bool:
    """Return true only for a confirmed decision with an unchanged binding."""

    return (
        confirmation.get("status") == "confirmed"
        and confirmation.get("accepted") is True
        and _stale_reason(
            confirmation=confirmation,
            execution_facts=execution_facts,
            candidate=candidate,
        )
        is None
    )
