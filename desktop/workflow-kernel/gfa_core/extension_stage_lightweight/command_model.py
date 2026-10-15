"""Build a command model from tokens and observed execution files only."""

from __future__ import annotations

from pathlib import Path
import re
import shlex
from typing import Any


_REDIRECTS = {">", ">>", "1>", "1>>", "2>", "2>>"}
_CONTROL_TOKENS = _REDIRECTS | {"<", "<<", "|", "||", "&&", ";"}
_SHELL_CONTROL_WORDS = {
    "case",
    "do",
    "done",
    "elif",
    "else",
    "esac",
    "exit",
    "fi",
    "for",
    "if",
    "set",
    "then",
    "while",
}


def _normalise_path(value: str, work_dir: Path) -> str:
    path = Path(value)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(work_dir.resolve()).as_posix()
        except ValueError:
            return path.as_posix()
    normalised = path.as_posix()
    while normalised.startswith("./"):
        normalised = normalised[2:]
    return normalised


def _logical_shell_commands(text: str) -> list[str]:
    commands: list[str] = []
    pending = ""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            pending += stripped[:-1].rstrip() + " "
            continue
        logical = (pending + stripped).strip()
        pending = ""
        if logical:
            commands.append(logical)
    if pending.strip():
        commands.append(pending.strip())
    return commands


def _split_shell_chain(command: str) -> list[str]:
    segments: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    escaped = False
    i = 0
    while i < len(command):
        char = command[i]
        if escaped:
            buffer.append(char)
            escaped = False
            i += 1
            continue
        if char == "\\":
            buffer.append(char)
            escaped = True
            i += 1
            continue
        if quote:
            buffer.append(char)
            if char == quote:
                quote = None
            i += 1
            continue
        if char in {"'", '"'}:
            quote = char
            buffer.append(char)
            i += 1
            continue
        is_boundary = char == ";" or (
            char in {"|", "&"}
            and i + 1 < len(command)
            and (char == "|" or command[i + 1] == char)
        )
        if is_boundary:
            segment = "".join(buffer).strip()
            if segment:
                segments.append(segment)
            buffer = []
            if i + 1 < len(command) and command[i + 1] == char:
                i += 1
            i += 1
            continue
        buffer.append(char)
        i += 1
    segment = "".join(buffer).strip()
    if segment:
        segments.append(segment)
    return segments or [command]


