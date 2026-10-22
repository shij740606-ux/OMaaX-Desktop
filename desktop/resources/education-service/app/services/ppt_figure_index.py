"""services/ppt_figure_index.py —— 论文真实图表清单扫描（P5 增强 1）。

问题根因：LLM 不知道论文里真实图编号与位置，suggested_figures 全靠编。
修复：用正则从论文全文扫描 Figure/Fig./图 + Table/表 编号 → figure_index
（[{label, para_id, caption, kind}]），注入 reduce prompt 供 LLM「只选不编」，
代码侧再按清单校验：不在清单里的图引用直接丢弃（而非标 unverified）。

para_id 与 ppt_outline.build_index 的分段完全一致（复用同一套分段逻辑），
因此 figure_index 的 para_id 可直接用于证据链与前端展示「Fig 1（论文 S1-P12）」。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FIG_RE = re.compile(r"(?:Figure|Fig\.?|图)\s*(\d+)")
TABLE_RE = re.compile(r"(?:Table|表)\s*(\d+)")
_CAPTION_MARK = re.compile(r"\s*[:：.]")     # 紧跟编号的冒号/句点 → caption 形态
_SENT_SPLIT = re.compile(r"(?<=[.!?。！？;；])\s*")
_MAX_CAPTION = 120

# label → 规约键：("fig", n) / ("table", n)（"Figure 2"/"Fig. 2"/"图 2"/"Fig 2" 同键）
_LABEL_KIND = re.compile(r"^\s*(figure|fig|图|table|表)", re.IGNORECASE)

# 行首 caption（借 paper-skills pdf_snap 的分隔符全集：:：. 与 |；pdf_snap MIT）
_CAPTION_LINE = re.compile(
    r"^\s*(Figure|Fig\.?|Table|Tab\.?|图|表|Algorithm|算法)\s*"
    r"([0-9]+[a-z]?|[A-Z]\.?[0-9]+)\s*[:：.|]\s*(\S.*)$", re.IGNORECASE,
)


def canon_label(label: Any) -> tuple[str, int] | None:
    """图/表 label 归一化 → (kind, number)；无法解析返回 None。"""
    s = str(label or "").strip()
    m = _LABEL_KIND.match(s)
    digits = re.search(r"(\d+)", s)
    if not m or not digits:
        return None
    kind = "table" if m.group(1).lower() in ("table", "表") else "fig"
    return (kind, int(digits.group(1)))


def figure_map(figure_index: list[dict[str, Any]] | None) -> dict[tuple[str, int], dict[str, Any]]:
    """figure_index → {("fig",2): entry} 查找表（校验用）。"""
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for entry in figure_index or []:
        key = canon_label(entry.get("label"))
        if key and key not in out:
            out[key] = entry
    return out


def _sentence(paragraph: str, span: tuple[int, int]) -> str:
    """取包含命中的句子（截 120 字符）作为 caption。"""
    start = 0
    best = paragraph
    for part in _SENT_SPLIT.split(paragraph):
        end = start + len(part)
        if start <= span[0] < end:
            best = part
            break
        start = end
    return best.strip()[:_MAX_CAPTION]


def _scan(index: dict[str, str]) -> list[dict[str, Any]]:
    """para_id→原文 索引 → 真实图表清单（按 (kind,编号) 去重）。"""
    found: dict[tuple[str, int], tuple[int, dict[str, Any]]] = {}
    for para_id, para_text in index.items():
        for regex, kind in ((FIG_RE, "figure"), (TABLE_RE, "table")):
            for m in regex.finditer(para_text):
                key = (kind, int(m.group(1)))
                # caption 形态（"Figure 2: ..."）优先于顺带提及；同分先到先得
                score = 2 if _CAPTION_MARK.match(para_text[m.end(): m.end() + 4]) else 1
                hit = {
                    "label": f"Fig {m.group(1)}" if kind == "figure" else f"Table {m.group(1)}",
                    "para_id": para_id,
                    "caption": _sentence(para_text, m.span()),
                    "kind": kind,
                }
                if key not in found or score > found[key][0]:
                    found[key] = (score, hit)
    return [entry for _, entry in found.values()]


def scan_figure_index(index: dict[str, str]) -> list[dict[str, Any]]:
    """para_id→原文 索引 → 图表清单（纯函数，可单测）。"""
    return _scan(index)


def build_figure_index(text: str) -> list[dict[str, Any]]:
    """论文全文 → 图表清单（与证据链共用同一套段落分段，para_id 一致）。"""
    from .ppt_outline import build_index  # 局部导入避免与 ppt_outline 循环依赖
    _, index = build_index(text)
    return _scan(index)


# ---------- PDF 原图提取（P5 增强 2.3；AGENTS 例外：vstore 只回文本，图片须本地解档） ----------

_BAND_ABOVE_PT = 300.0   # caption 上方搜索带（图底边距 caption 的最大距离，pt）
_BAND_PAD_X_PT = 40.0    # caption 左右扩展（横向重叠判定）
_MIN_PX = (60, 40)       # 裁切结果最小像素（过滤图标/装饰线）
_DPI = 300


def extract_figures(pdf_bytes: bytes, out_dir: Path) -> list[dict[str, Any]]:
    """PDF → 与 caption 配对的原图 PNG（out_dir/Fig_1.png…）+ 清单。

    策略（自 paper-skills pdf_snap「页渲染+裁图」与 paper-ppt-skill Step5 裁剪规则
    自动化而来）：同页 caption 行上方 band 内「嵌入位图 ∪ 矢量绘图」联合包围盒
    → clip 渲染 300dpi。Table 跳过（表格不出栅格图，由占位/表格版式承接）。
    配对失败/过小 → 跳过（build 侧回落文字占位），绝不抛错阻塞建 job。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict[str, Any]] = {}
    try:
        import pymupdf  # lock 中已有（format_check 同款）
    except ImportError:  # pragma: no cover
        logger.warning("pymupdf 不可用，跳过图表原图提取")
        return []
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        logger.warning("PDF 打开失败，跳过图表原图提取：%s", exc)
        return []
    try:
        for pno in range(doc.page_count):
            page = doc[pno]
            caps = _caption_lines(page)
            if not caps:
                continue
            img_rects = _image_rects(page)
            for cap in caps:
                if cap["kind"] != "fig":
                    continue
                key = f"Fig {cap['num']}"
                if key in results:
                    continue
                box = _figure_box(page, cap, img_rects)
                if box is None:
                    continue
                try:
                    pix = page.get_pixmap(clip=box, dpi=_DPI)
                except Exception:  # noqa: BLE001 - clip 越界等
                    continue
                if pix.width < _MIN_PX[0] or pix.height < _MIN_PX[1]:
                    continue
                fname = f"Fig_{cap['num']}.png"
                try:
                    pix.save(str(out / fname))
                except OSError:
                    continue
                results[key] = {
                    "label": key, "file": fname, "page": pno + 1,
                    "caption": cap["text"][:_MAX_CAPTION],
                    "width": pix.width, "height": pix.height,
                }
    finally:
        doc.close()
    ordered = sorted(results.values(), key=lambda r: int(r["label"].split()[1]))
    logger.info("图表原图提取：%d 张 → %s", len(ordered), out)
    return ordered


