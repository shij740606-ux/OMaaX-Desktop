#!/usr/bin/env python3
"""
RNA-Seq Differential Expression阶段：进行差异表达分析

输入：
  - 基因计数矩阵（来自RNA_QUAN阶段）
  - 分组配置文件
  - 基因家族成员ID文件（可选）

输出：
  - 差异表达基因列表（全基因组）
  - 基因家族成员差异表达结果（可选）
  - 可视化图表（MA plot, Volcano plot）
  - 分析报告和manifest
"""

import argparse
import json
import subprocess
import sys
import time
import yaml
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).absolute().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.rnaseq_context import apply_rnaseq_context, build_rnaseq_context, resolve_rnaseq_project_context
from gfa_core.rnaseq_de_design import plan_rnaseq_de_design
from typing import Any, Dict, List, Optional, Tuple
import hashlib
import os
import re

# 镜像标准形态: 当前解释器即环境解释器(bioflow-rnaseq); 工具一律 PATH 裸命令。
RNASEQ_BIN = str(Path(sys.executable).resolve().parent)
if RNASEQ_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = RNASEQ_BIN + os.pathsep + os.environ.get("PATH", "")


GENE_ID_SUFFIX_PATTERNS = [
    re.compile(r"(?i)([._-](?:mrna|rna|transcript|cds|protein|pep|peptide)\d+[A-Za-z]?)$"),
    re.compile(r"(?i)([._-][tp]\d+[A-Za-z]?)$"),
    re.compile(r"(?i)([._-][rp][A-Z])$"),
    re.compile(r"(\.\d+)$"),
]


def normalize_gene_id(gene_id: str) -> str:
    value = (gene_id or "").strip().strip('"').lstrip(">")
    value = re.split(r"\s+", value, maxsplit=1)[0]
    previous = None
    while value and value != previous:
        previous = value
        for pattern in GENE_ID_SUFFIX_PATTERNS:
            value = pattern.sub("", value)
    return value


def compute_file_hash(filepath: Path) -> str:
    """计算文件的SHA256哈希值"""
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception:
        return ""


def abs_text(path: Optional[Path]) -> str:
    return str(Path(path).absolute()) if path else ""


