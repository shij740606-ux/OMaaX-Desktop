"""Create and replay a minimal deterministic extension specification."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shlex
from typing import Any

from .command_model import build_command_model
from .evidence import run_raw_baseline


def _output_files(evidence: dict[str, Any]) -> list[str]:
    return sorted(
        set(evidence["outputs"]["new_files"])
        | set(evidence["outputs"]["modified_files"])
    )


def _fact_id(step_id: str, kind: str, item: dict[str, Any]) -> str:
    token_index = item.get("token_index")
    if not isinstance(token_index, int):
        raise ValueError(f"{kind} fact is missing token_index in {step_id}")
    return f"{step_id}:{kind}:{token_index:03d}"


def _facts_sha256(facts: dict[str, Any]) -> str:
    payload = json.dumps(
        facts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_execution_facts(evidence: dict[str, Any]) -> dict[str, Any]:
    """Build an immutable, ID-addressable view of observed execution facts."""

    output_files = _output_files(evidence)
    command_model = build_command_model(
        command=evidence["command"],
        work_dir=Path(evidence["work_dir"]),
        before_files=evidence["before_files"],
        output_files=output_files,
        code_files=evidence["staged"]["code_files"],
    )

    code_files = list(evidence["staged"]["code_files"])
    data_files = list(evidence["staged"]["data_files"])
    inventory: dict[str, dict[str, Any]] = {}
    for source, paths in (("raw_code", code_files), ("raw_test_data", data_files)):
        for path in paths:
            inventory[path] = {
                **deepcopy(evidence["before_files"][path]),
                "source": source,
            }

    commands = deepcopy(command_model["commands"])
    producers: dict[str, list[str]] = {path: [] for path in output_files}
    for command in commands:
        step_id = f"step_{command['index']:03d}"
        command["step_id"] = step_id
        for item in command["inputs"]:
            item["fact_id"] = _fact_id(step_id, "input", item)
        for item in command["parameters"]:
            item["fact_id"] = _fact_id(step_id, "parameter", item)
        for path in command["outputs"]:
            if path in producers:
                producers[path].append(step_id)

    file_facts = [
        {
            "fact_id": f"output:file:{index:03d}",
            "path": path,
            "producer_step_ids": producers[path],
        }
        for index, path in enumerate(output_files, start=1)
    ]
    output_ids = {item["path"]: item["fact_id"] for item in file_facts}
    stdout = deepcopy(evidence["outputs"]["stdout"])
    if stdout.get("present"):
        stdout["fact_id"] = "output:stdout"

    facts: dict[str, Any] = {
        "original_command": evidence["command"],
        "package": {
            "code_files": code_files,
            "data_files": data_files,
            "inventory": inventory,
        },
        "source_scripts": list(command_model["source_scripts"]),
        "commands": commands,
        "outputs": {
            "files": file_facts,
            "stdout": stdout,
        },
        "unmatched_output_fact_ids": [
            output_ids[path] for path in command_model["unmatched_outputs"]
        ],
        "baseline": {
            "success": evidence["success"],
            "returncode": evidence["returncode"],
            "logs": deepcopy(evidence["logs"]),
        },
    }
    facts["facts_sha256"] = _facts_sha256(facts)
    return facts


def build_spec(evidence: dict[str, Any]) -> dict[str, Any]:
    output_files = _output_files(evidence)
    command_model = build_command_model(
        command=evidence["command"],
        work_dir=Path(evidence["work_dir"]),
        before_files=evidence["before_files"],
        output_files=output_files,
        code_files=evidence["staged"]["code_files"],
    )
    return {
        "schema_version": "lightweight-extension/v1",
        "original_command": evidence["command"],
        "source_scripts": command_model["source_scripts"],
        "commands": command_model["commands"],
        "unmatched_outputs": command_model["unmatched_outputs"],
        "outputs": {
            "files": output_files,
            "stdout": dict(evidence["outputs"]["stdout"]),
        },
        "baseline": {
            "success": evidence["success"],
            "returncode": evidence["returncode"],
            "logs": dict(evidence["logs"]),
        },
    }


def write_spec(spec: dict[str, Any], destination: Path) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination


def write_wrapper(spec: dict[str, Any], destination: Path) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    quoted_command = shlex.quote(spec["original_command"])
    destination.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\nexec bash -c "
        + quoted_command
        + "\n",
        encoding="utf-8",
    )
    destination.chmod(0o755)
    return destination


def _baseline_input_paths(spec: dict[str, Any]) -> set[str]:
    return {
        item["path"]
        for command in spec["commands"]
        for item in command["inputs"]
        if item["source"] == "baseline_input"
    }


def replay(
    *,
    spec: dict[str, Any],
    raw_code: Path,
    raw_data: Path,
    run_dir: Path,
    input_overrides: dict[str, Path] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    overrides = input_overrides or {}
    unknown = sorted(set(overrides) - _baseline_input_paths(spec))
    if unknown:
        raise ValueError(f"Unknown input override: {', '.join(unknown)}")

    evidence = run_raw_baseline(
        command=spec["original_command"],
        raw_code=raw_code,
        raw_data=raw_data,
        run_dir=run_dir,
        env=env,
        input_overrides=overrides,
    )
    actual_files = set(evidence["outputs"]["new_files"]) | set(
        evidence["outputs"]["modified_files"]
    )
    expected_files = set(spec["outputs"]["files"])
    missing_outputs = sorted(expected_files - actual_files)
    expected_stdout = bool(spec["outputs"]["stdout"]["present"])
    stdout_ok = not expected_stdout or evidence["outputs"]["stdout"]["present"]
    evidence["missing_outputs"] = missing_outputs
    evidence["contract_passed"] = bool(
        evidence["success"] and not missing_outputs and stdout_ok
    )
    return evidence
