"""Extension-stage v2 contract primitives.

This module contains deterministic validation rules shared by API conversion,
runtime binding, publish gates, and repair gates.  LLM/API output is treated as
a candidate spec until it passes these local rules.
"""

from __future__ import annotations

import re
from typing import Any


SPEC_VERSION = "extension_stage_v2"

ANALYSIS_TYPES = {
    "sequence_extraction",
    "blast_search",
    "hmmer_search",
    "table_filter",
    "id_cleaning",
    "fasta_header_cleaning",
    "gff_processing",
    "plot_render",
    "enrichment_or_stats",
    "generic_cli",
}

TOOL_FAMILIES = {
    "seqtk",
    "blast",
    "hmmer",
    "awk_shell",
    "python",
    "r",
    "generic",
}

INPUT_ROLES = {
    "target_pep_fasta",
    "reference_pep_fasta",
    "target_cds_fasta",
    "target_id_list",
    "hmm_profile",
    "annotation_gff",
    "generic_input_file",
}

PLACEHOLDERS = {
    "code_dir",
    "input_dir",
    "input_file",
    "input_hmm",
    "input_fasta",
    "input_query_fasta",
    "input_reference_fasta",
    "input_pep_fasta",
    "input_cds_fasta",
    "input_id_file",
    "output_dir",
    "work_dir",
}

SEMANTIC_PLACEHOLDERS = {
    "input_hmm",
    "input_fasta",
    "input_query_fasta",
    "input_reference_fasta",
    "input_pep_fasta",
    "input_cds_fasta",
    "input_id_file",
}

ROLE_ALIASES = {
    "target_query_pep_fasta": "target_pep_fasta",
    "reference_or_database_pep_fasta": "reference_pep_fasta",
    "reference_database_pep_fasta": "reference_pep_fasta",
    "hmm_file": "hmm_profile",
    "generic_fasta": "generic_input_file",
    "input_table": "generic_input_file",
    "annotation_file": "annotation_gff",
}

ROLE_BY_PLACEHOLDER = {
    "input_hmm": {"hmm_profile"},
    "input_fasta": {"target_pep_fasta", "target_cds_fasta", "generic_input_file"},
    "input_query_fasta": {"target_pep_fasta"},
    "input_reference_fasta": {"reference_pep_fasta"},
    "input_pep_fasta": {"target_pep_fasta"},
    "input_cds_fasta": {"target_cds_fasta"},
    "input_id_file": {"target_id_list"},
    "input_file": {"target_id_list", "annotation_gff", "generic_input_file"},
}


class ExtensionSpecV2Error(RuntimeError):
    """Raised when a candidate extension spec violates local contract rules."""


def safe_parameter_name(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip())
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        return ""
    if text[0].isdigit():
        text = "p_" + text
    if text.startswith("param_"):
        text = text[6:]
    return text


def infer_analysis_type(extension_or_spec: dict[str, Any]) -> str:
    template = _template(extension_or_spec).lower()
    requirements = " ".join(str(x).lower() for x in ((extension_or_spec.get("requirements") or {}).get("system") or []))
    name = " ".join(str(extension_or_spec.get(k, "")).lower() for k in ("name", "display_name", "description", "extension_id"))
    if "blastp" in template or "blastp" in requirements:
        return "blast_search"
    if "hmmsearch" in template or "hmmscan" in template or "hmmbuild" in template or "hmmer" in name:
        return "hmmer_search"
    if "seqtk" in template and "subseq" in template:
        return "sequence_extraction"
    if any(token in name for token in ("clean_id", "id_clean", "header_clean")):
        return "fasta_header_cleaning"
    if "sequence_extraction" in name or "protein_sequence_extraction" in name or "cds_sequence_extraction" in name:
        return "sequence_extraction"
    if "cds" in name and "subseq" in template:
        return "sequence_extraction"
    if any(token in template for token in ("awk", "cut ", "grep ")):
        return "table_filter"
    return "generic_cli"


def infer_tool_family(extension_or_spec: dict[str, Any]) -> str:
    template = _template(extension_or_spec).lower()
    requirements = " ".join(str(x).lower() for x in ((extension_or_spec.get("requirements") or {}).get("system") or []))
    if "blastp" in template or "blastp" in requirements:
        return "blast"
    if any(token in template or token in requirements for token in ("hmmsearch", "hmmscan", "hmmbuild", "hmmer")):
        return "hmmer"
    if "seqtk" in template or "seqtk" in requirements:
        return "seqtk"
    if any(token in template for token in ("awk", "grep", "sort ")):
        return "awk_shell"
    runtime = extension_or_spec.get("runtime") or {}
    language = str(runtime.get("language") or "").lower()
    if language in {"python", "r"}:
        return language
    return "generic"


def used_placeholders(template: str) -> set[str]:
    return set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template or ""))


def unsupported_placeholders(template: str) -> set[str]:
    names = used_placeholders(template)
    return {name for name in names if name not in PLACEHOLDERS and not name.startswith("param_")}


def semantic_placeholders(template: str) -> set[str]:
    return used_placeholders(template) & SEMANTIC_PLACEHOLDERS


