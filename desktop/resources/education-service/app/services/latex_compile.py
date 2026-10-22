"""services/latex_compile.py —— tectonic 编译 .tex → PDF（P3a + P4 项目 zip）。

隔离与安全（对齐设计清单）：
  1. 一编译一目录：{reformat_dir}/{anchor_id}/compile/（input.tex/output.pdf/output.log/meta.json）
  2. 文件名永不落盘：用户文件名只做后缀校验，写入一律 input.tex
  3. tectonic --untrusted（官方旗标：禁用已知不安全特性，含 shell escape 路径）
  4. 子进程 start_new_session + 超时 killpg(SIGKILL)（整进程组）
  5. 单槽队列：asyncio.Lock + pending 计数，超出 compile_queue_limit → QueueFullError
  6. 产物 PDF > max_compile_pdf_bytes → 删文件 + OutputTooLargeError
  7. 启动/每次编译前机会性清扫 retention_days 天前的 job 目录（只删 12 位 hex 锚点目录）

P4 项目 zip（两段式 inspect + run，与 P3a 共享单槽队列/清扫/错误类）：
  - inspect_zip：安全解压（zip_safety）→ 主文件识别 → compile_inspect/{job_id}/（1h TTL）
  - compile_project_job：原子 rename 消费 inspect → compile_jobs/{cid}/ → tectonic →
    finally 删 project 树（只留 output.pdf/output.log/meta.json）
  - 失败也保证 output.log 落盘（补充 3）；classify_compile_error 给出用户友好文案
  - P3a 单文件路径（prepare_input/compile_tex/_run_once）零改动

tectonic 二进制缺失 → ToolUnavailableError（路由层回安装指引）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import get_settings

logger = logging.getLogger(__name__)

_ANCHOR_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_ENGINE = "tectonic-p3/0.17.0"
_LOG_TAIL_LINES = 200

# 单槽并发控制（进程内；重启归零可接受——本服务无持久队列需求）
_lock = asyncio.Lock()
_pending = 0


class CompileError(Exception):
    """编译失败（含 log 尾）。code ∈ {COMPILE_FAILED, TIMEOUT, OUTPUT_TOO_LARGE}。"""
    # compile_id：P4 项目编译失败时由 compile_project_job 附加（P3a 单文件为 None），
    # 路由层据此在失败信封里回 compile_id + log_url（补充 3）。
    compile_id: str | None = None

    def __init__(self, code: str, message: str, log_tail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.log_tail = log_tail


class QueueFullError(Exception):
    """等待槽位超限。"""


class ToolUnavailableError(Exception):
    """tectonic 二进制缺失/不可执行。"""


def _tectonic_bin() -> Path:
    return get_settings().tectonic_abs


def _ensure_tool() -> Path:
    from .local_tools import prepare_tectonic
    settings = get_settings()
    try:
        tool = prepare_tectonic(settings.tectonic_abs, settings.db_path_abs.parent)
        settings.tectonic_path = str(tool)
        return tool
    except Exception as exc:
        raise ToolUnavailableError("本地 LaTeX 编译器尚未准备完成，请检查网络后重试；原稿和修改稿已保留") from exc


def _log_tail(path: Path, lines: int = _LOG_TAIL_LINES) -> str:
    try:
        text = path.read_text("utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def compile_dir(anchor_id: str) -> Path:
    """{reformat_dir}/{anchor_id}/compile/（存在性由调用方按需 mkdir）。"""
    return get_settings().reformat_dir_abs / anchor_id / "compile"


def prepare_input(anchor_id: str, source: str) -> Path:
    """写入隔离目录 input.tex；返回其路径。"""
    cdir = compile_dir(anchor_id)
    cdir.mkdir(parents=True, exist_ok=True)
    input_tex = cdir / "input.tex"
    input_tex.write_text(source, encoding="utf-8")
    return input_tex


async def compile_tex(anchor_id: str) -> dict[str, Any]:
    """同步编译 anchor 的 input.tex（须先 prepare_input）。

    → {compile_id: anchor_id, pdf_path, log_path, seconds, pdf_bytes}
    失败抛 CompileError / QueueFullError / ToolUnavailableError。
    """
    global _pending
    settings = get_settings()
    await asyncio.to_thread(_ensure_tool)  # Local preparation must not block other panels.

    if _pending >= settings.compile_queue_limit:
        raise QueueFullError(
            f"编译队列已满（{_pending} 个任务排队），请稍后重试"
        )
    _pending += 1
    try:
        async with _lock:
            return await _run_once(anchor_id)
    finally:
        _pending -= 1


async def _run_once(anchor_id: str) -> dict[str, Any]:
    settings = get_settings()
    cdir = compile_dir(anchor_id)
    input_tex = cdir / "input.tex"
    pdf_path = cdir / "output.pdf"
    log_path = cdir / "output.log"
    if not input_tex.is_file():
        raise CompileError("COMPILE_FAILED", "编译输入不存在，请重新上传源码")

    env = dict(os.environ)
    env["TECTONIC_CACHE_DIR"] = str(settings.tectonic_cache_abs)
    cmd = [
        str(_tectonic_bin()), "-X", "compile", input_tex.name,
        "--untrusted", "--keep-logs", "--outdir", ".",
    ]
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cdir, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            await asyncio.wait_for(
                proc.communicate(), timeout=settings.compile_timeout_s
            )
        except asyncio.TimeoutError:
            _kill_group(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            log_path.write_text(_log_tail(log_path) + "\n[timeout] 编译超时，进程已终止",
                                 encoding="utf-8")
            raise CompileError(
                "TIMEOUT",
                f"编译超过 {settings.compile_timeout_s:g} 秒未完成，已终止",
                _log_tail(log_path),
            ) from None
    except FileNotFoundError as exc:
        raise ToolUnavailableError(f"tectonic 不可执行：{exc}") from exc
    finally:
        seconds = round(time.monotonic() - started, 2)

    # tectonic --keep-logs 把日志写在 cwd 的 input.log
    produced_log = cdir / "input.log"
    if produced_log.is_file():
        shutil.move(str(produced_log), str(log_path))
    if not log_path.is_file():
        log_path.write_text("", encoding="utf-8")

    produced_pdf = cdir / "input.pdf"
    if not produced_pdf.is_file():
        raise CompileError(
            "COMPILE_FAILED",
            "编译失败（未产出 PDF），请查看日志定位 LaTeX 错误",
            _log_tail(log_path),
        )
    pdf_bytes = produced_pdf.stat().st_size
    if pdf_bytes > settings.max_compile_pdf_bytes:
        produced_pdf.unlink(missing_ok=True)
        raise CompileError(
            "OUTPUT_TOO_LARGE",
            f"产出 PDF 超过 {settings.max_compile_pdf_bytes // (1024 * 1024)}MB 上限，已删除",
            _log_tail(log_path),
        )
    produced_pdf.rename(pdf_path)

    meta = {
        "compile_id": anchor_id,
        "engine": _ENGINE,
        "seconds": seconds,
        "pdf_bytes": pdf_bytes,
        "returncode": 0,
        "compiled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (cdir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("compile: %s → PDF %d bytes, %.2fs", anchor_id, pdf_bytes, seconds)
    return {
        "compile_id": anchor_id,
        "pdf_path": pdf_path,
        "log_path": log_path,
        "seconds": seconds,
        "pdf_bytes": pdf_bytes,
    }


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill() if os.name == "nt" else os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def sweep_expired() -> int:
    """删除 mtime 超 retention_days 的 12-hex 锚点目录（编译产物+P1 落盘一并清）。"""
    settings = get_settings()
    base = settings.reformat_dir_abs
    if not base.is_dir():
        return 0
    cutoff = time.time() - settings.compile_retention_days * 86400
    removed = 0
    for child in base.iterdir():
        if not child.is_dir() or not _ANCHOR_ID_RE.fullmatch(child.name):
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("compile sweep: 清理 %d 个过期任务目录（>%d 天）",
                    removed, settings.compile_retention_days)
    return removed


# ================================================================ P4：项目 zip（inspect + run）
#
# 与 P3a 共用：_lock/_pending 单槽队列、_ensure_tool、CompileError/QueueFullError/
# ToolUnavailableError、_kill_group、_log_tail、12-hex 目录与 mtime sweep 约定。
# P3a 函数（prepare_input/compile_tex/_run_once）零改动。


class InspectError(Exception):
    """inspect/消费阶段业务失败。
    code ∈ {VALIDATION, ZIP_UNSAFE, ZIP_BOMB, ZIP_INVALID, ZIP_TOO_LARGE}。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


