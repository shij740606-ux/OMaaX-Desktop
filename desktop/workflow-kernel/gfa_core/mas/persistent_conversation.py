"""Durable conversation history and goal-ledger updates for the main Agent."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from gfa_core.mas.agent_state_repository import SQLiteAgentStateRepository
from gfa_core.mas.workflow_models import CurrentRecommendation, GoalLedger


_INTERACTION_KINDS = {
    "supplement",
    "correction",
    "switch",
    "cancel",
    "status",
    "independent",
}


def is_short_acknowledgement(text: str) -> bool:
    normalized = re.sub(r"[\s，,。.!！?？；;：:]+", "", str(text or "").lower())
    return normalized in {
        "同意",
        "确认",
        "可以",
        "好的",
        "好",
        "是",
        "是的",
        "继续",
        "开始吧",
        "执行吧",
    }


@dataclass(frozen=True)
class ConversationTurn:
    conversation_id: str
    job_id: str
    query: str
    history: tuple[dict[str, str], ...]
    goal_ledger: GoalLedger


def _conversation_id(session_id: str) -> str:
    value = str(session_id or "").strip()
    if not value:
        return str(uuid4())
    try:
        parsed = UUID(value)
    except ValueError:
        return str(uuid5(NAMESPACE_URL, f"plantai-session:{value}"))
    return str(parsed) if str(parsed) == value else str(
        uuid5(NAMESPACE_URL, f"plantai-session:{value}")
    )


def _history(items: Any) -> tuple[dict[str, str], ...]:
    normalized: list[dict[str, str]] = []
    for item in items or ():
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        content = str(item.get("content") or "")
        if content:
            normalized.append({"role": item["role"], "content": content})
    return tuple(normalized)


class PersistentConversationStore:
    def __init__(self, repository: SQLiteAgentStateRepository) -> None:
        self.repository = repository

    def resolve_job_binding(self, *, session_id: str, requested_job_id: str) -> str:
        """Return the one job owned by a conversation, rejecting mixed jobs."""
        conversation_id = _conversation_id(session_id)
        conversation = self.repository.get_conversation(conversation_id)
        requested = str(requested_job_id or "").strip()
        if conversation is None:
            return requested
        ledger = self.repository.get_goal_ledger(conversation_id)
        conversation_job = str(conversation.job_id or "").strip()
        ledger_job = str(
            (ledger.user_terms if ledger is not None else {}).get("active_job_id")
            or ""
        ).strip()
        persisted_jobs = {value for value in (conversation_job, ledger_job) if value}
        if len(persisted_jobs) > 1:
            raise ValueError("conversation job binding conflict")
        bound = next(iter(persisted_jobs), "")
        if requested and bound and requested != bound:
            raise ValueError("conversation job binding conflict")
        return bound or requested

    def rebind_job(self, *, session_id: str, job_id: str) -> str:
        """Bind one conversation to one job without mixing histories."""
        conversation_id = _conversation_id(session_id)
        requested = str(job_id or "").strip()
        if not requested:
            raise ValueError("job_id is required")
        conversation = self.repository.get_conversation(conversation_id)
        if conversation is None:
            raise ValueError("conversation is missing")
        previous_job = str(conversation.job_id or "")
        current = self.repository.get_goal_ledger(conversation_id)
        ledger_job = str(
            (current.user_terms if current is not None else {}).get("active_job_id")
            or ""
        ).strip()
        bound_job = previous_job or ledger_job
        if bound_job == requested:
            return conversation_id
        if bound_job:
            replacement_id = str(uuid4())
            self.repository.create_conversation(
                conversation_id=replacement_id,
                principal_id=conversation.principal_id,
                job_id=requested,
            )
            self.repository.put_goal_ledger(
                GoalLedger(
                    conversation_id=replacement_id,
                    revision=0,
                    user_terms={"active_job_id": requested},
                ),
                expected_revision=None,
            )
            return replacement_id
        self.repository.update_conversation(
            conversation_id=conversation_id,
            principal_id=conversation.principal_id,
            job_id=requested,
        )
        if current is None:
            return conversation_id
        replacement = GoalLedger(
            conversation_id=conversation_id,
            revision=current.revision + 1,
            user_terms={"active_job_id": requested},
        )
        try:
            self.repository.put_goal_ledger(
                replacement,
                expected_revision=current.revision,
            )
        except Exception:
            self.repository.update_conversation(
                conversation_id=conversation_id,
                principal_id=conversation.principal_id,
                job_id=previous_job,
            )
            raise
        return conversation_id

    def begin_turn(
        self,
        *,
        session_id: str,
        job_id: str,
        query: str,
        client_history: Any,
    ) -> ConversationTurn:
        conversation_id = _conversation_id(session_id)
        conversation = self.repository.get_conversation(conversation_id)
        if conversation is None:
            conversation = self.repository.create_conversation(
                conversation_id=conversation_id,
                principal_id="web-session",
                job_id=str(job_id or ""),
            )
        ledger = self.repository.get_goal_ledger(conversation_id)
        if ledger is None:
            initial_terms = {"active_job_id": job_id} if job_id else {}
            ledger = self.repository.put_goal_ledger(
                GoalLedger(
                    conversation_id=conversation_id,
                    revision=0,
                    user_terms=initial_terms,
                ),
                expected_revision=None,
            )
        effective_job_id = self.resolve_job_binding(
            session_id=conversation_id,
            requested_job_id=job_id,
        )
        if effective_job_id and not conversation.job_id:
            conversation = self.repository.update_conversation(
                conversation_id=conversation_id,
                principal_id=conversation.principal_id,
                job_id=effective_job_id,
            )
        if effective_job_id and not ledger.user_terms.get("active_job_id"):
            serialized = ledger.model_dump(mode="json")
            user_terms = dict(serialized["user_terms"])
            user_terms["active_job_id"] = effective_job_id
            ledger = self.repository.put_goal_ledger(
                GoalLedger(
                    **{
                        **serialized,
                        "revision": ledger.revision + 1,
                        "user_terms": user_terms,
                    }
                ),
                expected_revision=ledger.revision,
            )
        stored_messages = self.repository.list_messages(conversation_id)
        if not stored_messages:
            for item in _history(client_history):
                persisted = self.repository.append_message(
                    conversation_id=conversation_id,
                    message_id=str(uuid4()),
                    role=item["role"],
                    content=item["content"],
                    expected_revision=conversation.revision,
                )
                conversation = self.repository.get_conversation(conversation_id)
                assert conversation is not None
                assert conversation.revision == persisted.revision
            stored_messages = self.repository.list_messages(conversation_id)
        current_user_already_saved = bool(
            stored_messages
            and stored_messages[-1].role == "user"
            and stored_messages[-1].content == query
        )
        history_messages = (
            stored_messages[:-1]
            if current_user_already_saved
            else stored_messages
        )
        prior_history = tuple(
            {"role": item.role, "content": item.content}
            for item in history_messages
            if item.role in {"user", "assistant"}
        )
        if not current_user_already_saved:
            self.repository.append_message(
                conversation_id=conversation_id,
                message_id=str(uuid4()),
                role="user",
                content=query,
                expected_revision=conversation.revision,
            )
        return ConversationTurn(
            conversation_id=conversation_id,
            job_id=effective_job_id,
            query=query,
            history=prior_history,
            goal_ledger=ledger,
        )

    def finish_turn(
        self,
        turn: ConversationTurn,
        *,
        assistant_message: str,
        interaction_kind: str,
        action: str | None,
        intent: dict[str, Any] | None = None,
        proposal: dict[str, str] | None = None,
        recommendation: Mapping[str, Any] | None = None,
    ) -> GoalLedger:
        if interaction_kind not in _INTERACTION_KINDS:
            raise ValueError("interaction_kind is invalid")
        conversation = self.repository.get_conversation(turn.conversation_id)
        if conversation is None:
            raise ValueError("conversation is missing")
        self.repository.append_message(
            conversation_id=turn.conversation_id,
            message_id=str(uuid4()),
            role="assistant",
            content=assistant_message,
            expected_revision=conversation.revision,
        )

        current = self.repository.get_goal_ledger(turn.conversation_id)
        if current is None:
            raise ValueError("goal ledger is missing")
        active_goal = current.active_goal
        queued_goals = list(current.queued_goals)
        corrections = list(current.corrections)
        prior_terms = dict(current.user_terms)
        pending_proposal = prior_terms.get("pending_proposal")
        pending_proposal = (
            dict(pending_proposal)
            if isinstance(pending_proposal, Mapping)
            else None
        )
        confirmed_pending_proposal = bool(
            action == "propose_analysis"
            and pending_proposal is not None
            and str(pending_proposal.get("job_id") or turn.job_id)
            == turn.job_id
            and isinstance(proposal, Mapping)
            and str(proposal.get("proposal_id") or "")
            == str(pending_proposal.get("proposal_id") or "")
        )
        prior_stages = {
            str(stage)
            for stage in prior_terms.get("requested_stages", ())
            if isinstance(stage, str) and stage.strip()
        }
        current_stages = {
            str(stage)
            for stage in ((intent or {}).get("stages") or ())
            if isinstance(stage, str) and stage.strip()
        }
        prior_family = str(prior_terms.get("gene_family") or "").strip()
        current_family = str((intent or {}).get("gene_family") or "").strip()
        is_current_phase = bool(
            active_goal
            and action == "propose_analysis"
            and current_stages
            and prior_stages
            and current_stages < prior_stages
            and not (
                prior_family
                and current_family
                and prior_family != current_family
            )
        )
        if interaction_kind == "cancel":
            active_goal = None
        elif interaction_kind == "correction":
            active_goal = turn.query
            if turn.query not in corrections:
                corrections.append(turn.query)
        elif interaction_kind == "switch":
            active_goal = turn.query
        elif interaction_kind == "independent" and action in {
            "propose_analysis",
            "propose_utility",
        } and not is_current_phase and not (
            active_goal and is_short_acknowledgement(turn.query)
        ):
            if active_goal and active_goal != turn.query and active_goal not in queued_goals:
                queued_goals.append(active_goal)
            active_goal = turn.query
        elif (
            interaction_kind == "supplement"
            and active_goal is None
            and action in {"propose_analysis", "propose_utility"}
        ):
            active_goal = (
                str(pending_proposal.get("request") or turn.query)
                if pending_proposal
                and (
                    is_short_acknowledgement(turn.query)
                    or confirmed_pending_proposal
                )
                else turn.query
            )
        if active_goal in queued_goals:
            queued_goals.remove(active_goal)

        serialized = current.model_dump(mode="json")
        user_terms = dict(serialized["user_terms"])
        if turn.job_id:
            user_terms["active_job_id"] = turn.job_id
        if action == "show_result":
            if recommendation is None:
                user_terms.pop("current_recommendation", None)
            else:
                current_recommendation = CurrentRecommendation.model_validate(
                    recommendation
                )
                if current_recommendation.job_id != turn.job_id:
                    raise ValueError("recommendation job binding conflict")
                user_terms["current_recommendation"] = (
                    current_recommendation.model_dump(mode="json", exclude_none=True)
                )
        if interaction_kind in {"cancel", "correction", "switch"}:
            user_terms.pop("current_recommendation", None)
        if action == "clarify" and isinstance(proposal, dict):
            normalized_proposal = {
                key: str(proposal.get(key) or "").strip()
                for key in ("proposal_id", "request", "subject")
            }
            if all(normalized_proposal.values()):
                original_request = str(
                    proposal.get("original_request") or ""
                ).strip()
                if original_request:
                    normalized_proposal["original_request"] = original_request
                workflow_id = str(proposal.get("workflow_id") or "").strip()
                if workflow_id in {
                    "data_preparation",
                    "gene_family",
                    "rnaseq_whole_genome",
                    "rnaseq_family_member",
                }:
                    normalized_proposal["workflow_id"] = workflow_id
                action_binding = {
                    key: str(proposal.get(key) or "").strip()
                    for key in (
                        "workflow_action_id",
                        "authorized_stages",
                        "target_stage",
                    )
                }
                if all(action_binding.values()):
                    normalized_proposal.update(action_binding)
                normalized_proposal["job_id"] = turn.job_id
                user_terms["pending_proposal"] = normalized_proposal
        if (
            action == "propose_analysis"
            and (
                is_short_acknowledgement(turn.query)
                or confirmed_pending_proposal
            )
            and pending_proposal is not None
            and str(pending_proposal.get("job_id") or turn.job_id)
            == turn.job_id
        ) or interaction_kind in {"cancel", "correction", "switch"}:
            user_terms.pop("pending_proposal", None)
        if (
            action == "clarify"
            or is_current_phase
            or (
                action == "propose_analysis"
                and interaction_kind == "supplement"
                and active_goal is not None
            )
        ):
            user_terms["current_action"] = turn.query
        if isinstance(intent, dict):
            scalar_fields = {
                "gene_family": ("gene_family",),
                "species": ("species",),
                "scope_mode": ("scope_mode", "scopeMode"),
                "primary_stage": ("primary_stage", "stage"),
                "task_kind": ("task_kind", "taskType"),
                "prefix": ("prefix",),
                "rna_route": ("rna_route", "rnaRoute", "analysis_route"),
                "foreground_mode": ("foreground_mode", "foregroundMode"),
            }
            for target, sources in scalar_fields.items():
                value = next(
                    (
                        intent.get(source)
                        for source in sources
                        if isinstance(intent.get(source), str)
                        and intent[source].strip()
                    ),
                    None,
                )
                if value is not None:
                    user_terms[target] = value

            sequence_fields = {
                "target_species": ("target_species", "targetSpecies"),
                "reference_species": ("reference_species", "referenceSpecies"),
                "comparative_species": (
                    "comparative_species",
                    "comparativeSpecies",
                ),
                "requested_stages": ("stages",),
            }
            params = intent.get("params")
            params = params if isinstance(params, dict) else {}
            for target, sources in sequence_fields.items():
                raw = next(
                    (
                        intent.get(source, params.get(source))
                        for source in sources
                        if isinstance(intent.get(source, params.get(source)), (list, tuple))
                    ),
                    None,
                )
                if (
                    raw is not None
                    and (
                        target == "requested_stages"
                        or bool(raw)
                        or target not in user_terms
                    )
                    and all(
                    isinstance(item, str) and item.strip() for item in raw
                    )
                ):
                    user_terms[target] = list(raw)
        changed = (
            active_goal != current.active_goal
            or tuple(queued_goals) != current.queued_goals
            or tuple(corrections) != current.corrections
            or user_terms != dict(current.user_terms)
        )
        if not changed:
            return current
        updated = GoalLedger(
            **{
                **serialized,
                "revision": current.revision + 1,
                "active_goal": active_goal,
                "queued_goals": tuple(queued_goals),
                "corrections": tuple(corrections),
                "user_terms": user_terms,
            }
        )
        return self.repository.put_goal_ledger(
            updated,
            expected_revision=current.revision,
        )

    def record_config_confirmation(
        self,
        *,
        session_id: str,
        job_id: str,
        evidence_id: str,
    ) -> GoalLedger | None:
        """Attach a confirmed config to an existing conversation goal."""
        conversation_id = _conversation_id(session_id)
        if self.repository.get_conversation(conversation_id) is None:
            return None
        bound_job_id = self.resolve_job_binding(
            session_id=conversation_id,
            requested_job_id=job_id,
        )
        current = self.repository.get_goal_ledger(conversation_id)
        if current is None:
            return None

        approved_decisions = list(current.approved_decisions)
        if "config.xlsx 已确认" not in approved_decisions:
            approved_decisions.append("config.xlsx 已确认")
        evidence_ids = list(current.evidence_ids)
        if evidence_id not in evidence_ids:
            evidence_ids.append(evidence_id)
        serialized = current.model_dump(mode="json")
        user_terms = dict(serialized["user_terms"])
        user_terms["config_confirmed"] = True
        if bound_job_id:
            user_terms["active_job_id"] = bound_job_id
        unresolved_blockers = tuple(
            blocker
            for blocker in current.unresolved_blockers
            if "config" not in blocker.lower() and "配置" not in blocker
        )

        updated = GoalLedger(
            **{
                **serialized,
                "revision": current.revision + 1,
                "approved_decisions": tuple(approved_decisions),
                "unresolved_blockers": unresolved_blockers,
                "user_terms": user_terms,
                "evidence_ids": tuple(evidence_ids),
            }
        )
        return self.repository.put_goal_ledger(
            updated,
            expected_revision=current.revision,
        )
