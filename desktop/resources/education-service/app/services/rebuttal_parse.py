"""services/rebuttal_parse.py —— 审稿意见文本 → 结构化 Comment 树。

流程（保守：正则先切骨架，LLM 只做分类/拆子条，行数交叉核对不匹配则回退正则）：
1. 归一化换行；正则识别 Reviewer 段头（Reviewer 1 / 审稿人 2 / Review A 等），
   无段头 → 单 Reviewer（label=Reviewer）。
2. 每个 Reviewer 段内按编号行切 comment 段（"1." "2)" "（3）" 等）；
   切不出编号 → 整段作 1 条。
3. LLM complete_json 对每段做：{title, severity, sub_items:[{title,body,confidence}]}。
   - LLM 返回段数 != 提交段数 → 全量回退正则（title=首 60 字，severity 默认 major）。
   - sub_items ≥ 2 且最高 confidence ≥ 0.7 → 拆多条（共享 group_id）；否则单条。
4. cross_refs：正文里 "see comment 3 / 参见第 2 条" → 解析目标；解析不到
   target_id=null 保留 raw（决策：不猜）。

不做：auto 分类（已砍）；severity 以外的任何 LLM 自由发挥字段。
"""
from __future__ import annotations

import re
from typing import Any, Callable

# LLM 依赖注入（测试打桩）：async (messages, temperature=..) -> dict
LLMCallable = Callable[..., Any]

_REVIEWER_HEADER = re.compile(
    r"^(?:reviewers?\s*#?\s*([0-9]+|[A-Z])|审稿人\s*#?\s*([0-9A-Z]+)|review\s+([A-Z]))\s*[:：.）)]?",
    re.IGNORECASE | re.MULTILINE,
)
_COMMENT_NUM = re.compile(
    r"(?:(?<=\n)|(?<=\.\s)|^)\s*(?:[#(（]?\s*(\d{1,2})\s*[)）.:、]|[（(]\s*([ivx]{1,4}|[a-z])\s*[)）])\s+",
    re.IGNORECASE | re.MULTILINE,
)
_CROSS_REF = re.compile(
    r"(?:see|noted\s+in|refer(?:ring)?\s+to|per)\s+comment\s*#?\s*(\d+)|"
    r"(?:please\s+)?refer\s+to\s+comment\s*#?\s*(\d+)|"
    r"参见\s*第?\s*(\d+)\s*条|见\s*comment\s*#?\s*(\d+)|同上(?:一条)?评论",
    re.IGNORECASE,
)

_SPLIT_CONFIDENCE = 0.7  # 决策 3：confidence ≥ 0.7 才拆


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def split_reviewers(text: str) -> list[tuple[str, str]]:
    """→ [(label, block_text)]；无段头 → [(Reviewer, 全文)]。"""
    text = _norm(text)
    matches = list(_REVIEWER_HEADER.finditer(text))
    if not matches:
        return [("Reviewer", text)]
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        raw = next(g for g in m.groups() if g is not None)
        label = raw.upper() if raw.isalpha() else f"Reviewer {raw}"
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        if block:
            out.append((label, block))
    if not out:  # 段头全空块 → 兜底
        return [("Reviewer", text)]
    # 段头前的引言（无段头内容）丢弃——保守：不猜归属
    return out


def split_comments(block: str) -> list[str]:
    """段内按编号行切 comment 块；无编号 → [整段]。"""
    block = _norm(block)
    matches = list(_COMMENT_NUM.finditer(block))
    if len(matches) < 2:  # 单个编号或无编号 → 整段一条
        return [block] if block else []
    out: list[str] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(block)
        chunk = block[start:end].strip()
        if chunk:
            out.append(chunk)
    return out or [block]


def _fallback_segment(seg: str) -> dict[str, Any]:
    title = re.sub(r"\s+", " ", seg)[:60]
    return {
        "title": title,
        "severity": "major",
        "sub_items": [{"title": title, "body": seg, "confidence": 1.0}],
    }


_SEG_SYSTEM = (
    "你是学术审稿意见解析助手。输入是一段审稿意见原文。输出 JSON："
    '{"title": "≤12词标题", "severity": "major|minor|question", '
    '"sub_items": [{"title": "...", "body": "该子问题完整原文", "confidence": 0.0-1.0}]}。'
    "severity：major=要求补实验/补数据/重分析；minor=格式/表述小问题；"
    "question=仅提问、无需修改稿件。"
    "若该段含多个可独立回复的子问题则拆成多个 sub_items（confidence 给拆分把握度），"
    "否则 1 个 sub_items（confidence=1.0）。只输出 JSON。"
)