def get_tool_versions() -> Dict[str, str]:
    """获取所需工具版本信息"""
    versions = {}
    
    # R
    try:
        result = subprocess.run(
            [f"{RNASEQ_BIN}/R", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        output = result.stdout.strip() or result.stderr.strip()
        versions["R"] = output.split('\n')[0] if output else "unknown"
    except Exception:
        versions["R"] = "not_found"
    
    # DESeq2 (reported through R)
    versions["DESeq2"] = "via R"
    
    return versions


def r_string(value: Any) -> str:
    """Return an R-safe string literal."""
    return json.dumps(str(value))


def r_vector(values: List[Any]) -> str:
    return "c(" + ", ".join(r_string(value) for value in values) + ")"


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_time_sort_value(value: Any) -> Tuple[int, Any]:
    text = str(value)
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if match:
        return (0, float(match.group(0)))
    return (1, text)


def comparison_name(group_a: str, group_b: str) -> str:
    return f"{group_a}_vs_{group_b}"


def normalize_samples_config(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    samples = cfg.get("samples") or {}
    if not isinstance(samples, dict):
        return {}
    normalized: Dict[str, Dict[str, Any]] = {}
    for sample, value in samples.items():
        normalized[str(sample)] = value if isinstance(value, dict) else {}
    return normalized



def extract_sample_metadata(
    sample_meta: dict,
    matrix_samples: list,
) -> dict:
    """Extract batch/genotype/subject_id/dose/tissue from sample metadata."""
    result = {
        "batch": {},
        "genotype": {},
        "subject_id": {},
        "dose": {},
        "tissue": {},
    }
    for s in matrix_samples:
        meta = sample_meta.get(s, {})
        for field in result:
            val = meta.get(field)
            if val is not None and str(val).strip():
                result[field][s] = str(val).strip()
    return result


def normalize_group_label(group: Any, replicate: Any = "") -> str:
    value = str(group or "").strip()
    rep = str(replicate or "").strip()
    if not value:
        return value
    if re.match(r"^-?\d+(?:\.\d+)?$", value):
        return value
    if rep:
        match = re.match(rf"^(.+?)[._-](?:rep|r)?{re.escape(rep)}$", value, flags=re.IGNORECASE)
        if match and match.group(1).strip():
            return match.group(1).strip()
    for pattern in (r"^(.+?)[._-](?:rep|r)?\d+$", r"^(.+?)[._-]\d+$"):
        match = re.match(pattern, value, flags=re.IGNORECASE)
        if match and match.group(1).strip():
            return match.group(1).strip()
    return value


def load_groups_from_config(cfg: Dict[str, Any]) -> Dict[str, str]:
    groups: Dict[str, str] = {}
    sample_meta = normalize_samples_config(cfg)
    raw_groups = cfg.get("groups") or {}
    if isinstance(raw_groups, dict):
        for sample, group in raw_groups.items():
            meta = sample_meta.get(str(sample), {})
            groups[str(sample)] = normalize_group_label(group, meta.get("replicate", ""))

    if not groups:
        for sample, meta in sample_meta.items():
            group = meta.get("group") or meta.get("condition") or meta.get("treatment")
            if group is not None:
                groups[sample] = normalize_group_label(group, meta.get("replicate", ""))

    if not groups and "samples" in cfg:
        for sample in sample_meta.keys():
            groups[sample] = sample
    return groups


def load_sample_times(cfg: Dict[str, Any], matrix_samples: List[str]) -> Dict[str, Any]:
    sample_meta = normalize_samples_config(cfg)
    times: Dict[str, Any] = {}
    for sample in matrix_samples:
        meta = sample_meta.get(sample, {})
        if "time" in meta:
            times[sample] = meta["time"]
        elif "day" in meta:
            times[sample] = meta["day"]
    return times


def build_replicate_counts(matrix_samples: List[str], groups: Dict[str, str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for sample in matrix_samples:
        group = groups[sample]
        counts[group] = counts.get(group, 0) + 1
    return counts


def get_replicate_policy(cfg: Dict[str, Any]) -> Dict[str, Any]:
    policy: Dict[str, Any] = {}
    if isinstance(cfg.get("replicate_policy"), dict):
        policy.update(cfg["replicate_policy"])
    de_cfg = cfg.get("de") if isinstance(cfg.get("de"), dict) else {}
    if isinstance(de_cfg.get("replicate_policy"), dict):
        policy.update(de_cfg["replicate_policy"])
    return {
        "min_reps_per_group": int(policy.get("min_reps_per_group", policy.get("min_bio_reps_per_group", 1))),
        "allow_no_replicate": parse_bool(policy.get("allow_no_replicate"), default=True),
        "on_violation": str(policy.get("on_violation", "warn")).lower(),
    }


def validate_replicates(replicate_counts: Dict[str, int], policy: Dict[str, Any]) -> List[str]:
    min_reps = int(policy.get("min_reps_per_group", 1))
    violations = [
        f"{group}={count}"
        for group, count in sorted(replicate_counts.items())
        if count < min_reps
    ]
    if violations and not policy.get("allow_no_replicate", True):
        message = (
            f"Replicate policy violation: min_reps_per_group={min_reps}, "
            + ", ".join(violations)
        )
        if policy.get("on_violation") == "error":
            raise ValueError(message)
        print(f"Warning: {message}", file=sys.stderr)
    return violations


def build_group_times(
    matrix_samples: List[str],
    groups: Dict[str, str],
    sample_times: Dict[str, Any],
) -> Dict[str, Any]:
    group_times: Dict[str, Any] = {}
    for sample in matrix_samples:
        if sample not in sample_times:
            continue
        group = groups[sample]
        time_value = sample_times[sample]
        if group in group_times and str(group_times[group]) != str(time_value):
            raise ValueError(
                f"Group {group} has inconsistent time values: {group_times[group]} and {time_value}"
            )
        group_times[group] = time_value
    return group_times


def infer_group_times_from_labels(groups: Dict[str, str]) -> Dict[str, Any]:
    """Infer time values from group labels such as T0, T12, T24 or day_3.

    This is intentionally conservative: every group must contain a numeric
    component, otherwise no inferred time-series design is returned.
    """
    unique_groups = sorted(set(groups.values()), key=lambda value: parse_time_sort_value(value))
    inferred: Dict[str, Any] = {}
    for group in unique_groups:
        sort_kind, sort_value = parse_time_sort_value(group)
        if sort_kind != 0:
            return {}
        inferred[group] = sort_value
    return inferred


def dedupe_comparisons(comparisons: List[List[str]]) -> List[List[str]]:
    seen = set()
    deduped: List[List[str]] = []
    for comp in comparisons:
        if len(comp) != 2:
            continue
        key = (str(comp[0]), str(comp[1]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append([key[0], key[1]])
    return deduped


def filter_comparisons_to_groups(comparisons: List[List[str]], valid_groups: List[str]) -> List[List[str]]:
    valid = set(valid_groups)
    filtered: List[List[str]] = []
    dropped: List[List[str]] = []
    for comp in comparisons:
        if len(comp) != 2:
            continue
        if comp[0] in valid and comp[1] in valid:
            filtered.append(comp)
        else:
            dropped.append(comp)
    if dropped:
        print(
            "DESeq2 warning: dropped comparisons with groups absent from samples.yaml/count matrix: "
            + ", ".join(f"{comp[0]}_vs_{comp[1]}" for comp in dropped),
            file=sys.stderr,
        )
    return dedupe_comparisons(filtered)


def build_auto_time_comparisons(
    existing: List[Any],
    group_times: Dict[str, Any],
    de_cfg: Dict[str, Any],
) -> List[List[str]]:
    existing = existing if isinstance(existing, list) else []
    comparisons = [[str(comp[0]), str(comp[1])] for comp in existing if isinstance(comp, list) and len(comp) == 2]
    if not group_times or not parse_bool(de_cfg.get("run_pairwise"), default=True):
        return dedupe_comparisons(comparisons)

    ordered_groups = [
        group for group, _ in sorted(group_times.items(), key=lambda item: parse_time_sort_value(item[1]))
    ]
    comparisons = filter_comparisons_to_groups(comparisons, ordered_groups)
    if len(ordered_groups) < 2:
        return dedupe_comparisons(comparisons)

    modes = de_cfg.get("pairwise_modes", ["baseline"])
    if isinstance(modes, str):
        modes = [modes]
    baseline_group = str(de_cfg.get("baseline_group") or ordered_groups[0])
    if baseline_group not in ordered_groups:
        raise ValueError(
            f"de.baseline_group={baseline_group} is not found in time-series groups: {', '.join(ordered_groups)}"
        )

    generated: List[List[str]] = []
    if "baseline" in modes:
        for group in ordered_groups:
            if group != baseline_group:
                generated.append([group, baseline_group])
    if "adjacent" in modes:
        for idx in range(1, len(ordered_groups)):
            generated.append([ordered_groups[idx], ordered_groups[idx - 1]])
    return dedupe_comparisons(comparisons + generated)


def get_experiment_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    experiment = cfg.get("experiment") if isinstance(cfg.get("experiment"), dict) else {}
    return experiment


def normalize_design(value: Any) -> str:
    design = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "timecourse": "time_series",
        "time_course": "time_series",
        "timeseries": "time_series",
        "case_control": "two_group",
        "binary": "two_group",
        "pairwise": "two_group",
        "categorical": "multi_group",
        "condition": "multi_group",
        "conditions": "multi_group",
    }
    return aliases.get(design, design)


def normalize_group_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [normalize_group_label(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [normalize_group_label(item) for item in value if str(item).strip()]
    return [normalize_group_label(value)] if str(value).strip() else []


def load_configured_comparisons(cfg: Dict[str, Any], de_cfg: Dict[str, Any]) -> List[List[str]]:
    raw = cfg.get("compare")
    if raw in (None, []) and isinstance(de_cfg.get("compare"), list):
        raw = de_cfg.get("compare")
    raw = raw if isinstance(raw, list) else []
    return [[normalize_group_label(comp[0]), normalize_group_label(comp[1])] for comp in raw if isinstance(comp, list) and len(comp) == 2]


def configured_group_value(
    cfg: Dict[str, Any],
    de_cfg: Dict[str, Any],
    key: str,
    fallback_keys: Optional[List[str]] = None,
) -> Any:
    experiment = get_experiment_cfg(cfg)
    keys = [key] + (fallback_keys or [])
    for container in (de_cfg, experiment, cfg):
        for item_key in keys:
            if isinstance(container, dict) and container.get(item_key) not in (None, ""):
                return container.get(item_key)
    return None


def build_control_comparisons(
    cfg: Dict[str, Any],
    de_cfg: Dict[str, Any],
    unique_groups: List[str],
) -> List[List[str]]:
    control_group = configured_group_value(cfg, de_cfg, "control_group", ["baseline_group", "reference_group"])
    treatment_groups = configured_group_value(cfg, de_cfg, "treatment_groups", ["treat_groups", "case_groups"])

    if not control_group:
        raise ValueError(
            "Non-time-series DE requires an explicit control_group/reference_group or manual compare. "
            "Example: experiment: {design: two_group, control_group: CK, treatment_groups: [Treat]} "
            "or compare: [[Treat, CK]]."
        )

    control_group = normalize_group_label(control_group)
    if control_group not in unique_groups:
        raise ValueError(
            f"control_group={control_group} is not found in groups: {', '.join(unique_groups)}"
        )

    treatments = normalize_group_list(treatment_groups)
    if not treatments:
        treatments = [group for group in unique_groups if group != control_group]
    missing = [group for group in treatments if group not in unique_groups]
    if missing:
        raise ValueError(f"treatment_groups not found in groups: {', '.join(missing)}")
    if not treatments:
        raise ValueError("No treatment groups remain after excluding control_group")

    return dedupe_comparisons([[group, control_group] for group in treatments if group != control_group])


def build_de_plan(
    cfg: Dict[str, Any],
    matrix_samples: List[str],
    groups: Dict[str, str],
    user_module: str = "",
) -> Dict[str, Any]:
    de_cfg = cfg.get("de") if isinstance(cfg.get("de"), dict) else {}
    experiment_cfg = get_experiment_cfg(cfg)
    configured_comparisons = load_configured_comparisons(cfg, de_cfg)
    sample_meta = normalize_samples_config(cfg)
    design_plan: Dict[str, Any] = {}
    if sample_meta:
        selected_module = str(user_module or de_cfg.get("module") or "").strip().upper()
        design_plan = plan_rnaseq_de_design(
            sample_meta,
            matrix_samples=matrix_samples,
            configured_compare=configured_comparisons,
            user_module=selected_module,
            design_options={**experiment_cfg, **de_cfg},
        )
        groups = dict(design_plan["groups"])
    elif not configured_comparisons:
        raise ValueError(
            "samples.yaml 缺少 samples/group_role，无法自动生成 DE 比较；"
            "请在样本表中补齐 group_role=control/compare/ignore。"
        )

    planning_samples = list(design_plan.get("active_samples") or matrix_samples)
    sample_times = load_sample_times(cfg, planning_samples)
    group_times = build_group_times(planning_samples, groups, sample_times)
    strategy = str(de_cfg.get("strategy", "auto")).lower()
    design = normalize_design(
        design_plan.get("design")
        or de_cfg.get("design")
        or experiment_cfg.get("design")
    )
    inferred_design = ""
    if not group_times and strategy == "auto" and design in {"", "time_series"}:
        group_times = infer_group_times_from_labels(groups)
        if group_times and not design:
            inferred_design = "time_series_from_group_labels"
    if design == "time_series" and not group_times:
        group_times = infer_group_times_from_labels(groups)
    replicate_counts = build_replicate_counts(planning_samples, groups)
    policy = get_replicate_policy(cfg)
    replicate_violations = validate_replicates(replicate_counts, policy)

    unique_groups = sorted(set(groups[sample] for sample in planning_samples), key=lambda value: parse_time_sort_value(value))
    if design_plan:
        comparisons = list(design_plan.get("compare") or [])
        comparison_mode = str(design_plan.get("comparison_mode") or "group_role_control")
    elif configured_comparisons:
        comparisons = filter_comparisons_to_groups(configured_comparisons, unique_groups)
        comparison_mode = "configured"
    elif strategy == "auto" and design in {"two_group", "multi_group"}:
        comparisons = build_control_comparisons(cfg, de_cfg, unique_groups)
        comparison_mode = "configured_control"
    elif strategy == "auto" and (design in {"", "time_series"} and group_times):
        comparisons = build_auto_time_comparisons(configured_comparisons, group_times, de_cfg)
        comparison_mode = "time_series_auto"
    else:
        comparisons = filter_comparisons_to_groups(configured_comparisons, unique_groups)
        comparison_mode = "configured" if configured_comparisons else ""

    if not comparisons and design == "manual":
        raise ValueError("manual/explicit DE design requires compare entries.")

    if strategy == "auto" and not comparisons and design_plan.get("module") != "M6":
        raise ValueError(
            "No pairwise comparisons were configured. Fill group_role in samples.yaml "
            "or provide explicit compare entries."
        )

    has_time_series = len(group_times) >= 3
    run_lrt = (
        parse_bool(de_cfg.get("run_lrt"), default=False)
        if "run_lrt" in de_cfg
        else strategy == "auto" and has_time_series and design in {"", "time_series"}
    )
    if design in {"two_group", "multi_group"}:
        run_lrt = False
    run_spline_lrt = parse_bool(de_cfg.get("run_spline_lrt"), default=False)
    spline_df = int(de_cfg.get("spline_df", 3))
    time_values_numeric = [parse_time_sort_value(value)[0] == 0 for value in sample_times.values()]
    run_spline_lrt = run_spline_lrt and has_time_series and all(time_values_numeric) and len(group_times) > spline_df
    time_levels = [group for group, _ in sorted(group_times.items(), key=lambda item: parse_time_sort_value(item[1]))]
    effective_control_group = (
        str(design_plan.get("control_group") or "")
        if design_plan
        else (time_levels[0] if time_levels else (comparisons[0][1] if comparisons else ""))
    )

    module_name = str(design_plan.get("module") or "")
    design_formula_v2 = str(design_plan.get("design_formula") or "")
    test_type_v2 = str(design_plan.get("test_type") or "Wald")
    batch_used_v2 = bool(design_plan.get("batch_used", False))
    # Extract extended metadata for modular R scripts
    sample_metadata = extract_sample_metadata(sample_meta, matrix_samples)
    batch_values = sample_metadata.get("batch", {})
    genotype_values = sample_metadata.get("genotype", {})
    subject_id_values = sample_metadata.get("subject_id", {})
    dose_values = sample_metadata.get("dose", {})
    tissue_values = sample_metadata.get("tissue", {})

    return {
        "strategy": strategy,
        "design": design or inferred_design or ("time_series" if group_times else "manual"),
        "comparisons": comparisons,
        "comparison_mode": comparison_mode,
        "active_samples": planning_samples,
        "ignored_samples": list(design_plan.get("ignored_samples") or []),
        "groups": groups,
        "replicate_counts": replicate_counts,
        "replicate_policy": policy,
        "replicate_violations": replicate_violations,
        "module": module_name,
        "design_formula": design_formula_v2,
        "test_type": test_type_v2,
        "batch_used": batch_used_v2,
        "batch_requested": bool(design_plan.get("batch_requested", False)),
        "batch_levels": list(design_plan.get("batch_levels") or []),
        "batch_fallback": bool(design_plan.get("batch_fallback", False)),
        "batch_fallback_reason": str(design_plan.get("batch_fallback_reason") or ""),
        "batch_values": batch_values,
        "genotype_values": genotype_values,
        "subject_id_values": subject_id_values,
        "dose_values": dose_values,
        "tissue_values": tissue_values,
        "condition_values": dict(design_plan.get("condition_values") or {}),
        "factor_values": dict(design_plan.get("factor_values") or {}),
        "advanced_factor": str(design_plan.get("advanced_factor") or ""),
        "interaction_full": str(design_plan.get("interaction_full") or ""),
        "interaction_reduced": str(design_plan.get("interaction_reduced") or ""),
        "control_condition": str(design_plan.get("control_condition") or ""),
        "reference_condition": str(design_plan.get("reference_condition") or ""),
        "reference_factor": str(design_plan.get("reference_factor") or ""),
        "comparison_axis": str(design_plan.get("comparison_axis") or ""),
        "comparison_axis_source": str(design_plan.get("comparison_axis_source") or ""),
        "stratify_axis": str(design_plan.get("stratify_axis") or ""),
        "condition_reference_source": str(design_plan.get("condition_reference_source") or ""),
        "factor_reference_source": str(design_plan.get("factor_reference_source") or ""),
        "factor_levels": list(design_plan.get("factor_levels") or []),
        "condition_levels": list(design_plan.get("condition_levels") or []),
        "design_warnings": list(design_plan.get("warnings") or []),
        "fallback_from": str(design_plan.get("fallback_from") or ""),
        "fallback_reason": str(design_plan.get("fallback_reason") or ""),
        "skipped_comparisons": list(design_plan.get("skipped_comparisons") or []),
        "sample_metadata": sample_metadata,
        "sample_times": {sample: sample_times.get(sample) for sample in matrix_samples if sample in sample_times},
        "group_times": group_times,
        "time_levels": time_levels,
        "effective_control_group": effective_control_group,
        "run_lrt": run_lrt and has_time_series,
        "run_spline_lrt": run_spline_lrt,
        "spline_df": spline_df,
    }


def run_family_de_analysis(
    de_dir: Path,
    family_ids_file: Path,
    final_dir: Path,
    family_name: str,
    padj_thresh: float = 0.05,
    log2fc_thresh: float = 1.0,
    prefix: str = "rnaseq"
) -> Dict[str, int]:
    """提取基因家族成员的差异表达结果"""
    import shutil
    
    # 读取家族基因ID
    family_ids = set()
    with open(family_ids_file, 'r') as f:
        for line in f:
            gene_id = normalize_gene_id(line.strip().split('\t')[0])
            if gene_id:
                family_ids.add(gene_id)
    
    print(f"  Loaded {len(family_ids)} family member IDs")
    
    stats = {"total_family_genes": len(family_ids), "de_family_genes": 0}
    
    # 处理每个比较组
    for de_file in de_dir.glob("*.DE_results.txt"):
        comp_name = de_file.stem.replace(".DE_results", "")
        
        # 读取全基因组DE结果
        family_de_results = []
        family_de_genes = []
        
        with open(de_file, 'r') as f:
            header = f.readline()  # 保存header
            for line in f:
                if not line.strip():
                    continue
                parts = line.strip().split('\t')
                if len(parts) < 7:
                    continue
                gene_id = normalize_gene_id(parts[0])
                if gene_id in family_ids:
                    family_de_results.append(line)
                    # 检查是否是差异表达基因
                    try:
                        padj = float(parts[6]) if parts[6] != 'NA' else 1.0
                        log2fc = float(parts[2]) if parts[2] != 'NA' else 0.0
                        if padj < padj_thresh and abs(log2fc) > log2fc_thresh:
                            family_de_genes.append(line)
                    except (ValueError, IndexError):
                        pass
        
        # 保存家族特异性结果，使用标准命名格式
        safe_prefix = prefix if prefix else "rnaseq"
        if family_de_results:
            family_results_file = final_dir / f"{safe_prefix}.rnaseq_de.{family_name}_{comp_name}.family_DE_results.txt"
            with open(family_results_file, 'w') as f:
                f.write(header)
                f.writelines(family_de_results)
            print(f"  {comp_name}: {len(family_de_results)} family genes in total results")
            
            # 生成家族特异性绘图
            generate_family_plots(family_results_file, final_dir, family_name, comp_name, safe_prefix, padj_thresh)
        
        if family_de_genes:
            family_de_file = final_dir / f"{safe_prefix}.rnaseq_de.{family_name}_{comp_name}.family_DE_genes.txt"
            with open(family_de_file, 'w') as f:
                f.write(header)
                f.writelines(family_de_genes)
            stats["de_family_genes"] += len(family_de_genes)
            print(f"  {comp_name}: {len(family_de_genes)} family DE genes")
    
    return stats


def generate_family_plots(
    de_results_file: Path,
    final_dir: Path,
    family_name: str,
    comp_name: str,
    prefix: str,
    padj_thresh: float = 0.05
):
    """为基因家族生成特异性绘图（MA图和火山图）"""
    import subprocess
    import tempfile
    
    # 创建R脚本
    r_script = f"""
    library(ggplot2)
    library(dplyr)
    
    # 读取数据
    de_data <- read.csv("{de_results_file}", sep="\\t", header=TRUE)
    
    # 确保列名正确
    colnames(de_data) <- make.names(colnames(de_data))
    
    # 计算mean expression (baseMean)
    de_data$meanExpr <- log2(de_data$baseMean + 1)
    
    # 确定显著性
    de_data$significant <- ifelse(de_data$padj < {padj_thresh}, "Significant", "Not Significant")
    
    # MA Plot
    ma_plot <- ggplot(de_data, aes(x=meanExpr, y=log2FoldChange, color=significant)) +
        geom_point(alpha=0.6, size=2) +
        scale_color_manual(values=c("Not Significant"="gray50", "Significant"="red")) +
        geom_hline(yintercept=0, linetype="dashed", color="blue") +
        theme_bw() +
        theme(legend.position="bottom") +
        labs(title="MA Plot - {family_name} Family",
             subtitle="{comp_name}",
             x="Log2 Mean Expression",
             y="Log2 Fold Change",
             color="Significance")
    
    ggsave("{final_dir}/{prefix}.rnaseq_de.{family_name}_{comp_name}_family_MA_plot.pdf", 
           ma_plot, width=8, height=6)
    ggsave("{final_dir}/{prefix}.rnaseq_de.{family_name}_{comp_name}_family_MA_plot.png", 
           ma_plot, width=8, height=6, dpi=150)
    
    # Volcano Plot
    de_data$volcano_color <- case_when(
        de_data$padj < {padj_thresh} & de_data$log2FoldChange > 0 ~ "Up-regulated",
        de_data$padj < {padj_thresh} & de_data$log2FoldChange < 0 ~ "Down-regulated",
        TRUE ~ "Not Significant"
    )
    
    volcano_plot <- ggplot(de_data, aes(x=log2FoldChange, y=-log10(pvalue), color=volcano_color)) +
        geom_point(alpha=0.6, size=2) +
        scale_color_manual(values=c("Up-regulated"="red", "Down-regulated"="blue", "Not Significant"="gray50")) +
        geom_vline(xintercept=c(-1, 1), linetype="dashed", color="gray") +
        geom_hline(yintercept=-log10(0.05), linetype="dashed", color="gray") +
        theme_bw() +
        theme(legend.position="bottom") +
        labs(title="Volcano Plot - {family_name} Family",
             subtitle="{comp_name}",
             x="Log2 Fold Change",
             y="-Log10 P-value",
             color="Regulation")
    
    ggsave("{final_dir}/{prefix}.rnaseq_de.{family_name}_{comp_name}_family_volcano_plot.pdf", 
           volcano_plot, width=8, height=6)
    ggsave("{final_dir}/{prefix}.rnaseq_de.{family_name}_{comp_name}_family_volcano_plot.png", 
           volcano_plot, width=8, height=6, dpi=150)
    
    cat("Family-specific plots generated successfully\\n")
    """
    
    # 写入临时R脚本
    with tempfile.NamedTemporaryFile(mode='w', suffix='.R', delete=False) as f:
        f.write(r_script)
        r_script_file = f.name
    
    try:
        # 运行R脚本
        result = subprocess.run(
            [f"{RNASEQ_BIN}/Rscript", r_script_file],
            capture_output=True,
            text=True,
            timeout=60
        )
        if result.returncode == 0:
            print(f"  Generated family-specific plots for {family_name}")
        else:
            print(f"  Warning: Failed to generate family plots: {result.stderr}")
    except Exception as e:
        print(f"  Warning: Error generating family plots: {e}")
    finally:
        # 清理临时文件
        import os
        if os.path.exists(r_script_file):
            os.remove(r_script_file)



def generate_modular_r_script(
    count_matrix,
    matrix_samples,
    de_plan,
    output_dir,
    padj_thresh,
    log2fc_thresh,
):
    """Generate modular R script based on v2 module type (M1-M6)."""
    pw_dds = "dds"
    pw_factor = "condition"
    module = de_plan.get("module", "")
    groups = de_plan.get("groups", {})
    comparisons = de_plan.get("comparisons", [])
    condition_map = de_plan.get("condition_values", {})
    sample_conditions = [condition_map.get(s, groups.get(s, "unknown")) for s in matrix_samples]
    sample_groups = [groups.get(s, "unknown") for s in matrix_samples]
    sample_cond_r = ", ".join('"' + str(s) + '"' for s in sample_conditions)
    sample_group_r = ", ".join('"' + str(s) + '"' for s in sample_groups)
    sample_names_r = ", ".join('"' + str(s) + '"' for s in matrix_samples)
    condition_values = sorted(set(str(s) for s in sample_conditions))
    cond_levels_r = ", ".join('"' + c + '"' for c in condition_values)

    # Extract metadata
    batch_map = de_plan.get("batch_values", {})
    genotype_map = de_plan.get("genotype_values", {})
    subject_id_map = de_plan.get("subject_id_values", {})
    dose_map = de_plan.get("dose_values", {})
    tissue_map = de_plan.get("tissue_values", {})
    sample_times = de_plan.get("sample_times", {})
    time_levels = de_plan.get("time_levels", [])
    batch_used = bool(de_plan.get("batch_used") and batch_map)
    batch_vals = [str(batch_map.get(s, "unknown")) for s in matrix_samples]
    batch_vals_r = ", ".join('"' + v + '"' for v in batch_vals)
    batch_levels_r = ", ".join('"' + v + '"' for v in sorted(set(batch_vals)))
    batch_code = f"\nbatch_vals <- c({batch_vals_r})" if batch_used else ""
    batch_col = f",\n    batch = factor(batch_vals, levels=c({batch_levels_r}))" if batch_used else ""
    batch_prefix = "batch + " if batch_used else ""

    r = f"""
library(DESeq2)
library(ggplot2)
library(ggrepel)

countData <- read.table("{count_matrix}", header=TRUE, row.names=1, sep="\\t",
                        check.names=FALSE, quote="", comment.char="")
featurecounts_meta_cols <- c("Chr", "Start", "End", "Strand", "Length")
if (all(featurecounts_meta_cols %in% colnames(countData))) {{
  countData <- countData[, setdiff(colnames(countData), featurecounts_meta_cols), drop=FALSE]
  colnames(countData) <- sub("\\\\.sorted$", "", tools::file_path_sans_ext(basename(colnames(countData))))
}}
countData[] <- lapply(countData, function(x) as.numeric(as.character(x)))
countData <- round(as.matrix(countData))
countData <- countData[, c({sample_names_r}), drop=FALSE]

samples <- colnames(countData)
condition_vals <- c({sample_cond_r})
group_vals <- c({sample_group_r})
{batch_code}
"""

    # ── Module-specific colData setup ──

    if module == "M5":
        pw_dds = "dds_pw"
        pw_factor = "group"
        def _pure_cond(gv):
            parts = str(gv).rsplit("_", 1)
            if len(parts) == 2 and parts[1].replace(".","").replace("h","").replace("d","").replace("min","").replace("s","").isdigit():
                return parts[0]
            return str(gv)
        pure_conds = [_pure_cond(groups.get(s, "")) for s in matrix_samples]
        pure_conds_r = ", ".join('"' + str(c) + '"' for c in pure_conds)
        cond_lvl = sorted(set(str(c) for c in pure_conds))
        cond_lvl_r = ", ".join('"' + c + '"' for c in cond_lvl)
        pw_conds = [str(groups.get(s, "unknown")) for s in matrix_samples]
        pw_conds_r = ", ".join('"' + c + '"' for c in pw_conds)
        pw_lvl = sorted(set(str(c) for c in pw_conds))
        pw_lvl_r = ", ".join('"' + c + '"' for c in pw_lvl)
        tvs = [str(sample_times.get(s, "")) for s in matrix_samples]
        tvs_r = ", ".join('"' + tv + '"' for tv in tvs)
        tls = sorted(set(tvs))
        tls_r = ", ".join('"' + str(t) + '"' for t in tls)
        has_g = bool(genotype_map)
        if has_g:
            gvs = [str(genotype_map.get(s, "unknown")) for s in matrix_samples]
            gvs_r = ", ".join('"' + gv + '"' for gv in gvs)
            glvl_r = ", ".join('"' + g + '"' for g in sorted(set(gvs)))
            df = de_plan.get("design_formula") or f"~ {batch_prefix}genotype + condition * time"
            rf = de_plan.get("reduced_formula") or f"~ {batch_prefix}genotype + condition + time"
            gcol = ",\n    genotype = factor(genotype_vals, c({glvl}))".format(glvl=glvl_r)
            gcode = "\ngenotype_vals <- c({gvs})".format(gvs=gvs_r)
        else:
            df = de_plan.get("design_formula") or f"~ {batch_prefix}condition * time"
            rf = de_plan.get("reduced_formula") or f"~ {batch_prefix}condition + time"
            gcol = ""
            gcode = ""
        r += f"""
# M5: Time series LRT{gcode}
condition_vals <- c({pure_conds_r})
time_vals <- c({tvs_r})
condition_vals_pw <- c({pw_conds_r})
coldata <- data.frame(
    condition = factor(condition_vals, levels=c({cond_lvl_r})),
    time = factor(time_vals, levels=c({tls_r})){gcol}{batch_col}
)
rownames(coldata) <- samples
dds <- DESeqDataSetFromMatrix(countData, coldata, design={df})
dds_lrt <- DESeq(dds, test="LRT", reduced={rf})
res_lrt <- na.omit(results(dds_lrt))
res_lrt_df <- as.data.frame(res_lrt)
res_lrt_df$gene_id <- rownames(res_lrt_df)
res_lrt_df <- res_lrt_df[, c("gene_id", setdiff(colnames(res_lrt_df), "gene_id"))]
write.table(res_lrt_df, file="{output_dir}/time_lrt.DE_results.txt", sep="\t", quote=FALSE, row.names=FALSE)
sig_lrt <- subset(res_lrt_df, padj < {padj_thresh})
write.table(sig_lrt, file="{output_dir}/time_lrt.DE_genes.txt", sep="\t", quote=FALSE, row.names=FALSE)
saveRDS(dds_lrt, file="{output_dir}/dds_time_lrt.rds")
coldata$group <- factor(condition_vals_pw, levels=c({pw_lvl_r}))
dds_pw <- DESeqDataSetFromMatrix(countData, coldata, design=~ {batch_prefix}group)
dds_pw <- DESeq(dds_pw)
"""

    elif module == "M4":
        # M4 always emits both the directional M3 simple effects and an
        # unsigned interaction LRT. Missing factor metadata is a design error,
        # never a reason to silently run M1.
        pw_dds = "dds_group"
        pw_factor = "group"
        inter_factor = str(de_plan.get("advanced_factor") or "")
        factor_map = de_plan.get("factor_values", {})
        control_condition = str(de_plan.get("effective_control_group") or "")
        control_condition = str(de_plan.get("control_condition") or control_condition)
        ordered_conditions = [control_condition] + sorted(
            value for value in set(sample_conditions) if value != control_condition
        )
        ordered_conditions_r = ", ".join('"' + value + '"' for value in ordered_conditions)
        group_levels_r = ", ".join('"' + value + '"' for value in sorted(set(sample_groups)))
        reference_factor = str(de_plan.get("reference_factor") or "")
        if inter_factor == "genotype" and factor_map:
            gvs = [factor_map.get(s, "") for s in matrix_samples]
            gvs_r = ", ".join('"' + gv + '"' for gv in gvs)
            ordered_factors = [reference_factor] + sorted(
                value for value in set(gvs) if value != reference_factor
            )
            glvl = ", ".join('"' + g + '"' for g in ordered_factors)
            r += f"""
# M4: Interaction ~ genotype * condition
genotype_vals <- c({gvs_r})
coldata <- data.frame(
    condition = factor(condition_vals, levels=c({ordered_conditions_r})),
    genotype = factor(genotype_vals, levels=c({glvl})),
    group = factor(group_vals, levels=c({group_levels_r})){batch_col}
)
rownames(coldata) <- samples
dds_group <- DESeqDataSetFromMatrix(countData, coldata, design=~ {batch_prefix}group)
dds_group <- DESeq(dds_group)
dds_wald <- DESeqDataSetFromMatrix(countData, coldata, design=~ {batch_prefix}genotype * condition)
dds_wald <- DESeq(dds_wald, test="Wald")
dds <- DESeqDataSetFromMatrix(countData, coldata, design=~ {batch_prefix}genotype * condition)
dds <- DESeq(dds, test="LRT", reduced=~ {batch_prefix}genotype + condition)
"""
        elif inter_factor == "tissue" and factor_map:
            tvs = [factor_map.get(s, "") for s in matrix_samples]
            tvs_r = ", ".join('"' + tv + '"' for tv in tvs)
            ordered_factors = [reference_factor] + sorted(
                value for value in set(tvs) if value != reference_factor
            )
            tlvl = ", ".join('"' + t + '"' for t in ordered_factors)
            r += f"""
# M4: Interaction ~ tissue * condition
tissue_vals <- c({tvs_r})
coldata <- data.frame(
    condition = factor(condition_vals, levels=c({ordered_conditions_r})),
    tissue = factor(tissue_vals, levels=c({tlvl})),
    group = factor(group_vals, levels=c({group_levels_r})){batch_col}
)
rownames(coldata) <- samples
dds_group <- DESeqDataSetFromMatrix(countData, coldata, design=~ {batch_prefix}group)
dds_group <- DESeq(dds_group)
dds_wald <- DESeqDataSetFromMatrix(countData, coldata, design=~ {batch_prefix}tissue * condition)
dds_wald <- DESeq(dds_wald, test="Wald")
dds <- DESeqDataSetFromMatrix(countData, coldata, design=~ {batch_prefix}tissue * condition)
dds <- DESeq(dds, test="LRT", reduced=~ {batch_prefix}tissue + condition)
"""
        else:
            raise ValueError("M4 requires complete genotype or tissue metadata")

        r += f"""
# M4 unsigned interaction result: GO/KEGG eligible, FGSEA ineligible.
res_interaction <- na.omit(results(dds))
res_interaction_df <- as.data.frame(res_interaction)
res_interaction_df$gene_id <- rownames(res_interaction_df)
res_interaction_df <- res_interaction_df[, c("gene_id", setdiff(colnames(res_interaction_df), "gene_id"))]
write.table(res_interaction_df, file="{output_dir}/{inter_factor}_by_condition.interaction_LRT.DE_results.txt",
            sep="\t", quote=FALSE, row.names=FALSE)
sig_interaction <- subset(res_interaction_df, padj < {padj_thresh})
write.table(sig_interaction, file="{output_dir}/{inter_factor}_by_condition.interaction_LRT.DE_genes.txt",
            sep="\t", quote=FALSE, row.names=FALSE)

# Directional interaction coefficients from the same factorial design.
interaction_names <- resultsNames(dds_wald)
interaction_names <- interaction_names[
    grepl("{inter_factor}", interaction_names, fixed=TRUE) &
    grepl("condition", interaction_names, fixed=TRUE)
]
if (length(interaction_names) == 0) {{
    stop("M4 factorial model did not expose an interaction coefficient")
}}
for (coef_name in interaction_names) {{
    safe_coef <- gsub("[^A-Za-z0-9_.-]+", "_", coef_name)
    res_wald_interaction <- na.omit(results(dds_wald, name=coef_name))
    res_wald_df <- as.data.frame(res_wald_interaction)
    res_wald_df$gene_id <- rownames(res_wald_df)
    res_wald_df <- res_wald_df[, c("gene_id", setdiff(colnames(res_wald_df), "gene_id"))]
    res_wald_df$regulation <- ifelse(
        res_wald_df$padj < {padj_thresh} & res_wald_df$log2FoldChange > {log2fc_thresh}, "up",
        ifelse(res_wald_df$padj < {padj_thresh} & res_wald_df$log2FoldChange < -{log2fc_thresh}, "down", "not")
    )
    output_base <- paste0("{inter_factor}_by_condition.interaction_Wald.", safe_coef)
    write.table(res_wald_df, file=file.path("{output_dir}", paste0(output_base, ".DE_results.txt")),
                sep="\t", quote=FALSE, row.names=FALSE)
    sig_wald <- subset(res_wald_df, padj < {padj_thresh} & abs(log2FoldChange) > {log2fc_thresh})
    write.table(sig_wald, file=file.path("{output_dir}", paste0(output_base, ".DE_genes.txt")),
                sep="\t", quote=FALSE, row.names=FALSE)
}}
saveRDS(dds_wald, file="{output_dir}/dds_interaction_wald.rds")
"""

    elif module == "M3":
        pw_factor = "group"
        group_levels_r = ", ".join('"' + value + '"' for value in sorted(set(sample_groups)))
        r += f"""
# M3: factor-stratified group comparison
coldata <- data.frame(group = factor(group_vals, levels=c({group_levels_r})){batch_col})
rownames(coldata) <- samples
dds <- DESeqDataSetFromMatrix(countData, coldata, design=~ {batch_prefix}group)
dds <- DESeq(dds)
"""

    elif module == "M2":
        has_subject = bool(subject_id_map)
        if has_subject:
            sids = [subject_id_map.get(s, f"subject_{i}") for i, s in enumerate(matrix_samples)]
            sids_r = ", ".join('"' + sid + '"' for sid in sids)
            sids_lvl = ", ".join('"' + sid + '"' for sid in sorted(set(sids)))
            r += f"""
# M2: Paired analysis ~ subject_id + condition
subject_vals <- c({sids_r})
coldata <- data.frame(
    condition = factor(condition_vals),
    subject_id = factor(subject_vals, levels=c({sids_lvl})){batch_col}
)
rownames(coldata) <- samples
dds <- DESeqDataSetFromMatrix(countData, coldata, design=~ {batch_prefix}subject_id + condition)
dds <- DESeq(dds)
"""
        else:
            r += f"""
# M2: Paired analysis (no subject_id metadata - falling back to ~condition)
coldata <- data.frame(condition = factor(condition_vals){batch_col})
rownames(coldata) <- samples
dds <- DESeqDataSetFromMatrix(countData, coldata, design=~ {batch_prefix}condition)
dds <- DESeq(dds)
print("[WARNING] M2 mode but no subject_id metadata found. Using ~condition instead.")
"""

    elif module == "M6":
        has_dose = bool(dose_map)
        if has_dose:
            dvs = [float(dose_map.get(s, "0")) for s in matrix_samples]
            dvs_r = ", ".join(str(dv) for dv in dvs)
            r += f"""
# M6: Dose-response ~ dose (continuous)
dose_vals <- c({dvs_r})
coldata <- data.frame(
    condition = factor(condition_vals),
    dose = as.numeric(dose_vals){batch_col}
)
rownames(coldata) <- samples
dds <- DESeqDataSetFromMatrix(countData, coldata, design=~ {batch_prefix}dose)
dds <- DESeq(dds)
res <- results(dds, name="dose")
res <- na.omit(res)
res_df <- as.data.frame(res)
res_df$gene_id <- rownames(res_df)
res_df <- res_df[, c("gene_id", setdiff(colnames(res_df), "gene_id"))]
res_df$regulation <- ifelse(res_df$padj < {padj_thresh} & res_df$log2FoldChange > 0, "up",
                            ifelse(res_df$padj < {padj_thresh} & res_df$log2FoldChange < 0, "down", "not"))
write.table(res_df, file="{output_dir}/dose_response.DE_results.txt", sep="\\t", quote=FALSE, row.names=FALSE)
sig <- subset(res_df, padj < {padj_thresh})
write.table(sig, file="{output_dir}/dose_response.DE_genes.txt", sep="\\t", quote=FALSE, row.names=FALSE)
"""
        else:
            r += f"""
# M6: Dose-response (no dose metadata - falling back to ~condition)
coldata <- data.frame(condition = factor(condition_vals){batch_col})
rownames(coldata) <- samples
dds <- DESeqDataSetFromMatrix(countData, coldata, design=~ {batch_prefix}condition)
dds <- DESeq(dds)
print("[WARNING] M6 mode but no dose metadata found. Using ~condition instead.")
"""

    else:
        # M1 or unknown: standard ~ condition
        r += f"""
# M1: Standard comparison
coldata <- data.frame(condition = factor(condition_vals){batch_col})
rownames(coldata) <- samples
dds <- DESeqDataSetFromMatrix(countData, coldata, design=~ {batch_prefix}condition)
dds <- DESeq(dds)
"""

    # Time-series LRT is an execution feature, not an M5-only module.
    # Keep M1 pairwise Wald results and append the overall categorical
    # time test whenever the planner explicitly enables it.
    if de_plan.get("run_lrt") and module != "M5":
        time_values = [str(sample_times.get(sample, "")).strip() for sample in matrix_samples]
        if any(not value for value in time_values):
            missing_time = [
                sample for sample, value in zip(matrix_samples, time_values) if not value
            ]
            raise ValueError(
                "run_lrt=True but time metadata is missing for samples: "
                + ", ".join(missing_time[:10])
            )
        planned_levels = [str(value) for value in de_plan.get("time_levels", [])]
        if set(time_values) != set(planned_levels):
            planned_levels = sorted(set(time_values), key=parse_time_sort_value)
        if len(planned_levels) < 3:
            raise ValueError("run_lrt=True requires at least 3 distinct time levels")
        time_values_r = ", ".join('"' + value + '"' for value in time_values)
        time_levels_r = ", ".join('"' + value + '"' for value in planned_levels)
        lrt_full = f"~ {batch_prefix}time_factor"
        lrt_reduced = "~ batch" if batch_used else "~ 1"
        r += f"""
# M1 optional categorical time LRT
time_factor <- factor(c({time_values_r}), levels=c({time_levels_r}))
coldata_lrt <- data.frame(time_factor=time_factor{batch_col})
rownames(coldata_lrt) <- samples
dds_lrt <- DESeqDataSetFromMatrix(countData, coldata_lrt, design={lrt_full})
dds_lrt <- DESeq(dds_lrt, test="LRT", reduced={lrt_reduced})
res_lrt <- na.omit(results(dds_lrt))
res_lrt_df <- as.data.frame(res_lrt)
res_lrt_df$gene_id <- rownames(res_lrt_df)
res_lrt_df <- res_lrt_df[, c("gene_id", setdiff(colnames(res_lrt_df), "gene_id"))]
write.table(res_lrt_df, file="{output_dir}/time_lrt.DE_results.txt",
            sep="\\t", quote=FALSE, row.names=FALSE)
sig_lrt <- subset(res_lrt_df, padj < {padj_thresh})
write.table(sig_lrt, file="{output_dir}/time_lrt.DE_genes.txt",
            sep="\\t", quote=FALSE, row.names=FALSE)
saveRDS(dds_lrt, file="{output_dir}/dds_time_lrt.rds")
"""

    # ── Pairwise comparisons (all modules except M6) ──
    if module != "M6":
        for comp in comparisons:
            if len(comp) == 2:
                g1, g2 = comp
                cn = f"{g1}_vs_{g2}"
                # Escape internal quotes
                g1q = str(g1).replace('"', '\"')
                g2q = str(g2).replace('"', '\"')
                r += f"""
# {g1q} vs {g2q}
res <- results({pw_dds}, contrast=c("{pw_factor}", "{g1q}", "{g2q}"))
res <- na.omit(res)
res_df <- as.data.frame(res)
res_df$gene_id <- rownames(res_df)
res_df <- res_df[, c("gene_id", setdiff(colnames(res_df), "gene_id"))]
res_df$regulation <- ifelse(res_df$padj < {padj_thresh} & res_df$log2FoldChange > {log2fc_thresh}, "up",
                            ifelse(res_df$padj < {padj_thresh} & res_df$log2FoldChange < -{log2fc_thresh}, "down", "not"))
write.table(res_df, file="{output_dir}/{cn}.DE_results.txt", sep="\\t", quote=FALSE, row.names=FALSE)
sig <- subset(res_df, padj < {padj_thresh} & abs(log2FoldChange) > {log2fc_thresh})
write.table(sig, file="{output_dir}/{cn}.DE_genes.txt", sep="\\t", quote=FALSE, row.names=FALSE)

pdf("{output_dir}/{cn}_MA_plot.pdf")
plotMA(res, main="{g1q} vs {g2q} MA Plot")
dev.off()
png("{output_dir}/{cn}_MA_plot.png", width=1000, height=800, res=150)
plotMA(res, main="{g1q} vs {g2q} MA Plot")
dev.off()

res_df$significant <- res_df$regulation
res_df$log10padj <- -log10(res_df$padj)
res_df$log10padj[is.infinite(res_df$log10padj)] <- max(
    res_df$log10padj[is.finite(res_df$log10padj)], na.rm=TRUE)

pdf("{output_dir}/{cn}_volcano_plot.pdf")
ggplot(res_df, aes(x=log2FoldChange, y=log10padj, color=significant)) +
  geom_point(alpha=0.5) +
  scale_color_manual(values=c(not="grey", up="red", down="blue")) +
  ggtitle("{g1q} vs {g2q} Volcano Plot") + theme_minimal()
dev.off()
png("{output_dir}/{cn}_volcano_plot.png", width=1000, height=800, res=150)
ggplot(res_df, aes(x=log2FoldChange, y=log10padj, color=significant)) +
  geom_point(alpha=0.5) +
  scale_color_manual(values=c(not="grey", up="red", down="blue")) +
  ggtitle("{g1q} vs {g2q} Volcano Plot") + theme_minimal()
dev.off()
"""

    r += f"""
saveRDS(dds, file="{output_dir}/dds.rds")
cat("DESeq2 modular analysis complete.\n")
sessionInfo()
"""
    return r

def run_deseq2(
    count_matrix: Path,
    config: Path,
    output_dir: Path,
    padj_thresh: float = 0.05,
    log2fc_thresh: float = 1.0,
    user_module: str = "",
) -> Tuple[Dict[str, bool], Dict[str, Any]]:
    """运行DESeq2差异分析（通过R脚本）"""
    results = {}
    
    # 读取配置获取比较组
    with open(config, 'r') as f:
        cfg = yaml.safe_load(f) or {}
    
    # 支持两种格式: groups (新格式) 或 samples (旧格式)
    groups = load_groups_from_config(cfg)
    
    # 按 count matrix 的列顺序对齐分组，避免 YAML 顺序与矩阵列顺序不一致。
    with open(count_matrix, 'r') as f:
        matrix_header = f.readline().strip().split('\t')
    matrix_samples = matrix_header[1:]
    de_plan: Dict[str, Any] = {
        "strategy": "auto",
        "comparisons": [],
        "replicate_counts": {},
        "replicate_policy": {},
        "replicate_violations": [],
        "active_samples": [],
        "ignored_samples": [],
        "sample_times": {},
        "group_times": {},
        "time_levels": [],
        "run_lrt": False,
        "run_spline_lrt": False,
        "spline_df": 3,
    }

    try:
        de_plan = build_de_plan(cfg, matrix_samples, groups, user_module=user_module)
    except ValueError as exc:
        print(f"DESeq2 configuration error: {exc}", file=sys.stderr)
        for comp in cfg.get("compare", []):
            if len(comp) == 2:
                results[f"{comp[0]}_vs_{comp[1]}"] = False
        return results, de_plan
    groups = de_plan.get("groups") or groups
    active_samples = list(de_plan.get("active_samples") or matrix_samples)
    missing_groups = [s for s in active_samples if s not in groups]
    if missing_groups:
        print(
            "DESeq2 configuration is missing group labels for active samples: "
            + ", ".join(missing_groups),
            file=sys.stderr,
        )
        for comp in cfg.get("compare", []):
            if len(comp) == 2:
                results[f"{comp[0]}_vs_{comp[1]}"] = False
        return results, de_plan
    matrix_samples = active_samples

    comparisons = de_plan["comparisons"]
    result_names = [comparison_name(comp[0], comp[1]) for comp in comparisons]
    if de_plan.get("module") == "M4":
        result_names.append(f"{de_plan.get('advanced_factor')}_by_condition.interaction_LRT")
    if de_plan.get("module") == "M6":
        result_names.append("dose_response")
    if de_plan.get("run_lrt"):
        result_names.append("time_lrt")
    if de_plan.get("run_spline_lrt"):
        result_names.append("time_spline_lrt")

    if not result_names:
        print("DESeq2 configuration has no pairwise comparisons or LRT analyses enabled.", file=sys.stderr)
        return {}, de_plan

    all_zero_counts = True
    total_genes = 0
    with open(count_matrix, 'r') as f:
        next(f, None)
        for line in f:
            if not line.strip():
                continue
            total_genes += 1
            values = line.rstrip("\n").split('\t')[1:]
            for value in values:
                try:
                    if float(value) != 0:
                        all_zero_counts = False
                        break
                except ValueError:
                    all_zero_counts = False
                    break
            if not all_zero_counts:
                break

    if all_zero_counts:
        no_signal_message = (
            f"Count matrix has {total_genes} genes but all sample counts are 0. "
            "DESeq2 cannot estimate size factors or dispersion; empty DE result files were written."
        )
        print(f"Warning: {no_signal_message}", file=sys.stderr)
        (output_dir / "NO_EXPRESSION_SIGNAL.txt").write_text(no_signal_message + "\n")
        empty_header = "gene_id\tbaseMean\tlog2FoldChange\tlfcSE\tstat\tpvalue\tpadj\tregulation\n"
        for comp_name in result_names:
            (output_dir / f"{comp_name}.DE_results.txt").write_text(empty_header)
            (output_dir / f"{comp_name}.DE_genes.txt").write_text(empty_header)
            results[comp_name] = True
        return results, de_plan

    # 生成R脚本
    r_script = output_dir / "run_deseq2.R"
    
    # 构建样本条件映射（用于R代码）
    sample_conditions = []
    for s in matrix_samples:
        cond = groups.get(s, "unknown")
        sample_conditions.append(cond)

    sample_time_values = [de_plan["sample_times"].get(s) for s in matrix_samples]
    time_factor_levels = de_plan.get("time_levels", [])
    group_times = de_plan.get("group_times", {})
    time_factor_values = [groups[s] for s in matrix_samples]
    numeric_time_values = [
        parse_time_sort_value(group_times.get(groups[s]))[1]
        if groups[s] in group_times else 0
        for s in matrix_samples
    ]
    
    # v2: use modular R script when module is detected
    module = de_plan.get("module", "")
    use_modular = bool(module in {"M1","M2","M3","M4","M5","M6"})
    if use_modular:
        print(f"[INFO] Using modular R script for module {module}: {de_plan.get('design_formula', '')}")
        r_code = generate_modular_r_script(
            count_matrix, matrix_samples, de_plan,
            output_dir, padj_thresh, log2fc_thresh
        )
    else:
        r_code = f"""
    library(DESeq2)
    library(ggplot2)
    library(ggrepel)
    library(splines)

    # 读取计数矩阵
    countData <- read.table({r_string(count_matrix)}, header=TRUE, row.names=1, sep="\\t",
                            check.names=FALSE, quote="", comment.char="")
    featurecounts_meta_cols <- c("Chr", "Start", "End", "Strand", "Length")
    if (all(featurecounts_meta_cols %in% colnames(countData))) {{
      countData <- countData[, setdiff(colnames(countData), featurecounts_meta_cols), drop=FALSE]
      colnames(countData) <- sub("\\\\.sorted$", "", tools::file_path_sans_ext(basename(colnames(countData))))
    }}
    countData[] <- lapply(countData, function(x) as.numeric(as.character(x)))
    if (anyNA(countData)) {{
      bad_cols <- colnames(countData)[vapply(countData, function(x) any(is.na(x)), logical(1))]
      stop(paste("Count matrix contains non-numeric columns:", paste(bad_cols, collapse=", ")))
    }}
    countData <- round(as.matrix(countData))
    active_samples <- {r_vector(matrix_samples)}
    missing_active_samples <- setdiff(active_samples, colnames(countData))
    if (length(missing_active_samples) > 0) {{
      stop(paste("Count matrix is missing active samples:", paste(missing_active_samples, collapse=", ")))
    }}
    countData <- countData[, active_samples, drop=FALSE]

    # 读取分组信息
    samples <- colnames(countData)
    conditions <- {r_vector(sample_conditions)}
    coldata <- data.frame(condition=factor(conditions))
    row.names(coldata) <- samples

    # 创建DESeq2对象
    dds <- DESeqDataSetFromMatrix(countData, coldata, design=~condition)

    # 运行DESeq2
    dds <- DESeq(dds)

    # 获取比较结果
    """
        for comp in comparisons:
            if len(comp) == 2:
                g1, g2 = comp
                comp_name = comparison_name(g1, g2)
                r_code += f"""
    # {g1} vs {g2}
    res <- results(dds, contrast=c("condition", {r_string(g1)}, {r_string(g2)}))
    res <- na.omit(res)
    res_df <- as.data.frame(res)
    res_df$gene_id <- rownames(res_df)
    res_df <- res_df[, c("gene_id", setdiff(colnames(res_df), "gene_id"))]
    res_df$regulation <- ifelse(res_df$padj < {padj_thresh} & res_df$log2FoldChange > {log2fc_thresh}, "up",
                                ifelse(res_df$padj < {padj_thresh} & res_df$log2FoldChange < -{log2fc_thresh}, "down", "not"))

    # 保存完整结果
    write.table(res_df, 
                file={r_string(output_dir / f"{comp_name}.DE_results.txt")},
                sep="\\t", quote=FALSE, row.names=FALSE)

    # 筛选显著基因
    sig_up <- subset(res_df, padj < {padj_thresh} & log2FoldChange > {log2fc_thresh})
    sig_down <- subset(res_df, padj < {padj_thresh} & log2FoldChange < -{log2fc_thresh})

    # 保存显著基因
    sig_genes <- rbind(sig_up, sig_down)
    write.table(sig_genes,
                file={r_string(output_dir / f"{comp_name}.DE_genes.txt")},
                sep="\\t", quote=FALSE, row.names=FALSE)

    # MA plot (PDF)
    pdf({r_string(output_dir / f"{comp_name}_MA_plot.pdf")})
    plotMA(res, main="{g1} vs {g2} MA Plot")
    dev.off()

    # MA plot (PNG)
    png({r_string(output_dir / f"{comp_name}_MA_plot.png")}, width=1000, height=800, res=150)
    plotMA(res, main="{g1} vs {g2} MA Plot")
    dev.off()

    # Volcano plot
    res_df$significant <- res_df$regulation

    # Volcano plot (PDF)
    pdf({r_string(output_dir / f"{comp_name}_volcano_plot.pdf")})
    ggplot(res_df, aes(x=log2FoldChange, y=-log10(padj), color=significant)) +
      geom_point(alpha=0.5) +
      scale_color_manual(values=c("not"="gray", "up"="red", "down"="blue")) +
      ggtitle("{g1} vs {g2} Volcano Plot") +
      theme_minimal()
    dev.off()

    # Volcano plot (PNG)
    png({r_string(output_dir / f"{comp_name}_volcano_plot.png")}, width=1000, height=800, res=150)
    ggplot(res_df, aes(x=log2FoldChange, y=-log10(padj), color=significant)) +
      geom_point(alpha=0.5) +
      scale_color_manual(values=c("not"="gray", "up"="red", "down"="blue")) +
      ggtitle("{g1} vs {g2} Volcano Plot") +
      theme_minimal()
    dev.off()
    """

        if de_plan.get("run_lrt"):
            r_code += f"""
    # Time-series categorical LRT
    time_factor <- factor({r_vector(time_factor_values)}, levels={r_vector(time_factor_levels)})
    coldata_lrt <- data.frame(time_factor=time_factor)
    row.names(coldata_lrt) <- samples
    dds_lrt <- DESeqDataSetFromMatrix(countData, coldata_lrt, design=~time_factor)
    dds_lrt <- DESeq(dds_lrt, test="LRT", reduced=~1)
    res_lrt <- results(dds_lrt)
    res_lrt <- na.omit(res_lrt)
    res_lrt_df <- as.data.frame(res_lrt)
    res_lrt_df$gene_id <- rownames(res_lrt_df)
    res_lrt_df <- res_lrt_df[, c("gene_id", setdiff(colnames(res_lrt_df), "gene_id"))]
    write.table(res_lrt_df,
                file={r_string(output_dir / "time_lrt.DE_results.txt")},
                sep="\\t", quote=FALSE, row.names=FALSE)
    sig_lrt <- subset(res_lrt_df, padj < {padj_thresh})
    write.table(sig_lrt,
                file={r_string(output_dir / "time_lrt.DE_genes.txt")},
                sep="\\t", quote=FALSE, row.names=FALSE)
    saveRDS(dds_lrt, file={r_string(output_dir / "dds_time_lrt.rds")})
    """

        if de_plan.get("run_spline_lrt"):
            r_code += f"""
    # Time-series spline LRT
    time_numeric <- c({', '.join(str(value) for value in numeric_time_values)})
    coldata_spline <- data.frame(time_numeric=time_numeric)
    row.names(coldata_spline) <- samples
    dds_spline <- DESeqDataSetFromMatrix(countData, coldata_spline, design=~ns(time_numeric, df={de_plan.get("spline_df", 3)}))
    dds_spline <- DESeq(dds_spline, test="LRT", reduced=~1)
    res_spline <- results(dds_spline)
    res_spline <- na.omit(res_spline)
    res_spline_df <- as.data.frame(res_spline)
    res_spline_df$gene_id <- rownames(res_spline_df)
    res_spline_df <- res_spline_df[, c("gene_id", setdiff(colnames(res_spline_df), "gene_id"))]
    write.table(res_spline_df,
                file={r_string(output_dir / "time_spline_lrt.DE_results.txt")},
                sep="\\t", quote=FALSE, row.names=FALSE)
    sig_spline <- subset(res_spline_df, padj < {padj_thresh})
    write.table(sig_spline,
                file={r_string(output_dir / "time_spline_lrt.DE_genes.txt")},
                sep="\\t", quote=FALSE, row.names=FALSE)
    saveRDS(dds_spline, file={r_string(output_dir / "dds_time_spline_lrt.rds")})
    """
    
        r_code += f'\n# Save RDS\nsaveRDS(dds, file={r_string(output_dir / "dds.rds")})\n'
    
    with open(r_script, 'w') as f:
        f.write(r_code)

    def validate_planned_outputs() -> Dict[str, bool]:
        status: Dict[str, bool] = {}
        for comp_name in result_names:
            expected = [
                output_dir / f"{comp_name}.DE_results.txt",
                output_dir / f"{comp_name}.DE_genes.txt",
            ]
            status[comp_name] = all(path.exists() and path.is_file() for path in expected)
        return status
    
    # Batch is optional.  When an opted-in batch model fails, retry exactly
    # once with the historical no-batch model and keep an explicit warning.
    try:
        result = subprocess.run(
            [f"{RNASEQ_BIN}/Rscript", str(r_script)],
            check=True,
            capture_output=True,
            text=True,
            timeout=3600  # 1小时超时
        )
        results.update(validate_planned_outputs())
        missing_outputs = [name for name, ok in results.items() if not ok]
        if missing_outputs:
            print(
                "DESeq2 completed but planned result files are missing: "
                + ", ".join(missing_outputs),
                file=sys.stderr,
            )
    except subprocess.CalledProcessError as e:
        if de_plan.get("batch_used") and use_modular:
            print(
                "[BATCH WARNING] 批次校正模型执行失败，正在自动改用无批次模型重试。",
                file=sys.stderr,
            )
            for pattern in (
                "*.DE_results.txt", "*.DE_genes.txt", "*_MA_plot.*",
                "*_volcano_plot.*", "dds*.rds",
            ):
                for partial in output_dir.glob(pattern):
                    partial.unlink(missing_ok=True)

            def remove_batch_term(formula):
                return str(formula or "").replace("batch + ", "")

            de_plan["batch_used"] = False
            de_plan["batch_fallback"] = True
            de_plan["batch_fallback_reason"] = "批次校正模型执行失败，已自动改用无批次模型完成分析。"
            de_plan["batch_error"] = (e.stderr or e.stdout or "batch model failed")[-2000:]
            de_plan["design_formula"] = remove_batch_term(de_plan.get("design_formula"))
            de_plan["interaction_full"] = remove_batch_term(de_plan.get("interaction_full"))
            de_plan["interaction_reduced"] = remove_batch_term(de_plan.get("interaction_reduced"))
            de_plan.setdefault("design_warnings", []).append({
                "code": "batch_runtime_fallback",
                "message": de_plan["batch_fallback_reason"],
            })
            (output_dir / "BATCH_FALLBACK.txt").write_text(
                de_plan["batch_fallback_reason"] + "\n",
                encoding="utf-8",
            )
            r_script.write_text(
                generate_modular_r_script(
                    count_matrix, matrix_samples, de_plan,
                    output_dir, padj_thresh, log2fc_thresh,
                ),
                encoding="utf-8",
            )
            try:
                subprocess.run(
                    [f"{RNASEQ_BIN}/Rscript", str(r_script)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=3600,
                )
                print("[BATCH WARNING] 已使用无批次模型完成差异分析。", file=sys.stderr)
                results.update(validate_planned_outputs())
                missing_outputs = [name for name, ok in results.items() if not ok]
                if missing_outputs:
                    print(
                        "DESeq2 batch fallback completed but planned result files are missing: "
                        + ", ".join(missing_outputs),
                        file=sys.stderr,
                    )
            except subprocess.CalledProcessError as retry_error:
                print(f"DESeq2 failed after batch fallback: {retry_error.stderr}", file=sys.stderr)
                for comp_name in result_names:
                    results[comp_name] = False
        else:
            print(f"DESeq2 failed: {e.stderr}", file=sys.stderr)
            for comp_name in result_names:
                results[comp_name] = False
    
    return results, de_plan


def generate_report(
    output_dir: Path,
    comparison_stats: Dict[str, Dict[str, Any]],
    versions: Dict[str, str],
    start_time: datetime,
    end_time: datetime,
    prefix: str = "",
    de_plan: Optional[Dict[str, Any]] = None
) -> Path:
    """生成差异表达报告"""
    safe_prefix = prefix if prefix else "rnaseq"
    report_file = output_dir / f"{safe_prefix}.rnaseq_de_report.md"
    
    elapsed = (end_time - start_time).total_seconds()
    
    content = f"""# RNA-Seq Differential Expression Report

## Summary

- **Analysis Date**: {end_time.strftime("%Y-%m-%d %H:%M:%S")}
- **Total Comparisons**: {len(comparison_stats)}
- **Total Runtime**: {elapsed:.1f} seconds

## Tool Versions

"""
    for tool, version in versions.items():
        content += f"- **{tool}**: {version}\n"

    if de_plan:
        content += "\n## DE Design\n\n"
        content += f"- **Strategy**: {de_plan.get('strategy', 'manual')}\n"
        content += f"- **Replicate counts**: {de_plan.get('replicate_counts', {})}\n"
        content += f"- **Pairwise comparisons**: {de_plan.get('comparisons', [])}\n"
        if de_plan.get("batch_requested"):
            batch_state = "used" if de_plan.get("batch_used") else "fallback to no-batch"
            content += f"- **Batch adjustment**: {batch_state}\n"
            if de_plan.get("batch_fallback_reason"):
                content += f"- **Batch note**: {de_plan.get('batch_fallback_reason')}\n"
        content += f"- **Categorical time LRT**: {de_plan.get('run_lrt', False)}\n"
        content += f"- **Spline time LRT**: {de_plan.get('run_spline_lrt', False)}\n"
    
    content += "\n## Comparison Results\n\n"
    content += "| Comparison | Test | Up-regulated | Down-regulated | Total DEGs |\n"
    content += "|------------|------|--------------|----------------|------------|\n"
    
    for comp, stats in comparison_stats.items():
        content += f"| {comp} | {stats.get('test', 'Wald')} | {stats.get('up', 0)} | {stats.get('down', 0)} | {stats.get('total', 0)} |\n"

    notes = [stats.get("note") for stats in comparison_stats.values() if stats.get("note")]
    if notes:
        content += "\n## Notes\n\n"
        for note in sorted(set(notes)):
            content += f"- {note}\n"
    
    with open(report_file, 'w') as f:
        f.write(content)
    
    return report_file


def generate_manifest(
    output_dir: Path,
    comparison_stats: Dict[str, Dict[str, Any]],
    versions: Dict[str, str],
    start_time: datetime,
    end_time: datetime,
    cmd: List[str],
    input_dir: Path,
    workspace_dir: Path,
    job_id: str,
    run_id: str,
    prefix: str,
    family_stats: Optional[Dict[str, Any]] = None,
    family_name: Optional[str] = None,
    de_plan: Optional[Dict[str, Any]] = None,
    sample_config: Optional[Path] = None,
    count_matrix: Optional[Path] = None,
    family_members_file: Optional[Path] = None,
    foreground_mode: str = "global",
    cpu: int = 4,
    padj_threshold: float = 0.05,
    log2fc_threshold: float = 1.0,
) -> Path:
    """生成manifest文件"""
    safe_prefix = prefix if prefix else "rnaseq"
    manifest_file = output_dir / f"{safe_prefix}.rnaseq_de.manifest.json"
    job_workspace = workspace_dir.parent if workspace_dir.name == "rnaseq_de" else workspace_dir
    
    total_de = sum(s.get('total', 0) for s in comparison_stats.values())
    total_up = sum(s.get('up', 0) for s in comparison_stats.values())
    total_down = sum(s.get('down', 0) for s in comparison_stats.values())
    
    manifest = {
        "manifest_version": "1.0",
        "stage": "rnaseq_de",
        "status": "success",
        "job_id": job_id,
        "run_id": run_id,
        "timestamp": end_time.isoformat(),
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "elapsed_sec": (end_time - start_time).total_seconds(),
        "cmd": cmd,
        "workspace_root": abs_text(job_workspace.parent),
        "workspace": abs_text(job_workspace),
        "input_dir": abs_text(job_workspace.parent.parent / "input" / job_id),
        "metrics": {
            "total_comparisons": len(comparison_stats),
            "de_genes_up": total_up,
            "de_genes_down": total_down,
            "de_genes_total": total_de,
            "comparisons": comparison_stats,
            "family_analysis": family_stats if family_stats else None,
            "replicate_counts": de_plan.get("replicate_counts", {}) if de_plan else {},
            "replicate_violations": de_plan.get("replicate_violations", []) if de_plan else [],
            "time_levels": de_plan.get("time_levels", []) if de_plan else [],
            "effective_control_group": de_plan.get("effective_control_group", "") if de_plan else "",
            "comparison_mode": de_plan.get("comparison_mode", "") if de_plan else "",
            "planned_comparisons": de_plan.get("comparisons", []) if de_plan else [],
            "de_module": de_plan.get("module", "") if de_plan else "",
            "advanced_factor": de_plan.get("advanced_factor", "") if de_plan else "",
            "fallback_from": de_plan.get("fallback_from", "") if de_plan else "",
            "fallback_reason": de_plan.get("fallback_reason", "") if de_plan else "",
            "skipped_comparisons": de_plan.get("skipped_comparisons", []) if de_plan else [],
            "comparison_axis": de_plan.get("comparison_axis", "") if de_plan else "",
            "comparison_axis_source": de_plan.get("comparison_axis_source", "") if de_plan else "",
            "stratify_axis": de_plan.get("stratify_axis", "") if de_plan else "",
            "reference_condition": de_plan.get("reference_condition", "") if de_plan else "",
            "reference_factor": de_plan.get("reference_factor", "") if de_plan else "",
            "condition_reference_source": de_plan.get("condition_reference_source", "") if de_plan else "",
            "factor_reference_source": de_plan.get("factor_reference_source", "") if de_plan else "",
            "design_warnings": de_plan.get("design_warnings", []) if de_plan else [],
            "batch_requested": bool(de_plan.get("batch_requested")) if de_plan else False,
            "batch_adjustment": bool(de_plan.get("batch_used")) if de_plan else False,
            "batch_levels": list(de_plan.get("batch_levels") or []) if de_plan else [],
            "batch_fallback": bool(de_plan.get("batch_fallback")) if de_plan else False,
            "batch_fallback_reason": de_plan.get("batch_fallback_reason", "") if de_plan else "",
            "batch_policy": (
                "used"
                if de_plan and de_plan.get("batch_used")
                else "fallback_without_batch"
                if de_plan and de_plan.get("batch_requested")
                else "not_requested"
            ),
        },
        "parameters": {
            "cpu": cpu,
            "prefix": prefix,
            "count_matrix": abs_text(count_matrix),
            "sample_config": abs_text(sample_config),
            "analysis_dir": abs_text(workspace_dir / "analysis"),
            "final_dir": abs_text(output_dir),
            "padj_threshold": padj_threshold,
            "log2fc_threshold": log2fc_threshold,
            "family_analysis": family_name is not None,
            "family_name": family_name,
            "family_members_file": abs_text(family_members_file) if family_members_file else "",
            "de_plan": de_plan if de_plan else {}
        },
        "artifacts": {},
        "tools": versions
    }
    sample_table = output_dir.parent / "rnaseq_sample_qc" / "sample_table.tsv"
    context = build_rnaseq_context(
        prefix=prefix,
        sample_config=sample_config,
        count_matrix=count_matrix,
        sample_table=sample_table if sample_table.exists() else None,
        foreground_mode=foreground_mode,
        family_members_file=family_members_file,
        family_label=family_name or "",
        stage="rnaseq_de",
    )
    if de_plan and de_plan.get("replicate_counts"):
        normalized_groups = list(de_plan["replicate_counts"].keys())
        normalized_signature = "_".join(re.sub(r"[^A-Za-z0-9_.-]+", "_", str(g)).strip("_") for g in normalized_groups[:8]) or "nogroups"
        context["rnaseq_condition_count"] = len(normalized_groups)
        context["rnaseq_condition_signature"] = normalized_signature
    if de_plan and de_plan.get("effective_control_group"):
        context["rnaseq_control_condition"] = de_plan["effective_control_group"]
    expression_manifest = output_dir / f"{safe_prefix}.expression.manifest.json"
    manifest["time_series"] = bool((de_plan or {}).get("run_lrt") or (de_plan or {}).get("run_spline_lrt"))
    manifest["triggered_expression"] = expression_manifest.exists()
    if expression_manifest.exists():
        manifest["triggered_expression_manifest"] = f"final/{expression_manifest.name}"
    apply_rnaseq_context(manifest, context)
    
    # 添加artifacts（参考rnaseq_qc契约，包含完整字段）
    def add_artifact(name: str, filepath: Path, artifact_type: str, **extra):
        """添加 artifact 到 manifest"""
        if filepath.exists():
            manifest["artifacts"][name] = {
                "artifact": name,
                "path": f"final/{filepath.name}",
                "full_path": str(filepath.absolute()),
                "type": artifact_type,
                "size": filepath.stat().st_size,
                "sha256": compute_file_hash(filepath),
                **extra
            }
    
    for comp in comparison_stats.keys():
        # DE 结果文件
        de_results = output_dir / f"{safe_prefix}.rnaseq_de.{comp}.DE_results.txt"
        add_artifact(f"de_results_{comp}", de_results, "tsv", format="tsv")
        
        # 显著基因文件
        de_genes = output_dir / f"{safe_prefix}.rnaseq_de.{comp}.DE_genes.txt"
        add_artifact(f"de_genes_{comp}", de_genes, "tsv", format="tsv")
        
        # MA plot (PDF + PNG)
        ma_pdf = output_dir / f"{safe_prefix}.rnaseq_de.{comp}_MA_plot.pdf"
        ma_png = output_dir / f"{safe_prefix}.rnaseq_de.{comp}_MA_plot.png"
        add_artifact(f"ma_plot_pdf_{comp}", ma_pdf, "pdf", format="pdf")
        add_artifact(f"ma_plot_png_{comp}", ma_png, "image", format="png")
        
        # Volcano plot (PDF + PNG)
        volcano_pdf = output_dir / f"{safe_prefix}.rnaseq_de.{comp}_volcano_plot.pdf"
        volcano_png = output_dir / f"{safe_prefix}.rnaseq_de.{comp}_volcano_plot.png"
        add_artifact(f"volcano_plot_pdf_{comp}", volcano_pdf, "pdf", format="pdf")
        add_artifact(f"volcano_plot_png_{comp}", volcano_png, "image", format="png")
    
    # 报告
    report_name = f"{prefix}.rnaseq_de_report.md" if prefix else "rnaseq_de_report.md"
    report_file = output_dir / report_name
    add_artifact("report", report_file, "markdown", format="md")

    no_signal_file = output_dir / f"{safe_prefix}.rnaseq_de.NO_EXPRESSION_SIGNAL.txt"
    add_artifact("no_expression_signal", no_signal_file, "text", format="txt")
    
    # 添加家族分析结果文件（如果存在）
    if family_name:
        for comp in comparison_stats.keys():
            family_results = output_dir / f"{safe_prefix}.rnaseq_de.{family_name}_{comp}.family_DE_results.txt"
            family_de_genes = output_dir / f"{safe_prefix}.rnaseq_de.{family_name}_{comp}.family_DE_genes.txt"
            add_artifact(f"family_results_{comp}", family_results, "tsv", format="tsv")
            add_artifact(f"family_de_genes_{comp}", family_de_genes, "tsv", format="tsv")
    
    # manifest 自身
    manifest["artifacts"]["manifest"] = {
        "artifact": "manifest",
        "path": f"final/{manifest_file.name}",
        "full_path": str(manifest_file.absolute()),
        "type": "json",
        "format": "json"
    }
    
    with open(manifest_file, 'w') as f:
        json.dump(manifest, f, indent=2)
    
    return manifest_file


def parse_de_results(de_file: Path) -> Dict[str, Any]:
    """解析差异表达结果文件"""
    result_name = de_file.stem.replace(".DE_genes", "")
    is_lrt = result_name in {"time_lrt", "time_spline_lrt"} or ".interaction_LRT" in result_name
    stats: Dict[str, Any] = {"up": 0, "down": 0, "total": 0, "test": "LRT" if is_lrt else "Wald"}
    
    if not de_file.exists():
        return stats
    
    with open(de_file, 'r') as f:
        lines = f.readlines()
    
    # 跳过header
    for line in lines[1:]:
        if not line.strip():
            continue
        stats["total"] += 1
        if is_lrt:
            continue
        parts = line.strip().split('\t')
        if len(parts) >= 3:
            try:
                log2fc = float(parts[2])
                if log2fc > 0:
                    stats["up"] += 1
                else:
                    stats["down"] += 1
            except (ValueError, IndexError):
                pass
    
    return stats


def cleanup_previous_outputs(de_dir: Path, final_dir: Path, safe_prefix: str) -> None:
    """Remove stale rnaseq_de outputs before a rerun so manifests reflect this run only."""
    for pattern in (
        "*.DE_results.txt",
        "*.DE_genes.txt",
        "*_MA_plot.pdf",
        "*_MA_plot.png",
        "*_volcano_plot.pdf",
        "*_volcano_plot.png",
        "dds*.rds",
        "NO_EXPRESSION_SIGNAL.txt",
    ):
        for path in de_dir.glob(pattern):
            if path.is_file():
                path.unlink()

    for pattern in (
        f"{safe_prefix}.rnaseq_de.*",
        f"{safe_prefix}.rnaseq_de_report.md",
    ):
        for path in final_dir.glob(pattern):
            if path.is_file():
                path.unlink()


def main():
    parser = argparse.ArgumentParser(description="RNA-Seq Differential Expression")
    parser.add_argument("-i", "--input", required=True, help="Input directory with count matrix")
    parser.add_argument("-c", "--config", required=True, help="Group/sample configuration (YAML)")
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    parser.add_argument("-p", "--prefix", default="rnaseq", help="Output prefix")
    parser.add_argument("-j", "--job-id", default="unknown", help="Job ID")
    parser.add_argument("-r", "--run-id", default="unknown", help="Run ID")
    parser.add_argument("-t", "--cpu", type=int, default=4, help="Number of CPU threads")
    parser.add_argument("--module", default="", choices=["", "M1", "M2", "M3", "M4"],
                        help="Simplified module type (M1/M3/M4). Empty = auto-detect.")
    parser.add_argument("--padj", type=float, default=0.05, help="P-adj threshold")
    parser.add_argument("--log2fc", type=float, default=1.0, help="log2FC threshold")
    parser.add_argument("-f", "--family-ids", help="Gene family member IDs file (optional)")
    parser.add_argument("-n", "--family-name", default="family", help="Family name for output files")
    parser.add_argument("--final-dir", help="Main final directory (default: output_dir/final)")
    
    args = parser.parse_args()
    
    start_time = datetime.now()
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    config_file = Path(args.config)
    context_for_prefix = resolve_rnaseq_project_context(
        input_dir=config_file.parent,
        final_dir=input_dir,
        requested_prefix=args.prefix,
        sample_config=config_file,
    )
    resolved_prefix = context_for_prefix.get("output_prefix")
    if resolved_prefix and resolved_prefix != args.prefix:
        print(
            f"[INFO] Using RNA-seq output prefix {resolved_prefix} "
            f"instead of requested {args.prefix or '<empty>'} "
            f"(source={context_for_prefix.get('source', '')})"
        )
        args.prefix = resolved_prefix
    
    # 创建输出目录
    output_dir.mkdir(parents=True, exist_ok=True)
    # final_dir: 主final目录（所有阶段共享），默认是output_dir/final
    # 如果指定了--final-dir，使用那个；否则使用output_dir/final
    print(f"[DEBUG] args.final_dir = {args.final_dir}")
    if args.final_dir:
        final_dir = Path(args.final_dir)
        final_dir.mkdir(parents=True, exist_ok=True)
        print(f"[DEBUG] Using provided final_dir: {final_dir}")
    else:
        final_dir = output_dir / "final"
        final_dir.mkdir(exist_ok=True)
        print(f"[DEBUG] Using default final_dir: {final_dir}")
    de_dir = output_dir / "analysis"
    de_dir.mkdir(exist_ok=True)
    safe_prefix = args.prefix if args.prefix else "rnaseq"
    cleanup_previous_outputs(de_dir, final_dir, safe_prefix)
    
    # 查找计数矩阵
    count_matrix = None
    for f in input_dir.glob("*.count_matrix.txt"):
        count_matrix = f
        break
    
    if not count_matrix:
        print("Error: Count matrix not found", file=sys.stderr)
        sys.exit(1)
    
    print(f"Processing differential expression analysis...")
    
    # 获取工具版本
    versions = get_tool_versions()
    print(f"Tool versions: {versions}")
    
    # 运行DESeq2。这里必须把 R 层失败显式传回上层；
    # 否则会生成 success manifest，但没有任何 DE 结果文件。
    deseq_status, de_plan = run_deseq2(count_matrix, config_file, de_dir, args.padj, args.log2fc, user_module=args.module)
    if not deseq_status:
        print("Error: No DESeq2 comparisons were configured or executed.", file=sys.stderr)
        sys.exit(1)
    failed_comparisons = [name for name, ok in deseq_status.items() if not ok]
    if failed_comparisons:
        print(
            "Error: DESeq2 failed for comparisons: " + ", ".join(failed_comparisons),
            file=sys.stderr,
        )
        sys.exit(1)
    
    # 解析结果统计
    comparison_stats = {}
    no_signal_marker = de_dir / "NO_EXPRESSION_SIGNAL.txt"
    no_signal_note = no_signal_marker.read_text().strip() if no_signal_marker.exists() else None
    for f in de_dir.glob("*.DE_genes.txt"):
        comp_name = f.stem.replace(".DE_genes", "")
        stats = parse_de_results(f)
        if no_signal_note:
            stats["note"] = no_signal_note
        comparison_stats[comp_name] = stats
        
        # 复制到final目录，使用标准命名格式: {prefix}.rnaseq_de.{comp_name}.xxx
        import shutil
        results_file = de_dir / f"{comp_name}.DE_results.txt"
        if results_file.exists():
            shutil.copy(results_file, final_dir / f"{safe_prefix}.rnaseq_de.{comp_name}.DE_results.txt")
        shutil.copy(f, final_dir / f"{safe_prefix}.rnaseq_de.{comp_name}.DE_genes.txt")
        
        # 复制图表文件到final目录
        ma_pdf = de_dir / f"{comp_name}_MA_plot.pdf"
        ma_png = de_dir / f"{comp_name}_MA_plot.png"
        volcano_pdf = de_dir / f"{comp_name}_volcano_plot.pdf"
        volcano_png = de_dir / f"{comp_name}_volcano_plot.png"
        
        if ma_pdf.exists():
            shutil.copy(ma_pdf, final_dir / f"{safe_prefix}.rnaseq_de.{comp_name}_MA_plot.pdf")
        if ma_png.exists():
            shutil.copy(ma_png, final_dir / f"{safe_prefix}.rnaseq_de.{comp_name}_MA_plot.png")
        if volcano_pdf.exists():
            shutil.copy(volcano_pdf, final_dir / f"{safe_prefix}.rnaseq_de.{comp_name}_volcano_plot.pdf")
        if volcano_png.exists():
            shutil.copy(volcano_png, final_dir / f"{safe_prefix}.rnaseq_de.{comp_name}_volcano_plot.png")

    if no_signal_marker.exists():
        import shutil
        shutil.copy(no_signal_marker, final_dir / f"{safe_prefix}.rnaseq_de.NO_EXPRESSION_SIGNAL.txt")
    
    # 基因家族特异性分析（如果提供了家族ID文件）
    family_stats = {}
    if args.family_ids:
        family_ids_file = Path(args.family_ids)
        if family_ids_file.exists():
            print(f"Processing family-specific DE analysis for {args.family_name}...")
            family_stats = run_family_de_analysis(
                de_dir, family_ids_file, final_dir, 
                args.family_name, args.padj, args.log2fc, args.prefix
            )
        else:
            print(f"Warning: Family IDs file not found: {family_ids_file}")
    
    # 生成报告
    end_time = datetime.now()
    generate_report(final_dir, comparison_stats, versions, start_time, end_time, args.prefix, de_plan)
    
    # 生成manifest
    cmd = ["python", __file__, "-i", str(input_dir), "-c", str(config_file),
           "-o", str(output_dir), "-p", args.prefix, "-j", args.job_id, "-r", args.run_id,
           "--padj", str(args.padj), "--log2fc", str(args.log2fc)]
    if args.family_ids:
        cmd.extend(["-f", args.family_ids, "-n", args.family_name])
    generate_manifest(
        final_dir, comparison_stats, versions, start_time, end_time,
        cmd, input_dir, output_dir, args.job_id, args.run_id, args.prefix,
        family_stats, args.family_name if args.family_ids else None, de_plan,
        sample_config=config_file,
        count_matrix=count_matrix,
        family_members_file=Path(args.family_ids) if args.family_ids else None,
        foreground_mode="family" if args.family_ids else "global",
        cpu=args.cpu,
        padj_threshold=args.padj,
        log2fc_threshold=args.log2fc,
    )
    
    print(f"RNA-DE completed. Results in {output_dir}")


if __name__ == "__main__":
    main()