def _substitute_shell_variables(command: str, variables: dict[str, str]) -> str:
    result: list[str] = []
    in_single_quote = False
    i = 0
    while i < len(command):
        char = command[i]
        if char == "'":
            in_single_quote = not in_single_quote
            result.append(char)
            i += 1
            continue
        if char != "$" or in_single_quote:
            result.append(char)
            i += 1
            continue
        if i + 1 < len(command) and command[i + 1] == "{":
            end = command.find("}", i + 2)
            if end != -1:
                key = command[i + 2 : end]
                if key in variables:
                    result.append(variables[key])
                    i = end + 1
                    continue
        match = re.match(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+", command[i + 1 :])
        if match:
            key = match.group(0)
            if key in variables:
                result.append(variables[key])
                i += len(key) + 1
                continue
        result.append(char)
        i += 1
    return "".join(result)


def _prepare_script_commands(text: str, positional_args: list[str]) -> list[str]:
    variables = {str(index): value for index, value in enumerate(positional_args, 1)}
    commands: list[str] = []
    for logical in _logical_shell_commands(text):
        for segment in _split_shell_chain(logical):
            expanded = _substitute_shell_variables(segment, variables)
            assignment = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", expanded)
            if assignment:
                values = shlex.split(assignment.group(2))
                variables[assignment.group(1)] = values[0] if values else ""
                continue
            try:
                segment_tokens = shlex.split(expanded)
            except ValueError:
                commands.append(expanded)
                continue
            if not segment_tokens or segment_tokens[0] in _SHELL_CONTROL_WORDS:
                continue
            commands.append(expanded)
    return commands


def _entry_commands(
    command: str, work_dir: Path, code_files: set[str]
) -> tuple[list[str], list[str]]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return [command], []
    for source_index, token in enumerate(tokens):
        relative = _normalise_path(token, work_dir)
        if relative not in code_files:
            continue
        path = work_dir / relative
        if path.suffix in {".sh", ".bash"} or tokens[0] in {"bash", "sh"}:
            script_commands = _prepare_script_commands(
                path.read_text(encoding="utf-8", errors="replace"),
                tokens[source_index + 1 :],
            )
            return script_commands, [relative]
    return _split_shell_chain(command), []


def _matching_outputs(value: str, outputs: set[str], work_dir: Path) -> list[str]:
    relative = _normalise_path(value, work_dir)
    if relative in outputs:
        return [relative]
    return sorted(
        output for output in outputs if output.startswith(relative + ".")
    )


def _append_unique(items: list[dict[str, Any]], item: dict[str, Any]) -> None:
    identity = (item.get("path"), item.get("flag"), item.get("token_index"))
    if all(
        (existing.get("path"), existing.get("flag"), existing.get("token_index"))
        != identity
        for existing in items
    ):
        items.append(item)


def _outer_quoted_token_indexes(command: str, tokens: list[str]) -> set[int]:
    """Return fully quoted token indexes when POSIX/non-POSIX splits align."""

    try:
        source_tokens = shlex.split(command, posix=False)
    except ValueError:
        return set()
    if len(source_tokens) != len(tokens):
        return set()
    for source_token, token in zip(source_tokens, tokens):
        try:
            if shlex.split(source_token, posix=True) != [token]:
                return set()
        except ValueError:
            return set()
    return {
        index
        for index, source_token in enumerate(source_tokens)
        if len(source_token) >= 2
        and source_token[0] in {"'", '"'}
        and source_token[-1] == source_token[0]
    }


def build_command_model(
    *,
    command: str,
    work_dir: Path,
    before_files: dict[str, dict[str, object]],
    output_files: list[str],
    code_files: list[str],
) -> dict[str, Any]:
    """Classify explicit tokens using baseline file evidence.

    No executable name or biological file format is used to make the decision.
    """

    work_dir = Path(work_dir)
    before = set(before_files)
    outputs = set(output_files)
    code = set(code_files)
    commands, source_scripts = _entry_commands(command, work_dir, code)
    models: list[dict[str, Any]] = []
    assigned_outputs: set[str] = set()
    produced_references: set[str] = set()

    for index, logical_command in enumerate(commands, start=1):
        try:
            tokens = shlex.split(logical_command)
        except ValueError as exc:
            models.append(
                {
                    "index": index,
                    "command": logical_command,
                    "tool": None,
                    "parse_error": str(exc),
                    "source_files": [],
                    "inputs": [],
                    "arguments": [],
                    "parameters": [],
                    "outputs": [],
                }
            )
            continue
        if not tokens:
            continue

        model: dict[str, Any] = {
            "index": index,
            "command": logical_command,
            "tool": tokens[0],
            "source_files": [],
            "inputs": [],
            "arguments": [],
            "parameters": [],
            "outputs": [],
        }
        command_outputs: set[str] = set()
        outer_quoted_indexes = _outer_quoted_token_indexes(logical_command, tokens)
        ambiguous_positional_indexes: set[int] = set()
        i = 1
        positional_index = 0
        while i < len(tokens):
            token = tokens[i]
            if token in _REDIRECTS and i + 1 < len(tokens):
                value = _normalise_path(tokens[i + 1], work_dir)
                matched = _matching_outputs(value, outputs, work_dir)
                command_outputs.update(matched or ([value] if value in outputs else []))
                produced_references.add(value)
                i += 2
                continue
            if token in _CONTROL_TOKENS:
                i += 1
                continue

            if token.startswith("-") and token != "-":
                if (
                    "=" not in token
                    and i + 1 in outer_quoted_indexes
                    and i + 2 < len(tokens)
                ):
                    candidate = _normalise_path(tokens[i + 1], work_dir)
                    candidate_is_reference = (
                        candidate in before
                        or candidate in code
                        or candidate in produced_references
                        or bool(_matching_outputs(candidate, outputs, work_dir))
                    )
                    later_has_input_evidence = any(
                        (
                            _normalise_path(later, work_dir) in before
                            and _normalise_path(later, work_dir) not in code
                        )
                        or _normalise_path(later, work_dir) in produced_references
                        for later in tokens[i + 2 :]
                    )
                    if not candidate_is_reference and later_has_input_evidence:
                        argument = {
                            "kind": "switch",
                            "flag": token,
                            "value": token,
                            "token_index": i,
                            "grouping_confidence": "ambiguous",
                        }
                        model["arguments"].append(argument)
                        model["parameters"].append(dict(argument))
                        ambiguous_positional_indexes.add(i + 1)
                        i += 1
                        continue
                if "=" in token:
                    flag, raw_value = token.split("=", 1)
                    value_index = i
                elif i + 1 < len(tokens) and not tokens[i + 1].startswith("-") and tokens[i + 1] not in _CONTROL_TOKENS:
                    flag, raw_value = token, tokens[i + 1]
                    value_index = i + 1
                else:
                    argument = {
                        "kind": "switch",
                        "flag": token,
                        "value": token,
                        "token_index": i,
                    }
                    model["arguments"].append(argument)
                    model["parameters"].append(dict(argument))
                    i += 1
                    continue

                value = _normalise_path(raw_value, work_dir)
                value_role = "value"
                if value in before and value not in code:
                    value_role = "input"
                    _append_unique(
                        model["inputs"],
                        {
                            "path": value,
                            "source": "baseline_input",
                            "flag": flag,
                            "token_index": value_index,
                        },
                    )
                elif value in produced_references:
                    value_role = "input"
                    _append_unique(
                        model["inputs"],
                        {
                            "path": value,
                            "source": "generated_input",
                            "flag": flag,
                            "token_index": value_index,
                        },
                    )
                else:
                    matched = _matching_outputs(value, outputs, work_dir)
                    if matched:
                        value_role = "output_reference"
                        command_outputs.update(matched)
                        produced_references.add(value)

                argument = {
                    "kind": "flag_value",
                    "flag": flag,
                    "value": raw_value,
                    "token_index": i,
                    "value_role": value_role,
                }
                model["arguments"].append(argument)
                if value_role != "input":
                    model["parameters"].append(dict(argument))
                i += 1 if "=" in token else 2
                continue

            relative = _normalise_path(token, work_dir)
            if relative in code:
                if relative not in model["source_files"]:
                    model["source_files"].append(relative)
            elif relative in before:
                _append_unique(
                    model["inputs"],
                    {
                        "path": relative,
                        "source": "baseline_input",
                        "flag": None,
                        "token_index": i,
                    },
                )
            elif relative in produced_references:
                _append_unique(
                    model["inputs"],
                    {
                        "path": relative,
                        "source": "generated_input",
                        "flag": None,
                        "token_index": i,
                    },
                )
            else:
                matched = _matching_outputs(relative, outputs, work_dir)
                value_role = "output_reference" if matched else "value"
                command_outputs.update(matched)
                if matched:
                    produced_references.add(relative)
                parameter = {
                    "kind": "positional",
                    "flag": None,
                    "value": token,
                    "token_index": i,
                    "position": positional_index,
                    "value_role": value_role,
                }
                if i in ambiguous_positional_indexes:
                    parameter["grouping_confidence"] = "ambiguous"
                model["parameters"].append(parameter)
                positional_index += 1
            i += 1

        model["outputs"] = sorted(command_outputs)
        assigned_outputs.update(command_outputs)
        models.append(model)

    return {
        "entry_command": command,
        "source_scripts": source_scripts,
        "commands": models,
        "unmatched_outputs": sorted(outputs - assigned_outputs),
    }
