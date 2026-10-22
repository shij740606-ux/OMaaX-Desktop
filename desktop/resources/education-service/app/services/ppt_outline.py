"""services/ppt_outline.py —— P5 科研汇报大纲生成（map-reduce + 证据程序化校验）。

管线（全 LLM 只负责引用与措辞，结构/页数/证据状态由代码裁决）：
  纯文本 → 段落索引（para_id 形如 S4-P1，figure 引用 S4-P1-Fig2）
  → 图表清单扫描（ppt_figure_index 正则扫 Figure/图/Table/表，非 LLM）
  → map：分块（≤5 块）抽 claims/figures（quote 逐字 + para_id 标注，LLM；
     figures 先过真实清单，不在清单的丢弃）
  → reduce：按 page_plan 配额生成页大纲（1 或 2 次调用；>14 页分两片，
    第二片带上一片页标题维持连贯；deepseek-chat 输出上限 8K，必须分片；
    真实图表清单注入 prompt，suggested_figures 只选不编）
  → 后校验（纯函数、可单测）：
      ① 页数：PAGE_TARGETS 决策 2（10→8-10/15→11-14/20→15-18/30→22-26），
         超上限从尾部可删页裁掉（section/reference 先删）；低于下限不凑页 + warning
      ② bullets（补充 3）：内容页 <2 条 → 一次按页扩充重试；>8 条 → 拆成两页
      ③ evidence：quote 归一化后必须是某段落的子串 → 反查真实 para_id
         （repair_evidence_para_ids，修复 LLM 编造段落ID）→ verified；
         找不到段落 → unverified；result/conclusion 页未核实占比 >50% → warning
      ④ suggested_figures：label 必须在真实图表清单内（否则丢弃而非标
         unverified）；para_id 以清单扫描出的真实位置为准 → verified

错误：OutlineError(code=VALIDATION|LLM_ERROR|LLM_INVALID) → 路由映射信封。
LLM 调用次数：map ≤5 + reduce 1~2 + expand ≤1 ≈ 4~8 次/篇。
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import unicodedata
from typing import Any

from ..config import get_settings
from . import llm
from .llm import LLMError
from .ppt_figure_index import canon_label, figure_map, scan_figure_index

# ---------- 枚举与决策常量 ----------

SCENARIOS = (
    "group_meeting",
    "proposal",
    "conclusion_report",
    "defense",
    "conference_talk",
)
SCENARIO_LABELS = {
    "group_meeting": "组会",
    "proposal": "开题",
    "conclusion_report": "结题",
    "defense": "答辩",
    "conference_talk": "学术会议",
}
# P5 增强 2：风格 3 → 8 → 14（与 data/ppt_styles/*.json 一一对应）。
# 其中 domestic/international/systems-talk/theory-beamer/jp-gothic/editorial
# 移植自开源 paper-skills styles/tokens.json（MIT），色/字号映射进自有 schema。
STYLE_IDS = (
    "academic-minimal", "botanic-forest", "classic-defense",
    "academic-blue", "nature-green", "tech-dark", "monochrome", "warm-earth",
    "domestic", "international", "systems-talk", "theory-beamer",
    "jp-gothic", "editorial",
)

# section = 章节分隔页（P5 增强 3：大标题居中 + 色块背景，由代码定骨架、LLM 只起标题）
PAGE_TYPES = (
    "title", "outline", "section", "background", "method", "result", "conclusion", "reference",
)
CONTENT_TYPES = ("background", "method", "result", "conclusion")

# 决策 2：时长 → [下限, 上限]（含封面/目录，defense 含参考文献页）
PAGE_TARGETS: dict[int, tuple[int, int]] = {
    10: (8, 10),
    15: (11, 14),
    20: (15, 18),
    30: (22, 26),
}
MINUTES_CHOICES = tuple(sorted(PAGE_TARGETS))

# 场景骨架：内容页权重（reference 只有答辩要）；代码算配额，LLM 只填内容
_SKELETONS: dict[str, dict[str, Any]] = {
    "group_meeting":     {"weights": {"background": 0.25, "method": 0.40, "result": 0.35}, "reference": False},
    "proposal":          {"weights": {"background": 0.40, "method": 0.45, "result": 0.15}, "reference": False},
    "conclusion_report": {"weights": {"background": 0.20, "method": 0.30, "result": 0.50}, "reference": False},
    "defense":           {"weights": {"background": 0.25, "method": 0.35, "result": 0.40}, "reference": True},
    "conference_talk":   {"weights": {"background": 0.25, "method": 0.35, "result": 0.40}, "reference": False},
}

# 索引切块（正文 ≤150K 字符 → 块数 ≤5，控制 map 调用次数）
_MAX_SECTIONS = 5
_PARAS_PER_SECTION = 40
_PARA_CHAR_CAP = 1000     # 单段送 map 的截断（pymupdf 块通常 <1000 字符）
_SECTION_CHAR_CAP = 30000 # 单块送 map 的总预算
_MAX_CLAIMS_TOTAL = 50
_MAX_FIGURES_TOTAL = 15
_REDUCE_PAGE_SLICE = 14   # 单次 reduce 页数上限（>14 页分两片，守住 8K 输出）

_MAP_TIMEOUT = 75.0
_REDUCE_TIMEOUT = 200.0
_EXPAND_TIMEOUT = 75.0
_MAP_CONCURRENCY = 2      # BFF 读超时 360s 硬顶：map 并发压总时长


class OutlineError(Exception):
    """大纲生成失败（VALIDATION | LLM_ERROR | LLM_INVALID）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------- 决策 2：页数配额（纯函数） ----------

