"""routers/rebuttal.py —— Rebuttal Phase 1（审稿回复助手，10 端点）。

信封与其余 review 端点一致：业务错误 = HTTP 200 + {ok:false,error:{code,message,degraded}}。
- 文本粘贴主入口 / PDF 次要入口（PDF 经 vstore 8094 解析）；
- 逐条 response → draft（校验C前置）→ confirm（后端重算 human_ack）→ letter 拼装；
- letter.md 下载门槛 = human_ack；DELETE 手动清（无 TTL，决策 5）。
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ..config import get_settings
from ..services import llm, paper_parse
from ..services.rebuttal_draft import PART_TYPES, assemble_letter, generate_draft, validate_parts
from ..services.rebuttal_parse import parse_review_text
from ..services import rebuttal_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/review/rebuttal", tags=["review-rebuttal"])

_MAX_RAW_CHARS = 200_000  # 粘贴文本上限（审稿意见通常 <20K，保守给大）


# ---------- 信封 ----------
class RebuttalResponse(BaseModel):
    ok: bool
    job: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None


def _error(code: str, message: str) -> RebuttalResponse:
    return RebuttalResponse(ok=False, error={"code": code, "message": message, "degraded": False})


def _ok(job: dict[str, Any], **meta: Any) -> RebuttalResponse:
    return RebuttalResponse(ok=True, job=job, meta=meta or None)


# ---------- 请求体 ----------
class JobCreateBody(BaseModel):
    text: str = Field(min_length=1)


class PartBody(BaseModel):
    type: Literal["new_data", "reanalysis", "location", "clarification", "rebuttal"]
    content: str = ""
    location_hint: str | None = None


class ResponseBody(BaseModel):
    parts: list[PartBody] = Field(default_factory=list)


class StructureBody(BaseModel):
    action: Literal["merge", "split"]
    # merge: comment_ids（含当前 cid，≥2 条）；split: items [{title, body}]
    comment_ids: list[str] | None = None
    items: list[dict[str, str]] | None = None


class ConfirmBody(BaseModel):
    confirmed: bool


# ---------- 内部工具 ----------
async def _messages_llm(messages: list[dict[str, str]], *, temperature: float = 0.3) -> dict[str, Any]:
    """适配 rebuttal_parse/draft 的 messages 形态 → llm.complete_json。"""
    system = next((m["content"] for m in messages if m.get("role") == "system"), "")
    user = next((m["content"] for m in messages if m.get("role") == "user"), "")
    return await llm.complete_json(system, user, temperature=temperature)


def _find_comment(job: dict[str, Any], cid: str) -> tuple[dict, dict] | None:
    for r in job["reviewers"]:
        for c in r["comments"]:
            if c["id"] == cid:
                return r, c
    return None


async def _build_job(raw_text: str, *, kind: str, filename: str | None) -> RebuttalResponse:
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return _error("VALIDATION", "审稿意见文本为空")
    if len(raw_text) > _MAX_RAW_CHARS:
        return _error("VALIDATION", f"文本超过 {_MAX_RAW_CHARS} 字符上限")
    reviewers = await parse_review_text(raw_text, _messages_llm)
    if not reviewers:
        return _error("PARSE_FAILED", "未能从文本中解析出任何审稿意见条目")
    job = {
        "id": rebuttal_store.new_id("rbt_"),
        "status": "ready",
        "source": {"kind": kind, "filename": filename, "raw_text": raw_text},
        "template": "generic",
        "reviewers": reviewers,
        "letter_md": None,
        "validation": None,
        "human_ack": False,
    }
    rebuttal_store.insert_job(job)
    n_comments = sum(len(r["comments"]) for r in reviewers)
    return _ok(job, reviewers=len(reviewers), comments=n_comments)


# ---------- 端点 ----------
@router.post("/jobs", response_model=RebuttalResponse)
async def create_job(body: JobCreateBody) -> RebuttalResponse:
    """文本粘贴主入口。"""
    return await _build_job(body.text, kind="text", filename=None)


@router.post("/jobs/pdf", response_model=RebuttalResponse)
async def create_job_pdf(file: UploadFile = File(...)) -> RebuttalResponse:
    """PDF 次要入口（批注版审稿 PDF；经 vstore 8094 解析）。"""
    settings = get_settings()
    filename = file.filename or "review.pdf"
    if not filename.lower().endswith(".pdf"):
        return _error("VALIDATION", "仅支持 PDF 文件（推荐直接粘贴文本）")
    data = await file.read()
    if not data:
        return _error("VALIDATION", "上传文件为空")
    if len(data) > settings.max_pdf_bytes:
        return _error("VALIDATION", f"PDF 超过 {settings.max_pdf_bytes // (1024 * 1024)}MB 上限")
    try:
        text = await paper_parse.parse_pdf_bytes(data, filename=filename)
    except Exception as exc:  # noqa: BLE001
        return _error("UPSTREAM_DOWN", f"PDF 解析失败：{exc}")
    return await _build_job(text, kind="pdf", filename=filename)


@router.get("/jobs/{job_id}", response_model=RebuttalResponse)
async def get_job(job_id: str) -> RebuttalResponse:
    job = rebuttal_store.get_job(job_id)
    if job is None:
        return _error("NOT_FOUND", f"job {job_id} 不存在或已删除")
    return _ok(job)


@router.patch("/jobs/{job_id}/comments/{cid}/response", response_model=RebuttalResponse)
async def patch_response(job_id: str, cid: str, body: ResponseBody) -> RebuttalResponse:
    job = rebuttal_store.get_job(job_id)
    if job is None:
        return _error("NOT_FOUND", f"job {job_id} 不存在或已删除")
    found = _find_comment(job, cid)
    if found is None:
        return _error("NOT_FOUND", f"comment {cid} 不存在")
    _, comment = found
    for p in body.parts:
        if p.type not in PART_TYPES:
            return _error("VALIDATION", f"未知 part.type: {p.type}")
    comment["response"] = {"parts": [p.model_dump() for p in body.parts]}
    # 改回复 → 旧 draft/validation 作废（保守：不保留过期草稿）
    comment["draft"] = None
    comment["validation"] = None
    job = rebuttal_store.update_job(job_id, reviewers=job["reviewers"]) or job
    return _ok(job)


@router.post("/jobs/{job_id}/comments/{cid}/draft", response_model=RebuttalResponse)
async def post_draft(job_id: str, cid: str) -> RebuttalResponse:
    job = rebuttal_store.get_job(job_id)
    if job is None:
        return _error("NOT_FOUND", f"job {job_id} 不存在或已删除")
    found = _find_comment(job, cid)
    if found is None:
        return _error("NOT_FOUND", f"comment {cid} 不存在")
    _, comment = found
    parts = (comment.get("response") or {}).get("parts") or []
    if not parts or not any(str(p.get("content") or "").strip() for p in parts):
        # 决策 4：生成前必须填 user_response；LLM 调用次数=0
        return _error("MISSING_RESPONSE", "请先填写回复要点（user_response）再生成草稿")
    validation = validate_parts(parts)
    comment["validation"] = validation
    if not validation["passed"]:
        job = rebuttal_store.update_job(job_id, reviewers=job["reviewers"]) or job
        return RebuttalResponse(
            ok=False, job=job,
            error={"code": "VALIDATION_FAILED", "message": "反幻觉校验未通过",
                   "degraded": False},
            meta={"validation": validation},
        )
    comment["draft"] = await generate_draft(_messages_llm, comment)
    job = rebuttal_store.update_job(job_id, reviewers=job["reviewers"]) or job
    return _ok(job, validation=validation)


@router.patch("/jobs/{job_id}/comments/{cid}/structure", response_model=RebuttalResponse)
async def patch_structure(job_id: str, cid: str, body: StructureBody) -> RebuttalResponse:
    job = rebuttal_store.get_job(job_id)
    if job is None:
        return _error("NOT_FOUND", f"job {job_id} 不存在或已删除")
    found = _find_comment(job, cid)
    if found is None:
        return _error("NOT_FOUND", f"comment {cid} 不存在")
    reviewer, comment = found
    comments = reviewer["comments"]
    idx = comments.index(comment)
    if body.action == "merge":
        ids = body.comment_ids or []
        if cid not in ids:
            ids = [cid] + ids
        if len(ids) < 2:
            return _error("VALIDATION", "merge 至少需要 2 条 comment")
        targets = [c for c in comments if c["id"] in ids]
        if len(targets) < 2:
            return _error("VALIDATION", "merge 目标中有效 comment 不足 2 条")
        merged_body = "\n\n".join(c["body"] for c in targets)
        comment["body"] = merged_body
        comment["title"] = f"{comment['title']} (+{len(targets) - 1} merged)"
        reviewer["comments"] = [
            c for c in comments
            if c is comment or c["id"] not in ids
        ]
    else:  # split
        items = body.items or []
        items = [i for i in items if str(i.get("body") or "").strip()]
        if len(items) < 2:
            return _error("VALIDATION", "split 至少需要 2 个非空子项")
        group_id = comment.get("group_id") or f"g_{cid}"
        new_comments = []
        for n, it in enumerate(items, start=1):
            c = dict(comment)
            c["id"] = rebuttal_store.new_id(f"{cid}s{n}")
            c["group_id"] = group_id
            c["title"] = str(it.get("title") or f"{comment['title']} ({n})")
            c["body"] = str(it.get("body") or "").strip()
            c["sub_items"] = []
            c["response"] = {"parts": []}
            c["draft"] = None
            c["validation"] = None
            c["human_confirmed"] = False
            new_comments.append(c)
        comments[idx:idx + 1] = new_comments
    job = rebuttal_store.update_job(job_id, reviewers=job["reviewers"]) or job
    return _ok(job)


@router.patch("/jobs/{job_id}/comments/{cid}/confirm", response_model=RebuttalResponse)
async def patch_confirm(job_id: str, cid: str, body: ConfirmBody) -> RebuttalResponse:
    job = rebuttal_store.get_job(job_id)
    if job is None:
        return _error("NOT_FOUND", f"job {job_id} 不存在或已删除")
    found = _find_comment(job, cid)
    if found is None:
        return _error("NOT_FOUND", f"comment {cid} 不存在")
    _, comment = found
    comment["human_confirmed"] = bool(body.confirmed)
    # human_ack 由 store 重算（决策 4：全勾才置位，不信前端）
    job = rebuttal_store.update_job(job_id, reviewers=job["reviewers"]) or job
    return _ok(job, human_ack=job["human_ack"])


@router.post("/jobs/{job_id}/letter", response_model=RebuttalResponse)
async def post_letter(job_id: str) -> RebuttalResponse:
    job = rebuttal_store.get_job(job_id)
    if job is None:
        return _error("NOT_FOUND", f"job {job_id} 不存在或已删除")
    job = rebuttal_store.update_job(job_id, letter_md=assemble_letter(job)) or job
    return _ok(job)


@router.get("/jobs/{job_id}/letter.md")
async def download_letter(job_id: str) -> PlainTextResponse:
    job = rebuttal_store.get_job(job_id)
    if job is None:
        return PlainTextResponse(
            "job not found", status_code=404, media_type="text/plain; charset=utf-8")
    if not job.get("human_ack"):
        # 信封口径与下载端点适配：GET 下载返回纯文本，业务拒绝用 400 + 明文
        return PlainTextResponse(
            "HUMAN_ACK_REQUIRED: 请逐条勾选确认后再下载 letter.md",
            status_code=400, media_type="text/plain; charset=utf-8")
    md = job.get("letter_md") or assemble_letter(job)
    return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")


@router.delete("/jobs/{job_id}", response_model=RebuttalResponse)
async def delete_job(job_id: str) -> RebuttalResponse:
    if not rebuttal_store.delete_job(job_id):
        return _error("NOT_FOUND", f"job {job_id} 不存在或已删除")
    return RebuttalResponse(ok=True, job=None, meta={"deleted": job_id})