_DOCCLASS_RE = re.compile(r"(?<!\\)\\documentclass\s*(\[|\{)")
_IMG_LIKE_EXT = {".png", ".jpg", ".jpeg", ".pdf", ".eps", ".svg"}


def inspect_root() -> Path:
    """data/compile_inspect/（1 小时 TTL 暂存根，存在性由调用方按需 mkdir）。"""
    return get_settings().inspect_dir_abs


def compile_job_dir(cid: str) -> Path:
    """data/compile_jobs/{cid}/（产物根；不走 reformat_dir，P4 独立清扫）。"""
    return get_settings().compile_jobs_dir_abs / cid


def _strip_tex_comments(text: str) -> str:
    """粗剥注释：行内 % 截断（\\% 误伤可接受，MVP 设计决策）。"""
    return "\n".join(ln.split("%", 1)[0] for ln in text.splitlines())


def _read_tex(path: Path) -> str:
    """utf-8 → gb18030 → latin-1 降级读（中文模板常见 GBK）。"""
    raw = path.read_bytes()
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def find_main_tex(project_dir: Path) -> tuple[str | None, list[str], int]:
    """主文件识别（补充 2）→ (main_rel | None, 含 documentclass 的相对路径清单, .tex 总数)。

    场景：唯一候选 → main；0 个 .tex / 0 候选 / ≥2 候选 → None + 清单（调用方报错）。
    返回的相对路径用 posix 分隔符（与 zip 条目名一致）。
    """
    tex_files = sorted(
        p for p in project_dir.rglob("*")
        if p.is_file() and p.suffix.lower() == ".tex"
    )
    if not tex_files:
        return None, [], 0
    candidates: list[str] = []
    for p in tex_files:
        if _DOCCLASS_RE.search(_strip_tex_comments(_read_tex(p))):
            candidates.append(p.relative_to(project_dir).as_posix())
    if len(candidates) == 1:
        return candidates[0], candidates, len(tex_files)
    return None, candidates, len(tex_files)