def page_plan(scenario: str, minutes: int) -> dict[str, tuple[int, int]]:
    """时长目标页数 + 场景骨架 → 每 type 配额 (min, max)。

    固定页：title=1、outline=1、conclusion=1（defense +reference=1）；
    其余按场景权重最大余数法分配（每内容页保底 1）。LLM 只能在配额内填，
    总页数另外夹在 PAGE_TARGETS[minutes] 区间内。
    """
    if scenario not in _SKELETONS:
        raise ValueError(f"未知场景: {scenario}")
    if minutes not in PAGE_TARGETS:
        raise ValueError(f"不支持的时长: {minutes}（可选 {list(MINUTES_CHOICES)}）")
    lo, hi = PAGE_TARGETS[minutes]
    target = (lo + hi) // 2
    weights = _SKELETONS[scenario]["weights"]
    n_ref = 1 if _SKELETONS[scenario]["reference"] else 0
    # 章节分隔页：≥15 分钟（target≥11）每个内容组一张；10 分钟小 deck 不放分隔页
    n_sec = len(weights) if target >= 11 else 0
    rest = max(target - (3 + n_ref + n_sec), len(weights))  # 3 = title+outline+conclusion

    raw = {t: w * rest for t, w in weights.items()}
    alloc = {t: int(v) for t, v in raw.items()}
    for t in sorted(raw, key=lambda k: raw[k] - alloc[k], reverse=True)[: rest - sum(alloc.values())]:
        alloc[t] += 1
    for t in alloc:
        alloc[t] = max(1, alloc[t])  # 内容页保底

    plan: dict[str, tuple[int, int]] = {
        "title": (1, 1), "outline": (1, 1), "conclusion": (1, 1),
    }
    if n_sec:
        plan["section"] = (n_sec, n_sec)
    if n_ref:
        plan["reference"] = (1, 1)
    for t, a in alloc.items():
        plan[t] = (a, a + 1)
    return plan


# ---------- 段落索引（para_id：S{块}-P{段}） ----------

