"""routers/compile.py —— LaTeX 编译 PDF（P3a：tectonic）。

端点：
  POST /api/review/compile               multipart：file(.tex 可选) | job_id(可选，二选一)
                                         → {ok, compile_id, pdf_url, log_url, meta}
  GET  /api/review/compile/{cid}         编译元信息（meta.json）
  GET  /api/review/compile/{cid}/pdf     output.pdf（FileResponse）
  GET  /api/review/compile/{cid}/log     output.log（text/plain）

信封：业务错误 = HTTP 200 + {ok:false,error:{code,message,detail?,degraded:false}}
（QUEUE_FULL 例外：HTTP 429 + 同款信封体，前端仍按 ok/error 单一口径处理）。
detail = log 尾 200 行（COMPILE_FAILED / TIMEOUT）。
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from ..config import get_settings
from ..services import latex_compile as lc
from ..services.latex_compile import (
    CompileError,
    QueueFullError,
    ToolUnavailableError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/review", tags=["review-compile"])

_CID_RE = re.compile(r"^[0-9a-f]{12}$")


class CompileResponse(BaseModel):
    ok: bool
    compile_id: str | None = None
    pdf_url: str | None = None
    log_url: str | None = None
    meta: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


def _error(code: str, message: str, detail: str = "") -> CompileResponse:
    err: dict[str, Any] = {"code": code, "message": message, "degraded": False}
    if detail:
        err["detail"] = detail
    return CompileResponse(ok=False, error=err)


def _jobs_root() -> Path:
    d = get_settings().reformat_dir_abs
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.post("/compile", response_model=CompileResponse)
async def compile_tex(
    file: UploadFile | None = File(None, description="LaTeX 源码（.tex，与 job_id 二选一）"),
    job_id: str = Form("", description="P1 重排版任务 id（优先于 file）"),
) -> CompileResponse:
    settings = get_settings()

    # ---- 输入解析：job_id 优先，否则直接上传的 .tex ----
    jid = (job_id or "").strip()
    if jid:
        if not _CID_RE.fullmatch(jid):
            return _error("VALIDATION", "job_id 格式非法（应为 12 位 hex）")
        src = _jobs_root() / f"{jid}.tex"
        if not src.is_file():
            return _error("VALIDATION", "job_id 对应的重排版结果不存在，请重新排版")
        try:
            source = src.read_text("utf-8")
        except UnicodeDecodeError:
            return _error("VALIDATION", "任务源码不是合法 UTF-8")
        anchor_id = jid
    else:
        if file is None:
            return _error("VALIDATION", "必须提供 file（.tex）或 job_id")
        filename = file.filename or "paper.tex"
        if Path(filename).name != filename or ".." in filename:
            return _error("VALIDATION", "非法文件名")
        if not filename.lower().endswith(".tex"):
            return _error("VALIDATION", "仅支持 .tex 文件")
        content = await file.read()
        if not content:
            return _error("VALIDATION", "上传文件为空")
        if len(content) > settings.max_tex_bytes:
            return _error("VALIDATION", "源码超过 2MB 上限")
        try:
            source = content.decode("utf-8")
        except UnicodeDecodeError:
            return _error("VALIDATION", "源码必须为 UTF-8 编码")
        anchor_id = uuid.uuid4().hex[:12]

    # ---- 编译（隔离目录；失败信封带 log 尾）----
    lc.prepare_input(anchor_id, source)
    try:
        result = await lc.compile_tex(anchor_id)
    except ToolUnavailableError as exc:
        return _error("TOOL_UNAVAILABLE", str(exc))
    except QueueFullError as exc:
        return JSONResponse(
            status_code=429,
            content={
                "ok": False,
                "compile_id": None, "pdf_url": None, "log_url": None, "meta": None,
                "error": {"code": "QUEUE_FULL", "message": str(exc), "degraded": False},
            },
        )
    except CompileError as exc:
        return _error(exc.code, str(exc), detail=exc.log_tail)

    meta_path = lc.compile_dir(anchor_id) / "meta.json"
    try:
        meta = json.loads(meta_path.read_text("utf-8"))
    except (OSError, ValueError):
        meta = {"compile_id": anchor_id, "seconds": result["seconds"]}
    return CompileResponse(
        ok=True,
        compile_id=anchor_id,
        pdf_url=f"/api/review/compile/{anchor_id}/pdf",
        log_url=f"/api/review/compile/{anchor_id}/log",
        meta=meta,
    )


# ================================================================ P4：项目 zip（两段式）

def _p4_error(code: str, message: str, detail: str = "",
              compile_id: str | None = None,
              log_url: str | None = None) -> CompileResponse:
    """P4 错误信封（补充 3：失败也带 compile_id + log_url，前端可下载完整 log）。"""
    err: dict[str, Any] = {"code": code, "message": message, "degraded": False}
    if detail:
        err["detail"] = detail
    return CompileResponse(
        ok=False, compile_id=compile_id, log_url=log_url, error=err)


def _project_pdf_url(cid: str) -> str:
    return f"/api/review/compile/zip/{cid}/pdf"


def _project_log_url(cid: str) -> str:
    return f"/api/review/compile/zip/{cid}/log"


@router.post("/compile/inspect", response_model=CompileResponse)
async def inspect_zip_project(
    file: UploadFile = File(..., description="LaTeX 项目 zip（含 main.tex/.cls/图片/.bib）"),
) -> CompileResponse:
    """两段式之①：安全解压 + 主文件识别 → job_id（1 小时 TTL，run 前不占编译队列）。

    暂用 request.body() 整体缓冲（≤50MB）；Phase 2 改流式上传
    """
    settings = get_settings()
    filename = file.filename or "project.zip"
    if Path(filename).name != filename or ".." in filename:
        return _p4_error("VALIDATION", "非法文件名")
    if not filename.lower().endswith(".zip"):
        return _p4_error("VALIDATION", "仅支持 .zip 文件")
    # 暂用 request.body() 整体缓冲（≤50MB）；Phase 2 改流式上传
    content = await file.read()
    if not content:
        return _p4_error("VALIDATION", "上传文件为空")
    if len(content) > settings.max_zip_bytes:
        return _p4_error(
            "VALIDATION",
            f"zip 超过 {settings.max_zip_bytes // (1024 * 1024)}MB 上限")
    try:
        meta = await lc.inspect_zip(content)
    except lc.InspectError as exc:
        return _p4_error(exc.code, exc.message)
    return CompileResponse(ok=True, meta=meta)


class RunZipRequest(BaseModel):
    job_id: str


@router.post("/compile/run", response_model=CompileResponse)
async def run_zip_compile(payload: RunZipRequest) -> CompileResponse:
    """两段式之②：原子消费 inspect（rename 防并发双跑）→ tectonic 编译 → pdf/log。

    队列/工具失败发生在消费**之前** → inspect 保留可重试；
    编译失败（COMPILE_FAILED/TIMEOUT）信封仍带 compile_id + log_url（补充 3）。
    """
    jid = (payload.job_id or "").strip()
    if not _CID_RE.fullmatch(jid):
        return _p4_error(
            "VALIDATION", "job_id 格式非法（应为 12 位 hex）")
    try:
        result = await lc.compile_project_job(jid)
    except lc.InspectError as exc:
        return _p4_error(exc.code, exc.message)
    except lc.ToolUnavailableError as exc:
        return _p4_error("TOOL_UNAVAILABLE", str(exc))
    except lc.QueueFullError as exc:
        # 队列满发生在 consume_inspect 之前 → job 未消费，前端可直接重试
        return JSONResponse(
            status_code=429,
            content={
                "ok": False,
                "compile_id": None, "pdf_url": None, "log_url": None, "meta": None,
                "error": {"code": "QUEUE_FULL", "message": str(exc),
                          "degraded": False},
            },
        )
    except lc.CompileError as exc:
        # 补充 3：失败也带 compile_id + log_url（cid 由 compile_project_job 附加）
        cid = getattr(exc, "compile_id", None)
        return _p4_error(
            exc.code, str(exc), detail=exc.log_tail,
            compile_id=cid,
            log_url=_project_log_url(cid) if cid else None)
    return CompileResponse(
        ok=True,
        compile_id=result["compile_id"],
        pdf_url=_project_pdf_url(result["compile_id"]),
        log_url=_project_log_url(result["compile_id"]),
        meta=json.loads((lc.compile_job_dir(result["compile_id"]) / "meta.json")
                        .read_text("utf-8"))
        if (lc.compile_job_dir(result["compile_id"]) / "meta.json").is_file()
        else {"compile_id": result["compile_id"], "seconds": result["seconds"]},
    )


def _cid_dir(cid: str) -> Path | None:
    if not _CID_RE.fullmatch(cid or ""):
        return None
    d = lc.compile_dir(cid)
    return d if d.is_dir() else None


def _zip_cid_dir(cid: str) -> Path | None:
    """P4 项目产物目录 data/compile_jobs/{cid}/（独立于 P3a reformat_dir 树）。"""
    if not _CID_RE.fullmatch(cid or ""):
        return None
    d = lc.compile_job_dir(cid)
    return d if d.is_dir() else None


@router.get("/compile/zip/{compile_id}", response_model=CompileResponse)
async def get_zip_compile(compile_id: str) -> CompileResponse:
    cdir = _zip_cid_dir(compile_id)
    meta_path = cdir / "meta.json" if cdir else None
    if meta_path is None or not meta_path.is_file():
        return _p4_error("NOT_FOUND", "编译任务不存在或已过期，请重新编译")
    try:
        meta = json.loads(meta_path.read_text("utf-8"))
    except (OSError, ValueError):
        return _p4_error("NOT_FOUND", "编译产物损坏，请重新编译")
    return CompileResponse(
        ok=True,
        compile_id=compile_id,
        pdf_url=_project_pdf_url(compile_id),
        log_url=_project_log_url(compile_id),
        meta=meta,
    )


@router.get("/compile/zip/{compile_id}/pdf", response_model=None)
async def download_zip_pdf(compile_id: str) -> FileResponse | JSONResponse:
    cdir = _zip_cid_dir(compile_id)
    pdf = cdir / "output.pdf" if cdir else None
    if pdf is None or not pdf.is_file():
        return JSONResponse(
            status_code=200,
            content={"ok": False, "error": {
                "code": "NOT_FOUND", "message": "PDF 不存在或已过期",
                "degraded": False}},
        )
    return FileResponse(pdf, media_type="application/pdf",
                        filename=f"{compile_id}.pdf")


@router.get("/compile/zip/{compile_id}/log", response_model=None)
async def download_zip_log(compile_id: str) -> PlainTextResponse | JSONResponse:
    """补充 3：成功/失败都可下载 output.log。"""
    cdir = _zip_cid_dir(compile_id)
    log = cdir / "output.log" if cdir else None
    if log is None or not log.is_file():
        return JSONResponse(
            status_code=200,
            content={"ok": False, "error": {
                "code": "NOT_FOUND", "message": "日志不存在或已过期",
                "degraded": False}},
        )
    return PlainTextResponse(log.read_text("utf-8", errors="replace"))


@router.get("/compile/{compile_id}", response_model=CompileResponse)
async def get_compile(compile_id: str) -> CompileResponse:
    cdir = _cid_dir(compile_id)
    meta_path = cdir / "meta.json" if cdir else None
    if meta_path is None or not meta_path.is_file():
        return _error("NOT_FOUND", "编译任务不存在或已过期，请重新编译")
    try:
        meta = json.loads(meta_path.read_text("utf-8"))
    except (OSError, ValueError):
        return _error("NOT_FOUND", "编译产物损坏，请重新编译")
    return CompileResponse(
        ok=True,
        compile_id=compile_id,
        pdf_url=f"/api/review/compile/{compile_id}/pdf",
        log_url=f"/api/review/compile/{compile_id}/log",
        meta=meta,
    )


@router.get("/compile/{compile_id}/pdf", response_model=None)
async def download_compile_pdf(compile_id: str) -> FileResponse | JSONResponse:
    cdir = _cid_dir(compile_id)
    pdf = cdir / "output.pdf" if cdir else None
    if pdf is None or not pdf.is_file():
        return JSONResponse(
            status_code=200,
            content={"ok": False, "error": {
                "code": "NOT_FOUND", "message": "PDF 不存在或已过期", "degraded": False}},
        )
    return FileResponse(pdf, media_type="application/pdf", filename=f"{compile_id}.pdf")


@router.get("/compile/{compile_id}/log", response_model=None)
async def download_compile_log(compile_id: str) -> PlainTextResponse | JSONResponse:
    cdir = _cid_dir(compile_id)
    log = cdir / "output.log" if cdir else None
    if log is None or not log.is_file():
        return JSONResponse(
            status_code=200,
            content={"ok": False, "error": {
                "code": "NOT_FOUND", "message": "日志不存在或已过期", "degraded": False}},
        )
    return PlainTextResponse(log.read_text("utf-8", errors="replace"))
