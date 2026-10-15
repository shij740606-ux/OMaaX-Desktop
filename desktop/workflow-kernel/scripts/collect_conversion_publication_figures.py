#!/usr/bin/env python3
"""Collect extension-stage conversion evidence and render manuscript figures.

This script is intentionally read-only with respect to source experiment
records. It writes normalized CSV tables, SVG/PDF/TIFF figures, and figure
legends under 项目记录/转写实验图表_YYYYMMDD.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PROJECT_RECORDS = ROOT / "项目记录"
EXTENSION_STAGES = ROOT / "extension_stages"
WORKSPACE_RUN_ROOT = Path(os.environ.get("PLANTAI_EXTENSION_WORKSPACE_RUN_ROOT", str(ROOT / "workspace"))).expanduser()

FINAL_PATTERNS = (
    "PartialData_FullRerun",
    "API20_0507_PartialData",
    "Balance补跑",
    "VCF_10_threshold_parameter_test",
    "VCF拓展分析阶段_API20_PartialData",
    "0507_实施验证",
    "发表级完整实验",
)
HISTORICAL_PATTERNS = (
    "Retest",
    "Benchmark_after_P0",
    "tooling_audit",
    "API20_Benchmark_20260622",
    "P0_fixture",
)

GATE_COLUMNS = OrderedDict(
    [
        ("raw_code_ok", "Raw code"),
        ("build_success", "Build"),
        ("wrapper_success", "Wrapper"),
        ("trial_success", "Trial run"),
        ("validate_success", "Validation"),
        ("golden_status", "Golden"),
        ("template_gate_status", "Template gate"),
        ("publish_success", "Publish"),
        ("published_run_success", "Published run"),
    ]
)

STAGE_LABELS = {
    "gwas_trait_vcf_qc": "GWAS-01 trait+VCF QC",
    "gwas_vcf_filter_index": "GWAS-02 VCF filter/index",
    "gwas_trait_prepare_match": "GWAS-03 trait matching",
    "gwas_plink_pca": "GWAS-04 PLINK PCA",
    "gwas_trait_blup_format": "GWAS-05 BLUP format",
    "gwas_plink_impute": "GWAS-06 PLINK impute",
    "gwas_tassel_compatible": "GWAS-07 TASSEL format",
    "gwas_gemma_assoc": "GWAS-08 GEMMA assoc.",
    "vcf_hard_filter_clean": "VCF-10 hard filter",
    "vcf_10_threshold_adapter_relaxed_api_test_e41b537b": "VCF-10 hard filter",
}


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def status_pass(value: Any) -> bool | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"", "none", "nan", "na"}:
        return None
    if text in {"true", "passed", "pass", "success", "published", "ok", "allow_publish"}:
        return True
    if text in {"false", "failed", "fail", "error", "blocked"}:
        return False
    return None


def ratio(num: int | float | None, den: int | float | None) -> float:
    if den in (None, 0) or pd.isna(den):
        return float("nan")
    return float(num or 0) / float(den)


def record_group(path: Path, case_id: str | None = None) -> str:
    text = str(path)
    case_text = case_id or ""
    if "VCF拓展分析阶段" in text or "VCF_10_threshold" in text:
        return "VCF"
    if "GWAS拓展分析阶段" in text or case_text.startswith("gwas_"):
        return "GWAS"
    if "/vcf_" in text or "vcf_" in case_text:
        return "VCF"
    return "General extension"


def evidence_set(path: Path) -> str:
    text = str(path)
    if any(pattern in text for pattern in FINAL_PATTERNS):
        return "path_candidate"
    if any(pattern in text for pattern in HISTORICAL_PATTERNS):
        return "development_history"
    return "registry_or_run"


def stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage.replace("_", " "))


def stage_sort_key(label: str) -> tuple[int, int, str]:
    text = str(label)
    match = re.search(r"GWAS-(\d+)", text)
    if match:
        return (0, int(match.group(1)), text)
    match = re.search(r"VCF-(\d+)", text)
    if match:
        return (1, int(match.group(1)), text)
    if text.lower().startswith("vcf"):
        return (1, 99, text)
    return (2, 99, text)


def short_profile(profile: str) -> str:
    mapping = {
        "default_hard_filter_v1": "default",
        "relaxed_snp_filter_v1": "relaxed",
        "front_param_test": "front-end",
    }
    return mapping.get(str(profile), str(profile))


def case_is_main_evidence(row: pd.Series) -> bool:
    exp = str(row.get("experiment_dir", ""))
    group = str(row.get("group", ""))
    case_id = str(row.get("case_id", ""))
    if group == "GWAS":
        return (
            "API20_PartialData_FullRerun" in exp
            or "API20_0507_PartialData" in exp
            or "API20_Balance补跑" in exp
        )
    if group == "VCF":
        return "VCF拓展分析阶段_API20_PartialData" in exp
    return False


def select_main_cases(cases: pd.DataFrame) -> pd.DataFrame:
    if cases.empty:
        return cases.copy()
    selected = cases[cases.apply(case_is_main_evidence, axis=1)].copy()
    return selected


def collect_benchmark_summaries() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in sorted(PROJECT_RECORDS.glob("**/summary.json")):
        data = read_json(path)
        if not data:
            continue
        total = data.get("case_result_count") or len(data.get("rows", []))
        success = data.get("api_autonomous_success")
        if success is None and data.get("rows"):
            success = sum(bool(r.get("api_autonomous_success")) for r in data["rows"])
        published = data.get("published_runnable")
        if published is None and data.get("rows"):
            published = sum(bool(r.get("published_runnable")) for r in data["rows"])
        template = data.get("template_eligible")
        if template is None and data.get("rows"):
            template = sum(bool(r.get("template_eligible")) for r in data["rows"])
        failed = data.get("failed")
        if failed is None and total is not None and success is not None:
            failed = int(total) - int(success)
        rows.append(
            {
                "source_file": str(path),
                "experiment_dir": path.parent.name,
                "group": record_group(path),
                "evidence_set": evidence_set(path),
                "schema_version": data.get("schema_version", ""),
                "benchmark_type": data.get("benchmark_type", ""),
                "created_at": data.get("created_at", ""),
                "case_count": total,
                "api_autonomous_success": success,
                "published_runnable": published,
                "template_eligible": template,
                "failed": failed,
                "api_success_rate": ratio(success, total),
                "published_rate": ratio(published, total),
                "template_rate": ratio(template, total),
            }
        )
    return pd.DataFrame(rows)


def collect_case_results() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in sorted(PROJECT_RECORDS.glob("**/result_row.json")):
        data = read_json(path)
        if not data:
            continue
        case_id = data.get("case_id") or data.get("archetype_id") or data.get("extension_id", "")
        rows.append(
            {
                "source_file": str(path),
                "experiment_dir": next(
                    (part for part in path.parts if part.startswith(("GWAS拓展分析阶段", "VCF"))),
                    path.parent.name,
                ),
                "group": record_group(path, str(case_id)),
                "evidence_set": evidence_set(path),
                "schema_version": data.get("schema_version", ""),
                "created_at": data.get("created_at", ""),
                "run_id": data.get("run_id", ""),
                "case_id": case_id,
                "case_label": stage_label(str(case_id)),
                "archetype_id": data.get("archetype_id", ""),
                "extension_id": data.get("extension_id", ""),
                "converter": data.get("converter", ""),
                "runtime_env": data.get("runtime_env", ""),
                "validation_scope": data.get("validation_scope", ""),
                "codex_manual_spec_edit": data.get("codex_manual_spec_edit", ""),
                "codex_framework_fix_required": data.get("codex_framework_fix_required", ""),
                "raw_code_ok": data.get("raw_code_ok", ""),
                "build_success": data.get("build_success", ""),
                "sanitize_success": data.get("sanitize_success", ""),
                "wrapper_success": data.get("wrapper_success", ""),
                "trial_success": data.get("trial_success", ""),
                "confirm_success": data.get("confirm_success", ""),
                "validate_success": data.get("validate_success", ""),
                "golden_status": data.get("golden_status", ""),
                "template_gate_status": data.get("template_gate_status", ""),
                "publish_gate_status": data.get("publish_gate_status", data.get("publish_status", "")),
                "publish_success": data.get("publish_success", ""),
                "published_run_success": data.get("published_run_success", ""),
                "api_autonomous_success": data.get("api_autonomous_success", ""),
                "published_runnable": data.get("published_runnable", data.get("published_run_success", "")),
                "template_eligible": data.get("template_eligible", ""),
                "environment_failed": data.get("environment_failed", ""),
                "repair_stalled": data.get("repair_stalled", ""),
                "first_failure_phase": data.get("first_failure_phase", ""),
                "last_error": data.get("last_error", data.get("error", "")),
                "output_count": data.get("output_count", data.get("published_output_count", data.get("trial_output_count", ""))),
                "build_api_attempt_count": data.get("build_api_attempt_count", ""),
                "total_api_attempt_count": data.get("total_api_attempt_count", data.get("build_api_attempt_count", "")),
                "trial_repair_attempt_count": data.get("trial_repair_attempt_count", ""),
                "validation_repair_attempt_count": data.get("validation_repair_attempt_count", ""),
                "publish_gate_repair_attempt_count": data.get("publish_gate_repair_attempt_count", ""),
                "wall_sec": data.get("wall_sec", ""),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        for col in ["wall_sec", "output_count", "build_api_attempt_count", "total_api_attempt_count"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["main_evidence"] = df.apply(case_is_main_evidence, axis=1)
    return df


def latest_golden_status(ext_dir: Path) -> tuple[str, str, int | None, int | None]:
    comparisons = sorted(ext_dir.glob("golden/**/golden_comparison.json"))
    if not comparisons:
        return "", "", None, None
    path = comparisons[-1]
    data = read_json(path) or {}
    comp = data.get("comparison") or data.get("semantic_comparison") or {}
    summary = comp.get("summary") or data.get("summary") or {}
    status = data.get("status") or comp.get("status") or ""
    return str(status), str(path), summary.get("paired_files"), summary.get("failed")


def collect_extension_registry() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    paths = list(EXTENSION_STAGES.glob("success/*/extension.json"))
    paths += list(EXTENSION_STAGES.glob("failed/*/extension.json"))
    for path in sorted(paths):
        data = read_json(path)
        if not data:
            continue
        ext_dir = path.parent
        params = data.get("parameters") or {}
        editable_params = [k for k, v in params.items() if isinstance(v, dict) and v.get("editable")]
        golden_status, golden_path, paired_files, golden_failed = latest_golden_status(ext_dir)
        command_template = (
            data.get("command_template")
            or data.get("entrypoint", {}).get("command_template")
            or data.get("wrapper_command_template")
            or ""
        )
        rows.append(
            {
                "source_file": str(path),
                "extension_dir": ext_dir.name,
                "group": record_group(path, str(data.get("archetype_id", data.get("extension_id", "")))),
                "extension_id": data.get("extension_id", ""),
                "display_name": data.get("display_name", data.get("name", "")),
                "status": data.get("status", ""),
                "created_at": data.get("created_at", ""),
                "runtime_env": data.get("runtime", {}).get("env", ""),
                "runtime_language": data.get("runtime", {}).get("language", ""),
                "converter_type": data.get("converter", {}).get("type", ""),
                "converter_mode": data.get("converter", {}).get("mode", ""),
                "converter_attempt": data.get("converter", {}).get("attempt", ""),
                "archetype_id": data.get("archetype_id", ""),
                "analysis_type": data.get("analysis_type", ""),
                "tool_family": data.get("tool_family", ""),
                "template_type": data.get("template_type", ""),
                "golden_status": golden_status,
                "golden_path": golden_path,
                "golden_paired_files": paired_files,
                "golden_failed": golden_failed,
                "parameterized": bool(editable_params),
                "editable_parameters": ";".join(editable_params),
                "command_has_runtime_params": "{param_" in command_template,
            }
        )
    return pd.DataFrame(rows)


def collect_extension_runs() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    roots = [EXTENSION_STAGES]
    if WORKSPACE_RUN_ROOT.exists():
        roots.append(WORKSPACE_RUN_ROOT)
    for root in roots:
        for path in sorted(root.glob("**/extension_run.json")):
            data = read_json(path)
            if not data:
                continue
            rows.append(
                {
                    "source_file": str(path),
                    "group": record_group(path, str(data.get("archetype_id", data.get("extension_id", "")))),
                    "extension_id": data.get("extension_id", ""),
                    "display_name": data.get("display_name", data.get("extension_name", "")),
                    "archetype_id": data.get("archetype_id", ""),
                    "run_id": data.get("run_id", ""),
                    "status": data.get("status", ""),
                    "validation": data.get("validation", ""),
                    "template_gate_status": data.get("template_gate_status", ""),
                    "runtime_env": data.get("runtime", {}).get("env", ""),
                    "elapsed_sec": data.get("elapsed_sec", ""),
                    "returncode": data.get("returncode", ""),
                    "output_count": len(data.get("outputs") or []),
                    "parameters_json": json.dumps(data.get("parameters") or {}, ensure_ascii=False, sort_keys=True),
                    "command": data.get("command", data.get("rendered_command", "")),
                    "started_at": data.get("started_at", ""),
                    "ended_at": data.get("ended_at", ""),
                    "recorded_at": data.get("recorded_at", ""),
                }
            )
    df = pd.DataFrame(rows)
    if not df.empty:
        df["elapsed_sec"] = pd.to_numeric(df["elapsed_sec"], errors="coerce")
    return df


def collect_vcf_metrics() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    roots = [PROJECT_RECORDS, EXTENSION_STAGES]
    if WORKSPACE_RUN_ROOT.exists():
        roots.append(WORKSPACE_RUN_ROOT)
    seen = set()
    for root in roots:
        for path in sorted(root.glob("**/hard_filter_metrics.json")):
            if path in seen:
                continue
            seen.add(path)
            data = read_json(path)
            if not data:
                continue
            snp = data.get("thresholds", {}).get("snp", {})
            indel = data.get("thresholds", {}).get("indel", {})
            rows.append(
                {
                    "source_file": str(path),
                    "run_dir": path.parent.parent.name if path.parent.name == "outputs" else path.parent.name,
                    "status": data.get("status", ""),
                    "parameter_profile": data.get("parameter_profile", ""),
                    "parameter_source": data.get("parameter_source", ""),
                    "snp_QD": snp.get("QD", ""),
                    "snp_QUAL": snp.get("QUAL", ""),
                    "snp_SOR": snp.get("SOR", ""),
                    "snp_FS": snp.get("FS", ""),
                    "snp_MQ": snp.get("MQ", ""),
                    "indel_QD": indel.get("QD", ""),
                    "indel_QUAL": indel.get("QUAL", ""),
                    "indel_FS": indel.get("FS", ""),
                    "snp_input_count": data.get("snp_input_count", ""),
                    "indel_input_count": data.get("indel_input_count", ""),
                    "snp_pass_count": data.get("snp_pass_count", ""),
                    "indel_pass_count": data.get("indel_pass_count", ""),
                    "clean_snp_count": data.get("clean_snp_count", ""),
                    "sample_count": data.get("sample_count", ""),
                    "clean_rule": data.get("clean_rule", ""),
                    "wall_sec": data.get("wall_sec", ""),
                    "command_rendered": data.get("command_rendered", ""),
                }
            )
    df = pd.DataFrame(rows)
    if not df.empty:
        num_cols = [
            "snp_QD",
            "snp_QUAL",
            "snp_SOR",
            "snp_FS",
            "snp_MQ",
            "indel_QD",
            "indel_QUAL",
            "indel_FS",
            "snp_input_count",
            "indel_input_count",
            "snp_pass_count",
            "indel_pass_count",
            "clean_snp_count",
            "sample_count",
            "wall_sec",
        ]
        for col in num_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df[
            df["status"].map(status_pass).fillna(False)
            & df["snp_MQ"].notna()
            & ~df["source_file"].str.contains("/fixtures/", regex=False)
        ].copy()
        df = df.drop_duplicates(
            subset=[
                "parameter_profile",
                "snp_MQ",
                "snp_FS",
                "snp_SOR",
                "indel_FS",
                "snp_pass_count",
                "indel_pass_count",
                "clean_snp_count",
            ]
        )
    return df


def make_manuscript_summary(cases: pd.DataFrame, registry: pd.DataFrame, vcf: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    selected = select_main_cases(cases)
    if not selected.empty:
        for group, sub in selected.groupby("group", dropna=False):
            n = len(sub)
            stage_count = sub["case_id"].nunique()
            rows.append(
                {
                    "evidence_block": f"{group} API conversion benchmark",
                    "analysis_family": group,
                    "n_records": n,
                    "n_stages": stage_count,
                    "n_api_autonomous_success": int(sub["api_autonomous_success"].map(status_pass).fillna(False).sum()),
                    "n_golden_passed": int(sub["golden_status"].map(status_pass).fillna(False).sum()),
                    "n_template_passed": int(sub["template_gate_status"].map(status_pass).fillna(False).sum()),
                    "n_published_run_success": int(sub["published_run_success"].map(status_pass).fillna(False).sum()),
                    "success_rate": ratio(sub["api_autonomous_success"].map(status_pass).fillna(False).sum(), n),
                    "scope_note": "curated API20/补跑 case-level result_row.json records; development history excluded",
                }
            )
    if not registry.empty:
        pub = registry[registry["status"].eq("published")]
        rows.append(
            {
                "evidence_block": "Published extension registry",
                "analysis_family": "Extension stage",
                "n_records": len(pub),
                "n_stages": pub["extension_id"].nunique() if "extension_id" in pub else len(pub),
                "n_api_autonomous_success": "",
                "n_golden_passed": int(pub["golden_status"].map(status_pass).fillna(False).sum()) if "golden_status" in pub else "",
                "n_template_passed": "",
                "n_published_run_success": "",
                "success_rate": "",
                "scope_note": "current extension_stages/success registry only",
            }
        )
    if not vcf.empty:
        rows.append(
            {
                "evidence_block": "VCF-10 parameterized front-end runs",
                "analysis_family": "VCF",
                "n_records": len(vcf),
                "n_stages": 1,
                "n_api_autonomous_success": "",
                "n_golden_passed": "",
                "n_template_passed": "",
                "n_published_run_success": int(vcf["status"].map(status_pass).fillna(False).sum()),
                "success_rate": ratio(vcf["status"].map(status_pass).fillna(False).sum(), len(vcf)),
                "scope_note": "hard_filter_metrics.json records with runtime thresholds",
            }
        )
    return pd.DataFrame(rows)


def write_tables(out_dir: Path, tables: dict[str, pd.DataFrame]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        csv_path = out_dir / f"{name}.csv"
        df.to_csv(csv_path, index=False)
        md_path = out_dir / f"{name}.md"
        if df.empty:
            md_path.write_text("_No records found._\n", encoding="utf-8")
        else:
            preview = df.copy()
            if len(preview) > 60:
                preview = preview.head(60)
            md_path.write_text(dataframe_to_markdown(preview), encoding="utf-8")


def dataframe_to_markdown(df: pd.DataFrame) -> str:
    """Small dependency-free Markdown table writer for preview files."""
    if df.empty:
        return "_No records found._\n"
    safe = df.fillna("").astype(str)
    max_width = 90
    headers = [str(c) for c in safe.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for _, row in safe.iterrows():
        cells = []
        for value in row.tolist():
            value = value.replace("\n", " ").replace("|", "\\|")
            if len(value) > max_width:
                value = value[: max_width - 1] + "..."
            cells.append(value)
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def style_axes(ax: plt.Axes) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color="#E5E7EB", linewidth=0.7)
    ax.set_axisbelow(True)


def save_figure(fig: plt.Figure, out_dir: Path, name: str) -> None:
    for ext in ("svg", "pdf", "tiff", "png"):
        kwargs: dict[str, Any] = {"bbox_inches": "tight"}
        if ext == "tiff":
            kwargs["dpi"] = 600
        elif ext == "png":
            kwargs["dpi"] = 300
        fig.savefig(out_dir / f"{name}.{ext}", **kwargs)
    plt.close(fig)


def plot_dashboard(cases: pd.DataFrame, vcf: pd.DataFrame, out_dir: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 6.35))
    fig.patch.set_facecolor("white")

    selected = select_main_cases(cases)

    ax = axes[0, 0]
    if not selected.empty:
        stage = (
            selected.groupby(["group", "case_label"], dropna=False)
            .agg(
                n=("case_label", "size"),
                success=("api_autonomous_success", lambda s: s.map(status_pass).fillna(False).sum()),
            )
            .reset_index()
        )
        stage["success_rate"] = stage["success"] / stage["n"]
        stage["stage_order"] = stage["case_label"].map(stage_sort_key)
        stage = stage.sort_values(["group", "stage_order", "case_label"])
        colors = np.where(stage["group"].eq("GWAS"), "#2F6F73", "#A14F3B")
        y = np.arange(len(stage))
        ax.barh(y, stage["success_rate"] * 100, color=colors, height=0.66)
        ax.set_yticks(y)
        ax.set_yticklabels(stage["case_label"], fontsize=6.3)
        ax.set_xlim(0, 105)
        ax.set_xlabel("API autonomous success (%)")
        for i, row in stage.iterrows():
            ax.text(min(102, row["success_rate"] * 100 + 1.2), i, f"{int(row['success'])}/{int(row['n'])}", va="center", fontsize=6.5)
    ax.set_title("a  Case-level conversion success", loc="left", fontweight="bold", fontsize=9)
    style_axes(ax)

    ax = axes[0, 1]
    if not selected.empty:
        gate_rows = []
        for col, label in GATE_COLUMNS.items():
            if col not in selected.columns:
                continue
            vals = selected[col].map(status_pass)
            den = vals.notna().sum()
            gate_rows.append({"gate": label, "pass_rate": ratio(vals.fillna(False).sum(), den) * 100, "n": den})
        gate_df = pd.DataFrame(gate_rows)
        ax.plot(gate_df["gate"], gate_df["pass_rate"], marker="o", color="#30475E", linewidth=1.8)
        ax.fill_between(range(len(gate_df)), gate_df["pass_rate"], color="#D9E4EC", alpha=0.65)
        ax.set_ylim(0, 105)
        ax.set_ylabel("Pass rate (%)")
        ax.set_xticks(range(len(gate_df)))
        ax.set_xticklabels(gate_df["gate"], rotation=45, ha="right", fontsize=6.8)
        for x, row in gate_df.iterrows():
            ax.text(x, row["pass_rate"] + 2, f"n={int(row['n'])}", ha="center", fontsize=6.2)
    ax.set_title("b  Gate-by-gate verification", loc="left", fontweight="bold", fontsize=9)
    style_axes(ax)

    ax = axes[1, 0]
    if not selected.empty and "total_api_attempt_count" in selected:
        attempts = pd.to_numeric(selected["total_api_attempt_count"], errors="coerce").dropna()
        if not attempts.empty:
            capped = attempts.clip(upper=8)
            bins = np.arange(0.5, 9.6, 1)
            ax.hist(capped, bins=bins, color="#8A6F3B", edgecolor="white", linewidth=0.8)
            ax.set_xticks(range(1, 9))
            ax.set_xticklabels([str(i) for i in range(1, 8)] + [">=8"])
            ax.set_xlabel("API attempts per successful case")
            ax.set_ylabel("Case count")
    ax.set_title("c  Repair demand distribution", loc="left", fontweight="bold", fontsize=9)
    style_axes(ax)

    ax = axes[1, 1]
    if not vcf.empty:
        vcf_plot = vcf.sort_values(["parameter_profile", "snp_MQ", "snp_FS"]).copy()
        labels = [
            f"{short_profile(r.parameter_profile)}"
            for r in vcf_plot.itertuples()
        ]
        x = np.arange(len(vcf_plot))
        width = 0.28
        ax.bar(x - width, vcf_plot["snp_pass_count"], width, label="SNP PASS", color="#2F6F73")
        ax.bar(x, vcf_plot["clean_snp_count"], width, label="Clean SNP", color="#7AA6A1")
        ax.bar(x + width, vcf_plot["indel_pass_count"], width, label="INDEL PASS", color="#A14F3B")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=0, fontsize=6.1)
        ax.set_ylabel("Variant records")
        ax.legend(frameon=False, fontsize=6.5, ncols=3, loc="upper center", bbox_to_anchor=(0.5, 1.08))
    ax.set_title("d  Runtime threshold traceability", loc="left", fontweight="bold", fontsize=9)
    style_axes(ax)

    fig.subplots_adjust(wspace=0.35, hspace=0.6)
    save_figure(fig, out_dir, "figure_1_conversion_evidence_dashboard")


def plot_heatmap(cases: pd.DataFrame, out_dir: Path) -> None:
    selected = select_main_cases(cases)
    gate_labels = [label for col, label in GATE_COLUMNS.items() if col in selected.columns]
    gate_cols = [col for col in GATE_COLUMNS if col in selected.columns]
    if selected.empty or not gate_cols:
        return

    stages = sorted(selected["case_label"].dropna().unique(), key=stage_sort_key)
    mat = []
    annot = []
    for stage in stages:
        sub = selected[selected["case_label"].eq(stage)]
        row = []
        arow = []
        for col in gate_cols:
            vals = sub[col].map(status_pass)
            den = vals.notna().sum()
            pass_n = int(vals.fillna(False).sum())
            row.append(ratio(pass_n, den) * 100 if den else np.nan)
            arow.append("NA" if den == 0 else f"{pass_n}/{den}")
        mat.append(row)
        annot.append(arow)
    arr = np.array(mat, dtype=float)

    fig, ax = plt.subplots(figsize=(7.2, max(3.8, 0.34 * len(stages) + 1.2)))
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list("pass", ["#F1D0C9", "#F8F4E3", "#2F6F73"])
    cmap.set_bad("#F3F4F6")
    im = ax.imshow(arr, aspect="auto", cmap=cmap, vmin=0, vmax=100)
    ax.set_xticks(range(len(gate_labels)))
    ax.set_xticklabels(gate_labels, rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(len(stages)))
    ax.set_yticklabels(stages, fontsize=7)
    for i in range(arr.shape[0]):
        for j in range(arr.shape[1]):
            text = annot[i][j]
            if text:
                ax.text(j, i, text, ha="center", va="center", fontsize=6.2, color="#111827")
    ax.set_title("Stage-wise verification matrix", loc="left", fontweight="bold", fontsize=10)
    cbar = fig.colorbar(im, ax=ax, shrink=0.8, pad=0.02)
    cbar.set_label("Pass rate (%)", fontsize=8)
    cbar.ax.tick_params(labelsize=7)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.subplots_adjust(bottom=0.28, left=0.32, right=0.96, top=0.9)
    save_figure(fig, out_dir, "figure_2_stage_gate_heatmap")


def plot_vcf_thresholds(vcf: pd.DataFrame, out_dir: Path) -> None:
    if vcf.empty:
        return
    df = vcf.sort_values(["parameter_profile", "snp_MQ", "snp_FS"]).copy()
    labels = [
        f"{short_profile(r.parameter_profile)}\nMQ {r.snp_MQ:g} | FS {r.snp_FS:g} | SOR {r.snp_SOR:g} | INDEL FS {r.indel_FS:g}"
        for r in df.itertuples()
    ]
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.1), gridspec_kw={"width_ratios": [1.35, 1]})
    x = np.arange(len(df))
    axes[0].plot(x, df["snp_input_count"], marker="o", color="#6B7280", label="SNP input")
    axes[0].plot(x, df["snp_pass_count"], marker="o", color="#2F6F73", label="SNP PASS")
    axes[0].plot(x, df["clean_snp_count"], marker="o", color="#7AA6A1", label="Clean SNP")
    axes[0].plot(x, df["indel_pass_count"], marker="o", color="#A14F3B", label="INDEL PASS")
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(labels, fontsize=6.2)
    axes[0].set_ylabel("Variant records")
    axes[0].legend(frameon=False, fontsize=6.5)
    axes[0].set_title("a  Variant retention", loc="left", fontweight="bold", fontsize=9)
    style_axes(axes[0])

    threshold_cols = ["snp_MQ", "snp_FS", "snp_SOR", "indel_FS"]
    y = np.arange(len(threshold_cols))
    for i, row in enumerate(df.itertuples()):
        values = [getattr(row, col) for col in threshold_cols]
        axes[1].scatter(values, y + (i - (len(df) - 1) / 2) * 0.08, s=35, label=short_profile(row.parameter_profile))
    axes[1].set_yticks(y)
    axes[1].set_yticklabels(["SNP MQ", "SNP FS", "SNP SOR", "INDEL FS"], fontsize=7)
    axes[1].set_xlabel("Threshold value")
    axes[1].set_title("b  Runtime thresholds", loc="left", fontweight="bold", fontsize=9)
    axes[1].legend(frameon=False, fontsize=6.5, loc="lower right")
    style_axes(axes[1])

    fig.subplots_adjust(wspace=0.35, bottom=0.3)
    save_figure(fig, out_dir, "figure_3_vcf10_threshold_traceability")


def write_notes(out_dir: Path, tables: dict[str, pd.DataFrame]) -> None:
    summary = tables["table_1_manuscript_evidence_summary"]
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# 转写实验图表与表格说明",
        "",
        f"生成时间: {generated}",
        "",
        "## 主要结论",
        "",
        "这些图表汇总了本项目最近的拓展分析/API 转写记录，覆盖 GWAS、VCF-10 阈值过滤和已发布 extension stage 注册表。所有数值均来自本地 JSON/CSV 记录，脚本不手工填写成功数。",
        "",
        "## 推荐正文图",
        "",
        "- `figure_1_conversion_evidence_dashboard.svg/pdf/tiff`: 转写成功率、门控验证、API 修复次数和 VCF-10 参数化运行指标的综合面板。",
        "- `figure_2_stage_gate_heatmap.svg/pdf/tiff`: 各阶段在 raw/build/trial/validation/golden/template/publish 等门控上的通过矩阵。",
        "- `figure_3_vcf10_threshold_traceability.svg/pdf/tiff`: VCF-10 前端运行参数阈值与变异保留数的对应关系。",
        "",
        "## 推荐正文/补充表",
        "",
        "- `table_1_manuscript_evidence_summary.csv`: 文章主表候选，按证据块汇总。",
        "- `table_s1_all_benchmark_summaries.csv`: 所有 benchmark 汇总，含历史开发记录和最终候选记录。",
        "- `table_s2_all_case_results.csv`: case 级 API 转写结果，含审计路径。",
        "- `table_s3_extension_registry.csv`: 当前 extension registry 状态、golden 状态和参数化情况。",
        "- `table_s4_extension_runs.csv`: validation/trial/前端真实执行记录。",
        "- `table_s5_vcf10_threshold_metrics.csv`: VCF-10 阈值、样本数和变异记录数。",
        "",
        "## 统计口径",
        "",
        "- `main_evidence`: 正文统计口径，包含 GWAS API20/补跑 case、VCF 01-10 API20 case，以及 VCF-10 前端阈值真实运行；不包含早期 retest、fixture、空字段中间测试或通用扩展阶段。",
        "- `development_history`: 早期 retest、tooling audit、P0 fixture 或 pre-fix benchmark，保留为方法修订过程，不并入主成功率。",
        "- 余额不足导致的中断不被自动统计为方法失败；只有 result/manifest 中明确 failed 的记录才进入失败列。",
        "- VCF API conversion benchmark 反映 VCF 01-10 的 API20 阶段池；VCF-10 最终参数化前端运行单独由 `table_s5_vcf10_threshold_metrics.csv` 和 Figure 3 支撑。",
        "- Figure 2 中的灰色 `NA` 表示该字段未被对应 schema 记录，不代表失败；例如 VCF API20 的 `raw_code_ok` 未写入 result_row.json。",
        "- VCF-10 使用的是 partial real VCF inputs，不等同全样本全基因组生产数据。",
        "",
        "## QA",
        "",
        "- 图已导出 SVG/PDF/TIFF/PNG；SVG 使用可编辑文本，PDF 使用 TrueType 字体。",
        "- 每张图的源数据均保存在同目录 CSV 中。",
        "- 绝对路径仅保留在审计表中，正文主表可使用 `table_1_manuscript_evidence_summary.csv`。",
        "",
    ]
    if not summary.empty:
        lines.extend(["## 主表预览", "", dataframe_to_markdown(summary), ""])
    (out_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")

    legend = [
        "# Figure Legends",
        "",
        "**Figure 1. Evidence dashboard for API-converted extension stages.** "
        "Panel a shows case-level autonomous API conversion success across the curated main_evidence record set. "
        "Panel b summarizes pass rates across build, wrapper, trial, validation, golden comparison, template gate and published-run checks. "
        "Panel c reports the number of API build/repair attempts needed for successful cases. "
        "Panel d links VCF-10 runtime thresholds to retained SNP/INDEL records.",
        "",
        "**Figure 2. Stage-wise verification matrix.** "
        "Rows represent GWAS/VCF conversion stages and columns represent verification gates. "
        "Cell labels show passed/available records; color encodes pass rate. "
        "Grey NA cells indicate fields not recorded by the corresponding result schema, not failed gates.",
        "",
        "**Figure 3. VCF-10 threshold traceability.** "
        "The left panel reports variant-retention counts under recorded hard-filter profiles. "
        "The right panel shows the runtime thresholds recorded by the front-end execution manifest.",
        "",
    ]
    (out_dir / "FIGURE_LEGENDS.md").write_text("\n".join(legend), encoding="utf-8")


def configure_matplotlib() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "DejaVu Sans", "Liberation Sans"],
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.labelsize": 8,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "legend.fontsize": 7,
            "axes.titlesize": 9,
            "figure.dpi": 150,
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=PROJECT_RECORDS / "转写实验图表_20260624")
    args = parser.parse_args()

    configure_matplotlib()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    summaries = collect_benchmark_summaries()
    cases = collect_case_results()
    registry = collect_extension_registry()
    runs = collect_extension_runs()
    vcf = collect_vcf_metrics()
    manuscript_summary = make_manuscript_summary(cases, registry, vcf)

    tables = {
        "table_1_manuscript_evidence_summary": manuscript_summary,
        "table_s1_all_benchmark_summaries": summaries,
        "table_s2_all_case_results": cases,
        "table_s3_extension_registry": registry,
        "table_s4_extension_runs": runs,
        "table_s5_vcf10_threshold_metrics": vcf,
    }
    write_tables(args.out_dir, tables)

    plot_dashboard(cases, vcf, args.out_dir)
    plot_heatmap(cases, args.out_dir)
    plot_vcf_thresholds(vcf, args.out_dir)
    write_notes(args.out_dir, tables)

    manifest = {
        "schema_version": "conversion_publication_figure_manifest_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "out_dir": str(args.out_dir),
        "source_roots": [str(PROJECT_RECORDS), str(EXTENSION_STAGES), str(WORKSPACE_RUN_ROOT)],
        "tables": {name: {"path": str(args.out_dir / f"{name}.csv"), "rows": int(len(df))} for name, df in tables.items()},
        "figures": [
            "figure_1_conversion_evidence_dashboard",
            "figure_2_stage_gate_heatmap",
            "figure_3_vcf10_threshold_traceability",
        ],
        "notes": "Historical development records are retained in supplementary tables but not merged into main_evidence pass rates.",
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
