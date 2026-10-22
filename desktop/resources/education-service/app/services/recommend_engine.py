"""services/recommend_engine.py —— presubmit recommendation 规则引擎（纯函数，零 LLM）。

背景（2026-10-12 诊断）：旧链路由 LLM 直接输出 recommendation，且 SYSTEM_PROMPT 配额
强制 "major_weaknesses 3-8 条"，导致 accept/minor_revision 几乎不可达（对照实验中
强稿——BUSCO 96.8%/Hi-C 98.7%/accession 齐/UFBoot 1000——仍被凑出 6 条 major）。
新链路：LLM 只如实列 weakness（major 带 hard_rule/fatal 标签），本引擎按可测规则
计算 recommendation + rationale（模板拼装，可复现，不经 LLM）。

判定链（自上而下首条命中）：
  R0    任一 major 带 fatal=true              → reject（须人工复核）
  R1    H = 硬性 major 数 ≥ 1                 → major_revision（与 tier 无关，不可协商）
  R2'   tier==3 且 1≤S≤2 且 H==0              → minor_revision（入门刊仅 soft 可降）
  R2    S = soft major 数 ≥ 1                 → major_revision（S≥5 标 severe，仍非 reject）
  R4    S == 0 且 minor ≤ accept_max_minor[tier] → accept
  R5    S == 0                                → minor_revision
其中 S = 总 major 数 − 硬性标签数（标签漏打时 S 变大，只会更严不会漏放）。
"""
from __future__ import annotations

import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from ..config import get_settings

# 硬性规则标签（SYSTEM_PROMPT 与 coerce 共用白名单；未知标签一律置 None）
HARD_RULE_TAGS = (
    "busco_missing",      # 组装类论文缺 BUSCO 完整格式
    "hic_missing",        # 宣称 chromosome-level/Hi-C 但缺挂载率
    "accession_missing",  # 缺数据 Accession（"will be deposited" 视为缺）
    "voucher_missing",    # 野生材料缺凭证标本
    "abs_missing",        # 缺 ABS/名古屋合规声明
)

HARD_RULE_LABELS = {
    "busco_missing": "BUSCO 完整度缺失",
    "hic_missing": "Hi-C 挂载率缺失",
    "accession_missing": "数据 Accession 缺失",
    "voucher_missing": "凭证标本缺失",
    "abs_missing": "ABS/名古屋合规声明缺失",
}

TIER_LABELS = {1: "顶刊", 2: "主流期刊", 3: "入门期刊"}

# tier 参数：accept 门槛（minor 上限）；tier3 额外允许 ≤2 条 soft major 降为小修
TIER_ACCEPT_MAX_MINOR = {1: 1, 2: 2, 3: 3}
TIER3_SOFT_MAJOR_AS_MINOR_MAX = 2

# ---- tier 查找优先级（2026-10-12）：override → featured → jif → 默认 ----
# ① 用户高频投稿顶刊硬编码（精确名匹配，不受 jif/综述刊启发式影响；
#    "The " 前缀归一化，"Plant Cell"/"The Plant Cell" 互认）
TIER1_OVERRIDES = frozenset(
    re.sub(r"^the\s+", "", name.casefold()) for name in (
        "Nature", "Nature Genetics", "Nature Plants", "Nature Biotechnology",
        "Nature Methods", "Cell", "Science", "Genome Biology", "Molecular Plant",
        "The Plant Cell", "Nature Communications", "Science Advances", "Cell Research",
    )
)
# 综述刊不因高 jif 升 tier1（Annual Review of… / Trends in… / …Reviews）
_REVIEW_JOURNAL_RE = re.compile(r"reviews?\b|\btrends\b|annual\s+review", re.IGNORECASE)
_JIF_TIER1_MIN = 15.0  # jif >= 15 → tier1；5 ≤ jif < 15 → tier2；<5/NULL → 默认 tier2


@lru_cache(maxsize=1)
def _tier_by_name() -> dict[str, int]:
    """journals.json 精选期刊 name(casefold) → review_tier（读文件一次，缓存）。"""
    path = get_settings().journals_seed_abs
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, int] = {}
    for j in payload.get("journals") or []:
        if not isinstance(j, dict):
            continue
        name = str(j.get("name") or "").strip().casefold()
        tier = j.get("review_tier")
        if name and isinstance(tier, int) and tier in (1, 2, 3):
            out[name] = tier
    return out


