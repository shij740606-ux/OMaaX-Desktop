"""Deterministic wrapper generation for extension-stage conversions.

The wrapper preserves uploaded source behavior by running the original command in
an isolated work directory. It materializes runtime inputs under the same
filenames used by raw_test_data, runs original_command unchanged, then copies
new outputs to --output-dir.
"""

from __future__ import annotations

import fnmatch
import re
import shlex
from pathlib import Path
from typing import Any

from gfa_core.extension_stage.spec_v2 import PLACEHOLDERS, semantic_placeholders, safe_parameter_name, used_placeholders


class DeterministicWrapperError(RuntimeError):
    """Raised when a deterministic wrapper cannot be generated safely."""


FASTA_SUFFIXES = {".fa", ".faa", ".fasta", ".fsa", ".pep", ".cds"}
ID_SUFFIXES = {".id", ".ids", ".list", ".geneid", ".geneids", ".txt", ".tsv", ".csv"}


def build_deterministic_wrapper_spec(spec: dict[str, Any], *, raw_code: Path, raw_data: Path) -> dict[str, Any]:
    """Return a spec whose executable wrapper is generated locally, not by LLM."""
    original_command = str((spec.get("entrypoint") or {}).get("original_command") or spec.get("original_command") or "").strip()
    if not original_command:
        raise DeterministicWrapperError("deterministic wrapper requires concrete original_command")
    if "<" in original_command and ">" in original_command and any(token in original_command for token in ("<input", "<output")):
        raise DeterministicWrapperError("original_command contains abstract placeholders and is not directly runnable")
    abstract_placeholders = _abstract_command_placeholders(original_command)
    if abstract_placeholders:
        raise DeterministicWrapperError(
            "original_command contains template placeholders and is not directly runnable: "
            + ", ".join(abstract_placeholders)
        )

    normalized = dict(spec)
    raw_files = sorted(p for p in Path(raw_data).iterdir() if p.is_file()) if Path(raw_data).exists() else []
    raw_code_files = sorted(p for p in Path(raw_code).iterdir() if p.is_file()) if Path(raw_code).exists() else []
    input_roles = dict(normalized.get("input_roles") or {})
    entrypoint = normalized.get("entrypoint") if isinstance(normalized.get("entrypoint"), dict) else {}
    template = str(normalized.get("command_template") or entrypoint.get("command_template") or "")
    if "semantic_command_template" in normalized or "semantic_command_template" in entrypoint:
        semantic_template = str(normalized.get("semantic_command_template") or entrypoint.get("semantic_command_template") or "").strip()
    else:
        semantic_template = _semantic_template_candidate(template)
    template_placeholders = semantic_placeholders(template)
    declared_inputs = {str(k) for k in input_roles if str(k).startswith("input_")}
    # Deterministic wrappers must expose every declared semantic input as an
    # explicit CLI flag.  Relying on --input-dir to copy undeclared files makes
    # trial runs pass while production/job runs silently miss inputs.
    placeholders = sorted(template_placeholders | declared_inputs)

    input_mapping = _infer_input_mapping(placeholders, input_roles, raw_files)
    missing_required = sorted(
        key for key in declared_inputs
        if key not in input_mapping and bool((input_roles.get(key) or {}).get("required", True))
    )
    if missing_required:
        raise DeterministicWrapperError("cannot bind required semantic input(s): " + ", ".join(missing_required))
    parameters = _lock_parameters(normalized.get("parameters") or {})
    normalized["output_contract"] = _align_output_contract_to_original(
        normalized.get("output_contract") or [],
        original_command=original_command,
    )
    command_template = _command_template(input_mapping.keys())
    wrapper = _render_shell_wrapper(
        original_command=original_command,
        input_mapping=input_mapping,
        raw_input_names=[p.name for p in raw_files],
        raw_code_names=[p.name for p in raw_code_files],
    )

    normalized["command_template"] = command_template
    normalized["wrapper_command_template"] = command_template
    if semantic_template:
        normalized["semantic_command_template"] = semantic_template
    entrypoint = dict(entrypoint)
    entrypoint["original_command"] = original_command
    entrypoint["command_template"] = command_template
    entrypoint["wrapper_command_template"] = command_template
    if semantic_template:
        entrypoint["semantic_command_template"] = semantic_template
    normalized["entrypoint"] = entrypoint
    normalized["parameters"] = parameters
    normalized["code_artifacts"] = [{"path": "run_extension.sh", "content": wrapper, "role": "deterministic_wrapper"}]
    normalized["generated_code_artifacts"] = normalized["code_artifacts"]
    converter = dict(normalized.get("converter") or {})
    converter.setdefault("type", "deepseek_api")
    converter["wrapper_builder"] = "deterministic_original_command_v1"
    normalized["converter"] = converter
    notes = str(normalized.get("notes") or "").strip()
    deterministic_note = "System generated deterministic wrapper; original command semantics are preserved."
    normalized["notes"] = (notes + " " + deterministic_note).strip() if notes else deterministic_note
    normalized["wrapper_policy"] = {
        "type": "deterministic_original_command_v1",
        "input_mapping": input_mapping,
        "parameters_locked": [name for name, item in parameters.items() if isinstance(item, dict) and not item.get("editable", True)],
    }
    return normalized


