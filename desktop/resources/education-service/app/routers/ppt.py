"""routers/ppt.py —— P5 科研汇报助手（9 端点，SQLite job 模式）。

信封与其余 review 端点一致：业务错误 = HTTP 200 + {ok:false,error:{code,message,degraded}}。
- GET /styles：风格清单（data/ppt_styles/*.json 实际数量 → 副标题/风格卡动态渲染）
- 创建（text 主入口 / pdf 次要入口）= 解析 + map-reduce 大纲生成（同步；
  map 并发 2 + 单调用超时收紧，总时长压在 BFF 读超时 360s 内）
- PATCH /jobs/{id}：结构清洗（复用 coerce_outline）+ evidence 状态按 raw_text
  重验（编辑后 quote 真伪即时刷新）；warnings 保留生成期记录（页数/拆页等
  结构提示不因编辑丢失）
- POST /jobs/{id}/build：python-pptx 本地生成 → data/ppt_jobs/{id}/draft.pptx
  + 逐页缩略图渲染 → preview/slide_{n}.png（失败仅 warning，不阻断下载）
- GET /jobs/{id}/pptx：FileResponse 附件下载
- GET /jobs/{id}/preview/{page}：第 page 页 PNG 缩略图（旧 job 缺图懒渲染）
- DELETE 手动清行 + 产物目录（无 TTL，对齐 rebuttal 决策 5）
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from ..config import get_settings
from ..services import paper_parse, ppt_build, ppt_outline, ppt_preview, ppt_store
from ..services.ppt_build import BuildError
from ..services.ppt_figure_index import extract_figures, scan_figure_index
from ..services.ppt_outline import (
    OutlineError,
    build_index,
    coerce_outline,
    renumber,
    repair_evidence_para_ids,
    validate_evidence,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/review/ppt", tags=["review-ppt"])

_MAX_RAW_CHARS = 500_000  # 粘贴文本上限（论文正文通常 ≤150K，留编辑余量）


# ---------- 信封 ----------
class PptResponse(BaseModel):
    ok: bool
    job: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None


def _error(code: str, message: str) -> PptResponse:
    return PptResponse(ok=False, error={"code": code, "message": message, "degraded": False})


def _ok(job: dict[str, Any], **meta: Any) -> PptResponse:
    return PptResponse(ok=True, job=job, meta=meta or None)


class StylesResponse(BaseModel):
    """GET /styles 信封（清单在 items，不用 job 字段）。"""

    ok: bool
    items: list[dict[str, Any]] = Field(default_factory=list)
    meta: dict[str, Any] | None = None


# ---------- 请求体 ----------
class JobCreateBody(BaseModel):
    text: str = Field(min_length=1)
    scenario: str = "group_meeting"
    minutes: int = 15
    style_id: str = "academic-minimal"


class PagesBody(BaseModel):
    pages: list[dict[str, Any]] = Field(min_length=1)


# ---------- 内部工具 ----------
def _pptx_path(job_id: str) -> Path:
    return get_settings().ppt_jobs_abs / job_id / "draft.pptx"


def _preview_dir(job_id: str) -> Path:
    return get_settings().ppt_jobs_abs / job_id / "preview"


def _preview_path(job_id: str, page: int) -> Path:
    return _preview_dir(job_id) / f"slide_{page:02d}.png"


async def _create_job(
    text: str, kind: str, filename: str | None,
    scenario: str, minutes: int, style_id: str,
    *, job_id: str | None = None, fig_meta: dict[str, Any] | None = None,
) -> PptResponse:
    if len(text) > _MAX_RAW_CHARS:
        return _error("VALIDATION", f"正文超过 {_MAX_RAW_CHARS} 字符上限")
    # v3 修复2：PDF 提取出的原图 label → 大纲生成只从这些图里选（build 必能嵌入）
    available_figures = None
    if fig_meta:
        fig_meta = dict(fig_meta)
        available_figures = fig_meta.pop("figure_labels", None) or None
    try:
        pages, warnings, meta = await ppt_outline.generate_outline(
            text, scenario, minutes, style_id,
            available_figures=available_figures,
        )
    except OutlineError as exc:
        return _error(exc.code, exc.message)
    job = {
        "id": job_id or ppt_store.new_id("ppt_"),
        "status": "outline_ready",
        "source": {"kind": kind, "filename": filename},
        "scenario": scenario,
        "minutes": minutes,
        "style_id": style_id,
        "pages": pages,
        "warnings": warnings,
        "pptx_path": None,
    }
    ppt_store.insert_job(job, raw_text=text)
    if fig_meta:
        meta = {**meta, **fig_meta}
    return _ok(ppt_store.get_job(job["id"]) or job, **meta)


def _extract_job_figures(pdf_bytes: bytes, job_id: str) -> dict[str, Any]:
    """2.3：PDF 原图 → data/ppt_jobs/{id}/figs/（失败仅告警，不阻塞建 job）。"""
    out_dir = get_settings().ppt_jobs_abs / job_id / "figs"
    try:
        figs = extract_figures(pdf_bytes, out_dir)
    except Exception as exc:  # noqa: BLE001 - 提取属增强项，绝不 500
        logger.warning("图表原图提取失败（该 job 回落文字占位）：%s", exc)
        return {"figures_extracted": 0, "figures_extract_error": str(exc)[:200]}
    # v3 修复2：已提取 label 供大纲只选「磁盘上有图」的条目
    return {
        "figures_extracted": len(figs),
        "figure_labels": [str(r.get("label") or "") for r in figs if r.get("label")],
    }


def _not_found(job_id: str) -> PptResponse:
    return _error("NOT_FOUND", f"job {job_id} 不存在或已删除")


# ---------- 端点 ----------
@router.get("/styles", response_model=StylesResponse)
async def get_styles() -> StylesResponse:
    """风格清单（data/ppt_styles/*.json 实际数量 + 文案；副标题/风格卡按它渲染）。"""
    items = ppt_build.list_styles()
    return StylesResponse(ok=True, items=items, meta={"count": len(items)})


@router.post("/jobs", response_model=PptResponse)
async def create_job(body: JobCreateBody) -> PptResponse:
    """文本粘贴主入口（论文纯文本 → 大纲）。"""
    return await _create_job(
        body.text, kind="text", filename=None,
        scenario=body.scenario, minutes=body.minutes, style_id=body.style_id,
    )


@router.post("/jobs/pdf", response_model=PptResponse)
async def create_job_pdf(
    file: UploadFile = File(...),
    scenario: str = Form("group_meeting"),
    minutes: int = Form(15),
    style_id: str = Form("academic-minimal"),
) -> PptResponse:
    """PDF 次要入口（经 vstore 8094 解析 → 大纲）。"""
    settings = get_settings()
    filename = file.filename or "paper.pdf"
    if not filename.lower().endswith(".pdf"):
        return _error("VALIDATION", "仅支持 PDF 文件")
    data = await file.read()
    if not data:
        return _error("VALIDATION", "上传文件为空")
    if len(data) > settings.max_pdf_bytes:
        return _error("VALIDATION", f"PDF 超过 {settings.max_pdf_bytes // (1024 * 1024)}MB 上限")
    try:
        text = await paper_parse.parse_pdf_bytes(data, filename=filename)
    except Exception as exc:  # noqa: BLE001 - ParseError/网络异常统一信封
        return _error("UPSTREAM_DOWN", f"PDF 解析失败：{exc}")
    # 2.3：先取 job_id 并提取原图（build 侧按 out 同级 figs/ 自动发现真图）
    job_id = ppt_store.new_id("ppt_")
    fig_meta = _extract_job_figures(data, job_id)
    return await _create_job(
        text, kind="pdf", filename=filename,
        scenario=scenario, minutes=minutes, style_id=style_id,
        job_id=job_id, fig_meta=fig_meta,
    )


@router.get("/jobs/{job_id}", response_model=PptResponse)
async def get_job(job_id: str) -> PptResponse:
    job = ppt_store.get_job(job_id)
    if job is None:
        return _not_found(job_id)
    return _ok(job)


@router.patch("/jobs/{job_id}", response_model=PptResponse)
async def patch_pages(job_id: str, body: PagesBody) -> PptResponse:
    """保存前端编辑后的完整 pages（结构清洗 + evidence 状态重验）。"""
    job = ppt_store.get_job(job_id)
    if job is None:
        return _not_found(job_id)
    try:
        pages = coerce_outline({"pages": body.pages})
    except OutlineError as exc:
        return _error("VALIDATION", exc.message)
    renumber(pages)
    raw_text = ppt_store.get_raw_text(job_id)
    if raw_text:
        _, index = build_index(raw_text)
        repair_evidence_para_ids(pages, index)   # 编辑保存时同样反查修正（兼容旧 job）
        validate_evidence(pages, index, scan_figure_index(index))
    updated = ppt_store.update_job(job_id, pages=pages, pptx_path=None)  # 编辑后旧 pptx 失效
    return _ok(updated or job)


@router.post("/jobs/{job_id}/build", response_model=PptResponse)
async def build_job(job_id: str) -> PptResponse:
    """大纲 → draft.pptx（python-pptx 本地生成，覆盖重建）。"""
    job = ppt_store.get_job(job_id)
    if job is None:
        return _not_found(job_id)
    if not job.get("pages"):
        return _error("VALIDATION", "大纲为空，无法生成 PPT")
    ppt_store.update_job(job_id, status="building")
    try:
        stats = ppt_build.build_pptx(
            job["pages"], job.get("style_id") or "academic-minimal",
            _pptx_path(job_id),
            context={"scenario": job.get("scenario"), "minutes": job.get("minutes")},
        )
    except BuildError as exc:
        warnings = list(job.get("warnings") or []) + [f"PPT 生成失败：{exc.message}"]
        ppt_store.update_job(job_id, status="error", warnings=warnings)
        fresh = ppt_store.get_job(job_id)
        return PptResponse(
            ok=False, job=fresh,
            error={"code": exc.code, "message": exc.message, "degraded": False},
        )
    # 浏览器内预览：build 成功即渲染缩略图；失败仅告警，不阻断下载
    previews = ppt_preview.render_previews(_pptx_path(job_id), _preview_dir(job_id))
    stats["previews"] = len(previews)
    updated = ppt_store.update_job(job_id, status="done", pptx_path=str(_pptx_path(job_id)))
    if not previews:
        fresh_warnings = list((updated or job).get("warnings") or []) + ["预览图生成失败（可正常下载 PPTX）"]
        updated = ppt_store.update_job(job_id, warnings=fresh_warnings) or updated
    return _ok(updated or job, **stats)


@router.get("/jobs/{job_id}/pptx", response_model=None)
async def download_pptx(job_id: str) -> FileResponse | JSONResponse:
    """下载 draft.pptx（二进制流；缺失时回同款信封 JSON，对齐 compile 下载端点）。"""
    job = ppt_store.get_job(job_id)
    # pptx_ready 同时看 DB 列与磁盘文件：编辑后列被清 → 盘上旧文件不再下发
    if job is None or not job.get("pptx_ready"):
        resp = _not_found(job_id)
        return JSONResponse(status_code=200, content=resp.model_dump())
    path = _pptx_path(job_id)
    filename = path.name
    src_name = (job.get("source") or {}).get("filename")
    if src_name:
        filename = Path(src_name).stem + ".pptx"
    return FileResponse(
        str(path),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=filename,
    )


@router.get("/jobs/{job_id}/preview/{page}", response_model=None)
async def preview_slide(job_id: str, page: int) -> FileResponse | JSONResponse:
    """第 page 页缩略图 PNG（1-based；对齐 download_pptx 的信封约定）。

    缺 preview 但 pptx 在盘（旧 job，端点上线前构建的）→ 懒渲染一次再回图。
    job 不存在 / pptx 未就绪 / 页码越界 → HTTP 200 + 信封 JSON（对齐 pptx 下载）。
    """
    job = ppt_store.get_job(job_id)
    if job is None or not job.get("pptx_ready"):
        return JSONResponse(status_code=200, content=_not_found(job_id).model_dump())
    png = _preview_path(job_id, page)
    if not png.is_file():
        # 懒渲染：首次访问补齐（渲染后仍不在 → 页码越界）
        written = ppt_preview.render_previews(_pptx_path(job_id), _preview_dir(job_id))
        png = _preview_path(job_id, page)
        if not png.is_file():
            if page < 1:
                return JSONResponse(
                    status_code=200,
                    content=_error("VALIDATION", "page 必须 ≥1").model_dump(),
                )
            return JSONResponse(
                status_code=200,
                content=_error("NOT_FOUND", f"page {page} 不存在（共 {len(written)} 页）").model_dump(),
            )
    return FileResponse(str(png), media_type="image/png")


@router.delete("/jobs/{job_id}", response_model=PptResponse)
async def delete_job(job_id: str) -> PptResponse:
    job = ppt_store.get_job(job_id)
    if job is None:
        return _not_found(job_id)
    ppt_store.delete_job(job_id)
    job_dir = get_settings().ppt_jobs_abs / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
    return PptResponse(ok=True, meta={"deleted": job_id})
