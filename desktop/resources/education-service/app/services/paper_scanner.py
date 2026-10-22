"""services/paper_scanner.py —— 关键词兜底扫描器（纯 stdlib，零 LLM）。

背景（2026-10-12）：hard_rule 标签由 LLM 输出，存在"正文明明有 accession/BUSCO/挂载率
却报缺失"的漏检风险（真实案例 s41588 Nature Genetics：正文有 PRJCA038097 等 +
BUSCO 98.57% + 94.18% anchored，仍被判 busco/accession/hic_missing → 大修）。
本模块做**程序层证伪**：LLM 说缺某项、扫描器在文中找到该项 → 剥掉 hard_rule
降级为非硬性 major，并在 rationale 记录冲突（前端黄色提示，请人工核对）。

边界（刻意保守，不引入新误判）：
- 只做"证伪硬性缺项"，不做"证实"——扫不到不等于真缺（可能同义表述），不升级任何条目
- accession 只在 Data Availability 段内扫（用户指令）：正文引用他人数据的
  accession 不得误认为"自己有"
"""
from __future__ import annotations

import re
from typing import Any

# ---- accession（只在 DA 段扫）----
ACCESSION_RE = re.compile(
    r"\b(PRJNA|PRJEB|PRJCA|SRP|ERP|DRP|CRA|SRR|ERR|DRR|GCA_|GCF_)\s?(\d[\d._]*)",
    re.IGNORECASE,
)

# ---- DA 段定位：标题多变体（行首允许编号前缀）----
_DA_START_RE = re.compile(
    r"^[ \t]*(?:\d+(?:\.\d+)*\.?\s+)?"
    r"(?:Data\s+(?:and\s+(?:code\s+)?)?[Aa]vailability"
    r"|Availability\s+of\s+(?:data|Data)"
    r"|Data\s+and\s+[Mm]aterials\s+[Aa]vailability"
    r"|Data\s+availability\s+statement)",
    re.MULTILINE,
)
# DA 段结束 = 下一个一级节标题
_NEXT_HEADER_RE = re.compile(
    r"^[ \t]*(?:\d+(?:\.\d+)*\.?\s+)?"
    r"(?:Abstract|Summary|Introduction|Results|Discussion|Methods"
    r"|Materials\s+and\s+Methods|References|Acknowledg(?:e)?ments?"
    r"|Author\s+[Ii]nformation|Rights\s+and\s+permissions|Change\s+history"
    r"|Data\s+(?:and\s+(?:code\s+)?)?[Aa]vailability"
    r"|Availability\s+of\s+[Dd]ata|Data\s+and\s+[Mm]aterials\s+[Aa]vailability)"
    r"\s*$",
    re.MULTILINE,
)
# 回退窗：DA 无行首标题时，短语出现处 + 上下文
_DA_PHRASE_RE = re.compile(r"data\s+availability", re.IGNORECASE)
_DA_FALLBACK_WINDOW = 3000

# ---- BUSCO（全文）----
_BUSCO_MENTION_RE = re.compile(r"\bBUSCO\b", re.IGNORECASE)
_BUSCO_FULL_RE = re.compile(
    r"(?:\bS\s*:\s*\d|\bSingle[- ]copy)"
    r".{0,200}?"
    r"(?:\bD\s*:\s*\d|\bDuplicat)",
    re.IGNORECASE | re.DOTALL,
)

# ---- Hi-C 挂载率（全文）----
HIC_RE = re.compile(
    r"(anchored\s+to\s+chromosomes?"
    r"|anchoring\s+rate"
    r"|pseudo[- ]?chromosomes?"
    r"|chromosome[- ]level"
    r"|%[^.\n]{0,40}anchored)",
    re.IGNORECASE,
)

_MAX_HITS = 20


def _extract_da_section(text: str) -> str:
    """定位 Data Availability 段正文（标题后 → 下一个一级节标题）。

    无行首标题时回退：短语出现处向后 3000 字符窗。均未命中 → ""。
    """
    m = _DA_START_RE.search(text)
    if m:
        nxt = _NEXT_HEADER_RE.search(text, m.end())
        end = nxt.start() if nxt else min(len(text), m.end() + _DA_FALLBACK_WINDOW)
        return text[m.start():end]
    m2 = _DA_PHRASE_RE.search(text)
    if m2:
        return text[m2.start():min(len(text), m2.start() + _DA_FALLBACK_WINDOW)]
    return ""


def _scan_accession(da_text: str) -> dict[str, Any]:
    if not da_text:
        return {"found": False, "hits": [], "in_da_section": False}
    hits: list[str] = []
    seen: set[str] = set()
    for m in ACCESSION_RE.finditer(da_text):
        token = (m.group(1) + m.group(2)).upper()
        if token not in seen:
            seen.add(token)
            hits.append(token)
            if len(hits) >= _MAX_HITS:
                break
    return {"found": bool(hits), "hits": hits, "in_da_section": True}


