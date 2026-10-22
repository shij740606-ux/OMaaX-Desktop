"""routers/presubmit.py —— 投稿前自审稿（POST /api/review/presubmit）。

流程：上传 PDF → paper_parse（vstore 8094 解析）→ 段落感知装箱 + token 双层保护
→ LLM 结构化审稿（DeepSeek json_object，System Prompt 为植物基因组学六维专属
清单，见 services/self_review.py）→ coerce 归一化 → 关键词扫描器证伪硬性缺项
（services/paper_scanner.py）→ 规则引擎计算 recommendation。
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel

from ..config import get_settings
from ..services import llm, paper_parse, paper_scanner, self_review
from ..services.recommend_engine import decide_recommendation, resolve_tier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/review", tags=["review-presubmit"])

# ---- token 双层保护（2026-10-12）----
# 第一层：估算 token 超阈值 → 自动降到 TOKEN_GUARD_REPACK_CHARS 字符重装箱
# 第二层：LLM 返回 HTTP 400/413 → 降到 LLM_REJECT_RETRY_CHARS 字符重试一次
_EST_TOKENS_DIVISOR = 1.5          # 粗略上界：英文 ~4 字符/token，1.5 偏保守
_TOKEN_GUARD_THRESHOLD = 48000
_TOKEN_GUARD_REPACK_CHARS = 100000
_LLM_REJECT_RETRY_CHARS = 80000
# 无节标题可辨时的尾窗（捞页尾 Data Availability 块）
_TAIL_FALLBACK_CHARS = 5000

# 节标题（行首、允许编号前缀、独占一行）；键 → 装箱分组名
_SECTION_HEADER_RE = re.compile(
    r"^[ \t]*(?:\d+(?:\.\d+)*\.?\s+)?"
    r"(Abstract|Summary|Introduction|Results|Discussion|Methods"
    r"|Materials\s+and\s+Methods"
    r"|Data\s+(?:and\s+(?:code\s+)?)?[Aa]vailability"
    r"|Availability\s+of\s+[Dd]ata"
    r"|Data\s+and\s+[Mm]aterials\s+[Aa]vailability"
    r"|References|Acknowledg(?:e)?ments?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_SECTION_GROUP = {
    "abstract": "abstract", "summary": "abstract",
    "introduction": "introduction",
    "results": "results",
    "discussion": "discussion",
    "methods": "methods", "materials and methods": "methods",
    "data availability": "data_availability",
    "data and code availability": "data_availability",
    "data availability statement": "data_availability",
    "availability of data": "data_availability",
    "data and materials availability": "data_availability",
    "references": "references", "acknowledgements": "acknowledgements",
    "acknowledgments": "acknowledgements",
}
# 装箱优先级（首条命中优先；methods 限 60% 预算——六维清单大量依赖 Methods）
_PACK_PRIORITY = ("abstract", "data_availability", "methods", "results", "discussion")
_METHODS_CAP_RATIO = 0.6


def estimate_tokens(text: str) -> int:
    """粗略 token 上界估计（len/1.5），无需 tokenizer 依赖。"""
    return int(len(text) / _EST_TOKENS_DIVISOR)


def _split_segments(text: str) -> list[tuple[str, str]] | None:
    """按节标题切段 → [(group, content)...]。无可辨标题 → None。"""
    matches = list(_SECTION_HEADER_RE.finditer(text))
    if not matches:
        return None
    segments: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        segments.append(("preamble", text[:matches[0].start()]))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        group = _SECTION_GROUP.get(m.group(1).casefold(), m.group(1).casefold())
        segments.append((group, text[m.start():end]))
    return segments


def pack_feed_text(text: str, budget: int) -> tuple[str, list[str]]:
    """段落感知装箱 → (feed_text, dropped_sections)。

    预算内：原文返回。超预算：按优先级保留
    ① Abstract ② Data Availability 全文 ③ Methods 前 60% ④ Results
    ⑤ Discussion，其余按原序填满；未收段记入 dropped。
    无节标题可辨 → 头 + 尾窗拼接（捞页尾 availability 块），不优于现状亦不劣于。
    """
    if len(text) <= budget:
        return text, []
    segments = _split_segments(text)
    if not segments:
        sep = "\n[...]\n"
        head = max(0, budget - _TAIL_FALLBACK_CHARS - len(sep))
        return (
            text[:head] + sep + text[-_TAIL_FALLBACK_CHARS:],
            ["[无节标题回退：保留头+尾窗]"],
        )
    methods_cap = int(budget * _METHODS_CAP_RATIO)
    chosen: dict[int, str] = {}
    used = 0
    for group in _PACK_PRIORITY:
        for i, (g, content) in enumerate(segments):
            if g != group or i in chosen:
                continue
            room = budget - used
            if room <= 0:
                break
            piece = content[:methods_cap] if group == "methods" else content
            if len(piece) > room:
                piece = piece[:room]
            if not piece:
                break
            chosen[i] = piece
            used += len(piece)
            break
    for i, (_, content) in enumerate(segments):
        if i in chosen or used >= budget:
            continue
        room = budget - used
        chosen[i] = content if len(content) <= room else content[:room]
        used += len(chosen[i])
    feed = "".join(chosen[i] for i in sorted(chosen))
    dropped: list[str] = []
    for i, (g, _) in enumerate(segments):
        if i not in chosen and g not in dropped:
            dropped.append(g)
    return feed, dropped


def _fit_feed_text(text: str, budget: int) -> tuple[str, list[str]]:
    """装箱 + 第一层 token 保护（估算超阈值自动降到 100K 字符重装）。"""
    feed, dropped = pack_feed_text(text, budget)
    if estimate_tokens(feed) > _TOKEN_GUARD_THRESHOLD and len(feed) > _TOKEN_GUARD_REPACK_CHARS:
        logger.info(
            "presubmit: token 估算 %d > %d，降到 %d 字符重装箱",
            estimate_tokens(feed), _TOKEN_GUARD_THRESHOLD, _TOKEN_GUARD_REPACK_CHARS,
        )
        feed, dropped = pack_feed_text(text, _TOKEN_GUARD_REPACK_CHARS)
    return feed, dropped


class PresubmitResponse(BaseModel):
    ok: bool
    report: self_review.SelfReviewReport | None = None
    error: dict[str, object] | None = None
    meta: dict[str, object] | None = None


def _error(code: str, message: str, *, degraded: bool = False) -> PresubmitResponse:
    return PresubmitResponse(
        ok=False, report=None,
        error={"code": code, "message": message, "degraded": degraded},
    )


@router.post("/presubmit", response_model=PresubmitResponse)
async def presubmit_review(
    file: UploadFile = File(..., description="待审稿 PDF"),
    target_journal: str = Form("", description="目标期刊（可选，影响格式类意见）"),
) -> PresubmitResponse:
    settings = get_settings()

    filename = file.filename or "paper.pdf"
    if not filename.lower().endswith(".pdf"):
        return _error("VALIDATION", "仅支持 PDF 文件")

    content = await file.read()
    if not content:
        return _error("VALIDATION", "上传文件为空")
    if len(content) > settings.max_pdf_bytes:
        return _error("VALIDATION", "PDF 超过 50MB 上限")

    # ---- 解析（vstore 8094）----
    try:
        text = await paper_parse.parse_pdf_bytes(content, filename=filename)
    except paper_parse.ParseError as exc:
        logger.warning("presubmit: 解析失败：%s", exc)
        return _error("PARSE_FAILED", str(exc), degraded=True)

    truncated = len(text) > settings.max_text_chars
    feed_text, dropped_sections = _fit_feed_text(text, settings.max_text_chars)

    # ---- LLM 结构化自审（植物基因组学六维清单）----
    # 第二层保护：HTTP 400/413（疑似超长/形态拒绝）→ 降 80K 字符重试一次
    user_prompt = self_review.build_user_prompt(feed_text, (target_journal or "").strip())
    try:
        try:
            payload = await llm.complete_json(
                self_review.SYSTEM_PROMPT,
                user_prompt,
                temperature=0.2,
                max_tokens=8000,
                timeout=settings.presubmit_read_timeout,
            )
        except llm.LLMError as exc:
            msg = str(exc)
            if ("HTTP 400" in msg or "HTTP 413" in msg) and len(feed_text) > _LLM_REJECT_RETRY_CHARS:
                logger.warning("presubmit: LLM 拒绝输入（%s），降到 %d 字符重试",
                               msg[:120], _LLM_REJECT_RETRY_CHARS)
                feed_text, dropped_sections = pack_feed_text(text, _LLM_REJECT_RETRY_CHARS)
                truncated = True
                payload = await llm.complete_json(
                    self_review.SYSTEM_PROMPT,
                    self_review.build_user_prompt(feed_text, (target_journal or "").strip()),
                    temperature=0.2,
                    max_tokens=8000,
                    timeout=settings.presubmit_read_timeout,
                )
            else:
                raise
    except llm.LLMError as exc:
        logger.error("presubmit: LLM 审稿失败：%s", exc)
        return _error("UPSTREAM_DOWN", f"审稿模型调用失败：{exc}", degraded=True)

    report = self_review.coerce_report(payload)

    # ---- 关键词扫描器：证伪 LLM 的硬性缺项（有 X 却报缺 X → 真降级至 minor）----
    # A2（2026-10-12）：被冲突的 major 不再留在 major 列表里只剥标签，而是移出
    # major、append 到 minor（scanner_downgraded=True）——major_count/引擎输入真实
    # 减少；fatal 条目例外（只记冲突不降级，须人工过目）。
    signals = paper_scanner.scan_paper_signals(feed_text)
    kept_majors, demoted_minors, scanner_conflicts = paper_scanner.reconcile_hard_rules(
        report.major_weaknesses, signals
    )
    report.major_weaknesses = kept_majors
    if demoted_minors:
        report.minor_weaknesses = list(report.minor_weaknesses) + list(demoted_minors)

    # recommendation 由规则引擎计算（LLM 输出的同名字段已被 coerce 忽略）
    tier, tier_source = resolve_tier((target_journal or "").strip())
    decision = decide_recommendation(
        majors=report.major_weaknesses,
        minors=report.minor_weaknesses,
        presentations=report.presentation_format,
        tier=tier,
    )
    rationale = decision["rationale"]
    rationale["scanner_conflicts"] = scanner_conflicts
    if scanner_conflicts:
        rationale["explanation"] += (
            f" 关键词扫描器与审稿意见存在 {len(scanner_conflicts)} 处冲突，"
            f"已降级为小修级问题，请人工核对。"
        )
    report.recommendation = decision["recommendation"]
    report.recommendation_rationale = rationale
    return PresubmitResponse(
        ok=True,
        report=report,
        meta={
            "filename": filename,
            "target_journal": (target_journal or "").strip(),
            "text_chars": len(feed_text),
            "truncated": truncated,
            "dropped_sections": dropped_sections,
            "scanner": {
                "accession_found": bool((signals.get("accession") or {}).get("found")),
                "busco_level": (signals.get("busco") or {}).get("level"),
                "hic_found": bool((signals.get("hic") or {}).get("found")),
            },
            "model": llm._llm_config()["model"],
            "tier": tier,
            "tier_label": decision["rationale"]["tier_label"],
            "tier_source": tier_source,
        },
    )