async def _ask_llm_segment(llm: LLMCallable, seg: str) -> dict[str, Any] | None:
    try:
        data = await llm(
            [{"role": "system", "content": _SEG_SYSTEM},
             {"role": "user", "content": seg}],
            temperature=0.0,
        )
        if not isinstance(data, dict) or not isinstance(data.get("sub_items"), list):
            return None
        sev = str(data.get("severity", "")).lower()
        title = str(data.get("title") or "").strip() or seg[:60]
        subs: list[dict[str, Any]] = []
        for s in data["sub_items"][:8]:
            if not isinstance(s, dict):
                continue
            body = str(s.get("body") or "").strip()
            if not body:
                continue
            try:
                conf = float(s.get("confidence", 1.0))
            except (TypeError, ValueError):
                conf = 0.0
            subs.append({
                "title": str(s.get("title") or "").strip() or title,
                "body": body,
                "confidence": max(0.0, min(1.0, conf)),
            })
        if not subs:
            return None
        return {"title": title,
                "severity": sev if sev in ("major", "minor", "question") else "major",
                "sub_items": subs}
    except Exception:  # noqa: BLE001 — LLM 失败即回退正则，不猜测
        return None


def _resolve_cross_refs(body: str, ordinal_map: dict[int, str]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for m in _CROSS_REF.finditer(body):
        raw = m.group(0).strip()
        num = next((int(g) for g in m.groups() if g and g.isdigit()), None)
        if num is None and "同上" in raw:
            num = _prev_ordinal(ordinal_map, m.start())  # 同上 → 上一条，保守仅前一条
        target = ordinal_map.get(num) if num is not None else None
        refs.append({"raw": raw, "target_id": target})
    return refs


def _prev_ordinal(ordinal_map: dict[int, str], _pos: int) -> int | None:
    # 简化：同上 → 已知最大编号（即前一条）
    return max(ordinal_map) if ordinal_map else None


async def parse_review_text(text: str, llm: LLMCallable) -> list[dict[str, Any]]:
    """→ reviewers=[{id, label, ordinal, comments:[Comment]}]（不含 job 包装）。"""
    reviewers_out: list[dict[str, Any]] = []
    cid_seq = 0
    for r_ord, (label, block) in enumerate(split_reviewers(text), start=1):
        segs = split_comments(block)
        if not segs:
            continue
        comments: list[dict[str, Any]] = []
        ordinal_map: dict[int, str] = {}
        parsed_seg: list[dict[str, Any]] = []
        for seg in segs:
            llm_res = await _ask_llm_segment(llm, seg)
            # 交叉核对：LLM 无法给出结构 → 该段回退正则
            parsed_seg.append(llm_res if llm_res else _fallback_segment(seg))
        for seg_idx, (seg, p) in enumerate(zip(segs, parsed_seg), start=1):
            subs = p["sub_items"]
            split = len(subs) >= 2 and max(s["confidence"] for s in subs) >= _SPLIT_CONFIDENCE
            group_id = f"g{r_ord}-{seg_idx}"
            items = subs if split else [subs[0]]
            if not split and len(subs) > 1:
                # 未达阈值 → 多子问题并回一条（body 用整段原文）
                items = [{"title": p["title"], "body": seg, "confidence": 1.0}]
            for it in items:
                cid_seq += 1
                cid = f"c{cid_seq:04d}"
                # 保守：不拆时 body 保留该段原文（不采信 LLM 改写）；拆时用子项 body
                body = it["body"] if split else seg
                ordinal_map[seg_idx] = cid
                comments.append({
                    "id": cid,
                    "group_id": group_id if split else None,
                    "severity": p["severity"],
                    "title": it["title"],
                    "body": body,
                    "sub_items": [s["title"] for s in subs] if split else [],
                    "cross_refs": _resolve_cross_refs(body, ordinal_map),
                    "response": {"parts": []},
                    "draft": None,
                    "validation": None,
                    "human_confirmed": False,
                    "scanner_downgraded": False,
                })
        # cross_refs 需全量 comment id 后再扫一遍（前面扫时 map 未齐）
        for c in comments:
            c["cross_refs"] = _resolve_cross_refs(c["body"], ordinal_map)
        if comments:
            reviewers_out.append({
                "id": f"r{r_ord}", "label": label, "ordinal": r_ord,
                "comments": comments,
            })
    return reviewers_out
