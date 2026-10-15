"""Apply bounded semantic patches without changing execution facts."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from .deepseek_semantics import (
    DeepSeekConfig,
    DeepSeekSemanticError,
    generate_semantic_patch,
)
from .semantic_model import bind_fact_derived_semantics, validate_semantic_candidate


MAX_REPAIR_ATTEMPTS = 20
_FIELDS = {
    "root": {"function_summary"},
    "inputs": {"role", "description", "replaceability"},
    "parameters": {"display_name", "description"},
    "outputs": {"description", "result_role"},
}
_ERROR_PATH = re.compile(
    r"^(inputs|parameters|outputs)\[(\d+)\]\.([A-Za-z_][A-Za-z0-9_]*)$"
)


def _error(field: str, code: str, message: str) -> dict[str, str]:
    return {"field": field, "code": code, "message": message}


def _expected_ids(facts: dict[str, Any]) -> dict[str, set[str]]:
    expected = {"inputs": set(), "parameters": set(), "outputs": set()}
    for command in facts["commands"]:
        expected["inputs"].update(item["fact_id"] for item in command["inputs"])
        expected["parameters"].update(
            item["fact_id"] for item in command["parameters"]
        )
    expected["outputs"].update(
        item["fact_id"] for item in facts["outputs"]["files"]
    )
    if facts["outputs"]["stdout"].get("present"):
        expected["outputs"].add(facts["outputs"]["stdout"]["fact_id"])
    return expected


def _all_targets(facts: dict[str, Any]) -> set[tuple[str, str | None, str]]:
    targets = {("root", None, "function_summary")}
    for section, fact_ids in _expected_ids(facts).items():
        targets.update(
            (section, fact_id, field)
            for fact_id in fact_ids
            for field in _FIELDS[section]
        )
    return targets


def _automatic_targets(
    facts: dict[str, Any],
    candidate: dict[str, Any],
    errors: list[dict[str, str]],
) -> set[tuple[str, str | None, str]]:
    targets: set[tuple[str, str | None, str]] = set()
    if any(error["field"] == "function_summary" for error in errors):
        targets.add(("root", None, "function_summary"))

    for error in errors:
        match = _ERROR_PATH.match(error["field"])
        if not match:
            continue
        section, raw_index, field = match.groups()
        entries = candidate.get(section, [])
        index = int(raw_index)
        if index < len(entries) and field in _FIELDS[section]:
            fact_id = entries[index].get("fact_id")
            if isinstance(fact_id, str):
                targets.add((section, fact_id, field))

    expected = _expected_ids(facts)
    for section, fact_ids in expected.items():
        present = {
            item.get("fact_id")
            for item in candidate.get(section, [])
            if isinstance(item, dict)
        }
        for fact_id in fact_ids - present:
            targets.update(
                (section, fact_id, field) for field in _FIELDS[section]
            )
    return targets


def _targets_as_records(
    targets: set[tuple[str, str | None, str]],
) -> list[dict[str, str | None]]:
    return [
        {"section": section, "fact_id": fact_id, "field": field}
        for section, fact_id, field in sorted(
            targets, key=lambda item: (item[0], item[1] or "", item[2])
        )
    ]


def _validate_patch(
    *,
    facts: dict[str, Any],
    patch: Any,
    allowed_targets: set[tuple[str, str | None, str]],
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not isinstance(patch, dict):
        return [_error("patch", "invalid_type", "repair patch must be an object")]
    required = {"facts_sha256", "changes"}
    for field in sorted(set(patch) - required):
        errors.append(
            _error(field, "unknown_field", f"repair patch field is not allowed: {field}")
        )
    for field in sorted(required - set(patch)):
        errors.append(_error(field, "required_field", f"repair patch requires {field}"))
    if patch.get("facts_sha256") != facts["facts_sha256"]:
        errors.append(
            _error(
                "facts_sha256",
                "facts_mismatch",
                "repair patch facts_sha256 does not match locked execution facts",
            )
        )
    changes = patch.get("changes")
    if not isinstance(changes, list):
        errors.append(_error("changes", "invalid_type", "changes must be an array"))
        return errors

    expected = _expected_ids(facts)
    seen: set[tuple[str, str | None, str]] = set()
    for index, change in enumerate(changes):
        path = f"changes[{index}]"
        if not isinstance(change, dict):
            errors.append(_error(path, "invalid_type", f"{path} must be an object"))
            continue
        allowed_keys = {"section", "fact_id", "fields"}
        for field in sorted(set(change) - allowed_keys):
            errors.append(
                _error(
                    f"{path}.{field}",
                    "unknown_field",
                    f"{path}.{field} is not allowed",
                )
            )
        if set(change) != allowed_keys:
            for field in sorted(allowed_keys - set(change)):
                errors.append(
                    _error(
                        f"{path}.{field}",
                        "required_field",
                        f"{path}.{field} is required",
                    )
                )
            continue

        section = change["section"]
        fact_id = change["fact_id"]
        fields = change["fields"]
        if section not in _FIELDS:
            errors.append(
                _error(f"{path}.section", "invalid_value", f"unknown section: {section}")
            )
            continue
        if section == "root":
            if fact_id is not None:
                errors.append(
                    _error(
                        f"{path}.fact_id",
                        "invalid_value",
                        "root patch fact_id must be null",
                    )
                )
        elif not isinstance(fact_id, str) or fact_id not in expected[section]:
            errors.append(
                _error(
                    f"{path}.fact_id",
                    "unknown_fact_id",
                    f"unknown {section} fact_id: {fact_id}",
                )
            )
        if not isinstance(fields, dict) or not fields:
            errors.append(
                _error(f"{path}.fields", "invalid_type", "fields must be an object")
            )
            continue
        for field, value in fields.items():
            target = (section, fact_id, field)
            if field not in _FIELDS[section]:
                errors.append(
                    _error(
                        f"{path}.fields.{field}",
                        "unknown_field",
                        f"semantic repair field is not allowed: {field}",
                    )
                )
            elif target not in allowed_targets:
                errors.append(
                    _error(
                        f"{path}.fields.{field}",
                        "target_not_allowed",
                        f"semantic repair target is not allowed: {section}/{fact_id}/{field}",
                    )
                )
            elif target in seen:
                errors.append(
                    _error(
                        f"{path}.fields.{field}",
                        "duplicate_target",
                        f"duplicate semantic repair target: {section}/{fact_id}/{field}",
                    )
                )
            elif not isinstance(value, str) or not value.strip():
                errors.append(
                    _error(
                        f"{path}.fields.{field}",
                        "invalid_value",
                        f"semantic repair value must be a non-empty string: {field}",
                    )
                )
            else:
                seen.add(target)
    return errors


def _apply_patch(
    candidate: dict[str, Any], patch: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    proposed = deepcopy(candidate)
    diff: list[dict[str, Any]] = []
    for change in patch["changes"]:
        section = change["section"]
        fact_id = change["fact_id"]
        if section == "root":
            target = proposed
        else:
            entries = proposed.setdefault(section, [])
            target = next(
                (item for item in entries if item.get("fact_id") == fact_id), None
            )
            if target is None:
                target = {"fact_id": fact_id}
                entries.append(target)
        for field, value in sorted(change["fields"].items()):
            before = target.get(field)
            target[field] = value
            if before != value:
                diff.append(
                    {
                        "section": section,
                        "fact_id": fact_id,
                        "field": field,
                        "before": before,
                        "after": value,
                    }
                )
    return proposed, diff


PatchGenerator = Callable[
    [dict[str, Any], int], tuple[dict[str, Any], dict[str, Any]]
]


def repair_semantic_candidate(
    *,
    execution_facts: dict[str, Any],
    candidate: dict[str, Any],
    validation_errors: list[dict[str, str]] | None = None,
    user_feedback: str | None = None,
    patch_generator: PatchGenerator | None = None,
    raw_code: Path | None = None,
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[[DeepSeekConfig], Any] | None = None,
    max_attempts: int = MAX_REPAIR_ATTEMPTS,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Repair semantic fields while preserving the supplied candidate until success."""

    original = deepcopy(candidate)
    errors = (
        deepcopy(validation_errors)
        if validation_errors is not None
        else validate_semantic_candidate(execution_facts, candidate)
    )
    feedback = (user_feedback or "").strip()
    if not errors and not feedback:
        return original, []
    mode = "user_feedback" if feedback else "automatic"
    allowed_targets = (
        _all_targets(execution_facts)
        if feedback
        else _automatic_targets(execution_facts, candidate, errors)
    )
    context = {
        "execution_facts": execution_facts,
        "candidate": original,
        "validation_errors": errors,
        "user_feedback": feedback or None,
        "allowed_targets": _targets_as_records(allowed_targets),
    }
    if patch_generator is None:
        if raw_code is None:
            raise ValueError("raw_code is required for DeepSeek semantic repair")

        def patch_generator(
            repair_context: dict[str, Any], attempt: int
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            return generate_semantic_patch(
                repair_context=repair_context,
                raw_code=raw_code,
                environ=environ,
                client_factory=client_factory,
                attempt=attempt,
            )

    history: list[dict[str, Any]] = []
    limit = min(MAX_REPAIR_ATTEMPTS, max(1, int(max_attempts)))
    for attempt in range(1, limit + 1):
        metadata: dict[str, Any] = {}
        patch_errors: list[dict[str, str]] = []
        diff: list[dict[str, Any]] = []
        try:
            patch, metadata = patch_generator(deepcopy(context), attempt)
            patch_errors = _validate_patch(
                facts=execution_facts,
                patch=patch,
                allowed_targets=allowed_targets,
            )
            if not patch_errors:
                proposed, diff = _apply_patch(original, patch)
                if mode == "automatic":
                    proposed, binding_audit = bind_fact_derived_semantics(
                        execution_facts, proposed
                    )
                    if binding_audit:
                        metadata["fact_derived_semantic_bindings"] = binding_audit
                patch_errors = validate_semantic_candidate(
                    execution_facts, proposed
                )
                if not patch_errors:
                    history.append(
                        {
                            "attempt": attempt,
                            "mode": mode,
                            "status": "success",
                            "diff": diff,
                            "metadata": deepcopy(metadata),
                            "errors": [],
                        }
                    )
                    return proposed, history
        except DeepSeekSemanticError as exc:
            metadata = deepcopy(exc.metadata)
            patch_errors = [_error("repair", exc.code, str(exc))]
        except Exception as exc:
            patch_errors = [
                _error("repair", "patch_generation_failed", str(exc))
            ]

        status = "needs_user_confirmation" if attempt == limit else "rejected"
        history.append(
            {
                "attempt": attempt,
                "mode": mode,
                "status": status,
                "diff": diff,
                "metadata": deepcopy(metadata),
                "errors": patch_errors,
            }
        )
    return original, history
