"""Golden behavior-baseline runner for extension-stage conversions."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from gfa_core.extension_stage.comparators import compare_output_dirs, file_manifest
from gfa_core.extension_stage.executor import DEFAULT_ENV_BIN, execute_extension_command, render_command_template, tail
from gfa_core.extension_stage.provenance import inventory_files


class GoldenValidationError(RuntimeError):
    """Raised when a golden validation run cannot be prepared."""


def run_golden_validation(
    extension: dict[str, Any],
    *,
    extension_root: Path,
    converted_code_dir: Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run original behavior and converted wrapper, then compare outputs."""

    extension_root = Path(extension_root)
    paths = extension.get("paths") or {}
    raw_code = extension_root / str(paths.get("raw_code") or "raw_code")
    raw_data = extension_root / str(paths.get("raw_test_data") or "raw_test_data")
    converted = Path(converted_code_dir) if converted_code_dir else extension_root / str(paths.get("converted_code") or "converted_code")
    run_id = run_id or f"golden_{_timestamp()}"
    golden_root = extension_root / "golden" / run_id
    original_run_dir = golden_root / "original_run"
    semantic_run_dir = golden_root / "semantic_run"
    converted_run_dir = golden_root / "converted_run"
    for path in (original_run_dir, converted_run_dir):
        path.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "schema_version": "extension_golden_validation_v1",
        "status": "failed",
        "run_id": run_id,
        "workspace": str(golden_root),
        "behavior_baseline": {},
        "semantic_validation": {},
        "semantic_comparison": {},
        "converted_validation": {},
        "comparison": {},
        "semantic_vs_converted_comparison": {},
        "started_at": datetime.now().isoformat(),
    }
    try:
        baseline = _run_original_behavior(extension, raw_code=raw_code, raw_data=raw_data, run_dir=original_run_dir)
        report["behavior_baseline"] = baseline
        if baseline.get("status") != "success":
            report["error"] = baseline.get("error", "original behavior baseline failed")
            _finish(report, golden_root)
            return report
        semantic_template = _semantic_template(extension)
        if semantic_template:
            semantic_run = _run_semantic_template_behavior(
                extension,
                raw_code=raw_code,
                raw_data=raw_data,
                run_dir=semantic_run_dir,
                template=semantic_template,
            )
            report["semantic_validation"] = semantic_run
            if semantic_run.get("status") != "success":
                report["error"] = semantic_run.get("error", "semantic command template failed")
                _finish(report, golden_root)
                return report
            semantic_comparison = compare_output_dirs(
                original_run_dir / "outputs",
                semantic_run_dir / "outputs",
                output_contract=extension.get("output_contract") or [],
                comparator_plan=extension.get("comparator_plan") or {},
            )
            report["semantic_comparison"] = semantic_comparison
            if semantic_comparison.get("status") != "passed":
                report["error"] = _comparison_error(semantic_comparison).replace("golden comparison", "semantic template comparison")
                _finish(report, golden_root)
                return report
        converted_run = execute_extension_command(
            extension,
            run_dir=converted_run_dir,
            input_dir=raw_data,
            converted_code_dir=converted,
            parameters=_default_parameters(extension),
        )
        report["converted_validation"] = {
            "status": converted_run.get("status"),
            "command": converted_run.get("command"),
            "returncode": converted_run.get("returncode"),
            "runtime": converted_run.get("runtime"),
            "logs": {
                "stdout": "converted_run/logs/stdout.log",
                "stderr": "converted_run/logs/stderr.log",
            },
            "error": converted_run.get("error", ""),
        }
        if converted_run.get("status") != "success":
            report["error"] = converted_run.get("error", "converted wrapper failed")
            _finish(report, golden_root)
            return report
        comparison = compare_output_dirs(
            original_run_dir / "outputs",
            converted_run_dir / "outputs",
            output_contract=extension.get("output_contract") or [],
            comparator_plan=extension.get("comparator_plan") or {},
        )
        report["comparison"] = comparison
        if semantic_template:
            report["semantic_vs_converted_comparison"] = compare_output_dirs(
                semantic_run_dir / "outputs",
                converted_run_dir / "outputs",
                output_contract=extension.get("output_contract") or [],
                comparator_plan=extension.get("comparator_plan") or {},
            )
        report["status"] = "passed" if comparison.get("status") == "passed" else "failed"
        if report["status"] != "passed":
            report["error"] = _comparison_error(comparison)
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
    _finish(report, golden_root)
    return report


