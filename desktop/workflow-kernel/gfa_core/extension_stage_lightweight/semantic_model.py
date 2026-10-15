"""Validate semantic annotations without allowing execution-fact rewrites."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


_TOP_LEVEL_FIELDS = {
    "facts_sha256",
    "function_summary",
    "inputs",
    "parameters",
    "outputs",
}
_SECTION_FIELDS = {
    "inputs": {
        "fact_id",
        "role",
        "description",
        "replaceability",
    },
    "parameters": {
        "fact_id",
        "display_name",
        "description",
    },
    "outputs": {
        "fact_id",
        "description",
        "result_role",
    },
}
_ENUMS = {
    ("inputs", "replaceability"): {
        "replaceable",
        "fixed_resource",
        "generated",
    },
    ("outputs", "result_role"): {"final", "intermediate", "log", "unknown"},
}


class SemanticCandidateError(ValueError):
    """Raised when a semantic candidate does not satisfy its fact contract."""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = deepcopy(errors)
        summary = "; ".join(error["message"] for error in errors)
        super().__init__(f"Invalid semantic candidate: {summary}")


def _error(field: str, code: str, message: str) -> dict[str, str]:
    return {"field": field, "code": code, "message": message}


def _expected_fact_ids(facts: dict[str, Any]) -> dict[str, set[str]]:
    expected = {"inputs": set(), "parameters": set(), "outputs": set()}
    for command in facts.get("commands", []):
        expected["inputs"].update(
            item["fact_id"] for item in command.get("inputs", [])
        )
        expected["parameters"].update(
            item["fact_id"] for item in command.get("parameters", [])
        )
    expected["outputs"].update(
        item["fact_id"] for item in facts.get("outputs", {}).get("files", [])
    )
    stdout = facts.get("outputs", {}).get("stdout", {})
    if stdout.get("present"):
        expected["outputs"].add(stdout["fact_id"])
    return expected


def _fact_records_by_section(facts: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    records = {
        "inputs": [
            item
            for command in facts.get("commands", [])
            for item in command.get("inputs", [])
        ],
        "parameters": [
            item
            for command in facts.get("commands", [])
            for item in command.get("parameters", [])
        ],
        "outputs": list(facts.get("outputs", {}).get("files", [])),
    }
    stdout = facts.get("outputs", {}).get("stdout", {})
    if stdout.get("present"):
        records["outputs"].append(stdout)
    return records


def _missing_semantic_row(section: str, fact: dict[str, Any]) -> dict[str, str]:
    fact_id = fact["fact_id"]
    if section == "inputs":
        path = fact.get("path", fact_id)
        return {
            "fact_id": fact_id,
            "role": f"Locked input: {path}.",
            "description": f"Observed input file: {path}.",
            "replaceability": (
                "generated"
                if fact.get("source") == "generated_input"
                else "replaceable"
            ),
        }
    if section == "parameters":
        value = fact.get("value", fact_id)
        return {
            "fact_id": fact_id,
            "display_name": f"Locked parameter: {value}.",
            "description": f"Observed command parameter: {value}.",
        }
    if "path" in fact:
        description = f"Observed output file: {fact['path']}."
    else:
        description = "Observed standard output."
    return {
        "fact_id": fact_id,
        "description": description,
        "result_role": "unknown",
    }


def bind_fact_derived_semantics(
    facts: dict[str, Any], candidate: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Bind facts the model cannot decide and discard unrepairable references."""

    bound = deepcopy(candidate)
    audit: list[dict[str, Any]] = []
    expected = _expected_fact_ids(facts)
    records_by_section = _fact_records_by_section(facts)
    input_sources = {
        item["fact_id"]: item.get("source")
        for command in facts.get("commands", [])
        for item in command.get("inputs", [])
    }
    for section in ("inputs", "parameters", "outputs"):
        entries = bound.get(section)
        if not isinstance(entries, list):
            continue
        kept: list[Any] = []
        seen: set[str] = set()
        discarded_reference = False
        for entry in entries:
            if not isinstance(entry, dict):
                kept.append(entry)
                continue
            fact_id = entry.get("fact_id")
            if isinstance(fact_id, str) and fact_id not in expected[section]:
                discarded_reference = True
                audit.append(
                    {
                        "action": "discard_unknown_reference",
                        "section": section,
                        "fact_id": fact_id,
                    }
                )
                continue
            if isinstance(fact_id, str) and fact_id in seen:
                discarded_reference = True
                audit.append(
                    {
                        "action": "discard_duplicate_reference",
                        "section": section,
                        "fact_id": fact_id,
                    }
                )
                continue
            if isinstance(fact_id, str):
                seen.add(fact_id)
            if (
                section == "inputs"
                and isinstance(fact_id, str)
                and not str(entry.get("replaceability") or "").strip()
            ):
                bound_value = (
                    "generated"
                    if input_sources.get(fact_id) == "generated_input"
                    else "replaceable"
                )
                audit.append(
                    {
                        "action": "bind_missing_input_replaceability",
                        "section": section,
                        "fact_id": fact_id,
                        "field": "replaceability",
                        "bound_value": bound_value,
                    }
                )
                entry["replaceability"] = bound_value
            if (
                section == "inputs"
                and input_sources.get(fact_id) == "generated_input"
                and entry.get("replaceability") != "generated"
            ):
                audit.append(
                    {
                        "action": "bind_generated_input",
                        "section": section,
                        "fact_id": fact_id,
                        "field": "replaceability",
                        "discarded_value": entry.get("replaceability"),
                    }
                )
                entry["replaceability"] = "generated"
            kept.append(entry)
        if not discarded_reference:
            for fact in records_by_section[section]:
                fact_id = fact["fact_id"]
                if fact_id in seen:
                    continue
                kept.append(_missing_semantic_row(section, fact))
                audit.append(
                    {
                        "action": "complete_missing_semantic_row",
                        "section": section,
                        "fact_id": fact_id,
                    }
                )
        bound[section] = kept
    return bound, audit


