"""Read-only catalog for the four canonical PlantAI workflow contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import UUID

import yaml

from gfa_core.mas.workflow_contracts import (
    ActionRegistry,
    PredicateRegistry,
    WorkflowContract,
    load_workflow_contract,
)


_WORKFLOW_FILES = MappingProxyType(
    {
        "data_preparation": "data_preparation_v1.yaml",
        "gene_family": "gene_family_v1.yaml",
        "rnaseq_family_member": "rnaseq_family_member_v1.yaml",
        "rnaseq_whole_genome": "rnaseq_whole_genome_v1.yaml",
    }
)

_RNASEQ_BUSINESS_CONTEXT_FILE = "rnaseq_business_context_v1.yaml"
_RNASEQ_WORKFLOW_MODES = MappingProxyType(
    {
        "rnaseq_whole_genome": "whole_genome",
        "rnaseq_family_member": "family_member",
    }
)


@dataclass(frozen=True, slots=True)
class CrossContractProducerBinding:
    consumer_workflow_id: str
    consumer_requirement_id: str
    producer_workflow_id: str
    producer_transition_id: str
    producer_requirement_id: str


CROSS_CONTRACT_PRODUCERS = (
    CrossContractProducerBinding(
        consumer_workflow_id="gene_family",
        consumer_requirement_id="genome_prepared",
        producer_workflow_id="data_preparation",
        producer_transition_id="prepare_genome",
        producer_requirement_id="genome_prepared",
    ),
    CrossContractProducerBinding(
        consumer_workflow_id="rnaseq_family_member",
        consumer_requirement_id="confirmed_family",
        producer_workflow_id="gene_family",
        producer_transition_id="confirm_subfamilies",
        producer_requirement_id="confirmed_family",
    ),
    CrossContractProducerBinding(
        consumer_workflow_id="rnaseq_family_member",
        consumer_requirement_id="shared_whole_genome_de",
        producer_workflow_id="rnaseq_whole_genome",
        producer_transition_id="run_rnaseq_de",
        producer_requirement_id="shared_de",
    ),
    CrossContractProducerBinding(
        consumer_workflow_id="rnaseq_whole_genome",
        consumer_requirement_id="config_confirmed",
        producer_workflow_id="data_preparation",
        producer_transition_id="confirm_config",
        producer_requirement_id="config_confirmed",
    ),
    CrossContractProducerBinding(
        consumer_workflow_id="rnaseq_whole_genome",
        consumer_requirement_id="genome_prepared",
        producer_workflow_id="data_preparation",
        producer_transition_id="prepare_genome",
        producer_requirement_id="genome_prepared",
    ),
)


def _context_binding_is_complete(context: Any) -> bool:
    string_fields = (
        "active_job_id",
        "workflow_id",
        "contract_digest",
        "action_id",
        "current_state_id",
        "target_state_id",
        "payload_sha256",
        "dependency_fingerprint_digest",
    )
    return bool(
        all(
            isinstance(getattr(context, field_name, None), str)
            and getattr(context, field_name)
            for field_name in string_fields
        )
        and isinstance(getattr(context, "pending_action_id", None), UUID)
        and context.pending_issued_at <= context.evaluated_at < context.pending_expires_at
    )


def _declarative_action_not_executable(*_args: Any, **_kwargs: Any) -> None:
    raise RuntimeError("canonical workflow catalog does not execute actions")


PREDICATE_REGISTRY = PredicateRegistry(
    {"context_binding_is_complete": _context_binding_is_complete}
)
ACTION_REGISTRY = ActionRegistry(
    {
        name: _declarative_action_not_executable
        for name in (
            "project_rna_facts",
            "request_config_confirmation",
            "run_combo",
            "run_genome_prepare",
            "run_identify",
            "run_reference_classification",
            "run_rnaseq_align",
            "run_rnaseq_de",
            "run_rnaseq_family_projection",
            "run_rnaseq_qc",
            "run_rnaseq_quant",
            "run_rnaseq_sample_qc",
            "run_subfamily_confirm",
        )
    }
)


def validate_cross_contract_producers(
    contracts: Mapping[str, WorkflowContract],
    bindings: tuple[CrossContractProducerBinding, ...],
) -> Mapping[tuple[str, str], CrossContractProducerBinding]:
    validated: dict[tuple[str, str], CrossContractProducerBinding] = {}
    edges: dict[str, set[str]] = {workflow_id: set() for workflow_id in contracts}
    for binding in bindings:
        key = (
            binding.consumer_workflow_id,
            binding.consumer_requirement_id,
        )
        if key in validated:
            raise ValueError(f"duplicate consumer producer binding: {key}")
        try:
            consumer = contracts[binding.consumer_workflow_id]
            producer = contracts[binding.producer_workflow_id]
        except KeyError as exc:
            raise ValueError(f"unknown workflow in producer binding: {exc.args[0]}") from exc

        consumer_requirements = {
            item.id: item for item in consumer.evidence_requirements
        }
        producer_requirements = {
            item.id: item for item in producer.evidence_requirements
        }
        if binding.consumer_requirement_id not in consumer_requirements:
            raise ValueError(
                f"unknown consumer requirement: {binding.consumer_requirement_id}"
            )
        if binding.producer_requirement_id not in producer_requirements:
            raise ValueError(
                f"unknown producer requirement: {binding.producer_requirement_id}"
            )
        transitions = {item.id: item for item in producer.transitions}
        if binding.producer_transition_id not in transitions:
            raise ValueError(
                f"unknown producer transition: {binding.producer_transition_id}"
            )
        transition = transitions[binding.producer_transition_id]
        states = {item.id: item for item in producer.states}
        source_ids = set(states[transition.from_state_id].evidence_requirement_ids)
        target_ids = set(states[transition.to_state_id].evidence_requirement_ids)
        if (
            binding.producer_requirement_id not in target_ids
            or binding.producer_requirement_id in source_ids
        ):
            raise ValueError("producer requirement is not a transition output milestone")
        consumer_requirement = consumer_requirements[binding.consumer_requirement_id]
        producer_requirement = producer_requirements[binding.producer_requirement_id]
        if (
            consumer_requirement.evidence_type,
            consumer_requirement.stage,
        ) != (
            producer_requirement.evidence_type,
            producer_requirement.stage,
        ):
            raise ValueError("cross-contract producer selector mismatch")
        actions = {item.id: item for item in producer.actions}
        if actions[transition.action_id].target_stage != producer_requirement.stage:
            raise ValueError("producer action target does not match output selector stage")
        validated[key] = binding
        edges[binding.producer_workflow_id].add(binding.consumer_workflow_id)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(workflow_id: str) -> None:
        if workflow_id in visiting:
            raise ValueError("cross-contract producer graph contains a cycle")
        if workflow_id in visited:
            return
        visiting.add(workflow_id)
        for consumer_id in edges[workflow_id]:
            visit(consumer_id)
        visiting.remove(workflow_id)
        visited.add(workflow_id)

    for workflow_id in sorted(edges):
        visit(workflow_id)
    return MappingProxyType(dict(sorted(validated.items())))


def load_canonical_workflow_contracts() -> Mapping[str, WorkflowContract]:
    workflow_dir = Path(__file__).with_name("workflows")
    contracts = {
        workflow_id: load_workflow_contract(
            (workflow_dir / filename).read_bytes(),
            predicate_registry=PREDICATE_REGISTRY,
            action_registry=ACTION_REGISTRY,
        )
        for workflow_id, filename in _WORKFLOW_FILES.items()
    }
    for workflow_id, contract in contracts.items():
        if contract.workflow_id != workflow_id:
            raise ValueError(
                f"canonical workflow resource {workflow_id} declares "
                f"{contract.workflow_id}"
            )
    validate_cross_contract_producers(contracts, CROSS_CONTRACT_PRODUCERS)
    return MappingProxyType(contracts)


def load_canonical_workflow_contract(workflow_id: str) -> WorkflowContract:
    """Load one trusted workflow contract without reading unrelated workflows."""
    try:
        filename = _WORKFLOW_FILES[workflow_id]
    except KeyError as exc:
        raise ValueError(f"unknown canonical workflow: {workflow_id}") from exc
    workflow_dir = Path(__file__).with_name("workflows")
    contract = load_workflow_contract(
        (workflow_dir / filename).read_bytes(),
        predicate_registry=PREDICATE_REGISTRY,
        action_registry=ACTION_REGISTRY,
    )
    if contract.workflow_id != workflow_id:
        raise ValueError(
            f"canonical workflow resource {workflow_id} declares "
            f"{contract.workflow_id}"
        )
    return contract


def load_rnaseq_business_context(workflow_id: str) -> dict[str, Any]:
    """Project the recorded RNA-seq semantics without exposing runtime stages."""
    try:
        mode = _RNASEQ_WORKFLOW_MODES[workflow_id]
    except KeyError as exc:
        raise ValueError(f"not an RNA-seq workflow: {workflow_id}") from exc

    workflow_dir = Path(__file__).with_name("workflows")
    payload = yaml.safe_load(
        (workflow_dir / _RNASEQ_BUSINESS_CONTEXT_FILE).read_text(encoding="utf-8")
    )
    metadata = payload["record_metadata"]
    contract = payload["contract"]
    designs = payload["design_selection"]
    enabled_designs = [
        design_id
        for design_id in ("M1", "M2", "M3", "M4")
        if designs.get(design_id, {}).get("enabled", True)
    ]
    disabled_designs = [
        design_id
        for design_id in ("M1", "M2", "M3", "M4")
        if not designs.get(design_id, {}).get("enabled", True)
    ]
    return {
        "source": _RNASEQ_BUSINESS_CONTEXT_FILE,
        "status": metadata["status"],
        "runtime_authority": metadata["runtime_authority"],
        "purpose": metadata["purpose"],
        "authoritative_inputs": contract["authoritative_inputs"],
        "ownership": contract["ownership"],
        "analysis_mode": {
            "mode": mode,
            **payload["analysis_modes"][mode],
        },
        "samples_yaml": payload["samples_yaml"],
        "design_policy": designs["policy"],
        "enabled_designs": enabled_designs,
        "disabled_designs": disabled_designs,
        "design_definitions": {
            design_id: designs[design_id] for design_id in enabled_designs
        },
        "safe_fallback": designs["safe_fallback"],
        "batch_policy": payload["batch_policy"],
        "result_semantics": payload["result_semantics"],
        "runtime_mapping": payload["runtime_mapping"],
    }


__all__ = [
    "ACTION_REGISTRY",
    "CROSS_CONTRACT_PRODUCERS",
    "PREDICATE_REGISTRY",
    "CrossContractProducerBinding",
    "load_canonical_workflow_contract",
    "load_canonical_workflow_contracts",
    "load_rnaseq_business_context",
    "validate_cross_contract_producers",
]
