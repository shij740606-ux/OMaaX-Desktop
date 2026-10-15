"""Deterministic cleanup for API-generated extension-stage specs.

API output is a candidate, not a contract.  This module handles common,
mechanical mistakes before strict ``spec_v2`` validation so the API/agent path
does not need Codex hand edits for basic JSON, role, or shell-template issues.
"""

from __future__ import annotations

import json
import re
import string
from pathlib import Path
from typing import Any

from gfa_core.extension_stage.spec_v2 import INPUT_ROLES, PLACEHOLDERS, ROLE_ALIASES
from gfa_core.extension_stage.template_cases import ARCHETYPE_BY_ID


class ExtensionSpecSanitizerError(RuntimeError):
    """Raised when a raw API response cannot be converted to a candidate spec."""



SANITIZER_RULES: dict[str, dict[str, Any]] = {
    "sanitize_conversion_spec": {
        "responsibility": "audit every sanitizer pass and preserve source phase",
        "validated_by": "test_sanitizer_rule_manifest_is_complete_and_tested",
    },
    "extracted_json_from_markdown_fence": {
        "responsibility": "recover strict JSON from markdown-fenced API responses",
        "validated_by": "test_extract_json_object_from_prompt_echo_markdown",
    },
    "extracted_json_substring": {
        "responsibility": "recover the outer JSON object from prompt echo or surrounding prose",
        "validated_by": "test_extract_json_object_from_prompt_echo_markdown",
    },
    "promoted_entrypoint_original_command": {
        "responsibility": "promote nested entrypoint.original_command to the canonical top-level field",
        "validated_by": "test_sanitizer_rule_manifest_is_complete_and_tested",
    },
    "promoted_entrypoint_command_template": {
        "responsibility": "promote nested entrypoint.command_template to the canonical top-level field",
        "validated_by": "test_sanitizer_rule_manifest_is_complete_and_tested",
    },
    "dropped_invalid_input_role_key": {
        "responsibility": "drop non-input placeholders or unresolvable API role keys before schema validation",
        "validated_by": "test_sanitizer_rule_manifest_is_complete_and_tested",
    },
    "preserved_unknown_input_role_as_input_binding": {
        "responsibility": "preserve open-format API input descriptions as input_bindings instead of dropping them",
        "validated_by": "test_sanitize_preserves_open_input_roles_as_bindings",
    },
    "mapped_unknown_input_role_to_generic": {
        "responsibility": "keep schema compatibility by mapping unknown input role labels to generic_input_file with semantic_hint evidence",
        "validated_by": "test_sanitize_preserves_open_input_roles_as_bindings",
    },
    "normalized_input_role_key": {
        "responsibility": "map API filename-style role keys such as input.fa to legal bbav5 placeholders",
        "validated_by": "test_sanitize_filename_input_roles_and_unsafe_semantic_template",
    },
    "normalized_input_role_alias": {
        "responsibility": "map known role aliases to spec_v2 canonical roles",
        "validated_by": "test_sanitizer_rule_manifest_is_complete_and_tested",
    },
    "promoted_input_role_from_inputs": {
        "responsibility": "promote placeholder roles declared under inputs into top-level input_roles",
        "validated_by": "test_sanitize_promotes_placeholder_roles_from_inputs_and_disables_textual_semantic",
    },
    "restored_required_archetype_input_role": {
        "responsibility": "restore required input_roles from a declared reusable archetype when API generalized them away",
        "validated_by": "test_sanitize_restores_required_reference_classification_input_role",
    },
    "locked_unapplied_editable_parameter": {
        "responsibility": "prevent editable parameters that are not wired into command_template from blocking publish",
        "validated_by": "test_sanitizer_rule_manifest_is_complete_and_tested",
    },
    "promoted_nested_output_contract": {
        "responsibility": "promote outputs.output_contract to top-level output_contract",
        "validated_by": "test_sanitize_promotes_nested_output_contract",
    },
    "converted_output_contract_object_to_list": {
        "responsibility": "convert object-shaped output_contract into the list shape required by spec_v2",
        "validated_by": "test_sanitize_output_contract_dict_to_list",
    },
    "created_output_contract_from_outputs": {
        "responsibility": "create a minimal output_contract from outputs.patterns when API omitted one",
        "validated_by": "test_sanitizer_rule_manifest_is_complete_and_tested",
    },
    "disabled_unsafe_semantic_command_template": {
        "responsibility": "disable API semantic templates that cannot safely run under golden validation",
        "validated_by": "test_sanitize_filename_input_roles_and_unsafe_semantic_template",
    },
    "disabled_unsafe_command_template_as_semantic": {
        "responsibility": "prevent unsafe command_template sketches from being derived as semantic baselines",
        "validated_by": "test_sanitize_prevents_unsafe_command_template_semantic_derivation",
    },
}


