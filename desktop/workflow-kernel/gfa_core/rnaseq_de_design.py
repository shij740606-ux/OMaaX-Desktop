"""Shared RNA-seq differential-expression design inference (Modular v2.0).

The standard user path is sample-table driven: every included sample must carry
an explicit group_role, then this module derives experiment/compare/de blocks.

v2.0 新增：
- M1-M6 六大实验设计模块自动检测
- batch 校正（正交性检查）
- subject_id 配对设计
- genotype/tissue 多因素设计
- dose 剂量响应
- time × condition 时间序列 LRT
- 模糊边界检测（10种场景）
- 完全向后兼容旧接口
"""
from __future__ import annotations

import re
from typing import Any


ROLE_ALIASES = {
    "baseline": "control",
    "reference": "control",
    "ref": "control",
    "case": "compare",
    "treated": "compare",
    "treat": "compare",
    "treatment": "compare",
    "compare_group": "compare",
    "comparison": "compare",
    "skip": "ignore",
    "ignored": "ignore",
    "exclude": "ignore",
}


# 可识别的额外字段（超出旧版 condition/time）
EXTRA_FIELD_KEYS = {
    "genotype", "tissue", "dose", "batch",
    "subject_id", "subject", "patient_id", "plant_id",
    "phenotype", "trait",
}

# phenotype_ 前缀匹配
PHENOTYPE_PREFIX = "phenotype_"

# 剂量类字段别名
DOSE_ALIASES = {"dose", "concentration", "conc", "dosage", "amount"}


def parse_time_sort_value(value: Any) -> tuple[int, Any]:
    text = str(value)
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if match:
        return (0, float(match.group(0)))
    return (1, text)


def parse_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"", "none", "null"}:
        return default
    return text in {"1", "true", "yes", "y", "on"}


def text_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def first_text_value(*values: Any) -> str:
    for value in values:
        text = text_value(value)
        if text:
            return text
    return ""


def infer_optional_batch(
    samples: dict[str, dict[str, Any]],
    active_samples: list[str],
) -> dict[str, Any]:
    """Return a deliberately small optional-batch plan.

    Batch is opt-in: no filled values means the historical design is unchanged.
    Complete metadata with at least two levels enables batch; incomplete or
    single-level metadata falls back to the historical no-batch design.
    Runtime DESeq2 failures are handled by the CLI with one no-batch retry.
    """
    values = {
        str(sample): text_value((samples.get(str(sample)) or {}).get("batch"))
        for sample in active_samples
    }
    filled = {sample: value for sample, value in values.items() if value}
    requested = bool(filled)
    missing = [sample for sample, value in values.items() if not value]
    levels = sorted(set(filled.values()))
    used = requested and not missing and len(levels) >= 2
    reason = ""
    if requested and missing:
        reason = "部分参与分析的样本未填写 batch，已按无批次模型运行。"
    elif requested and len(levels) < 2:
        reason = "batch 只有一个有效水平，无需校正，已按无批次模型运行。"
    return {
        "batch_requested": requested,
        "batch_used": used,
        "batch_values": filled if used else {},
        "batch_levels": levels,
        "batch_fallback": bool(requested and not used),
        "batch_fallback_reason": reason,
    }


def normalize_group_role(value: Any) -> str:
    role = str(value or "").strip().lower().replace("-", "_")
    role = ROLE_ALIASES.get(role, role)
    if role not in {"control", "compare", "ignore"}:
        raise ValueError(f"samples.yaml 分组角色非法: {value}; 允许 control/compare/ignore")
    return role


def normalize_group_label(value: Any) -> str:
    return text_value(value)


def dedupe_comparisons(comparisons: list[list[str]]) -> list[list[str]]:
    seen: set[tuple[str, str]] = set()
    out: list[list[str]] = []
    for comp in comparisons:
        if len(comp) != 2:
            continue
        key = (normalize_group_label(comp[0]), normalize_group_label(comp[1]))
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        out.append([key[0], key[1]])
    return out


def _configured_comparisons(value: Any) -> list[list[str]]:
    if not isinstance(value, list):
        return []
    return dedupe_comparisons(
        [[str(comp[0]), str(comp[1])] for comp in value if isinstance(comp, list) and len(comp) == 2]
    )


# ────────────────────────────────────────────────
# v2.0 新增：字段检测
# ────────────────────────────────────────────────

def _detect_fields(
    meta: dict[str, Any],
) -> dict[str, Any]:
    """从单个样本的 metadata 检测额外字段。

    Returns:
        {field_name: value, ...} 不含 condition/time/group_role
    """
    detected: dict[str, Any] = {}
    for key in EXTRA_FIELD_KEYS:
        val = meta.get(key)
        if val is not None and text_value(val):
            detected[key] = text_value(val)
    # phenotype_* 字段
    for key in meta:
        if key.startswith(PHENOTYPE_PREFIX) or key.lower() in {"phenotype", "trait"}:
            val = meta.get(key)
            if val is not None and text_value(val):
                detected[key] = text_value(val)
    # dose aliases
    for alias in DOSE_ALIASES:
        if alias in meta and alias != "dose":
            val = text_value(meta.get(alias))
            if val and "dose" not in detected:
                detected["dose"] = val
    return detected