_MISSING_FILE_RE = re.compile(
    r"LaTeX Error: File [`']([^`']+)' not found"
    r"|I can't find file [`']([^`']+)'"
)
_IMG_INCLUSION_RE = re.compile(r"""image inclusion failed for ["']([^"']+)["']""")

_EPS_CONVERTED_SUFFIX = "-eps-converted-to.pdf"
_REF_TEXT_EXTS = {".tex", ".cls", ".sty", ".cfg", ".def"}

# EPS 现场转换 v2（约束 1-4）：xdvipdfmx 无法内嵌 EPS → 解压后批量转同名 .pdf
_EPS_CONVERT_TIMEOUT_S = 15                      # 约束2：单文件转换超时（秒）
_EPS_CONVERT_MAX_FILE = 20 * 1024 * 1024         # 约束3：单文件转换后上限（warning）
_EPS_CONVERT_MAX_TOTAL = 100 * 1024 * 1024       # 约束3：转换产物总上限（中止）


def _eps_tool_commands(eps: Path, pdf: Path) -> list[tuple[str, list[str]]]:
    """转换工具优先级：gs（约束1 必须 -dSAFER）→ epstopdf → ps2pdf；只列 PATH 里可用的。"""
    tools: list[tuple[str, list[str]]] = []
    gs = shutil.which("gs")
    if gs:
        tools.append(("gs", [
            gs, "-dSAFER", "-dNOPAUSE", "-dBATCH", "-sDEVICE=pdfwrite",
            "-dPDFSETTINGS=/prepress",
            f"-sOutputFile={pdf}", str(eps),
        ]))
    epstopdf = shutil.which("epstopdf")
    if epstopdf:
        # epstopdf 默认输出到同目录同名 .pdf（即 pdf 路径）
        tools.append(("epstopdf", [epstopdf, str(eps)]))
    ps2pdf = shutil.which("ps2pdf")
    if ps2pdf:
        tools.append(("ps2pdf", [ps2pdf, str(eps), str(pdf)]))
    return tools


