"""Deterministic inventory of HTTP and model-dispatched mutation surfaces."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, TypeVar

from fastapi import FastAPI
from fastapi.routing import APIRoute


_SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_ROUTE_POLICY_ATTRIBUTE = "__plantai_route_policy__"
_Endpoint = TypeVar("_Endpoint", bound=Callable)


class MutationSurfaceError(ValueError):
    """Raised when a state-changing surface lacks explicit policy metadata."""


class RoutePolicy(str, Enum):
    READ_ONLY = "read_only"
    MUTATION = "mutation"


@dataclass(frozen=True)
class RouteSurfaceEntry:
    path: str
    methods: tuple[str, ...]
    name: str
    endpoint: str
    required_policy: RoutePolicy
    declared_policy: RoutePolicy | None

    @property
    def covered(self) -> bool:
        if self.declared_policy is None:
            return self.required_policy is RoutePolicy.READ_ONLY
        return self.declared_policy is self.required_policy

    @property
    def label(self) -> str:
        return f"{','.join(self.methods)} {self.path}"

    @property
    def identity(self) -> tuple[tuple[str, ...], str, str, str]:
        return (self.methods, self.path, self.name, self.endpoint)


@dataclass(frozen=True)
class RoutePolicyDeclaration:
    path: str
    methods: tuple[str, ...]
    name: str
    endpoint: str
    policy: RoutePolicy

    @property
    def identity(self) -> tuple[tuple[str, ...], str, str, str]:
        return (self.methods, self.path, self.name, self.endpoint)


@dataclass(frozen=True)
class ToolSurfaceEntry:
    name: str
    permission: str
    handler: str


def declare_route_policy(policy: RoutePolicy) -> Callable[[_Endpoint], _Endpoint]:
    if not isinstance(policy, RoutePolicy):
        raise TypeError("route policy must be a RoutePolicy")

    def decorate(endpoint: _Endpoint) -> _Endpoint:
        existing = getattr(endpoint, _ROUTE_POLICY_ATTRIBUTE, None)
        if existing is not None:
            raise MutationSurfaceError("route policy is already declared")
        setattr(endpoint, _ROUTE_POLICY_ATTRIBUTE, policy)
        return endpoint

    return decorate


def inventory_fastapi_routes(app: FastAPI) -> tuple[RouteSurfaceEntry, ...]:
    entries: list[tuple[int, RouteSurfaceEntry]] = []
    for index, route in enumerate(app.routes):
        if not isinstance(route, APIRoute):
            continue
        methods = tuple(sorted(route.methods))
        required_policy = (
            RoutePolicy.READ_ONLY
            if set(methods) <= _SAFE_HTTP_METHODS
            else RoutePolicy.MUTATION
        )
        raw_policy = getattr(route.endpoint, _ROUTE_POLICY_ATTRIBUTE, None)
        if raw_policy is not None and not isinstance(raw_policy, RoutePolicy):
            raise MutationSurfaceError(
                f"{','.join(methods)} {route.path} has invalid route policy metadata"
            )
        endpoint_name = (
            f"{route.endpoint.__module__}.{route.endpoint.__qualname__}"
        )
        entries.append(
            (
                index,
                RouteSurfaceEntry(
                    path=route.path,
                    methods=methods,
                    name=route.name,
                    endpoint=endpoint_name,
                    required_policy=required_policy,
                    declared_policy=raw_policy,
                ),
            )
        )
    entries.sort(
        key=lambda item: (
            item[1].path,
            item[1].methods,
            item[1].endpoint,
            item[1].name,
            item[0],
        )
    )
    return tuple(entry for _index, entry in entries)


def validate_route_policy_coverage(
    entries: tuple[RouteSurfaceEntry, ...],
) -> None:
    uncovered = tuple(item for item in entries if not item.covered)
    if uncovered:
        labels = ", ".join(item.label for item in uncovered)
        raise MutationSurfaceError(
            f"{len(uncovered)} route(s) lack matching route policy: {labels}"
        )


def apply_route_policy_manifest(
    entries: tuple[RouteSurfaceEntry, ...],
    declarations: tuple[RoutePolicyDeclaration, ...],
) -> tuple[RouteSurfaceEntry, ...]:
    mutation_entries = tuple(
        item for item in entries if item.required_policy is RoutePolicy.MUTATION
    )
    entry_identities = [item.identity for item in mutation_entries]
    if len(entry_identities) != len(set(entry_identities)):
        raise MutationSurfaceError("duplicate mutation route identity")
    declaration_identities = [item.identity for item in declarations]
    if len(declaration_identities) != len(set(declaration_identities)):
        raise MutationSurfaceError("duplicate route policy declaration")
    if any(item.policy is not RoutePolicy.MUTATION for item in declarations):
        raise MutationSurfaceError("mutation route manifest may only declare MUTATION")
    entry_keys = set(entry_identities)
    declaration_keys = set(declaration_identities)
    missing = entry_keys - declaration_keys
    extra = declaration_keys - entry_keys
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append(f"missing={len(missing)}")
        if extra:
            details.append(f"extra={len(extra)}")
        raise MutationSurfaceError(
            "route policy manifest disagrees with runtime surface: "
            + ", ".join(details)
        )
    declarations_by_identity = {
        item.identity: item.policy for item in declarations
    }
    conflicting = tuple(
        item
        for item in mutation_entries
        if item.declared_policy is not None
        and item.declared_policy
        is not declarations_by_identity[item.identity]
    )
    if conflicting:
        labels = ", ".join(item.label for item in conflicting)
        raise MutationSurfaceError(
            f"conflicting endpoint and manifest route policy: {labels}"
        )
    return tuple(
        replace(
            item,
            declared_policy=declarations_by_identity[item.identity],
        )
        if item.identity in declarations_by_identity
        else item
        for item in entries
    )


def route_inventory_digest(entries: tuple[RouteSurfaceEntry, ...]) -> str:
    payload = [
        {
            "path": item.path,
            "methods": item.methods,
            "name": item.name,
            "endpoint": item.endpoint,
            "required_policy": item.required_policy.value,
            "declared_policy": (
                item.declared_policy.value if item.declared_policy else ""
            ),
        }
        for item in entries
    ]
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = [
    "MutationSurfaceError",
    "RoutePolicy",
    "RoutePolicyDeclaration",
    "RouteSurfaceEntry",
    "ToolSurfaceEntry",
    "apply_route_policy_manifest",
    "declare_route_policy",
    "inventory_fastapi_routes",
    "route_inventory_digest",
    "validate_route_policy_coverage",
]
