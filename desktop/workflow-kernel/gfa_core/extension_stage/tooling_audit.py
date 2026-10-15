"""Deterministic tooling and repair-audit helpers for extension stages."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "extension_tooling_audit_v1"

# 镜像标准形态: 默认候选为空; GF_<ENV>_BIN 环境变量为显式覆盖点。
ENV_BIN_CANDIDATES: dict[str, list[Path]] = {}

API_ASSIST_TOOL_PROBES: dict[str, list[str]] = {
    "shellcheck": ["--version"],
    "shfmt": ["--version"],
    "jq": ["--version"],
    "pytest": ["--version"],
}

GWAS_TOOL_PROBES: dict[str, list[str]] = {
    "bcftools": ["--version"],
    "tabix": ["--version"],
    "bgzip": ["--version"],
    "plink": ["--version"],
    "gemma": ["-h"],
    "run_pipeline.pl": ["-h"],
    "run_tassel_pipeline.pl": ["-h"],
}

IGNORED_SYSTEM_TOKENS = {
    "",
    "bash",
    "sh",
    "cd",
    "cp",
    "mv",
    "rm",
    "mkdir",
    "cat",
    "awk",
    "sed",
    "grep",
    "sort",
    "cut",
    "head",
    "tail",
    "echo",
    "printf",
    "test",
    "[",
    "true",
    "false",
    "python",
    "python3",
    "Rscript",
    "perl",
    "case",
    "esac",
    "do",
    "done",
    "else",
    "elif",
    "fi",
    "for",
    "function",
    "if",
    "local",
    "read",
    "return",
    "set",
    "shift",
    "then",
    "trap",
    "while",
    ";;",
    "*)",
}

CRITICAL_REPAIR_FIELDS = {
    "entrypoint.original_command",
    "input_bindings",
    "input_roles",
    "output_contract",
    "requirements",
    "runtime",
}


def env_bin_for(runtime_env: str) -> Path | None:
    """Return the bin directory for a runtime env when explicitly configured (GF_<ENV>_BIN).

    镜像标准形态下默认返回 None —— 每个容器只承载单一 conda 环境,
    容器 PATH 本身即该环境的 bin; 编排层按阶段选择对应镜像。
    """
    env_name = str(runtime_env or "").strip()
    candidates = list(ENV_BIN_CANDIDATES.get(env_name) or [])
    override = os.environ.get(f"GF_{env_name}_BIN") if env_name else None
    if override:
        candidates.append(Path(override).expanduser())
    for path in candidates:
        if path.exists():
            return path
    return None


def path_with_env_bin(runtime_env: str, env: dict[str, str] | None = None) -> str:
    """Build PATH with the requested conda env bin prepended when available."""
    base = (env or os.environ).get("PATH", "")
    env_bin = env_bin_for(runtime_env)
    if env_bin:
        return str(env_bin) + os.pathsep + base
    return base


def tool_capability_report(
    *,
    runtime_env: str = "",
    tools: dict[str, list[str]] | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 8,
) -> dict[str, Any]:
    """Probe tools without installing or repairing anything."""
    probes = tools if tools is not None else {**API_ASSIST_TOOL_PROBES, **GWAS_TOOL_PROBES}
    path_value = path_with_env_bin(runtime_env, env)
    rows = [
        probe_tool(name, args, path_value=path_value, timeout=timeout)
        for name, args in sorted(probes.items())
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now().isoformat(),
        "runtime_env": runtime_env,
        "env_bin": str(env_bin_for(runtime_env) or ""),
        "tool_count": len(rows),
        "available_tools": sorted(row["tool"] for row in rows if row.get("present")),
        "missing_tools": sorted(row["tool"] for row in rows if not row.get("present")),
        "tools": rows,
    }


def probe_tool(name: str, args: list[str] | None = None, *, path_value: str = "", timeout: int = 8) -> dict[str, Any]:
    command = normalize_system_command(name)
    path = shutil.which(command, path=path_value or None) if command else None
    row: dict[str, Any] = {
        "tool": name,
        "command": command,
        "present": bool(path),
        "path": path or "",
        "usable": False,
        "returncode": None,
        "version_or_probe": "",
        "stderr_tail": "",
    }
    if not path:
        return row
    probe_args = list(args or [])
    if not probe_args:
        row["usable"] = True
        return row
    try:
        proc = subprocess.run(
            [path, *probe_args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PATH": path_value or os.environ.get("PATH", "")},
        )
    except Exception as exc:
        row["stderr_tail"] = str(exc)[-800:]
        return row
    output = (proc.stdout or proc.stderr or "").strip()
    row.update({
        "usable": proc.returncode == 0 or bool(output),
        "returncode": proc.returncode,
        "version_or_probe": output.splitlines()[0] if output else "",
        "stderr_tail": (proc.stderr or "")[-800:],
    })
    return row


def system_requirement_report(extension: dict[str, Any], *, env: dict[str, str], cwd: Path) -> dict[str, Any]:
    """Check external command requirements separately from Python/R packages."""
    runtime = extension.get("runtime") or {}
    runtime_env = str(runtime.get("env") or "")
    requirements = extension.get("requirements") or {}
    commands = normalize_system_requirements(requirements.get("system") or [])
    path_value = env.get("PATH", "")
    rows = [probe_tool(command, [], path_value=path_value, timeout=5) for command in commands]
    missing = sorted(row["command"] for row in rows if not row.get("present"))
    return {
        "schema_version": "extension_system_requirements_v1",
        "created_at": datetime.now().isoformat(),
        "cwd": str(cwd),
        "runtime_env": runtime_env,
        "env_bin": str(env_bin_for(runtime_env) or ""),
        "required_commands": commands,
        "missing_commands": missing,
        "tools": rows,
    }


def dependency_error_message(report: dict[str, Any]) -> str:
    missing = report.get("missing_commands") or []
    if missing:
        return "System command dependency check failed: missing " + ", ".join(str(x) for x in missing)
    return ""


def normalize_system_requirements(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    commands: set[str] = set()
    for value in values or []:
        command = normalize_system_command(str(value or ""))
        if command and command not in IGNORED_SYSTEM_TOKENS:
            commands.add(command)
    return sorted(commands)


def normalize_system_command(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.split(r"\s*[<>=!]=?\s*", text, maxsplit=1)[0].strip()
    if not text:
        return ""
    token = re.split(r"\s+", text, maxsplit=1)[0]
    if token == ";;" or token.endswith(")"):
        return ""
    return Path(token).name if "/" in token else token


def repair_field_snapshot(spec_or_extension: dict[str, Any]) -> dict[str, Any]:
    entrypoint = spec_or_extension.get("entrypoint") if isinstance(spec_or_extension.get("entrypoint"), dict) else {}
    return {
        "entrypoint.original_command": str(spec_or_extension.get("original_command") or entrypoint.get("original_command") or ""),
        "entrypoint.command_template": str(spec_or_extension.get("command_template") or entrypoint.get("command_template") or ""),
        "entrypoint.semantic_command_template": str(spec_or_extension.get("semantic_command_template") or entrypoint.get("semantic_command_template") or ""),
        "entrypoint.wrapper_command_template": str(spec_or_extension.get("wrapper_command_template") or entrypoint.get("wrapper_command_template") or ""),
        "input_roles": spec_or_extension.get("input_roles") or {},
        "input_bindings": spec_or_extension.get("input_bindings") or {},
        "output_contract": spec_or_extension.get("output_contract") or [],
        "comparator_plan": spec_or_extension.get("comparator_plan") or {},
        "requirements": spec_or_extension.get("requirements") or {},
        "runtime": spec_or_extension.get("runtime") or {},
    }


def repair_field_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_snapshot = repair_field_snapshot(before)
    after_snapshot = repair_field_snapshot(after)
    changes = []
    for key in sorted(set(before_snapshot) | set(after_snapshot)):
        if before_snapshot.get(key) != after_snapshot.get(key):
            changes.append({"field": key, "before": before_snapshot.get(key), "after": after_snapshot.get(key)})
    changed = {item["field"] for item in changes}
    return {
        "schema_version": "extension_repair_field_diff_v1",
        "changed_fields": changes,
        "changed_field_count": len(changes),
        "critical_fields_changed": sorted(changed & CRITICAL_REPAIR_FIELDS),
        "critical_changed": bool(changed & CRITICAL_REPAIR_FIELDS),
        "before_hash": _stable_hash(before_snapshot),
        "after_hash": _stable_hash(after_snapshot),
    }


def repair_stall_summary(records: list[dict[str, Any]], *, threshold: int = 3) -> dict[str, Any]:
    """Detect repeated failures where critical repair fields did not change."""
    if len(records) < threshold:
        return {"stalled": False, "threshold": threshold, "repeat_count": len(records)}
    tail = records[-threshold:]
    signatures = [_error_signature(record) for record in tail]
    same_error = len(set(signatures)) == 1 and bool(signatures[0])
    no_critical_change = all(not _record_critical_changed(record) for record in tail)
    stalled = same_error and no_critical_change
    return {
        "schema_version": "extension_repair_stall_v1",
        "stalled": stalled,
        "threshold": threshold,
        "repeat_count": threshold,
        "error_signature": signatures[-1],
        "reason": "same_error_without_critical_field_change" if stalled else "",
    }


def _record_critical_changed(record: dict[str, Any]) -> bool:
    for key in ("field_diff", "contract_diff"):
        diff = record.get(key)
        if isinstance(diff, dict):
            if bool(diff.get("critical_changed")):
                return True
            fields = {
                str(item.get("field") or "")
                for item in diff.get("changed_fields") or []
                if isinstance(item, dict)
            }
            if fields & CRITICAL_REPAIR_FIELDS:
                return True
    return False


def _error_signature(record: dict[str, Any]) -> str:
    error = str(record.get("error") or "")
    validation = record.get("validation")
    if not error and isinstance(validation, dict):
        error = str(validation.get("error") or "")
    if not error:
        return ""
    normalized = error.lower()
    normalized = re.sub(r"(?:[a-z0-9_.-]+/)+[a-z0-9_.-]+", "<path>", normalized)
    normalized = re.sub(r"/[^\s:]+", "<path>", normalized)
    normalized = re.sub(r"\d+", "<n>", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized[:240]


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