def _scan_busco(text: str) -> dict[str, Any]:
    m = _BUSCO_MENTION_RE.search(text)
    if not m:
        return {"level": "none", "excerpt": ""}
    excerpt = text[m.start():m.start() + 200]
    if _BUSCO_FULL_RE.search(text):
        level = "full"
    else:
        level = "partial"  # 只有百分比、无 S/D 分项 → prompt 已定为 minor
    return {"level": level, "excerpt": excerpt}


def _scan_hic(text: str) -> dict[str, Any]:
    m = HIC_RE.search(text)
    if not m:
        return {"found": False, "excerpt": ""}
    start = max(0, m.start() - 40)
    return {"found": True, "excerpt": text[start:m.end() + 80]}


def scan_paper_signals(text: str) -> dict[str, Any]:
    """全文 → 结构化信号。accession 只在 DA 段；busco/hic 全文。"""
    if not text:
        return {
            "accession": {"found": False, "hits": [], "in_da_section": False},
            "busco": {"level": "none", "excerpt": ""},
            "hic": {"found": False, "excerpt": ""},
        }
    return {
        "accession": _scan_accession(_extract_da_section(text)),
        "busco": _scan_busco(text),
        "hic": _scan_hic(text),
    }


def _get_hr(item: Any) -> Any:
    if isinstance(item, dict):
        return item.get("hard_rule")
    return getattr(item, "hard_rule", None)


def _item_fatal(item: Any) -> bool:
    if isinstance(item, dict):
        return bool(item.get("fatal"))
    return bool(getattr(item, "fatal", False))


def _set_downgraded(item: Any) -> None:
    if isinstance(item, dict):
        item["scanner_downgraded"] = True
    else:
        try:
            item.scanner_downgraded = True
        except Exception:  # pydantic 严格模式等极端场景：吞掉，冲突记录仍保留
            pass


def _set_hr_none(item: Any) -> None:
    if isinstance(item, dict):
        item["hard_rule"] = None
    else:
        try:
            item.hard_rule = None
        except Exception:  # pydantic 严格模式等极端场景：吞掉，冲突记录仍保留
            pass


def _item_title(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("title") or "")
    return str(getattr(item, "title", "") or "")


# 冲突映射：hard_rule 标签 → (信号检查, 冲突描述)
def _conflict_for(tag: str, signals: dict[str, Any]) -> str | None:
    acc = signals.get("accession") or {}
    busco = signals.get("busco") or {}
    hic = signals.get("hic") or {}
    if tag == "accession_missing" and acc.get("found"):
        hits = ", ".join(acc.get("hits") or [])[:120]
        return f"Data Availability 段已检出 accession（{hits}）"
    if tag == "busco_missing" and busco.get("level") in ("partial", "full"):
        return f"正文已提及 BUSCO（{busco.get('level')} 格式）"
    if tag == "hic_missing" and hic.get("found"):
        excerpt = (hic.get("excerpt") or "").strip()[:80]
        return f"正文已检出挂载率描述（{excerpt}）"
    return None


def reconcile_hard_rules(
    majors: list[Any], signals: dict[str, Any]
) -> tuple[list[Any], list[Any], list[dict[str, Any]]]:
    """对带 hard_rule 的 major 做扫描器证伪：命中 → 剥标签 + **移出 major 降级至
    minor**（A2 语义，2026-10-12）→ 记冲突。

    返回 (kept_majors, demoted_minors, conflicts)：
    - kept_majors：未冲突/未处理的 major（原序）
    - demoted_minors：被冲突降级的条目（hard_rule 置 None、scanner_downgraded=True）
    - conflicts：{hard_rule, title, reason}——fatal 条目只记冲突不降级
      （拒稿级问题须人工过目，扫描器证伪不了伦理判断）。
    """
    kept: list[Any] = []
    demoted: list[Any] = []
    conflicts: list[dict[str, Any]] = []
    for m in majors:
        tag = _get_hr(m)
        if not isinstance(tag, str):
            kept.append(m)
            continue
        reason = _conflict_for(tag, signals)
        if reason is None:
            kept.append(m)
            continue
        conflicts.append({
            "hard_rule": tag,
            "title": _item_title(m),
            "reason": reason,
        })
        if _item_fatal(m):
            kept.append(m)  # fatal 保留原位（含其 hard_rule 供引擎 R1/R0 使用）
            continue
        _set_hr_none(m)
        _set_downgraded(m)
        demoted.append(m)
    return kept, demoted, conflicts
