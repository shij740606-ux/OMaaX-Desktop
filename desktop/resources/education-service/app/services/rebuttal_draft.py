"""services/rebuttal_draft.py —— 5 类回复片段 Prompt + 反幻觉校验 + letter 拼装。

- part.type 枚举（决策 2，无 auto）：new_data | reanalysis | location | clarification | rebuttal。
- 反幻觉校验（补充 2 收窄版，只做负向证伪，不做泛化打分）：
  * location：出现 "shows that / as shown" 但无 "added/新增" 迹证 → FAIL
    （"S3 shows that..." 红 FAIL；"we have added ... in S3" 过）。
  * rebuttal：命中负向词表 we agree / we accept / we concede / you are right /
    we were wrong → FAIL；无立场词（maintain/disagree/respectfully/坚持/不同意…）
    → FAIL；"respectfully maintain" 过。
  * 其他三类：Phase 1 仅查非空。
- 校验前置（决策：校验C前置）：draft 生成前先跑校验，FAIL 则不调 LLM。
- letter 拼装：按 reviewer → comment 顺序，原文 + 草稿；无草稿留 "[待补充]"。
"""
from __future__ import annotations

import re
from typing import Any, Callable

LLMCallable = Callable[..., Any]

PART_TYPES = ("new_data", "reanalysis", "location", "clarification", "rebuttal")

_TYPE_FRAGMENTS: dict[str, str] = {
    "new_data": (
        "你的回复属于【补充新数据】类：说明新增了哪些实验/数据（图/表编号、关键数值），"
        "并明确回应原审稿关切。不得虚构未提供的数据——只用 user_response 给出的事实。"
    ),
    "reanalysis": (
        "你的回复属于【重新分析】类：说明重跑了哪些分析、方法/参数变化与新结论要点。"
        "不得虚构未提供的结果——只用 user_response 给出的事实。"
    ),
    "location": (
        "你的回复属于【定位/指出位置】类：明确指出修改或证据在稿件中的位置"
        "（如 Section 3 / Fig. S2 / Methods 段落），与 user_response 给出的位置一致。"
        "不得虚构未提供的位置。"
    ),
    "clarification": (
        "你的回复属于【澄清说明】类：针对误解给出准确、简洁的解释，"
        "只用 user_response 与原评论内容，不引入新主张。"
    ),
    "rebuttal": (
        "你的回复属于【礼貌反驳】类：礼貌但明确地表达不同立场（maintain/disagree），"
        "给出理由（基于 user_response 与原文），不得虚构证据。"
    ),
}

_BASELINE = (
    "通用底线：只基于原评论与 user_response 起草；不得编造数据、图表编号、"
    "统计结果或已做修改；不确定处用占位（[XX]）而非猜测。"
    "输出 JSON：{\"draft_md\": \"英文正式回复正文（markdown，≤200词）\"}。只输出 JSON。"
)

_STANCE = re.compile(
    r"\b(maintain|disagree|respectfully|cannot\s+agree|dispute|stand\s+by|"
    r"hold\s+to)\b|坚持|不同意|保留|异议",
    re.IGNORECASE,
)
_NEGATIVE = re.compile(
    r"\bwe\s+agree\b|\bwe\s+accept\b|\bwe\s+concede\b|\byou\s+are\s+right\b|"
    r"\bwe\s+were\s+wrong\b",
    re.IGNORECASE,
)
_LOC_SHOWS = re.compile(r"shows?\s+that|as\s+shown|表明", re.IGNORECASE)
_LOC_ADDED = re.compile(r"\badded\b|新增|have\s+added|we\s+have\s+added", re.IGNORECASE)


def validate_parts(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """反幻觉校验 → {passed: bool, findings: [{part_index, code, message}]}。"""
    findings: list[dict[str, Any]] = []
    for i, p in enumerate(parts):
        ptype = str(p.get("type") or "")
        content = str(p.get("content") or "").strip()
        if not content:
            findings.append({"part_index": i, "code": "EMPTY_CONTENT",
                             "message": "回复内容不能为空"})
            continue
        if ptype == "location":
            if _LOC_SHOWS.search(content) and not _LOC_ADDED.search(content):
                findings.append({
                    "part_index": i, "code": "LOCATION_EVIDENCE_WEAK",
                    "message": "location 类回复出现“shows that/如所示”但未给出新增位置；"
                               "请写明“we have added … in S{n}”式定位",
                })
        elif ptype == "rebuttal":
            if _NEGATIVE.search(content):
                findings.append({
                    "part_index": i, "code": "REBUTTAL_NEGATIVE",
                    "message": "rebuttal 类回复出现让步词（we agree/accept/concede…）；"
                               "该类应礼貌但明确地表达不同立场",
                })
            elif not _STANCE.search(content):
                findings.append({
                    "part_index": i, "code": "REBUTTAL_NO_STANCE",
                    "message": "rebuttal 类回复缺少立场词（maintain/disagree/respectfully…）",
                })
        # new_data / reanalysis / clarification：Phase 1 仅非空
    return {"passed": not findings, "findings": findings}


def _draft_system(parts: list[dict[str, Any]]) -> str:
    frags = [_TYPE_FRAGMENTS.get(str(p.get("type") or ""), "") for p in parts]
    frags = [f for f in frags if f]
    return " ".join(frags) + " " + _BASELINE if frags else _BASELINE


async def generate_draft(llm: LLMCallable, comment: dict[str, Any]) -> str:
    """按 comment.response.parts 起草 draft_md（调用方须先过 validate_parts）。"""
    parts = (comment.get("response") or {}).get("parts") or []
    user_lines = []
    for p in parts:
        hint = str(p.get("location_hint") or "").strip()
        line = f"- [{p.get('type')}] {p.get('content')}"
        if hint:
            line += f" (location_hint: {hint})"
        user_lines.append(line)
    user_msg = (
        f"原审稿意见：\n{comment.get('body') or comment.get('title') or ''}\n\n"
        f"作者拟回复要点（user_response）：\n" + "\n".join(user_lines)
    )
    try:
        data = await llm(
            [{"role": "system", "content": _draft_system(parts)},
             {"role": "user", "content": user_msg}],
            temperature=0.2,
        )
        draft = str((data or {}).get("draft_md") or "").strip()
        return draft or "[draft generation returned empty]"
    except Exception:  # noqa: BLE001
        return "[draft generation failed — 请重试]"


def assemble_letter(job: dict[str, Any]) -> str:
    """拼装整封 response letter markdown（Phase 1 纯 .md，docx 放 Phase 2）。"""
    lines: list[str] = ["# Response to Reviewers", ""]
    lines.append(f"_Job: {job.get('id')} · template: {job.get('template') or 'generic'}_")
    lines.append("")
    for r in job.get("reviewers") or []:
        lines.append(f"## {r.get('label') or 'Reviewer'}")
        lines.append("")
        for c in r.get("comments") or []:
            title = c.get("title") or c.get("id") or "Comment"
            lines.append(f"### {title}")
            lines.append("")
            lines.append("**Original comment:**")
            lines.append("")
            lines.append(str(c.get("body") or "").strip())
            lines.append("")
            lines.append("**Our response:**")
            lines.append("")
            draft = str(c.get("draft") or "").strip()
            lines.append(draft if draft else "[待补充]")
            lines.append("")
    return "\n".join(lines)