@lru_cache(maxsize=256)
def _jif_by_name(name_bare_cf: str) -> float | None:
    """journal_directory 按名查 jif（只读一列；表缺失/查不到 → None）。"""
    db = get_settings().db_path_abs
    if not Path(db).exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute(
                "SELECT jif FROM journal_directory WHERE lang='en' "
                "AND (lower(name)=? OR lower(name)=?) "
                "ORDER BY jif DESC LIMIT 1",
                (name_bare_cf, "the " + name_bare_cf),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not row or row[0] is None:
        return None
    try:
        return float(row[0])
    except (TypeError, ValueError):
        return None


def resolve_tier(target_journal: str) -> tuple[int, str]:
    """目标期刊 → (tier, source)。source ∈ override/featured/jif/默认。

    查找优先级：① 硬编码 13 本顶刊 ② journals.json review_tier
    ③ journal_directory jif 启发式（综述刊除外）④ 默认 tier 2。
    """
    raw = (target_journal or "").strip()
    if not raw:
        return 2, "默认"
    key = raw.casefold()
    bare = re.sub(r"^the\s+", "", key)
    if bare in TIER1_OVERRIDES:
        return 1, "override"
    featured = _tier_by_name()
    tier = featured.get(key, featured.get(bare))
    if isinstance(tier, int) and tier in (1, 2, 3):
        return tier, "featured"
    if not _REVIEW_JOURNAL_RE.search(bare):
        jif = _jif_by_name(bare)
        if jif is not None:
            return (1 if jif >= _JIF_TIER1_MIN else 2), "jif"
    return 2, "默认"


def _item_field(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def decide_recommendation(
    majors: Iterable[Any],
    minors: Iterable[Any] = (),
    presentations: Iterable[Any] = (),
    tier: int = 2,
) -> dict[str, Any]:
    """规则引擎入口。majors 项可为 dict 或带 hard_rule/fatal 属性的对象。

    返回 {"recommendation": ..., "rationale": {...}}；
    rationale 含 rule/hard_hits/计数/tier/tier_label/explanation（模板拼装）。
    """
    del presentations  # 呈现类不参与判定（仅归档展示）
    majors = list(majors)
    minors = list(minors)
    if tier not in (1, 2, 3):
        tier = 2

    hard_hits: list[str] = []
    fatal = False
    for m in majors:
        if bool(_item_field(m, "fatal")):
            fatal = True
        hr = _item_field(m, "hard_rule")
        if hr in HARD_RULE_LABELS:
            hard_hits.append(hr)

    m_count = len(majors)
    h_count = len(hard_hits)
    s_count = m_count - h_count  # soft major（标签漏打 → 计入 S，只会更严）
    minor_count = len(minors)
    tier_label = TIER_LABELS[tier]

    # tier3 降级通道提示（对作者可见：同稿投不同档结果不同）
    tier_note = ""
    if h_count == 0 and 1 <= s_count <= TIER3_SOFT_MAJOR_AS_MINOR_MAX and tier != 3:
        tier_note = f"当前判定基于 tier {tier} 档；tier 3 入门刊此稿可能判 minor_revision。"

    def _result(rec: str, rule: str, explanation: str) -> dict[str, Any]:
        return {
            "recommendation": rec,
            "rationale": {
                "rule": rule,
                "hard_hits": hard_hits,
                "major_count": m_count,
                "hard_count": h_count,
                "soft_count": s_count,
                "minor_count": minor_count,
                "tier": tier,
                "tier_label": tier_label,
                "explanation": explanation + tier_note,
            },
        }

    # R0：fatal → reject（前端须配"建议人工复核"）
    if fatal:
        return _result(
            "reject", "R0",
            "存在 fatal 级问题（数据可靠性/伦理，须人工复核）→ 建议拒稿。")

    # R1：硬性 major 不可协商
    if h_count >= 1:
        labels = "、".join(HARD_RULE_LABELS.get(t, t) for t in dict.fromkeys(hard_hits))
        return _result(
            "major_revision", "R1",
            f"命中硬性缺陷（{labels}）→ 大修（不可协商）；"
            f"另有大修级 {s_count} 条、小修级 {minor_count} 条。")

    # R2'：入门刊 1-2 条 soft major 可降为小修
    if tier == 3 and 1 <= s_count <= TIER3_SOFT_MAJOR_AS_MINOR_MAX:
        return _result(
            "minor_revision", "R2'",
            f"无硬性缺陷，大修级（soft）{s_count} 条；"
            f"入门刊（tier 3）可将 ≤{TIER3_SOFT_MAJOR_AS_MINOR_MAX} 条 soft 降为小修 → 建议小修。")

    # R2/R3：soft major ≥ 1（S≥5 标 severe）
    if s_count >= 1:
        sev = "，severe" if s_count >= 5 else ""
        return _result(
            "major_revision", "R2",
            f"大修级问题 {s_count} 条（均非硬性{sev}）→ 建议大修。")

    # R4/R5：无 major
    accept_max = TIER_ACCEPT_MAX_MINOR[tier]
    if minor_count <= accept_max:
        return _result(
            "accept", "R4",
            f"无大修级问题，小修级 {minor_count} 条（≤{accept_max}）→ 建议接收。")
    return _result(
        "minor_revision", "R5",
        f"无大修级问题，小修级 {minor_count} 条 → 建议小修。")