def _convert_eps_file(eps: Path, pdf: Path) -> tuple[str, str]:
    """逐工具尝试把 eps 转成同名 pdf → ("ok", tool) | ("timeout", tool) | ("fail", "")。

    约束2：单工具 15s 超时 → 放弃该文件（继续下一个 eps，不再试后续工具）。
    成功判定：退出后 pdf 存在且非空（防半成品）。
    """
    tools = _eps_tool_commands(eps, pdf)
    if not tools:
        return "fail", ""
    for tool, cmd in tools:
        pdf.unlink(missing_ok=True)  # 清上一个工具的半成品
        try:
            subprocess.run(
                cmd, timeout=_EPS_CONVERT_TIMEOUT_S,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            )
        except subprocess.TimeoutExpired:
            pdf.unlink(missing_ok=True)
            return "timeout", tool
        except OSError:
            continue
        if pdf.is_file() and pdf.stat().st_size > 0:
            return "ok", tool
    pdf.unlink(missing_ok=True)
    return "fail", ""


def process_eps_images(project: Path) -> list[str]:
    """EPS 兼容 v2：解压后**批量转换项目内所有 .eps**（不依赖 zip 自带预转换版）。

    对每个 X.eps（原 .eps 保留）：
      1. 已有同名 X.pdf → 跳过转换（引用照常切换为 .pdf）；
      2. zip 自带 X-eps-converted-to.pdf 预转换版 → 复制为 X.pdf（免转换）；
      3. 否则现场转换：gs(-dSAFER) → epstopdf → ps2pdf 逐个 fallback（约束1），
         单文件 15s 超时 → warning 后继续下一个（约束2）。
    转换成功 → 显式 `X.eps` 引用字节替换为 `X.pdf`（.tex/.cls/.sty/.cfg/.def 全扫，
    纯字节 ASCII/UTF-8 替换不碰编码）；无扩展名引用 `{.../X}` 由 graphicx 直接
    命中新生成的 X.pdf（解析顺序 .pdf→…→.eps）。
    约束3：单文件转换后 >20MB → warning；转换产物累计 >100MB → 抛
    InspectError("ZIP_TOO_LARGE") 中止；约束4：返回值为写入 output.log 的
    "[eps-convert]" 日志行（由 _run_project 落盘）。
    → 日志行列表（无 .eps 项目 → 空）。
    """
    eps_files = sorted(
        p for p in project.rglob("*")
        if p.is_file() and p.suffix.lower() == ".eps"
    )
    if not eps_files:
        return []
    lines: list[str] = []
    handled: list[tuple[bytes, bytes]] = []
    total_out = 0

    def _rel(p: Path) -> str:
        return p.relative_to(project).as_posix()

    for eps in eps_files:
        pdf = eps.with_suffix(".pdf")
        src_rel, dst_rel = _rel(eps), _rel(pdf)
        if pdf.is_file():
            handled.append((eps.name.encode("utf-8"), pdf.name.encode("utf-8")))
            continue
        converted = eps.with_name(eps.stem + _EPS_CONVERTED_SUFFIX)
        if converted.is_file():
            t0 = time.monotonic()
            shutil.copyfile(converted, pdf)
            elapsed = time.monotonic() - t0
            tool = "preset"
        else:
            t0 = time.monotonic()
            status, tool = _convert_eps_file(eps, pdf)
            elapsed = time.monotonic() - t0
            if status == "timeout":
                lines.append(f"[eps-convert] {src_rel} 转换超时")  # 约束2
                continue
            if status != "ok":
                lines.append(
                    f"[eps-convert] 全部工具失败，图片 {src_rel} 未转换")
                continue
        # 约束4：转换日志（成功路径）
        lines.append(
            f"[eps-convert] {src_rel} → {dst_rel} "
            f"({eps.stat().st_size} → {pdf.stat().st_size}, "
            f"{elapsed:.2f}s, {tool})")
        size_out = pdf.stat().st_size
        if size_out > _EPS_CONVERT_MAX_FILE:                        # 约束3
            lines.append(
                f"[eps-convert] 警告：{dst_rel} 转换后 {size_out} 字节"
                f"超过 {_EPS_CONVERT_MAX_FILE // (1024 * 1024)}MB 上限")
        total_out += size_out
        if total_out > _EPS_CONVERT_MAX_TOTAL:                      # 约束3
            raise InspectError(
                "ZIP_TOO_LARGE",
                f"EPS 转换后总大小超过 {_EPS_CONVERT_MAX_TOTAL // (1024 * 1024)}MB 上限"
                f"（已达 {total_out // (1024 * 1024)}MB），请压缩图片后重试")
        handled.append((eps.name.encode("utf-8"), pdf.name.encode("utf-8")))

    # 显式 .eps 引用 → .pdf（仅对转换成功者；失败的保持 .eps → 编译兜底友好报错）
    changed = 0
    if handled:
        for f in project.rglob("*"):
            if not (f.is_file() and f.suffix.lower() in _REF_TEXT_EXTS):
                continue
            data = f.read_bytes()
            new = data
            for old_b, new_b in handled:
                new = new.replace(old_b, new_b)
            if new != data:
                f.write_bytes(new)
                changed += 1
    if changed:
        lines.append(f"[eps-convert] {changed} 个文件的显式 .eps 引用已改写为 .pdf")
    if lines:
        logger.info("eps v2: %s", " | ".join(lines))
    return lines