def _collect_fields_report(
    active_items: list[tuple[str, dict[str, Any], str]],
) -> dict[str, Any]:
    """收集所有活跃样本的字段统计。

    Returns:
        完整字段报告 dict，包含 has_*, *_levels, *_values 等
    """
    report: dict[str, Any] = {
        "has_condition": True,
        "condition_levels": 0,
        "condition_values": [],
        "has_time": False,
        "time_levels": 0,
        "time_values": [],
        "has_genotype": False,
        "genotype_levels": 0,
        "genotype_values": [],
        "has_tissue": False,
        "tissue_levels": 0,
        "tissue_values": [],
        "has_dose": False,
        "dose_levels": 0,
        "dose_values": [],
        "dose_is_numeric": False,
        "has_subject_id": False,
        "subject_id_levels": 0,
        "n_complete_pairs": 0,
        "n_total_pairs": 0,
        "pairing_completeness": 1.0,
        "has_batch": False,
        "batch_levels": 0,
        "batch_values": [],
        "batch_orthogonal": True,
        "has_phenotype": False,
        "phenotype_fields": [],
        "n_active_samples": len(active_items),
        "replicate_counts": {},
    }

    condition_set: set[str] = set()
    time_set: set[str] = set()
    genotype_set: set[str] = set()
    tissue_set: set[str] = set()
    dose_set: set[str] = set()
    batch_set: set[str] = set()
    subject_id_map: dict[str, set[str]] = {}  # subject_id -> {conditions}
    sample_to_condition: dict[str, str] = {}
    sample_to_batch: dict[str, str] = {}
    phenotype_fields: set[str] = set()
    group_counts: dict[str, int] = {}

    for sample, meta, _role in active_items:
        cond = first_text_value(
            meta.get("condition"), meta.get("group"), meta.get("treatment")
        )
        if cond:
            condition_set.add(cond)
            sample_to_condition[sample] = cond
            group_counts[cond] = group_counts.get(cond, 0) + 1

        time_val = text_value(meta.get("time"))
        if time_val:
            time_set.add(time_val)

        genotype_val = text_value(meta.get("genotype"))
        if genotype_val:
            genotype_set.add(genotype_val)

        tissue_val = text_value(meta.get("tissue"))
        if tissue_val:
            tissue_set.add(tissue_val)

        dose_val = text_value(meta.get("dose"))
        if dose_val:
            dose_set.add(dose_val)

        batch_val = text_value(meta.get("batch"))
        if batch_val:
            batch_set.add(batch_val)
            sample_to_batch[sample] = batch_val

        # subject_id detection
        sid = first_text_value(
            meta.get("subject_id"), meta.get("subject"),
            meta.get("patient_id"), meta.get("plant_id"),
        )
        if sid and cond:
            if sid not in subject_id_map:
                subject_id_map[sid] = set()
            subject_id_map[sid].add(cond)

        # phenotype detection
        for key in meta:
            if key.startswith(PHENOTYPE_PREFIX) or key.lower() in {"phenotype", "trait"}:
                val = text_value(meta.get(key))
                if val:
                    phenotype_fields.add(key)

    # Fill report
    report["condition_levels"] = len(condition_set)
    report["condition_values"] = sorted(condition_set)
    report["has_condition"] = len(condition_set) > 0

    report["has_time"] = len(time_set) > 0
    report["time_levels"] = len(time_set)
    report["time_values"] = sorted(time_set, key=parse_time_sort_value)

    report["has_genotype"] = len(genotype_set) > 0
    report["genotype_levels"] = len(genotype_set)
    report["genotype_values"] = sorted(genotype_set)

    report["has_tissue"] = len(tissue_set) > 0
    report["tissue_levels"] = len(tissue_set)
    report["tissue_values"] = sorted(tissue_set)

    report["has_dose"] = len(dose_set) > 0
    report["dose_levels"] = len(dose_set)
    report["dose_values"] = sorted(dose_set, key=parse_time_sort_value)
    # 检查 dose 是否都是数值
    dose_all_numeric = all(
        parse_time_sort_value(v)[0] == 0 for v in dose_set
    ) if dose_set else False
    report["dose_is_numeric"] = dose_all_numeric

    report["has_subject_id"] = len(subject_id_map) > 0
    report["subject_id_levels"] = len(subject_id_map)
    # 配对完整度：跨 condition 的 subject 数
    complete_pairs = sum(
        1 for conditions in subject_id_map.values() if len(conditions) >= 2
    )
    total_subjects = len(subject_id_map)
    report["n_complete_pairs"] = complete_pairs
    report["n_total_pairs"] = total_subjects
    report["pairing_completeness"] = (
        complete_pairs / total_subjects if total_subjects > 0 else 1.0
    )

    report["has_batch"] = len(batch_set) > 1
    report["batch_levels"] = len(batch_set)
    report["batch_values"] = sorted(batch_set)
    # batch 正交性：每个 batch 里是否包含多个 condition
    if batch_set and condition_set:
        batch_orth = True
        batch_to_conditions: dict[str, set[str]] = {}
        for sample, meta, _role in active_items:
            b = sample_to_batch.get(sample, "__no_batch__")
            c = sample_to_condition.get(sample, "__no_cond__")
            batch_to_conditions.setdefault(b, set()).add(c)
        # 如果任何 batch 只包含一个 condition，检查是否混杂
        mixing_batches = []
        for b, conds in batch_to_conditions.items():
            if b == "__no_batch__":
                continue
            if len(conds) == 1 and len(batch_set) > 1:
                # 这个 batch 只对应一个 condition，可能是混杂
                mixing_batches.append(b)
        # 所有 batch 都只对应一个 condition → 完全混杂
        if len(mixing_batches) == len(batch_set) and len(batch_set) > 1:
            batch_orth = False
        report["batch_orthogonal"] = batch_orth
    else:
        report["batch_orthogonal"] = True

    report["has_phenotype"] = len(phenotype_fields) > 0
    report["phenotype_fields"] = sorted(phenotype_fields)

    report["replicate_counts"] = group_counts

    return report


# ────────────────────────────────────────────────
# v2.0 新增：模块检测
# ────────────────────────────────────────────────

def detect_module(
    fields_report: dict[str, Any],
    *,
    user_module: str = "",
) -> dict[str, Any]:
    """从字段报告中推断最佳实验设计模块。

    Args:
        fields_report: _collect_fields_report() 的输出
        user_module: 用户显式指定的模块 (M1-M6)，空串表示自动推断

    Returns:
        {
            "primary_module": "M5",       # 主推荐模块
            "primary_module_name": "时间序列分析",
            "alternatives": ["M3", "M1"], # 备选模块
            "not_recommended": [],        # 不推荐
            "design_formula": "~ batch + condition * time",
            "test_type": "LRT",
            "comparison_strategy": "baseline",
            "module_source": "auto",      # "auto" / "user"
        }
    """
    if user_module:
        return _build_module_result(user_module, fields_report, source="user")

    # 精简模块模式只自动选择 M4/M3/M1。M1 保持为基础保底流程，
    # 其余历史模块不再参与自动竞争。
    candidates: list[str] = []

    # M4: 交互项 (需要 genotype + condition)
    if (
        fields_report.get("has_genotype")
        and fields_report.get("has_condition")
        and fields_report.get("genotype_levels", 0) >= 2
        and fields_report.get("condition_levels", 0) >= 2
    ):
        candidates.append("M4")

    # M3 只承担多因子内的分层比较。普通多处理共享对照仍由 M1 处理。
    has_multi_factor = (
        (fields_report.get("has_genotype") and fields_report.get("genotype_levels", 0) >= 2)
        or (fields_report.get("has_tissue") and fields_report.get("tissue_levels", 0) >= 2)
    )
    if (
        fields_report.get("has_condition")
        and (
            has_multi_factor and fields_report.get("condition_levels", 0) >= 2
        )
    ):
        candidates.append("M3")

    # M1: 兜底（标准比较）
    if fields_report.get("has_condition") and fields_report.get("condition_levels", 0) >= 2:
        candidates.append("M1")

    if not candidates:
        raise ValueError(
            "无法推断实验设计模块。请确保样本表中至少标记了 condition 字段（>=2 个水平）。"
        )

    primary = candidates[0]
    alternatives = [m for m in candidates[1:] if m != primary]

    return _build_module_result(primary, fields_report, source="auto", alternatives=alternatives)