def build_index(text: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """纯文本 → (blocks, index)。blocks 含段落ID标注文本（map 输入），index 为
    para_id → 原文（evidence 校验用）。块按固定段数均分（不做标题猜测，
    确定性可测；S 编号 = 块序号，P 编号 = 块内段序）。"""
    paras = [ln.strip() for ln in text.splitlines() if ln.strip()]
    paras = [p[:_PARA_CHAR_CAP] for p in paras]
    if not paras:
        return [], {}
    n_sections = max(1, min(_MAX_SECTIONS, math.ceil(len(paras) / _PARAS_PER_SECTION)))
    size = math.ceil(len(paras) / n_sections)
    blocks: list[dict[str, Any]] = []
    index: dict[str, str] = {}
    for si in range(0, len(paras), size):
        sec_no = si // size + 1
        lines = paras[si : si + size]
        paras_out = []
        for pi, p in enumerate(lines, 1):
            pid = f"S{sec_no}-P{pi}"
            paras_out.append({"para_id": pid, "text": p})
            index[pid] = p
        blocks.append({
            "section_id": f"S{sec_no}",
            "paras": paras_out,
            "text": "\n".join(f"[{v['para_id']}] {v['text']}" for v in paras_out),
        })
    return blocks, index


# ---------- map：分块抽 claims / figures ----------

_MAP_SYSTEM = """你是植物学论文精读助手。输入是一个论文文本块，每段以 [段落ID] 标注。
只输出单个 JSON 对象：
{"claims": [{"claim": "一句话事实陈述", "para_id": "S1-P2", "quote": "支撑该事实的原文逐字引用"}],
 "figures": [{"label": "Fig 2", "para_id": "S1-P5-Fig2"}]}
硬性要求：
- claim 必须能被同条 quote 直接支撑；quote 必须逐字来自对应段落（≤60字、单句、不得跨段拼接、不得改写）；
- para_id 必须照抄输入标注；figure 的 para_id = 其 caption 所在段落ID + "-FigN"（N 为图序号）；
- 每块最多 8 条 claims、4 个 figures；没有实质内容就返回空数组；严禁编造引用、数据、图号；
- 不要输出 JSON 以外的任何文字。"""


async def _map_block(block: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    payload = await llm.complete_json(
        _MAP_SYSTEM, _map_block_user(block), temperature=0.2,
        max_tokens=4000, timeout=_MAP_TIMEOUT,
    )
    claims = [c for c in (payload.get("claims") or []) if isinstance(c, dict)][:8]
    figures = [f for f in (payload.get("figures") or []) if isinstance(f, dict)][:4]
    return claims, figures


def _map_block_user(block: dict[str, Any]) -> str:
    body = block["text"]
    if len(body) > _SECTION_CHAR_CAP:
        body = body[:_SECTION_CHAR_CAP]
    return f"论文文本块 {block['section_id']}：\n{body}"


# ---------- reduce：页大纲（配额内填内容） ----------

_REDUCE_SYSTEM = """你是学术汇报策划。根据给出的论文摘要卡片（claims/figures 已带逐字引用）、
论文真实图表清单与页数配额，生成 PPT 大纲。只输出单个 JSON 对象：
{"pages": [{"type": "...", "title": "...", "subtitle": "...", "bullets": ["..."],
            "evidence": [{"quote": "..."}],
            "speaker_notes": "...", "suggested_figures": [{"label": "Fig 2", "para_id": "S1-P12"}],
            "highlight": {"value": "+0.82 cm", "label": "体长增长"},
            "table": {"headers": ["处理", "株高(cm)"], "rows": [["对照", "10.2"]]}}]}
硬性要求：
- type 只能取：title, outline, section, background, method, result, conclusion, reference；
- 结构顺序固定：title → outline →（section → 对应内容组）×3 → conclusion（→ reference，若配额里有）；
  type=section 表示章节分隔页：放在 background、method、result 各组第一张之前，
  title 只用组名（如「研究背景」，2~6 字，末尾不得带「是/的/了」等虚词，不要写成句子），
  subtitle 必给（分隔页副标题，渲染在主标题下方）：照抄该章第一张内容页的 title
  （如「黄瓜遗传改良亟需高精度泛基因组」，≤20 字、一句话、与该页标题逐字一致），
  bullets 用空数组（副标题走 subtitle 字段，不要再塞进 bullets），
  evidence/speaker_notes 可省略；
- 每个 type 的页数必须落在给定配额 [min,max] 内，不得自创结构；
- 标题即结论：内容页 title 写成一句话结论或核心问题（≤20 字，看标题就懂这页讲什么），
  「背景/方法/结果」这类栏目词只允许出现在 section 分隔页；
- 叙事：方法页讲机制与做法，结果页说明「证明了什么」而不是罗列做了什么；
- bullets 每页 3-5 条（title 页放作者/单位/来源，outline 页列各部分标题，
  reference 页列 5-8 条文献）；单条 ≤40 字、名词短语化、不写整句；
  关键词强调用 **双星号** 包裹（如 **盐胁迫**）；子要点写成独立数组元素并以「- 」开头
  （如 "- 子要点"，其 evidence 填 null）；
- 版面标记（生成端只按此写法，渲染端自动识别，勿在同一页混用多种）：
  · 并列短要点：方法/结果/结论页若内容就是 2-4 个并列短语，每条 ≤16 字、不带任何标记，
    直接写成这样的短句条目（会渲染为卡片网格）；
  · 关键词行：背景/方法概述页 ≥3 条统一用「关键词：说明」句式（如「靶基因：SOS1」）；
  · 流程：步骤页 ≥3 条统一以「→ 」开头（如「→ 提取 RNA」），会渲染为流程卡；
  · 强调框：全篇最多 1-2 条最核心结论，以「> 」开头单独成条放在该页最后，
    其 evidence 填 null（会渲染为底部强调引用框）；
  · 表格：结果对比页优先用 table 字段（headers ≤5 列、rows ≤4 行、单元格 ≤18 字）
    代替 bullets；该页 bullets 最多 2 条导读，表格里的数据引用写进 speaker_notes；
- evidence 与 bullets 等长对齐（一一对齐，没有来源的位置填 null）；
  每条 evidence 只需要 "quote" 字段：从摘要卡片里逐字照抄 quote 原文；
  **不需要写 para_id，后端会按 quote 自动反查真实段落ID**（你写错会干扰校验）；
  卡片里找不到对应 quote 的一律填 null，严禁改写或编造；
- speaker_notes 是讲稿口吻（80-160 字），可自然转述 quote 但不得出现段落ID；
- suggested_figures 只能从用户消息给出的「论文真实图表清单」里照抄 label 与 para_id，
  清单为空则一律给空数组；你只决定"哪个图放哪页"，绝不发明图号；
- result 组最后 1-2 页若呈现对比数据，给该页加 "highlight": {"value": "≤12字符的数字",
  "label": "≤16字符的说明"}（如 {"value": "+0.82 cm", "label": "体长增长"}）；
  没有可突出的数字对比就省略 highlight 字段；
- 严禁编造引用、数据、图表编号。不要输出 JSON 以外的任何文字。"""


def _plan_table(plan: dict[str, tuple[int, int]], types: tuple[str, ...] | list[str]) -> str:
    return "\n".join(f"- {t}: {plan[t][0]}~{plan[t][1]} 页" for t in types)


def _narrow_figure_index(
    figure_index: list[dict], available: list[str] | None,
) -> list[dict]:
    """v3 修复2：把注入 prompt 的图表清单收窄到「已提取原图」的 label。

    available 为空/None → 不收窄（text 模式保持全清单占位框）；
    交集为空（提取 label 与扫描不一致等意外）→ 回退全清单，绝不把图清空。
    匹配按 canon_label（kind+编号）进行，且把 entry.label 统一成磁盘文件名形态
    （"Fig. 6b" → "Fig 6"，子图后缀无对应文件），同编号去重只留首个。
    """
    if not available or not figure_index:
        return figure_index
    by_key: dict[tuple[str, int], str] = {}
    for lbl in available:
        key = canon_label(lbl)
        if key and key not in by_key:
            by_key[key] = str(lbl).strip()
    if not by_key:
        return figure_index
    narrowed: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for f in figure_index:
        key = canon_label(f.get("label"))
        if key in by_key and key not in seen:
            entry = dict(f)
            entry["label"] = by_key[key]   # LLM 所见 = 磁盘可嵌形态
            narrowed.append(entry)
            seen.add(key)
    return narrowed or figure_index


def _claims_block(claims: list[dict], figures: list[dict]) -> str:
    # para_id 不下发给 reduce/expand（LLM 会照抄编造；quote 由后端反查真实段落ID）
    compact = [
        {"claim": str(c.get("claim") or "")[:200],
         "quote": str(c.get("quote") or "")[:80]}
        for c in claims
    ]
    figs = [
        {"label": str(f.get("label") or "")[:40], "para_id": str(f.get("para_id") or "")}
        for f in figures
    ]
    return json.dumps({"claims": compact, "figures": figs}, ensure_ascii=False)


def _reduce_user(
    scenario: str, minutes: int, plan: dict[str, tuple[int, int]],
    claims: list[dict], figures: list[dict],
    types: list[str], *, prior_titles: list[str] | None = None, total_hint: int | None = None,
    figure_index: list[dict] | None = None,
) -> str:
    parts = [
        f"场景：{SCENARIO_LABELS[scenario]}（{scenario}），汇报时长 {minutes} 分钟，"
        f"目标总页数 {PAGE_TARGETS[minutes][0]}~{PAGE_TARGETS[minutes][1]} 页。",
        f"本次生成的页类型与配额（按顺序）：\n{_plan_table(plan, types)}",
    ]
    if total_hint is not None:
        parts.append(f"本次需生成 {total_hint} 页。")
    if prior_titles:
        parts.append("前半部分已生成的页标题（保持连贯，不要重复）：" + "；".join(prior_titles))
    parts.append(f"摘要卡片：\n{_claims_block(claims, figures)}")
    # P5 增强 1：论文真实图表清单注入（LLM 只选不编）
    if figure_index:
        compact = [
            {"label": f.get("label"), "para_id": f.get("para_id"),
             "caption": str(f.get("caption") or "")[:80]}
            for f in figure_index
        ]
        parts.append(
            "论文中真实存在的图表清单（suggested_figures 只能从这里选，label 与 para_id 照抄）：\n"
            + json.dumps(compact, ensure_ascii=False)
        )
    else:
        parts.append("论文中未扫描到任何图表编号：suggested_figures 一律给空数组。")
    return "\n\n".join(parts)


# ---------- 结构裁决（纯函数） ----------

def coerce_outline(payload: Any) -> list[dict]:
    """LLM 返回 → 规范化 pages（枚举/字段/bullets-evidence 对齐/slide_number）。"""
    pages_raw = payload.get("pages") if isinstance(payload, dict) else None
    if not isinstance(pages_raw, list):
        raise OutlineError("LLM_INVALID", "LLM 输出缺少 pages 列表")
    pages: list[dict] = []
    for raw in pages_raw:
        if not isinstance(raw, dict):
            continue
        ptype = str(raw.get("type") or "")
        if ptype not in PAGE_TYPES:
            continue
        title = str(raw.get("title") or "").strip() or ptype
        if ptype == "section":
            # v3 修复3：LLM 偶发把「是」等虚词缀进组名（如「研究方法是」）
            title = re.sub(r"[是的了]$", "", title) or title
        bullets = [
            str(b).strip() for b in (raw.get("bullets") or [])
            if isinstance(b, (str, int, float)) and str(b).strip()
        ]
        ev_raw = raw.get("evidence")
        evidence: list[dict | None] = []
        for i in range(len(bullets)):
            item = ev_raw[i] if isinstance(ev_raw, list) and i < len(ev_raw) else None
            if isinstance(item, dict) and str(item.get("quote") or "").strip():
                evidence.append({
                    "para_id": str(item.get("para_id") or ""),
                    "quote": str(item.get("quote") or "").strip(),
                    "status": "unverified",
                })
            else:
                evidence.append(None)
        figs_raw = raw.get("suggested_figures")
        figs = []
        if isinstance(figs_raw, list):
            for f in figs_raw[:6]:
                if isinstance(f, dict) and str(f.get("para_id") or "").strip():
                    figs.append({
                        "label": str(f.get("label") or "").strip(),
                        "para_id": str(f.get("para_id") or "").strip(),
                        "status": "unverified",
                    })
        # P5 增强 3：大数字突出页标记（result 组对比数据页，可选）
        hl_raw = raw.get("highlight")
        highlight = None
        if isinstance(hl_raw, dict) and str(hl_raw.get("value") or "").strip():
            highlight = {
                "value": str(hl_raw.get("value") or "").strip()[:24],
                "label": str(hl_raw.get("label") or "").strip()[:32],
            }
        # P5 二次开发 2.2：对比表格（可选；rows 超限截断，坏结构直接丢弃）
        table = None
        tbl_raw = raw.get("table")
        if isinstance(tbl_raw, dict) and isinstance(tbl_raw.get("rows"), list):
            headers = [
                str(h).strip()[:24] for h in (tbl_raw.get("headers") or [])
                if isinstance(h, (str, int, float)) and str(h).strip()
            ][:8]
            rows: list[list[str]] = []
            for r in tbl_raw["rows"][:12]:
                if not isinstance(r, (list, tuple)):
                    continue
                cells = [
                    str(c).strip()[:60] for c in r
                    if isinstance(c, (str, int, float))
                ][:8]
                if any(cells):
                    rows.append(cells)
            if rows:
                table = {"headers": headers, "rows": rows}
        page = {
            "slide_number": 0,
            "type": ptype,
            "title": title,
            "bullets": bullets,
            "evidence": evidence,
            "speaker_notes": str(raw.get("speaker_notes") or "").strip(),
            "suggested_figures": figs,
        }
        if ptype == "section":
            # P5 改进 2：分隔页副标题独立字段（LLM 给 subtitle；旧大纲仍在 bullets[0]，
            # 编辑器改 bullets[0] 也算数 → bullets 优先，避免编辑被旧 subtitle 覆盖）
            page["subtitle"] = (bullets[0] if bullets else "") or str(
                raw.get("subtitle") or ""
            ).strip()
        if highlight:
            page["highlight"] = highlight
        if table:
            page["table"] = table
        pages.append(page)
    if not pages:
        raise OutlineError("LLM_INVALID", "LLM 输出中没有可用页（type 全部非法）")
    return pages


# 超页裁剪优先级：section/reference 可整页删（floor 0），内容页保底 1 页；
# title/outline/conclusion 永不删
_DROP_PRIORITY = ("section", "reference", "background", "method", "result")
_FLOOR_ZERO = ("section", "reference")


def trim_to_max(
    pages: list[dict], hi: int, warnings: list[str],
    floors: dict[str, int] | None = None,
) -> list[dict]:
    """总页数超上限：按 _DROP_PRIORITY 优先级删该类型的最后一页；不凑下限。

    floors（v3 修复3）：按配额给类型设保底（如 section=(3,3) → 传 {"section": 3}）。
    有保底时先删「超配额的内容页/文献页」，section 不再被一刀清空；
    全部 floor 都满足却仍超页 → 才回退到 section/reference 整页删（原语义兜底）。
    """
    floors = floors or {}
    floor_of = lambda t: floors.get(t, 0 if t in _FLOOR_ZERO else 1)  # noqa: E731
    while len(pages) > hi:
        counts: dict[str, int] = {}
        for p in pages:
            counts[p["type"]] = counts.get(p["type"], 0) + 1
        drop_type = next(
            (t for t in _DROP_PRIORITY if counts.get(t, 0) > floor_of(t)),
            None,
        )
        if drop_type is None:
            # 配额 floor 全满足仍超页 → 兜底砍可整删类型（section/reference 优先）
            drop_type = next(
                (t for t in _DROP_PRIORITY
                 if t in _FLOOR_ZERO and counts.get(t, 0) > 0),
                None,
            )
        if drop_type is None:
            break
        idx = max(i for i, p in enumerate(pages) if p["type"] == drop_type)
        removed = pages.pop(idx)
        warnings.append(
            f"页数超过 {hi} 页上限，已移除 {removed['type']} 页「{removed['title']}」"
        )
    return pages


def split_oversized(pages: list[dict], warnings: list[str]) -> list[dict]:
    """内容页 bullets >8：拆成两页（续页 title+"（续）"），递归直到全 ≤8。"""
    out: list[dict] = []
    for page in pages:
        todo = [page]
        while todo:
            cur = todo.pop(0)
            bullets = cur.get("bullets") or []
            if cur["type"] in CONTENT_TYPES and len(bullets) > 8:
                k = math.ceil(len(bullets) / 2)
                ev_full = list(cur.get("evidence") or [])
                if len(ev_full) < len(bullets):
                    ev_full += [None] * (len(bullets) - len(ev_full))
                nxt = json.loads(json.dumps(cur))  # 深拷贝
                nxt["bullets"] = bullets[k:]
                nxt["evidence"] = ev_full[k:]
                nxt["title"] = f"{cur['title']}（续）"
                nxt["suggested_figures"] = []   # 图占位只放首页
                nxt["speaker_notes"] = ""
                cur["bullets"] = bullets[:k]
                cur["evidence"] = ev_full[:k]
                warnings.append(f"「{cur['title'].removesuffix('（续）')}」要点超过 8 条，已拆成两页")
                todo.insert(0, nxt)
                todo.insert(0, cur)
                continue
            out.append(cur)
    return out


def renumber(pages: list[dict]) -> list[dict]:
    for i, p in enumerate(pages, 1):
        p["slide_number"] = i
    return pages


# ---------- 证据程序化校验（纯函数） ----------

_FIG_RE = re.compile(r"^(S\d+-P\d+)(?:-Fig(\d+))?$", re.IGNORECASE)


def _norm(s: str) -> str:
    """归一化：NFKC + 小写 + 去掉全部空白与标点（只留字母数字/CJK）。"""
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"[\W_]+", "", s, flags=re.UNICODE)


def _concat_segments(norm_index: dict[str, str]) -> tuple[str, dict[str, tuple[int, int]]]:
    """按 document 顺序把各段归一化文本拼成一条，并给出每段在拼接串中的 [start, end)。

    LLM 照抄的 quote 常横跨两个段落边界 → 任何单段都不含它，但整篇文档含。
    repair/validate 据此做跨段宽松反查（v3 修复4）。
    """
    offsets: dict[str, tuple[int, int]] = {}
    chunks: list[str] = []
    pos = 0
    for pid, nt in norm_index.items():
        offsets[pid] = (pos, pos + len(nt))
        chunks.append(nt)
        pos += len(nt)
    return "".join(chunks), offsets


def _locate_span(
    nq: str, concat: str, offsets: dict[str, tuple[int, int]],
) -> str | None:
    """跨段定位：nq 起点落在哪个段（取起点所在段为归属）。≥10 归一化字符才启用。"""
    if len(nq) < 10:
        return None
    i = concat.find(nq)
    if i < 0:
        return None
    for pid, (start, end) in offsets.items():
        if start <= i < end:
            return pid
    return None


def repair_evidence_para_ids(pages: list[dict], index: dict[str, str]) -> int:
    """按 quote 归一化子串反查真实 para_id（P5 修复：LLM 常编 para_id，quote 往往逐字正确）。

    LLM reduce/expand 只需给 quote（prompt 已声明后端反查）；这里按 document
    顺序找第一个包含该 quote 的段落填回 para_id，后续 validate_evidence 据此标
    verified。找不到 → 不动，留给 validate_evidence 标 unverified（真幻觉）。
    返回修复条数。
    """
    norm_index = {pid: _norm(t) for pid, t in index.items()}
    concat, offsets = _concat_segments(norm_index)
    repaired = 0
    for page in pages:
        for ev in page.get("evidence") or []:
            if not isinstance(ev, dict):
                continue
            quote = str(ev.get("quote") or "")
            nq = _norm(quote)
            if not nq:
                continue
            pid = str(ev.get("para_id") or "")
            if pid and nq in norm_index.get(pid, ""):
                continue  # 标注已正确，不动
            hit = next((p for p, nt in norm_index.items() if nq in nt), None)
            if not hit:
                # 跨段 quote：任何单段都不含，但在整篇拼接串里 → 归属起点所在段
                hit = _locate_span(nq, concat, offsets)
            if hit:
                ev["para_id"] = hit
                repaired += 1
    return repaired


def validate_evidence(
    pages: list[dict], index: dict[str, str],
    figure_index: list[dict] | None = None,
) -> list[str]:
    """逐条校验 evidence / suggested_figures → status + warnings。

    figure_index（论文真实图表清单）非空时，图引用按清单裁决：
      - label 不在清单 → 直接丢弃（不是标 unverified，结果里不会出现论文没有的图）；
      - label 在清单 → 以清单里的真实 para_id 为准，status=verified。
    为 None 时保持旧路径（格式 + 原文 fig{N} 子串校验），兼容无清单的调用方。
    """
    warnings: list[str] = []
    norm_index = {pid: _norm(t) for pid, t in index.items()}
    doc_all = "".join(norm_index.values())
    concat, offsets = _concat_segments(norm_index)
    fmap = figure_map(figure_index) if figure_index is not None else None
    for page in pages:
        sn = page["slide_number"]
        evs = page.get("evidence") or []
        ver = unver = 0
        for ev in evs:
            if ev is None:
                continue
            quote = str(ev.get("quote") or "")
            pid = str(ev.get("para_id") or "")
            nq, np_ = _norm(quote), norm_index.get(pid)
            if quote and np_ is not None and nq and nq in np_:
                ev["status"] = "verified"
                ver += 1
            elif quote and nq and pid and _locate_span(nq, concat, offsets) == pid:
                # 跨段 quote：repair 已把 para_id 指到起点段 → 等同逐字命中
                ev["status"] = "verified"
                ver += 1
            else:
                ev["status"] = "unverified"
                unver += 1
                if nq and nq in doc_all:
                    warnings.append(
                        f"第{sn}页：引用真实存在，但段落标注 {pid or '（空）'} 不符（已标 unverified）"
                    )
        total = ver + unver
        if page["type"] in ("result", "conclusion") and total and unver / total > 0.5:
            warnings.append(
                f"第{sn}页（{page['type']}）证据未核实占比超过 50%（{unver}/{total}），请人工复核"
            )
        kept: list[dict] = []
        for fig in page.get("suggested_figures") or []:
            if fmap is not None:
                key = canon_label(fig.get("label"))
                entry = fmap.get(key) if key else None
                if entry is None:
                    warnings.append(
                        f"第{sn}页：图引用 {fig.get('label') or '（空）'} 不在论文真实图表清单，已丢弃"
                    )
                    continue
                fig["para_id"] = entry["para_id"]   # 以扫描出的真实位置为准
                fig["status"] = "verified"
                kept.append(fig)
                continue
            m = _FIG_RE.match(str(fig.get("para_id") or ""))
            if not m:
                fig["status"] = "unverified"
                warnings.append(f"第{sn}页：图引用 {fig.get('para_id')} 格式不合法")
                kept.append(fig)
                continue
            base, num = m.group(1).upper(), m.group(2)
            base_norm = norm_index.get(base)
            fig_ok = base_norm is not None and (
                num is None or f"fig{num}" in base_norm or f"figure{num}" in base_norm
            )
            fig["status"] = "verified" if fig_ok else "unverified"
            if not fig_ok:
                warnings.append(
                    f"第{sn}页：图引用 {fig['para_id']} 未在原文对应段落找到（已标 unverified）"
                )
            kept.append(fig)
        page["suggested_figures"] = kept
    return warnings


# ---------- 扩充重试（内容页 bullets <2 → 一次按页补齐） ----------

_EXPAND_SYSTEM = """你是学术汇报编辑。某几页幻灯片的要点不足（少于 2 条）。
基于给出的页信息与摘要卡片（claims 已带逐字引用），为每页补齐到 3-6 条要点。
只输出单个 JSON 对象：
{"pages": [{"index": 0, "bullets": ["..."], "evidence": [{"quote": "..."}]}]}
硬性要求：
- index 是给定页在列表中的下标；bullets 保留原有要点（可精炼改写）并补充同主题新要点；
- evidence 与 bullets 等长对齐；每条 evidence 只需要 "quote" 字段，从卡片里逐字照抄，
  **不需要写 para_id，后端会按 quote 自动反查**；没有依据填 null；
- 单条 ≤60 字；严禁编造。不要输出 JSON 以外的任何文字。"""


async def _expand_thin(
    pages: list[dict], thin_idx: list[int],
    claims: list[dict], figures: list[dict], warnings: list[str],
) -> None:
    """一次 LLM 调用补齐所有薄弱页；失败仅 warning，不阻断。"""
    page_info = [
        {
            "index": i,
            "type": pages[i]["type"],
            "title": pages[i]["title"],
            "bullets": pages[i]["bullets"],
            "speaker_notes": pages[i]["speaker_notes"],
        }
        for i in thin_idx
    ]
    user = (
        f"待补齐页（下标即列表位置）：\n{json.dumps(page_info, ensure_ascii=False)}\n\n"
        f"摘要卡片：\n{_claims_block(claims, figures)}"
    )
    try:
        payload = await llm.complete_json(
            _EXPAND_SYSTEM, user, temperature=0.3,
            max_tokens=4000, timeout=_EXPAND_TIMEOUT,
        )
    except LLMError as exc:
        warnings.append(f"薄弱页扩充重试失败（保留原状）：{exc}")
        return
    for item in payload.get("pages") or []:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        if not isinstance(idx, int) or not (0 <= idx < len(pages)) or idx not in thin_idx:
            continue
        page = pages[idx]
        bullets = [
            str(b).strip() for b in (item.get("bullets") or [])
            if isinstance(b, (str, int, float)) and str(b).strip()
        ][:8]
        if len(bullets) < 2:
            continue
        ev_raw = item.get("evidence")
        evidence: list[dict | None] = []
        for i in range(len(bullets)):
            e = ev_raw[i] if isinstance(ev_raw, list) and i < len(ev_raw) else None
            if isinstance(e, dict) and str(e.get("quote") or "").strip():
                evidence.append({
                    "para_id": str(e.get("para_id") or ""),
                    "quote": str(e.get("quote") or "").strip(),
                    "status": "unverified",
                })
            else:
                evidence.append(None)
        page["bullets"] = bullets
        page["evidence"] = evidence


# ---------- 主编排 ----------

async def generate_outline(
    text: str, scenario: str, minutes: int, style_id: str,
    *,
    available_figures: list[str] | None = None,
) -> tuple[list[dict], list[str], dict[str, Any]]:
    """生成完整大纲。返回 (pages, warnings, meta)；失败抛 OutlineError。

    available_figures（v3 修复2）：PDF 入口已提取到磁盘的原图 label 列表。
    非空时只把「已提取」的图注入 reduce/expand（LLM 选了 build 就能 add_picture
    嵌入，不再出虚线占位）；None/空 → 保持全清单（text 模式占位框不变）。
    """
    if scenario not in SCENARIOS:
        raise OutlineError("VALIDATION", f"未知场景 scenario={scenario}")
    if minutes not in PAGE_TARGETS:
        raise OutlineError("VALIDATION", f"不支持的时长 {minutes} 分钟（可选 {list(MINUTES_CHOICES)}）")
    if style_id not in STYLE_IDS:
        raise OutlineError("VALIDATION", f"未知风格 style_id={style_id}")

    text = (text or "").strip()
    if len(text) < 300:
        raise OutlineError("VALIDATION", "正文过短（<300 字），无法生成汇报大纲")
    settings = get_settings()
    if len(text) > settings.max_text_chars:
        text = text[: settings.max_text_chars]

    blocks, index = build_index(text)
    if not blocks:
        raise OutlineError("VALIDATION", "正文解析为空，无法生成汇报大纲")

    # P5 增强 1：论文真实图表清单（正则扫描，非 LLM）——供 reduce 只选不编
    figure_index = scan_figure_index(index)
    # v3 修复2：有已提取原图 → 注入清单收窄到「磁盘上有图」的条目（交集空则回退全清单）
    inject_index = _narrow_figure_index(figure_index, available_figures)
    fmap_pick = figure_map(inject_index)

    plan = page_plan(scenario, minutes)
    lo, hi = PAGE_TARGETS[minutes]
    warnings: list[str] = []
    llm_calls = 0

    # ---- map（并发 2，控总时长压在 BFF 360s 内） ----
    claims: list[dict] = []
    figures: list[dict] = []
    sem = asyncio.Semaphore(_MAP_CONCURRENCY)

    async def _map_one(block: dict[str, Any]) -> tuple[list[dict], list[dict]]:
        async with sem:
            return await _map_block(block)

    try:
        results = await asyncio.gather(*(_map_one(b) for b in blocks))
    except LLMError as exc:
        raise OutlineError("LLM_ERROR", f"章节摘要失败：{exc}") from exc
    llm_calls += len(results)
    for cs, fs in results:
        for c in cs:
            if len(claims) < _MAX_CLAIMS_TOTAL:
                claims.append(c)
        for f in fs:
            if len(figures) < _MAX_FIGURES_TOTAL:
                figures.append(f)
    # P5 增强 1：map 抽到的图先过一遍注入清单（不在清单 → 丢弃；在 → 用真实 para_id）
    if fmap_pick:
        filtered: list[dict] = []
        for f in figures:
            key = canon_label(f.get("label"))
            entry = fmap_pick.get(key) if key else None
            if entry:
                f = dict(f, para_id=entry["para_id"])
                filtered.append(f)
        if len(filtered) != len(figures):
            warnings.append(
                f"map 阶段 {len(figures) - len(filtered)} 个图引用不在论文真实图表清单，已丢弃"
            )
        figures = filtered
    else:
        if figures:
            warnings.append(f"论文未扫描到图表编号，丢弃 map 阶段 {len(figures)} 个图引用")
        figures = []
    if not claims:
        raise OutlineError("LLM_INVALID", "未能从正文抽取到任何可引用事实（claims 为空）")

    # ---- reduce（>14 页分两片，第二片带上一片标题连贯） ----
    full_types = [t for t in PAGE_TYPES if t in plan]
    split_at = len(full_types)
    acc = 0
    for i, t in enumerate(full_types):
        acc += plan[t][1]
        if acc >= _REDUCE_PAGE_SLICE:
            split_at = i + 1
            break
    first_types, second_types = full_types[:split_at], full_types[split_at:]

    def _types_quota(ts: list[str]) -> int:
        return sum(plan[t][0] for t in ts)

    pages: list[dict] = []
    try:
        payload = await llm.complete_json(
            _REDUCE_SYSTEM,
            _reduce_user(scenario, minutes, plan, claims, figures, first_types,
                         figure_index=inject_index),
            temperature=0.4, max_tokens=8000, timeout=_REDUCE_TIMEOUT,
        )
        llm_calls += 1
        pages = coerce_outline(payload)
        if second_types:
            prior_titles = [p["title"] for p in pages]
            payload2 = await llm.complete_json(
                _REDUCE_SYSTEM,
                _reduce_user(
                    scenario, minutes, plan, claims, figures, second_types,
                    prior_titles=prior_titles, total_hint=_types_quota(second_types),
                    figure_index=inject_index,
                ),
                temperature=0.4, max_tokens=8000, timeout=_REDUCE_TIMEOUT,
            )
            llm_calls += 1
            pages.extend(coerce_outline(payload2))
    except LLMError as exc:
        msg = str(exc)
        if "JSON" in msg or "json" in msg:
            raise OutlineError("LLM_INVALID", f"LLM 大纲输出无法解析：{msg}") from exc
        raise OutlineError("LLM_ERROR", f"大纲生成失败：{msg}") from exc

    # ---- 后校验：拆页 → 夹页数 → 扩充 → 证据 → 重编号 ----
    pages = split_oversized(pages, warnings)
    renumber(pages)
    # v3 修复3：按配额给全类型设保底（先删超配额的页；section/reference 不再被一刀清空）
    floors = {t: mn for t, (mn, _mx) in plan.items() if mn}
    pages = trim_to_max(pages, hi, warnings, floors=floors)
    renumber(pages)
    if len(pages) < lo:
        warnings.append(
            f"生成 {len(pages)} 页，低于 {minutes} 分钟目标下限 {lo} 页（不凑页，请人工补足）"
        )
    # 类型顺序归一：保持 LLM 输出顺序即可，但至少保证 title 在首位
    pages.sort(key=lambda p: (0 if p["type"] == "title" else 1))
    renumber(pages)

    thin_idx = [
        i for i, p in enumerate(pages)
        if p["type"] in CONTENT_TYPES and len(p["bullets"]) < 2
    ]
    if thin_idx:
        await _expand_thin(pages, thin_idx, claims, figures, warnings)
        for i in thin_idx:
            if len(pages[i]["bullets"]) < 2:
                warnings.append(f"第{pages[i]['slide_number']}页要点不足 2 条（扩充后仍不足）")

    # P5 修复：LLM 只给 quote（或编 para_id）→ 按 quote 反查真实段落ID，再进校验
    repaired = repair_evidence_para_ids(pages, index)
    if repaired:
        warnings.append(f"{repaired} 条 evidence 的段落标注已按 quote 反查修正")
    warnings.extend(validate_evidence(pages, index, inject_index))
    renumber(pages)

    unverified_total = sum(
        1 for p in pages for e in (p.get("evidence") or []) if e and e.get("status") == "unverified"
    )
    meta = {
        "scenario": scenario,
        "minutes": minutes,
        "style_id": style_id,
        "paragraphs": len(index),
        "sections": len(blocks),
        "claims": len(claims),
        "figures": len(figure_index),          # 真实图表清单规模（扫描所得）
        "figures_used": sum(                   # 大纲里实际引用且通过校验的图数
            1 for p in pages for f in (p.get("suggested_figures") or [])
            if f.get("status") == "verified"
        ),
        "llm_calls": llm_calls,
        "target_pages": [lo, hi],
        "unverified_evidence": unverified_total,
    }
    return pages, warnings, meta