def _caption_lines(page: Any) -> list[dict[str, Any]]:
    """页内行首 caption → [{kind, num, text, x0, x1, y0}]（每编号取最先一行）。"""
    caps: dict[str, dict[str, Any]] = {}
    try:
        data = page.get_text("dict")
    except Exception:  # noqa: BLE001
        return []
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(sp.get("text", "") for sp in line.get("spans", []))
            m = _CAPTION_LINE.match(text)
            if not m:
                continue
            raw_kind = m.group(1).rstrip(".").lower()   # "Fig." → "fig"（v3：剥尾点）
            if raw_kind in ("figure", "fig", "图"):
                kind = "fig"
            elif raw_kind in ("table", "tab", "表"):
                kind = "table"
            else:
                continue  # Algorithm 等：清单不含，原图也跳过
            num = m.group(2)
            digits = re.match(r"(\d+)", num)
            if not digits:
                continue
            key = f"{kind}{digits.group(1)}"
            if key in caps:
                continue
            x0, y0, x1, y1 = line["bbox"]
            caps[key] = {
                "kind": kind, "num": int(digits.group(1)),
                "text": m.group(3).strip(),
                "x0": float(x0), "x1": float(x1), "y0": float(y0),
            }
    return list(caps.values())


def _image_rects(page: Any) -> list[Any]:
    """页内嵌入位图的 rect 列表（失败返回空）。"""
    rects = []
    try:
        for info in page.get_image_info(xrefs=True):
            xref = info.get("xref")
            if not xref:
                continue
            for r in page.get_image_rects(xref):
                rects.append(r)
    except Exception:  # noqa: BLE001
        return []
    return rects


def _figure_box(page: Any, cap: dict[str, Any], img_rects: list[Any]) -> Any | None:
    """caption 上方 band 内「位图 ∪ 矢量绘图」联合包围盒（含 6pt 内边距）。"""
    import pymupdf  # 局部导入（extract_figures 已保证可用）

    page_rect = page.rect
    band_y0 = cap["y0"] - _BAND_ABOVE_PT
    band_y1 = cap["y0"] - 1.0
    bx0 = cap["x0"] - _BAND_PAD_X_PT
    bx1 = cap["x1"] + _BAND_PAD_X_PT

    def _in_band(r: Any) -> bool:
        return band_y0 <= float(r.y1) <= band_y1 and not (float(r.x1) < bx0 or float(r.x0) > bx1)

    union = None

    def _merge(r: Any) -> None:
        nonlocal union
        r = r & page_rect
        if r.is_empty:
            return
        union = r if union is None else (union | r)

    for r in img_rects:
        if _in_band(r):
            _merge(r)
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001
        drawings = []
    for d in drawings:
        r = d.get("rect")
        if r is None or r.is_empty:
            continue
        # 细长规则线（页眉横线/表格线）不进联合，避免整页宽条
        if float(r.height) < 1.5 or (float(r.width) < 1.5):
            continue
        if _in_band(r):
            _merge(r)
    if union is None:
        return None
    box = pymupdf.Rect(union.x0 - 6, union.y0 - 6, union.x1 + 6, union.y1 + 6) & page_rect
    return None if box.is_empty else box
