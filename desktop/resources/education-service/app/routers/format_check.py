"""routers/format_check.py —— 版式与规范检查（POST /api/review/format-check）。

纯规则引擎（services/format_check.py）：PDF/Word 本地解档取版式元数据 +
journals.json 规则集（format_rules_default + 每条 format_rules 带 rules_source）。
无 LLM / 无 vstore，同步执行通常 <1s；恒 200 + {ok, checks, summary, meta|error} 信封。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel

from ..config import get_settings
from ..services import format_check

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/review", tags=["review-format"])


class CheckItem(BaseModel):
    key: str
    name: str
    status: str  # pass | warn | fail
    detail: str
    suggestion: str = ""


class FormatCheckResponse(BaseModel):
    ok: bool
    checks: list[CheckItem] | None = None
    summary: dict[str, int] | None = None
    meta: dict[str, object] | None = None
    error: dict[str, object] | None = None


def _error(code: str, message: str, *, degraded: bool = False) -> FormatCheckResponse:
    return FormatCheckResponse(
        ok=False, error={"code": code, "message": message, "degraded": degraded},
    )


@router.post("/format-check", response_model=FormatCheckResponse)
async def format_check_endpoint(
    file: UploadFile = File(..., description="待检查的 PDF / Word(.docx) / LaTeX(.tex) 文档"),
    target_journal: str = Form("", description="目标期刊（可选，传 journals 的 id）"),
) -> FormatCheckResponse:
    settings = get_settings()

    filename = file.filename or ""
    low = filename.lower()
    if low.endswith(".pdf"):
        kind = "pdf"
    elif low.endswith(".docx"):
        kind = "docx"
    elif low.endswith(".tex"):
        kind = "tex"
    else:
        return _error("VALIDATION", "仅支持 PDF / Word（.docx）/ LaTeX（.tex）文件")

    content = await file.read()
    if not content:
        return _error("VALIDATION", "上传文件为空")
    if len(content) > settings.max_pdf_bytes:
        return _error("VALIDATION", "上传文件超过 50MB 上限")

    try:
        result = format_check.check_document(content, kind, (target_journal or "").strip())
    except format_check.FormatCheckError as exc:
        logger.warning("format-check: 解档失败：%s", exc)
        return _error("PARSE_FAILED", str(exc))

    meta = dict(result.get("meta") or {})
    meta["filename"] = filename
    return FormatCheckResponse(
        ok=True,
        checks=[CheckItem(**c) for c in result["checks"]],
        summary=result["summary"],
        meta=meta,
    )
