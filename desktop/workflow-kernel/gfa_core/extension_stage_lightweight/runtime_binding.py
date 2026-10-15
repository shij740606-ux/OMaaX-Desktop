"""Build an evidence-owned contract for later runtime binding."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import shlex
import shutil
import tempfile
from typing import Any, Callable

from .command_model import _prepare_script_commands
from .evidence import inventory_files
from .process_control import ProcessIdentity, start_managed_process, stop_process_group


SCHEMA_VERSION = "lightweight-runtime-binding/v1"


class RuntimeBindingError(RuntimeError):
    """A deterministic runtime binding could not be rendered safely."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _digest_without(value: dict[str, Any], field: str) -> str:
    return _sha256_json({key: item for key, item in value.items() if key != field})


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_tree(source: Path) -> str:
    """确定性目录摘要：每个普通文件的 (相对路径, 内容摘要) 级联；跳过符号链接本身。"""
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source).as_posix()
        if path.is_symlink():
            continue
        if path.is_file():
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(_sha256_file(path).encode("utf-8"))
            digest.update(b"\0")
        elif path.is_dir():
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0dir\0")
    return digest.hexdigest()


def _verify_raw_code(
    inventory: dict[str, dict[str, Any]], raw_code: Path
) -> None:
    for relative, record in sorted(inventory.items()):
        if record.get("source") != "raw_code":
            continue
        source = raw_code / relative
        if not source.is_file():
            raise ValueError(f"Code file is missing after baseline: {relative}")
        if _sha256_file(source) != record.get("sha256"):
            raise ValueError(f"Code file changed after baseline: {relative}")


def _semantic_text_by_fact(
    candidate: dict[str, Any], section: str, allowed_fields: set[str]
) -> dict[str, dict[str, str]]:
    projected: dict[str, dict[str, str]] = {}
    entries = candidate.get(section, []) if isinstance(candidate, dict) else []
    if not isinstance(entries, list):
        return projected
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        fact_id = entry.get("fact_id")
        if not isinstance(fact_id, str) or fact_id in projected:
            continue
        text = {
            field: value.strip()
            for field in allowed_fields
            if isinstance((value := entry.get(field)), str) and value.strip()
        }
        projected[fact_id] = text
    return projected


