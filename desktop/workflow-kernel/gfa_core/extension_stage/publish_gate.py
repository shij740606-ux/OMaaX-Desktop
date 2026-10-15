"""Publish gate checks for extension stages."""

from __future__ import annotations

import re
from typing import Any

from gfa_core.extension_stage.spec_v2 import ExtensionSpecV2Error, infer_analysis_type, normalize_spec_v2, semantic_placeholders
from gfa_core.extension_stage.template_gate import LEGACY_TEMPLATE_GATE_STATUS, evaluate_template_gate, template_gate_publish_status


def publish_checks(extension: dict[str, Any], merged_values: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    merged_values = dict(merged_values or {})
    if not merged_values:
        for raw_name, spec in ((extension.get("confirmed_parameters") or extension.get("parameters") or {}) or {}).items():
            name = safe_parameter_name(raw_name)
            if not name:
                continue
            if isinstance(spec, dict):
                merged_values[name] = spec.get("current_value", spec.get("default", spec.get("value", "")))
            else:
                merged_values[name] = spec
    try:
        normalized_spec = normalize_spec_v2(extension, require_code_artifacts=(extension.get("converter") or {}).get("type") == "deepseek_api")
    except ExtensionSpecV2Error as exc:
        normalized_spec = {}
        errors.append("spec_v2 validation failed: " + str(exc))
    if (extension.get("converter") or {}).get("type") in {"rule", "local_rule"}:
        errors.append("rule/local_rule converter output cannot be published for natural-language execution without spec_v2 migration")
    analysis_type = str((normalized_spec or {}).get("analysis_type") or infer_analysis_type(extension))
    template_for_semantics = _semantic_template(extension) or str((extension.get("entrypoint") or {}).get("command_template") or "")
    if analysis_type != "generic_cli" and semantic_placeholders(template_for_semantics) and not extension.get("input_roles"):
        errors.append("semantic command_template placeholders require input_roles")
    if extension.get("status") not in {"validated", "confirmed", "published"}:
        errors.append("validate successfully before publishing")
    if extension.get("status") != "published" and not extension.get("confirmed_at"):
        errors.append("confirm parameters and outputs before publishing")
    if not extension.get("confirmed_parameters"):
        if extension.get("parameters"):
            errors.append("confirm parameters before publishing")
    outputs = extension.get("outputs_confirmed") or []
    if not outputs:
        errors.append("confirm at least one output, or explicitly mark this extension as no-file-output in a later version")
    if not extension.get("input_roles") and not extension.get("input_bindings"):
        errors.append("confirm semantic input_roles or input_bindings before publishing")
    if not extension.get("output_contract"):
        errors.append("confirm output_contract before publishing")
    if not extension.get("generated_code_artifacts") and (extension.get("converter") or {}).get("type") == "deepseek_api":
        errors.append("DeepSeek-generated extensions must persist generated_code_artifacts")
    if not extension.get("build_record") and (extension.get("converter") or {}).get("type") == "deepseek_api":
        errors.append("DeepSeek-generated extensions must persist extension_build.json provenance")
    last_validation = extension.get("last_validation") or {}
    if last_validation.get("status") != "success":
        errors.append("last validation did not succeed")
    golden = extension.get("golden_comparison") or last_validation.get("golden_comparison") or {}
    if golden.get("status") != "passed":
        errors.append("golden original-vs-converted comparison did not pass")
    if _semantic_template(extension):
        if (golden.get("semantic_validation") or {}).get("status") != "success":
            errors.append("semantic command_template did not run successfully in golden validation")
        if (golden.get("semantic_comparison") or {}).get("status") != "passed":
            errors.append("semantic command_template does not match original behavior baseline")
    template_gate_status = template_gate_publish_status(extension)
    gate = extension.get("template_gate") or {}
    if template_gate_status != LEGACY_TEMPLATE_GATE_STATUS:
        gate = gate or evaluate_template_gate(extension)
        template_gate_status = str(gate.get("status") or "")
    template = str((extension.get("entrypoint") or {}).get("command_template") or "")
    if "{output_dir}" not in template:
        errors.append("command_template must write outputs under {output_dir}")
    missing = sorted(missing_parameter_placeholders(template, merged_values))
    if missing:
        errors.append("missing parameter defaults: " + ", ".join(missing))
    unapplied = sorted(unapplied_editable_parameters(template, extension.get("confirmed_parameters") or {}))
    if unapplied:
        errors.append("editable parameters are not used by command_template: " + ", ".join(unapplied))
    severity = "passed" if not errors else "blocked"
    return {
        "passed": not errors,
        "severity": severity,
        "analysis_type": analysis_type,
        "template_gate_status": template_gate_status,
        "template_eligible": template_gate_status == "passed",
        "template_gate_errors": list((gate or {}).get("errors") or []),
        "published_runnable": not errors,
        "errors": errors,
    }


def _semantic_template(extension: dict[str, Any]) -> str:
    entrypoint = extension.get("entrypoint") or {}
    return str(extension.get("semantic_command_template") or entrypoint.get("semantic_command_template") or "").strip()


def missing_parameter_placeholders(template: str, values: dict[str, Any]) -> set[str]:
    names = set(re.findall(r"\{param_([A-Za-z_][A-Za-z0-9_]*)\}", template or ""))
    return {name for name in names if name not in values}


def unapplied_editable_parameters(template: str, parameters: dict[str, Any]) -> set[str]:
    used = set(re.findall(r"\{param_([A-Za-z_][A-Za-z0-9_]*)\}", template or ""))
    unapplied: set[str] = set()
    for raw_name, spec in (parameters or {}).items():
        name = safe_parameter_name(raw_name)
        editable = True
        if isinstance(spec, dict):
            editable = bool(spec.get("editable", True))
        if editable and name and name not in used:
            unapplied.add(name)
    return unapplied


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
