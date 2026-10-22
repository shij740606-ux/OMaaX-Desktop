"""services/journal_rank.py —— LLM 重排 + 幻觉防护 + 三档归类。

职责：
1. rerank：把带语义分的候选期刊（来自 journal_kb）+ 用户偏好交给 LLM，
   要求按 id 白名单返回 {rankings:[{id, tier, match_reason, risk_note}]}。
2. validate：硬校验——白名单外 id 丢弃（幻觉拦截）；tier 非法/LLM 缺漏的
   期刊按规则补位（绝不因 LLM 输出丢失候选期刊）；期刊名称/指标一律不采信
   LLM，最终展示全部用 DB 字段（防止改名型幻觉）。
3. fallback_rankings：LLM 不可用时的确定性规则分档（按 impact_factor）。

三档语义：challenge 挑战型（高影响/慢审稿）、fit 适配型（均衡）、
safe 稳妥型（快速/门槛低）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..config import get_settings
from . import llm

TIERS = ("challenge", "fit", "safe")
TIER_LABELS = {
    "challenge": "挑战型（高影响因子/慢审稿）",
    "fit": "适配型（中等影响/周期适中）",
    "safe": "稳妥型（快速发表/门槛较低）",
}

# 规则分档阈值（近似值，可后续配置化）
_CHALLENGE_MIN_IF = 9.0
_SAFE_MAX_IF = 4.0

# tier 同义词归一（LLM 偶发中文标签）
_TIER_ALIASES = {
    "挑战": "challenge",
    "挑战型": "challenge",
    "高风险": "challenge",
    "适配": "fit",
    "适配型": "fit",
    "稳妥": "safe",
    "稳妥型": "safe",
    "低风险": "safe",
}

SYSTEM_PROMPT = """你是学术投稿推荐专家（植物基因组学方向）。根据给定候选期刊、论文摘要与用户偏好，为**每一个**候选期刊输出推荐分档与理由。

硬规则（违反即视为无效输出）：
1. id 白名单：只能使用输入 candidates 中出现的 id；绝对禁止添加、替换或臆造列表之外的任何期刊。
2. 每个输入 id 恰好输出一次，不得遗漏、不得重复；期刊名称等元数据由系统填充，你只输出 id。
3. tier 只能取 challenge | fit | safe：
   - challenge（挑战型）：高影响因子、审稿较慢或竞争激烈的期刊
   - fit（适配型）：影响力与发表周期均衡、与论文契合的期刊
   - safe（稳妥型）：审稿较快、门槛较低、命中概率较高的期刊
4. match_reason：中文，1-2 句，结合摘要的研究主题与该刊 scope_desc 及指标（如有 similarity 一并参考），说明为何匹配/不匹配。
5. risk_note：中文，1-2 句，指出投稿风险（超出用户偏好、IF 门槛偏高、范围匹配偏弱、审稿周期长等）；无明显风险写「无」。
6. 用户 preferences 为软约束：不满足偏好的期刊仍必须输出，并在 risk_note 中明确说明。
7. 不确定时宁可保守陈述，禁止编造统计数据（录用率、审稿天数等）。

