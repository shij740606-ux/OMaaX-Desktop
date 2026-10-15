# HTML rendering and representative-figure selection for gene-family reports.
from __future__ import annotations
import base64
import html
import mimetypes
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

REPORT_RENDERER_VERSION = "gene_family_report_html_v4"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
STAGE_FIGURE_TEXT = {
    "phylogeny": ("系统发育图展示目标成员与参考成员之间的聚类关系。", "结合分支拓扑、节点支持率和成员标签阅读，不把邻近关系直接等同于功能一致。", "目标与参考序列的聚类关系见图，具体分支以图中节点和标签为准。"),
    "reference_classification": ("参考分类图展示目标成员与已知参考亚家族的相对位置。", "结合参考成员标签、目标成员所在分支及支持率判断分类线索。", "目标成员的参考辅助分类位置见图，分类结论以本节确定性结果为准。"),
    "motif": ("Motif图展示各成员短保守片段的组成、顺序和相对位置。", "按成员逐行比较Motif颜色、编号、数量和排列，不把Motif相似直接写成功能相同。", "本次成员的Motif组成与排列见图，数量统计以本节结果数据为准。"),
    "domain": ("Domain图展示蛋白主要功能区域的类型和位置。", "结合结构域名称、覆盖区间和成员间差异阅读。", "各成员的结构域组成见图，覆盖数量和比例以本节结果数据为准。"),
    "structure": ("基因结构图展示外显子、内含子和基因长度的相对关系。", "结合图例比较外显子数量、结构排列和异常长基因。", "目标成员的基因结构差异见图，具体统计以本节结果数据为准。"),
    "combo": ("综合组合图并列展示系统发育、Motif、Domain和基因结构信息。", "沿同一成员横向比较多个证据面板，关注一致模式与明显差异。", "多类结构证据的对应关系见图，不据此单独确认功能改变。"),
    "promoter": ("启动子图展示顺式作用元件的类型、数量或位置分布。", "结合元件图例、位置坐标和成员标签比较，不把预测位点写成已验证调控。", "目标成员启动子元件的分布见图，数量和类别统计以本节结果数据为准。"),
    "synteny": ("共线性图展示染色体区段和基因对之间的连线关系。", "结合染色体标签、连线和家族成员标记阅读，区分全基因组背景与家族证据。", "本次共线性背景见图，家族专属关系以本节可追溯结果为准。"),
    "karyotype": ("染色体分布图展示家族成员在染色体上的物理位置。", "结合染色体编号、位置刻度和基因标签查看分布范围。", "目标家族成员的染色体位置见图，成员数和染色体数以本节结果数据为准。"),
    "interspecies_synteny": ("种间共线性图展示目标物种与参考物种之间的保守区块和基因连线。", "结合物种、染色体、标记连线和成员标签阅读，不直接移植参考物种功能。", "跨物种共线性关系见图，目标与参考家族配对数以本节结果数据为准。"),
    "pep": ("蛋白特征图展示理化性质、跨膜区域或亚细胞定位预测结果。", "根据具体图例比较成员分布，区分不同预测子任务。", "本次蛋白预测结果见图，具体数量与分类以本节结果数据为准。"),
    "tfbs": ("Motif统计图展示不同基因的位点计数、全部Motif计数和高频Motif。", "结合基因标签、颜色图例、计数刻度和Motif名称比较分布。", "本次gene、all和top motif统计结果见图，具体计数以图例和源结果为准。"),
}

def _artifact_image(final_dir: Path, artifact: Any) -> Path | None:
    if not isinstance(artifact, Mapping): return None
    raw = str(artifact.get("full_path") or artifact.get("path") or "").strip()
    target = final_dir / Path(raw).name if raw else None
    return target if target and target.is_file() and target.suffix.lower() in IMAGE_SUFFIXES else None

def _kind(role: str, filename: str) -> str:
    value = f"{role} {filename}".lower()
    groups = (("gene_motif_count", ("gene_motif_heatmap",)), ("all_motif_count", ("all_motif_count_heatmap",)), ("top_motif", ("top_motifs_barplot",)), ("tree", ("tree",)), ("msa", ("msa_plot", "conserved_region")), ("synteny", ("synteny", "circos")), ("motif", ("motif",)), ("domain", ("domain",)), ("structure", ("structure",)), ("combo", ("combo",)), ("promoter_combined", ("promoter_v6_combined",)), ("promoter_position", ("promoter_position_distribution",)), ("promoter", ("promoter",)), ("karyotype", ("karyotype",)), ("tmhmm", ("tmhmm",)), ("subcellular", ("subcellular",)), ("properties", ("properties",)))
    return next((kind for kind, tokens in groups if any(token in value for token in tokens)), role.lower())