def _command_template(input_keys: Any) -> str:
    parts = ["bash {code_dir}/run_extension.sh", "--input-dir {input_dir}"]
    for key in sorted(input_keys):
        parts.append(f"--{str(key).replace('_', '-')} {{{key}}}")
    parts.append("--output-dir {output_dir}")
    return " ".join(parts)


def _abstract_command_placeholders(command: str) -> list[str]:
    names = used_placeholders(command)
    abstract = sorted(name for name in names if name in PLACEHOLDERS or name.startswith("param_"))
    return abstract


def _semantic_template_candidate(template: str) -> str:
    template = str(template or "").strip()
    if not template:
        return ""
    if template.startswith("bash {code_dir}/run_extension.sh"):
        return ""
    names = used_placeholders(template)
    if names & set(PLACEHOLDERS) or any(name.startswith("param_") for name in names):
        return template
    return ""


def _lock_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    locked: dict[str, Any] = {}
    for raw_name, raw_value in (parameters or {}).items():
        name = safe_parameter_name(raw_name)
        if not name:
            continue
        item = dict(raw_value) if isinstance(raw_value, dict) else {"default": raw_value}
        item["editable"] = False
        item.setdefault("advanced", True)
        item.setdefault("source", "deterministic_wrapper_locked")
        description = str(item.get("description") or "").strip()
        suffix = "Locked because deterministic wrapper preserves the original command without reinterpreting parameters."
        item["description"] = (description + " " + suffix).strip() if description else suffix
        locked[name] = item
    return locked


def _align_output_contract_to_original(output_contract: list[Any], *, original_command: str) -> list[dict[str, Any]]:
    outputs = _redirect_output_names(original_command)
    contract = [dict(item) for item in (output_contract or []) if isinstance(item, dict)]
    if not outputs:
        return contract
    unmatched_outputs = [name for name in outputs if not _contract_matches_output(contract, name)]
    if not unmatched_outputs:
        return contract
    matched_any_output = any(_contract_matches_output(contract, name) for name in outputs)
    if contract and not matched_any_output:
        first = contract[0]
        first.setdefault("original_name_pattern", first.get("name_pattern", ""))
        first["name_pattern"] = unmatched_outputs[0]
        first["required"] = True
        first.setdefault("allow_empty", False)
        first.setdefault("required_for_golden", True)
        first.setdefault("source", "deterministic_original_command_redirect")
        for name in unmatched_outputs[1:]:
            contract.append(_redirect_output_contract(name))
        return contract
    for name in unmatched_outputs:
        contract.append(_redirect_output_contract(name))
    return contract


def _redirect_output_contract(name: str) -> dict[str, Any]:
    return {
        "name_pattern": name,
        "type": _output_type_from_name(name),
        "primary": False,
        "required": True,
        "allow_empty": False,
        "biological_meaning": "Output produced by original command redirection",
        "semantic_role": "original_redirect_output",
        "compare_mode": "auto",
        "required_for_golden": True,
        "source": "deterministic_original_command_redirect",
    }


