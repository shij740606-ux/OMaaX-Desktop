"""routers/reformat.py —— 论文自动排版 P1（.tex 源码级重排版，不编译）。

端点：
  POST /api/review/reformat          multipart 上传 .tex + target_journal →
                                     修改后源码 + 逐行 diff 报告（job 落盘）
  GET  /api/review/reformat/{id}     取完整报告（重启后仍在）
  GET  /api/review/reformat/{id}/file 下载修改后的 .tex

信封与其余 review 端点一致：业务错误 = HTTP 200 + {ok:false,error:{code,message,degraded:false}}，
前端经 review.js unwrap 归一为 ApiError。本次改动无上游依赖（零 LLM/零 vstore），
degraded 恒 false。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ..config import get_settings
from ..services.reformat import ReformatError, reformat_tex, resolve_rules

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/review", tags=["review-reformat"])

_JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_ENGINE = "reformat-p1/1.0"


class ReformatResponse(BaseModel):
    ok: bool
    job_id: str | None = None
    modified_tex: str | None = None
    changes: list[dict[str, Any]] | None = None
    warnings: list[dict[str, Any]] | None = None
    meta: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


def _error(code: str, message: str) -> ReformatResponse:
    return ReformatResponse(
        ok=False,
        error={"code": code, "message": message, "degraded": False},
    )


def _jobs_dir() -> Path:
    d = get_settings().reformat_dir_abs
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.post("/reformat", response_model=ReformatResponse)
async def reformat(
    file: UploadFile = File(..., description="LaTeX 源码（.tex 单文件）"),
    target_journal: str = Form("", description="目标期刊 id/名称，或 default=通用规则"),
) -> ReformatResponse:
    settings = get_settings()

    # ---- 入参校验（拍板 5：只收 .tex，docx/pdf 走 format-check 审核不重排）----
    filename = file.filename or "paper.tex"
    if not filename.lower().endswith(".tex"):
        return _error("VALIDATION", "仅支持 .tex 文件（docx/pdf 请走格式审核）")
    target = (target_journal or "").strip()
    if not target:
        return _error("VALIDATION", "必须指定目标期刊（或选择「通用规则」）")

    content = await file.read()
    if not content:
        return _error("VALIDATION", "上传文件为空")
    if len(content) > settings.max_tex_bytes:
        return _error("VALIDATION", "源码超过 2MB 上限")
    try:
        source = content.decode("utf-8")
    except UnicodeDecodeError:
        return _error("VALIDATION", "源码必须为 UTF-8 编码")

    # ---- 规则解析（journals.json / format_cache / journal_directory 只读回链）----
    resolved = await resolve_rules(target)

    # ---- 变换（纯规则引擎；结构畸形 → PARSER_FAILED）----
    try:
        result = reformat_tex(source, resolved["rules"])
    except ReformatError as exc:
        logger.warning("reformat: 结构守卫拒收 %s: %s", filename, exc)
        return _error(exc.code, str(exc))

    # ---- 落盘（.tex + .json 报告；重启后 GET 仍可用）----
    job_id = uuid.uuid4().hex[:12]
    base = _jobs_dir()
    (base / f"{job_id}.tex").write_text(result["modified_tex"], encoding="utf-8")

    meta: dict[str, Any] = {
        "journal_id": resolved["journal_id"],
        "journal_name": resolved["journal_name"],
        "rules": resolved["rules"],
        "rules_source": resolved["rules_source"],
        "rules_note": resolved["rules_note"],
        "filename": filename,
        "original_sha256": hashlib.sha256(content).hexdigest(),
        "engine": _ENGINE,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    payload = {
        "ok": True,
        "job_id": job_id,
        "modified_tex": result["modified_tex"],
        "changes": result["changes"],
        "warnings": result["warnings"],
        "meta": meta,
    }
    (base / f"{job_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "reformat: %s → job %s（%d 处修改, %d 条提示, 规则 %s）",
        filename, job_id,
        sum(1 for c in result["changes"] if c["action"] == "modified"),
        sum(1 for c in result["changes"] if c["action"] == "warning"),
        resolved["rules_source"],
    )
    return ReformatResponse(**payload)


def _load_job(job_id: str) -> dict[str, Any] | None:
    if not _JOB_ID_RE.fullmatch(job_id or ""):
        return None
    path = _jobs_dir() / f"{job_id}.json"
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) and payload.get("ok") else None


_NOT_FOUND = {"ok": False, "error": {"code": "NOT_FOUND",
                                     "message": "任务不存在或已过期，请重新上传源码",
                                     "degraded": False}}


@router.get("/reformat/{job_id}", response_model=ReformatResponse)
async def get_reformat_job(job_id: str) -> ReformatResponse:
    payload = _load_job(job_id)
    if payload is None:
        return _error("NOT_FOUND", "任务不存在或已过期，请重新上传源码")
    return ReformatResponse(**payload)


@router.get("/reformat/{job_id}/file", response_model=None)
async def download_reformat_file(job_id: str) -> FileResponse | JSONResponse:
    """下载修改后的 .tex；缺件/非法 id → 200 信封（前端单一错误口径）。"""
    valid = bool(_JOB_ID_RE.fullmatch(job_id or ""))
    tex = _jobs_dir() / f"{job_id}.tex" if valid else None
    if tex is None or not tex.is_file():
        return JSONResponse(status_code=200, content=_NOT_FOUND)
    return FileResponse(tex, media_type="application/x-tex", filename=f"{job_id}.tex")