def normalize_input_roles(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for placeholder, role_spec in raw.items():
        key = str(placeholder or "").strip().strip("{}")
        if key not in PLACEHOLDERS:
            errors.append(f"{key}: unknown placeholder")
            continue
        if isinstance(role_spec, dict):
            item = dict(role_spec)
        else:
            item = {"role": str(role_spec)}
        role = str(item.get("role") or "").strip()
        role = ROLE_ALIASES.get(role, role)
        if role and role not in INPUT_ROLES:
            errors.append(f"{key}: unknown role {role}")
            continue
        allowed = ROLE_BY_PLACEHOLDER.get(key)
        if role and allowed and role not in allowed:
            errors.append(f"{key}: role {role} is not compatible with placeholder {key}")
            continue
        item["role"] = role
        item.setdefault("resolve_by", "")
        item.setdefault("required", True)
        normalized[key] = item
    if errors:
        raise ExtensionSpecV2Error("invalid input_roles: " + "; ".join(errors))
    return normalized


def normalize_input_bindings(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for raw_name, raw_spec in raw.items():
        name = safe_parameter_name(raw_name)
        if not name:
            continue
        item = dict(raw_spec) if isinstance(raw_spec, dict) else {"semantic_hint": str(raw_spec)}
        placeholder = str(item.get("placeholder") or "input_file").strip().strip("{}")
        if placeholder not in PLACEHOLDERS:
            placeholder = "input_file"
        if placeholder in {"code_dir", "output_dir", "work_dir"}:
            placeholder = "input_file"
        item["placeholder"] = placeholder
        item.setdefault("type", "directory" if placeholder == "input_dir" else "file")
        item.setdefault("required", True)
        item.setdefault("semantic_hint", str(raw_name))
        item.setdefault("format_hint", "")
        item.setdefault("description", "")
        normalized[name] = item
    return normalized


def normalize_parameters(parameters: dict[str, Any], template: str) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    used = {name[6:] for name in used_placeholders(template) if name.startswith("param_")}
    for raw_name, raw_value in (parameters or {}).items():
        name = safe_parameter_name(raw_name)
        if not name:
            continue
        item = dict(raw_value) if isinstance(raw_value, dict) else {"default": raw_value}
        editable = bool(item.get("editable", True))
        if editable and name not in used:
            raise ExtensionSpecV2Error(f"editable parameter is not used in command_template: {name}")
        item.setdefault("editable", editable)
        normalized[name] = item
    return normalized


def normalize_output_contract(spec: dict[str, Any]) -> list[dict[str, Any]]:
    raw = spec.get("output_contract")
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = list(raw.values())
    else:
        outputs = spec.get("outputs") or {}
        patterns = outputs.get("patterns") if isinstance(outputs, dict) else []
        items = [{"name_pattern": pattern, "required": True, "primary": index == 0} for index, pattern in enumerate(patterns or ["*"])]
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if isinstance(item, str):
            item = {"name_pattern": item}
        if not isinstance(item, dict):
            continue
        record = dict(item)
        record.setdefault("name_pattern", record.get("path") or record.get("name") or "*")
        record.setdefault("type", "file")
        record.setdefault("primary", index == 0)
        record.setdefault("required", True)
        record.setdefault("allow_empty", False)
        record.setdefault("biological_meaning", "")
        record.setdefault("semantic_role", record.get("role") or "")
        record.setdefault("compare_mode", record.get("validator") or "auto")
        record.setdefault("required_for_golden", bool(record.get("required", True)))
        record.setdefault("allow_name_change", True)
        record.setdefault("ignore_patterns", [])
        normalized.append(record)
    if not normalized:
        normalized.append({"name_pattern": "*", "type": "file", "primary": True, "required": True, "allow_empty": False, "biological_meaning": ""})
    return normalized



def normalize_comparator_plan(raw: Any, output_contract: list[dict[str, Any]]) -> dict[str, Any]:
    if isinstance(raw, dict):
        plan = dict(raw)
    else:
        plan = {}
    plan.setdefault("mode", "pipeline_golden_diff")
    plan.setdefault("default_compare", "auto")
    steps = plan.get("steps")
    if not isinstance(steps, list):
        steps = []
    if not steps:
        for item in output_contract:
            if bool(item.get("required_for_golden", True)):
                steps.append({
                    "name_pattern": item.get("name_pattern", "*"),
                    "semantic_role": item.get("semantic_role", ""),
                    "compare_mode": item.get("compare_mode", "auto"),
                    "required": bool(item.get("required", True)),
                })
    plan["steps"] = steps
    return plan

def normalize_spec_v2(spec: dict[str, Any], *, require_code_artifacts: bool = False) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise ExtensionSpecV2Error("extension spec must be a JSON object")
    normalized = dict(spec)
    entrypoint = normalized.get("entrypoint") if isinstance(normalized.get("entrypoint"), dict) else {}
    original = str(normalized.get("original_command") or entrypoint.get("original_command") or "").strip()
    template = str(normalized.get("command_template") or entrypoint.get("command_template") or "").strip()
    if not original:
        raise ExtensionSpecV2Error("conversion spec missing original_command")
    if not template:
        raise ExtensionSpecV2Error("conversion spec missing command_template")
    if "{output_dir}" not in template:
        raise ExtensionSpecV2Error("command_template must write outputs below {output_dir}")
    unsupported = sorted(unsupported_placeholders(template))
    if unsupported:
        raise ExtensionSpecV2Error("unsupported placeholders: " + ", ".join(unsupported))
    artifacts = normalized.get("code_artifacts") or normalized.get("generated_code_artifacts") or []
    if require_code_artifacts and not artifacts:
        raise ExtensionSpecV2Error("conversion spec must include API-generated code_artifacts")
    if artifacts and not isinstance(artifacts, list):
        raise ExtensionSpecV2Error("code_artifacts must be a list")

    normalized["schema_version"] = SPEC_VERSION
    normalized["original_command"] = original
    normalized["command_template"] = template
    analysis_type = str(normalized.get("analysis_type") or infer_analysis_type(normalized))
    tool_family = str(normalized.get("tool_family") or infer_tool_family(normalized))
    if analysis_type not in ANALYSIS_TYPES:
        raise ExtensionSpecV2Error(f"unsupported analysis_type: {analysis_type}")
    if tool_family not in TOOL_FAMILIES:
        raise ExtensionSpecV2Error(f"unsupported tool_family: {tool_family}")
    normalized["analysis_type"] = analysis_type
    normalized["tool_family"] = tool_family
    raw_roles = normalized.get("input_roles")
    if not isinstance(raw_roles, dict) and isinstance(normalized.get("inputs"), dict):
        raw_roles = normalized.get("inputs", {}).get("input_roles")
    normalized["input_roles"] = normalize_input_roles(raw_roles)
    normalized["input_bindings"] = normalize_input_bindings(normalized.get("input_bindings") or {})
    _require_roles_for_semantic_placeholders(template, normalized["input_roles"])
    normalized["parameters"] = normalize_parameters(normalized.get("parameters") or {}, template)
    normalized["output_contract"] = normalize_output_contract(normalized)
    normalized["comparator_plan"] = normalize_comparator_plan(normalized.get("comparator_plan") or {}, normalized["output_contract"])
    normalized.setdefault("outputs", {"directory": "outputs", "patterns": ["*"]})
    normalized.setdefault("template_type", "shell_pipeline")
    normalized.setdefault("requires_user_confirmation", not bool(normalized["input_roles"]))
    return normalized


def contract_regressions(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    before_roles = normalize_input_roles(before.get("input_roles") or {})
    after_roles = normalize_input_roles(after.get("input_roles") or {})
    for placeholder, spec in before_roles.items():
        if bool(spec.get("required", True)) and placeholder not in after_roles:
            errors.append(f"repair removed required input role: {placeholder}")
    before_params = set((before.get("confirmed_parameters") or before.get("parameters") or {}).keys())
    after_params = set((after.get("parameters") or {}).keys())
    removed_params = sorted(safe_parameter_name(p) for p in before_params if safe_parameter_name(p) not in after_params)
    if removed_params:
        errors.append("repair removed parameter(s): " + ", ".join(removed_params))
    before_type = before.get("analysis_type") or infer_analysis_type(before)
    after_type = after.get("analysis_type") or infer_analysis_type(after)
    if before_type != after_type:
        errors.append(f"repair changed analysis_type from {before_type} to {after_type}")
    before_contract = normalize_output_contract(before)
    after_contract = normalize_output_contract(after)
    if before_contract and len(after_contract) == 1 and after_contract[0].get("name_pattern") == "*":
        if not (len(before_contract) == 1 and before_contract[0].get("name_pattern") == "*"):
            errors.append("repair degraded output_contract to wildcard")
    before_plan = normalize_comparator_plan(before.get("comparator_plan") or {}, before_contract)
    after_plan = normalize_comparator_plan(after.get("comparator_plan") or {}, after_contract)
    if before_plan.get("steps") and not after_plan.get("steps"):
        errors.append("repair removed comparator_plan steps")
    if before_type == "blast_search":
        before_template = _template(before)
        after_template = _template(after)
        if "input_query_fasta" in before_template and "input_reference_fasta" in before_template:
            if "input_query_fasta" not in after_template or "input_reference_fasta" not in after_template:
                errors.append("repair changed BLAST query/reference semantic placeholders")
    return errors


def _require_roles_for_semantic_placeholders(template: str, roles: dict[str, dict[str, Any]]) -> None:
    missing = []
    for placeholder in sorted(semantic_placeholders(template)):
        if placeholder in {"input_fasta"}:
            continue
        role = roles.get(placeholder, {}).get("role")
        if not role:
            missing.append(placeholder)
    if missing:
        raise ExtensionSpecV2Error("semantic placeholders missing input_roles: " + ", ".join(missing))


def _template(value: dict[str, Any]) -> str:
    entrypoint = value.get("entrypoint") if isinstance(value.get("entrypoint"), dict) else {}
    return str(value.get("command_template") or entrypoint.get("command_template") or "")
