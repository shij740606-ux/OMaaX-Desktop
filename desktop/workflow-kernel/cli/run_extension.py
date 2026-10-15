#!/usr/bin/env python3
"""run_extension.py —— 扩展分析容器通用入口(宿主挂载 /opt/bioflow-entry,镜像零改动)。

执行形态(契约 §3):
    python -B /opt/bioflow-entry/run_extension.py --ext-dir /job/ext \
        --input-dir /job/input --output-dir /job/output [--param name=value ...]

职责:
1. 读 <ext-dir>/extension.json(command_template 唯一事实源,契约 §2 schema)。
2. render_command_template:移植 175 executor 模板渲染纯函数(白名单占位符
   12 个 + {param_*};{output_dir} 必含)。
3. subprocess 执行(无 shell:shlex 切分;stdout/stderr 透传;超时进程组
   SIGTERM → 3s → SIGKILL,与 176 服务侧 terminate→kill 约定同源)。
4. 输出契约校验:输出目录非空,否则退出码 3。

退出码约定(容器内零第三方依赖,仅 python 标准库):
  0    成功
  1..  命令自身退出码透传(1/2/7/137…)
  2    extension.json 缺失/非法、模板渲染失败、白名单外占位符、缺 {output_dir}
  3    输出契约校验失败(输出目录为空)
  124  执行超时(进程组已终止)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_SETUP = 2
EXIT_OUTPUT_CONTRACT = 3
EXIT_TIMEOUT = 124

DEFAULT_TIMEOUT_SEC = 36000.0  # 与 176 单阶段 10h 超时对齐

# 占位符白名单(spec_v2 PLACEHOLDERS 移植;扩展 {param_<name>})
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

_ID_SUFFIXES = {".id", ".ids", ".geneid", ".geneids", ".txt", ".tsv", ".csv"}
_FASTA_SUFFIXES = {".fa", ".fasta", ".faa", ".fsa", ".pep"}
_GFF_SUFFIXES = {".gff", ".gff3"}

_ACTIVE_PROC: subprocess.Popen | None = None


class ExtensionRenderError(RuntimeError):
    """模板渲染失败(占位符缺失/参数缺值)。"""


# ---------------------------------------------------------------------------
# 模板渲染纯函数(executor.py 移植裁剪;不 import gfa_core,零第三方依赖)
# ---------------------------------------------------------------------------

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


def _shell_parameter_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    text = str(value)
    if re.fullmatch(r"[A-Za-z0-9_./:+,@%-]+", text):
        return text
    return shlex.quote(text)


def _first_input_by_suffix(paths: list[Path], suffixes: set[str]) -> str:
    for path in paths:
        name = path.name.lower()
        if path.suffix.lower() in suffixes or any(name.endswith(s) for s in suffixes):
            return path.as_posix()
    return ""


def _input_files_by_suffix(paths: list[Path], suffixes: set[str]) -> list[Path]:
    matched = []
    for path in paths:
        name = path.name.lower()
        if path.suffix.lower() in suffixes or any(name.endswith(s) for s in suffixes):
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
    return max(paths, key=lambda p: p.stat().st_size if p.exists() else -1).as_posix()


def _smallest_file(paths: list[Path]) -> str:
    if not paths:
        return ""
    return min(paths, key=lambda p: p.stat().st_size if p.exists() else 10**18).as_posix()


def _role_of(extension: dict[str, Any], placeholder: str) -> str:
    """input_roles 双形态兼容:契约 §2 平铺 {"input_file": "fastq"},175 旧版
    {"input_file": {"role": "generic_input_file"}}。仅用于语义占位符消歧。"""
    roles = extension.get("input_roles")
    if not isinstance(roles, dict):
        return ""
    spec = roles.get(placeholder)
    if isinstance(spec, dict):
        return str(spec.get("role") or "").lower()
    return str(spec or "").lower()


def _used_placeholders(template: str) -> set[str]:
    return set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template or ""))


def _validate_template(template: str) -> str | None:
    """模板白名单校验(与 spec_v2 normalize 同纪律);None = 通过。"""
    if not template.strip():
        return "extension.json 缺少 command_template"
    if "{output_dir}" not in template:
        return "command_template 必须包含 {output_dir}(输出契约硬约束)"
    bad = sorted(
        name for name in _used_placeholders(template)
        if name not in PLACEHOLDERS and not name.startswith("param_")
    )
    if bad:
        return "command_template 含白名单外占位符: " + ", ".join(bad)
    return None


def build_render_context(
    extension: dict[str, Any],
    *,
    run_dir: Path,
    input_dir: Path,
    input_files: list[Path],
    parameters: dict[str, Any] | None = None,
    output_dir: Path | None = None,
    code_dir: Path | None = None,
) -> dict[str, Any]:
    """渲染上下文:12 个白名单占位符(语义占位符按输入文件后缀/角色推导)+ param_*。"""
    output_dir = output_dir or (run_dir / "outputs")
    code_dir = code_dir or (run_dir / "code")
    input_file_role = _role_of(extension, "input_file")
    id_file = _first_input_by_suffix(input_files, _ID_SUFFIXES)
    annotation_file = (
        _first_input_by_suffix(input_files, _GFF_SUFFIXES)
        if ("annotation_gff" in input_file_role or "gff" in input_file_role)
        else ""
    )
    cis_element_file = (
        _first_input_by_name(input_files, {"plantcare", "cis_element", "cis-elements", "cis.elements"})
        if input_file_role == "generic_input_file"
        else ""
    )
    input_file = (
        annotation_file or cis_element_file or id_file
        or (input_files[0].as_posix() if input_files else input_dir.as_posix())
    )
    input_hmm = _first_input_by_suffix(input_files, {".hmm"}) or input_file
    fasta_paths = _input_files_by_suffix(input_files, _FASTA_SUFFIXES)
    input_fasta = fasta_paths[0].as_posix() if fasta_paths else input_file
    query_fasta = _largest_file(fasta_paths) or input_fasta
    reference_fasta = (
        _smallest_file([p for p in fasta_paths if p.as_posix() != query_fasta])
        if len(fasta_paths) > 1 else input_fasta
    )
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
    for name, value in (parameters or {}).items():
        render_context["param_" + _safe_parameter_name(name)] = _shell_parameter_value(value)
    return render_context


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
    """模板渲染纯函数(executor.py 同签名移植)。占位符缺值 → ExtensionRenderError。"""
    context = build_render_context(
        extension,
        run_dir=run_dir,
        input_dir=input_dir,
        input_files=input_files,
        parameters=parameters,
        output_dir=output_dir,
        code_dir=code_dir,
    )
    try:
        return template.format(**context)
    except KeyError as exc:
        missing = str(exc).strip("'\"")
        raise ExtensionRenderError(f"Missing command template value: {missing}") from exc


# ---------------------------------------------------------------------------
# 执行与输出契约
# ---------------------------------------------------------------------------

def _output_contract_ok(output_dir: Path) -> bool:
    """输出契约:输出目录存在且非空(至少一个条目)。"""
    return output_dir.is_dir() and any(output_dir.iterdir())


def _find_code_dir(ext_dir: Path) -> Path | None:
    """代码目录:converted_code 优先(契约 §1 布局),raw_code 兜底。"""
    for name in ("converted_code", "raw_code"):
        candidate = ext_dir / name
        if candidate.is_dir():
            return candidate
    return None


def _copy_code_tree(src: Path, dst: Path) -> set[str]:
    """把代码树复制进可写目录;返回原始代码相对路径集合(收割时排除)。"""
    import shutil
    rels: set[str] = set()
    if not src.is_dir():
        return rels
    for item in src.rglob("*"):
        rel = item.relative_to(src).as_posix()
        dest = dst / rel
        if item.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
            rels.add(rel)
    return rels


def _harvest_outputs(src: Path, dst: Path, exclude: set[str]) -> None:
    """把 src 内产物(排除原始代码路径)合并进 dst,保留相对路径。"""
    import shutil
    for item in src.rglob("*"):
        rel = item.relative_to(src).as_posix()
        if rel in exclude:
            continue
        # 排除路径是原始代码文件所在目录时也跳过其祖先目录本身
        if any(rel == e or rel.startswith(e + "/") for e in exclude):
            continue
        dest = dst / rel
        if item.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)


def _kill_group(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _sigterm_handler(signum: int, frame: Any) -> None:
    """docker stop 的 SIGTERM:转发到子进程组后以 124 退出(日志可辨)。"""
    proc = _ACTIVE_PROC
    if proc is not None and proc.poll() is None:
        _kill_group(proc.pid, signal.SIGTERM)
    sys.exit(EXIT_TIMEOUT)


def _run_command(command: list[str], cwd: Path, env: dict[str, str], timeout_sec: float) -> int:
    """执行命令(stdout/stderr 直接透传容器流;超时进程组 SIGTERM→3s→SIGKILL)。

    可执行文件缺失(镜像缺工具)→ 可读退出码 2(不抛 traceback)。"""
    global _ACTIVE_PROC
    try:
        proc = subprocess.Popen(command, cwd=str(cwd), env=env, start_new_session=True)
    except FileNotFoundError:
        print(f"[run_extension] 可执行文件缺失(镜像 PATH 中不存在): {command[0]}",
              file=sys.stderr)
        return EXIT_SETUP
    _ACTIVE_PROC = proc
    try:
        return proc.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _kill_group(proc.pid, signal.SIGKILL)
            proc.wait()
        return EXIT_TIMEOUT
    finally:
        _ACTIVE_PROC = None


def _parse_params(items: list[str]) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep or not name.strip():
            raise ExtensionRenderError(f"invalid --param (需要 name=value): {item}")
        parameters[name.strip()] = value
    return parameters


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_extension",
        description="扩展分析容器通用入口(契约 EXTENSION_CONTRACT §3)",
    )
    parser.add_argument("--ext-dir", default="/job/ext", help="扩展目录(挂载 :ro)")
    parser.add_argument("--input-dir", default="/job/input", help="输入目录")
    parser.add_argument("--output-dir", default="/job/output", help="输出目录(契约校验对象)")
    parser.add_argument("--work-dir", default="", help="工作目录(默认=输出目录)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SEC, help="执行超时(秒)")
    parser.add_argument("--param", action="append", default=[], help="参数 name=value(可重复)")
    args = parser.parse_args(argv)

    try:
        signal.signal(signal.SIGTERM, _sigterm_handler)
    except ValueError:  # 非主线程(测试)不支持 handler
        pass

    ext_dir = Path(args.ext_dir)
    ext_json_path = ext_dir / "extension.json"
    if not ext_json_path.is_file():
        print(f"[run_extension] extension.json 缺失: {ext_json_path}", file=sys.stderr)
        return EXIT_SETUP
    try:
        extension = json.loads(ext_json_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"[run_extension] extension.json 解析失败: {exc}", file=sys.stderr)
        return EXIT_SETUP
    if not isinstance(extension, dict):
        print("[run_extension] extension.json 必须是 JSON 对象", file=sys.stderr)
        return EXIT_SETUP

    template = str(extension.get("command_template") or "")
    error = _validate_template(template)
    if error:
        print(f"[run_extension] {error}", file=sys.stderr)
        return EXIT_SETUP

    code_dir = _find_code_dir(ext_dir)
    if code_dir is None:
        print("[run_extension] 代码目录缺失(converted_code/raw_code 均不存在)", file=sys.stderr)
        return EXIT_SETUP

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    # 2026-09-15 修复:代码挂载只读,但 18 用例语义是「cd 代码目录后脚本写
    # outputs/ 相对目录」。等价做法 = 把代码复制进可写 run_dir 执行,
    # 结束后把 run_dir 产物收割进 output_dir。显式 --work-dir 可覆盖。
    work_dir = Path(args.work_dir) if args.work_dir else (output_dir / "_work")
    work_dir.mkdir(parents=True, exist_ok=True)
    code_rel_paths = _copy_code_tree(code_dir, work_dir)
    # 模板中 {code_dir} 指向可写副本(而非只读挂载)
    effective_code_dir = work_dir

    try:
        parameters = _parse_params(args.param)
    except ExtensionRenderError as exc:
        print(f"[run_extension] {exc}", file=sys.stderr)
        return EXIT_SETUP

    input_files = sorted(p for p in input_dir.rglob("*") if p.is_file())
    try:
        command_text = render_command_template(
            extension,
            template=template,
            run_dir=work_dir,
            input_dir=input_dir,
            input_files=input_files,
            parameters=parameters,
            output_dir=output_dir,
            code_dir=effective_code_dir,
        )
    except ExtensionRenderError as exc:
        print(f"[run_extension] 模板渲染失败: {exc}", file=sys.stderr)
        return EXIT_SETUP

    try:
        command = shlex.split(command_text)
    except ValueError as exc:
        print(f"[run_extension] 命令切分失败: {exc}", file=sys.stderr)
        return EXIT_SETUP
    if not command:
        print("[run_extension] 渲染后命令为空", file=sys.stderr)
        return EXIT_SETUP

    print("[run_extension] command: " + command_text)
    sys.stdout.flush()

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    code = _run_command(command, work_dir, env, args.timeout)
    if code == EXIT_TIMEOUT:
        print(f"[run_extension] 执行超时({args.timeout}s),进程组已终止 (exit {EXIT_TIMEOUT})", file=sys.stderr)
        return EXIT_TIMEOUT
    if code != EXIT_OK:
        print(f"[run_extension] command exited with code {code}", file=sys.stderr)
        return code
    # 收割:work_dir 产物(排除原始代码文件)合并进 output_dir,然后删除 work 目录
    try:
        _harvest_outputs(work_dir, output_dir, code_rel_paths)
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)
    except OSError as exc:
        print(f"[run_extension] 产物收割失败: {exc}", file=sys.stderr)
        return EXIT_SETUP
    if not _output_contract_ok(output_dir):
        print(f"[run_extension] 输出契约校验失败:输出目录为空 {output_dir} (exit {EXIT_OUTPUT_CONTRACT})",
              file=sys.stderr)
        return EXIT_OUTPUT_CONTRACT
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