def _source_scripts(
    execution_facts: dict[str, Any], raw_code: Path
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    inventory = execution_facts["package"]["inventory"]
    records: list[dict[str, Any]] = []
    words_by_script: dict[str, list[str]] = {}
    for relative in execution_facts.get("source_scripts", []):
        package_record = inventory.get(relative)
        if (
            not isinstance(package_record, dict)
            or package_record.get("source") != "raw_code"
        ):
            raise ValueError(f"Source script is not locked raw code: {relative}")
        source = raw_code / relative
        if not source.is_file():
            raise ValueError(f"Source script does not exist in raw_code: {relative}")
        actual_hash = _sha256_file(source)
        records.append(
            {"path": relative, "sha256": actual_hash, "locked": True}
        )
        lexer = shlex.shlex(
            source.read_text(encoding="utf-8", errors="replace"),
            posix=True,
            punctuation_chars="|&;()<>",
        )
        lexer.whitespace_split = True
        lexer.commenters = "#"
        try:
            words_by_script[relative] = list(lexer)
        except ValueError:
            words_by_script[relative] = []
    return records, words_by_script


def _package_files(
    inventory: dict[str, dict[str, Any]], exposed_input_paths: set[str]
) -> list[dict[str, Any]]:
    return [
        {
            "path": path,
            "source": record.get("source"),
            "kind": record.get("kind"),
            "size": record.get("size"),
            "sha256": record.get("sha256"),
            "locked": record.get("source") == "raw_code",
            "exposed_as_input": path in exposed_input_paths,
        }
        for path, record in sorted(inventory.items())
    ]


def _command_anchor(command: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_id": command["step_id"],
        "command_digest": hashlib.sha256(
            command["command"].encode("utf-8")
        ).hexdigest(),
    }


def _command_tokens(command: dict[str, Any]) -> list[str]:
    try:
        return shlex.split(command["command"])
    except ValueError:
        return []


def _fact_anchor(
    fact: dict[str, Any], command: dict[str, Any], token_index: int
) -> dict[str, Any]:
    return {
        "fact_id": fact["fact_id"],
        **_command_anchor(command),
        "token_index": token_index,
    }


def _input_anchor(
    fact: dict[str, Any], command: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    token_index = fact["token_index"]
    tokens = _command_tokens(command)
    if token_index >= len(tokens) or tokens[token_index] != fact["path"]:
        return None, "input_reference_anchor_not_found"
    return _fact_anchor(fact, command, token_index), None


def _parameter_anchor(
    fact: dict[str, Any], command: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    token_index = fact["token_index"]
    tokens = _command_tokens(command)
    if token_index >= len(tokens):
        return None, "parameter_reference_anchor_not_found"
    if fact["kind"] == "flag_value":
        if "=" in tokens[token_index]:
            return None, "inline_flag_value_not_supported"
        token_index += 1
    literal = fact["flag"] if fact["kind"] == "switch" else fact["value"]
    if token_index >= len(tokens) or tokens[token_index] != literal:
        return None, "parameter_reference_anchor_not_found"
    return _fact_anchor(fact, command, token_index), None


def _output_anchors(
    output: dict[str, Any], commands_by_step: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], str | None]:
    anchors: list[dict[str, Any]] = []
    producer_step_ids = output.get("producer_step_ids", [])
    if not producer_step_ids:
        return [], "output_reference_anchor_not_found"
    for step_id in producer_step_ids:
        command = commands_by_step.get(step_id)
        if command is None:
            return [], "output_reference_anchor_not_found"
        matching_indexes = [
            index
            for index, token in enumerate(_command_tokens(command))
            if token == output["path"]
        ]
        if not matching_indexes:
            return [], "output_reference_anchor_not_found"
        if len(matching_indexes) != 1:
            return [], "output_reference_anchor_ambiguous"
        anchors.append(_fact_anchor(output, command, matching_indexes[0]))
    return anchors, None


def _patch_candidates(
    commands: list[dict[str, Any]],
    inputs: list[dict[str, Any]],
    parameters: list[dict[str, Any]],
    path_references: list[dict[str, Any]],
    output_files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    commands_by_step = {command["step_id"]: command for command in commands}
    path_members: dict[str, list[dict[str, Any]]] = {}
    for item in inputs:
        command = commands_by_step[item["step_id"]]
        anchor, reason = _input_anchor(item, command)
        path_members.setdefault(item["path"], []).append(
            {
                "fact_id": item["fact_id"],
                "member_kind": (
                    "input" if item["replaceable"] else "generated_input"
                ),
                "anchors": [anchor] if anchor else [],
                "reason": reason,
            }
        )
    for item in output_files:
        anchors, reason = _output_anchors(item, commands_by_step)
        path_members.setdefault(item["path"], []).append(
            {
                "fact_id": item["fact_id"],
                "member_kind": "output",
                "anchors": anchors,
                "producer_step_ids": deepcopy(
                    item.get("producer_step_ids", [])
                ),
                "reason": reason,
            }
        )

    candidates: list[dict[str, Any]] = []
    for path, members in path_members.items():
        member_kinds = {member["member_kind"] for member in members}
        reasons = [member["reason"] for member in members if member["reason"]]
        if "generated_input" in member_kinds and "output" not in member_kinds:
            reasons.append("generated_input_without_output_binding")
        producer_step_ids = [
            step_id
            for member in members
            for step_id in member.get("producer_step_ids", [])
        ]
        candidates.append(
            {
                "binding_key": f"path:{path}",
                "fact_ids": [member["fact_id"] for member in members],
                "anchors": [
                    anchor
                    for member in members
                    for anchor in member["anchors"]
                ],
                "original_literal": path,
                "patch_kind": (
                    "output_path" if "output" in member_kinds else "input_path"
                ),
                **(
                    {"producer_step_ids": producer_step_ids}
                    if producer_step_ids
                    else {}
                ),
                **({"_unpatchable_reason": reasons[0]} if reasons else {}),
            }
        )

    for item in parameters:
        command = commands_by_step[item["step_id"]]
        anchor, reason = _parameter_anchor(item, command)
        candidate = {
            "binding_key": f"parameter:{item['fact_id']}",
            "fact_ids": [item["fact_id"]],
            "anchors": [anchor] if anchor else [],
            "original_literal": (
                item["flag"] if item["kind"] == "switch" else item["value"]
            ),
            "patch_kind": (
                "parameter_switch"
                if item["kind"] == "switch"
                else "parameter_value"
            ),
        }
        if reason:
            candidate["_unpatchable_reason"] = reason
        candidates.append(candidate)
    for item in path_references:
        command = commands_by_step[item["step_id"]]
        anchor, reason = _parameter_anchor(item, command)
        candidate = {
            "binding_key": f"path_reference:{item['fact_id']}",
            "fact_ids": [item["fact_id"]],
            "anchors": [anchor] if anchor else [],
            "original_literal": item["path"],
            "patch_kind": "output_reference_path",
        }
        if reason:
            candidate["_unpatchable_reason"] = reason
        candidates.append(candidate)
    return candidates


def _locate_patch_targets(
    candidates: list[dict[str, Any]],
    source_scripts: list[dict[str, Any]],
    words_by_script: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    targets: list[dict[str, Any]] = []
    unpatchable: list[dict[str, Any]] = []
    for candidate in candidates:
        reason = candidate.get("_unpatchable_reason")
        source_matches = [
            (
                script,
                words_by_script[script["path"]].count(
                    candidate["original_literal"]
                ),
            )
            for script in source_scripts
            if candidate["original_literal"] in words_by_script[script["path"]]
        ]
        anchors = candidate["anchors"]
        anchor_identities = {
            (
                anchor["step_id"],
                anchor["token_index"],
                anchor["command_digest"],
            )
            for anchor in anchors
        }
        if reason is None:
            if not source_matches:
                reason = "source_literal_not_found_in_raw_shell"
            elif len(source_matches) > 1:
                # 字面量跨多个脚本，无法唯一定位
                reason = "source_literal_ambiguous"
            elif len(anchors) != 1 or len(anchor_identities) != 1:
                # 需要唯一 anchor 才能安全 patch
                reason = "source_literal_ambiguous"

        clean_candidate = {
            key: value
            for key, value in candidate.items()
            if key != "_unpatchable_reason"
        }
        if reason is not None:
            unpatchable.append(
                {
                    **clean_candidate,
                    "reason": reason,
                }
            )
            continue
        script, count = source_matches[0]
        target = {
            **clean_candidate,
            "script_path": script["path"],
            "script_hash": script["sha256"],
            "expected_exact_match_count": count,
        }
        if len(anchors) == 1:
            target.update(
                {
                    "step_id": anchors[0]["step_id"],
                    "token_index": anchors[0]["token_index"],
                    "command_digest": anchors[0]["command_digest"],
                }
            )
        targets.append(target)
    return targets, unpatchable


def build_runtime_binding_contract(
    execution_facts: dict[str, Any],
    semantic_candidate: dict[str, Any],
    raw_code: Path,
) -> dict[str, Any]:
    """Project locked execution facts into a runtime-binding contract.

    Semantic data is optional display text. It never controls commands, paths,
    values, ownership, or replaceability.
    """

    raw_code = Path(raw_code)
    inventory = execution_facts["package"]["inventory"]
    _verify_raw_code(inventory, raw_code)
    input_text = _semantic_text_by_fact(
        semantic_candidate, "inputs", {"role", "description"}
    )
    parameter_text = _semantic_text_by_fact(
        semantic_candidate, "parameters", {"display_name", "description"}
    )
    output_text = _semantic_text_by_fact(
        semantic_candidate, "outputs", {"description", "result_role"}
    )

    inputs: list[dict[str, Any]] = []
    parameters: list[dict[str, Any]] = []
    path_references: list[dict[str, Any]] = []
    for command in execution_facts.get("commands", []):
        step_id = command["step_id"]
        for fact in command.get("inputs", []):
            replaceable = fact.get("source") == "baseline_input"
            inputs.append(
                {
                    **deepcopy(fact),
                    "step_id": step_id,
                    "replaceable": replaceable,
                    "locked": not replaceable,
                    **input_text.get(fact["fact_id"], {}),
                }
            )
        for fact in command.get("parameters", []):
            if fact.get("value_role") == "output_reference":
                path_references.append(
                    {
                        **deepcopy(fact),
                        "step_id": step_id,
                        "path": fact["value"],
                    }
                )
                continue
            if fact.get("value_role") == "input":
                continue
            parameters.append(
                {
                    **deepcopy(fact),
                    "step_id": step_id,
                    **parameter_text.get(fact["fact_id"], {}),
                }
            )

    output_files = [
        {
            **deepcopy(fact),
            **output_text.get(fact["fact_id"], {}),
        }
        for fact in execution_facts.get("outputs", {}).get("files", [])
    ]
    stdout = deepcopy(execution_facts.get("outputs", {}).get("stdout", {}))
    stdout_fact_id = stdout.get("fact_id")
    if isinstance(stdout_fact_id, str):
        stdout.update(output_text.get(stdout_fact_id, {}))

    scripts, words_by_script = _source_scripts(execution_facts, raw_code)
    patch_targets, unpatchable_targets = _locate_patch_targets(
        _patch_candidates(
            execution_facts.get("commands", []),
            inputs,
            parameters,
            path_references,
            output_files,
        ),
        scripts,
        words_by_script,
    )
    exposed_input_paths = {
        item["path"] for item in inputs if item["replaceable"]
    }
    contract = {
        "schema_version": SCHEMA_VERSION,
        "facts_sha256": execution_facts["facts_sha256"],
        "package_digest": _sha256_json(inventory),
        "original_command": execution_facts["original_command"],
        "source_scripts": scripts,
        "package_files": _package_files(inventory, exposed_input_paths),
        "inputs": inputs,
        "parameters": parameters,
        "path_references": path_references,
        "outputs": {"files": output_files, "stdout": stdout},
        "patch_targets": patch_targets,
        "unpatchable_targets": unpatchable_targets,
    }
    contract["contract_digest"] = _digest_without(contract, "contract_digest")
    return contract


_SWITCH_TOKEN = re.compile(r"-{1,2}[A-Za-z0-9][A-Za-z0-9_.:+-]*\Z")
_PREFIX_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_PREFIX_DELIMITERS = ".-_"


def _resolved_result(
    contract: dict[str, Any],
    *,
    status: str,
    input_bindings: list[dict[str, Any]] | None = None,
    parameter_bindings: list[dict[str, Any]] | None = None,
    output_bindings: list[dict[str, Any]] | None = None,
    prefix_binding: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "status": status,
        "facts_sha256": contract.get("facts_sha256"),
        "package_digest": contract.get("package_digest"),
        "contract_digest": contract.get("contract_digest"),
        "input_bindings": input_bindings or [],
        "parameter_bindings": parameter_bindings or [],
        "output_bindings": output_bindings or [],
        "prefix_binding": prefix_binding,
        "error": error,
    }
    result["binding_digest"] = _digest_without(result, "binding_digest")
    return result


def _blocked_result(
    contract: dict[str, Any],
    code: str,
    message: str,
    *,
    input_bindings: list[dict[str, Any]] | None = None,
    parameter_bindings: list[dict[str, Any]] | None = None,
    **details: Any,
) -> dict[str, Any]:
    return _resolved_result(
        contract,
        status="blocked",
        input_bindings=input_bindings,
        parameter_bindings=parameter_bindings,
        error={"code": code, "message": message, **details},
    )


def _has_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _input_groups(
    contract: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str], set[str]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in contract.get("inputs", []):
        if not isinstance(item, dict) or not item.get("replaceable"):
            continue
        path = item.get("path")
        if isinstance(path, str) and path:
            groups.setdefault(path, []).append(item)

    aliases: dict[str, str] = {}
    ambiguous: set[str] = set()
    for path, items in groups.items():
        for alias in [path, *(item.get("fact_id") for item in items)]:
            if not isinstance(alias, str) or not alias:
                continue
            if alias in aliases and aliases[alias] != path:
                ambiguous.add(alias)
            else:
                aliases[alias] = path
    roles: dict[str, set[str]] = {}
    for path, items in groups.items():
        for item in items:
            role = item.get("role")
            if isinstance(role, str) and role:
                roles.setdefault(role, set()).add(path)
    for role, paths in roles.items():
        if len(paths) == 1:
            aliases[role] = next(iter(paths))
        else:
            ambiguous.add(role)
    for alias in ambiguous:
        aliases.pop(alias, None)
    return groups, aliases, ambiguous


def _resolve_inputs(
    contract: dict[str, Any], input_overrides: dict[str, Any]
) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    groups, aliases, ambiguous = _input_groups(contract)
    selected: dict[str, list[Path]] = {path: [] for path in groups}
    for alias, value in input_overrides.items():
        if alias in ambiguous:
            return None, {
                "code": "INPUT_BINDING_AMBIGUOUS",
                "message": f"Input alias is not unique: {alias}",
            }
        original_path = aliases.get(alias)
        if original_path is None:
            return None, {
                "code": "INPUT_NOT_DECLARED",
                "message": f"Input is not declared by the contract: {alias}",
                "available_fact_ids": sorted(
                    item["fact_id"]
                    for items in groups.values()
                    for item in items
                    if isinstance(item.get("fact_id"), str)
                ),
            }
        try:
            expanded = Path(value).expanduser()
            selected[original_path].append(Path(os.path.abspath(expanded)))
        except (TypeError, ValueError, OSError):
            return None, {
                "code": "INPUT_PATH_NOT_FOUND",
                "message": f"Input path is invalid: {value}",
            }

    bindings: list[dict[str, Any]] = []
    for original_path, items in sorted(groups.items()):
        paths = selected[original_path]
        if not paths:
            return None, {
                "code": "INPUT_BINDING_MISSING",
                "message": f"Required input has no runtime binding: {original_path}",
            }
        distinct = {str(path) for path in paths}
        if len(distinct) != 1:
            return None, {
                "code": "INPUT_BINDING_CONFLICT",
                "message": f"Input has conflicting runtime bindings: {original_path}",
            }
        runtime_path = paths[0]
        if not (runtime_path.is_file() or runtime_path.is_dir()):
            return None, {
                "code": "INPUT_PATH_NOT_FOUND",
                "message": f"Runtime input file or directory does not exist: {runtime_path}",
            }
        bindings.append(
            {
                "original_path": original_path,
                "runtime_path": str(runtime_path),
                "fact_ids": sorted(
                    item["fact_id"]
                    for item in items
                    if isinstance(item.get("fact_id"), str)
                ),
            }
        )
    return bindings, None


def _resolve_parameters(
    contract: dict[str, Any], parameter_overrides: dict[str, Any]
) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    declared = {
        item["fact_id"]: item
        for item in contract.get("parameters", [])
        if isinstance(item, dict) and isinstance(item.get("fact_id"), str)
    }
    unknown = sorted(set(parameter_overrides) - set(declared))
    if unknown:
        return None, {
            "code": "PARAMETER_NOT_DECLARED",
            "message": f"Parameter is not declared by the original command: {unknown[0]}",
            "available_fact_ids": sorted(declared),
        }

    bindings: list[dict[str, Any]] = []
    for fact_id, item in sorted(declared.items()):
        original = item.get("value", item.get("flag", ""))
        runtime = parameter_overrides.get(fact_id, original)
        runtime_value = str(runtime)
        if not runtime_value or _has_control(runtime_value):
            return None, {
                "code": "PARAMETER_VALUE_INVALID",
                "message": f"Parameter contains an unsafe value: {fact_id}",
            }
        if item.get("kind") == "switch" and not _SWITCH_TOKEN.fullmatch(
            runtime_value
        ):
            return None, {
                "code": "PARAMETER_VALUE_INVALID",
                "message": f"Switch must be one safe option token: {fact_id}",
            }
        bindings.append(
            {
                "fact_id": fact_id,
                "kind": item.get("kind"),
                "flag": item.get("flag"),
                "original_value": str(original),
                "runtime_value": runtime_value,
                "changed": runtime_value != str(original),
            }
        )
    return bindings, None


def _validate_prefix(value: Any) -> bool:
    return (
        isinstance(value, str)
        and ".." not in value
        and _PREFIX_TOKEN.fullmatch(value) is not None
    )


def _output_has_prefix(output_path: str, prefix: str) -> bool:
    """Return True when *output_path* begins with *prefix* at a safe boundary.

    The prefix may match the filename (``results/foo.txt`` → ``foo``) or
    a directory component in the path (``meme_out/logo1.png`` → ``meme_out``).
    """
    path = PurePosixPath(output_path)
    name = path.name
    # 1) filename starts with prefix at a delimiter boundary
    if name.startswith(prefix) and (
        len(name) == len(prefix)
        or name[len(prefix)] in _PREFIX_DELIMITERS
    ):
        return True
    # 2) the first directory component starts with prefix
    parts = path.parts
    for part in parts[:-1]:  # exclude the filename
        if part.startswith(prefix) and (
            len(part) == len(prefix)
            or part[len(prefix)] in _PREFIX_DELIMITERS
        ):
            return True
    return False


def _replace_output_prefix(output_path: str, old: str, new: str) -> str:
    """Replace *old* prefix with *new* at a filename or directory boundary."""
    path = PurePosixPath(output_path)
    # When the prefix matches a directory component, rename the *directory*.
    matched_part = None
    for part in path.parts[:-1]:  # exclude filename
        if part.startswith(old) and (
            len(part) == len(old)
            or part[len(old)] in _PREFIX_DELIMITERS
        ):
            matched_part = part
            break
    if matched_part is not None:
        new_dir = new + matched_part[len(old):]
        parts = list(path.parts)
        idx = parts.index(matched_part)
        parts[idx] = new_dir
        return str(PurePosixPath(*parts))
    # Otherwise replace the filename prefix as before.
    name = new + path.name[len(old) :]
    return name if str(path.parent) == "." else str(path.parent / name)


def _shared_output_prefix(outputs: list[dict[str, Any]]) -> str | None:
    """Return one delimiter-owned prefix from declared output filenames.

    If filename-based extraction fails (e.g. ``logo1.png`` + ``meme.html``
    share no prefix), fall back to the deepest shared directory component.
    """

    names = [PurePosixPath(item["path"]).name for item in outputs]
    if not names:
        return None
    if len(names) == 1:
        suffix = PurePosixPath(names[0]).suffix
        candidate = names[0][: -len(suffix)] if suffix else names[0]
    else:
        candidate = os.path.commonprefix(names).rstrip("._-")
    if not _validate_prefix(candidate):
        candidate = _fallback_dir_prefix(outputs)
    if not candidate:
        return None
    if not _validate_prefix(candidate):
        return None
    if not all(_output_has_prefix(item["path"], candidate) for item in outputs):
        return None
    return candidate


_DIR_OVERRIDE_SWITCHES = ("-oc", "-o")  # tools that accept a writable output directory


def _prefix_targets_directory(paths: list[str], prefix: str) -> bool:
    """Return True when *prefix* matches a directory component of any path.

    Checks the actual output paths rather than guessing from the prefix string:
    ``meme_out/logo1.png`` has ``meme_out`` as a directory component, while
    ``input_qc.tsv`` and ``Ft.blast`` do not — so filename stems are not mistaken
    for directory prefixes.
    """
    for path in paths:
        for part in PurePosixPath(path).parts[:-1]:  # 排除文件名
            if part.startswith(prefix) and (
                len(part) == len(prefix) or part[len(prefix)] in _PREFIX_DELIMITERS
            ):
                return True
    return False


def _replace_positional_prefix(command: str, old: str, new: str) -> str:
    """Replace *old* prefix with *new* in positional arguments.

    When a tool takes an output prefix as a positional argument and the
    prefix rename targets that argument (e.g. ``MCScanX ./Ft`` →
    ``MCScanX ./output``), replace the matching token verbatim.

    Returns *command* unchanged when no token matches.
    """
    tokens = shlex.split(command)
    for i, token in enumerate(tokens):
        if token == old or token.endswith("/" + old):
            tokens[i] = token[: -len(old)] + new
            return shlex.join(tokens)
    return command


def _symlink_prefix_data_files(
    old: str,
    new: str,
    work_dir: Path,
    source_hashes: set[str],
) -> None:
    """Create ``<new>.<ext>`` symlinks to ``<old>.<ext>`` for existing data files."""
    for relative in sorted(source_hashes):
        name = PurePosixPath(relative).name
        if name.startswith(old) and (
            len(name) == len(old) or name[len(old)] in _PREFIX_DELIMITERS
        ):
            link_name = new + name[len(old):]
            link_path = work_dir / link_name
            if not link_path.exists():
                link_path.symlink_to(relative)


def _inject_dir_prefix_override(command: str, old: str, new: str) -> str:
    """Insert a ``-oc <new>`` flag after the first positional argument."""
    tokens = shlex.split(command)
    for idx, token in enumerate(tokens):
        if idx == 0:
            continue  # skip the tool name
        if not token.startswith("-") and "=" not in token:
            break
    else:
        idx = len(tokens)
    tokens.insert(idx + 1, new)
    tokens.insert(idx + 1, _DIR_OVERRIDE_SWITCHES[0])
    return shlex.join(tokens)


def _fallback_dir_prefix(outputs: list[dict[str, Any]]) -> str | None:
    """Use the deepest shared directory component as a prefix.

    For outputs like ``meme_out/logo1.png`` and ``meme_out/meme.html``,
    the deepest common directory is ``meme_out`` — use that.
    """
    dirs = [PurePosixPath(item["path"]).parent for item in outputs]
    common = PurePosixPath(os.path.commonpath([str(d) for d in dirs]))
    # If common is "." (no shared dir), return None — will use "output"
    if common == PurePosixPath("."):
        return None
    # Pick the deepest component: e.g. "meme_out" from "meme_out"
    deepest = common.name if common.name else None
    if deepest and _validate_prefix(deepest):
        return deepest
    return None


def _runtime_input_owned_output(
    output_path: str,
    runtime_by_original: dict[str, str],
    *,
    allow_sidecar: bool,
) -> str | None:
    """Preserve outputs that are the runtime input itself or its sidecar."""

    candidates = set()
    for original_input, runtime_input in runtime_by_original.items():
        if output_path == original_input:
            suffix = ""
        elif allow_sidecar and output_path.startswith(original_input) and output_path[
            len(original_input) : len(original_input) + 1
        ] in _PREFIX_DELIMITERS:
            suffix = output_path[len(original_input) :]
        else:
            continue
        candidates.add(Path(runtime_input).name + suffix)
    return next(iter(candidates)) if len(candidates) == 1 else None


def _candidate_prefix_pairs(
    original_input: str, runtime_input: str, output_path: str
) -> tuple[set[tuple[str, str]], bool]:
    """Return safe prefix substitutions supported by one input/output pair."""

    original = PurePosixPath(original_input).name
    runtime = Path(runtime_input).name
    boundaries = [
        index
        for index, character in enumerate(original)
        if character in _PREFIX_DELIMITERS and index > 0
    ]
    boundaries.append(len(original))
    pairs: set[tuple[str, str]] = set()
    invalid = False
    for boundary in boundaries:
        old_prefix = original[:boundary]
        if not _output_has_prefix(output_path, old_prefix):
            continue
        suffix = original[boundary:]
        if suffix and not runtime.endswith(suffix):
            continue
        new_prefix = runtime[: -len(suffix)] if suffix else runtime
        if not _validate_prefix(old_prefix) or not _validate_prefix(new_prefix):
            invalid = True
            continue
        pairs.add((old_prefix, new_prefix))
    return pairs, invalid


def _participant_paths(
    contract: dict[str, Any],
    output: dict[str, Any],
    all_paths: set[str],
) -> set[str]:
    producer_steps = set(output.get("producer_step_ids", []))
    same_step = {
        item["path"]
        for item in contract.get("inputs", [])
        if isinstance(item, dict)
        and item.get("replaceable")
        and item.get("step_id") in producer_steps
        and item.get("path") in all_paths
    }
    return same_step or all_paths


def resolve_runtime_bindings(
    contract: dict[str, Any],
    input_overrides: dict[str, Any],
    parameter_overrides: dict[str, Any],
    explicit_prefix: str | None = None,
) -> dict[str, Any]:
    """Resolve runtime inputs and explicit parameters without changing inputs.

    Output-prefix propagation is evidence based. The original output prefix is
    taken from declared output paths; the runtime prefix defaults to ``output``.
    """

    if contract.get("schema_version") != SCHEMA_VERSION:
        return _blocked_result(
            contract,
            "CONTRACT_SCHEMA_UNSUPPORTED",
            "Runtime binding requires lightweight-runtime-binding/v1",
        )
    if explicit_prefix is not None and not _validate_prefix(explicit_prefix):
        return _blocked_result(
            contract,
            "INVALID_PREFIX",
            "Prefix must be one ASCII-safe filename prefix",
        )

    input_bindings, input_error = _resolve_inputs(contract, input_overrides)
    if input_error:
        return _blocked_result(contract, **input_error)
    parameter_bindings, parameter_error = _resolve_parameters(
        contract, parameter_overrides
    )
    if parameter_error:
        return _blocked_result(
            contract,
            input_bindings=input_bindings,
            **parameter_error,
        )

    output_files = [
        item
        for item in contract.get("outputs", {}).get("files", [])
        if isinstance(item, dict)
        and isinstance(item.get("fact_id"), str)
        and isinstance(item.get("path"), str)
    ]
    if not output_files:
        stdout = contract.get("outputs", {}).get("stdout", {})
        if isinstance(stdout, dict) and stdout.get("present"):
            return _resolved_result(
                contract,
                status="resolved",
                input_bindings=input_bindings,
                parameter_bindings=parameter_bindings,
                output_bindings=[],
                prefix_binding={"mode": "none"},
                error=None,
            )
        return _blocked_result(
            contract,
            "OUTPUT_BINDING_AMBIGUOUS",
            "No file output can be bound and stdout is not a declared result",
            input_bindings=input_bindings,
            parameter_bindings=parameter_bindings,
        )

    runtime_by_original = {
        binding["original_path"]: binding["runtime_path"]
        for binding in input_bindings or []
    }
    direct_output_fact_ids = {
        fact_id
        for target in contract.get("patch_targets", [])
        if isinstance(target, dict)
        and target.get("patch_kind") in {"output_path", "output_reference_path"}
        for fact_id in target.get("fact_ids", [])
        if isinstance(fact_id, str)
    }
    # Outputs owned by an input (the input itself or a sidecar like
    # ``Pfam-A.WRKY-subset.hmm.h3f`` next to ``Pfam-A.WRKY-subset.hmm``) do not
    # belong to the output-prefix namespace; they are renamed via their input
    # relationship. Excluding them keeps the shared-prefix derivation from
    # being dragged to ``.`` by a top-level sidecar while the remaining outputs
    # live under a common directory.
    input_owned_paths = {
        output["path"]
        for output in output_files
        if _runtime_input_owned_output(
            output["path"],
            runtime_by_original,
            allow_sidecar=output["fact_id"] not in direct_output_fact_ids,
        )
        is not None
    }
    prefix_output_files = [
        output for output in output_files if output["path"] not in input_owned_paths
    ]
    participant_options: list[set[tuple[str, str]]] = []
    participating_paths: set[str] = set()
    for output in output_files:
        eligible_paths = _participant_paths(
            contract, output, set(runtime_by_original)
        )
        options_for_output: list[tuple[str, set[tuple[str, str]]]] = []
        invalid = False
        for original_path in sorted(eligible_paths):
            pairs, unsafe = _candidate_prefix_pairs(
                original_path,
                runtime_by_original[original_path],
                output["path"],
            )
            invalid = invalid or unsafe
            if pairs:
                options_for_output.append((original_path, pairs))
        if not options_for_output and eligible_paths != set(runtime_by_original):
            for original_path in sorted(runtime_by_original):
                pairs, unsafe = _candidate_prefix_pairs(
                    original_path,
                    runtime_by_original[original_path],
                    output["path"],
                )
                invalid = invalid or unsafe
                if pairs:
                    options_for_output.append((original_path, pairs))
        if invalid:
            return _blocked_result(
                contract,
                "INVALID_PREFIX",
                "An input-derived prefix is not an ASCII-safe filename prefix",
                input_bindings=input_bindings,
                parameter_bindings=parameter_bindings,
            )
        for original_path, pairs in options_for_output:
            participating_paths.add(original_path)
            participant_options.append(pairs)

    common_pairs = (
        set.intersection(*participant_options) if participant_options else set()
    )
    if common_pairs:
        old_prefix = max(
            common_pairs, key=lambda pair: (len(pair[0]), len(pair[1]), pair)
        )[0]
    elif participant_options:
        common_old = set.intersection(
            *[{old for old, _new in options} for options in participant_options]
        )
        if not common_old:
            old_prefix = _shared_output_prefix(prefix_output_files)
        else:
            old_prefix = max(common_old, key=lambda value: (len(value), value))
    else:
        old_prefix = _shared_output_prefix(prefix_output_files)
    if old_prefix is None:
        return _blocked_result(
            contract,
            "OUTPUT_BINDING_AMBIGUOUS",
            "Declared file outputs do not share one safe output prefix",
            input_bindings=input_bindings,
            parameter_bindings=parameter_bindings,
        )
    new_prefix = explicit_prefix or "output"
    unpatchable_fact_ids = {
        fact_id
        for target in contract.get("unpatchable_targets", [])
        if isinstance(target, dict)
        for fact_id in target.get("fact_ids", [])
        if isinstance(fact_id, str)
    }
    output_bindings = []
    output_paths = [output["path"] for output in output_files]
    prefix_targets_dir = _prefix_targets_directory(output_paths, old_prefix)
    for output in output_files:
        original_path = output["path"]
        runtime_path = _runtime_input_owned_output(
            original_path,
            runtime_by_original,
            allow_sidecar=output["fact_id"] not in direct_output_fact_ids,
        )
        if runtime_path is None:
            # 可重命名当且仅当：input-derived 前缀 / 目录前缀 / 有可 patch 的
            # 输出字面量。只有三者都不满足（硬编码且无法 patch，如
            # input_qc.tsv 动态拼接）才保持原名。
            can_rename = (
                bool(participating_paths)
                or prefix_targets_dir
                or output["fact_id"] not in unpatchable_fact_ids
            )
            runtime_path = (
                _replace_output_prefix(original_path, old_prefix, new_prefix)
                if can_rename and _output_has_prefix(original_path, old_prefix)
                else original_path
            )
        output_bindings.append(
            {
                "fact_id": output["fact_id"],
                "original_path": original_path,
                "runtime_path": runtime_path,
                "transformed": runtime_path != original_path,
            }
        )
    runtime_output_paths: dict[str, str] = {}
    for binding in output_bindings:
        runtime_path = binding["runtime_path"]
        if runtime_path in runtime_output_paths:
            return _blocked_result(
                contract,
                "OUTPUT_PATH_COLLISION",
                "Two declared outputs resolve to the same runtime path",
                input_bindings=input_bindings,
                parameter_bindings=parameter_bindings,
                runtime_path=runtime_path,
                fact_ids=sorted(
                    [runtime_output_paths[runtime_path], binding["fact_id"]]
                ),
            )
        runtime_output_paths[runtime_path] = binding["fact_id"]
    return _resolved_result(
        contract,
        status="resolved",
        input_bindings=input_bindings,
        parameter_bindings=parameter_bindings,
        output_bindings=output_bindings,
        prefix_binding={
            "mode": "explicit" if explicit_prefix is not None else "default",
            "old_prefix": old_prefix,
            "new_prefix": new_prefix,
            "participating_inputs": sorted(participating_paths),
        },
        error=None,
    )


_SHELL_WORD_EDGE = r"A-Za-z0-9_./:+-"


def _safe_runtime_relative(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or _has_control(value):
        raise RuntimeBindingError(
            "UNSAFE_RELATIVE_PATH", f"{field} must be a non-empty relative path"
        )
    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith("/") or ".." in path.parts:
        raise RuntimeBindingError(
            "UNSAFE_RELATIVE_PATH", f"{field} escapes the runtime directory: {value}"
        )
    if str(path) in {"", "."}:
        raise RuntimeBindingError(
            "UNSAFE_RELATIVE_PATH", f"{field} is not a file path: {value}"
        )
    return str(path)


def _resolved_digest(resolved: dict[str, Any]) -> str:
    return _digest_without(resolved, "binding_digest")


def _package_inventory_from_contract(
    contract: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    inventory: dict[str, dict[str, Any]] = {}
    for record in contract.get("package_files", []):
        if not isinstance(record, dict):
            raise RuntimeBindingError(
                "PACKAGE_INVENTORY_INVALID", "package file record must be an object"
            )
        relative = _safe_runtime_relative(record.get("path"), field="package path")
        if relative in inventory:
            raise RuntimeBindingError(
                "PACKAGE_INVENTORY_INVALID", f"duplicate package path: {relative}"
            )
        inventory[relative] = {
            key: record.get(key)
            for key in ("kind", "size", "sha256", "source")
        }
    return inventory


def _validate_contract_digest(contract: dict[str, Any]) -> None:
    if contract.get("contract_digest") != _digest_without(
        contract, "contract_digest"
    ):
        raise RuntimeBindingError(
            "CONTRACT_DIGEST_MISMATCH", "runtime binding contract was modified"
        )
    inventory = _package_inventory_from_contract(contract)
    if contract.get("package_digest") != _sha256_json(inventory):
        raise RuntimeBindingError(
            "PACKAGE_DIGEST_MISMATCH",
            "package digest does not match the locked package inventory",
        )


def _canonical_resolution(
    contract: dict[str, Any], resolved: dict[str, Any]
) -> dict[str, Any]:
    input_overrides = {
        str(item.get("original_path") or ""): item.get("runtime_path")
        for item in resolved.get("input_bindings", [])
        if isinstance(item, dict)
    }
    parameter_overrides = {
        str(item.get("fact_id") or ""): item.get("runtime_value")
        for item in resolved.get("parameter_bindings", [])
        if isinstance(item, dict)
    }
    prefix = resolved.get("prefix_binding")
    explicit_prefix = (
        prefix.get("new_prefix")
        if isinstance(prefix, dict) and prefix.get("mode") == "explicit"
        else None
    )
    return resolve_runtime_bindings(
        contract,
        input_overrides,
        parameter_overrides,
        explicit_prefix=explicit_prefix,
    )


def _validate_render_inputs(
    contract: dict[str, Any], raw_code: Path, resolved: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    _validate_contract_digest(contract)
    if resolved.get("status") != "resolved":
        raise RuntimeBindingError(
            "RESOLUTION_NOT_READY", "runtime bindings must have status=resolved"
        )
    for key in ("facts_sha256", "package_digest", "contract_digest"):
        if resolved.get(key) != contract.get(key):
            raise RuntimeBindingError(
                "RESOLUTION_CONTRACT_MISMATCH",
                f"resolved {key} does not match the binding contract",
            )
    if resolved.get("binding_digest") != _resolved_digest(resolved):
        raise RuntimeBindingError(
            "RESOLUTION_DIGEST_MISMATCH", "resolved binding digest is invalid"
        )
    if _canonical_resolution(contract, resolved) != resolved:
        raise RuntimeBindingError(
            "RESOLUTION_CANONICAL_MISMATCH",
            "resolved bindings do not match deterministic resolution",
        )

    declared: dict[str, dict[str, Any]] = {}
    for record in contract.get("package_files", []):
        if not isinstance(record, dict) or record.get("source") != "raw_code":
            continue
        relative = _safe_runtime_relative(record.get("path"), field="code path")
        if relative in declared:
            raise RuntimeBindingError(
                "SOURCE_INVENTORY_INVALID", f"duplicate code path: {relative}"
            )
        declared[relative] = record

    raw_code = Path(raw_code)
    actual = {
        path.relative_to(raw_code).as_posix(): path
        for path in sorted(raw_code.rglob("*"))
        if path.is_file()
    }
    if set(actual) != set(declared):
        raise RuntimeBindingError(
            "SOURCE_INVENTORY_MISMATCH",
            "raw_code file set no longer matches the locked package",
        )
    source_hashes: dict[str, str] = {}
    raw_code_root = raw_code.resolve()
    for relative, source in actual.items():
        if source.is_symlink():
            raise RuntimeBindingError(
                "SOURCE_SYMLINK_UNSAFE", f"raw code must not be a symlink: {relative}"
            )
        try:
            source.resolve().relative_to(raw_code_root)
        except ValueError as exc:
            raise RuntimeBindingError(
                "SOURCE_PATH_ESCAPE", f"raw code escapes its package: {relative}"
            ) from exc
        digest = _sha256_file(source)
        if digest != declared[relative].get("sha256"):
            raise RuntimeBindingError(
                "SOURCE_HASH_MISMATCH", f"raw code changed after baseline: {relative}"
            )
        source_hashes[relative] = digest
    return declared, source_hashes


def _binding_replacements(
    contract: dict[str, Any], resolved: dict[str, Any]
) -> tuple[dict[str, str], dict[str, dict[str, list[str]]]]:
    requested: dict[str, str] = {}
    patchable = {
        target.get("binding_key")
        for target in contract.get("patch_targets", [])
        if isinstance(target, dict)
    }
    patched_path_roots: list[tuple[str, str]] = []

    prefix = resolved.get("prefix_binding")
    prefix_old = prefix.get("old_prefix") if isinstance(prefix, dict) else None
    prefix_new = prefix.get("new_prefix") if isinstance(prefix, dict) else None
    participating_inputs = (
        set(prefix.get("participating_inputs") or ())
        if isinstance(prefix, dict)
        else set()
    )

    def add(binding_key: str, original: str, replacement: str) -> None:
        if replacement == original:
            return
        previous = requested.get(binding_key)
        if previous is not None and previous != replacement:
            raise RuntimeBindingError(
                "PATCH_REPLACEMENT_CONFLICT",
                f"one binding resolves to different values: {binding_key}",
            )
        requested[binding_key] = replacement

    for binding in resolved.get("input_bindings", []):
        original = str(binding.get("original_path") or "")
        runtime = Path(str(binding.get("runtime_path") or ""))
        binding_key = f"path:{original}"
        replacement = runtime.name
        # When the output prefix is derived from an input name (e.g. seqkit
        # derives ``split_fasta/Ft.SPL.pep.part_001.fasta`` from the input
        # ``Ft.SPL.pep.fasta``), the participating input must be renamed as
        # well. Otherwise the tool keeps writing outputs under the original
        # prefix and the renamed outputs never materialise. ``output.fasta``
        # is symlinked into the work tree by render_runtime_package.
        if (
            isinstance(prefix_old, str)
            and isinstance(prefix_new, str)
            and original in participating_inputs
            and _output_has_prefix(replacement, prefix_old)
        ):
            replacement = _replace_output_prefix(
                replacement, prefix_old, prefix_new
            )
        add(binding_key, original, replacement)
        if replacement != original and binding_key in patchable:
            patched_path_roots.append((original, replacement))

    if isinstance(prefix, dict):
        old_prefix = prefix_old
        new_prefix = prefix_new
        if isinstance(old_prefix, str) and isinstance(new_prefix, str):
            for reference in contract.get("path_references", []):
                if not isinstance(reference, dict):
                    continue
                fact_id = str(reference.get("fact_id") or "")
                original = str(reference.get("path") or "")
                if not fact_id or not _output_has_prefix(original, old_prefix):
                    continue
                replacement = _replace_output_prefix(
                    original, old_prefix, new_prefix
                )
                binding_key = f"path_reference:{fact_id}"
                add(binding_key, original, replacement)
                if replacement != original and binding_key in patchable:
                    patched_path_roots.append((original, replacement))

    def covered_by_patched_root(original: str, runtime: str) -> bool:
        for old_root, new_root in patched_path_roots:
            if original == old_root:
                suffix = ""
            elif (
                original.startswith(old_root)
                and len(original) > len(old_root)
                and original[len(old_root)] in (*_PREFIX_DELIMITERS, "/")
            ):
                suffix = original[len(old_root) :]
            else:
                continue
            if runtime == new_root + suffix:
                return True
        return False

    def _root_prefix_pair(original: str, runtime: str) -> tuple[str, str] | None:
        """Extract old / new root from a prefix-driven output rename."""
        orig_path = PurePosixPath(original)
        rt_path = PurePosixPath(runtime)
        for i, (oc, rc) in enumerate(zip(orig_path.parts, rt_path.parts)):
            if oc != rc:
                return (
                    str(PurePosixPath(*orig_path.parts[: i + 1])),
                    str(PurePosixPath(*rt_path.parts[: i + 1])),
                )
        return None

    for binding in resolved.get("output_bindings", []):
        original = str(binding.get("original_path") or "")
        runtime = _safe_runtime_relative(
            binding.get("runtime_path"), field="runtime output path"
        )
        binding_key = f"path:{original}"
        if runtime == original:
            continue
        if binding_key in patchable:
            add(binding_key, original, runtime)
        elif covered_by_patched_root(original, runtime):
            continue
        else:
            # When a prefix-driven output rename has no explicit patch target
            # in the shell script, register the old/new root pair so that all
            # sibling outputs under the same directory prefix are covered.
            root = _root_prefix_pair(original, runtime)
            if root is not None:
                patched_path_roots.append(root)
                if covered_by_patched_root(original, runtime):
                    continue
            add(binding_key, original, runtime)
    for binding in resolved.get("parameter_bindings", []):
        fact_id = str(binding.get("fact_id") or "")
        original = str(binding.get("original_value") or "")
        runtime = str(binding.get("runtime_value") or "")
        if not fact_id or not runtime or _has_control(runtime):
            raise RuntimeBindingError(
                "PARAMETER_VALUE_INVALID", f"unsafe runtime parameter: {fact_id}"
            )
        # 输出目录参数（--outdir / -oc / mkdir -p 的值）跟随输出前缀重命名，
        # 否则渲染后的 run.sh 里目录参数与 file 输出路径指向不一致的目录。
        if isinstance(prefix, dict):
            old_prefix = prefix.get("old_prefix")
            new_prefix = prefix.get("new_prefix")
            if (
                isinstance(old_prefix, str)
                and isinstance(new_prefix, str)
                and _output_has_prefix(runtime, old_prefix)
            ):
                runtime = _replace_output_prefix(runtime, old_prefix, new_prefix)
        add(f"parameter:{fact_id}", original, runtime)

    targets_by_key = {
        str(target.get("binding_key") or ""): target
        for section in ("patch_targets", "unpatchable_targets")
        for target in contract.get(section, [])
        if isinstance(target, dict) and target.get("binding_key")
    }
    patchable_by_literal: dict[str, list[str]] = {}
    for target in contract.get("patch_targets", []):
        if not isinstance(target, dict):
            continue
        key = str(target.get("binding_key") or "")
        literal = str(target.get("original_literal") or "")
        if key and literal:
            patchable_by_literal.setdefault(literal, []).append(key)

    alias_coverage: dict[str, dict[str, list[str]]] = {}
    for binding_key in list(requested):
        if binding_key not in patchable:
            target = targets_by_key.get(binding_key, {})
            literal = str(target.get("original_literal") or "")
            candidates = [
                key
                for key in patchable_by_literal.get(literal, [])
                if key in requested
            ]
            matching = [
                key
                for key in candidates
                if requested[key] == requested[binding_key]
            ]
            if matching:
                owner = sorted(matching)[0]
                evidence = alias_coverage.setdefault(
                    owner,
                    {"covered_binding_keys": [], "covered_fact_ids": []},
                )
                evidence["covered_binding_keys"].append(binding_key)
                evidence["covered_fact_ids"].extend(
                    str(fact_id)
                    for fact_id in target.get("fact_ids", [])
                    if isinstance(fact_id, str)
                )
                del requested[binding_key]
                continue
            if candidates:
                raise RuntimeBindingError(
                    "PATCH_REPLACEMENT_CONFLICT",
                    f"same literal resolves to different values: {literal}",
                )
            raise RuntimeBindingError(
                "PATCH_TARGET_UNAVAILABLE",
                f"changed binding has no safe shell patch target: {binding_key}",
            )
    for evidence in alias_coverage.values():
        evidence["covered_binding_keys"] = sorted(
            set(evidence["covered_binding_keys"])
        )
        evidence["covered_fact_ids"] = sorted(set(evidence["covered_fact_ids"]))
    return requested, alias_coverage


def _shell_word_pattern(literal: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![{_SHELL_WORD_EDGE}])"
        rf"(?P<quote>['\"]?){re.escape(literal)}(?P=quote)"
        rf"(?![{_SHELL_WORD_EDGE}])"
    )


def _shell_word_replacement(replacement: str) -> Callable[[re.Match[str]], str]:
    """替换函数：保留原文中字面量自带的引号。

    ``shlex.quote`` 在 shell 上下文是正确的，但 ``python -c "...'a.fa'..."`` 里的
    字符串字面量替换后必须保留引号，否则会变成裸标识符导致 NameError。
    """

    def _replace(match: re.Match[str]) -> str:
        quote = match.group("quote") or ""
        return f"{quote}{replacement}{quote}"

    return _replace


def _script_positional_args(contract: dict[str, Any], script: str) -> list[str]:
    try:
        tokens = shlex.split(str(contract.get("original_command") or ""))
    except ValueError as exc:
        raise RuntimeBindingError(
            "PATCH_ANCHOR_MISMATCH", "entry command cannot be tokenized"
        ) from exc
    normalized_script = str(PurePosixPath(script))
    for index, token in enumerate(tokens):
        normalized_token = str(PurePosixPath(token))
        while normalized_token.startswith("./"):
            normalized_token = normalized_token[2:]
        if normalized_token == normalized_script:
            return tokens[index + 1 :]
    raise RuntimeBindingError(
        "PATCH_ANCHOR_MISMATCH",
        f"patch script is not the locked entry script: {script}",
    )


def _anchor_command(
    commands: list[str], anchor: dict[str, Any], literal: str
) -> tuple[int, int]:
    step_id = anchor.get("step_id")
    match = re.fullmatch(r"step_(\d+)", str(step_id or ""))
    if match is None:
        raise RuntimeBindingError(
            "PATCH_ANCHOR_MISMATCH", f"invalid patch step: {step_id}"
        )
    step_index = int(match.group(1)) - 1
    if step_index < 0 or step_index >= len(commands):
        raise RuntimeBindingError(
            "PATCH_ANCHOR_MISMATCH", f"patch step is missing: {step_id}"
        )
    command = commands[step_index]
    digest = hashlib.sha256(command.encode("utf-8")).hexdigest()
    if digest != anchor.get("command_digest"):
        raise RuntimeBindingError(
            "PATCH_ANCHOR_MISMATCH", f"patch command changed: {step_id}"
        )
    try:
        token_index = int(anchor.get("token_index"))
        tokens = shlex.split(command)
    except (TypeError, ValueError) as exc:
        raise RuntimeBindingError(
            "PATCH_ANCHOR_MISMATCH", f"patch token is invalid: {step_id}"
        ) from exc
    if token_index < 0 or token_index >= len(tokens) or tokens[token_index] != literal:
        raise RuntimeBindingError(
            "PATCH_ANCHOR_MISMATCH",
            f"patch literal is not at its locked token: {step_id}:{token_index}",
        )
    return step_index, token_index


def _validate_patch_anchors_before(
    commands: list[str], targets: list[dict[str, Any]]
) -> None:
    for target in targets:
        literal = str(target.get("original_literal") or "")
        anchors = target.get("anchors")
        if not isinstance(anchors, list) or not anchors:
            raise RuntimeBindingError(
                "PATCH_ANCHOR_MISMATCH",
                f"patch target has no command anchor: {literal}",
            )
        for anchor in anchors:
            if not isinstance(anchor, dict):
                raise RuntimeBindingError(
                    "PATCH_ANCHOR_MISMATCH", f"invalid patch anchor: {literal}"
                )
            _anchor_command(commands, anchor, literal)


def _validate_patch_anchors_after(
    before: list[str],
    after: list[str],
    targets: list[dict[str, Any]],
    replacements: dict[str, str],
) -> None:
    if len(after) != len(before):
        raise RuntimeBindingError(
            "PATCH_ANCHOR_MISMATCH", "patch changed the logical command count"
        )
    for target in targets:
        replacement = replacements[str(target["binding_key"])]
        for anchor in target["anchors"]:
            match = re.fullmatch(r"step_(\d+)", str(anchor.get("step_id") or ""))
            if match is None:
                raise RuntimeBindingError(
                    "PATCH_ANCHOR_MISMATCH", "invalid post-patch step"
                )
            step_index = int(match.group(1)) - 1
            token_index = int(anchor.get("token_index"))
            try:
                tokens = shlex.split(after[step_index])
            except (IndexError, ValueError) as exc:
                raise RuntimeBindingError(
                    "PATCH_ANCHOR_MISMATCH", "post-patch command cannot be tokenized"
                ) from exc
            if token_index >= len(tokens) or tokens[token_index] != replacement:
                raise RuntimeBindingError(
                    "PATCH_ANCHOR_MISMATCH",
                    "patched value is not at the locked command token",
                )


def _render_script_updates(
    contract: dict[str, Any],
    raw_code: Path,
    replacements: dict[str, str],
    alias_coverage: dict[str, dict[str, list[str]]],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    selected = [
        target
        for target in contract.get("patch_targets", [])
        if isinstance(target, dict)
        and target.get("binding_key") in replacements
    ]
    by_literal: dict[str, str] = {}
    for target in selected:
        literal = str(target.get("original_literal") or "")
        replacement = replacements[str(target["binding_key"])]
        previous = by_literal.get(literal)
        if previous is not None and previous != replacement:
            raise RuntimeBindingError(
                "PATCH_REPLACEMENT_CONFLICT",
                f"literal has conflicting runtime values: {literal}",
            )
        by_literal[literal] = replacement

    grouped: dict[str, list[dict[str, Any]]] = {}
    for target in selected:
        script = _safe_runtime_relative(
            target.get("script_path"), field="patch script path"
        )
        grouped.setdefault(script, []).append(target)

    rendered: dict[str, str] = {}
    patch_diff: list[dict[str, Any]] = []
    for script, targets in sorted(grouped.items()):
        source = raw_code / script
        if not source.is_file():
            raise RuntimeBindingError(
                "PATCH_TARGET_MISSING", f"patch script does not exist: {script}"
            )
        source_hash = _sha256_file(source)
        if any(target.get("script_hash") != source_hash for target in targets):
            raise RuntimeBindingError(
                "SOURCE_HASH_MISMATCH", f"patch script hash changed: {script}"
            )
        text = source.read_text(encoding="utf-8", errors="replace")
        positional_args = _script_positional_args(contract, script)
        before_commands = _prepare_script_commands(text, positional_args)
        _validate_patch_anchors_before(before_commands, targets)
        seen: set[tuple[str, str]] = set()
        for target in targets:
            literal = str(target.get("original_literal") or "")
            replacement = replacements[str(target["binding_key"])]
            identity = (literal, replacement)
            if identity in seen:
                continue
            seen.add(identity)
            try:
                expected = int(target.get("expected_exact_match_count"))
            except (TypeError, ValueError) as exc:
                raise RuntimeBindingError(
                    "PATCH_TARGET_INVALID", f"invalid match count for {literal}"
                ) from exc
            if expected < 1:
                raise RuntimeBindingError(
                    "PATCH_TARGET_INVALID", f"invalid match count for {literal}"
                )
            updated, count = _shell_word_pattern(literal).subn(
                _shell_word_replacement(replacement), text
            )
            if count < expected:
                raise RuntimeBindingError(
                    "PATCH_MATCH_COUNT_MISMATCH",
                    f"expected at least {expected} exact matches for {literal} in {script}, found {count}",
                )
            text = updated
            binding_key = str(target["binding_key"])
            patch_diff.append(
                {
                    "script_path": script,
                    "binding_key": binding_key,
                    "patch_kind": target.get("patch_kind"),
                    "original_literal": literal,
                    "runtime_literal": replacement,
                    "replacement_count": count,
                    **alias_coverage.get(binding_key, {}),
                }
            )
        after_commands = _prepare_script_commands(text, positional_args)
        _validate_patch_anchors_after(
            before_commands, after_commands, targets, replacements
        )
        rendered[script] = text
    return rendered, patch_diff


def _input_materialization_plan(
    contract: dict[str, Any], resolved: dict[str, Any], code_paths: set[str]
) -> list[dict[str, Any]]:
    modified_originals = {
        str(binding.get("original_path") or "")
        for binding in resolved.get("output_bindings", [])
    }
    used: set[str] = set()
    plan: list[dict[str, Any]] = []
    for binding in resolved.get("input_bindings", []):
        original = str(binding.get("original_path") or "")
        lexical_source = Path(str(binding.get("runtime_path") or ""))
        source = lexical_source.resolve()
        if not (source.is_file() or source.is_dir()):
            raise RuntimeBindingError(
                "INPUT_PATH_NOT_FOUND", f"runtime input does not exist: {source}"
            )
        destination = lexical_source.name
        _safe_runtime_relative(destination, field="materialized input path")
        if destination in {".", ".."} or destination.startswith("-"):
            raise RuntimeBindingError(
                "UNSAFE_INPUT_BASENAME",
                f"runtime input basename is unsafe as a shell word: {destination}",
            )
        if destination in used or destination in code_paths:
            raise RuntimeBindingError(
                "RUNTIME_PATH_COLLISION",
                f"runtime input basename collides in work directory: {destination}",
            )
        used.add(destination)
        if source.is_dir():
            plan.append(
                {
                    "original_path": original,
                    "runtime_path": str(lexical_source),
                    "materialized_path": destination,
                    "method": "copy_tree",
                    "sha256": _sha256_tree(source),
                }
            )
        else:
            plan.append(
                {
                    "original_path": original,
                    "runtime_path": str(lexical_source),
                    "materialized_path": destination,
                    "method": "copy" if original in modified_originals else "symlink",
                    "sha256": _sha256_file(source),
                }
            )
    return plan


def render_runtime_package(
    contract: dict[str, Any],
    raw_code: Path,
    resolved: dict[str, Any],
    run_dir: Path,
) -> dict[str, Any]:
    """Render a resolved binding into a job-local package without executing it."""

    raw_code = Path(raw_code)
    _declared, source_hashes = _validate_render_inputs(
        contract, raw_code, resolved
    )
    materialization = _input_materialization_plan(
        contract, resolved, set(source_hashes)
    )
    replacements, alias_coverage = _binding_replacements(contract, resolved)
    script_updates, patch_diff = _render_script_updates(
        contract, raw_code, replacements, alias_coverage
    )

    expected_outputs: list[dict[str, Any]] = []
    materialized_by_original = {
        item["original_path"]: item["materialized_path"]
        for item in materialization
    }
    for binding in resolved.get("output_bindings", []):
        runtime_path = _safe_runtime_relative(
            binding.get("runtime_path"), field="runtime output path"
        )
        if runtime_path in source_hashes:
            raise RuntimeBindingError(
                "RUNTIME_PATH_COLLISION",
                f"runtime output collides with code: {runtime_path}",
            )
        original_path = str(binding.get("original_path") or "")
        input_path = materialized_by_original.get(original_path)
        if runtime_path in set(materialized_by_original.values()) and runtime_path != input_path:
            raise RuntimeBindingError(
                "RUNTIME_PATH_COLLISION",
                f"runtime output collides with another input: {runtime_path}",
            )
        expected_outputs.append(
            {
                "fact_id": binding.get("fact_id"),
                "original_path": original_path,
                "runtime_path": runtime_path,
            }
        )

    command = contract.get("original_command")
    if (
        not isinstance(command, str)
        or not command.strip()
        or _has_control(command.replace("\n", ""))
    ):
        raise RuntimeBindingError(
            "ENTRY_COMMAND_INVALID", "binding contract has no executable command"
        )
    run_dir = Path(run_dir)
    if run_dir.exists() and any(run_dir.iterdir()):
        raise RuntimeBindingError(
            "RUN_DIRECTORY_NOT_EMPTY", f"runtime directory is not empty: {run_dir}"
        )
    work_dir = run_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)

    source_script_updates: dict[str, str] = {}

    # When a prefix rename moves an entire output directory (e.g.
    # ``meme_out/`` → ``output/``) and no patch targets exist to
    # tell the tool to write elsewhere, rewrite the command so the
    # output lands where the runtime contract expects it.
    prefix = resolved.get("prefix_binding")
    actual_command = command
    if (
        isinstance(prefix, dict)
        and isinstance(prefix.get("old_prefix"), str)
        and isinstance(prefix.get("new_prefix"), str)
        and not replacements
    ):
        old = prefix["old_prefix"]
        new = prefix["new_prefix"]
        if any(
            b.get("transformed") for b in resolved.get("output_bindings", [])
        ):
            # 目录前缀（如 meme_out/logo1.png 的 meme_out）→ -oc 注入。
            # 文件名 stem（如 input_qc.tsv）不是目录，不能注入 -oc。
            transformed_paths = [
                b["original_path"]
                for b in resolved.get("output_bindings", [])
                if b.get("transformed")
            ]
            is_dir_prefix = _prefix_targets_directory(transformed_paths, old)
            if is_dir_prefix:
                actual_command = _inject_dir_prefix_override(
                    command, old, new
                )
            # 改写 shell 脚本里的命令（位置参数替换 / -oc 注入）。
            # 只处理 .sh 脚本：代码文件（.py 等）的输出路径由
            # _render_script_updates 负责，shlex 会破坏它们的语法。
            for script in sorted(source_hashes):
                if not script.endswith(".sh"):
                    continue
                script_path = raw_code / script
                inner = script_path.read_text(
                    encoding="utf-8", errors="replace"
                )
                rewritten = _replace_positional_prefix(inner, old, new)
                if rewritten == inner and is_dir_prefix:
                    rewritten = _inject_dir_prefix_override(inner, old, new)
                if rewritten != inner:
                    source_script_updates[script] = rewritten
                    _symlink_prefix_data_files(
                        old, new, work_dir, set(source_hashes)
                    )

    for relative in sorted(source_hashes):
        destination = work_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(raw_code / relative, destination)
        if _sha256_file(destination) != source_hashes[relative]:
            raise RuntimeBindingError(
                "SOURCE_COPY_HASH_MISMATCH",
                f"copied code does not match its locked source: {relative}",
            )
    for script, text in script_updates.items():
        (work_dir / script).write_text(text, encoding="utf-8")
    for script, text in source_script_updates.items():
        if script not in script_updates:
            (work_dir / script).write_text(text, encoding="utf-8")
    for item in materialization:
        destination = work_dir / item["materialized_path"]
        source = Path(item["runtime_path"])
        if item["method"] == "copy_tree":
            shutil.copytree(source, destination, symlinks=True)
        elif item["method"] == "copy":
            shutil.copy2(source, destination)
        else:
            destination.symlink_to(source)

    # Copy locked raw_test_data files into work_dir so validation has
    # the same data files baseline did (exposed_as_input=False files
    # are not in source_hashes nor materialization).
    root = raw_code.parent
    raw_data_root = root / "raw_test_data"
    for record in contract.get("package_files", []):
        if not isinstance(record, dict):
            continue
        if record.get("source") != "raw_test_data":
            continue
        relative = str(record.get("path") or "")
        if not relative:
            continue
        # exposed_as_input files are already handled via materialization
        if record.get("exposed_as_input"):
            continue
        src = raw_data_root / relative
        dst = work_dir / relative
        if src.is_file() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    # When a prefix rename is active, create symlinks so tools that
    # derive input paths from the positional output-prefix argument
    # (e.g. MCScanX ./output expects output.blast) can find their
    # data files under the new prefix.
    if (
        isinstance(prefix, dict)
        and isinstance(prefix.get("old_prefix"), str)
        and isinstance(prefix.get("new_prefix"), str)
    ):
        _symlink_prefix_data_files(
            prefix["old_prefix"],
            prefix["new_prefix"],
            work_dir,
            # Collect every regular file now present in work_dir.
            {
                str(p.relative_to(work_dir))
                for p in work_dir.rglob("*")
                if p.is_file()
            },
        )

    locked_work_hashes = {
        relative: _sha256_file(work_dir / relative)
        for relative in sorted(source_hashes)
    }

    record = {
        "schema_version": "lightweight-runtime-package/v1",
        "status": "rendered",
        "facts_sha256": contract.get("facts_sha256"),
        "package_digest": contract.get("package_digest"),
        "binding_digest": resolved.get("binding_digest"),
        "original_command": command,
        "actual_command": actual_command,
        "_prefix_binding": prefix,
        "run_dir": str(run_dir.resolve()),
        "work_dir": str(work_dir.resolve()),
        "source_hashes": source_hashes,
        "locked_work_hashes": locked_work_hashes,
        "render_work_tree": _work_tree_manifest(work_dir),
        "input_materialization": materialization,
        "patch_diff": patch_diff,
        "expected_outputs": expected_outputs,
        "stdout_contract": deepcopy(
            contract.get("outputs", {}).get("stdout", {})
        ),
    }
    record["record_digest"] = _digest_without(record, "record_digest")
    record_path = run_dir / "runtime_binding_record.json"
    record_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return record


def _event(event: str, **details: Any) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **details,
    }


_UNSAFE_ENV_KEYS = {
    "BASH_ENV",
    "ENV",
    "SHELLOPTS",
    "BASHOPTS",
    "CDPATH",
    "GLOBIGNORE",
    "PS4",
    "PROMPT_COMMAND",
    "LD_PRELOAD",
    "LD_AUDIT",
}


def _unsafe_env_key(key: str) -> bool:
    return key in _UNSAFE_ENV_KEYS or key.startswith("BASH_FUNC_")


def _validate_tool_path(value: str, *, filter_invalid: bool) -> list[str]:
    entries: list[str] = []
    for raw_entry in value.split(os.pathsep):
        valid = bool(raw_entry) and not _has_control(raw_entry)
        path = Path(raw_entry) if valid else None
        valid = bool(path is not None and path.is_absolute())
        try:
            resolved = path.resolve(strict=True) if valid and path is not None else None
        except OSError:
            resolved = None
        valid = bool(resolved is not None and resolved.is_dir())
        if not valid:
            if filter_invalid:
                continue
            raise RuntimeBindingError(
                "UNSAFE_TOOL_PATH",
                f"PATH entry must be one existing absolute directory: {raw_entry!r}",
            )
        canonical = str(resolved)
        if canonical not in entries:
            entries.append(canonical)
    if not entries:
        raise RuntimeBindingError(
            "UNSAFE_TOOL_PATH", "PATH contains no valid absolute tool directory"
        )
    return entries


def _process_environment(
    env: dict[str, str] | None,
) -> tuple[dict[str, str], list[str], str]:
    supplied = {str(key): str(value) for key, value in (env or {}).items()}
    unsafe = sorted(key for key in supplied if _unsafe_env_key(key))
    if unsafe:
        raise RuntimeBindingError(
            "UNSAFE_ENVIRONMENT",
            f"runtime environment contains a shell injection key: {unsafe[0]}",
        )
    process_env = {
        key: value for key, value in os.environ.items() if not _unsafe_env_key(key)
    }
    explicit_tool_path = "PATH" in supplied
    tool_path_entries = _validate_tool_path(
        supplied.get("PATH", process_env.get("PATH", "")),
        filter_invalid=not explicit_tool_path,
    )
    process_env.update({key: value for key, value in supplied.items() if key != "PATH"})
    process_env["PATH"] = os.pathsep.join(tool_path_entries)
    return process_env, tool_path_entries, _sha256_json(tool_path_entries)


def _trusted_bash() -> Path:
    for candidate in (Path("/usr/bin/bash"), Path("/bin/bash")):
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if (
            resolved.is_file()
            and os.access(resolved, os.X_OK)
            and resolved.parent in {Path("/usr/bin"), Path("/bin")}
        ):
            return resolved
    raise RuntimeBindingError(
        "TRUSTED_BASH_NOT_FOUND", "no trusted absolute system Bash is available"
    )


def _absolute_entry_bash(command: str, bash_path: Path) -> str:
    return re.sub(
        r"^\s*(?:bash|/bin/bash|/usr/bin/bash)(?=\s)",
        shlex.quote(str(bash_path)),
        command,
        count=1,
    )


def _work_tree_manifest(root: Path) -> dict[str, dict[str, Any]]:
    manifest: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        _safe_runtime_relative(relative, field="work tree path")
        if path.is_symlink():
            manifest[relative] = {
                "type": "symlink",
                "target": os.readlink(path),
            }
        elif path.is_dir():
            manifest[relative] = {"type": "directory"}
        elif path.is_file():
            manifest[relative] = {
                "type": "regular",
                "sha256": _sha256_file(path),
            }
        else:
            raise RuntimeBindingError(
                "WORK_TREE_UNSUPPORTED",
                f"unsupported work tree entry: {relative}",
            )
    return manifest


def _verify_render_work_tree(
    rendered_package: dict[str, Any], work_dir: Path
) -> None:
    if _work_tree_manifest(work_dir) != rendered_package.get("render_work_tree"):
        raise RuntimeBindingError(
            "WORK_TREE_CHANGED",
            "runtime work tree changed after package rendering",
        )


def _load_and_validate_render_record(
    rendered_package: dict[str, Any],
) -> tuple[Path, Path]:
    if (
        rendered_package.get("schema_version")
        != "lightweight-runtime-package/v1"
        or rendered_package.get("status") != "rendered"
    ):
        raise RuntimeBindingError(
            "RENDERED_PACKAGE_INVALID", "runtime package is not ready to execute"
        )
    run_dir = Path(str(rendered_package.get("run_dir") or ""))
    work_dir = Path(str(rendered_package.get("work_dir") or ""))
    if (
        not work_dir.is_dir()
        or work_dir.resolve() != (run_dir.resolve() / "work")
    ):
        raise RuntimeBindingError(
            "RENDERED_PACKAGE_INVALID", "work directory is missing or detached"
        )
    record_path = run_dir / "runtime_binding_record.json"
    try:
        disk_record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeBindingError(
            "RECORD_MISSING", "runtime binding record cannot be loaded"
        ) from exc
    if disk_record.get("record_digest") != _digest_without(
        disk_record, "record_digest"
    ):
        raise RuntimeBindingError(
            "RECORD_DIGEST_MISMATCH", "runtime binding record was modified"
        )
    if rendered_package.get("record_digest") != _digest_without(
        rendered_package, "record_digest"
    ):
        raise RuntimeBindingError(
            "RECORD_DIGEST_MISMATCH", "in-memory runtime binding record is invalid"
        )
    if disk_record != rendered_package:
        raise RuntimeBindingError(
            "RECORD_MISMATCH", "disk and in-memory runtime binding records differ"
        )
    if rendered_package.get("actual_command") != rendered_package.get(
        "original_command"
    ):
        # Permit the discrepancy when the only change is a directory-prefix
        # override injected by render_runtime_package (e.g. ``-oc output``).
        binding = rendered_package.get("_prefix_binding") or {}
        if not (
            isinstance(binding, dict)
            and isinstance(binding.get("old_prefix"), str)
            and isinstance(binding.get("new_prefix"), str)
        ):
            raise RuntimeBindingError(
                "ENTRY_COMMAND_CHANGED",
                "actual command differs from the locked entry command",
            )
    return run_dir, work_dir


def _verify_locked_work(rendered_package: dict[str, Any], work_dir: Path) -> None:
    for relative, expected in rendered_package.get("locked_work_hashes", {}).items():
        safe = _safe_runtime_relative(relative, field="locked work path")
        path = work_dir / safe
        if not path.is_file() or path.is_symlink() or _sha256_file(path) != expected:
            raise RuntimeBindingError(
                "WORK_PACKAGE_CHANGED", f"locked work file changed: {safe}"
            )


def _verify_runtime_inputs(
    rendered_package: dict[str, Any], work_dir: Path, *, after: bool
) -> None:
    for item in rendered_package.get("input_materialization", []):
        source = Path(str(item.get("runtime_path") or ""))
        materialized = work_dir / _safe_runtime_relative(
            item.get("materialized_path"), field="materialized input path"
        )
        expected = item.get("sha256")
        if item.get("method") == "copy_tree":
            if not source.is_dir() or _sha256_tree(source) != expected:
                raise RuntimeBindingError(
                    "INPUT_CONTENT_CHANGED",
                    f"runtime input directory content changed: {source}",
                )
            if after:
                continue
            if not materialized.is_dir() or _sha256_tree(materialized) != expected:
                raise RuntimeBindingError(
                    "INPUT_CONTENT_CHANGED",
                    f"materialized input directory content changed: {materialized}",
                )
            continue
        if not source.is_file() or _sha256_file(source) != expected:
            raise RuntimeBindingError(
                "INPUT_CONTENT_CHANGED",
                f"runtime input content changed: {source}",
            )
        if after:
            continue
        if not materialized.is_file() or _sha256_file(materialized) != expected:
            raise RuntimeBindingError(
                "INPUT_CONTENT_CHANGED",
                f"materialized input content changed: {materialized}",
            )


def _isolate_runtime_inputs(
    rendered_package: dict[str, Any], work_dir: Path
) -> list[dict[str, str]]:
    execution_inputs: list[dict[str, str]] = []
    for item in rendered_package.get("input_materialization", []):
        source = Path(str(item.get("runtime_path") or ""))
        relative = _safe_runtime_relative(
            item.get("materialized_path"), field="materialized input path"
        )
        destination = work_dir / relative
        if item.get("method") == "copy_tree":
            if not destination.is_dir() or destination.is_symlink():
                raise RuntimeBindingError(
                    "INPUT_ISOLATION_FAILED",
                    f"input directory is not an isolated copy: {relative}",
                )
            execution_inputs.append(
                {
                    "materialized_path": relative,
                    "mode": "isolated_copy",
                    "source_path": str(source),
                }
            )
            continue
        if item.get("method") == "symlink":
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix=".bbav5-input-", dir=destination.parent
            )
            os.close(file_descriptor)
            temporary = Path(temporary_name)
            try:
                shutil.copy2(source, temporary)
                if _sha256_file(temporary) != item.get("sha256"):
                    raise RuntimeBindingError(
                        "INPUT_CONTENT_CHANGED",
                        f"isolated input copy does not match source: {source}",
                    )
                os.replace(temporary, destination)
            finally:
                if temporary.exists():
                    temporary.unlink()
        elif item.get("method") != "copy":
            raise RuntimeBindingError(
                "RENDERED_PACKAGE_INVALID",
                f"unknown input materialization method: {item.get('method')}",
            )
        if not destination.is_file() or destination.is_symlink():
            raise RuntimeBindingError(
                "INPUT_ISOLATION_FAILED", f"input is not an isolated copy: {relative}"
            )
        execution_inputs.append(
            {
                "materialized_path": relative,
                "mode": "isolated_copy",
                "source_path": str(source),
            }
        )
    return execution_inputs


def replay_runtime_binding(
    rendered_package: dict[str, Any],
    env: dict[str, str] | None = None,
    on_process_start: Callable[[ProcessIdentity], None] | None = None,
) -> dict[str, Any]:
    """Execute one rendered package and validate only its declared results."""

    run_dir, work_dir = _load_and_validate_render_record(rendered_package)
    command = rendered_package.get("actual_command")
    if not isinstance(command, str) or not command.strip():
        raise RuntimeBindingError(
            "ENTRY_COMMAND_INVALID", "rendered package has no actual command"
        )
    process_env, tool_path_entries, tool_path_digest = _process_environment(env)
    bash_path = _trusted_bash()
    execution_command = _absolute_entry_bash(command, bash_path)
    _verify_locked_work(rendered_package, work_dir)
    _verify_render_work_tree(rendered_package, work_dir)
    _verify_runtime_inputs(rendered_package, work_dir, after=False)
    execution_inputs = _isolate_runtime_inputs(rendered_package, work_dir)
    _verify_runtime_inputs(rendered_package, work_dir, after=True)
    execution_work_tree = _work_tree_manifest(work_dir)

    before = inventory_files(work_dir)
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / "stdout.log"
    stderr_path = logs_dir / "stderr.log"
    events_path = logs_dir / "execution_events.jsonl"
    event_start = _event(
        "start",
        command=execution_command,
        bash_path=str(bash_path),
        tool_path_digest=tool_path_digest,
        tool_path_entries=tool_path_entries,
        execution_inputs=execution_inputs,
        execution_work_tree=execution_work_tree,
    )
    with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
        process, process_identity = start_managed_process(
            [str(bash_path), "-c", execution_command],
            cwd=work_dir,
            env=process_env,
            stdout=stdout_handle,
            stderr=stderr_handle,
        )
        event_start["process"] = process_identity
        events_path.write_text(
            json.dumps(event_start, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        try:
            if on_process_start is not None:
                on_process_start(dict(process_identity))
        except BaseException:
            stop_process_group(process_identity)
            process.wait()
            raise
        returncode = process.wait()
    after = inventory_files(work_dir)
    missing_outputs: list[str] = []
    outputs: list[dict[str, Any]] = []
    for expected in rendered_package.get("expected_outputs", []):
        relative = _safe_runtime_relative(
            expected.get("runtime_path"), field="expected output path"
        )
        record = after.get(relative)
        if record is None or (
            relative in before
            and before[relative].get("sha256") == record.get("sha256")
        ):
            missing_outputs.append(relative)
            continue
        outputs.append(
            {
                "fact_id": expected.get("fact_id"),
                "runtime_path": relative,
                "status": "modified" if relative in before else "new",
                "size": record["size"],
                "sha256": record["sha256"],
            }
        )
    stdout_required = bool(
        rendered_package.get("stdout_contract", {}).get("present")
    )
    stdout_ok = not stdout_required or stdout_path.stat().st_size > 0
    success = returncode == 0
    post_input_error: RuntimeBindingError | None = None
    try:
        _verify_runtime_inputs(rendered_package, work_dir, after=True)
    except RuntimeBindingError as exc:
        post_input_error = exc
    contract_passed = bool(
        success and not missing_outputs and stdout_ok and post_input_error is None
    )
    if post_input_error is not None:
        failure_type = post_input_error.code
    elif not success:
        failure_type = "execution_failed"
    elif missing_outputs:
        failure_type = "missing_outputs"
    elif not stdout_ok:
        failure_type = "stdout_missing"
    else:
        failure_type = None
    result_path = run_dir / "runtime_execution_result.json"
    result = {
        "success": success,
        "returncode": returncode,
        "process": process_identity,
        "contract_passed": contract_passed,
        "missing_outputs": missing_outputs,
        "outputs": outputs,
        "stdout_contract_passed": stdout_ok,
        "failure_type": failure_type,
        "bash_path": str(bash_path),
        "execution_command": execution_command,
        "tool_path_digest": tool_path_digest,
        "tool_path_entries": tool_path_entries,
        "execution_inputs": execution_inputs,
        "execution_work_tree": execution_work_tree,
        "before_files": before,
        "after_files": after,
        "logs": {
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
            "events": str(events_path),
            "result": str(result_path),
        },
    }
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                _event(
                    "finish",
                    returncode=returncode,
                    contract_passed=contract_passed,
                    missing_outputs=missing_outputs,
                    failure_type=failure_type,
                    bash_path=str(bash_path),
                    tool_path_digest=tool_path_digest,
                    tool_path_entries=tool_path_entries,
                    execution_inputs=execution_inputs,
                    execution_work_tree=execution_work_tree,
                ),
                ensure_ascii=False,
            )
            + "\n"
        )
    if post_input_error is not None:
        raise post_input_error
    return result
