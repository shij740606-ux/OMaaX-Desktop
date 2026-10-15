"""Command rendering and process execution for extension stages."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from gfa_core.extension_stage.spec_v2 import infer_analysis_type, semantic_placeholders
from gfa_core.extension_stage.tooling_audit import (
    dependency_error_message,
    env_bin_for,
    system_requirement_report,
)

# 镜像标准形态: 每镜像=单一环境, 运行时容器 PATH 即该环境 bin 目录。
# 默认值一律为空(由编排层按阶段选镜像/挂 env); 保留 GF_<ENV>_BIN 显式覆盖点。
DEFAULT_ENV_BIN: dict[str, Path] = {}


class ExtensionExecutionError(RuntimeError):
    """Raised when command rendering cannot proceed."""


def render_command(
    extension: dict[str, Any],
    *,
    run_dir: Path,
    input_dir: Path,
    input_files: list[Path],
    parameters: dict[str, Any] | None = None,
    output_dir: Path | None = None,
    code_dir: Path | None = None,
) -> str:
    template = str((extension.get("entrypoint") or {}).get("command_template") or "")
    return render_command_template(
        extension,
        template=template,
        run_dir=run_dir,
        input_dir=input_dir,
        input_files=input_files,
        parameters=parameters,
        output_dir=output_dir,
        code_dir=code_dir,
    )


def render_command_template(
    extension: dict[str, Any],
    *,
    template: str,
    run_dir: Path,
    input_dir: Path,
    input_files: list[Path],
    parameters: dict[str, Any] | None = None,
    output_dir: Path | None = None,
    code_dir: Path | None = None,
) -> str:
    output_dir = output_dir or (run_dir / "outputs")
    code_dir = code_dir or (run_dir / "code")
    render_context = build_render_context(
        extension,
        run_dir=run_dir,
        input_dir=input_dir,
        input_files=input_files,
        parameters=parameters,
        output_dir=output_dir,
        code_dir=code_dir,
        template=template,
    )
    try:
        return template.format(**render_context)
    except KeyError as exc:
        missing = str(exc).strip("'\"")
        raise ExtensionExecutionError(f"Missing command template value: {missing}") from exc


def build_render_context(
    extension: dict[str, Any],
    *,
    run_dir: Path,
    input_dir: Path,
    input_files: list[Path],
    parameters: dict[str, Any] | None = None,
    output_dir: Path | None = None,
    code_dir: Path | None = None,
    template: str = "",
) -> dict[str, Any]:
    output_dir = output_dir or (run_dir / "outputs")
    code_dir = code_dir or (run_dir / "code")
    id_suffixes = {".id", ".ids", ".geneid", ".geneids", ".txt", ".tsv", ".csv"}
    fasta_suffixes = {".fa", ".fasta", ".faa", ".fsa", ".pep"}
    input_roles = extension.get("input_roles") if isinstance(extension.get("input_roles"), dict) else {}
    input_file_role = str((input_roles.get("input_file") or {}).get("role") or "").lower()
    id_file = _first_input_by_suffix(input_files, id_suffixes)
    annotation_file = _first_input_by_suffix(input_files, {".gff", ".gff3"}) if ("annotation_gff" in input_file_role or "gff" in input_file_role) else ""
    cis_element_file = _first_input_by_name(input_files, {"plantcare", "cis_element", "cis-elements", "cis.elements"}) if input_file_role == "generic_input_file" else ""
    input_file = annotation_file or cis_element_file or id_file or (input_files[0].as_posix() if input_files else input_dir.as_posix())
    input_hmm = _first_input_by_suffix(input_files, {".hmm"}) or input_file
    fasta_paths = _input_files_by_suffix(input_files, fasta_suffixes)
    input_fasta = (fasta_paths[0].as_posix() if fasta_paths else input_file)
    query_fasta = _largest_file(fasta_paths) or input_fasta
    reference_fasta = _smallest_file([path for path in fasta_paths if path.as_posix() != query_fasta]) or input_fasta
    pep_fasta = _first_input_by_name(fasta_paths, {"pep", "protein", "faa"}) or query_fasta
    cds_fasta = _first_input_by_name(fasta_paths, {"cds"}) or input_fasta
    render_context: dict[str, Any] = {
        "code_dir": code_dir.as_posix(),
        "input_dir": input_dir.as_posix(),
        "input_file": input_file,
        "input_hmm": input_hmm,
        "input_fasta": input_fasta,
        "input_query_fasta": query_fasta,
        "input_reference_fasta": reference_fasta,
        "input_pep_fasta": pep_fasta,
        "input_cds_fasta": cds_fasta,
        "input_id_file": id_file or input_file,
        "output_dir": output_dir.as_posix(),
        "work_dir": run_dir.as_posix(),
    }
    bindings = {}
    if isinstance(parameters, dict):
        bindings = parameters.get("binding_report") or parameters.get("config_input_bindings") or {}
    placeholders = bindings.get("placeholder_to_file") or bindings.get("placeholders") if isinstance(bindings, dict) else {}
    if isinstance(placeholders, dict):
        for key in ("input_file", "input_hmm", "input_fasta", "input_query_fasta", "input_reference_fasta", "input_pep_fasta", "input_cds_fasta", "input_id_file"):
            value = str(placeholders.get(key) or "").strip()
            if value:
                render_context[key] = value
    if isinstance(parameters, dict) and parameters.get("strict_semantic_binding"):
        missing = sorted(ph for ph in semantic_placeholders(template) if not render_context.get(ph) or not Path(str(render_context.get(ph))).exists())
        if missing:
            raise ExtensionExecutionError("Missing semantic input binding(s): " + ", ".join(missing))
        if infer_analysis_type(extension) != "generic_cli":
            for ph in semantic_placeholders(template):
                bound = Path(str(render_context.get(ph)))
                if not bound.exists() or not bound.is_file() or bound.stat().st_size == 0:
                    raise ExtensionExecutionError(f"Semantic input binding is invalid or empty: {ph}={bound}")
    for name, value in (parameters or {}).items():
        render_context[f"param_{_safe_parameter_name(name)}"] = _shell_parameter_value(value)
    return render_context


def execute_extension_command(
    extension: dict[str, Any],
    *,
    run_dir: Path,
    input_dir: Path,
    converted_code_dir: Path,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    started = time.time()
    run_dir.mkdir(parents=True, exist_ok=True)
    code_dir = run_dir / "code"
    output_dir = run_dir / "outputs"
    log_dir = run_dir / "logs"
    for path in (code_dir, output_dir, log_dir):
        path.mkdir(parents=True, exist_ok=True)
    _copy_tree_contents(converted_code_dir, code_dir)

    input_files = sorted(p for p in input_dir.rglob("*") if p.is_file())
    command = render_command(
        extension,
        run_dir=run_dir,
        input_dir=input_dir,
        input_files=input_files,
        output_dir=output_dir,
        code_dir=code_dir,
        parameters=parameters,
    )
    runtime = extension.get("runtime") or {}
    env_name = str(runtime.get("env") or "test4py311")
    env = os.environ.copy()
    env_bin = DEFAULT_ENV_BIN.get(env_name)
    if not env_bin or not env_bin.exists():
        env_bin = env_bin_for(env_name)
    if env_bin and env_bin.exists():
        env["PATH"] = str(env_bin) + os.pathsep + env.get("PATH", "")

    stdout_path = log_dir / "stdout.log"
    stderr_path = log_dir / "stderr.log"
    status = "failed"
    error = ""
    returncode = None
    tool_diagnostics = system_requirement_report(extension, env=env, cwd=run_dir)
    requirement_error = dependency_error_message(tool_diagnostics) or runtime_requirement_error(extension, env, run_dir)
    if requirement_error:
        status = "environment_failed"
        error = requirement_error
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text(requirement_error + "\n", encoding="utf-8")
    else:
        try:
            with stdout_path.open("w", encoding="utf-8") as out, stderr_path.open("w", encoding="utf-8") as err:
                proc = subprocess.run(
                    command,
                    cwd=run_dir,
                    shell=True,
                    text=True,
                    stdout=out,
                    stderr=err,
                    env=env,
                    timeout=int(extension.get("timeout_sec") or 3600),
                )
            returncode = proc.returncode
            status = "success" if proc.returncode == 0 else "failed"
            if proc.returncode != 0:
                error = tail(stderr_path)
        except subprocess.TimeoutExpired:
            status = "timeout"
            error = "Extension analysis timed out."
        except Exception as exc:
            status = "failed"
            error = str(exc)

    return {
        "status": status,
        "command": command,
        "returncode": returncode,
        "runtime": runtime,
        "tool_diagnostics": tool_diagnostics,
        "logs": {
            "stdout_path": stdout_path,
            "stderr_path": stderr_path,
        },
        "error": error,
        "started_at": datetime.fromtimestamp(started).isoformat(),
        "ended_at": datetime.now().isoformat(),
        "elapsed_sec": round(time.time() - started, 3),
    }


def runtime_requirement_error(extension: dict[str, Any], env: dict[str, str], cwd: Path) -> str:
    requirements = extension.get("requirements") or {}
    runtime = extension.get("runtime") or {}
    language = str(runtime.get("language") or "").lower()
    py_packages = [pkg for pkg in requirements.get("python", []) if pkg]
    r_packages = [pkg for pkg in requirements.get("r", []) if pkg]
    if language == "python" and py_packages:
        code = ";".join(f"import {pkg}" for pkg in py_packages)
        proc = subprocess.run(["python", "-c", code], cwd=cwd, env=env, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return "Python dependency check failed: " + (proc.stderr or proc.stdout or ", ".join(py_packages)).strip()
    if language == "r" and r_packages:
        expr = ";".join(f"library({pkg})" for pkg in r_packages)
        proc = subprocess.run(["Rscript", "-e", expr], cwd=cwd, env=env, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return "R dependency check failed: " + (proc.stderr or proc.stdout or ", ".join(r_packages)).strip()
    return ""


def tail(path: Path, limit: int = 4000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    return text[-limit:]


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


def _first_input_by_suffix(paths: list[Path], suffixes: set[str]) -> str:
    for path in paths:
        name = path.name.lower()
        if path.suffix.lower() in suffixes or any(name.endswith(suffix) for suffix in suffixes):
            return path.as_posix()
    return ""


def _input_files_by_suffix(paths: list[Path], suffixes: set[str]) -> list[Path]:
    matched = []
    for path in paths:
        name = path.name.lower()
        if path.suffix.lower() in suffixes or any(name.endswith(suffix) for suffix in suffixes):
            matched.append(path)
    return matched


def _first_input_by_name(paths: list[Path], tokens: set[str]) -> str:
    for path in paths:
        name = path.name.lower()
        if any(token in name for token in tokens):
            return path.as_posix()
    return ""


def _largest_file(paths: list[Path]) -> str:
    if not paths:
        return ""
    return max(paths, key=lambda path: path.stat().st_size if path.exists() else -1).as_posix()


def _smallest_file(paths: list[Path]) -> str:
    if not paths:
        return ""
    return min(paths, key=lambda path: path.stat().st_size if path.exists() else 10**18).as_posix()


def _shell_parameter_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9_./:+,@%-]+", text):
        return text
    return shlex.quote(text)


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
