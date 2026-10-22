"""routers/conferences.py —— 会议投稿时间线（只读静态种子）。

- GET /api/review/conferences?domain=&ccf=&upcoming_days=180
  data/journals/conferences.json → 过滤/排序 → 列表 + meta。
- 数据诚实性铁律：每条会议必须带 deadline_source ∈ {placeholder, official}、
  last_verified 与 deadline_confidence ∈ {high, medium, low}，缺失/非法条目加载时
  跳过并记日志（宁缺毋造）；本端点无任何生成日期的逻辑，日期只来自种子常量。
  v2 共 27/27 全 placeholder，meta.data_source_note 显式声明占位与置信度，
  由前端渲染「⚠ 日期待核验」及置信度提示。
- 过滤语义：domain 对 domain_tags 精确匹配（大小写不敏感）；ccf ∈ {A,B,C}；
  upcoming_days=0 不过滤，>0 仅保留 deadline_full_paper ∈ [UTC now, now+N 天]，
  缺失/不可解析截稿日期在过滤开启时排除。排序：deadline_full_paper 升序，
  缺失/不可解析恒排最后。
- 只读无写接口；文件不可读 → ok=false + DATA_UNAVAILABLE 信封；参数非法 →
  VALIDATION 信封（均 HTTP 200，对齐本服务"错误信封永远 200"约定）。
- 静态文件首次请求加载进内存（lru_cache），进程内 O(1)；不触碰 SQLite/BFF。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/review", tags=["review-conferences"])

# review_service/app/routers/ → parents[2] = review_service（对齐 format_check 写法）
_CONFERENCES_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "journals" / "conferences.json"
)

_CCF_OK = frozenset({"A", "B", "C"})
_REQUIRED = ("id", "name", "deadline_source", "last_verified", "deadline_confidence")
_CONFIDENCE_OK = frozenset({"high", "medium", "low"})
_SOURCE_NOTE = (
    "会议截稿/通知日期首版均为占位数据（deadline_source=placeholder），"
    "接入官方渠道前须人工核验；deadline_confidence 标注日期置信度"
    "（high=官方已公布/历年极稳定，medium=按历年规律推算，low=待核验）；"
    "所有时间按 AoE (UTC-12) 时区。"
)


# ---- 请求/响应模型 ----

class ConferenceItem(BaseModel):
    """会议公开字段（种子全字段白名单；未知键丢弃）。"""
    id: str
    name: str
    full_name: str = ""
    domain_tags: list[str] = []
    application_tags: list[str] = []
    ccf_rank: str | None = None
    deadline_abstract: str | None = None
    deadline_full_paper: str | None = None
    notification_date: str | None = None
    conference_date: str | None = None
    location: str | None = None
    official_url: str | None = None
    timezone_note: str | None = None
    deadline_source: str
    last_verified: str
    deadline_confidence: str = ""


class ConferencesResponse(BaseModel):
    ok: bool
    items: list[ConferenceItem] = []
    meta: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


def _error(code: str, message: str) -> ConferencesResponse:
    return ConferencesResponse(
        ok=False, items=[], meta=None,
        error={"code": code, "message": message, "degraded": False},
    )


# ---- 种子加载（首次调用读盘，进程内缓存）----

@lru_cache(maxsize=1)
def _load_conferences() -> list[dict[str, Any]] | None:
    """读 conferences.json → 合法会议列表；文件不可读 → None（DATA_UNAVAILABLE）。"""
    try:
        payload = json.loads(_CONFERENCES_PATH.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("conferences: 种子文件不可读 %s: %s", _CONFERENCES_PATH, exc)
        return None
    raw = payload.get("conferences") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        logger.warning("conferences: 种子缺少 conferences 数组：%s", _CONFERENCES_PATH)
        return None
    items: list[dict[str, Any]] = []
    for conf in raw:
        if not isinstance(conf, dict):
            continue
        missing = [k for k in _REQUIRED if not conf.get(k)]
        if missing:
            logger.warning("conferences: 条目缺字段 %s（id=%s），跳过", missing, conf.get("id"))
            continue
        if conf.get("deadline_source") not in ("placeholder", "official"):
            logger.warning(
                "conferences: deadline_source 非法（id=%s），跳过", conf.get("id")
            )
            continue
        if str(conf.get("deadline_confidence") or "").strip().lower() not in _CONFIDENCE_OK:
            logger.warning(
                "conferences: deadline_confidence 非法（id=%s），跳过", conf.get("id")
            )
            continue
        items.append(conf)
    return items


def _parse_deadline(value: Any) -> datetime | None:
    """截稿时间 → aware datetime（naive 视为 UTC）；非法 → None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# ---- GET /api/review/conferences ----

@router.get("/conferences", response_model=ConferencesResponse)
async def conferences_list(
    domain: str = "",
    ccf: str = "",
    upcoming_days: int = 180,
) -> ConferencesResponse:
    """只读：会议投稿时间线（无写/删/改接口）。

    - ?domain= 对 domain_tags 精确匹配（大小写不敏感），空=不过滤；
    - ?ccf= ccf_rank ∈ A/B/C，空=不过滤，非法值 → VALIDATION 信封；
    - ?upcoming_days= int≥0，默认 180；0=不过滤，>0 仅保留截稿在
      [UTC now, now+N 天] 内的会议，负数 → VALIDATION 信封；
    - 排序按 deadline_full_paper 升序，缺失/不可解析恒排最后；
    - 空结果 → items=[] + meta.count=0（HTTP 200，不报错）。
    """
    ccf_norm = (ccf or "").strip().upper()
    if ccf_norm and ccf_norm not in _CCF_OK:
        return _error("VALIDATION", f"不支持的 ccf={ccf!r}，可选：A / B / C")
    if upcoming_days < 0:
        return _error(
            "VALIDATION", f"upcoming_days 必须 ≥ 0，收到 {upcoming_days}"
        )

    all_items = _load_conferences()
    if all_items is None:
        return _error("DATA_UNAVAILABLE", "会议种子数据不可读，请联系管理员")

    items = list(all_items)

    dom = (domain or "").strip().casefold()
    if dom:
        items = [
            c for c in items
            if any(
                str(t).strip().casefold() == dom
                for t in (c.get("domain_tags") or [])
            )
        ]
    if ccf_norm:
        items = [
            c for c in items
            if str(c.get("ccf_rank") or "").strip().upper() == ccf_norm
        ]
    if upcoming_days > 0:
        now = datetime.now(timezone.utc)
        limit = now + timedelta(days=upcoming_days)
        items = [
            c for c in items
            if (dl := _parse_deadline(c.get("deadline_full_paper"))) is not None
            and now <= dl <= limit
        ]

    def _sort_key(c: dict[str, Any]) -> tuple[bool, datetime]:
        dl = _parse_deadline(c.get("deadline_full_paper"))
        if dl is None:
            return (True, datetime.max.replace(tzinfo=timezone.utc))
        return (False, dl)

    items.sort(key=_sort_key)

    out = [ConferenceItem(**c) for c in items]
    verified = max(
        (str(c.get("last_verified") or "") for c in all_items), default=""
    )
    meta: dict[str, Any] = {
        "count": len(out),
        "last_verified": verified or None,
        "data_source_note": _SOURCE_NOTE,
        "query": {
            "domain": (domain or "").strip(),
            "ccf": ccf_norm,
            "upcoming_days": upcoming_days,
        },
    }
    return ConferencesResponse(ok=True, items=out, meta=meta, error=None)
