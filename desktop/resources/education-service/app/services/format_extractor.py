"""services/format_extractor.py —— 投稿指南 → 格式规则 JSON（LLM 抽取，第一批）。

- 调 services/llm.py 的 complete_json（DeepSeek json_object + 修复重试，零新依赖）
- Prompt = 「格式规则抽取器」模板：目标字段与 journals.json format_rules_default
  同构（max_pages / min_font_pt / min_margin_mm / min_line_spacing / min_figures /
  max_fonts / refs_style / anonymous），强制每字段带 source_quotes + confidence
- 诚实原则：正文没写 → null，绝不编造（与 format_check 诚实原则一致）
- 解析/校验失败 → {"ok": False, "error": ...}

返回：
  成功 {"ok": True, "rules": {...}, "source_quotes": {...},
        "confidence": {...}, "notes": str, "raw": LLM原始JSON, "error": None}
  失败 {"ok": False, "error": "...", 其余键为 None}
source_url 由调用方（scripts/fetch_format_rules.py）在落盘时补入结果文件。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .llm import LLMError, complete_json

logger = logging.getLogger(__name__)

# 「格式规则抽取器」模板（system prompt）
_SYSTEM = """你是「格式规则抽取器」：从期刊官方投稿指南的 Markdown 正文中抽取格式规则，供投稿排版检查引擎使用。

# 目标字段（rules 对象，严格用以下字段名）
- max_pages：正文页数上限（整数；原文无明确上限 → null）
- min_font_pt：最小字号（数字，单位 pt；原文若用 cm/mm/inch 给出则换算为 pt）
- min_margin_mm：最小页边距（数字，单位 mm；原文若用 inch 给出则 ×25.4 换算）
- min_line_spacing：最小行距倍数（数字；如 "double-spaced" → 2.0，"1.5 spacing" → 1.5）
- min_figures：研究论文最少图数（整数；无明确要求 → null）
- max_fonts：全文最多字体种类（整数；无明确要求 → null）
- refs_style：参考文献样式，枚举 apa | gb7714 | author_year | numbered | vancouver | mla | chicago | other（无法判断 → null）
- anonymous：是否要求匿名/双盲评审（true / false / null）

# 凭证（强制）
- 每个非 null 字段必须给 source_quotes[字段名]：从正文【逐字】摘录的 1-3 句原文，
  不改写、不翻译、不丢掉含关键数字的句子。
- source_quotes["_general"]：整体最有凭证价值的 1-3 条原文摘录。
- confidence：{"overall": 0-1, "per_field": {"字段名": 0-1}}。
  直接原文命中 ≥0.9；单位换算 0.6-0.9；靠上下文推断 <0.6。

# 诚实原则
- 正文没写到的字段一律 null，绝不用常识或训练记忆补值。
- 单位换算允许做，但该字段 confidence 不得超过 0.9。
- 换算过程、拿不准的点、页面疑似不完整，写进 notes。

# 输出
只输出一个 JSON 对象，不要代码块、不要解释文字：
{"rules": {"max_pages": null, "min_font_pt": null, "min_margin_mm": null,
  "min_line_spacing": null, "min_figures": null, "max_fonts": null,
  "refs_style": null, "anonymous": null},
 "source_quotes": {"字段名": ["原文1", "..."], "_general": ["原文1", "..."]},
 "confidence": {"overall": 0.0, "per_field": {"字段名": 0.0}},
 "notes": "一句话说明抽取依据与不确定点"}"""

_USER_TMPL = """期刊：{journal_name}