def _run_original_behavior(extension: dict[str, Any], *, raw_code: Path, raw_data: Path, run_dir: Path) -> dict[str, Any]:
    command = str((extension.get("entrypoint") or {}).get("original_command") or "").strip()
    if not command:
        return {"status": "failed", "error": "original_command is missing"}
    if "<" in command and ">" in command and any(token in command for token in ("<input", "<output")):
        return {"status": "failed", "error": "original_command contains abstract placeholders and is not directly runnable"}
    work_dir = run_dir / "work"
    output_dir = run_dir / "outputs"
    log_dir = run_dir / "logs"
    for path in (work_dir, output_dir, log_dir):
        path.mkdir(parents=True, exist_ok=True)
    _copy_tree_contents(raw_code, work_dir)
    _copy_tree_contents(raw_data, work_dir)
    _copy_tree_contents(raw_data, work_dir / "raw_test_data")
    before = _snapshot(work_dir)
    runtime = extension.get("runtime") or {}
    env = os.environ.copy()
    env_bin = DEFAULT_ENV_BIN.get(str(runtime.get("env") or "test4py311"))
    if env_bin and env_bin.exists():
        env["PATH"] = str(env_bin) + os.pathsep + env.get("PATH", "")
    stdout_path = log_dir / "stdout.log"
    stderr_path = log_dir / "stderr.log"
    started = time.time()
    try:
        with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open("w", encoding="utf-8") as err:
            proc = subprocess.run(
                ["bash", "-o", "pipefail", "-c", command],
                cwd=work_dir,
                shell=False,
                text=True,
                stdout=out,
                stderr=err,
                env=env,
                timeout=int(extension.get("timeout_sec") or 3600),
            )
        status = "success" if proc.returncode == 0 else "failed"
        error = "" if proc.returncode == 0 else tail(stderr_path)
        returncode = proc.returncode
    except subprocess.TimeoutExpired:
        status = "timeout"
        error = "Original behavior baseline timed out."
        returncode = None
    except Exception as exc:
        status = "failed"
        error = str(exc)
        returncode = None
    changed = _changed_files(work_dir, before)
    for path in changed:
        target = output_dir / path.relative_to(work_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    if status == "success" and not changed:
        status = "failed"
        error = "Original behavior produced no detectable output files."
    return {
        "status": status,
        "command": command,
        "returncode": returncode,
        "error": error,
        "elapsed_sec": round(time.time() - started, 3),
        "input_manifest": inventory_files(raw_data, relative_to=run_dir),
        "raw_code_manifest": inventory_files(raw_code, relative_to=run_dir),
        "outputs": file_manifest(output_dir),
        "logs": {"stdout": "original_run/logs/stdout.log", "stderr": "original_run/logs/stderr.log"},
    }


def _semantic_template(extension: dict[str, Any]) -> str:
    entrypoint = extension.get("entrypoint") or {}
    return str(extension.get("semantic_command_template") or entrypoint.get("semantic_command_template") or "").strip()


def _run_semantic_template_behavior(
    extension: dict[str, Any],
    *,
    raw_code: Path,
    raw_data: Path,
    run_dir: Path,
    template: str,
) -> dict[str, Any]:
    work_dir = run_dir / "work"
    output_dir = run_dir / "outputs"
    log_dir = run_dir / "logs"
    for path in (work_dir, output_dir, log_dir):
        path.mkdir(parents=True, exist_ok=True)
    _copy_tree_contents(raw_code, work_dir)
    _copy_tree_contents(raw_data, work_dir)
    _copy_tree_contents(raw_data, work_dir / "raw_test_data")
    input_files = sorted(p for p in raw_data.rglob("*") if p.is_file()) if raw_data.exists() else []
    started = time.time()
    try:
        command = render_command_template(
            extension,
            template=template,
            run_dir=run_dir,
            input_dir=raw_data,
            input_files=input_files,
            output_dir=output_dir,
            code_dir=work_dir,
            parameters=_default_parameters(extension),
        )
    except Exception as exc:
        return {
            "status": "failed",
            "command": template,
            "returncode": None,
            "error": "semantic command template render failed: " + str(exc),
            "elapsed_sec": round(time.time() - started, 3),
            "outputs": file_manifest(output_dir),
            "logs": {"stdout": "semantic_run/logs/stdout.log", "stderr": "semantic_run/logs/stderr.log"},
        }
    runtime = extension.get("runtime") or {}
    env = os.environ.copy()
    env_bin = DEFAULT_ENV_BIN.get(str(runtime.get("env") or "test4py311"))
    if env_bin and env_bin.exists():
        env["PATH"] = str(env_bin) + os.pathsep + env.get("PATH", "")
    stdout_path = log_dir / "stdout.log"
    stderr_path = log_dir / "stderr.log"
    try:
        with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open("w", encoding="utf-8") as err:
            proc = subprocess.run(
                ["bash", "-o", "pipefail", "-c", command],
                cwd=work_dir,
                shell=False,
                text=True,
                stdout=out,
                stderr=err,
                env=env,
                timeout=int(extension.get("timeout_sec") or 3600),
            )
        status = "success" if proc.returncode == 0 else "failed"
        error = "" if proc.returncode == 0 else tail(stderr_path)
        returncode = proc.returncode
    except subprocess.TimeoutExpired:
        status = "timeout"
        error = "Semantic command template timed out."
        returncode = None
    except Exception as exc:
        status = "failed"
        error = str(exc)
        returncode = None
    outputs = file_manifest(output_dir)
    if status == "success" and not outputs:
        status = "failed"
        error = "Semantic command template produced no output files under {output_dir}."
    return {
        "status": status,
        "command": command,
        "returncode": returncode,
        "error": error,
        "elapsed_sec": round(time.time() - started, 3),
        "input_manifest": inventory_files(raw_data, relative_to=run_dir),
        "raw_code_manifest": inventory_files(raw_code, relative_to=run_dir),
        "outputs": outputs,
        "logs": {"stdout": "semantic_run/logs/stdout.log", "stderr": "semantic_run/logs/stderr.log"},
    }


def _default_parameters(extension: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, spec in (extension.get("confirmed_parameters") or extension.get("parameters") or {}).items():
        if isinstance(spec, dict):
            values[name] = spec.get("default", "")
        else:
            values[name] = spec
    return values


def _snapshot(root: Path) -> dict[str, tuple[int, str]]:
    rows: dict[str, tuple[int, str]] = {}
    if not root.exists():
        return rows
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rows[path.relative_to(root).as_posix()] = (path.stat().st_size, _mtime_token(path))
    return rows


def _changed_files(root: Path, before: dict[str, tuple[int, str]]) -> list[Path]:
    changed = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        current = (path.stat().st_size, _mtime_token(path))
        if rel not in before or before[rel] != current:
            changed.append(path)
    return changed


def _mtime_token(path: Path) -> str:
    return f"{path.stat().st_mtime_ns}"


def _comparison_error(comparison: dict[str, Any]) -> str:
    failures = comparison.get("failures") or []
    if not failures:
        return "golden comparison failed"
    first = failures[0]
    reason = first.get("reason") or first.get("status") or "output mismatch"
    original = first.get("original") or ""
    converted = first.get("converted") or ""
    if original or converted:
        return f"golden comparison failed: {reason}; original={original}; converted={converted}"
    return "golden comparison failed: " + str(reason)


def _finish(report: dict[str, Any], golden_root: Path) -> None:
    report["ended_at"] = datetime.now().isoformat()
    golden_root.mkdir(parents=True, exist_ok=True)
    (golden_root / "golden_comparison.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _copy_tree_contents(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        return
    for item in source.iterdir():
        dest = target / item.name
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest)
        elif item.is_file():
            shutil.copy2(item, dest)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")
