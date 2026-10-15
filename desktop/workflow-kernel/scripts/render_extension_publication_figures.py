#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT = PROJECT_ROOT / "项目记录" / "拓展分析阶段_发表级完整实验_20260616"
FIG_DIR = ROOT / "figures" / "nature_style"
TABLE_DIR = ROOT / "tables" / "nature_style"
REPORT = ROOT / "summary_report_publication.html"

mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "font.size": 7,
    "axes.spines.right": False,
    "axes.spines.top": False,
    "axes.linewidth": 0.7,
    "legend.frameon": False,
})

PALETTE = {
    "ink": "#1b2430",
    "muted": "#687386",
    "line": "#d6dce6",
    "green": "#2f7d55",
    "green_soft": "#dcefe4",
    "red": "#b33a3a",
    "red_soft": "#f3d7d7",
    "blue": "#2d6f9f",
    "blue_soft": "#dce9f5",
    "amber": "#b5791f",
    "amber_soft": "#f4e3c5",
    "grey": "#f2f4f7",
}


def load_json(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def short_name(name: str) -> str:
    return {
        "sequence_extraction": "Sequence\nextraction",
        "hmmer_domain": "HMMER\ndomain",
        "motif_meme": "MEME\nmotif",
        "phylogeny_tree": "Phylogeny\ntree",
        "reference_classification": "Reference\nclassification",
        "synteny_collinearity": "Synteny\ncollinearity",
        "kaks_pairwise": "Ka/Ks\npairwise",
        "pep_properties": "Protein\nproperties",
    }.get(name, name.replace("_", "\n"))


def save_all(fig, stem: Path):
    stem.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(stem.with_suffix(".svg"), bbox_inches="tight")
    fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(stem.with_suffix(".png"), dpi=450, bbox_inches="tight")
    fig.savefig(stem.with_suffix(".tiff"), dpi=600, bbox_inches="tight")


def panel_label(ax, label):
    ax.text(-0.04, 1.04, label, transform=ax.transAxes, fontweight="bold", fontsize=10, va="top", ha="left", color=PALETTE["ink"])


def draw_workflow(ax):
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    steps = [
        ("Executable\ncode + data", PALETTE["blue_soft"], 0.05),
        ("Spec\nconversion", "#ffffff", 0.23),
        ("Deterministic\nwrapper", PALETTE["green_soft"], 0.41),
        ("Golden\ncomparison", PALETTE["amber_soft"], 0.59),
        ("Template +\npublish gates", PALETTE["amber_soft"], 0.77),
        ("Published\nextension", PALETTE["green_soft"], 0.91),
    ]
    y = 0.62
    for i, (txt, face, x) in enumerate(steps):
        w = 0.13 if i < 5 else 0.11
        patch = FancyBboxPatch((x - w/2, y - 0.105), w, 0.21, boxstyle="round,pad=0.012,rounding_size=0.012", fc=face, ec=PALETTE["line"], lw=0.9)
        ax.add_patch(patch)
        ax.text(x, y, txt, ha="center", va="center", fontsize=7.2, color=PALETTE["ink"], fontweight="bold" if i in {2, 3, 4, 5} else "normal")
        if i < len(steps) - 1:
            x2 = steps[i + 1][2]
            ax.add_patch(FancyArrowPatch((x + w/2 + 0.005, y), (x2 - 0.065, y), arrowstyle="-|>", mutation_scale=8, lw=0.9, color=PALETTE["muted"]))
    ax.text(0.05, 0.88, "Validation-driven extension-stage construction", fontsize=10, fontweight="bold", color=PALETTE["ink"], ha="left")
    ax.text(0.05, 0.78, "API output is treated as a candidate; publication requires deterministic execution evidence.", fontsize=7.5, color=PALETTE["muted"], ha="left")
    ax.add_patch(FancyBboxPatch((0.53, 0.18), 0.36, 0.18, boxstyle="round,pad=0.012,rounding_size=0.012", fc=PALETTE["red_soft"], ec="#d8a5a5", lw=0.8))
    ax.text(0.71, 0.27, "Failed candidates are blocked\nbefore entering the published stage list", ha="center", va="center", fontsize=7.5, color=PALETTE["ink"])
    ax.add_patch(FancyArrowPatch((0.77, 0.52), (0.72, 0.36), arrowstyle="-|>", mutation_scale=8, lw=0.9, color=PALETTE["red"]))


def draw_lifecycle_matrix(ax, det_rows, api_rows):
    phases = ["Build", "Trial", "Golden", "Template\ngate", "Publish"]
    cases = [r["archetype_id"] for r in det_rows]
    values = []
    for case in cases:
        d = next(r for r in det_rows if r["archetype_id"] == case)
        a = next((r for r in api_rows if r["archetype_id"] == case), {})
        values.append([bool(d["build_success"]), bool(d["trial_success"]), d["golden_status"] == "passed", d["template_gate_status"] == "passed", bool(d["publish_success"])])
        values.append([bool(a.get("build_success")), bool(a.get("trial_success")), a.get("golden_status") == "passed", a.get("template_gate_status") == "passed", bool(a.get("publish_success"))])
    ax.set_xlim(0, len(phases))
    ax.set_ylim(0, len(values))
    ax.invert_yaxis()
    for y, row in enumerate(values):
        for x, val in enumerate(row):
            ax.add_patch(Rectangle((x, y), 1, 1, fc=PALETTE["green_soft"] if val else PALETTE["red_soft"], ec="white", lw=1.2))
            ax.text(x + 0.5, y + 0.5, "✓" if val else "×", ha="center", va="center", fontsize=8, color=PALETTE["green"] if val else PALETTE["red"], fontweight="bold")
    for y in range(0, len(values), 2):
        ax.add_patch(Rectangle((0, y), len(phases), 2, fill=False, ec=PALETTE["line"], lw=0.6))
    ax.set_xticks(np.arange(len(phases)) + 0.5, phases, fontsize=7)
    ylabels = []
    for case in cases:
        ylabels.extend([short_name(case) + "\nD", short_name(case) + "\nAPI"])
    ax.set_yticks(np.arange(len(values)) + 0.5, ylabels, fontsize=5.8)
    ax.tick_params(length=0)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_title("Lifecycle gate outcomes for deterministic and API/Agent conversion", loc="left", fontsize=8.5, fontweight="bold", pad=8)


def draw_api_attempts(ax, api_rows):
    names = [short_name(r["archetype_id"]) for r in api_rows]
    attempts = [int(r.get("api_attempt_count") or 0) for r in api_rows]
    ok = [bool(r.get("publish_success")) for r in api_rows]
    colors = [PALETTE["blue"] if x else PALETTE["red"] for x in ok]
    y = np.arange(len(names))
    ax.barh(y, attempts, color=colors, height=0.62)
    for yi, val, success in zip(y, attempts, ok):
        ax.text(val + 0.2, yi, str(val), va="center", ha="left", fontsize=7, color=PALETTE["ink"])
        if not success:
            ax.text(max(val * 0.45, 0.2), yi, "blocked", va="center", ha="center", fontsize=6.5, color="white", fontweight="bold")
    ax.set_yticks(y, names, fontsize=6.6)
    ax.invert_yaxis()
    ax.set_xlabel("API attempts before final decision", fontsize=7)
    ax.set_xlim(0, max(attempts) + 2)
    ax.grid(axis="x", color=PALETTE["line"], lw=0.5)
    ax.set_title("API/Agent conversion required variable repair effort", loc="left", fontsize=8.5, fontweight="bold", pad=8)


def draw_summary_bars(ax, det, api, neg):
    labels = ["Deterministic\nchain", "API/Agent\nconversion", "Negative\ncontrols"]
    numerator = [det["passed"], api["passed"], neg["blocked"]]
    denom = [det["case_count"], api["case_count"], neg["case_count"]]
    colors = [PALETTE["green"], PALETTE["blue"], PALETTE["amber"]]
    x = np.arange(3)
    ax.bar(x, [n / d for n, d in zip(numerator, denom)], color=colors, width=0.58)
    for xi, n, d in zip(x, numerator, denom):
        ax.text(xi, n / d + 0.045, f"{n}/{d}", ha="center", va="bottom", fontsize=8, fontweight="bold", color=PALETTE["ink"])
    ax.set_xticks(x, labels, fontsize=7)
    ax.set_ylim(0, 1.16)
    ax.set_ylabel("Fraction passing expected decision", fontsize=7)
    ax.set_yticks([0, 0.5, 1.0], ["0", "0.5", "1.0"])
    ax.grid(axis="y", color=PALETTE["line"], lw=0.5)
    ax.set_title("Three independent evidence layers support safe extensibility", loc="left", fontsize=8.5, fontweight="bold", pad=8)


def draw_negative_controls(ax, rows):
    labels = [r["scenario"].replace("_", "\n") for r in rows]
    y = np.arange(len(rows))
    ax.barh(y, [1] * len(rows), color=PALETTE["red_soft"], height=0.6, edgecolor="#d8a5a5", lw=0.6)
    for yi, r in zip(y, rows):
        ax.text(0.5, yi, "blocked" if r["blocked"] and not r["published"] else "leaked", ha="center", va="center", fontsize=7, color=PALETTE["red"], fontweight="bold")
    ax.set_yticks(y, labels, fontsize=6.7)
    ax.set_xticks([])
    ax.set_xlim(0, 1)
    ax.invert_yaxis()
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_title("Failure-injection cases were blocked before publication", loc="left", fontsize=8.5, fontweight="bold", pad=8)


def write_source_tables(det, api, neg):
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    with (TABLE_DIR / "source_data_main_figure.csv").open("w", encoding="utf-8", newline="") as f:
        fields = ["group", "archetype_id", "build", "trial", "golden", "template_gate", "publish", "api_attempts", "error"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for group, rows in [("deterministic", det["rows"]), ("api_agent", api["rows"] )]:
            for r in rows:
                w.writerow({
                    "group": group,
                    "archetype_id": r["archetype_id"],
                    "build": r.get("build_success"),
                    "trial": r.get("trial_success"),
                    "golden": r.get("golden_status"),
                    "template_gate": r.get("template_gate_status"),
                    "publish": r.get("publish_success"),
                    "api_attempts": r.get("api_attempt_count", ""),
                    "error": r.get("error", ""),
                })
    with (TABLE_DIR / "source_data_negative_controls.csv").open("w", encoding="utf-8", newline="") as f:
        fields = ["scenario", "blocked", "published", "error"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in neg["rows"]:
            w.writerow({k: r.get(k, "") for k in fields})


def make_main_figure(det, api, neg):
    fig = plt.figure(figsize=(7.2, 8.0), constrained_layout=False)
    gs = fig.add_gridspec(3, 2, height_ratios=[1.02, 1.6, 1.0], width_ratios=[1.18, 0.82], hspace=0.52, wspace=0.36)
    axA = fig.add_subplot(gs[0, :])
    axB = fig.add_subplot(gs[1, 0])
    axC = fig.add_subplot(gs[1, 1])
    axD = fig.add_subplot(gs[2, 0])
    axE = fig.add_subplot(gs[2, 1])
    draw_workflow(axA)
    panel_label(axA, "a")
    draw_lifecycle_matrix(axB, det["rows"], api["rows"])
    panel_label(axB, "b")
    draw_api_attempts(axC, api["rows"])
    panel_label(axC, "c")
    draw_summary_bars(axD, det, api, neg)
    panel_label(axD, "d")
    draw_negative_controls(axE, neg["rows"])
    panel_label(axE, "e")
    fig.text(0.012, 0.012, "Source data: real extension-stage publication experiment package; deterministic chain n=8, API/Agent n=8, negative controls n=4.", fontsize=6.5, color=PALETTE["muted"])
    save_all(fig, FIG_DIR / "figure_1_extension_stage_validation")
    plt.close(fig)


def make_html(det, api, neg):
    failed = [r for r in api["rows"] if not r.get("publish_success")]
    failure_text = failed[0]["error"] if failed else ""
    rows_det = "".join(
        f"<tr><td>{html.escape(r['archetype_id'])}</td><td>{r['build_success']}</td><td>{r['trial_success']}</td><td>{html.escape(str(r['golden_status']))}</td><td>{html.escape(str(r['template_gate_status']))}</td><td>{r['publish_success']}</td><td>{r.get('output_count')}</td></tr>"
        for r in det["rows"]
    )
    rows_api = "".join(
        f"<tr><td>{html.escape(r['archetype_id'])}</td><td>{r.get('api_attempt_count')}</td><td>{html.escape(str(r.get('golden_status')))}</td><td>{html.escape(str(r.get('template_gate_status')))}</td><td>{r.get('publish_success')}</td><td>{html.escape(str(r.get('error') or '')[:220])}</td></tr>"
        for r in api["rows"]
    )
    rows_neg = "".join(
        f"<tr><td>{html.escape(r['scenario'])}</td><td>{r['blocked']}</td><td>{r['published']}</td><td>{html.escape(r['error'][:220])}</td></tr>"
        for r in neg["rows"]
    )
    doc = f"""<!doctype html>
<html lang=\"zh-CN\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>拓展分析阶段发表级实验结果</title>
<style>
:root {{ --ink:#1b2430; --muted:#687386; --line:#d6dce6; --blue:#2d6f9f; --green:#2f7d55; --red:#b33a3a; --amber:#b5791f; --panel:#f7f9fc; }}
body {{ margin:0; font:14px/1.68 Arial, 'Microsoft YaHei', sans-serif; color:var(--ink); background:#fff; }}
main {{ max-width:1180px; margin:0 auto; padding:30px 28px 60px; }}
h1 {{ font-size:28px; margin:0 0 8px; }}
h2 {{ margin:34px 0 12px; font-size:20px; border-left:5px solid var(--blue); padding-left:10px; }}
h3 {{ margin:20px 0 8px; font-size:16px; }}
.subtitle {{ color:var(--muted); max-width:920px; }}
.grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin:18px 0; }}
.metric {{ border:1px solid var(--line); background:var(--panel); padding:14px; border-radius:8px; }}
.metric b {{ display:block; font-size:24px; color:var(--blue); }}
.metric span {{ color:var(--muted); }}
.note {{ border-left:4px solid var(--blue); background:#eaf2fa; padding:11px 13px; margin:12px 0; }}
.warn {{ border-left-color:var(--amber); background:#f7ecd7; }}
.good {{ color:var(--green); font-weight:700; }}
.bad {{ color:var(--red); font-weight:700; }}
table {{ width:100%; border-collapse:collapse; margin:12px 0 22px; table-layout:fixed; }}
th,td {{ border:1px solid var(--line); padding:8px 9px; vertical-align:top; word-break:break-word; }}
th {{ background:#edf2f8; text-align:left; }}
.figure {{ border:1px solid var(--line); padding:12px; border-radius:8px; background:#fff; margin:14px 0 22px; }}
.figure img {{ max-width:100%; display:block; margin:auto; }}
.caption {{ color:var(--muted); font-size:13px; margin-top:8px; }}
code {{ background:#f2f4f7; padding:1px 4px; border-radius:4px; font-size:12px; }}
@media (max-width:850px) {{ .grid {{ grid-template-columns:1fr 1fr; }} main {{ padding:20px 14px 42px; }} table {{ font-size:12px; }} }}
</style>
</head>
<body><main>
<header>
<h1>拓展分析阶段：发表级实验结果</h1>
<p class=\"subtitle\">本文档将前期真实执行结果整理为论文结果写作状态。核心结论是：确定性拓展阶段构建链路在 8 类真实数据 archetype 上全部通过；API/Agent 自动转写在同一任务集上达到 7/8 发布成功，失败案例被 golden validation 与发布门禁拦截。</p>
</header>
<section>
<h2>一、结果总览</h2>
<div class=\"grid\">
<div class=\"metric\"><b>{det['passed']}/{det['case_count']}</b><span>确定性构建链路通过</span></div>
<div class=\"metric\"><b>{api['passed']}/{api['case_count']}</b><span>API/Agent 转写发布成功</span></div>
<div class=\"metric\"><b>{neg['blocked']}/{neg['case_count']}</b><span>结构性负例被阻断</span></div>
<div class=\"metric\"><b>60</b><span>拓展阶段相关单元测试通过</span></div>
</div>
<div class=\"note\">论文表述应分开写：<b>确定性主链路 8/8</b> 证明系统闭环稳定；<b>API/Agent 7/8</b> 是自动转写能力对照；<b>负例 4/4 blocked</b> 证明发布门禁有效。</div>
</section>
<section>
<h2>二、主图</h2>
<div class=\"figure\"><img src=\"figures/nature_style/figure_1_extension_stage_validation.svg\" alt=\"Extension-stage validation multi-panel figure\"><div class=\"caption\"><b>Figure 1 | Validation-driven construction of extension analysis stages.</b> a, Extension-stage construction and publication workflow. b, Lifecycle gate outcomes for deterministic and API/Agent conversion across eight archetypes. D = deterministic chain; API = DeepSeek/API Agent conversion. c, API attempts required before final decision. d, Summary of three evidence layers. e, Negative-control cases blocked before publication.</div></div>
<p>导出文件：<code>figure_1_extension_stage_validation.svg</code>, <code>.pdf</code>, <code>.png</code>, <code>.tiff</code>。Source data 已导出到 <code>tables/nature_style/</code>。</p>
</section>
<section>
<h2>三、确定性主链路</h2>
<p>8 类真实数据 archetype 均通过 build、trial、golden comparison、template gate 和 publish gate。该结果可作为论文主结果。</p>
<table><thead><tr><th>Archetype</th><th>Build</th><th>Trial</th><th>Golden</th><th>Template gate</th><th>Publish</th><th>Outputs</th></tr></thead><tbody>{rows_det}</tbody></table>
</section>
<section>
<h2>四、API/Agent 对照</h2>
<p>API/Agent 对照完整覆盖同一 8 类任务，结果为 <b>{api['passed']}/{api['case_count']}</b> 发布成功。失败的 <code>hmmer_domain</code> 并非系统执行链路失败，而是 API 生成的 semantic command template 未复现原始行为，被 golden/publish gate 阻断。</p>
<div class=\"note warn\">失败原因摘要：{html.escape(failure_text[:420])}</div>
<table><thead><tr><th>Archetype</th><th>API attempts</th><th>Golden</th><th>Template gate</th><th>Publish</th><th>Error</th></tr></thead><tbody>{rows_api}</tbody></table>
</section>
<section>
<h2>五、失败拦截负例</h2>
<p>4 个结构性负例全部被阻断，没有进入 published stage 列表。该实验应称为 <b>negative-control / failure-injection test</b>，不是严格意义的消融实验。</p>
<table><thead><tr><th>Scenario</th><th>Blocked</th><th>Published</th><th>Blocking evidence</th></tr></thead><tbody>{rows_neg}</tbody></table>
</section>
<section>
<h2>六、论文可用表述</h2>
<p>推荐写法：</p>
<div class=\"note\">We evaluated the extension-stage construction framework using eight real-data executable-code archetypes. The deterministic construction pipeline passed all eight cases through trial execution, original-vs-converted golden comparison, template-gate validation and publish-gate checking. In an API/Agent conversion benchmark on the same cases, seven of eight stages were successfully published, while one HMMER-domain case was blocked because the API-generated semantic command template failed to reproduce the original behavior. Four structural negative controls were all intercepted before publication.</div>
<p>不应写成：<span class=\"bad\">API/LLM 自动转写 8/8 全部成功</span>。</p>
</section>
</main></body></html>
"""
    REPORT.write_text(doc, encoding="utf-8")


def main():
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    det = load_json("results/archetype_spec/summary.json")
    api = load_json("results/deepseek_agent/summary.json")
    neg = load_json("negative_cases/negative_control_summary.json")
    write_source_tables(det, api, neg)
    make_main_figure(det, api, neg)
    make_html(det, api, neg)
    print(REPORT)


if __name__ == "__main__":
    main()