def _build_module_result(
    module: str,
    fields: dict[str, Any],
    source: str = "auto",
    alternatives: list[str] | None = None,
) -> dict[str, Any]:
    """构建模块检测结果 dict。"""
    from .rnaseq_de_modules import MODULE_CONFIGS

    cfg = MODULE_CONFIGS.get(module, MODULE_CONFIGS["M1"])
    batch_ok = fields.get("has_batch") and fields.get("batch_orthogonal", True)
    batch_prefix = "batch + " if batch_ok else ""

    # 构建 design formula
    if module == "M5":
        extra = ""
        if fields.get("has_genotype"):
            extra = "genotype + "
        design = f"~ {batch_prefix}{extra}condition * time"
    elif module == "M4":
        factor = "genotype"
        if fields.get("has_tissue"):
            factor = "tissue"
        design = f"~ {batch_prefix}{factor} * condition"
    elif module == "M3":
        design = f"~ {batch_prefix}group"
    elif module == "M2":
        design = f"~ {batch_prefix}subject_id + condition"
    elif module == "M6":
        design = f"~ {batch_prefix}dose"
    else:  # M1
        design = f"~ {batch_prefix}condition"

    test_type = cfg.get("test", "Wald")

    # 构建分组逻辑
    group_field = "condition"
    if module == "M5":
        group_field = "time_condition"
    elif module == "M4":
        group_field = "interaction"
    elif module == "M3":
        group_field = "group"
    elif module == "M6":
        group_field = "dose"

    return {
        "primary_module": module,
        "primary_module_name": cfg.get("name", module),
        "alternatives": alternatives or [],
        "not_recommended": [],
        "design_formula": design,
        "test_type": test_type,
        "comparison_strategy": cfg.get("comparison_strategy", "all_vs_control"),
        "module_source": source,
        "group_field": group_field,
        "batch_used": batch_ok,
        "fields_detected": fields,
    }