def sanitizer_rule_manifest() -> dict[str, dict[str, Any]]:
    """Return the reviewed sanitizer rule manifest used by tests and audits."""
    return {key: dict(value) for key, value in SANITIZER_RULES.items()}


def _record_action(actions: list[dict[str, Any]], action: str, **payload: Any) -> None:
    if action not in SANITIZER_RULES:
        raise ExtensionSpecSanitizerError(f"unregistered sanitizer action: {action}")
    actions.append({"action": action, **payload})

def extract_json_object(content: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Extract a JSON object from a raw API response."""
    text = str(content or "").strip()
    actions: list[dict[str, Any]] = []
    if not text:
        raise ExtensionSpecSanitizerError("API response was empty")
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value, actions
    except Exception:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1)
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                _record_action(actions, "extracted_json_from_markdown_fence")
                return value, actions
        except Exception:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidate = text[start : end + 1]
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                _record_action(actions, "extracted_json_substring")
                return value, actions
        except Exception as exc:
            raise ExtensionSpecSanitizerError(f"API response contained malformed JSON object: {exc}") from exc
    raise ExtensionSpecSanitizerError("API response did not contain a JSON object")


def sanitize_conversion_spec(spec: dict[str, Any], *, raw_data: Path | None = None, source: str = "api") -> dict[str, Any]:
    """Return a normalized candidate spec plus ``sanitizer_actions`` evidence."""
    if not isinstance(spec, dict):
        raise ExtensionSpecSanitizerError("conversion spec must be a JSON object")
    raw_data = Path(raw_data) if raw_data else None
    cleaned = dict(spec)
    actions: list[dict[str, Any]] = list(cleaned.get("sanitizer_actions") or [])
    _record_action(actions, "sanitize_conversion_spec", source=source)

    _sanitize_entrypoint(cleaned, actions)
    input_bindings = dict(cleaned.get("input_bindings") or {}) if isinstance(cleaned.get("input_bindings"), dict) else {}
    cleaned["input_roles"] = _sanitize_input_roles(
        cleaned.get("input_roles"),
        raw_data=raw_data,
        actions=actions,
        input_bindings=input_bindings,
    )
    if isinstance(cleaned.get("inputs"), dict):
        inputs = cleaned.get("inputs", {})
        nested = inputs.get("input_roles") or inputs.get("roles")
        nested_roles = _sanitize_input_roles(nested, raw_data=raw_data, actions=actions, input_bindings=input_bindings)
        if not nested_roles:
            nested_roles = _sanitize_input_roles(
                {key: value for key, value in inputs.items() if str(key).strip().strip("{}").replace("-", "_") in PLACEHOLDERS},
                raw_data=raw_data,
                actions=actions,
                input_bindings=input_bindings,
            )
        for key, value in nested_roles.items():
            if key not in cleaned["input_roles"]:
                cleaned["input_roles"][key] = value
                _record_action(actions, "promoted_input_role_from_inputs", placeholder=key)
    if input_bindings:
        cleaned["input_bindings"] = input_bindings
    _restore_required_archetype_input_roles(cleaned, actions)
    _sanitize_parameters(cleaned, actions)
    _sanitize_outputs(cleaned, actions)
    _sanitize_semantic_template(cleaned, actions)
    cleaned["sanitizer_actions"] = actions
    return cleaned


def _sanitize_entrypoint(spec: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    entrypoint = spec.get("entrypoint") if isinstance(spec.get("entrypoint"), dict) else {}
    if entrypoint:
        if not spec.get("original_command") and entrypoint.get("original_command"):
            spec["original_command"] = entrypoint.get("original_command")
            _record_action(actions, "promoted_entrypoint_original_command")
        if not spec.get("command_template") and entrypoint.get("command_template"):
            spec["command_template"] = entrypoint.get("command_template")
            _record_action(actions, "promoted_entrypoint_command_template")


def _sanitize_input_roles(
    raw: Any,
    *,
    raw_data: Path | None,
    actions: list[dict[str, Any]],
    input_bindings: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for raw_key, raw_spec in raw.items():
        key = _placeholder_key(str(raw_key or ""), raw_data=raw_data)
        if not key or key in {"code_dir", "input_dir", "output_dir", "work_dir"}:
            if input_bindings is not None and key != "input_dir" and _looks_like_open_input(str(raw_key), raw_spec):
                binding_key = _safe_binding_key(raw_key)
                input_bindings[binding_key] = _open_input_binding(raw_key, raw_spec, placeholder="input_file")
                normalized.setdefault(
                    "input_file",
                    {"role": "generic_input_file", "resolve_by": "uploaded_test_data_validation_only", "required": True},
                )
                _record_action(actions, "preserved_unknown_input_role_as_input_binding", key=str(raw_key), binding=binding_key)
                _record_action(actions, "mapped_unknown_input_role_to_generic", placeholder="input_file", semantic_hint=str((raw_spec or {}).get("role") if isinstance(raw_spec, dict) else raw_spec or raw_key))
            else:
                _record_action(actions, "dropped_invalid_input_role_key", key=str(raw_key))
            continue
        if key != str(raw_key).strip().strip("{}"):
            _record_action(actions, "normalized_input_role_key", **{"from": str(raw_key), "to": key})
        item = dict(raw_spec) if isinstance(raw_spec, dict) else {"role": str(raw_spec)}
        role = str(item.get("role") or "").strip()
        mapped_role = ROLE_ALIASES.get(role, role)
        if mapped_role != role:
            _record_action(actions, "normalized_input_role_alias", **{"from": role, "to": mapped_role})
        if mapped_role and mapped_role not in INPUT_ROLES:
            if input_bindings is not None:
                binding_key = _safe_binding_key(raw_key)
                input_bindings[binding_key] = _open_input_binding(raw_key, raw_spec, placeholder=key)
                _record_action(actions, "preserved_unknown_input_role_as_input_binding", key=str(raw_key), binding=binding_key)
            item["semantic_hint"] = mapped_role
            mapped_role = "generic_input_file"
            _record_action(actions, "mapped_unknown_input_role_to_generic", placeholder=key, semantic_hint=item.get("semantic_hint", ""))
        item["role"] = mapped_role
        item.setdefault("resolve_by", "uploaded_test_data_validation_only")
        item.setdefault("required", True)
        normalized[key] = item
    return normalized


def _looks_like_open_input(raw_key: str, raw_spec: Any) -> bool:
    text = str(raw_key or "").lower()
    if text in {"code_dir", "output_dir", "work_dir"}:
        return False
    if any(token in text for token in ("input", "file", "vcf", "trait", "table", "bam", "bed", "json", "csv", "tsv", "matrix", "dir")):
        return True
    if isinstance(raw_spec, dict):
        joined = " ".join(str(raw_spec.get(key, "")) for key in ("role", "format", "format_hint", "description", "path"))
        return bool(joined.strip())
    return bool(str(raw_spec or "").strip())


def _open_input_binding(raw_key: Any, raw_spec: Any, *, placeholder: str) -> dict[str, Any]:
    item = dict(raw_spec) if isinstance(raw_spec, dict) else {"role": str(raw_spec or "")}
    role = str(item.get("role") or raw_key or "").strip()
    binding = {
        "source_key": str(raw_key),
        "placeholder": placeholder,
        "required": bool(item.get("required", True)),
        "semantic_hint": role,
        "format_hint": str(item.get("format_hint") or item.get("format") or _format_hint_from_name(str(raw_key))),
        "path_hint": str(item.get("path") or item.get("example") or ""),
        "description": str(item.get("description") or ""),
    }
    if str(item.get("type") or "").strip():
        binding["type"] = str(item.get("type")).strip()
    else:
        binding["type"] = "directory" if "dir" in str(raw_key).lower() else "file"
    return binding


def _safe_binding_key(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip().strip("{}"))
    text = re.sub(r"_+", "_", text).strip("_").lower()
    return text or "input_file"


def _format_hint_from_name(value: str) -> str:
    lower = value.lower()
    for token in ("vcf", "bam", "bed", "json", "csv", "tsv", "txt", "fa", "fasta", "gz"):
        if token in lower:
            return token
    return ""


def _restore_required_archetype_input_roles(spec: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    archetype_id = str(spec.get("archetype_id") or "").strip()
    archetype = ARCHETYPE_BY_ID.get(archetype_id)
    if not archetype:
        return
    roles = spec.get("input_roles")
    if not isinstance(roles, dict):
        roles = {}
        spec["input_roles"] = roles
    for placeholder, role_spec in (archetype.input_roles or {}).items():
        if placeholder in roles:
            continue
        if not isinstance(role_spec, dict) or not bool(role_spec.get("required", True)):
            continue
        if placeholder not in PLACEHOLDERS or placeholder in {"code_dir", "input_dir", "output_dir", "work_dir"}:
            continue
        restored = dict(role_spec)
        restored.setdefault("resolve_by", "uploaded_test_data_validation_only")
        restored.setdefault("required", True)
        roles[placeholder] = restored
        _record_action(
            actions,
            "restored_required_archetype_input_role",
            archetype_id=archetype_id,
            placeholder=placeholder,
            role=restored.get("role", ""),
        )


def _placeholder_key(value: str, *, raw_data: Path | None) -> str:
    key = value.strip().strip("{}").replace("-", "_")
    if key in PLACEHOLDERS:
        return key
    lower = Path(key).name.lower()
    if lower in PLACEHOLDERS:
        return lower
    if lower.startswith("input.") or lower.startswith("input_"):
        if any(token in lower for token in ("domtbl", "tblout", "tsv", "csv", "txt", "blast", "collinearity")):
            return "input_file"
        if any(token in lower for token in ("hmm", "hmm3")):
            return "input_hmm"
        if any(token in lower for token in ("ids", "id", "geneid")):
            return "input_id_file"
        if any(token in lower for token in ("pep", "faa", "fasta", ".fa", "cds")):
            return "input_fasta"
    if lower in {"query_fasta", "query_fa", "input_query", "input_query_fa"}:
        return "input_query_fasta"
    if lower in {"reference_fasta", "reference_fa", "db_fasta", "input_db_fasta", "input_reference"}:
        return "input_reference_fasta"
    if raw_data:
        path = raw_data / Path(value).name
        if path.exists():
            return _placeholder_from_file(path)
    return ""


def _placeholder_from_file(path: Path) -> str:
    lower = path.name.lower()
    suffix = path.suffix.lower()
    if suffix in {".hmm", ".hmm3"} or "hmm" in lower:
        return "input_hmm"
    if suffix in {".fa", ".faa", ".fasta", ".fsa", ".pep", ".cds"} or any(token in lower for token in (".pep.", ".protein.")):
        return "input_fasta"
    if any(token in lower for token in ("ids", "geneid")) or suffix in {".id", ".ids", ".list"}:
        return "input_id_file"
    return "input_file"


def _sanitize_parameters(spec: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    template = str(spec.get("command_template") or "")
    parameters = spec.get("parameters")
    if not isinstance(parameters, dict):
        spec["parameters"] = {}
        return
    used = set(re.findall(r"\{param_([A-Za-z_][A-Za-z0-9_]*)\}", template))
    cleaned: dict[str, Any] = {}
    for raw_name, raw_value in parameters.items():
        name = _safe_parameter_name(raw_name)
        if not name:
            continue
        item = dict(raw_value) if isinstance(raw_value, dict) else {"default": raw_value}
        if bool(item.get("editable", True)) and name not in used:
            item["editable"] = False
            item.setdefault("advanced", True)
            _record_action(actions, "locked_unapplied_editable_parameter", parameter=name)
        cleaned[name] = item
    spec["parameters"] = cleaned


def _sanitize_outputs(spec: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    contract = spec.get("output_contract")
    outputs = spec.get("outputs") if isinstance(spec.get("outputs"), dict) else {}
    if not contract and isinstance(outputs, dict) and isinstance(outputs.get("output_contract"), list):
        contract = outputs.get("output_contract")
        spec["output_contract"] = contract
        _record_action(actions, "promoted_nested_output_contract")
    if isinstance(contract, dict):
        spec["output_contract"] = list(contract.values())
        _record_action(actions, "converted_output_contract_object_to_list")
    elif not isinstance(contract, list):
        patterns = outputs.get("patterns") if isinstance(outputs, dict) else None
        spec["output_contract"] = [{"name_pattern": pattern, "required": True, "primary": index == 0} for index, pattern in enumerate(patterns or ["*"])]
        _record_action(actions, "created_output_contract_from_outputs")


def _sanitize_semantic_template(spec: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    entrypoint = spec.get("entrypoint") if isinstance(spec.get("entrypoint"), dict) else {}
    semantic_present = "semantic_command_template" in spec or "semantic_command_template" in entrypoint
    semantic = str(spec.get("semantic_command_template") or entrypoint.get("semantic_command_template") or "")
    if semantic:
        reason = _semantic_template_problem(semantic)
        if not reason:
            return
        spec["semantic_command_template"] = ""
        if entrypoint:
            entrypoint = dict(entrypoint)
            entrypoint["semantic_command_template"] = ""
            spec["entrypoint"] = entrypoint
        _record_action(actions, "disabled_unsafe_semantic_command_template", reason=reason, preview=semantic[:300])
        return

    template = str(spec.get("command_template") or entrypoint.get("command_template") or "")
    reason = _semantic_template_problem(template) if template else ""
    if reason and not semantic_present:
        spec["semantic_command_template"] = ""
        if entrypoint:
            entrypoint = dict(entrypoint)
            entrypoint["semantic_command_template"] = ""
            spec["entrypoint"] = entrypoint
        _record_action(actions, "disabled_unsafe_command_template_as_semantic", reason=reason, preview=template[:300])


def _semantic_template_problem(template: str) -> str:
    normalized = str(template or "").strip()
    if "{code_dir}/" in normalized and "{code_dir}/run_extension.sh" not in normalized:
        return "semantic template calls uploaded code directly with API-guessed arguments"
    if "{output_dir}" in normalized and ">" not in normalized and " --out" not in normalized and " -o" not in normalized:
        return "semantic template does not contain an explicit output-writing operation"
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as exc:
        return f"format parse failed: {exc}"
    bad_fields = sorted(
        str(field)
        for _, field, _, _ in parsed
        if field and field not in PLACEHOLDERS and not str(field).startswith("param_")
    )
    if bad_fields:
        return "unsupported format field(s): " + ", ".join(bad_fields)
    return ""


def _safe_parameter_name(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip())
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        return ""
    if text[0].isdigit():
        text = "p_" + text
    if text.startswith("param_"):
        text = text[6:]
    return text