def _rank(role: str, path: Path) -> tuple[int, int, str]:
    value = f"{role} {path.name}".lower()
    role_lower = role.lower()
    preferred = -5 if role_lower == "synteny_marked_png" else -4 if "circular" in value else -2 if "marked" in value else -1 if "summary" in value else 0
    return (preferred, 0, path.name)

def collect_stage_figures(final_dir: Path, stages: Iterable[str], manifests: Mapping[str, Mapping[str, Any]]) -> dict[str, list[dict[str, str]]]:
    result = {}
    for stage in stages:
        selected = {}; seen = set()
        for role, artifact in ((manifests.get(stage) or {}).get("artifacts") or {}).items():
            target = _artifact_image(final_dir, artifact)
            if target is None or target.name in seen: continue
            seen.add(target.name); kind = _kind(str(role), target.name)
            candidate = (_rank(str(role), target), {"role": str(role), "filename": target.name})
            if kind not in selected or candidate[0] < selected[kind][0]: selected[kind] = candidate
        if stage == "tfbs":
            for role, pattern in (
                ("gene_motif_heatmap", "*gene_motif_heatmap.png"),
                ("all_motif_count_heatmap", "*all_motif_count_heatmap.png"),
                ("top_motifs_barplot", "*top_motifs_barplot.png"),
            ):
                matches = sorted(final_dir.glob(pattern))
                if not matches:
                    continue
                target = matches[0]
                kind = _kind(role, target.name)
                selected[kind] = (_rank(role, target), {"role": role, "filename": target.name})
        figures = [entry[1] for _, entry in sorted(selected.items(), key=lambda item: (item[1][0], item[0]))][:4]
        if figures: result[stage] = figures
    return result

def render_stage_figures(stage: str, figures: list[dict[str, str]]) -> str:
    meaning, reading, result = STAGE_FIGURE_TEXT.get(stage, ("结果图展示该阶段的主要计算产物。", "结合图题、坐标轴、图例和成员标签阅读。", "本阶段代表结果见图，具体事实以本节确定性数据为准。"))
    lines = ["", "### 结果图"]
    for index, figure in enumerate(figures, 1):
        title = figure["role"]
        lines.extend(["", f"#### 图 {index}. {title}", "", f"![{title}]({figure['filename']})", "", f"图形含义：{meaning}", "", f"阅读方法：{reading}", "", f"本次结果：{result}"])
    return "\n".join(lines)

def _inline(value: str, report_path: Path) -> str:
    tokens = []
    def stash(content):
        token = f"@@GFAHTML{len(tokens)}@@"; tokens.append(content); return token
    def image(match):
        alt = match.group(1).strip() or "基因家族结果图"; target = report_path.parent / Path(match.group(2).strip().replace("\\", "/")).name
        source = target.name; mime = mimetypes.guess_type(target.name)[0] or ""
        if target.is_file() and mime.startswith("image/"): source = f"data:{mime};base64,{base64.b64encode(target.read_bytes()).decode('ascii')}"
        return stash(f'<figure><img src="{html.escape(source, quote=True)}" alt="{html.escape(alt, quote=True)}" loading="lazy"><figcaption>{html.escape(alt)}</figcaption></figure>')
    def link(match):
        label = html.escape(match.group(1).strip()); target = Path(match.group(2).strip().replace("\\", "/")).name
        return stash(f'<a href="{html.escape(target, quote=True)}" target="_blank" rel="noopener">{label}</a>')
    value = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", image, value); value = re.sub(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)", link, value)
    value = re.sub(r"`([^`]+)`", lambda m: stash(f"<code>{html.escape(m.group(1))}</code>"), value); value = html.escape(value); value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
    for index, content in enumerate(tokens): value = value.replace(f"@@GFAHTML{index}@@", content)
    return value