输出：仅一个 JSON 对象，格式：
{"rankings":[{"id":"...","tier":"challenge","match_reason":"...","risk_note":"..."}]}
不要输出任何 JSON 之外的内容。"""


class RankError(Exception):
    """LLM 输出形态异常（无法按 JSON 解析/结构非法）。"""


@dataclass
class Ranking:
    journal_id: str
    tier: str
    match_reason: str
    risk_note: str


def rule_tier(journal: dict[str, Any]) -> str:
    """确定性分档规则（LLM 缺漏/不可用时兜底）。"""
    inf = journal.get("impact_factor")
    weeks = journal.get("review_time_weeks")
    if isinstance(inf, (int, float)):
        if inf >= _CHALLENGE_MIN_IF:
            return "challenge"
        if inf <= _SAFE_MAX_IF:
            return "safe"
    elif isinstance(weeks, (int, float)) and weeks <= 4:
        return "safe"
    return "fit"


def build_user_prompt(
    abstract: str,
    candidates: list[dict[str, Any]],
    preferences: dict[str, Any],
) -> str:
    """候选 + 偏好 + 摘要 → 用户消息（JSON）。"""
    payload = {
        "abstract": abstract.strip()[:4000],
        "preferences": preferences or {},
        "candidates": [
            {
                "id": str(c["journal"]["id"]),
                "name": str(c["journal"].get("name") or ""),
                "impact_factor": c["journal"].get("impact_factor"),
                "review_time_weeks": c["journal"].get("review_time_weeks"),
                "acceptance_rate": c["journal"].get("acceptance_rate"),
                "similarity": c.get("similarity"),
                "scope_desc": str(c["journal"].get("scope_desc") or "")[:400],
            }
            for c in candidates
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _normalize_tier(raw: Any) -> str | None:
    tier = str(raw or "").strip().lower()
    tier = _TIER_ALIASES.get(str(raw or "").strip(), tier)
    return tier if tier in TIERS else None


def validate_rankings(
    payload: Any, candidates: list[dict[str, Any]]
) -> tuple[list[Ranking], int, int]:
    """校验 LLM 输出。

    返回 (rankings, dropped_hallucinated, repaired)：
    - 白名单外 id / 重复 id → 丢弃并计数（repaired 中重复不计）；
    - tier 非法、LLM 缺漏的 id → 按规则补位（repaired 计数）。
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("rankings"), list):
        raise RankError("LLM 输出缺少 rankings 数组")

    by_id = {str(c["journal"]["id"]): c["journal"] for c in candidates}
    input_ids = list(by_id)
    seen: set[str] = set()
    rankings: list[Ranking] = []
    dropped = 0
    repaired = 0

    for entry in payload["rankings"]:
        if not isinstance(entry, dict):
            dropped += 1
            continue
        jid = str(entry.get("id") or "").strip()
        if jid in seen:
            continue
        if jid not in by_id:
            dropped += 1  # 幻觉期刊：绝不进输出
            continue
        seen.add(jid)
        tier = _normalize_tier(entry.get("tier"))
        if tier is None:
            tier = rule_tier(by_id[jid])
            repaired += 1
        reason = str(entry.get("match_reason") or "").strip() or "基于期刊范围与指标的匹配"
        risk = str(entry.get("risk_note") or "").strip() or "无"
        rankings.append(Ranking(journal_id=jid, tier=tier, match_reason=reason, risk_note=risk))

    for jid in input_ids:
        if jid in seen:
            continue
        rankings.append(
            Ranking(
                journal_id=jid,
                tier=rule_tier(by_id[jid]),
                match_reason="LLM 未给出该期刊条目，已按影响因子/周期规则补位",
                risk_note="未提供（LLM 输出缺漏，规则兜底）",
            )
        )
        repaired += 1
    return rankings, dropped, repaired


async def rerank(
    abstract: str,
    candidates: list[dict[str, Any]],
    preferences: dict[str, Any],
) -> tuple[list[Ranking], int, int]:
    """LLM 重排 + 校验。LLMError / RankError 上抛（路由降级为规则分档）。"""
    settings = get_settings()
    payload = await llm.complete_json(
        SYSTEM_PROMPT,
        build_user_prompt(abstract, candidates, preferences),
        temperature=0.2,
        max_tokens=3000,
        timeout=settings.recommend_read_timeout,
    )
    return validate_rankings(payload, candidates)


def fallback_rankings(candidates: list[dict[str, Any]]) -> list[Ranking]:
    """LLM 不可用时的确定性分档（不丢任何候选期刊）。"""
    rankings: list[Ranking] = []
    for c in candidates:
        j = c["journal"]
        inf = j.get("impact_factor")
        weeks = j.get("review_time_weeks")
        rankings.append(
            Ranking(
                journal_id=str(j["id"]),
                tier=rule_tier(j),
                match_reason=(
                    f"规则分档：影响因子 {inf if inf is not None else '未收录'}、"
                    f"审稿约 {weeks if weeks is not None else '未收录'} 周"
                ),
                risk_note="LLM 不可用，本条按影响因子/周期规则估算",
            )
        )
    return rankings