=== 官方投稿指南正文（Markdown，已截断） ===
{markdown}"""

_RULE_INT_KEYS = ("max_pages", "min_figures", "max_fonts")
_REFS_ALLOWED = {"apa", "gb7714", "author_year", "numbered", "vancouver",
                 "mla", "chicago", "other"}
_REFS_ALIAS = {
    "apa-7": "apa", "apa7": "apa", "apa 7th": "apa", "apa_7th": "apa",
    "harvard": "author_year", "author-year": "author_year", "authoryear": "author_year",
    "gb/t 7714": "gb7714", "gb/t-7714": "gb7714", "gb7714-2015": "gb7714",
    "gb/t7714": "gb7714", "chicago author-date": "chicago",
}


# 字符串值带显式单位时的目标单位换算表（LLM 若回传原文单位的兜底；无单位=数值即目标单位）
_UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4, "inch": 25.4, "inches": 25.4, "pt": 25.4 / 72}
_UNIT_TO_PT = {"pt": 1.0, "mm": 72 / 25.4, "cm": 72 / 2.54, "in": 72.0, "inch": 72.0, "inches": 72.0}


def _to_int(v: Any) -> int | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(round(v))
    if isinstance(v, str):
        m = re.search(r"-?\d+", v)
        if m:
            return int(m.group(0))
    return None


def _to_float(v: Any, units: dict[str, float] | None = None) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"(-?\d+(?:\.\d+)?)\s*([a-zA-Z]+)?", v)
        if not m:
            return None
        x = float(m.group(1))
        unit = (m.group(2) or "").lower()
        if units and unit:
            factor = units.get(unit)
            if factor is None:
                return None  # 未知单位不猜
            x *= factor
        return x
    return None


def _to_bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if v in (0, 1):
            return bool(v)
    if isinstance(v, str):
        low = v.strip().lower()
        if low in ("true", "yes", "required"):
            return True
        if low in ("false", "no", "not required"):
            return False
    return None


def _norm_refs(v: Any) -> str | None:
    if not isinstance(v, str) or not v.strip():
        return None
    low = v.strip().lower()
    low = _REFS_ALIAS.get(low, low)
    return low if low in _REFS_ALLOWED else "other"


def _norm_conf(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return max(0.0, min(1.0, float(v)))


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """白名单归一：类型错的值置 null（宁缺勿错，raw 全量保留在结果里）。"""
    src = raw.get("rules")
    src = src if isinstance(src, dict) else {}
    rules: dict[str, Any] = {}
    for k in _RULE_INT_KEYS:
        rules[k] = _to_int(src.get(k))
    rules["min_font_pt"] = _to_float(src.get("min_font_pt"), _UNIT_TO_PT)
    rules["min_margin_mm"] = _to_float(src.get("min_margin_mm"), _UNIT_TO_MM)
    rules["min_line_spacing"] = _to_float(src.get("min_line_spacing"))
    rules["refs_style"] = _norm_refs(src.get("refs_style"))
    rules["anonymous"] = _to_bool(src.get("anonymous"))

    sq_src = raw.get("source_quotes")
    sq_src = sq_src if isinstance(sq_src, dict) else {}
    quotes: dict[str, list[str]] = {}
    for key, val in sq_src.items():
        if key != "_general" and key not in rules:
            continue
        items = val if isinstance(val, list) else [val]
        cleaned = [str(x).strip() for x in items if isinstance(x, (str, int, float))
                   and str(x).strip()]
        if cleaned:
            quotes[key] = cleaned

    conf_src = raw.get("confidence")
    conf_src = conf_src if isinstance(conf_src, dict) else {}
    pf_src = conf_src.get("per_field")
    pf_src = pf_src if isinstance(pf_src, dict) else {}
    per_field: dict[str, float] = {}
    for k, v in pf_src.items():
        c = _norm_conf(v)
        if c is not None and (k in rules or k == "_general"):
            per_field[k] = c
    overall = _norm_conf(conf_src.get("overall"))
    if overall is None and per_field:
        overall = sum(per_field.values()) / len(per_field)
    confidence = {"overall": overall, "per_field": per_field}

    notes = raw.get("notes")
    return {
        "rules": rules,
        "source_quotes": quotes,
        "confidence": confidence,
        "notes": str(notes) if notes else "",
    }


def _validation_error(norm: dict[str, Any]) -> str | None:
    """强制凭证：任一非 null 字段缺 source_quotes → 拒收（ok=False）。"""
    missing = [k for k, v in norm["rules"].items()
               if v is not None and k not in norm["source_quotes"]]
    if missing:
        return f"LLM 输出缺少非 null 字段的 source_quotes：{', '.join(missing)}"
    if not norm["source_quotes"].get("_general"):
        return "LLM 输出缺少 source_quotes._general 整体引证"
    if any(v is not None for v in norm["rules"].values()):
        return None
    return "LLM 未抽出任何非 null 字段（rules 全空）"


async def extract_format_rules(markdown: str, journal_name: str) -> dict:
    """markdown（≤30000 字符）+ 期刊名 → 抽取结果（schema 见模块 docstring）。"""
    if not markdown or not markdown.strip():
        return {"ok": False, "error": "markdown 为空", "rules": None,
                "source_quotes": None, "confidence": None, "notes": None, "raw": None}
    user = _USER_TMPL.format(journal_name=journal_name, markdown=markdown)
    try:
        raw = await complete_json(_SYSTEM, user, temperature=0.1, max_tokens=None)
    except LLMError as exc:
        return {"ok": False, "error": f"LLM 调用失败：{exc}", "rules": None,
                "source_quotes": None, "confidence": None, "notes": None, "raw": None}
    if not isinstance(raw, dict):
        return {"ok": False, "error": f"LLM 输出非 JSON 对象：{type(raw).__name__}",
                "rules": None, "source_quotes": None, "confidence": None,
                "notes": None, "raw": None}

    norm = _normalize(raw)
    err = _validation_error(norm)
    if err:
        logger.warning("[extract] %s 校验失败：%s", journal_name, err)
        return {"ok": False, "error": err, "rules": None,
                "source_quotes": norm["source_quotes"], "confidence": norm["confidence"],
                "notes": norm["notes"], "raw": raw}
    filled = sum(1 for v in norm["rules"].values() if v is not None)
    logger.info("[extract] %s → 8 字段中 %d 个非 null", journal_name, filled)
    return {"ok": True, "error": None, "rules": norm["rules"],
            "source_quotes": norm["source_quotes"],
            "confidence": norm["confidence"], "notes": norm["notes"], "raw": raw}
