"""services/ppt_preview.py —— pptx → PNG 页预览（PIL 自渲染，读回 python-pptx 对象）。

移植自交付脚本 /tmp/opencode/render_pptx_preview.py（本项目自有验证脚本），
用途：build 后给前端缩略图条 / 大图预览（浏览器内直接目检版式）。
非 PowerPoint 截图：几何/颜色/字体色忠实还原，字形以 Noto CJK 近似（预览够用）。

依赖仅 Pillow + python-pptx（均已在 tooling/requirements.lock），零新依赖。
约定：输出 out_dir/slide_{i:02d}.png（i 从 1 起），1280×720 @96dpi；
损坏/空 pptx → 返回 []（调用方降级，不抛）。
"""
from __future__ import annotations

import logging
import tempfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Emu

logger = logging.getLogger(__name__)

DPI = 96
SLIDE_W_IN, SLIDE_H_IN = 13.333, 7.5

# 字体 fallback 链：Noto CJK（中英混排）→ DejaVu（兜底，缺 CJK 也不崩）
_FONT_CANDIDATES_REG = (
    "C:/Windows/Fonts/msyh.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
_FONT_CANDIDATES_BOLD = (
    "C:/Windows/Fonts/msyhbd.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def _first_existing(paths: tuple[str, ...]) -> str:
    for p in paths:
        if Path(p).is_file():
            return p
    return paths[-1]


_FONT_REG = _first_existing(_FONT_CANDIDATES_REG)
_FONT_BOLD = _first_existing(_FONT_CANDIDATES_BOLD)


def px(emu: int) -> int:
    return int(Emu(emu).inches * DPI)


def font_for(pt: float, bold: bool) -> ImageFont.FreeTypeFont:
    path = _FONT_BOLD if bold else _FONT_REG
    return ImageFont.truetype(path, max(int(pt * DPI / 72), 6))


def run_style(run) -> tuple[float, bool, tuple[int, int, int]]:
    f = run.font
    pt = (f.size.pt if f.size else 18)
    bold = bool(f.bold)
    color = (0, 0, 0)
    try:
        if f.color and f.color.rgb is not None:
            color = tuple(f.color.rgb)
    except Exception:  # noqa: BLE001 - 主题色等非直接 RGB，回退黑
        pass
    return pt, bold, color


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: float) -> list[str]:
    lines: list[str] = []
    cur = ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if draw.textlength(cur + ch, font=font) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines or [""]


def draw_text_frame(draw, tf, box, *, v_anchor: str = "top") -> None:
    """box = (x, y, w, h)；按段落逐 run 画（粗体分段 / level 缩进 / 居中）。"""
    x, y, w, h = box
    lines_all: list[tuple[str, ImageFont.ImageFont, tuple, int, str]] = []
    for p in tf.paragraphs:
        runs = p.runs
        if not runs:
            lines_all.append(("", font_for(14, False), (0, 0, 0), 0, str(p.alignment or "left")))
            continue
        segs = []
        for r in runs:
            pt, bold, col = run_style(r)
            segs.append((r.text, pt, bold, col))
        # 预览够用：整段拼接，取最大字号 + 任一粗体，色随首 run
        joined = "".join(s[0] for s in segs)
        max_pt = max(s[1] for s in segs)
        any_bold = any(s[2] for s in segs)
        col = segs[0][3]
        indent = (p.level or 0) * 24
        align = str(p.alignment or "left")
        fnt = font_for(max_pt, any_bold)
        for line in wrap_text(draw, joined, fnt, max(w - indent - 8, 20)):
            lines_all.append((line, fnt, col, indent, align))
    total_h = sum(int(f.size * 1.35) for _, f, _, _, _ in lines_all)
    if v_anchor == "middle":
        y = y + max((h - total_h) // 2, 0)
    for line, fnt, col, indent, align in lines_all:
        lw = draw.textlength(line, font=fnt)
        if "CENTER" in align:
            lx = x + (w - lw) / 2
        elif "RIGHT" in align:
            lx = x + w - lw - 6
        else:
            lx = x + indent + 4
        draw.text((lx, y), line, font=fnt, fill=col)
        y += int(fnt.size * 1.35)


def slide_bg_color(slide) -> tuple[int, int, int]:
    try:
        fill = slide.background.fill
        if fill.type is not None and fill.fore_color and fill.fore_color.rgb is not None:
            return tuple(fill.fore_color.rgb)
    except Exception:  # noqa: BLE001 - 无背景填充 → 白底
        pass
    return (255, 255, 255)


def render_slide(slide) -> Image.Image:
    img = Image.new("RGB", (int(SLIDE_W_IN * DPI), int(SLIDE_H_IN * DPI)), slide_bg_color(slide))
    d = ImageDraw.Draw(img)
    for sh in slide.shapes:
        try:
            x, y, w, h = px(sh.left), px(sh.top), px(sh.width), px(sh.height)
        except Exception:  # noqa: BLE001 - 无定位形状（组合件等）跳过
            continue
        if sh.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE:
            # 色块（分隔页横幅 / 封面强调条 / 引用框）
            fc = None
            try:
                if sh.fill.type is not None and sh.fill.fore_color.rgb is not None:
                    fc = tuple(sh.fill.fore_color.rgb)
            except Exception:  # noqa: BLE001
                fc = None
            if fc:
                d.rectangle([x, y, x + w, y + h], fill=fc)
            else:
                d.rectangle([x, y, x + w, y + h], outline=(150, 150, 150), width=2)
            if sh.has_text_frame and sh.text_frame.text.strip():
                draw_text_frame(d, sh.text_frame, (x, y, w, h), v_anchor="middle")
        elif sh.shape_type == MSO_SHAPE_TYPE.PICTURE:
            # 真图（Tier B contain 插入的原图）
            try:
                pic = Image.open(BytesIO(sh.image.blob)).convert("RGB")
                img.paste(pic.resize((max(w, 1), max(h, 1))), (x, y))
            except Exception:  # noqa: BLE001 - 损坏图 → 灰块占位
                d.rectangle([x, y, x + w, y + h], fill=(205, 205, 205))
        elif getattr(sh, "has_table", False):
            # 原生表格
            tbl = sh.table
            cw = [px(tbl.columns[c].width) for c in range(len(tbl.columns))]
            rh = [px(tbl.rows[r].height) for r in range(len(tbl.rows))]
            cy = y
            for r, row_h in enumerate(rh):
                cx = x
                for c, col_w in enumerate(cw):
                    cell = tbl.cell(r, c)
                    try:
                        fc = tuple(cell.fill.fore_color.rgb)
                    except Exception:  # noqa: BLE001
                        fc = (245, 245, 245)
                    d.rectangle([cx, cy, cx + col_w, cy + row_h], fill=fc, outline=(170, 170, 170))
                    draw_text_frame(d, cell.text_frame, (cx + 3, cy + 2, col_w - 6, row_h - 4))
                    cx += col_w
                cy += row_h
        elif sh.has_text_frame:
            # 占位符 / 文本框：虚线图框判定
            txt = sh.text_frame.text
            if "请插入" in txt:
                d.rectangle([x, y, x + w, y + h], outline=(150, 150, 150), width=3)
                for i in range(x, x + w, 12):  # 近似虚线
                    d.line([i, y, min(i + 6, x + w), y], fill=(150, 150, 150), width=3)
                    d.line([i, y + h, min(i + 6, x + w), y + h], fill=(150, 150, 150), width=3)
                d.line([x, y, x, y + h], fill=(150, 150, 150), width=3)
                d.line([x + w, y, x + w, y + h], fill=(150, 150, 150), width=3)
            anchor = "middle" if "请插入" in txt else "top"
            draw_text_frame(d, sh.text_frame, (x, y, w, h), v_anchor=anchor)
    return img


def render_previews(pptx_path: Path, out_dir: Path) -> list[Path]:
    """draft.pptx → out_dir/slide_{i:02d}.png；失败/空/损坏 → []（绝不抛）。"""
    if not pptx_path.is_file():
        return []
    try:
        prs = Presentation(str(pptx_path))
    except Exception as exc:  # noqa: BLE001 - 坏文件降级（调用方 warning）
        logger.warning("预览渲染：pptx 打开失败 %s：%s", pptx_path, exc)
        return []
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        # 清旧页：重建后页数变少时不留 slide_{n}.png 残影（懒渲染 GET 同样受益）
        for stale in out_dir.glob("slide_*.png"):
            stale.unlink(missing_ok=True)
        written: list[Path] = []
        for i, slide in enumerate(prs.slides, 1):
            img = render_slide(slide)
            final = out_dir / f"slide_{i:02d}.png"
            # 原子写：先临时文件再 replace，避免并发 GET 读到半张图
            with tempfile.NamedTemporaryFile(dir=out_dir, suffix=".png", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                img.save(tmp_path, format="PNG")
                tmp_path.replace(final)
            finally:
                tmp_path.unlink(missing_ok=True)
            written.append(final)
        return written
    except Exception as exc:  # noqa: BLE001 - 渲染异常不阻断构建
        logger.warning("预览渲染失败（该 job 无缩略图）：%s", exc)
        return []
