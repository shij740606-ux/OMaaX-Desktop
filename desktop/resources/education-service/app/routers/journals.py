"""routers/journals.py —— 期刊情报与智能投稿推荐。

- GET  /api/review/journals      只读：journals 表全量列表（?field=/&order= 排序），
  meta 含 count / last_updated / data_source_note。无任何写接口；空表返回
  空数组 + meta.count=0（HTTP 200，绝不 500）。
- POST /api/review/recommend     智能投稿推荐：vstore 向量匹配（摘要 vs
  scope_desc，白名单检索）→ LLM 按 id 白名单重排（幻觉期刊丢弃、缺漏规则
  补位）→ 三档输出：挑战型 / 适配型 / 稳妥型，附匹配理由与风险提示。

降级哲学（同 presubmit）：向量失败 → 全量候选 + vector_ok=false；
LLM 失败 → 规则分档 + llm_ok=false；推荐永不 5xx。仅入参非法返回
VALIDATION 错误信封（HTTP 200，与本服务其余端点一致）。
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..config import get_settings
from ..db import SeedError, ensure_journals_seeded, list_journals
from ..services import journal_kb, journal_rank, llm

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/review", tags=["review-journals"])


# ---- 请求/响应模型 ----

class Preferences(BaseModel):
    max_review_weeks: float | None = Field(default=None, ge=1, le=104)
    min_impact_factor: float | None = Field(default=None, ge=0, le=200)
    tier_focus: Literal["all", "challenge", "fit", "safe"] = "all"


class RecommendRequest(BaseModel):
    abstract: str
    preferences: Preferences = Preferences()


class JournalItem(BaseModel):
    id: str
    name: str
    impact_factor: float | None = None
    review_time_weeks: float | None = None
    acceptance_rate: float | None = None
    official_url: str | None = None
    similarity: float | None = None
    tier: str
    match_reason: str
    risk_note: str


class TierBox(BaseModel):
    label: str
    items: list[JournalItem]


class RecommendResponse(BaseModel):
    ok: bool
    tiers: dict[str, TierBox] | None = None
    meta: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


# ---- GET /api/review/journals（只读列表）----

class JournalBrief(BaseModel):
    """期刊公开字段（journals 表全列；不含 vstore_doc_id 内部映射）。

    标签/分区 6 字段不进 SQLite：GET 时按 id 从 journals.json 内存缓存合并；
    JSON 缺该 id → 空数组/null 默认值。
    """
    id: str
    name: str
    scope_desc: str = ""
    impact_factor: float | None = None
    review_time_weeks: float | None = None
    acceptance_rate: float | None = None
    submission_deadline: str | None = None
    official_url: str | None = None
    last_updated: str = ""
    scope_tags: list[str] = []
    application_tags: list[str] = []
    cas_partition: str | None = None
    jcr_quartile: str | None = None
    partition_source: str | None = None
    cross_domain: list[str] = []


class JournalsListResponse(BaseModel):
    ok: bool
    items: list[JournalBrief] = []
    meta: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


# ?field= 排序白名单（列名无法参数化，白名单防注入；非法值 → VALIDATION 信封）
_SORT_FIELDS = frozenset({
    "id",
    "name",
    "impact_factor",
    "review_time_weeks",
    "acceptance_rate",
    "last_updated",
})
_DATA_SOURCE_NOTE = (
    "IF/审稿周期为公开近似值，acceptance_rate 为 null 表示暂无公开数据，需人工核验。"
    "CAS/JCR 分区为首版占位数据，需核验。"
)

# journals.json 新增字段（标签/分区；刻意不进 SQLite，见 C-2 方案）
_TAG_FIELDS = (
    "scope_tags",
    "application_tags",
    "cas_partition",
    "jcr_quartile",
    "partition_source",
    "cross_domain",
)


@lru_cache(maxsize=1)
def _load_tag_extras() -> dict[str, dict[str, Any]]:
    """journals.json 标签/分区字段 → {id: {6 新字段}}。

    首次请求读盘后进程内缓存，后续 GET O(1) 按 id 合并（不做每请求磁盘 IO）。
    JSON 缺该 id → 合并时用空数组/null 默认；文件不可读 → 空 dict 降级（不 500）。
    """
    seed = get_settings().journals_seed_abs
    try:
        payload = json.loads(Path(seed).read_text("utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("journals: 标签扩展文件不可读，合并降级为默认值：%s", exc)
        return {}
    raw = payload.get("journals") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for j in raw:
        if isinstance(j, dict) and j.get("id"):
            out[str(j["id"])] = {k: j.get(k) for k in _TAG_FIELDS}
    return out


def _tag_fields(extra: dict[str, Any] | None) -> dict[str, Any]:
    """缓存条目 → JournalBrief 关键字参数（JSON 缺该 id → 空数组/null 默认）。"""
    ex = extra or {}
    return {
        "scope_tags": list(ex.get("scope_tags") or []),
        "application_tags": list(ex.get("application_tags") or []),
        "cas_partition": ex.get("cas_partition"),
        "jcr_quartile": ex.get("jcr_quartile"),
        "partition_source": ex.get("partition_source"),
        "cross_domain": list(ex.get("cross_domain") or []),
    }


def _sort_rows(rows: list[dict[str, Any]], field: str, order: str) -> list[dict[str, Any]]:
    """按白名单字段排序；NULL 恒排最后；次序不稳定时按 db 默认序（稳定排序保留）。"""
    present = [r for r in rows if r.get(field) is not None]
    missing = [r for r in rows if r.get(field) is None]
    present.sort(key=lambda r: r.get(field), reverse=(order == "desc"))
    return present + missing


def _list_error(message: str) -> JournalsListResponse:
    return JournalsListResponse(
        ok=False, items=[], meta=None,
        error={"code": "VALIDATION", "message": message, "degraded": False},
    )


@router.get("/journals", response_model=JournalsListResponse)
async def journals_list(
    field: str = "name",
    order: str = "asc",
) -> JournalsListResponse:
    """只读：journals 表全量列表（无写/删/改接口）。

    - ?field= 排序字段白名单：id/name/impact_factor/review_time_weeks/
      acceptance_rate/last_updated，默认 name；
    - ?order=asc|desc，默认 asc；
    - 空表 → items=[] + meta.count=0（HTTP 200，不报错）。种子装载由
      POST /recommend 首次调用幂等完成，本端点严格只读。
    """
    col = (field or "").strip()
    if col not in _SORT_FIELDS:
        return _list_error(
            f"不支持的排序字段 field={field!r}，可选：{', '.join(sorted(_SORT_FIELDS))}"
        )
    ord_norm = (order or "asc").strip().lower()
    if ord_norm not in ("asc", "desc"):
        return _list_error(f"不支持的排序方向 order={order!r}，可选：asc, desc")

    rows = await list_journals()
    rows = _sort_rows(rows, col, ord_norm)

    tag_extras = _load_tag_extras()
    items = [
        JournalBrief(
            id=str(r.get("id") or ""),
            name=str(r.get("name") or ""),
            scope_desc=str(r.get("scope_desc") or ""),
            impact_factor=r.get("impact_factor"),
            review_time_weeks=r.get("review_time_weeks"),
            acceptance_rate=r.get("acceptance_rate"),
            submission_deadline=r.get("submission_deadline"),
            official_url=r.get("official_url"),
            last_updated=str(r.get("last_updated") or ""),
            **_tag_fields(tag_extras.get(str(r.get("id") or ""))),
        )
        for r in rows
    ]
    latest = max((str(r.get("last_updated") or "") for r in rows), default="")
    meta: dict[str, Any] = {
        "count": len(items),
        "last_updated": latest or None,
        "data_source_note": _DATA_SOURCE_NOTE,
    }
    return JournalsListResponse(ok=True, items=items, meta=meta, error=None)


def _error(code: str, message: str, *, degraded: bool = False) -> RecommendResponse:
    return RecommendResponse(
        ok=False, tiers=None, meta=None,
        error={"code": code, "message": message, "degraded": degraded},
    )


def _item_sort_key(it: JournalItem) -> tuple[int, float, float]:
    return (
        1 if it.similarity is None else 0,
        -(it.similarity or 0.0),
        -(it.impact_factor or 0.0),
    )


def _apply_soft_preferences(item: JournalItem, prefs: Preferences) -> None:
    """软偏好：不满足只加风险提示，不删条目。"""
    weeks = item.review_time_weeks
    if prefs.max_review_weeks is not None and weeks is not None and weeks > prefs.max_review_weeks:
        prefix = f"⚠ 审稿约 {weeks:g} 周，超出你 {prefs.max_review_weeks:g} 周的周期偏好；"
        item.risk_note = prefix + ("无" if item.risk_note == "无" else item.risk_note)
    inf = item.impact_factor
    if prefs.min_impact_factor is not None:
        if inf is None:
            item.risk_note = "⚠ 影响因子未收录，无法确认满足你的要求；" + item.risk_note
        elif inf < prefs.min_impact_factor:
            prefix = f"⚠ 影响因子 {inf:g} 低于你 {prefs.min_impact_factor:g} 的要求；"
            item.risk_note = prefix + ("无" if item.risk_note == "无" else item.risk_note)


@router.post("/recommend", response_model=RecommendResponse)
async def recommend(req: RecommendRequest) -> RecommendResponse:
    settings = get_settings()

    # ---- 入参校验（手动，错误信封与本服务其余端点一致）----
    abstract = (req.abstract or "").strip()
    if len(abstract) < 50:
        return _error("VALIDATION", "abstract 至少需要 50 个字符（请提供摘要或正文片段）")
    if len(abstract) > 20000:
        return _error("VALIDATION", "abstract 超过 20000 字符上限")

    prefs = req.preferences

    # ---- 种子 + 期刊元数据 ----
    try:
        await ensure_journals_seeded()
        journals = await list_journals()
    except SeedError as exc:
        logger.error("recommend: 种子装载失败：%s", exc)
        return _error("SEED_UNAVAILABLE", str(exc), degraded=True)
    if not journals:
        return _error("SEED_UNAVAILABLE", "期刊库为空", degraded=True)

    # ---- ① vstore 向量匹配（摘要 vs scope_desc）----
    try:
        candidates, vector_ok = await journal_kb.match(abstract, journals)
    except journal_kb.MatchError as exc:
        logger.warning("recommend: 向量匹配降级：%s", exc)
        candidates, vector_ok = [], False
    if not candidates:
        # 无有效语义分 → 全量候选（similarity=None），LLM 仍可按 scope 文本重排
        candidates = [{"journal": j, "similarity": None} for j in journals]
        vector_ok = False

    # ---- ② LLM 重排（id 白名单硬校验）----
    prefs_dict = prefs.model_dump(exclude_none=True)
    llm_ok = True
    dropped = 0
    repaired = 0
    try:
        rankings, dropped, repaired = await journal_rank.rerank(abstract, candidates, prefs_dict)
    except (llm.LLMError, journal_rank.RankError, ValueError) as exc:
        # ValueError：complete_json 最终 json.loads 修复仍失败（同 presubmit 宽容口径）
        logger.warning("recommend: LLM 重排降级为规则分档：%s", exc)
        rankings = journal_rank.fallback_rankings(candidates)
        llm_ok = False

    # ---- ③ 三档组装（名称/指标全部来自 DB，不采信 LLM 文本）----
    by_id = {str(c["journal"]["id"]): c for c in candidates}
    tiers: dict[str, list[JournalItem]] = {t: [] for t in journal_rank.TIERS}
    for r in rankings:
        cand = by_id.get(r.journal_id)
        if cand is None:
            continue
        j = cand["journal"]
        sim = cand.get("similarity")
        item = JournalItem(
            id=str(j["id"]),
            name=str(j.get("name") or j["id"]),
            impact_factor=j.get("impact_factor"),
            review_time_weeks=j.get("review_time_weeks"),
            acceptance_rate=j.get("acceptance_rate"),
            official_url=j.get("official_url"),
            similarity=round(float(sim), 4) if sim is not None else None,
            tier=r.tier,
            match_reason=r.match_reason,
            risk_note=r.risk_note,
        )
        _apply_soft_preferences(item, prefs)
        tiers.setdefault(r.tier, []).append(item)

    # 档内排序：相似度（None 靠后）→ 影响因子
    for box in tiers.values():
        box.sort(key=_item_sort_key)

    # tier_focus：软展示聚焦（默认 all；聚焦时其余档 items 置空，label 保留）
    out_tiers: dict[str, TierBox] = {}
    for t in journal_rank.TIERS:
        items = tiers.get(t, [])
        if prefs.tier_focus != "all" and t != prefs.tier_focus:
            items = []
        out_tiers[t] = TierBox(label=journal_rank.TIER_LABELS[t], items=items)

    meta: dict[str, Any] = {
        "model": llm._llm_config()["model"],
        "vector_ok": vector_ok,
        "matching_method": "local_scopes_llm",
        "llm_ok": llm_ok,
        "seed_count": len(journals),
        "candidate_count": len(candidates),
        "dropped_hallucinated": dropped,
        "repaired": repaired,
        "preferences": prefs_dict,
        "abstract_chars": len(abstract),
    }
    return RecommendResponse(ok=True, tiers=out_tiers, meta=meta, error=None)