def _validate_text_field(
    *,
    item: dict[str, Any],
    field: str,
    path: str,
    errors: list[dict[str, str]],
) -> None:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        errors.append(
            _error(
                f"{path}.{field}",
                "invalid_value",
                f"{path}.{field} must be a non-empty string",
            )
        )


def validate_semantic_candidate(
    facts: dict[str, Any], candidate: dict[str, Any]
) -> list[dict[str, str]]:
    """Return deterministic field errors for a semantic-only candidate."""

    errors: list[dict[str, str]] = []
    if not isinstance(candidate, dict):
        return [
            _error("candidate", "invalid_type", "candidate must be an object")
        ]

    for field in sorted(set(candidate) - _TOP_LEVEL_FIELDS):
        errors.append(
            _error(field, "unknown_field", f"candidate field is not allowed: {field}")
        )
    for field in sorted(_TOP_LEVEL_FIELDS - set(candidate)):
        errors.append(
            _error(field, "required_field", f"candidate field is required: {field}")
        )

    if candidate.get("facts_sha256") != facts.get("facts_sha256"):
        errors.append(
            _error(
                "facts_sha256",
                "facts_mismatch",
                "candidate facts_sha256 does not match locked execution facts",
            )
        )
    summary = candidate.get("function_summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append(
            _error(
                "function_summary",
                "invalid_value",
                "function_summary must be a non-empty string",
            )
        )

    expected_by_section = _expected_fact_ids(facts)
    input_sources = {
        item["fact_id"]: item.get("source")
        for command in facts.get("commands", [])
        for item in command.get("inputs", [])
    }
    for section, allowed_fields in _SECTION_FIELDS.items():
        entries = candidate.get(section)
        if not isinstance(entries, list):
            errors.append(
                _error(section, "invalid_type", f"{section} must be an array")
            )
            continue

        seen: set[str] = set()
        for index, entry in enumerate(entries):
            path = f"{section}[{index}]"
            if not isinstance(entry, dict):
                errors.append(
                    _error(path, "invalid_type", f"{path} must be an object")
                )
                continue
            for field in sorted(set(entry) - allowed_fields):
                errors.append(
                    _error(
                        f"{path}.{field}",
                        "unknown_field",
                        f"{path}.{field} is not allowed",
                    )
                )
            for field in sorted(allowed_fields - set(entry)):
                errors.append(
                    _error(
                        f"{path}.{field}",
                        "required_field",
                        f"{path}.{field} is required",
                    )
                )

            fact_id = entry.get("fact_id")
            if isinstance(fact_id, str) and fact_id:
                if fact_id in seen:
                    errors.append(
                        _error(
                            f"{path}.fact_id",
                            "duplicate_fact_id",
                            f"duplicate {section} fact_id: {fact_id}",
                        )
                    )
                else:
                    seen.add(fact_id)
                if fact_id not in expected_by_section[section]:
                    errors.append(
                        _error(
                            f"{path}.fact_id",
                            "unknown_fact_id",
                            f"unknown {section} fact_id: {fact_id}",
                        )
                    )
            else:
                errors.append(
                    _error(
                        f"{path}.fact_id",
                        "invalid_value",
                        f"{path}.fact_id must be a non-empty string",
                    )
                )

            for field in allowed_fields - {"fact_id"}:
                _validate_text_field(
                    item=entry,
                    field=field,
                    path=path,
                    errors=errors,
                )
                allowed_values = _ENUMS.get((section, field))
                if allowed_values is not None and entry.get(field) not in allowed_values:
                    errors.append(
                        _error(
                            f"{path}.{field}",
                            "invalid_value",
                            f"{path}.{field} must be one of: "
                            + ", ".join(sorted(allowed_values)),
                        )
                    )

            if fact_id in input_sources:
                replaceability = entry.get("replaceability")
                source = input_sources[fact_id]
                source_mismatch = (
                    source == "generated_input" and replaceability != "generated"
                ) or (
                    source != "generated_input" and replaceability == "generated"
                )
                if source_mismatch:
                    errors.append(
                        _error(
                            f"{path}.replaceability",
                            "input_source_mismatch",
                            f"{path}.replaceability conflicts with locked input source: "
                            f"{source}",
                        )
                    )

        for fact_id in sorted(expected_by_section[section] - seen):
            errors.append(
                _error(
                    section,
                    "missing_fact_id",
                    f"missing {section} fact_id: {fact_id}",
                )
            )
    return errors


def merge_semantics(
    facts: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    """Combine validated semantics with an untouched copy of execution facts."""

    errors = validate_semantic_candidate(facts, candidate)
    if errors:
        raise SemanticCandidateError(errors)
    return {
        "execution_facts": deepcopy(facts),
        "semantics": deepcopy(candidate),
    }