def infer_priority_advanced_design(
    samples: dict[str, dict[str, Any]],
    *,
    matrix_samples: list[str],
    user_module: str = "",
    minimum_replicates: int = 2,
    design_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the small M3/M4 priority layer used ahead of the legacy M1 path.

    An empty result means that no supported advanced axis varies and the caller
    should use the unchanged legacy design.  Invalid factorial metadata raises
    instead of silently pooling biologically different strata into M1.
    """
    options = dict(design_options or {})
    requested = str(user_module or "").strip().upper()
    if requested and requested not in {"M1", "M3", "M4"}:
        raise ValueError("精简模块模式只支持 M1、M3 和 M4。")
    if requested == "M1":
        return {}

    active: list[dict[str, str]] = []
    missing_meta: list[str] = []
    for sample_id in matrix_samples:
        meta = samples.get(str(sample_id))
        if not isinstance(meta, dict):
            missing_meta.append(str(sample_id))
            continue
        if not parse_bool(meta.get("include"), default=True):
            continue
        role_raw = text_value(meta.get("group_role")).lower()
        role = "compare" if role_raw == "treatment" else role_raw
        if role in {"ignore", "exclude"}:
            continue
        if role and role not in {"control", "compare"}:
            raise ValueError(f"样本 {sample_id} 的 group_role 无效: {role_raw}")
        condition = first_text_value(meta.get("condition"), meta.get("group"), meta.get("treatment"))
        if not condition:
            raise ValueError(f"样本 {sample_id} 缺少 condition，不能生成 DE 设计。")
        active.append({
            "sample_id": str(sample_id),
            "condition": condition,
            "role": role,
            "genotype": text_value(meta.get("genotype")),
            "tissue": text_value(meta.get("tissue")),
            "time": text_value(meta.get("time")),
            "dose": text_value(meta.get("dose")),
            "batch": text_value(meta.get("batch")),
        })
    if missing_meta:
        raise ValueError("samples.yaml 缺少 count matrix 样本 metadata: " + ", ".join(missing_meta[:10]))
    if not active:
        raise ValueError("没有可用于 DE 的样本。")

    varying_axes = {
        field: sorted({row[field] for row in active if row[field]})
        for field in ("genotype", "tissue", "time", "dose")
    }
    advanced_axes = [field for field in ("genotype", "tissue") if len(varying_axes[field]) >= 2]
    if not advanced_axes:
        return {}
    if len(advanced_axes) > 1:
        raise ValueError("同时检测到 genotype 和 tissue 变化；精简 M3/M4 模式不能静默忽略其中一个因子。")
    if len(varying_axes["dose"]) >= 2:
        raise ValueError("M3/M4 精简模式暂不支持同时变化的 dose 因子。")

    factor = advanced_axes[0]
    factor_levels = varying_axes[factor]
    condition_levels = sorted({row["condition"] for row in active})
    missing_factor = [row["sample_id"] for row in active if not row[factor]]
    if missing_factor:
        raise ValueError(f"部分样本缺少 {factor}: " + ", ".join(missing_factor[:10]))

    cells: dict[tuple[str, str], list[str]] = {}
    for row in active:
        cells.setdefault((row[factor], row["condition"]), []).append(row["sample_id"])
    def role_axis(axis: str, levels: list[str]) -> dict[str, Any]:
        roles_by_level = {
            level: {row["role"] for row in active if row[axis] == level and row["role"]}
            for level in levels
        }
        complete = all(roles_by_level[level] for level in levels)
        consistent = complete and all(len(roles_by_level[level]) == 1 for level in levels)
        controls = [level for level in levels if roles_by_level[level] == {"control"}]
        compares = [level for level in levels if roles_by_level[level] == {"compare"}]
        return {
            "valid": consistent and len(controls) == 1 and bool(compares),
            "control": controls[0] if len(controls) == 1 else "",
            "compares": compares,
            "roles": roles_by_level,
        }

    condition_role = role_axis("condition", condition_levels)
    factor_role = role_axis(factor, factor_levels)

    def option(*keys: str) -> str:
        for key in keys:
            value = str(options.get(key) or "").strip()
            if value:
                return value
        return ""

    warnings: list[dict[str, str]] = []
    batch_plan = infer_optional_batch(
        samples,
        [row["sample_id"] for row in active],
    )
    if batch_plan["batch_fallback"]:
        warnings.append({
            "code": "batch_fallback_without_adjustment",
            "message": batch_plan["batch_fallback_reason"],
        })
    # M3 extension: one genotype factor plus multiple time points.
    # Build genotype__time groups and compare every later time with the
    # genotype-specific baseline.  This intentionally does not fit a
    # genotype x condition x time model.
    if factor == "genotype" and len(varying_axes["time"]) >= 2:
        time_levels = sorted(varying_axes["time"], key=parse_time_sort_value)
        time_cells: dict[tuple[str, str], list[str]] = {}
        for row in active:
            if not row["time"]:
                raise ValueError(f"样本 {row['sample_id']} 缺少 time，不能执行品种分层时间比较。")
            time_cells.setdefault((row[factor], row["time"]), []).append(row["sample_id"])

        explicit_reference_time = option(
            "reference_time", "referenceTime", "baseline_time", "baselineTime",
            "baseline_group", "baselineGroup",
        )
        reference_time = ""
        reference_time_source = ""
        if explicit_reference_time:
            reference_time = next(
                (level for level in time_levels if level.lower() == explicit_reference_time.lower()),
                "",
            )
            if reference_time:
                reference_time_source = "user"
            else:
                warnings.append({
                    "code": "invalid_reference_time_ignored",
                    "message": f"reference_time={explicit_reference_time} 不存在，已自动选择时间基线。",
                })

        if not reference_time:
            roles_by_time = {
                level: {row["role"] for row in active if row["time"] == level and row["role"]}
                for level in time_levels
            }
            control_times = [
                level for level in time_levels if roles_by_time[level] == {"control"}
            ]
            if len(control_times) == 1:
                reference_time = control_times[0]
                reference_time_source = "group_role"
            else:
                zero_time = next(
                    (
                        level for level in time_levels
                        if re.sub(r"[\s_.-]+", "", level.strip().lower()) in {"0", "0h", "t0"}
                    ),
                    "",
                )
                if zero_time:
                    reference_time = zero_time
                    reference_time_source = "semantic"
                else:
                    reference_time = time_levels[0]
                    reference_time_source = "earliest_time"
                    warnings.append({
                        "code": "reference_time_auto_earliest",
                        "message": f"未识别到唯一对照时间，使用最早时间 {reference_time} 作为基线。",
                    })

        comparisons: list[list[str]] = []
        skipped: list[dict[str, str]] = []
        for factor_level in factor_levels:
            reference_n = len(time_cells.get((factor_level, reference_time), []))
            for time_level in time_levels:
                if time_level == reference_time:
                    continue
                compare_n = len(time_cells.get((factor_level, time_level), []))
                if reference_n >= minimum_replicates and compare_n >= minimum_replicates:
                    comparisons.append([
                        f"{factor_level}__{time_level}",
                        f"{factor_level}__{reference_time}",
                    ])
                else:
                    skipped.append({
                        "comparison_axis": "time",
                        "stratum": factor_level,
                        "comparison": f"{time_level}_vs_{reference_time}",
                        "reason": f"replicates reference={reference_n}, compare={compare_n}",
                    })
        if not comparisons:
            raise ValueError("没有任何满足重复要求的品种分层时间比较。")

        requested_axis = option(
            "comparison_axis", "comparisonAxis", "primary_factor", "primaryFactor"
        ).lower()
        if requested_axis and requested_axis != "time":
            warnings.append({
                "code": "comparison_axis_overridden_for_stratified_time",
                "message": f"品种×时间场景固定按 time 比较，已忽略 comparison_axis={requested_axis}。",
            })

        groups = {
            row["sample_id"]: f"{row[factor]}__{row['time']}"
            for row in active
        }
        condition_values = {row["sample_id"]: row["condition"] for row in active}
        factor_values = {row["sample_id"]: row[factor] for row in active}
        batch_prefix = "batch + " if batch_plan["batch_used"] else ""
        fallback_from = "M4" if requested == "M4" else ""
        if fallback_from:
            warnings.append({
                "code": "m4_to_m3_stratified_time",
                "message": "品种×时间场景按 M3 品种分层时间比较执行；未运行 M4 品种×处理交互。",
            })
        result = {
            "design": "stratified_time",
            "module": "M3",
            "module_name": "品种分层时间比较",
            "module_source": "user" if requested else "auto",
            "advanced_factor": factor,
            "groups": groups,
            "condition_values": condition_values,
            "factor_values": factor_values,
            "active_samples": [row["sample_id"] for row in active],
            "ignored_samples": [sample for sample in matrix_samples if sample not in groups],
            "control_group": f"{factor_levels[0]}__{reference_time}",
            "control_condition": reference_time,
            "reference_condition": reference_time,
            "reference_time": reference_time,
            "reference_factor": factor_levels[0],
            "reference_genotype": factor_levels[0],
            "condition_reference_source": reference_time_source,
            "factor_reference_source": "not_applicable",
            "comparison_axis": "time",
            "comparison_axis_source": reference_time_source,
            "stratify_axis": factor,
            "condition_levels": condition_levels,
            "factor_levels": factor_levels,
            "time_levels": time_levels,
            "treatment_groups": [pair[0] for pair in comparisons],
            "compare": comparisons,
            "comparison_mode": "factor_stratified_time_baseline",
            "design_formula": f"~ {batch_prefix}group",
            "test_type": "Wald",
            "m4_eligible": False,
            "fallback_from": fallback_from,
            "fallback_reason": (
                "genotype x time routed to M3 genotype-stratified baseline comparisons"
                if fallback_from else ""
            ),
            "skipped_comparisons": skipped,
            "warnings": warnings,
        }
        result.update(batch_plan)
        return result

    if len(varying_axes["time"]) >= 2:
        raise ValueError("M3/M4 精简模式暂只支持 genotype × time 的分层时间比较。")
    if len(condition_levels) < 2:
        return {}

    explicit_axis = option("comparison_axis", "comparisonAxis", "primary_factor", "primaryFactor").lower()
    if explicit_axis in {"genotype", "tissue"}:
        explicit_axis = factor if explicit_axis == factor else explicit_axis
    if explicit_axis and explicit_axis not in {"condition", factor}:
        warnings.append({
            "code": "invalid_comparison_axis_ignored",
            "message": f"comparison_axis={explicit_axis} 不适用于当前 {factor} x condition 设计，已自动推断。",
        })
        explicit_axis = ""

    if explicit_axis:
        comparison_axis = explicit_axis
        axis_source = "user"
    elif condition_role["valid"] and not factor_role["valid"]:
        comparison_axis = "condition"
        axis_source = "group_role"
    elif factor_role["valid"] and not condition_role["valid"]:
        comparison_axis = factor
        axis_source = "group_role"
    elif condition_role["valid"]:
        comparison_axis = "condition"
        axis_source = "group_role"
    else:
        comparison_axis = "condition"
        axis_source = "auto"
        warnings.append({
            "code": "comparison_axis_auto_default",
            "message": "group_role 未能唯一确定比较轴，默认按 condition 比较；可显式设置 comparison_axis。",
        })
    stratify_axis = factor if comparison_axis == "condition" else "condition"

    def normalized_label(value: str) -> str:
        return re.sub(r"[\s_.-]+", "", value.strip().lower())

    def choose_reference(
        axis: str,
        levels: list[str],
        explicit: str,
        role_info: dict[str, Any],
        semantic_tokens: set[str],
    ) -> tuple[str, str]:
        if explicit:
            match = next((level for level in levels if level.lower() == explicit.lower()), "")
            if match:
                return match, "user"
            warnings.append({
                "code": f"invalid_reference_{axis}_ignored",
                "message": f"reference_{axis}={explicit} 不存在，已自动选择参考水平。",
            })
        if role_info["valid"]:
            return str(role_info["control"]), "group_role"
        semantic = next(
            (level for level in levels if normalized_label(level) in semantic_tokens),
            "",
        )
        if semantic:
            return semantic, "semantic"
        warnings.append({
            "code": f"reference_{axis}_auto_sorted",
            "message": f"无法识别 {axis} 参考水平，使用稳定排序后的 {levels[0]}；建议用户确认。",
        })
        return levels[0], "auto_sorted"

    reference_condition, condition_reference_source = choose_reference(
        "condition",
        condition_levels,
        option("reference_condition", "referenceCondition", "control_condition", "controlCondition"),
        condition_role,
        {"control", "ctrl", "ck", "mock", "vehicle", "dmso", "untreated", "baseline", "0", "0h"},
    )
    reference_factor, factor_reference_source = choose_reference(
        factor,
        factor_levels,
        option(f"reference_{factor}", f"reference{factor.title()}", "control_genotype", "controlGenotype"),
        factor_role,
        {"wt", "wildtype", "reference", "ref"},
    )

    def axis_comparisons(axis: str) -> tuple[list[list[str]], list[dict[str, str]]]:
        comparisons: list[list[str]] = []
        skipped: list[dict[str, str]] = []
        if axis == "condition":
            for factor_level in factor_levels:
                reference_n = len(cells.get((factor_level, reference_condition), []))
                for condition in condition_levels:
                    if condition == reference_condition:
                        continue
                    compare_n = len(cells.get((factor_level, condition), []))
                    if reference_n >= minimum_replicates and compare_n >= minimum_replicates:
                        comparisons.append([
                            f"{factor_level}__{condition}",
                            f"{factor_level}__{reference_condition}",
                        ])
                    else:
                        skipped.append({
                            "comparison_axis": "condition",
                            "stratum": factor_level,
                            "comparison": f"{condition}_vs_{reference_condition}",
                            "reason": f"replicates reference={reference_n}, compare={compare_n}",
                        })
        else:
            for condition in condition_levels:
                reference_n = len(cells.get((reference_factor, condition), []))
                for factor_level in factor_levels:
                    if factor_level == reference_factor:
                        continue
                    compare_n = len(cells.get((factor_level, condition), []))
                    if reference_n >= minimum_replicates and compare_n >= minimum_replicates:
                        comparisons.append([
                            f"{factor_level}__{condition}",
                            f"{reference_factor}__{condition}",
                        ])
                    else:
                        skipped.append({
                            "comparison_axis": factor,
                            "stratum": condition,
                            "comparison": f"{factor_level}_vs_{reference_factor}",
                            "reason": f"replicates reference={reference_n}, compare={compare_n}",
                        })
        return comparisons, skipped

    primary_comparisons, primary_skipped = axis_comparisons(comparison_axis)
    secondary_axis = factor if comparison_axis == "condition" else "condition"
    secondary_comparisons, secondary_skipped = axis_comparisons(secondary_axis)
    if not primary_comparisons and secondary_comparisons and not explicit_axis:
        comparison_axis, secondary_axis = secondary_axis, comparison_axis
        stratify_axis = factor if comparison_axis == "condition" else "condition"
        primary_comparisons, secondary_comparisons = secondary_comparisons, primary_comparisons
        primary_skipped, secondary_skipped = secondary_skipped, primary_skipped
        warnings.append({
            "code": "comparison_axis_auto_switched",
            "message": f"默认比较轴没有可执行比较，已切换为 {comparison_axis}。",
        })

    m4_eligible = all(
        len(cells.get((level, condition), [])) >= minimum_replicates
        for level in factor_levels
        for condition in condition_levels
    )
    if requested == "M4" and not m4_eligible and not primary_comparisons:
        raise ValueError("M4 不可估计，且没有任何满足重复要求的 M3 分层比较。")
    if requested == "M3" and not primary_comparisons:
        raise ValueError("没有任何满足重复要求的 M3 分层比较。")
    if not m4_eligible and not primary_comparisons:
        raise ValueError("复杂因子正在变化，但 M4/M3 均不满足完整组合和重复要求；拒绝合并为 M1。")

    module = "M4" if m4_eligible and requested != "M3" else "M3"
    comparisons = list(primary_comparisons)
    skipped = list(primary_skipped)
    if module == "M4":
        seen = {tuple(item) for item in comparisons}
        comparisons.extend(item for item in secondary_comparisons if tuple(item) not in seen)
        skipped.extend(secondary_skipped)
    groups = {
        row["sample_id"]: f"{row[factor]}__{row['condition']}"
        for row in active
    }
    condition_values = {row["sample_id"]: row["condition"] for row in active}
    factor_values = {row["sample_id"]: row[factor] for row in active}
    fallback_from = "M4" if module == "M3" else ""
    batch_prefix = "batch + " if batch_plan["batch_used"] else ""
    result = {
        "design": "interaction" if module == "M4" else "stratified",
        "module": module,
        "module_name": "响应差异分析" if module == "M4" else "多组合分层比较",
        "module_source": "user" if requested else "auto",
        "advanced_factor": factor,
        "groups": groups,
        "condition_values": condition_values,
        "factor_values": factor_values,
        "active_samples": [row["sample_id"] for row in active],
        "ignored_samples": [sample for sample in matrix_samples if sample not in groups],
        "control_group": (
            f"{factor_levels[0]}__{reference_condition}"
            if comparison_axis == "condition"
            else f"{reference_factor}__{condition_levels[0]}"
        ),
        "control_condition": reference_condition,
        "reference_condition": reference_condition,
        "reference_factor": reference_factor,
        f"reference_{factor}": reference_factor,
        "condition_reference_source": condition_reference_source,
        "factor_reference_source": factor_reference_source,
        "comparison_axis": comparison_axis,
        "comparison_axis_source": axis_source,
        "stratify_axis": stratify_axis,
        "condition_levels": condition_levels,
        "factor_levels": factor_levels,
        "treatment_groups": [pair[0] for pair in comparisons],
        "compare": comparisons,
        "comparison_mode": "factor_stratified_control",
        "design_formula": f"~ {batch_prefix}{factor} * condition" if module == "M4" else f"~ {batch_prefix}group",
        "test_type": "LRT+Wald" if module == "M4" else "Wald",
        "interaction_full": f"~ {batch_prefix}{factor} * condition",
        "interaction_reduced": f"~ {batch_prefix}{factor} + condition",
        "m4_eligible": m4_eligible,
        "fallback_from": fallback_from,
        "fallback_reason": "M4 cells incomplete; using valid M3 strata" if fallback_from else "",
        "skipped_comparisons": skipped,
        "warnings": warnings,
    }
    result.update(batch_plan)
    return result


# ────────────────────────────────────────────────
# 原始 infer_rnaseq_de_design (保持向后兼容)
# ────────────────────────────────────────────────

def infer_rnaseq_de_design(
    samples: dict[str, dict[str, Any]],
    *,
    matrix_samples: list[str] | None = None,
    configured_compare: list[list[str]] | None = None,
    legacy_mode: bool = True,
    user_module: str = "",
) -> dict[str, Any]:
    """Infer DE design from sample group_role values.

    Included samples must have group_role.  Ignore/excluded samples are retained
    in samples.yaml but omitted from groups and DE comparisons.

    When legacy_mode=False, also detect module (M1-M6) and extra factors.
    """
    selected = list(matrix_samples) if matrix_samples is not None else list(samples.keys())
    selected_set = set(map(str, selected))
    sample_items: list[tuple[str, dict[str, Any]]] = []
    missing_sample_meta = []
    for sample in selected:
        meta = samples.get(str(sample))
        if not isinstance(meta, dict):
            missing_sample_meta.append(str(sample))
            continue
        sample_items.append((str(sample), meta))
    if missing_sample_meta:
        raise ValueError(
            "samples.yaml 缺少这些 count matrix 样本的 metadata: "
            + ", ".join(missing_sample_meta[:10])
        )

    missing_roles: list[str] = []
    active_items: list[tuple[str, dict[str, Any], str]] = []
    ignored_samples: list[str] = []
    for sample, meta in sample_items:
        include = parse_bool(meta.get("include"), default=True)
        raw_role = str(meta.get("group_role") or "").strip()
        if not raw_role and include:
            missing_roles.append(sample)
            continue
        role = normalize_group_role(raw_role) if raw_role else "ignore"
        if not include or role == "ignore":
            ignored_samples.append(sample)
            continue
        active_items.append((sample, meta, role))

    if missing_roles:
        raise ValueError(
            "samples.yaml 缺少 group_role；请在样本表中为这些样本标记 control/compare/ignore: "
            + ", ".join(missing_roles[:10])
        )
    if not active_items:
        raise ValueError("没有可用于 DE 的样本；请至少保留一个 control 和一个 compare 样本。")

    fields_report = _collect_fields_report(active_items)
    module_info: dict[str, Any] = {}
    if not legacy_mode:
        module_info = detect_module(fields_report, user_module=user_module)
    module_id = str(module_info.get("primary_module") or "")

    time_present = [bool(text_value(meta.get("time"))) for _sample, meta, _role in active_items]
    if any(time_present) and not all(time_present):
        missing_time = [
            sample
            for sample, meta, _role in active_items
            if not text_value(meta.get("time"))
        ]
        raise ValueError(
            "检测到部分样本填写了 time；time_series 设计要求所有参与 DE 的样本都有 time: "
            + ", ".join(missing_time[:10])
        )
    design = "time_series" if all(time_present) else ""
    if module_id == "M6":
        design = "dose_response"

    # 分组逻辑：时间序列按 condition + time 分组，否则按 condition 分组
    groups: dict[str, str] = {}
    roles_by_group: dict[str, set[str]] = {}
    for sample, meta, role in active_items:
        if module_id == "M6":
            group = normalize_group_label(meta.get("dose") or meta.get("phenotype") or meta.get("condition"))
            if not group:
                raise ValueError(f"样本 {sample} 缺少 dose/phenotype/condition，不能生成剂量响应设计。")
        elif design == "time_series":
            t = normalize_group_label(meta.get("time"))
            if not t:
                raise ValueError(f"样本 {sample} 缺少 time，不能生成 DE 分组。")
            group = t
        else:
            group = first_text_value(meta.get("condition"), meta.get("group"), meta.get("treatment"))
            if not group:
                raise ValueError(f"样本 {sample} 缺少 condition，不能生成 DE 分组。")
        groups[sample] = group
        roles_by_group.setdefault(group, set()).add(role)

    conflict_groups = sorted(group for group, roles in roles_by_group.items() if len(roles) > 1)

    # Auto-upgrade: if conflict and genotype/tissue exists, retry with compound grouping
    upgrade_design = False
    if conflict_groups:
        has_genotype = any(first_text_value(meta.get("genotype")) for _, meta, _ in active_items)
        has_tissue = any(first_text_value(meta.get("tissue")) for _, meta, _ in active_items)
        if has_genotype or has_tissue:
            inter_field = "genotype" if has_genotype else "tissue"
            print(f"[INFO] 检测到分组冲突，启用了 {inter_field} 交互模式")
            upgrade_design = True
            design = "interaction"
            # Rebuild groups with compound key: condition_genotype
            groups = {}
            roles_by_group = {}
            for sample, meta, role in active_items:
                cond = first_text_value(meta.get("condition"), meta.get("group"), meta.get("treatment"))
                extra = first_text_value(meta.get(inter_field))
                if not cond or not extra:
                    raise ValueError(f"样本 {sample} 缺少 condition 或 {inter_field}，无法生成交互分组。")
                group = f"{extra}_{cond}"
                groups[sample] = group
                roles_by_group.setdefault(group, set()).add(role)
            conflict_groups = sorted(group for group, roles in roles_by_group.items() if len(roles) > 1)

    if conflict_groups and not upgrade_design and module_id != "M6":
        raise ValueError("同一 DE 分组内不能混合 control/compare: " + ", ".join(conflict_groups))

    control_groups = sorted(
        [group for group, roles in roles_by_group.items() if "control" in roles],
        key=parse_time_sort_value,
    )
    compare_groups = sorted(
        [group for group, roles in roles_by_group.items() if "compare" in roles],
        key=parse_time_sort_value,
    )
    if module_id == "M6":
        control_groups = control_groups[:1] or sorted(set(groups.values()), key=parse_time_sort_value)[:1]
        compare_groups = sorted([g for g in set(groups.values()) if g not in set(control_groups)], key=parse_time_sort_value)
    elif len(control_groups) != 1:
        if not control_groups:
            raise ValueError("请在样本表中标记唯一对照组 group_role=control；系统不会自动猜测 control。")
        if design == "time_series":
            control_groups = [control_groups[0]]
        else:
            raise ValueError("只能有一个对照组，当前为: " + ", ".join(control_groups))
    if not compare_groups and module_id != "M6":
        raise ValueError("请在样本表中至少标记一个比较组 group_role=compare。")

    control = control_groups[0] if control_groups else ""
    if not design:
        design = "two_group" if len(compare_groups) == 1 else "multi_group"

    explicit_compare = _configured_comparisons(configured_compare or [])
    valid_groups = set(groups.values())
    if explicit_compare:
        invalid = [comp for comp in explicit_compare if comp[0] not in valid_groups or comp[1] not in valid_groups]
        if invalid:
            raise ValueError(
                "compare 引用了不存在或已 ignore 的分组: "
                + ", ".join(f"{left}_vs_{right}" for left, right in invalid)
            )
        comparisons = explicit_compare
        comparison_mode = "configured"
    elif design == "time_series":
        comparisons = [[group, control] for group in compare_groups if group != control]
        comparison_mode = "time_baseline_group_role"
    elif module_id == "M6":
        comparisons = []
        comparison_mode = "dose_response"
    else:
        comparisons = [[group, control] for group in compare_groups]
        comparison_mode = "group_role_control"

    result: dict[str, Any] = {
        "design": design,
        "groups": groups,
        "active_samples": [sample for sample, _meta, _role in active_items if sample in selected_set],
        "ignored_samples": ignored_samples,
        "control_group": control,
        "treatment_groups": compare_groups,
        "compare": comparisons,
        "comparison_mode": comparison_mode,
        "experiment": {
            "design": design,
            "control_group": control,
            "treatment_groups": compare_groups,
        },
        "de": {
            "strategy": "auto",
            "design": design,
            "control_group": control,
            "treatment_groups": compare_groups,
            "run_lrt": bool(design == "time_series" and len(set(
                normalize_group_label(meta.get("time")) for _s, meta, _r in active_items
            )) >= 3),
            "run_pairwise": True,
            "pairwise_modes": ["baseline"] if design == "time_series" else ["control"],
            "baseline_group": control,
        },
    }

    # ── v2.0: 非 legacy 模式，附加模块检测 ──
    if not legacy_mode:
        if not module_info:
            module_info = detect_module(fields_report, user_module=user_module)
        result["module"] = module_info["primary_module"]
        result["module_name"] = module_info["primary_module_name"]
        result["design_formula"] = module_info["design_formula"]
        result["test_type"] = module_info["test_type"]
        result["fields_report"] = fields_report
        result["batch_used"] = module_info["batch_used"]
        result["alternatives"] = module_info.get("alternatives", [])
        result["module_source"] = module_info.get("module_source", "auto")

        # 覆盖 de block
        result["de"]["strategy"] = "modular"
        result["de"]["design"] = module_info["primary_module"]
        result["de"]["test_type"] = module_info["test_type"]
        result["de"]["design_formula"] = module_info["design_formula"]
        result["de"]["batch_used"] = module_info["batch_used"]

    return result


def infer_rnaseq_de_design_v2(
    samples: dict[str, dict[str, Any]],
    *,
    matrix_samples: list[str] | None = None,
    configured_compare: list[list[str]] | None = None,
    user_module: str = "",
) -> dict[str, Any]:
    """v2.0 完整推断：模块检测 + 模糊场景 + 字段报告。

    与 infer_rnaseq_de_design(legacy_mode=False) 等效，但返回更丰富的字段报告。
    """
    result = infer_rnaseq_de_design(
        samples,
        matrix_samples=matrix_samples,
        configured_compare=configured_compare,
        legacy_mode=False,
        user_module=user_module,
    )
    return result


def plan_rnaseq_de_design(
    samples: dict[str, dict[str, Any]],
    *,
    matrix_samples: list[str] | None = None,
    configured_compare: list[list[str]] | None = None,
    user_module: str = "",
    design_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Use the same M3/M4-first planning policy for web review and execution."""
    selected_samples = list(matrix_samples or samples.keys())
    requested = str(user_module or "").strip().upper()
    # Repeated biological subjects require the already implemented M2 R model.
    # Keep the existing M3/M4 path for independent factorial designs.
    subject_values = [str(samples.get(s, {}).get("subject_id") or "").strip() for s in selected_samples
                      if parse_bool(samples.get(s, {}).get("include"), default=True)
                      and normalize_group_role(samples.get(s, {}).get("group_role") or "ignore") != "ignore"]
    repeated_subjects = len([s for s in subject_values if s]) != len(set(s for s in subject_values if s))
    if requested == "M2" or repeated_subjects:
        if requested and requested != "M2":
            raise ValueError("存在重复 subject_id，不能作为独立组执行；请选择 M2 配对设计。")
        paired = infer_rnaseq_de_design(samples, matrix_samples=selected_samples,
                                       configured_compare=configured_compare, legacy_mode=True)
        active = paired["active_samples"]
        conditions = set(paired["groups"].values())
        pairs = {}
        for sample in active:
            meta = samples[sample]
            subject = str(meta.get("subject_id") or "").strip()
            if not subject:
                raise ValueError("配对设计需要每个有效样本有 subject_id。")
            condition = paired["groups"][sample]
            key = (subject, condition)
            if key in pairs:
                raise ValueError("同一 subject_id 和 condition 出现多个文库；请先审核技术重复。")
            pairs[key] = sample
        subjects = {key[0] for key in pairs}
        if len(conditions) != 2 or len(subjects) < 2 or any((s, cond) not in pairs for s in subjects for cond in conditions):
            raise ValueError("当前 M2 入口要求两个组、至少两个独立 subject_id 和完整配对；拒绝退回独立组。")
        if len({str(samples[s].get("batch") or "") for s in active}) > 1:
            raise ValueError("配对同时包含多批次需审核联合设计；当前 M2 不自动忽略 batch。")
        for axis in ("genotype", "tissue", "time", "dose"):
            if len({str(samples[s].get(axis) or "") for s in active}) > 1:
                raise ValueError("配对且含其他变化因子需审核联合设计：" + axis)
        paired.update(module="M2", module_name="配对组间比较", module_source="user" if requested else "auto",
                      design_formula="~ subject_id + condition", test_type="Wald", batch_used=False,
                      condition_values=dict(paired["groups"]), control_condition=paired["control_group"])
        paired["de"].update(strategy="modular", design="M2", design_formula="~ subject_id + condition")
        return paired
    advanced = infer_priority_advanced_design(
        samples,
        matrix_samples=selected_samples,
        user_module=requested,
        design_options=design_options,
    )
    if advanced:
        return advanced

    plan = infer_rnaseq_de_design(
        samples,
        matrix_samples=selected_samples,
        configured_compare=configured_compare,
        legacy_mode=True,
    )
    plan["module"] = "M1"
    plan["module_name"] = "标准组间比较"
    plan["module_source"] = "user" if requested == "M1" else "fallback"
    batch_plan = infer_optional_batch(samples, list(plan.get("active_samples") or selected_samples))
    plan.update(batch_plan)
    batch_prefix = "batch + " if batch_plan["batch_used"] else ""
    plan["design_formula"] = f"~ {batch_prefix}group"
    plan["test_type"] = "Wald"
    if batch_plan["batch_fallback"]:
        plan.setdefault("warnings", []).append({
            "code": "batch_fallback_without_adjustment",
            "message": batch_plan["batch_fallback_reason"],
        })
    if requested in {"M3", "M4"}:
        plan["fallback_from"] = requested
        plan["fallback_reason"] = "样品表未检测到可执行的 genotype/tissue × condition 高级设计，使用 M1。"
    return plan


def build_de_design_review(plan: dict[str, Any]) -> dict[str, Any]:
    """Convert an executable design plan into a stable, UI-facing contract."""
    module = str(plan.get("module") or "M1")
    factor = str(plan.get("advanced_factor") or "")
    comparison_axis = str(plan.get("comparison_axis") or "condition")
    stratify_axis = str(plan.get("stratify_axis") or "")

    comparisons: list[dict[str, str]] = []
    for pair in plan.get("compare") or []:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        compare_group, reference_group = str(pair[0]), str(pair[1])
        display = f"{compare_group} vs {reference_group}"
        if factor and "__" in compare_group and "__" in reference_group:
            compare_factor, compare_condition = compare_group.split("__", 1)
            reference_factor, reference_condition = reference_group.split("__", 1)
            if compare_factor == reference_factor and compare_condition != reference_condition:
                display = f"{compare_factor} 中：{compare_condition} vs {reference_condition}"
            elif compare_condition == reference_condition and compare_factor != reference_factor:
                display = f"{compare_condition} 条件下：{compare_factor} vs {reference_factor}"
        comparisons.append({
            "name": f"{compare_group}_vs_{reference_group}",
            "compare": compare_group,
            "reference": reference_group,
            "display": display,
        })

    raw_warnings = plan.get("warnings") or []
    warnings = [
        dict(item) if isinstance(item, dict) else {"code": "design_warning", "message": str(item)}
        for item in raw_warnings
    ]
    fallback_from = str(plan.get("fallback_from") or "")
    return {
        "status": "ready",
        "module": module,
        "module_name": str(plan.get("module_name") or module),
        "module_source": str(plan.get("module_source") or "auto"),
        "design": str(plan.get("design") or ""),
        "design_formula": str(plan.get("design_formula") or "~ group"),
        "test_type": str(plan.get("test_type") or "Wald"),
        "factor": factor,
        "condition_factor": "condition",
        "comparison_axis": comparison_axis,
        "comparison_axis_source": str(plan.get("comparison_axis_source") or ""),
        "stratify_axis": stratify_axis,
        "reference_levels": {
            "condition": str(plan.get("reference_condition") or plan.get("control_condition") or plan.get("control_group") or ""),
            factor: str(plan.get("reference_factor") or plan.get(f"reference_{factor}") or "") if factor else "",
        },
        "reference_sources": {
            "condition": str(plan.get("condition_reference_source") or ""),
            factor: str(plan.get("factor_reference_source") or "") if factor else "",
        },
        "comparisons": comparisons,
        "interaction": {
            "enabled": module == "M4",
            "display": f"{factor} × condition 交互效应" if module == "M4" and factor else "",
            "methods": ["LRT", "Wald"] if module == "M4" else [],
        },
        "batch": {
            "requested": bool(plan.get("batch_requested")),
            "used": bool(plan.get("batch_used")),
            "levels": list(plan.get("batch_levels") or []),
            "fallback": bool(plan.get("batch_fallback")),
            "fallback_reason": str(plan.get("batch_fallback_reason") or ""),
            "display": (
                "检测到 batch，将优先使用批次校正模型；若执行失败会自动改用无批次模型。"
                if plan.get("batch_used")
                else str(plan.get("batch_fallback_reason") or "未填写 batch，使用默认无批次模型。")
            ),
        },
        "fallback": {
            "occurred": bool(fallback_from),
            "from": fallback_from,
            "target": module,
            "reason": str(plan.get("fallback_reason") or ""),
        },
        "skipped_comparisons": list(plan.get("skipped_comparisons") or []),
        "warnings": warnings,
    }