def markdown_to_html(markdown: str, report_path: Path, title: str) -> str:
    output = []; list_tag = ""; table_rows = []
    def close_list():
        nonlocal list_tag
        if list_tag: output.append(f"</{list_tag}>"); list_tag = ""
    def flush_table():
        nonlocal table_rows
        if not table_rows: return
        rows, table_rows = table_rows, []
        if len(rows) >= 2 and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in rows[1]):
            output.append('<div class="table-wrap"><table><thead><tr>' + ''.join(f"<th>{_inline(c, report_path)}</th>" for c in rows[0]) + '</tr></thead><tbody>'); output.extend('<tr>' + ''.join(f"<td>{_inline(c, report_path)}</td>" for c in row) + '</tr>' for row in rows[2:]); output.append('</tbody></table></div>')
        else: output.extend(f"<p>{_inline(' | '.join(row), report_path)}</p>" for row in rows)
    for raw in markdown.splitlines():
        line = raw.strip()
        if line.startswith("|") and line.endswith("|"): close_list(); table_rows.append([c.strip() for c in line.strip("|").split("|")]); continue
        flush_table()
        if not line: close_list(); continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading: close_list(); level=len(heading.group(1)); output.append(f"<h{level}>{_inline(heading.group(2), report_path)}</h{level}>"); continue
        if line.startswith("> "): close_list(); output.append(f"<blockquote>{_inline(line[2:], report_path)}</blockquote>"); continue
        item = re.match(r"^([-*]|\d+\.)\s+(.+)$", line)
        if item:
            tag = "ol" if item.group(1)[0].isdigit() else "ul"
            if list_tag != tag: close_list(); list_tag=tag; output.append(f"<{tag}>")
            output.append(f"<li>{_inline(item.group(2), report_path)}</li>"); continue
        close_list(); rendered=_inline(line, report_path); output.append(rendered if line.startswith("![") else f"<p>{rendered}</p>")
    close_list(); flush_table(); body="\n".join(output); safe_title=html.escape(title)
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{safe_title}</title><style>:root{{--ink:#17352c;--paper:#fffdf7;--line:#dce5dc;--accent:#157a57;--soft:#edf6ef;--warm:#f2e5c7}}*{{box-sizing:border-box}}html{{background:#e9efe9}}body{{margin:0;color:var(--ink);font-family:"Noto Serif SC","Source Han Serif SC","Microsoft YaHei",serif;line-height:1.85;background:radial-gradient(circle at top left,#f8edcf 0,transparent 28rem),linear-gradient(150deg,#edf4ec,#f6f2e8)}}main{{width:min(1120px,calc(100% - 32px));margin:32px auto 64px;padding:clamp(28px,5vw,72px);background:var(--paper);border:1px solid rgba(23,53,44,.12);border-radius:22px;box-shadow:0 24px 70px rgba(35,62,48,.14);overflow:hidden}}h1{{font-size:clamp(2rem,5vw,3.5rem);line-height:1.2;color:#0d4f39}}h2{{margin:3.2rem 0 1.1rem;padding-top:.7rem;border-top:3px solid var(--warm);font-size:clamp(1.45rem,3vw,2rem)}}h3{{color:var(--accent)}}p,h1,h2,h3,h4,a,figcaption,code{{overflow-wrap:anywhere;word-break:break-word}}blockquote{{margin:1.4rem 0;padding:1rem 1.25rem;border-left:5px solid var(--accent);background:var(--soft)}}code{{padding:.14em .4em;border-radius:5px;background:#edf0eb}}figure{{margin:1.6rem 0 2.2rem;padding:clamp(8px,2vw,18px);border:1px solid var(--line);border-radius:16px;background:#fff;box-shadow:0 10px 30px rgba(28,62,48,.09)}}img{{display:block;max-width:100%;height:auto;margin:auto;border-radius:10px}}figcaption{{margin-top:.7rem;text-align:center;color:#60726b}}a{{color:#087553}}.table-wrap{{overflow-x:auto;margin:1.4rem 0;border:1px solid var(--line);border-radius:12px}}table{{width:100%;border-collapse:collapse;background:#fff}}th,td{{padding:.72rem .85rem;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:var(--soft)}}@media(max-width:640px){{main{{width:100%;margin:0;padding:24px 18px 48px;border:0;border-radius:0}}}}@media print{{html,body{{background:#fff}}main{{width:100%;margin:0;padding:0;border:0;box-shadow:none}}figure{{break-inside:avoid}}}}</style></head><body><main>{body}</main></body></html>'''
