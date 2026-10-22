"""routers/journal_directory.py —— 期刊目录浏览（1056 本，只读分页）。

- GET /api/review/journals/directory：journal_directory 表（scripts/import_journals.py
  导入的 v4 目录：2025 中科院分区 + 2025 JIF（2026 年 JCR 发布）+ 年报 2025 版
  （中文统计年 2024）+ 官网网址/核对状态）→ 过滤/排序/分页 → 列表 + meta。
- 与 GET /journals（journals.json 精选 5 本，供 FormatPanel 下拉/recommend 语义）物理
  分离（方案 A）；本端点绝不读写 journals 表，recommend/journal_kb/journal_rank 零触碰。
- 读路径只 SELECT；表由导入脚本拥有（db.py 零改动）；表不存在 → data_imported=false
  优雅降级（HTTP 200 空列表）。

读时归一化（拍板细节 1-3，均在 API 层做，不动脚本/DB 原值）：
  细节1 lang=zh → cas_partition 恒 null + cas_partition_note="不按中科院分区口径列示"；
  细节2 jif_note 源文本映射（v4 值域已是用户可读文本，旧值映射保留兜底）；
  细节3 缺值即矛盾清零：en 且 jif=null → jif_source_url=null。
        （zh 的 IF 载体是 composite/comprehensive，其 jif_source_url 本恒 null。）
  决策2：响应不下发"来源"文本字段（partition_source/jif_source/data_source_label
        已移除）；jif_source_url/data_source_url 保留供展开行链接。
  决策4（来源展示优化）：partition_source_url 不再下发——DB 原值分毫未动
        （仍 SELECT * 读出后被模型丢弃），响应改为常量字段 partition_source_note
        文案，前端以灰色说明文字替代原"分区来源"超链接。
        新增 official_check_status / jcr_release_year。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from ..db import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/review", tags=["review-directory"])

_LANGS = frozenset({"en", "zh"})
# 排序列白名单（列名不可参数化 → 白名单防注入）
_FIELDS = frozenset({
    "name",
    "jif",
    "composite_impact_factor",
    "comprehensive_impact_factor",
    "research_direction",
    "cas_partition",
})
_ORDERS = frozenset({"asc", "desc"})
_PARTS = {"1": "1区", "2": "2区", "3": "3区", "4": "4区"}
_MAX_SIZE = 200

# 细节2：源核对状态 → 用户可读 note
_JIF_NOTE_MAP = {
    "源表标N/A": "未被2024 JCR收录",
    "所用年度来源未检得": "本次汇编未查到，建议查阅最新 JCR",
}
_ZH_PART_NOTE = "不按中科院分区口径列示"
# 决策4：分区来源改为常量说明文案（不下发 partition_source_url；DB 原值不动）
_PARTITION_SOURCE_NOTE = (
    "2025 中科院官方表（2026 年起停更；官方入口 fenqubiao.com 需机构登录）"
)

_NOTE_EN = (
    "分区：2025年中科院公开表（多表比对，2026年起停止更新）；"
    "JIF：2025年度指标（2026年JCR发布，两份公开汇编一致或已核对出版社官方指标）；"
    "整理日期2026-10-10。空白=来源未给值≠0；研究方向/期刊标记/文章类型为导航标签，非官方评级。"
)
_NOTE_ZH = (
    "指标：国内自然科学期刊年报2025版（统计年2024，official，含复合/综合IF两口径）；"
    "中文期刊不按中科院分区口径列示；国内IF与JIF统计源不同，不可混排。"
)
_UNIMPORTED_NOTE = "目录数据未导入（请先运行 scripts/import_journals.py）"


# ---- 响应模型 ----

class DirectoryItem(BaseModel):
    """目录公开字段（DB 全列白名单；imported_at 等内部列丢弃）。"""
    id: str
    lang: str
    name: str
    research_direction: str | None = None
    cas_partition: str | None = None
    cas_partition_note: str | None = None
    cas_partition_year: int | None = None
    jif: float | None = None
    jif_year: int | None = None
    jif_note: str | None = None
    jif_check_status: str | None = None
    composite_impact_factor: float | None = None
    comprehensive_impact_factor: float | None = None
    metric_year: int | None = None
    metric_report_year: int | None = None
    metric_caliber: str | None = None
    annual_report_page: int | None = None
    top_flag: str | None = None
    publisher_series: str | None = None
    journal_mark: str | None = None
    article_type: str | None = None
    issn: str | None = None
    eissn: str | None = None
    jcr_disciplines: list[str] = []
    supplement_note: str | None = None
    evidence_note: str | None = None
    official_url: str | None = None
    official_check_status: str | None = None
    jcr_release_year: int | None = None
    jif_source_url: str | None = None
    partition_source_note: str = _PARTITION_SOURCE_NOTE  # 决策4：常量，替代分区来源链接
    data_source_url: str | None = None


class DirectoryResponse(BaseModel):
    ok: bool
    items: list[DirectoryItem] = []
    meta: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


def _error(code: str, message: str) -> DirectoryResponse:
    return DirectoryResponse(
        ok=False, items=[], meta=None,
        error={"code": code, "message": message, "degraded": False},
    )


# ---- 读时归一化（拍板细节 1-3）----

def _present(raw: dict[str, Any], lang: str) -> dict[str, Any]:
    d = dict(raw)
    # jcr_disciplines：JSON 数组字符串 → list
    jcr = d.get("jcr_disciplines")
    if jcr:
        try:
            parsed = json.loads(jcr)
            d["jcr_disciplines"] = parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            d["jcr_disciplines"] = []
    else:
        d["jcr_disciplines"] = []

    # 细节2：jif_note 源文本 → 用户可读
    note = d.get("jif_note")
    if note in _JIF_NOTE_MAP:
        d["jif_note"] = _JIF_NOTE_MAP[note]

    # 细节1：zh 分区归一（DB 原值"不按此口径列示"只留在库里）
    if lang == "zh":
        d["cas_partition"] = None
        d["cas_partition_note"] = _ZH_PART_NOTE

    # 细节3：缺值 → 来源链接清零（消除 null 值却挂来源链接的矛盾；
    #          分区来源链接已按决策4 移出响应，改为常量 partition_source_note）
    if lang == "en":
        if d.get("jif") is None:
            d["jif_source_url"] = None
    return d


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---- GET /api/review/journals/directory ----

@router.get("/journals/directory", response_model=DirectoryResponse)
async def journals_directory(
    lang: str = "en",
    domain: str = "",
    cas_partition: str = "",
    q: str = "",
    page: int = 1,
    size: int = 50,
    field: str = "",
    order: str = "",
) -> DirectoryResponse:
    """只读：期刊目录分页浏览（无写接口）。

    - ?lang=en|zh（默认 en；**强制单语，保证国际/中文永不混排**）；
    - ?domain= 研究方向精确匹配；?cas_partition=1|2|3|4（仅 lang=en，zh 忽略）；
    - ?q= 期刊名模糊（ASCII 大小写不敏感）；?page≥1 ?size 1-200（默认 50）；
    - ?field= 排序白名单（name/jif/composite_impact_factor/
      comprehensive_impact_factor/research_direction/cas_partition），?order=asc|desc；
      未传 field → 默认排序 en=jif 降序 / zh=复合IF 降序（NULL 恒排最后）；
    - 参数非法 → VALIDATION 信封；表不存在 → data_imported=false 空列表（HTTP 200）。
    """
    lang_n = (lang or "").strip().lower()
    if lang_n not in _LANGS:
        return _error("VALIDATION", f"不支持的 lang={lang!r}，可选：en / zh")
    if page < 1:
        return _error("VALIDATION", f"page 必须 ≥ 1，收到 {page}")
    if size < 1 or size > _MAX_SIZE:
        return _error("VALIDATION", f"size 必须在 1-{_MAX_SIZE}，收到 {size}")
    field_n = (field or "").strip()
    if field_n and field_n not in _FIELDS:
        return _error(
            "VALIDATION",
            f"不支持的 field={field!r}，可选：{', '.join(sorted(_FIELDS))}",
        )
    order_n = (order or "").strip().lower()
    if order_n and order_n not in _ORDERS:
        return _error("VALIDATION", f"不支持的 order={order!r}，可选：asc / desc")
    part_q = (cas_partition or "").strip()
    if part_q:
        part_q = _PARTS.get(part_q, _PARTS.get(part_q.rstrip("区").strip(), ""))
        if not part_q:
            return _error(
                "VALIDATION",
                f"不支持的 cas_partition={cas_partition!r}，可选：1 / 2 / 3 / 4",
            )

    # 过滤条件（白名单拼接；参数全部占位符）
    where = ["lang = ?"]
    params: list[Any] = [lang_n]
    domain_n = (domain or "").strip()
    if domain_n:
        where.append("research_direction = ?")
        params.append(domain_n)
    if part_q and lang_n == "en":  # zh 不分区：忽略（meta 回显）
        where.append("cas_partition = ?")
        params.append(part_q)
    q_n = (q or "").strip()
    if q_n:
        where.append("name LIKE ? ESCAPE '\\'")
        params.append(f"%{_escape_like(q_n)}%")
    where_sql = " AND ".join(where)

    # 排序（列名出自白名单；NULL 恒排最后）
    if field_n:
        dir_sql = "DESC" if order_n == "desc" else "ASC"
        order_sql = f"{field_n} IS NULL, {field_n} {dir_sql}"
    else:
        col = "jif" if lang_n == "en" else "composite_impact_factor"
        order_sql = f"{col} IS NULL, {col} DESC"

    try:
        total_row = await db.fetchone(
            f"SELECT COUNT(*) AS c FROM journal_directory WHERE {where_sql}", params  # noqa: S608
        )
        total = int(total_row["c"]) if total_row else 0
        rows = await db.fetchall(
            f"SELECT * FROM journal_directory WHERE {where_sql} "  # noqa: S608
            f"ORDER BY {order_sql} LIMIT ? OFFSET ?",
            [*params, size, (page - 1) * size],
        )
        dom_rows = await db.fetchall(
            "SELECT research_direction AS d, COUNT(*) AS c FROM journal_directory "
            "WHERE lang = ? AND research_direction IS NOT NULL "
            "GROUP BY 1 ORDER BY c DESC, d ASC",
            [lang_n],
        )
    except sqlite3.OperationalError as exc:
        # 表不存在（脚本未跑）→ 优雅降级，HTTP 200
        logger.warning("journal_directory: 表不可用（目录未导入？）: %s", exc)
        return DirectoryResponse(
            ok=True, items=[],
            meta={
                "total": 0, "page": page, "size": size, "lang": lang_n,
                "domain": domain_n, "cas_partition": part_q, "q": q_n,
                "domains": [], "data_source_note": _UNIMPORTED_NOTE,
                "data_imported": False,
            },
            error=None,
        )

    items = [DirectoryItem(**_present(dict(r), lang_n)) for r in rows]
    meta: dict[str, Any] = {
        "total": total,
        "page": page,
        "size": size,
        "lang": lang_n,
        "domain": domain_n,
        "cas_partition": part_q if lang_n == "en" else "",
        "q": q_n,
        "domains": [r["d"] for r in dom_rows if r["d"]],
        "data_source_note": _NOTE_EN if lang_n == "en" else _NOTE_ZH,
        "data_imported": True,
    }
    return DirectoryResponse(ok=True, items=items, meta=meta, error=None)
