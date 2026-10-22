r"""services/reformat.py —— P1 论文重排版（.tex 源码级规则变换，不编译）。

链路：resolve_rules（三级规则解析）→ reformat_tex（4 变换器 + 结构守卫）
      → 路由层落盘 data/reformat_jobs/{job_id}.tex/.json。
纯规则、零 LLM、零执行用户内容、不碰 docx/pdf（那两个只在 FormatPanel 审核）。

拍板（用户 4 点确认，2026-10-10）：
1. changes[] 每条带 rule_key ∈ {font_pt, margin, line_spacing, bib_style}、
   action ∈ {modified, inserted, warning}（P1 只产生 modified/warning，
   inserted 留给后续阶段）；前端按 rule_key 着色、按 action 计数。
2. 保守插入策略：命令/选项缺失一律【不改源码】→ changes 里 action=warning，
   reason 附"建议手动添加"的代码片段；warning 不计入"已修改"计数。
   （warnings[] 顶层数组只放结构级提示，如多处 \documentclass。）
3. target_journal 必填；"default"= 通用规则（min_font_pt=11, min_margin_mm=20,
   min_line_spacing=1.5, refs_style=apa），前端下拉首项即通用。
4. P1 仅 .tex；结构畸形宁拒不猜（PARSER_FAILED）。

铁律：
① 只在违反规则时改（合规零触碰 → 重跑幂等，changes=[]）
② 只收紧不放松：字号/行距/边距只调大、绝不调小
③ 每条修改必须能回答"为什么"：reason 带原值/目标值/规则

规则解析链（resolve_rules）：
  "default"                                  → 内置通用规则
  journals.json id/name 命中                 → data/format_cache/{id}.json ok → fetched
                                             → 否则 format_rules 非 placeholder → official
                                             → 否则通用规则 + 占位说明
  形如 "en:xxx"/"zh:xxx"（journal_directory）→ 只读查目录拿 name → 回链上一步
  其他字符串（期刊名）                       → 回链 journals.json → 未中 → 通用 + 未匹配说明
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# review_service/app/services/ → parents[2] = review_service/
_ROOT = Path(__file__).resolve().parents[2]
_SEED_PATH = _ROOT / "data" / "journals" / "journals.json"
_CACHE_DIR = _ROOT / "data" / "format_cache"

_EPS = 1e-9

# 通用规则（拍板 3：与 journals.json format_rules_default 的 9/15/1.15 不同，
# 这是重排版自己的"通用规则"口径，勿混用）
RULES_DEFAULT: dict[str, Any] = {
    "rules_source": "default",
    "max_pages": 10,
    "min_font_pt": 11,
    "min_margin_mm": 20.0,
    "min_line_spacing": 1.5,
    "min_figures": 1,
    "max_fonts": 6,
    "refs_style": "apa",
    "anonymous": None,
}

# 参考文献样式 → 经典 .bst（均为发行版基础件）。
# 修正（2026-10-10）：author_year 旧映射 plain 是错的——plain.bst 是编号制 [1]；
# author-year 制应映射 apalike.bst（Author, 2020）。apa 同理。
REFS_BST_MAP: dict[str, str] = {
    "apa": "apalike",
    "author_year": "apalike",
    "numbered": "unsrt",
    "vancouver": "unsrt",
    "gb7714": "gb7714",
}
# biblatex 工作流的 style= 建议名（与 .bst 是两套体系）
BIBLATEX_STYLE_MAP: dict[str, str] = {
    "apa": "apa",
    "author_year": "authoryear",
    "numbered": "numeric",
    "vancouver": "numeric",
    "gb7714": "gb7714",
}

_DOCCLASS_RE = re.compile(r"(\\documentclass)\s*(\[[^\]]*\])?\s*\{([^}]*)\}")
_PKG_RE = re.compile(r"\\(?:usepackage|RequirePackage)\s*(\[[^\]]*\])?\s*\{([^}]*)\}")
_SPACING_RE = re.compile(r"\\(?:linespread|setstretch)\s*\{\s*(\d+(?:\.\d+)?)\s*\}")
_BIBSTYLE_RE = re.compile(r"\\bibliographystyle\s*\{\s*([^}]+?)\s*\}")
_PT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*pt\b")
_BEGIN_DOC_RE = re.compile(r"\\begin\s*\{\s*document\s*\}")
_BIBLATEX_RE = re.compile(r"\\(?:printbibliography|addbibresource)")

_MM_FACTORS = {"mm": 1.0, "cm": 10.0, "in": 25.4, "inch": 25.4, "pt": 25.4 / 72.0}
_SIDE_KEYS = ("left", "right", "top", "bottom")
_MARGIN_ATTRS = ("margin", "hmargin", "vmargin", "left", "right", "top", "bottom",
                 "inner", "outer")


class ReformatError(Exception):
    """结构畸形无法安全处理（code: PARSER_FAILED）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ================================================================ 规则解析链