def _append_eps_lines(log_path: Path, lines: list[str] | None) -> None:
    """约束4：把 [eps-convert] 转换日志写入 output.log（引擎 log 之后、chatter 之前）。"""
    if not lines:
        return
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def classify_compile_error(log_tail: str) -> str:
    """COMPILE_FAILED 的日志 → 用户友好文案（Q4/§4.4）。"""
    m = _MISSING_FILE_RE.search(log_tail)
    if m:
        name = next(g for g in m.groups() if g)
        ext = Path(name).suffix.lower()
        if ext == ".cls":
            return f"项目缺少 {name}，请从期刊官网下载模板后重新上传"
        if ext in (".sty", ".def", ".clo"):
            return f"缺少 LaTeX 宏包 {name}，可能是模板不完整"
        if ext in _IMG_LIKE_EXT:
            return f"图片 {name} 未在 zip 里，请检查引用路径与大小写"
        if ext == ".bib":
            return f"缺少文献库 {name}，请确认 .bib 已包含在 zip 中"
        return f"编译找不到文件 {name}，请检查 zip 内文件路径"
    m = _IMG_INCLUSION_RE.search(log_tail)
    if m:
        name = m.group(1)
        if Path(name).suffix.lower() == ".eps":
            # EPS v2 已现场转换；仍失败 = 服务器转换没成功（工具缺失/超时/格式怪异）
            return "项目包含 EPS 图片，服务器转换失败，请转成 PDF/PNG 后重试"
        return f"图片 {name} 无法内嵌，请检查图片格式与路径（建议使用 PDF/PNG）"
    if re.search(r"Emergency stop|Undefined control sequence", log_tail):
        return "LaTeX 源码有语法错误，请展开日志定位出错行"
    return "编译失败，请查看日志定位 LaTeX 错误"


