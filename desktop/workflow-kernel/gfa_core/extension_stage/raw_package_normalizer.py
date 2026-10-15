"""Normalize uploaded runnable code into one concrete extension entry command."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


SHELL_SUFFIXES = {".sh", ".bash", ".command"}
PYTHON_SUFFIXES = {".py"}
R_SUFFIXES = {".r"}


def normalize_raw_package(
    *,
    explicit_command: str,
    raw_code: Path,
    raw_data: Path,
    saved_code: list[Path],
) -> dict[str, Any]:
    """Return a deterministic entry command record for an uploaded raw package."""
    raw_code = Path(raw_code)
    raw_data = Path(raw_data)
    explicit_command = str(explicit_command or "").strip()
    scripts = _script_inventory(saved_code, raw_code)

    if explicit_command and not saved_code:
        run_sh = raw_code / "run.sh"
        run_sh.parent.mkdir(parents=True, exist_ok=True)
        run_sh.write_text("#!/usr/bin/env bash\nset -euo pipefail\n\n" + explicit_command + "\n", encoding="utf-8")
        run_sh.chmod(0o755)
        return _record(
            entry_command="bash run.sh",
            entry_source="inline_command_wrapped",
            entry_confidence="high",
            requires_user_confirmation=False,
            scripts=["run.sh"],
            warnings=[],
            raw_data=raw_data,
        )

    if explicit_command:
        return _record(
            entry_command=explicit_command,
            entry_source="user_command",
            entry_confidence="high",
            requires_user_confirmation=False,
            scripts=scripts,
            warnings=[],
            raw_data=raw_data,
        )

    shell_scripts = _scripts_by_suffix(saved_code, raw_code, SHELL_SUFFIXES)
    if len(shell_scripts) == 1:
        return _record(
            entry_command=f"bash {shell_scripts[0]}",
            entry_source="single_shell_script",
            entry_confidence="high",
            requires_user_confirmation=False,
            scripts=scripts,
            warnings=[],
            raw_data=raw_data,
        )
    if len(shell_scripts) > 1:
        ranked = _rank_shell_scripts(shell_scripts)
        ambiguous = len(ranked) > 1 and _shell_rank_key(ranked[0]) == _shell_rank_key(ranked[1])
        return _record(
            entry_command=f"bash {ranked[0]}",
            entry_source="ambiguous_shell_script" if ambiguous else "ranked_shell_script",
            entry_confidence="low" if ambiguous else "medium",
            requires_user_confirmation=ambiguous,
            scripts=scripts,
            warnings=["multiple shell entry candidates detected"] if ambiguous else [],
            raw_data=raw_data,
        )

    python_scripts = _scripts_by_suffix(saved_code, raw_code, PYTHON_SUFFIXES)
    if len(python_scripts) == 1:
        return _record(
            entry_command=f"python {python_scripts[0]}",
            entry_source="single_python_script",
            entry_confidence="medium",
            requires_user_confirmation=False,
            scripts=scripts,
            warnings=["python script parameters may require confirmation"],
            raw_data=raw_data,
        )

    r_scripts = _scripts_by_suffix(saved_code, raw_code, R_SUFFIXES)
    if len(r_scripts) == 1:
        return _record(
            entry_command=f"Rscript {r_scripts[0]}",
            entry_source="single_r_script",
            entry_confidence="medium",
            requires_user_confirmation=False,
            scripts=scripts,
            warnings=["R script parameters may require confirmation"],
            raw_data=raw_data,
        )

    if saved_code:
        first = sorted(saved_code, key=lambda p: p.relative_to(raw_code).as_posix().lower())[0]
        rel = first.relative_to(raw_code).as_posix()
        return _record(
            entry_command=rel,
            entry_source="fallback_code_file",
            entry_confidence="low",
            requires_user_confirmation=True,
            scripts=scripts,
            warnings=["could not identify a standard runnable entry script"],
            raw_data=raw_data,
        )

    return _record(
        entry_command="",
        entry_source="missing_entry",
        entry_confidence="none",
        requires_user_confirmation=True,
        scripts=[],
        warnings=["no command or code file was provided"],
        raw_data=raw_data,
    )


def _record(
    *,
    entry_command: str,
    entry_source: str,
    entry_confidence: str,
    requires_user_confirmation: bool,
    scripts: list[str],
    warnings: list[str],
    raw_data: Path,
) -> dict[str, Any]:
    return {
        "schema_version": "raw_package_normalized_entry_v1",
        "entry_command": entry_command,
        "entry_source": entry_source,
        "entry_confidence": entry_confidence,
        "requires_user_confirmation": bool(requires_user_confirmation),
        "detected_scripts": sorted(scripts),
        "raw_test_data_files": sorted(p.relative_to(raw_data).as_posix() for p in raw_data.rglob("*") if p.is_file()),
        "normalization_warnings": warnings,
    }


def _script_inventory(paths: list[Path], raw_code: Path) -> list[str]:
    return [
        path.relative_to(raw_code).as_posix()
        for path in paths
        if path.is_file() and path.suffix.lower() in SHELL_SUFFIXES | PYTHON_SUFFIXES | R_SUFFIXES
    ]


def _scripts_by_suffix(paths: list[Path], raw_code: Path, suffixes: set[str]) -> list[str]:
    return sorted(
        path.relative_to(raw_code).as_posix()
        for path in paths
        if path.is_file() and path.suffix.lower() in suffixes
    )


def _rank_shell_scripts(scripts: list[str]) -> list[str]:
    return sorted(scripts, key=lambda name: (_shell_rank_key(name), name.lower()))


def _shell_rank_key(name: str) -> int:
    base = Path(name).name.lower()
    stem = re.sub(r"\.(sh|bash|command)$", "", base)
    if stem in {"run", "main"}:
        return 0
    if stem.startswith("run_") or stem.startswith("run-") or stem.startswith("run"):
        return 1
    if stem.endswith("_run") or stem.endswith("-run"):
        return 2
    if stem.startswith("main_") or stem.startswith("main-") or stem.startswith("main"):
        return 3
    if "install" in stem or "clean" in stem:
        return 9
    return 5

