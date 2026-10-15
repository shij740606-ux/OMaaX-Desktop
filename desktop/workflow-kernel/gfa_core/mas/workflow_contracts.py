"""Strict, bounded loading for declarative workflow contracts.

This leaf module validates names against fixed Python registries.  It never
imports, evaluates, or invokes contract-authored values.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Annotated, Any, ClassVar, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)
from yaml.constructor import ConstructorError
from yaml.events import (
    AliasEvent,
    CollectionEndEvent,
    CollectionStartEvent,
    ScalarEvent,
)
from yaml.nodes import MappingNode

from gfa_core.mas.workflow_models import EvidenceKind


_SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_SEMVER_RE = r"^[0-9]+\.[0-9]+\.[0-9]+$"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

SafeName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=_SAFE_NAME_RE.pattern),
]
SemanticVersion = Annotated[
    str,
    StringConstraints(min_length=5, max_length=32, pattern=_SEMVER_RE),
]


class ContractLoadError(ValueError):
    """Raised when untrusted contract text cannot be loaded safely."""


@dataclass(frozen=True)
class ContractLimits:
    max_bytes: int = 64 * 1024
    max_depth: int = 12
    max_items: int = 512
    max_string_length: int = 256

    def __post_init__(self) -> None:
        for field_name in (
            "max_bytes",
            "max_depth",
            "max_items",
            "max_string_length",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")


class _CallableRegistry:
    __slots__ = ("_handlers",)

    kind: ClassVar[str]

    def __setattr__(self, name: str, value: Any) -> None:
        if hasattr(self, name):
            raise AttributeError(f"{self.kind} registry is fixed")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{self.kind} registry is fixed")

    def __init__(self, handlers: Mapping[str, Callable[..., Any]]) -> None:
        copied: dict[str, Callable[..., Any]] = {}
        for name, handler in handlers.items():
            if not isinstance(name, str) or not _SAFE_NAME_RE.fullmatch(name):
                raise ValueError(f"{self.kind} registry name must be a safe name")
            if not callable(handler):
                raise TypeError(f"{self.kind} registry values must be callable")
            copied[name] = handler
        self._handlers = MappingProxyType(copied)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    def resolve(self, name: str) -> Callable[..., Any]:
        try:
            return self._handlers[name]
        except KeyError as exc:
            raise KeyError(f"unknown {self.kind}: {name}") from exc


class PredicateRegistry(_CallableRegistry):
    __slots__ = ()
    kind = "predicate"


class ActionRegistry(_CallableRegistry):
    __slots__ = ()
    kind = "action"


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> "_ContractModel":
        if not update:
            return super().model_copy(deep=deep)
        data = self.model_dump(mode="python", round_trip=True)
        data.update(dict(update))
        return type(self).model_validate(data)


def _sorted_unique(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must contain unique IDs")
    return tuple(sorted(values))


class StateContract(_ContractModel):
    id: SafeName
    terminal: StrictBool = False
    evidence_requirement_ids: tuple[SafeName, ...] = ()

    @field_validator("evidence_requirement_ids")
    @classmethod
    def normalize_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique(values, "evidence_requirement_ids")


class ResourcePolicy(str, Enum):
    JOB_EXCLUSIVE = "job_exclusive"
    TARGET_STAGE = "target_stage"
    RNA_EXPERIMENT = "rna_experiment"
    FAMILY_PROJECTION = "family_projection"


class ActionContract(_ContractModel):
    id: SafeName
    handler: SafeName
    dependency_kind: SafeName
    resource_policy: ResourcePolicy
    target_stage: SafeName


class PrerequisiteContract(_ContractModel):
    id: SafeName
    predicate: SafeName


class EvidenceRequirementContract(_ContractModel):
    id: SafeName
    evidence_type: EvidenceKind
    stage: SafeName

    @model_validator(mode="after")
    def validate_rna_fact_selector(self) -> "EvidenceRequirementContract":
        fact_kinds = {
            EvidenceKind.RNA_EXPERIMENT_INPUT,
            EvidenceKind.RNA_ALIGNMENT_STRANDNESS,
            EvidenceKind.RNA_DE_DESIGN,
            EvidenceKind.RNA_GENOME_RESOURCE,
        }
        if self.evidence_type in fact_kinds:
            if self.stage != "rna_fact_projection":
                raise ValueError(
                    "RNA fact selectors require stage=rna_fact_projection"
                )
        elif self.stage == "rna_fact_projection":
            raise ValueError(
                "rna_fact_projection stage is reserved for fixed RNA facts"
            )
        return self


class ConfirmationContract(_ContractModel):
    id: SafeName
    evidence_requirement_ids: tuple[SafeName, ...] = Field(min_length=1)

    @field_validator("evidence_requirement_ids")
    @classmethod
    def normalize_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique(values, "evidence_requirement_ids")


class InvalidationRuleContract(_ContractModel):
    id: SafeName
    trigger_evidence_id: SafeName
    invalidates_evidence_ids: tuple[SafeName, ...] = Field(min_length=1)

    @field_validator("invalidates_evidence_ids")
    @classmethod
    def normalize_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return _sorted_unique(values, "invalidates_evidence_ids")


class TransitionContract(_ContractModel):
    id: SafeName
    from_state_id: SafeName
    to_state_id: SafeName
    action_id: SafeName
    prerequisite_ids: tuple[SafeName, ...] = ()
    confirmation_ids: tuple[SafeName, ...] = ()
    evidence_requirement_ids: tuple[SafeName, ...] = ()
    invalidation_rule_ids: tuple[SafeName, ...] = ()

    @field_validator(
        "prerequisite_ids",
        "confirmation_ids",
        "evidence_requirement_ids",
        "invalidation_rule_ids",
    )
    @classmethod
    def normalize_refs(cls, values: tuple[str, ...], info) -> tuple[str, ...]:
        return _sorted_unique(values, info.field_name)


class ProhibitedLegacyTransitionContract(_ContractModel):
    id: SafeName
    from_state_id: SafeName
    to_state_id: SafeName
    legacy_action_ids: tuple[SafeName, ...] = Field(min_length=1)
    legacy_handler_names: tuple[SafeName, ...] = Field(min_length=1)

    @field_validator("legacy_action_ids", "legacy_handler_names")
    @classmethod
    def normalize_refs(cls, values: tuple[str, ...], info) -> tuple[str, ...]:
        return _sorted_unique(values, info.field_name)


def _require_unique_ids(values: tuple[Any, ...], label: str) -> None:
    ids = [value.id for value in values]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate {label} ID")


def _require_known(
    references: tuple[str, ...],
    known: set[str],
    field_name: str,
) -> None:
    missing = sorted(set(references) - known)
    if missing:
        raise ValueError(f"{field_name} has dangling references: {missing}")


class _WorkflowContractBody(_ContractModel):
    contract_version: Literal["workflow_contract_v2"]
    contract_id: str
    workflow_id: SafeName
    workflow_version: SemanticVersion
    initial_state_id: SafeName
    states: tuple[StateContract, ...] = Field(min_length=1)
    actions: tuple[ActionContract, ...] = Field(min_length=1)
    prerequisites: tuple[PrerequisiteContract, ...] = Field(min_length=1)
    confirmations: tuple[ConfirmationContract, ...] = Field(min_length=1)
    evidence_requirements: tuple[EvidenceRequirementContract, ...] = Field(min_length=1)
    invalidation_rules: tuple[InvalidationRuleContract, ...] = Field(min_length=1)
    transitions: tuple[TransitionContract, ...] = Field(min_length=1)
    prohibited_legacy_transitions: tuple[
        ProhibitedLegacyTransitionContract, ...
    ] = Field(min_length=1)

    @field_validator(
        "states",
        "actions",
        "prerequisites",
        "confirmations",
        "evidence_requirements",
        "invalidation_rules",
        "transitions",
        "prohibited_legacy_transitions",
    )
    @classmethod
    def sort_definitions(cls, values: tuple[Any, ...]) -> tuple[Any, ...]:
        return tuple(sorted(values, key=lambda value: value.id))

    @model_validator(mode="after")
    def validate_graph(self) -> "_WorkflowContractBody":
        expected_id = f"{self.workflow_id}@{self.workflow_version}"
        if self.contract_id != expected_id:
            raise ValueError(f"contract_id must be {expected_id}")

        groups = (
            (self.states, "state"),
            (self.actions, "action"),
            (self.prerequisites, "prerequisite"),
            (self.confirmations, "confirmation"),
            (self.evidence_requirements, "evidence requirement"),
            (self.invalidation_rules, "invalidation rule"),
            (self.transitions, "transition"),
            (self.prohibited_legacy_transitions, "prohibited legacy transition"),
        )
        for values, label in groups:
            _require_unique_ids(values, label)
        selector_keys = [
            (requirement.evidence_type, requirement.stage)
            for requirement in self.evidence_requirements
        ]
        if len(selector_keys) != len(set(selector_keys)):
            raise ValueError("duplicate evidence selector")

        state_ids = {value.id for value in self.states}
        action_ids = {value.id for value in self.actions}
        prerequisite_ids = {value.id for value in self.prerequisites}
        confirmation_ids = {value.id for value in self.confirmations}
        evidence_ids = {value.id for value in self.evidence_requirements}
        invalidation_ids = {value.id for value in self.invalidation_rules}
        if self.initial_state_id not in state_ids:
            raise ValueError("initial_state_id has a dangling state reference")
        for state in self.states:
            _require_known(
                state.evidence_requirement_ids,
                evidence_ids,
                "state evidence_requirement_ids",
            )

        transition_keys: set[tuple[str, str, str]] = set()
        transition_routes: set[tuple[str, str]] = set()
        for transition in self.transitions:
            if transition.from_state_id not in state_ids:
                raise ValueError("transition from_state_id has a dangling state reference")
            if transition.to_state_id not in state_ids:
                raise ValueError("transition to_state_id has a dangling state reference")
            if transition.action_id not in action_ids:
                raise ValueError("transition action_id has a dangling action reference")
            _require_known(transition.prerequisite_ids, prerequisite_ids, "prerequisite_ids")
            _require_known(transition.confirmation_ids, confirmation_ids, "confirmation_ids")
            _require_known(
                transition.evidence_requirement_ids,
                evidence_ids,
                "evidence_requirement_ids",
            )
            _require_known(
                transition.invalidation_rule_ids,
                invalidation_ids,
                "invalidation_rule_ids",
            )
            key = (
                transition.from_state_id,
                transition.to_state_id,
                transition.action_id,
            )
            if key in transition_keys:
                raise ValueError("duplicate transition edge")
            transition_keys.add(key)
            route = (transition.from_state_id, transition.action_id)
            if route in transition_routes:
                raise ValueError(
                    "from_state and action must select at most one transition target"
                )
            transition_routes.add(route)
            if transition.from_state_id != transition.to_state_id:
                states_by_id = {state.id: state for state in self.states}
                source_requirements = set(
                    states_by_id[transition.from_state_id].evidence_requirement_ids
                )
                target_requirements = set(
                    states_by_id[transition.to_state_id].evidence_requirement_ids
                )
                if not target_requirements - source_requirements:
                    raise ValueError(
                        "forward state requires at least one new authoritative "
                        "milestone"
                    )

        terminal_state_ids = {state.id for state in self.states if state.terminal}
        if not terminal_state_ids:
            raise ValueError("contract requires at least one terminal state")
        outgoing_state_ids = {transition.from_state_id for transition in self.transitions}
        if terminal_state_ids.intersection(outgoing_state_ids):
            raise ValueError("terminal states cannot have outgoing transitions")
        reachable = {self.initial_state_id}
        changed = True
        while changed:
            changed = False
            for transition in self.transitions:
                if (
                    transition.from_state_id in reachable
                    and transition.to_state_id not in reachable
                ):
                    reachable.add(transition.to_state_id)
                    changed = True
        unreachable = sorted(state_ids - reachable)
        if unreachable:
            raise ValueError(f"all states must be reachable from initial_state_id: {unreachable}")

        for confirmation in self.confirmations:
            _require_known(
                confirmation.evidence_requirement_ids,
                evidence_ids,
                "confirmation evidence_requirement_ids",
            )
        for rule in self.invalidation_rules:
            _require_known((rule.trigger_evidence_id,), evidence_ids, "invalidation evidence")
            _require_known(
                rule.invalidates_evidence_ids,
                evidence_ids,
                "invalidation evidence",
            )

        allowed_handler_names = {action.handler for action in self.actions}
        prohibited_keys: set[tuple[str, str, str, str]] = set()
        for prohibited in self.prohibited_legacy_transitions:
            if (
                prohibited.from_state_id not in state_ids
                or prohibited.to_state_id not in state_ids
            ):
                raise ValueError("prohibited legacy transition has a dangling state reference")
            key = (
                prohibited.from_state_id,
                prohibited.to_state_id,
                ",".join(prohibited.legacy_action_ids),
                ",".join(prohibited.legacy_handler_names),
            )
            if key in prohibited_keys:
                raise ValueError("duplicate prohibited legacy transition edge")
            if action_ids.intersection(prohibited.legacy_action_ids):
                raise ValueError(
                    "prohibited legacy action ID conflicts with an allowed action"
                )
            if allowed_handler_names.intersection(prohibited.legacy_handler_names):
                raise ValueError(
                    "prohibited legacy handler name conflicts with an allowed action"
                )
            prohibited_keys.add(key)
        return self


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _body_digest(body: _WorkflowContractBody) -> str:
    encoded = _canonical_json(body.model_dump(mode="json")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class WorkflowContract(_WorkflowContractBody):
    contract_digest: str

    @model_validator(mode="after")
    def validate_digest(self) -> "WorkflowContract":
        if not _SHA256_RE.fullmatch(self.contract_digest):
            raise ValueError("contract_digest must be a lowercase SHA-256 digest")
        body = _WorkflowContractBody.model_validate(
            self.model_dump(mode="python", exclude={"contract_digest"})
        )
        if self.contract_digest != _body_digest(body):
            raise ValueError("contract_digest does not match canonical contract")
        return self


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


_UniqueKeySafeLoader.yaml_implicit_resolvers = {
    key: [
        (tag, pattern)
        for tag, pattern in resolvers
        if tag != "tag:yaml.org,2002:bool"
    ]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_UniqueKeySafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$"),
    list("tTfF"),
)


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ConstructorError(None, None, "unhashable YAML mapping key", key_node.start_mark) from exc
        if duplicate:
            raise ConstructorError(
                None,
                None,
                f"duplicate YAML key: {key}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _preflight_yaml_events(text: str, limits: ContractLimits) -> None:
    depth = 0
    item_count = 0
    try:
        for event in yaml.parse(text, Loader=_UniqueKeySafeLoader):
            if isinstance(event, AliasEvent):
                raise ContractLoadError("YAML aliases are not allowed")
            if getattr(event, "anchor", None) is not None:
                raise ContractLoadError("YAML anchors are not allowed")
            if isinstance(event, CollectionStartEvent):
                depth += 1
                if depth > limits.max_depth:
                    raise ContractLoadError("max_depth exceeded")
                item_count += 1
            elif isinstance(event, CollectionEndEvent):
                depth -= 1
            elif isinstance(event, ScalarEvent):
                item_count += 1
                if len(event.value) > limits.max_string_length:
                    raise ContractLoadError("max_string_length exceeded")
            if item_count > limits.max_items:
                raise ContractLoadError("max_items exceeded")
    except ContractLoadError:
        raise
    except (yaml.YAMLError, RecursionError) as exc:
        raise ContractLoadError(f"safe YAML parse failed: {exc}") from exc


def _check_structure(value: Any, limits: ContractLimits) -> None:
    item_count = 0
    active: set[int] = set()

    def visit(item: Any, depth: int) -> None:
        nonlocal item_count
        if depth > limits.max_depth:
            raise ContractLoadError("max_depth exceeded")
        item_count += 1
        if item_count > limits.max_items:
            raise ContractLoadError("max_items exceeded")
        if isinstance(item, str):
            if len(item) > limits.max_string_length:
                raise ContractLoadError("max_string_length exceeded")
            return
        if item is None or isinstance(item, (bool, int, float)):
            return
        if not isinstance(item, (dict, list)):
            raise ContractLoadError("safe YAML must contain only JSON-compatible values")
        identity = id(item)
        if identity in active:
            raise ContractLoadError("safe YAML aliases cannot form cycles")
        active.add(identity)
        if isinstance(item, dict):
            for key, nested in item.items():
                if not isinstance(key, str):
                    raise ContractLoadError("safe YAML mapping keys must be strings")
                visit(key, depth + 1)
                visit(nested, depth + 1)
        else:
            for nested in item:
                visit(nested, depth + 1)
        active.remove(identity)

    visit(value, 1)


def load_workflow_contract(
    document: str | bytes,
    *,
    predicate_registry: PredicateRegistry,
    action_registry: ActionRegistry,
    limits: ContractLimits | None = None,
) -> WorkflowContract:
    """Load one declarative contract without evaluating or invoking its names."""

    limits = limits or ContractLimits()
    if isinstance(document, str):
        try:
            raw = document.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ContractLoadError("contract must be valid UTF-8") from exc
        text = document
    elif isinstance(document, bytes):
        raw = document
        try:
            text = document.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractLoadError("contract must be valid UTF-8") from exc
    else:
        raise TypeError("contract document must be str or bytes")
    if len(raw) > limits.max_bytes:
        raise ContractLoadError("max_bytes exceeded")

    _preflight_yaml_events(text, limits)

    try:
        payload = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except (yaml.YAMLError, RecursionError) as exc:
        message = str(exc)
        if "duplicate YAML key" in message:
            raise ContractLoadError(message) from exc
        raise ContractLoadError(f"safe YAML parse failed: {message}") from exc
    if not isinstance(payload, dict):
        raise ContractLoadError("safe YAML contract must be a top-level mapping")
    _check_structure(payload, limits)

    body = _WorkflowContractBody.model_validate(payload)
    for prerequisite in body.prerequisites:
        try:
            predicate_registry.resolve(prerequisite.predicate)
        except KeyError as exc:
            raise ContractLoadError(str(exc)) from exc
    for action in body.actions:
        try:
            action_registry.resolve(action.handler)
        except KeyError as exc:
            raise ContractLoadError(str(exc)) from exc

    return WorkflowContract.model_validate(
        {
            **body.model_dump(mode="python"),
            "contract_digest": _body_digest(body),
        }
    )


__all__ = [
    "ActionContract",
    "ActionRegistry",
    "ContractLimits",
    "ContractLoadError",
    "PredicateRegistry",
    "ResourcePolicy",
    "WorkflowContract",
    "load_workflow_contract",
]
