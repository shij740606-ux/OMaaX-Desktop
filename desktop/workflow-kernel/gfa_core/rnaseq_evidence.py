"""Lightweight, provider-free RNA-seq manifest evidence snapshot.

The live interpretation view and the formal report share this module so scope
selection and manifest provenance have one implementation.  It intentionally
does not import the MAS agent package.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


REPORT_STAGE = "rnaseq_report"
SUCCESS_STATUSES = {"success", "partial_success"}
GLOBAL_LEGACY_STAGES = {
    "rnaseq_qc",
    "rnaseq_align",
    "rnaseq_quant",
    "annotation",
    "annotation_go",
    "annotation_kegg",
}
RNASEQ_STAGE_NAMES = {
    "rnaseq_qc": "RNA-seq质控",
    "rnaseq_align": "RNA-seq比对",
    "rnaseq_quant": "RNA-seq定量",
    "rnaseq_sample_qc": "RNA-seq样本质量评估",
    "rnaseq_de": "RNA-seq差异表达",
    "annotation": "背景功能注释",
    "annotation_go": "GO背景自动注释",
    "annotation_kegg": "KEGG/KO背景自动注释",
    "rnaseq_go_enrich": "RNA-seq GO富集分析",
    "rnaseq_kegg_enrich": "RNA-seq KEGG富集分析",
    "rnaseq_enrich": "RNA-seq富集分析（兼容入口）",
    "rnaseq_fgsea": "RNA-seq fgsea排序富集",
    "rnaseq_wgcna": "RNA-seq WGCNA共表达分析",
    "rnaseq_candidate_family": "RNA-seq候选家族筛选",
    "rnaseq_family_score": "RNA-seq候选家族评分",
}
RNASEQ_STAGE_ORDER = list(RNASEQ_STAGE_NAMES)


@dataclass(frozen=True)
class RNASeqManifestSelection:
    rnaseq_scope_id: str
    discovered_scope_ids: tuple[str, ...]
    manifests: dict[str, dict[str, Any]]


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def manifest_identity_value(manifest: dict[str, Any], key: str) -> str:
    context = manifest.get("stage_context") or manifest.get("stageContext") or {}
    params = manifest.get("parameters") or {}
    return str(manifest.get(key) or params.get(key) or context.get(key) or "").strip()


def is_rnaseq_evidence_stage(stage: str) -> bool:
    stage = str(stage or "").strip()
    return stage != REPORT_STAGE and (
        stage.startswith("rnaseq_") or stage in {"annotation", "annotation_go", "annotation_kegg"}
    )


def stage_sort_key(stage: str) -> tuple[int, str]:
    try:
        return RNASEQ_STAGE_ORDER.index(stage), stage
    except ValueError:
        return len(RNASEQ_STAGE_ORDER), stage


def _manifest_score(path: Path, manifest: dict[str, Any]) -> tuple[int, int, int, float]:
    status = str(manifest.get("status") or "")
    status_rank = 2 if status == "success" else 1 if status == "partial_success" else 0
    metrics = manifest.get("metrics") if isinstance(manifest.get("metrics"), dict) else {}
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return status_rank, len(metrics), len(artifacts), mtime


def select_rnaseq_manifests(
    final_dir: Path,
    prefix: str,
    *,
    rnaseq_scope_id: str = "",
) -> RNASeqManifestSelection:
    """Select one non-mixing manifest per RNA-seq stage for a prefix/scope."""
    final_dir = Path(final_dir)
    pattern = f"{prefix}.*.manifest.json" if prefix else "*.manifest.json"
    candidates: list[tuple[Path, dict[str, Any]]] = []
    latest_by_scope: dict[str, float] = {}
    for path in sorted(final_dir.glob(pattern)) if final_dir.exists() else []:
        manifest = read_json(path)
        stage = str(manifest.get("stage") or "")
        if not is_rnaseq_evidence_stage(stage):
            continue
        manifest_prefix = str(manifest.get("prefix") or "").strip()
        if prefix and manifest_prefix and manifest_prefix != prefix:
            continue
        candidates.append((path, manifest))
        scope = manifest_identity_value(manifest, "rnaseq_scope_id")
        if scope:
            try:
                latest_by_scope[scope] = max(latest_by_scope.get(scope, 0.0), path.stat().st_mtime)
            except OSError:
                latest_by_scope.setdefault(scope, 0.0)

    discovered = sorted(latest_by_scope, key=lambda item: (-latest_by_scope[item], item))
    resolved_scope = str(rnaseq_scope_id or "").strip() or (discovered[0] if discovered else "")
    selected_paths: dict[str, Path] = {}
    selected: dict[str, dict[str, Any]] = {}
    for path, manifest in candidates:
        stage = str(manifest.get("stage") or "")
        scope = manifest_identity_value(manifest, "rnaseq_scope_id")
        if resolved_scope and scope and scope != resolved_scope:
            continue
        if resolved_scope and not scope and stage not in GLOBAL_LEGACY_STAGES:
            continue
        existing = selected.get(stage)
        if existing is not None and _manifest_score(selected_paths[stage], existing) >= _manifest_score(path, manifest):
            continue
        manifest = dict(manifest)
        manifest["_manifest_path"] = str(path)
        selected[stage] = manifest
        selected_paths[stage] = path
    return RNASeqManifestSelection(resolved_scope, tuple(discovered), selected)


def _compact_value(value: Any, limit: int = 240) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def resolve_artifact_path(
    final_dir: Path,
    item: dict[str, Any],
    *,
    require_exists: bool = False,
) -> Path | None:
    """Resolve artifacts after a workspace has been moved or mounted elsewhere."""
    final_dir = Path(final_dir)
    workspace = final_dir.parent
    candidates: list[Path] = []
    for raw in (item.get("full_path"), item.get("path")):
        if not raw:
            continue
        path = Path(str(raw))
        candidates.append(path if path.is_absolute() else workspace / path)
        if path.is_absolute():
            candidates.append(final_dir / path.name)

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        identity = str(path)
        if identity not in seen:
            seen.add(identity)
            unique.append(path)
    for path in unique:
        if path.exists():
            return path
    if require_exists:
        return None
    return unique[0] if unique else None


def build_rnaseq_evidence_snapshot(
    final_dir: Path,
    prefix: str,
    *,
    rnaseq_scope_id: str = "",
    include_stages: Iterable[str] | None = None,
    selection: RNASeqManifestSelection | None = None,
) -> dict[str, Any]:
    """Build a compact evidence object without invoking an LLM or agent cache."""
    selection = selection or select_rnaseq_manifests(
        final_dir, prefix, rnaseq_scope_id=rnaseq_scope_id
    )
    allowed = {str(stage) for stage in include_stages or [] if str(stage).strip()}
    manifests = {
        stage: manifest
        for stage, manifest in selection.manifests.items()
        if not allowed or stage in allowed
    }
    stages = sorted(manifests, key=stage_sort_key)
    stage_flow: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    card_index = 0
    for stage in stages:
        manifest = manifests[stage]
        status = str(manifest.get("status") or "unknown")
        metrics = manifest.get("metrics") if isinstance(manifest.get("metrics"), dict) else {}
        parameters = manifest.get("parameters") if isinstance(manifest.get("parameters"), dict) else {}
        artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
        metric_items = list(sorted(metrics.items()))[:12]
        metric_summary = ", ".join(f"{key}={_compact_value(value, 80)}" for key, value in metric_items[:5])
        key_result = metric_summary or f"登记产物 {len(artifacts)} 个"
        stage_flow.append(
            {
                "id": stage,
                "name": RNASEQ_STAGE_NAMES.get(stage, stage),
                "status": status,
                "key_result": key_result,
            }
        )
        findings.append(
            {
                "stage": stage,
                "title": RNASEQ_STAGE_NAMES.get(stage, stage),
                "description": f"manifest 状态为 {status}；{key_result}。",
            }
        )
        limitations: list[str] = []
        if status == "partial_success":
            limitations.append("该阶段为 partial_success，结果可用但不完整。")
        elif status != "success":
            limitations.append("该阶段未成功，不纳入正式结论。")
        missing_artifacts: list[str] = []
        for name, item in sorted(artifacts.items()):
            if not isinstance(item, dict):
                continue
            path = resolve_artifact_path(final_dir, item)
            exists = bool(path and path.exists())
            card_index += 1
            cards.append(
                {
                    "id": f"RNA-E{card_index:04d}",
                    "stage": stage,
                    "claim": f"产物 {name}",
                    "evidence": f"path={path or ''}, exists={exists}",
                    "source_type": "raw_file" if exists else "manifest",
                }
            )
            if not exists:
                missing_artifacts.append(str(name))
        for key, value in metric_items:
            card_index += 1
            cards.append(
                {
                    "id": f"RNA-E{card_index:04d}",
                    "stage": stage,
                    "claim": f"指标 {key}",
                    "evidence": _compact_value(value),
                    "source_type": "manifest",
                }
            )
        safe_parameter_items = [
            (key, value)
            for key, value in sorted(parameters.items())
            if not any(secret in str(key).lower() for secret in ("api_key", "token", "secret", "password"))
        ][:10]
        for key, value in safe_parameter_items:
            card_index += 1
            cards.append(
                {
                    "id": f"RNA-E{card_index:04d}",
                    "stage": stage,
                    "claim": f"参数 {key}",
                    "evidence": _compact_value(value),
                    "source_type": "manifest",
                }
            )
        if missing_artifacts:
            shown = missing_artifacts[:12]
            remainder = len(missing_artifacts) - len(shown)
            suffix = f"（另有 {remainder} 个，详见 evidence JSON）" if remainder else ""
            message = f"manifest 登记但文件不存在的产物：{', '.join(shown)}{suffix}"
            limitations.append(message)
            risks.append({"stage": stage, "severity": "high", "message": message})
        if status not in SUCCESS_STATUSES:
            risks.append({"stage": stage, "severity": "high", "message": f"阶段状态为 {status}，不得作为成功证据。"})
        reviews.append({"stage": stage, "stage_name": RNASEQ_STAGE_NAMES.get(stage, stage), "limitations": limitations})

    successful = [stage for stage in stages if str(manifests[stage].get("status") or "") in SUCCESS_STATUSES]
    return {
        "schema": "rnaseq_manifest_evidence_v1",
        "metadata": {
            "prefix": prefix,
            "rnaseq_scope_id": selection.rnaseq_scope_id,
            "discovered_scope_ids": list(selection.discovered_scope_ids),
            "included_stages": stages,
            "successful_stages": successful,
            "interpretation_boundary": "仅陈述当前 prefix 与 RNA-seq scope 下 manifest/产物支持的事实；富集、相关性和候选排序不得写成调控因果。",
        },
        "stage_flow": {"visualization": " -> ".join(stages), "stages": stage_flow},
        "key_findings": findings,
        "stage_reviews": reviews,
        "evidence_cards": cards,
        "risk_flags": risks,
        "major_claims": [],
        "biological_story": (
            f"当前报告汇总了 {len(successful)} 个成功或部分成功的 RNA-seq 阶段。"
            "跨阶段生物学解释必须以各阶段数值、图表和风险标记为边界。"
        ),
        "next_steps": ["补齐缺失或失败阶段后重新生成报告，以刷新证据快照。"],
    }