def _seed_entries() -> list[dict[str, Any]]:
    try:
        data = json.loads(_SEED_PATH.read_text("utf-8"))
        entries = data.get("journals")
        return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []
    except (OSError, ValueError):
        return []


def _find_seed(text: str) -> dict[str, Any] | None:
    low = text.strip().casefold()
    if not low:
        return None
    for e in _seed_entries():
        if str(e.get("id", "")).casefold() == low or str(e.get("name", "")).casefold() == low:
            return e
    return None


def _cache_rules(journal_id: str) -> dict[str, Any] | None:
    """data/format_cache/{id}.json（抓取+LLM 抽取产物）；ok=false/缺失 → None。"""
    try:
        payload = json.loads((_CACHE_DIR / f"{journal_id}.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not payload.get("ok"):
        return None
    rules = payload.get("format_rules")
    return rules if isinstance(rules, dict) and rules.get("rules_source") else None


def _merged(rules: dict[str, Any]) -> dict[str, Any]:
    """专属规则叠加在通用规则上（缺失键/显式 null 保留——fetched 的 null 是诚实未知）。"""
    return {**RULES_DEFAULT, **rules}


async def _directory_name(journal_id: str) -> str | None:
    """journal_directory id → name（只读；表未连/不存在 → None，绝不抛错）。"""
    try:
        from ..db import db  # 惰性导入：resolver 可在无 lifespan 的测试中工作
        row = await db.fetchone(
            "SELECT name FROM journal_directory WHERE id = ?", (journal_id,)
        )
        return str(row["name"]) if row and row.get("name") else None
    except Exception:  # noqa: BLE001 —— 查不到/未连接都按未匹配处理
        return None


async def resolve_rules(target: str) -> dict[str, Any]:
    """→ {journal_id, journal_name, rules, rules_source, rules_note}（永不失败）。"""
    target = (target or "").strip()

    if target == "default":
        return {
            "journal_id": "default",
            "journal_name": "通用规则",
            "rules": dict(RULES_DEFAULT),
            "rules_source": "default",
            "rules_note": "通用规则（APA + 通用论文规范）",
        }

    display_name = target
    entry = _find_seed(target)
    if entry is None and ":" in target:
        # journal_directory id（en:0028-0836 / zh:slug）→ 目录查 name → 再回链种子
        name = await _directory_name(target)
        if name:
            display_name = name
            entry = _find_seed(name)
    elif entry is None:
        display_name = target  # 直接当期刊名回链

    if entry is not None:
        jid = str(entry.get("id") or "")
        jname = str(entry.get("name") or display_name)
        cached = _cache_rules(jid)
        if cached:
            return {
                "journal_id": jid, "journal_name": jname, "rules": _merged(cached),
                "rules_source": "fetched",
                "rules_note": "该刊规则来自官网抓取 + LLM 抽取（data/format_cache，待人工核验）",
            }
        fr = entry.get("format_rules") if isinstance(entry.get("format_rules"), dict) else {}
        if fr and fr.get("rules_source") not in (None, "placeholder", "default"):
            return {
                "journal_id": jid, "journal_name": jname, "rules": _merged(fr),
                "rules_source": str(fr.get("rules_source")),
                "rules_note": "journals.json 期刊专属规则",
            }
        return {
            "journal_id": jid, "journal_name": jname, "rules": dict(RULES_DEFAULT),
            "rules_source": "default",
            "rules_note": f"「{jname}」在 journals.json 中仅有占位规则（placeholder），已用通用规则",
        }

    return {
        "journal_id": None,
        "journal_name": display_name or target,
        "rules": dict(RULES_DEFAULT),
        "rules_source": "default",
        "rules_note": f"未匹配到「{display_name or target}」的专属格式规则，已用通用规则",
    }


# ================================================================ 变换器工具

def _change(line_no: int, original: str, modified: str, reason: str,
            rule_key: str, action: str) -> dict[str, Any]:
    return {
        "line": line_no,
        "original": original.rstrip("\n"),
        "modified": modified.rstrip("\n"),
        "reason": reason,
        "rule_key": rule_key,
        "action": action,
    }


def _warning_change(line_no: int, original: str, reason: str, rule_key: str) -> dict[str, Any]:
    return _change(line_no, original, "", reason, rule_key, "warning")


def _parse_mm(raw: str) -> float | None:
    """geometry 长度值 → mm；裸数字按 TeX 惯例 pt（同 format_check 先例）；未知 → None。"""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([A-Za-z]*)\s*", raw or "")
    if not m:
        return None
    unit = (m.group(2) or "pt").lower()
    factor = _MM_FACTORS.get(unit)
    if factor is None:
        return None
    return float(m.group(1)) * factor


def _fmt(v: float) -> str:
    return f"{v:g}"


# ================================================================ ① 字号 font_pt

def _transform_font(lines: list[str], orig: list[str], rules: dict[str, Any],
                    doc_idx: int) -> list[dict[str, Any]]:
    min_pt = rules.get("min_font_pt")
    if min_pt is None:
        return []
    min_pt = float(min_pt)
    line = lines[doc_idx]
    m = _DOCCLASS_RE.search(line)
    if not m:  # 守卫已保证，理论不可达
        return []
    opts = m.group(2)  # "[...]" 或 None
    cls = m.group(3).strip()
    opts_inner = opts[1:-1] if opts else ""
    pt_m = _PT_RE.search(opts_inner)

    if pt_m is None:
        # 拍板 2：选项缺失不自动加 → warning + 建议片段
        if opts_inner:
            suggestion = f"\\documentclass[{opts_inner}, {_fmt(min_pt)}pt]{{{cls}}}"
        else:
            suggestion = f"\\documentclass[{_fmt(min_pt)}pt]{{{cls}}}"
        return [_warning_change(
            doc_idx + 1, orig[doc_idx],
            f"未检测到字号选项，无法核验正文字号（目标下限 {_fmt(min_pt)}pt），"
            f"建议手动声明：{suggestion}",
            "font_pt",
        )]

    cur = float(pt_m.group(1))
    if cur >= min_pt - _EPS:
        return []
    target = next((s for s in (10, 11, 12) if s >= min_pt - _EPS), min_pt)
    new_inner = opts_inner[:pt_m.start(1)] + _fmt(target) + opts_inner[pt_m.end(1):]
    new_line = line[:m.start(2) + 1] + new_inner + line[m.start(2) + 1 + len(opts_inner):]
    lines[doc_idx] = new_line
    return [_change(
        doc_idx + 1, orig[doc_idx], new_line,
        f"正文字号 {_fmt(cur)}pt 低于目标下限 {_fmt(min_pt)}pt，改为 {_fmt(target)}pt",
        "font_pt", "modified",
    )]


# ================================================================ ② 页边距 margin

def _find_geometry(lines: list[str]) -> tuple[int, re.Match] | None:
    for i, line in enumerate(lines):
        for m in _PKG_RE.finditer(line):
            pkgs = [p.strip().lower() for p in m.group(2).split(",")]
            if "geometry" in pkgs:
                return i, m
    return None


def _patch_attr(line: str, key: str, new_val: str) -> str:
    """geometry 选项里 key=old → key=new（首个匹配）。

    值止于 `,` 或 `]`——绝不能吞掉 `]{geometry}` 尾巴（否则下一轮就找不到
    geometry 宏包，破坏幂等）。key 带 \\b 防 hmargin/margin 互撞。
    """
    return re.sub(rf"(\b{key}\s*=\s*)([^,\]]+)", lambda m: m.group(1) + new_val,
                  line, count=1)


def _transform_margin(lines: list[str], orig: list[str], rules: dict[str, Any],
                      doc_idx: int) -> list[dict[str, Any]]:
    min_mm = rules.get("min_margin_mm")
    if min_mm is None:
        return []
    min_mm = float(min_mm)
    out: list[dict[str, Any]] = []
    found = _find_geometry(lines)

    if found is None:
        return [_warning_change(
            doc_idx + 1, orig[doc_idx],
            f"未检测到 geometry 宏包，无法核验页边距（目标下限 {_fmt(min_mm)}mm），"
            f"建议手动添加：\\usepackage[margin={_fmt(min_mm)}mm]{{geometry}}"
            f"（置于 \\documentclass 之后）",
            "margin",
        )]

    gi, gm = found
    opts = gm.group(1)
    if opts is None:
        # \usepackage{geometry} 裸用 → 建议加选项（拍板 2 不自动改）
        return [_warning_change(
            gi + 1, orig[gi],
            f"geometry 未设置边距选项，无法核验页边距（目标下限 {_fmt(min_mm)}mm），"
            f"建议手动改为：\\usepackage[margin={_fmt(min_mm)}mm]{{geometry}}",
            "margin",
        )]
    opts_inner = opts[1:-1]
    attrs: dict[str, str] = {}
    for part in opts_inner.split(","):
        if "=" in part:
            k, _, v = part.partition("=")
            attrs[k.strip().lower()] = v.strip()

    present = [k for k in _MARGIN_ATTRS if k in attrs]
    if not present:
        return [_warning_change(
            gi + 1, orig[gi],
            f"geometry 未设置边距相关选项（目标下限 {_fmt(min_mm)}mm），"
            f"建议在现有选项中添加：margin={_fmt(min_mm)}mm",
            "margin",
        )]

    # 逐边解析提供者（side > h/vmargin > margin；inner/outer 单独按自身长度评）
    def provider_for(side: str) -> str | None:
        if side in attrs:
            return side
        if side in ("left", "right") and "hmargin" in attrs:
            return "hmargin"
        if side in ("top", "bottom") and "vmargin" in attrs:
            return "vmargin"
        if "margin" in attrs:
            return "margin"
        if side in ("left", "right") and "inner" in attrs:
            return "inner"  # 见下：inner/outer 单独评估，此处不按边处理
        if side in ("left", "right") and "outer" in attrs:
            return "outer"
        return None

    patch_keys: dict[str, str] = {}      # attr → 新值（同键只抬一次）
    reasons: list[str] = []
    unresolved: list[str] = []

    for side in _SIDE_KEYS:
        pkey = provider_for(side)
        if pkey in ("inner", "outer"):
            continue  # 由下方 inner/outer 评估负责
        if pkey is None:
            unresolved.append(side)
            continue
        mm = _parse_mm(attrs.get(pkey, ""))
        if mm is None:
            unresolved.append(side)
            continue
        if mm < min_mm - _EPS and pkey not in patch_keys:
            patch_keys[pkey] = f"{_fmt(min_mm)}mm"
            reasons.append(f"页边距 {side}({_fmt(mm)}mm) 低于下限 {_fmt(min_mm)}mm → {_fmt(min_mm)}mm")

    for key in ("inner", "outer"):
        if key in attrs:
            mm = _parse_mm(attrs[key])
            if mm is None:
                unresolved.append(key)
            elif mm < min_mm - _EPS and key not in patch_keys:
                patch_keys[key] = f"{_fmt(min_mm)}mm"
                reasons.append(f"页边距 {key}({_fmt(mm)}mm) 低于下限 {_fmt(min_mm)}mm → {_fmt(min_mm)}mm")

    out: list[dict[str, Any]] = []
    if patch_keys:
        new_line = lines[gi]
        for key, val in patch_keys.items():
            new_line = _patch_attr(new_line, key, val)
        lines[gi] = new_line
        out.append(_change(
            gi + 1, orig[gi], new_line, "；".join(reasons), "margin", "modified",
        ))

    if unresolved:
        uniq = "、".join(dict.fromkeys(unresolved))
        out.append(_warning_change(
            gi + 1, orig[gi],
            f"geometry 未设置/无法解析 {uniq} 边（沿用类默认，无法核验），"
            f"建议显式声明：{uniq}={_fmt(min_mm)}mm",
            "margin",
        ))
    return out


# ================================================================ ③ 行距 line_spacing

def _transform_spacing(lines: list[str], orig: list[str], rules: dict[str, Any],
                       doc_idx: int) -> list[dict[str, Any]]:
    min_ls = rules.get("min_line_spacing")
    if min_ls is None:
        return []
    min_ls = float(min_ls)

    # 只看导言区（\begin{document} 之前）；正文中的 \setstretch 是局部覆盖，不碰
    end = len(lines)
    for i, line in enumerate(lines):
        if _BEGIN_DOC_RE.search(line):
            end = i
            break
    records: list[tuple[int, re.Match]] = []
    for i in range(end):
        for m in _SPACING_RE.finditer(lines[i]):
            records.append((i, m))

    if not records:
        return [_warning_change(
            doc_idx + 1, orig[doc_idx],
            f"未在导言区检测到 \\linespread 或 \\setstretch，无法核验行距"
            f"（目标下限 {_fmt(min_ls)}），建议手动添加到导言区："
            f"\\linespread{{{_fmt(min_ls)}}}",
            "line_spacing",
        )]

    i, m = records[-1]  # 最后一次出现者生效（LaTeX 后设覆盖前设）
    cur = float(m.group(1))
    if cur >= min_ls - _EPS:
        return []
    new_line = lines[i][:m.start(1)] + _fmt(min_ls) + lines[i][m.end(1):]
    lines[i] = new_line
    return [_change(
        i + 1, orig[i], new_line,
        f"行距 {_fmt(cur)} 低于目标下限 {_fmt(min_ls)}，改为 {_fmt(min_ls)}",
        "line_spacing", "modified",
    )]


# ================================================================ ④ 参考文献 bib_style

def _transform_bib(lines: list[str], orig: list[str], rules: dict[str, Any],
                   doc_idx: int) -> list[dict[str, Any]]:
    style = str(rules.get("refs_style") or "").strip().lower()
    if not style:
        # 规则未提供已确认的 refs_style（如 Frontiers 两种制式并存待核实）：
        # 绝不猜测改写用户样式——保持原样 + warning 提示（拍板 2026-10-10）
        return [_warning_change(
            doc_idx + 1, orig[doc_idx],
            "该刊引用风格未确认，未改动 \\bibliographystyle；"
            "建议人工核实期刊投稿指南后再调整",
            "bib_style",
        )]

    m = next((mm for line in lines if (mm := _BIBSTYLE_RE.search(line))), None)
    if m is not None:
        i = next(idx for idx, line in enumerate(lines) if _BIBSTYLE_RE.search(line))
        cur = m.group(1).strip()
        if style not in REFS_BST_MAP:
            return [_warning_change(
                i + 1, orig[i],
                f"目标参考文献样式 {style} 无对应标准 .bst，未替换"
                f"（当前 \\bibliographystyle{{{cur}}}），请按目标期刊模板手动选择样式",
                "bib_style",
            )]
        target = REFS_BST_MAP[style]
        if cur.casefold() == target.casefold():
            return []
        new_line = lines[i][:m.start(1)] + target + lines[i][m.end(1):]
        lines[i] = new_line
        hint = "（需 gbt7714 宏包）" if target == "gb7714" else ""
        return [_change(
            i + 1, orig[i], new_line,
            f"参考文献样式要求 {style}：\\bibliographystyle{{{cur}}} → {{{target}}}{hint}",
            "bib_style", "modified",
        )]

    has_biblatex = any(_BIBLATEX_RE.search(line) for line in lines)
    if has_biblatex:
        suggest = BIBLATEX_STYLE_MAP.get(style, style)
        return [_warning_change(
            doc_idx + 1, orig[doc_idx],
            f"检测到 biblatex 工作流（未找到 \\bibliographystyle），"
            f"请在 \\printbibliography 选项中手动配置：style={suggest}（对应 {style}）",
            "bib_style",
        )]

    if style in REFS_BST_MAP:
        target = REFS_BST_MAP[style]
        return [_warning_change(
            doc_idx + 1, orig[doc_idx],
            f"未检测到 \\bibliographystyle，建议手动添加：\\bibliographystyle{{{target}}}"
            f"（对应 {style}）",
            "bib_style",
        )]
    return [_warning_change(
        doc_idx + 1, orig[doc_idx],
        f"未检测到 \\bibliographystyle，目标样式 {style} 无标准 .bst 映射，"
        f"请按期刊模板手动配置",
        "bib_style",
    )]


# ================================================================ 入口

def reformat_tex(source: str, rules: dict[str, Any]) -> dict[str, Any]:
    """源码 + 规则 → {modified_tex, changes[], warnings[]}。

    结构守卫：0 处 \\documentclass → ReformatError(PARSER_FAILED)；
    >1 处 → 只处理第一处 + warnings 补 MULTI_DOCUMENTCLASS。
    """
    lines = source.splitlines(keepends=True)
    doc_idxs = [i for i, line in enumerate(lines) if _DOCCLASS_RE.search(line)]
    if not doc_idxs:
        raise ReformatError(
            "PARSER_FAILED",
            "未找到 \\documentclass{...}：无法确定主文档"
            "（P1 不支持由 \\input 拆分的多文件工程，请上传完整单文件源码）",
        )
    warnings: list[dict[str, str]] = []
    if len(doc_idxs) > 1:
        warnings.append({
            "code": "MULTI_DOCUMENTCLASS",
            "message": f"检测到 {len(doc_idxs)} 处 \\documentclass，"
                       f"仅处理第一处（第 {doc_idxs[0] + 1} 行）",
        })
    doc_idx = doc_idxs[0]
    orig = list(lines)  # 纯净快照：所有 change.original 取自这里

    changes: list[dict[str, Any]] = []
    changes += _transform_font(lines, orig, rules, doc_idx)
    changes += _transform_margin(lines, orig, rules, doc_idx)
    changes += _transform_spacing(lines, orig, rules, doc_idx)
    changes += _transform_bib(lines, orig, rules, doc_idx)
    changes.sort(key=lambda c: c["line"])

    return {"modified_tex": "".join(lines), "changes": changes, "warnings": warnings}