async def inspect_zip(zip_bytes: bytes) -> dict[str, Any]:
    """两段式之①：安全解压 + 主文件识别 + 落盘 inspect 暂存（1h TTL）。

    → {job_id, main, stats{files,tex,images,cls,bib,other,bytes}, skipped[], skipped_count}
    失败抛 InspectError（路由层转信封）；任何失败 → rmtree 暂存目录，磁盘零残留。
    """
    from .zip_safety import ZipSafetyError, safe_extract

    settings = get_settings()
    sweep_inspect_expired()          # 机会性清扫（同 P3a 模式）
    job_id = uuid.uuid4().hex[:12]
    jdir = inspect_root() / job_id
    project = jdir / "project"
    try:
        try:
            result = await asyncio.to_thread(safe_extract, zip_bytes, project, settings)
        except ZipSafetyError as exc:
            raise InspectError(exc.code, exc.message) from exc
        main, candidates, tex_total = find_main_tex(project)
        if tex_total == 0:
            raise InspectError("VALIDATION", "zip 中没有 .tex 文件")
        if not candidates:
            raise InspectError(
                "VALIDATION", "未找到含 \\documentclass 的 .tex 文件")
        if main is None:
            shown = ", ".join(Path(c).name for c in candidates)
            raise InspectError(
                "VALIDATION",
                f"检测到多个主文件（{shown}），请合并为单一入口后重新上传")
        meta = {
            "job_id": job_id,
            "main": main,
            "stats": {
                "files": result.files, "tex": result.tex, "images": result.images,
                "cls": result.cls, "bib": result.bib, "other": result.other,
                "bytes": result.bytes,
            },
            "skipped": [{"name": s.name, "reason": s.reason} for s in result.skipped],
            "skipped_count": result.skipped_count,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        jdir.mkdir(parents=True, exist_ok=True)
        (jdir / "inspect.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta
    except BaseException:
        shutil.rmtree(jdir, ignore_errors=True)
        raise


def consume_inspect(job_id: str, cid: str) -> tuple[Path, dict[str, Any]]:
    """两段式之②前置：**原子 rename 消费** inspect 暂存 → compile_jobs/{cid}/project。

    并发正确性（防同一 job 双跑）：
      os.rename(src, dst) 在同一文件系统内是原子的，且 src 不存在 → FileNotFoundError。
      两个并发 run 拿同一 job_id 时，只有一个能成功 rename；后到者拿到
      FileNotFoundError → 路由层转 VALIDATION"已过期/不存在"，**绝不可能双编译**。
      inspect 残壳（inspect.json）在 rename 成功后立即删除，也杜绝二跑读到半状态。
    """
    settings = get_settings()
    if not _ANCHOR_ID_RE.fullmatch(job_id or ""):
        raise InspectError("VALIDATION", "job_id 格式非法（应为 12 位 hex）")
    src_root = inspect_root() / job_id
    meta_path = src_root / "inspect.json"
    src_project = src_root / "project"
    if not (meta_path.is_file() and src_project.is_dir()):
        raise InspectError(
            "VALIDATION", "上传任务不存在或已过期（1 小时），请重新上传 zip")
    try:
        meta = json.loads(meta_path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise InspectError(
            "VALIDATION", "上传任务数据损坏，请重新上传 zip") from exc
    dst_root = compile_job_dir(cid)
    dst_project = dst_root / "project"
    dst_root.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src_project, dst_project)
    except FileNotFoundError as exc:
        raise InspectError(
            "VALIDATION", "上传任务不存在或已过期（1 小时），请重新上传 zip") from exc
    except OSError as exc:  # 跨设备等罕见失败 → 保持 inspect 原样可重试
        raise InspectError(
            "VALIDATION", "任务消费失败，请重试或重新上传") from exc
    shutil.rmtree(src_root, ignore_errors=True)  # 消费成功 → 删残壳
    return dst_project, meta


async def compile_project_job(job_id: str) -> dict[str, Any]:
    """两段式之②：原子消费 inspect → tectonic 编译 → 产物留 pdf/log/meta。

    队列/工具检查**先于消费**（inspect 保留 → 失败可直接重试）；
    EPS v2：解压后批量转换所有 .eps → 同名 .pdf（gs -dSAFER → epstopdf → ps2pdf，
    单文件 15s 超时；产物累计 >100MB → ZIP_TOO_LARGE 中止），转换日志进 output.log；
    成功 → 立即删 project/；失败（COMPILE_FAILED/TIMEOUT）→ **保留 project/ 现场 24h**
    供诊断（sweep_compile_jobs_expired 按 failed_project_ttl_s 清理；
    KEEP_FAILED_PROJECTS=1 永久保留）；
    失败必落完整 output.log（含 --print 的 XDV→PDF 阶段输出，补充 3）。

    → {compile_id, pdf_path, log_path, seconds, pdf_bytes}
    """
    global _pending
    settings = get_settings()
    await asyncio.to_thread(_ensure_tool)
    sweep_inspect_expired()
    if _pending >= settings.compile_queue_limit:
        raise QueueFullError(
            f"编译队列已满（{_pending} 个任务排队），请稍后重试")
    _pending += 1                     # 占位先于消费：QUEUE_FULL 时 inspect 未动
    cid = uuid.uuid4().hex[:12]
    project: Path | None = None
    meta: dict[str, Any] = {}
    try:
        project, meta = await asyncio.to_thread(consume_inspect, job_id, cid)
        try:
            # EPS v2：解压后批量转换所有 .eps → 同名 .pdf 并切换显式引用
            # （不依赖 zip 自带预转换版；失败现场保留转换后的改写结果）
            eps_lines = await asyncio.to_thread(process_eps_images, project)
            async with _lock:
                result = await _run_project(
                    cid, project, str(meta.get("main", "")), eps_lines)
            # 成功 → 立即清理 project 树（失败路径在 finally 保留现场）
            shutil.rmtree(project, ignore_errors=True)
            project = None
            return result
        except CompileError as exc:
            # 补充 3：失败信封也要能下载 log → 异常携带产物 cid（log 落盘于 compile_jobs/{cid}/）
            exc.compile_id = cid
            raise
    finally:
        _pending -= 1
        if project is not None:
            # 编译失败 → 保留 compile_jobs/{cid}/project/ 现场供诊断；
            # touch 目录使 24h 保留窗从失败时刻起算
            try:
                os.utime(project, None)
            except OSError:
                pass


async def _run_project(
    cid: str,
    project: Path,
    main_rel: str,
    eps_log_lines: list[str] | None = None,
) -> dict[str, Any]:
    """单槽内执行项目编译：cwd=main 所在目录（相对路径解析），outdir=compile_jobs/{cid}。

    eps_log_lines：EPS v2 的 [eps-convert] 转换日志（约束4），正常/超时两条路径
    都会写进 output.log（引擎 log 之后、chatter/[timeout] 之前）。
    """
    settings = get_settings()
    outdir = compile_job_dir(cid)
    outdir.mkdir(parents=True, exist_ok=True)
    main_path = project / main_rel
    if not main_rel or not main_path.is_file():
        raise InspectError("VALIDATION", "主文件缺失，请重新上传 zip")
    cwd = main_path.parent
    stem = main_path.stem

    env = dict(os.environ)
    env["TECTONIC_CACHE_DIR"] = str(settings.tectonic_cache_abs)
    # --print：打印引擎完整 chatter（含 XDV→PDF/xdvipdfmx 阶段输出）；
    # 不加时 xdvipdfmx 报错只进 tectonic stderr、不落 {stem}.log，导致"LaTeX 成功但
    # 无 PDF"且 log 无错误行（MDPI 模板实测）。stdout/stderr 统一落盘 output.log。
    cmd = [
        str(_tectonic_bin()), "-X", "compile", main_path.name,
        "--untrusted", "--keep-logs", "--print", "--outdir", str(outdir),
    ]
    timeout = settings.compile_project_timeout_s
    log_path = outdir / "output.log"
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(cwd), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(
                proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            _kill_group(proc)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            # 超时后 communicate 已取消，chatter 不可取；.log（--keep-logs）仍完整
            _ensure_project_log(outdir, stem, cwd)
            # 约束4：转换日志先落（转换发生在编译之前），再追加超时标记
            _append_eps_lines(log_path, eps_log_lines)
            # 追加而非覆盖：保留完整引擎输出（诊断 XDV→PDF 阶段必需）
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"\n[timeout] 编译超时（{timeout:g}s），进程已终止\n")
            raise CompileError(
                "TIMEOUT",
                f"编译超过 {timeout:g} 秒未完成，已终止"
                "（项目首次编译需下载宏包，可能较慢，请重试）",
                _log_tail(log_path),
            ) from None
    except FileNotFoundError as exc:
        raise ToolUnavailableError(f"tectonic 不可执行：{exc}") from exc
    finally:
        seconds = round(time.monotonic() - started, 2)

    _ensure_project_log(outdir, stem, cwd)

    # 产物归一：{stem}.pdf/.log → output.pdf/.log（补充 3：失败也必有 output.log）
    produced_pdf = outdir / f"{stem}.pdf"
    produced_log = outdir / f"{stem}.log"
    if produced_log.is_file():
        shutil.move(str(produced_log), str(log_path))
    if not log_path.is_file():
        log_path.write_text(
            "[no log] tectonic 未产出日志（进程异常退出）", encoding="utf-8")
    # 约束4：[eps-convert] 转换日志（引擎 log 之后、chatter 之前）
    _append_eps_lines(log_path, eps_log_lines)
    # --print 的进程输出（含 XDV→PDF 阶段）追加到 output.log 全文之后；
    # 信封 detail 仍只取尾 200 行，磁盘保留完整 log 供"查看完整 log"下载
    _append_chatter(log_path, stdout_bytes)

    if not produced_pdf.is_file():
        raise CompileError(
            "COMPILE_FAILED",
            classify_compile_error(_log_tail(log_path)),
            _log_tail(log_path),
        )
    pdf_bytes = produced_pdf.stat().st_size
    if pdf_bytes > settings.max_compile_pdf_bytes:
        produced_pdf.unlink(missing_ok=True)
        raise CompileError(
            "OUTPUT_TOO_LARGE",
            f"产出 PDF 超过 {settings.max_compile_pdf_bytes // (1024 * 1024)}MB 上限，已删除",
            _log_tail(log_path),
        )
    produced_pdf.rename(outdir / "output.pdf")

    meta = {
        "compile_id": cid,
        "project": True,
        "main": main_rel,
        "engine": _ENGINE,
        "seconds": seconds,
        "pdf_bytes": pdf_bytes,
        "returncode": 0,
        "compiled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (outdir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("compile(project): %s main=%s → PDF %d bytes, %.2fs",
                cid, main_rel, pdf_bytes, seconds)
    return {
        "compile_id": cid,
        "pdf_path": outdir / "output.pdf",
        "log_path": log_path,
        "seconds": seconds,
        "pdf_bytes": pdf_bytes,
    }


def _ensure_project_log(outdir: Path, stem: str, cwd: Path) -> None:
    """tectonic 落 log 到 outdir 或 cwd → 统一挪到 outdir/output.log；都没有则占位。"""
    log_path = outdir / "output.log"
    if log_path.is_file():
        return
    for cand in (outdir / f"{stem}.log", cwd / f"{stem}.log"):
        if cand.is_file():
            shutil.move(str(cand), str(log_path))
            return


def _append_chatter(log_path: Path, stdout_bytes: bytes | None) -> None:
    """把 tectonic --print 的进程 stdout（含 XDV→PDF/xdvipdfmx 阶段）追加到 output.log。

    {stem}.log 只有 LaTeX 引擎内容（止于 "Output written on … .xdv"）；
    XDV→PDF 转换错误只出现在进程 chatter 里——不落盘则"无 PDF 且 log 无错误行"。
    """
    if not stdout_bytes:
        return
    chatter = stdout_bytes.decode("utf-8", errors="replace").strip()
    if not chatter:
        return
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write("\n\n===== tectonic --print（进程输出，含 XDV→PDF 阶段）=====\n")
        fh.write(chatter)
        fh.write("\n")


def sweep_inspect_expired() -> int:
    """删 mtime 超 inspect_ttl_s（默认 1h）的 12-hex inspect 暂存目录。"""
    settings = get_settings()
    base = settings.inspect_dir_abs
    if not base.is_dir():
        return 0
    cutoff = time.time() - settings.inspect_ttl_s
    removed = 0
    for child in base.iterdir():
        if not child.is_dir() or not _ANCHOR_ID_RE.fullmatch(child.name):
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("inspect sweep: 清理 %d 个过期 inspect 暂存（>%d 秒）",
                    removed, settings.inspect_ttl_s)
    return removed


def _keep_failed_projects() -> bool:
    """失败现场是否永久保留（调试）：REVIEW_KEEP_FAILED_PROJECTS=1 或裸 KEEP_FAILED_PROJECTS=1。"""
    if get_settings().keep_failed_projects:
        return True
    return os.environ.get("KEEP_FAILED_PROJECTS", "").strip() == "1"


def sweep_compile_jobs_expired() -> int:
    """编译产物清扫，两级：
      ① 失败现场 project/ 子目录：mtime 超 failed_project_ttl_s（默认 24h）→ 删子目录
         （compile_jobs/{cid} 本体与 output.log/meta 保留，供"查看完整 log"）；
         KEEP_FAILED_PROJECTS=1 跳过（永久保留，调试用）。
      ② 整个 compile_jobs/{cid}：mtime 超 compile_retention_days（7 天）→ 整目录删。
    """
    settings = get_settings()
    base = settings.compile_jobs_dir_abs
    if not base.is_dir():
        return 0
    removed = 0
    keep_failed = _keep_failed_projects()
    # ① 失败现场 24h 清理（成功编译的 project/ 已在 compile_project_job 立即删）
    if not keep_failed:
        proj_cutoff = time.time() - settings.failed_project_ttl_s
        for child in base.iterdir():
            if not child.is_dir() or not _ANCHOR_ID_RE.fullmatch(child.name):
                continue
            proj = child / "project"
            if not proj.is_dir():
                continue
            try:
                if proj.stat().st_mtime < proj_cutoff:
                    shutil.rmtree(proj, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
    # ② 整目录 7 天清理
    cutoff = time.time() - settings.compile_retention_days * 86400
    for child in base.iterdir():
        if not child.is_dir() or not _ANCHOR_ID_RE.fullmatch(child.name):
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("compile_jobs sweep: 清理 %d 个过期项目产物/失败现场", removed)
    return removed