def _contract_matches_output(contract: list[dict[str, Any]], output_name: str) -> bool:
    for item in contract:
        pattern = str(item.get("name_pattern") or item.get("path") or item.get("name") or "")
        if pattern and fnmatch.fnmatch(output_name, Path(pattern).name):
            return True
    return False


def _redirect_output_names(command: str) -> list[str]:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return []
    names: list[str] = []
    for idx, token in enumerate(tokens):
        target = ""
        if token in {">", "1>", "2>"} and idx + 1 < len(tokens):
            target = tokens[idx + 1]
        elif token.startswith(">") and len(token) > 1:
            target = token[1:]
        elif token.startswith("1>") and len(token) > 2:
            target = token[2:]
        if not target or target.startswith("&") or "$" in target or "{" in target or "}" in target:
            continue
        name = Path(target).name
        if name and name not in names:
            names.append(name)
    return names


def _output_type_from_name(name: str) -> str:
    lower = name.lower()
    if lower.endswith((".fa", ".fasta", ".faa", ".fsa")) or ".pep." in lower:
        return "fasta"
    if lower.endswith((".tsv", ".csv")):
        return "table"
    if lower.endswith((".txt", ".id", ".ids")):
        return "text"
    return "file"


def _infer_input_mapping(placeholders: list[str], input_roles: dict[str, Any], raw_files: list[Path]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for key in placeholders:
        selected = _select_raw_file(key, input_roles.get(key) or {}, raw_files, used)
        if selected:
            mapping[key] = selected.name
            used.add(selected.name)
    return mapping


def _select_raw_file(key: str, role_spec: dict[str, Any], raw_files: list[Path], used: set[str]) -> Path | None:
    role = str((role_spec or {}).get("role") or "").lower()
    available = [p for p in raw_files if p.name not in used]
    lower_key = key.lower()
    if key == "input_hmm" or "hmm" in role:
        return _first(available, lambda p: p.suffix.lower() in {".hmm", ".hmm3"} or "hmm" in p.name.lower())
    if key == "input_id_file" or "id_list" in role:
        return _first(available, lambda p: p.suffix.lower() in ID_SUFFIXES or re.search(r"(^|[._-])ids?($|[._-])", p.name.lower()))
    if key == "input_file" and ("annotation_gff" in role or "gff" in role):
        return _first(available, lambda p: p.suffix.lower() in {".gff", ".gff3"} or p.name.lower().endswith((".gff.gz", ".gff3.gz")))
    if key == "input_file" and role == "generic_input_file":
        preferred = _first(available, lambda p: any(token in p.name.lower() for token in ("plantcare", "cis_element", "cis-elements", "cis.elements")))
        if preferred:
            return preferred
    fasta_files = [p for p in available if _is_fasta_like(p)]
    if key in {"input_reference_fasta"} or "reference" in role:
        preferred = [p for p in fasta_files if any(token in p.name.lower() for token in ("ref", "reference", "ath", "arabidopsis", "db"))]
        return (preferred or sorted(fasta_files, key=lambda p: p.stat().st_size))[0] if fasta_files else None
    if "fasta" in lower_key or "pep" in lower_key or "cds" in lower_key or "fasta" in role or "pep" in role:
        preferred = [p for p in fasta_files if any(token in p.name.lower() for token in ("pep", "protein", "faa"))]
        candidates = preferred or fasta_files
        return sorted(candidates, key=lambda p: p.stat().st_size, reverse=True)[0] if candidates else None
    return available[0] if available else None


def _is_fasta_like(path: Path) -> bool:
    name = path.name.lower()
    return path.suffix.lower() in FASTA_SUFFIXES or name.endswith(('.fa.gz', '.fasta.gz', '.faa.gz')) or any(token in name for token in ('.pep.fa', '.pep.fasta', '.protein.fa'))


def _first(paths: list[Path], predicate: Any) -> Path | None:
    for path in paths:
        if predicate(path):
            return path
    return None


def _render_shell_wrapper(*, original_command: str, input_mapping: dict[str, str], raw_input_names: list[str], raw_code_names: list[str]) -> str:
    variables = {key: _shell_var(key) for key in input_mapping}
    usage_flags = ["--input-dir <dir>"] + [f"--{key.replace('_', '-')} <file>" for key in sorted(input_mapping)] + ["--output-dir <dir>"]
    lines: list[str] = [
        "#!/bin/bash",
        "set -euo pipefail",
        "usage() {",
        f"  echo \"Usage: $0 {' '.join(usage_flags)}\" >&2",
        "  exit 1",
        "}",
        "SCRIPT_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"",
        "INPUT_DIR=\"\"",
        "OUTDIR=\"\"",
    ]
    for var in variables.values():
        lines.append(f"{var}=\"\"")
    lines.extend([
        "while [ \"$#\" -gt 0 ]; do",
        "  case \"$1\" in",
        "    --input-dir) INPUT_DIR=\"$2\"; shift 2 ;;",
        "    --output-dir) OUTDIR=\"$2\"; shift 2 ;;",
    ])
    for key, var in sorted(variables.items()):
        lines.append(f"    --{key.replace('_', '-')}) {var}=\"$2\"; shift 2 ;;")
    lines.extend([
        "    --param-*) shift 2 ;;",
        "    *) usage ;;",
        "  esac",
        "done",
        "[ -n \"$OUTDIR\" ] || usage",
        "mkdir -p \"$OUTDIR\"",
        "WORKDIR=\"$(mktemp -d)\"",
        "cleanup() { rm -rf \"$WORKDIR\"; }",
        "trap cleanup EXIT",
        "if [ -n \"$INPUT_DIR\" ] && [ -d \"$INPUT_DIR\" ]; then",
        "  while IFS= read -r -d '' item; do cp -R \"$item\" \"$WORKDIR/\"; done < <(find \"$INPUT_DIR\" -mindepth 1 -maxdepth 1 -print0)",
        "  mkdir -p \"$WORKDIR/raw_test_data\"",
        "  while IFS= read -r -d '' item; do cp -R \"$item\" \"$WORKDIR/raw_test_data/\"; done < <(find \"$INPUT_DIR\" -mindepth 1 -maxdepth 1 -print0)",
        "fi",
        "while IFS= read -r -d '' item; do cp -R \"$item\" \"$WORKDIR/\"; done < <(find \"$SCRIPT_DIR\" -mindepth 1 -maxdepth 1 ! -name 'run_extension.sh' -print0)",
    ])
    for key, original_name in sorted(input_mapping.items()):
        var = variables[key]
        lines.extend([
            f"if [ -n \"${var}\" ]; then",
            f"  cp -f \"${var}\" \"$WORKDIR/{_shell_escape_path(original_name)}\"",
            "fi",
        ])
    lines.extend([
        "cd \"$WORKDIR\"",
        "BEFORE_MANIFEST=\"$WORKDIR/.bbav5_before_files\"",
        "find . -type f -printf '%P\\n' | sort > \"$BEFORE_MANIFEST\"",
        f"bash -o pipefail -c {shlex.quote(original_command)}",
        "AFTER_MANIFEST=\"$WORKDIR/.bbav5_after_files\"",
        "find . -type f -printf '%P\\n' | sort > \"$AFTER_MANIFEST\"",
        "while IFS= read -r file; do",
        "  [ -n \"$file\" ] || continue",
        "  [ \"$file\" = '.bbav5_before_files' ] && continue",
        "  [ \"$file\" = '.bbav5_after_files' ] && continue",
        "  if grep -Fxq -- \"$file\" \"$BEFORE_MANIFEST\"; then continue; fi",
        "  mkdir -p \"$OUTDIR/$(dirname \"$file\")\"",
        "  cp -f \"$WORKDIR/$file\" \"$OUTDIR/$file\"",
        "done < \"$AFTER_MANIFEST\"",
    ])
    return "\n".join(lines) + "\n"


def _shell_var(key: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", key.upper()).strip("_")
    return text or "INPUT_FILE"


def _shell_escape_path(name: str) -> str:
    return str(name).replace('"', '\\"')
