"""Evidence-bound RNA-seq result report generation.

This module deliberately keeps the formal report independent from the live
frontend interpretation cache.  It reuses the same pure evidence builder, but
materializes a deterministic Markdown report and provenance artifacts under a
job's ``final/`` directory.
"""
from __future__ import annotations

import base64
import hashlib
import ast
import csv
import html
import json
import math
import mimetypes
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote

from gfa_core.rnaseq_evidence import (
    RNASEQ_STAGE_NAMES,
    build_rnaseq_evidence_snapshot,
    resolve_artifact_path,
    manifest_identity_value,
    select_rnaseq_manifests,
)


REPORT_STAGE = "rnaseq_report"
REPORT_RENDERER_VERSION = "17"
SUCCESS_STATUSES = {"success", "partial_success"}
DEFAULT_EXPECTED_STAGES = [
    "rnaseq_qc",
    "rnaseq_align",
    "rnaseq_quant",
    "rnaseq_sample_qc",
    "rnaseq_de",
]
PREFERRED_STAGE_ORDER = [
    "rnaseq_qc",
    "rnaseq_align",
    "rnaseq_quant",
    "rnaseq_sample_qc",
    "rnaseq_de",
    "annotation",
    "annotation_go",
    "annotation_kegg",
    "rnaseq_go_enrich",
    "rnaseq_kegg_enrich",
    "rnaseq_enrich",
    "rnaseq_fgsea",
    "rnaseq_wgcna",
    "rnaseq_candidate_family",
    "rnaseq_family_score",
]
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
STAGE_NAMES = RNASEQ_STAGE_NAMES


class NoSuccessfulRNASeqEvidence(RuntimeError):
    """Raised when a report would otherwise be empty."""


@dataclass(frozen=True)
class RNASeqReportResult:
    report_path: Path
    evidence_path: Path
    model_path: Path
    manifest_path: Path
    coverage_status: str
    included_stages: tuple[str, ...]
    rnaseq_scope_id: str
    reused: bool = False


BEIJING_TIMEZONE = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(BEIJING_TIMEZONE).isoformat(timespec="seconds")


def _display_generated_at(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BEIJING_TIMEZONE)
        return parsed.astimezone(BEIJING_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(value or "").replace("T", " ")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _identity(manifest: dict[str, Any], key: str) -> str:
    return manifest_identity_value(manifest, key)


def is_rnaseq_report_source(stage: str) -> bool:
    stage = str(stage or "").strip()
    return stage != REPORT_STAGE and (
        stage.startswith("rnaseq_") or stage in {"annotation", "annotation_go", "annotation_kegg"}
    )


def _stage_sort_key(stage: str) -> tuple[int, str]:
    try:
        return (PREFERRED_STAGE_ORDER.index(stage), stage)
    except ValueError:
        return (len(PREFERRED_STAGE_ORDER), stage)


def _normalize_expected_stages(stages: Iterable[str] | None) -> list[str]:
    raw = list(stages) if stages is not None else list(DEFAULT_EXPECTED_STAGES)
    result: list[str] = []
    for stage in raw:
        value = str(stage or "").strip()
        if value and is_rnaseq_report_source(value) and value not in result:
            result.append(value)
    return result


def _source_selection(
    final_dir: Path,
    prefix: str,
    rnaseq_scope_id: str,
) -> tuple[str, list[str], dict[str, dict[str, Any]]]:
    selection = select_rnaseq_manifests(
        final_dir,
        prefix,
        rnaseq_scope_id=rnaseq_scope_id,
    )
    return selection.rnaseq_scope_id, list(selection.discovered_scope_ids), selection.manifests


def _source_records(manifests: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for stage in sorted(manifests, key=_stage_sort_key):
        manifest = manifests[stage]
        path = Path(str(manifest.get("_manifest_path") or ""))
        records.append(
            {
                "stage": stage,
                "status": str(manifest.get("status") or "unknown"),
                "rnaseq_scope_id": _identity(manifest, "rnaseq_scope_id"),
                "path": str(path),
                "sha256": _sha256(path) if path.is_file() else "",
                "mtime": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
                if path.is_file()
                else "",
            }
        )
    return records


def _fingerprint(
    records: list[dict[str, Any]], expected_stages: list[str], use_model: bool
) -> tuple[str, str]:
    source_payload = json.dumps(records, ensure_ascii=False, sort_keys=True).encode("utf-8")
    source_fingerprint = hashlib.sha256(source_payload).hexdigest()
    request_payload = json.dumps(
        {
            "source_fingerprint": source_fingerprint,
            "expected_stages": expected_stages,
            "use_model": use_model,
            "report_renderer_version": REPORT_RENDERER_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return source_fingerprint, hashlib.sha256(request_payload).hexdigest()


def _artifact_path(workspace: Path, manifest: dict[str, Any], item: dict[str, Any]) -> Path | None:
    return resolve_artifact_path(workspace / "final", item, require_exists=True)


def _enrichment_figure_identity(stage: str, path: Path) -> tuple[str, str, str] | None:
    name = path.name
    lower = name.lower()
    comparison = ""
    category = ""
    pathway_id = ""
    if stage == "rnaseq_go_enrich" or (stage == "rnaseq_enrich" and ".go_enrich" in lower):
        match = re.search(r"rnaseq_(?:go_enrich|enrich)\.(.+?)\.go_enrich", name, flags=re.IGNORECASE)
        comparison = match.group(1) if match else ""
        if ".go_enrich.ontology_barplot." in lower:
            category = "go_ontology"
        elif ".go_enrich.barplot." in lower:
            category = "go_bar"
        elif ".go_enrich.dotplot." in lower:
            category = "go_dot"
        elif re.search(r"\.go_enrich\.svg$", lower):
            category = "go_bar"
    elif stage == "rnaseq_kegg_enrich" or (stage == "rnaseq_enrich" and "kegg" in lower):
        match = re.search(r"rnaseq_(?:kegg_enrich|enrich)\.(.+?)(?:\.kegg|_kegg)", name, flags=re.IGNORECASE)
        comparison = match.group(1) if match else ""
        pathway_match = re.search(r"\.kegg_pathway_map\.(ko\d+)\.", lower)
        if pathway_match:
            category = "kegg_pathway"
            pathway_id = pathway_match.group(1)
        elif re.search(r"(?:\.|_)kegg_enrich\.(?:png|jpe?g|webp|svg|pdf)$", lower):
            category = "kegg_bar"
        elif re.search(r"_kegg_dotplot\.(?:png|jpe?g|webp|svg|pdf)$", lower):
            category = "kegg_dot"
    elif stage == "rnaseq_fgsea":
        match = re.search(r"rnaseq_fgsea\.(.+?)\.fgsea", name, flags=re.IGNORECASE)
        comparison = match.group(1) if match else ""
        if ".fgsea_kegg_dotplot." in lower:
            category = "fgsea_dot"
        elif ".fgsea_kegg_top_enrichment." in lower:
            category = "fgsea_curve"
    if not category:
        return None
    identity = f"{category}:{comparison}:{pathway_id}" if category == "kegg_pathway" else category
    return identity, comparison, pathway_id


def _artifact_comparison(stage: str, path: Path, category: str = "") -> str:
    patterns = {
        "rnaseq_go_enrich": r"rnaseq_go_enrich\.(.+?)\.go_enrich",
        "rnaseq_kegg_enrich": r"rnaseq_kegg_enrich\.(.+?)(?:\.kegg|_kegg)",
        "rnaseq_fgsea": r"rnaseq_fgsea\.(.+?)\.fgsea",
    }
    if stage == "rnaseq_enrich":
        pattern = (
            r"rnaseq_enrich\.(.+?)\.go_enrich"
            if category.startswith("go_")
            else r"rnaseq_enrich\.(.+?)(?:\.kegg|_kegg)"
        )
    else:
        pattern = patterns.get(stage)
    match = re.search(pattern, path.name, flags=re.IGNORECASE) if pattern else None
    return match.group(1) if match else ""


def _table_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            delimiter = "," if path.suffix.lower() == ".csv" else "\t"
            return [
                {str(key): str(value or "") for key, value in row.items() if key is not None}
                for row in csv.DictReader(handle, delimiter=delimiter)
            ]
    except OSError:
        return []


def _supporting_tables(
    workspace: Path,
    manifest: dict[str, Any],
    comparison: str,
    category: str = "",
) -> list[tuple[Path, list[dict[str, str]]]]:
    tables: list[tuple[Path, list[dict[str, str]]]] = []
    for item in (manifest.get("artifacts") or {}).values():
        if not isinstance(item, dict):
            continue
        path = _artifact_path(workspace, manifest, item)
        if (
            not path
            or not path.is_file()
            or path.suffix.lower() not in {".txt", ".tsv", ".csv"}
            or (
                comparison
                and _artifact_comparison(str(manifest.get("stage") or ""), path, category) != comparison
            )
        ):
            continue
        lower_name = path.name.lower()
        if category.startswith("go_") and not lower_name.endswith(".go_enrich.txt"):
            continue
        if category in {"kegg_bar", "kegg_dot"} and not lower_name.endswith(".kegg_enrich.txt"):
            continue
        if category == "kegg_pathway" and not (
            lower_name.endswith(".kegg_enrich.txt")
            or ".kegg_pathway_gene_hits." in lower_name
            or ".kegg_pathway_mapper." in lower_name
            or ".kegg_pathway_map.ko_log2fc." in lower_name
        ):
            continue
        if category.startswith("fgsea_") and not lower_name.endswith(".fgsea_kegg.tsv"):
            continue
        rows = _table_rows(path)
        if rows:
            tables.append((path, rows))
    return tables


def _best_adjusted_p(tables: list[tuple[Path, list[dict[str, str]]]]) -> float:
    values: list[float] = []
    for _, rows in tables:
        for row in rows:
            raw = row.get("p.adjust") or row.get("padj") or row.get("FDR") or ""
            try:
                value = float(raw)
                if math.isfinite(value) and 0.0 <= value <= 1.0:
                    values.append(value)
            except (TypeError, ValueError):
                continue
    return min(values, default=float("inf"))


def _fgsea_selection_score(tables: list[tuple[Path, list[dict[str, str]]]]) -> tuple[float, float]:
    scores: list[tuple[float, float]] = []
    for _, rows in tables:
        for row in rows:
            try:
                padj = float(row.get("padj") or row.get("p.adjust") or "")
                nes = float(row.get("NES") or "")
            except (TypeError, ValueError):
                continue
            if math.isfinite(padj) and 0.0 <= padj <= 1.0 and math.isfinite(nes):
                scores.append((padj, -abs(nes)))
    return min(scores, default=(float("inf"), float("inf")))


def _support_labels(value: str) -> list[str]:
    """Parse both the real semicolon serialization and legacy list encodings."""
    raw = str(value or "").strip()
    if not raw:
        return []
    if raw[:1] in {"[", "("}:
        try:
            parsed = json.loads(raw) if raw.startswith("[") else ast.literal_eval(raw)
        except (ValueError, SyntaxError, json.JSONDecodeError):
            try:
                parsed = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                parsed = None
        if isinstance(parsed, (list, tuple)):
            return list(dict.fromkeys(str(item).strip() for item in parsed if str(item).strip()))
    return list(dict.fromkeys(item.strip() for item in raw.split(";") if item.strip()))


def _support_identity(label: str) -> tuple[str, str]:
    raw = str(label or "").strip()
    go_match = re.match(r"(?i)^GO:(GO:\d+)(?::.*)?$", raw)
    if go_match:
        return "go", go_match.group(1)
    match = re.match(r"(?i)^(KEGG|fgsea):([^:]+)(?::.*)?$", raw)
    if match:
        return match.group(1).lower(), match.group(2).lower()
    return "", ""


def _candidate_rows(workspace: Path, manifests: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    manifest = manifests.get("rnaseq_family_score") or {}
    item = (manifest.get("artifacts") or {}).get("analysis_ready_family")
    if not isinstance(item, dict):
        return []
    path = _artifact_path(workspace, manifest, item)
    if not path or not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(_table_rows(path), 1):
        family = str(row.get("family_id") or row.get("family_name") or "").strip()
        comparison = str(row.get("best_comparison") or "").strip()
        if not family or not comparison:
            continue
        try:
            rank = int(str(row.get("rank") or index))
        except ValueError:
            rank = index
        rows.append(
            {
                "family": family,
                "family_name": str(row.get("family_name") or "").strip(),
                "rank": rank,
                "comparison": comparison,
                "support_labels": _support_labels(str(row.get("best_support_labels") or "")),
                "source_table": str(path),
            }
        )
    return sorted(rows, key=lambda row: (row["rank"], row["family"]))


def _pathway_adjusted_p(
    tables: list[tuple[Path, list[dict[str, str]]]], pathway_id: str
) -> float:
    row = _first_matching_row(tables, pathway_id=pathway_id)
    try:
        value = float(row.get("p.adjust") or row.get("padj") or row.get("FDR") or "")
    except (TypeError, ValueError):
        return float("inf")
    return value if math.isfinite(value) and 0.0 <= value <= 1.0 else float("inf")


def _figures(
    workspace: Path, manifests: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for stage in sorted(manifests, key=_stage_sort_key):
        artifacts = manifests[stage].get("artifacts") or {}
        if not isinstance(artifacts, dict):
            continue
        for key, item in sorted(artifacts.items()):
            if not isinstance(item, dict):
                continue
            path = _artifact_path(workspace, manifests[stage], item)
            if not path or not path.is_file():
                continue
            classified = _enrichment_figure_identity(stage, path)
            if path.suffix.lower() not in IMAGE_SUFFIXES and not (path.suffix.lower() == ".pdf" and classified):
                continue
            identity = str(path.resolve())
            if identity in seen:
                continue
            seen.add(identity)
            row = {"stage": stage, "artifact": str(key), "path": str(path)}
            if classified:
                identity, comparison, pathway_id = classified
                row.update(
                    {
                        "selection_identity": identity,
                        "category": identity.split(":", 1)[0],
                        "comparison": comparison,
                        "pathway_id": pathway_id,
                    }
                )
            candidates.append(row)

    selected: list[dict[str, Any]] = []
    enrichment_candidates: list[dict[str, Any]] = []
    for row in candidates:
        if not row.get("selection_identity"):
            row.update(
                candidate_families=[], support_labels=[], selection_reason="all_stage_figures",
                source_tables=[],
            )
            selected.append(row)
        else:
            enrichment_candidates.append(row)

    formal_candidates = _candidate_rows(workspace, manifests)
    format_rank = {".png": 0, ".jpg": 1, ".jpeg": 1, ".webp": 2, ".svg": 3, ".pdf": 4}
    table_cache: dict[tuple[str, str, str], list[tuple[Path, list[dict[str, str]]]]] = {}

    def tables_for(row: dict[str, Any]) -> list[tuple[Path, list[dict[str, str]]]]:
        cache_key = (row["stage"], row.get("comparison", ""), row.get("category", ""))
        if cache_key not in table_cache:
            table_cache[cache_key] = _supporting_tables(
                workspace, manifests[row["stage"]], cache_key[1], cache_key[2]
            )
        return table_cache[cache_key]

    def table_contains(category: str, identifier: str, row: dict[str, Any]) -> bool:
        target_identifier = identifier.lower()
        if category == "kegg_pathway":
            return True
        tables = tables_for(row)
        if category == "fgsea_curve":
            top = _best_fgsea_row(tables)
            top_identifier = str(top.get("pathway") or top.get("PathwayID") or top.get("ID") or "")
            return bool(top_identifier) and top_identifier.lower() == target_identifier
        for _, rows in tables:
            for table_row in rows:
                value = str(
                    table_row.get("ID")
                    or table_row.get("PathwayID")
                    or table_row.get("pathway")
                    or ""
                )
                if value.lower() == target_identifier:
                    return True
        return False

    def row_support(row: dict[str, Any]) -> tuple[list[str], list[str], int, list[str]]:
        category = row.get("category", "")
        wanted_kind = (
            "go" if category.startswith("go_") else "fgsea" if category.startswith("fgsea_") else "kegg"
        )
        pathway_id = str(row.get("pathway_id") or "").lower()
        matched: list[tuple[int, str, str, str]] = []
        for candidate in formal_candidates:
            if candidate["comparison"] != row.get("comparison"):
                continue
            for label in candidate["support_labels"]:
                kind, identifier = _support_identity(label)
                if (
                    kind != wanted_kind
                    or (category == "kegg_pathway" and identifier != pathway_id)
                    or not table_contains(category, identifier, row)
                ):
                    continue
                matched.append(
                    (candidate["rank"], candidate["family"], label, candidate["source_table"])
                )
        matched.sort(key=lambda value: (value[0], value[1], value[2]))
        return (
            list(dict.fromkeys(value[1] for value in matched)),
            list(dict.fromkeys(value[2] for value in matched)),
            matched[0][0] if matched else 10**9,
            list(dict.fromkeys(value[3] for value in matched)),
        )

    def evidence_score(row: dict[str, Any]) -> tuple[Any, ...]:
        tables = tables_for(row)
        if row.get("category") == "fgsea_curve":
            evidence: tuple[Any, ...] = _fgsea_selection_score(tables)
        elif row.get("category") == "kegg_pathway":
            evidence = (_pathway_adjusted_p(tables, row.get("pathway_id", "")),)
        else:
            evidence = (_best_adjusted_p(tables),)
        return (*evidence, format_rank.get(Path(row["path"]).suffix.lower(), 9), Path(row["path"]).name)

    for row in enrichment_candidates:
        families, labels, candidate_rank, candidate_tables = row_support(row)
        row["candidate_families"] = families
        row["support_labels"] = labels
        row["candidate_rank"] = candidate_rank
        row["source_tables"] = candidate_tables + [str(path) for path, _ in tables_for(row)]

    selection_mode = (
        "candidate_guided"
        if any(row.get("support_labels") for row in enrichment_candidates)
        else "representative_fallback"
    )
    if selection_mode == "representative_fallback":
        for row in enrichment_candidates:
            row["candidate_families"] = []
            row["support_labels"] = []
            row["candidate_rank"] = 10**9
            row["source_tables"] = [str(path) for path, _ in tables_for(row)]

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in enrichment_candidates:
        category = row.get("category", "")
        if selection_mode == "candidate_guided" and not row.get("support_labels"):
            continue
        key = (
            f"{category}:{row.get('comparison', '')}:{row.get('pathway_id', '')}"
            if selection_mode == "candidate_guided" and category == "kegg_pathway"
            else f"{category}:{row.get('pathway_id', '')}"
            if category == "kegg_pathway"
            else category
        )
        groups.setdefault(key, []).append(row)

    for rows in groups.values():
        rows.sort(
            key=lambda row: (
                row.get("candidate_rank", 10**9) if selection_mode == "candidate_guided" else 0,
                *evidence_score(row),
            )
        )
        chosen = rows[0]
        chosen["selection_reason"] = (
            "candidate_family_support" if selection_mode == "candidate_guided" else "representative_fallback"
        )
        selected.append(chosen)

    selected_paths = {row["path"] for row in selected}
    omitted: list[dict[str, Any]] = []
    for row in enrichment_candidates:
        if row["path"] in selected_paths:
            continue
        row.setdefault("candidate_families", [])
        row.setdefault("support_labels", [])
        row.setdefault("source_tables", [])
        row["selection_reason"] = (
            "not_candidate_supported"
            if selection_mode == "candidate_guided" and not row["support_labels"]
            else "lower_ranked_representative"
        )
        omitted.append(row)
    stage_rank = {stage: index for index, stage in enumerate(PREFERRED_STAGE_ORDER)}
    ordered = sorted(
        selected,
        key=lambda row: (stage_rank.get(row["stage"], len(stage_rank)), Path(row["path"]).name),
    )
    omitted.sort(key=lambda row: (stage_rank.get(row["stage"], len(stage_rank)), Path(row["path"]).name))
    return ordered, omitted, selection_mode


def _first_matching_row(
    tables: list[tuple[Path, list[dict[str, str]]]],
    *,
    pathway_id: str = "",
) -> dict[str, str]:
    rows = [row for _, table_rows in tables for row in table_rows]
    if pathway_id:
        for row in rows:
            identifier = row.get("ID") or row.get("PathwayID") or row.get("pathway") or ""
            if identifier.lower() == pathway_id.lower():
                return row
        return {}
    return rows[0] if rows else {}


def _best_fgsea_row(tables: list[tuple[Path, list[dict[str, str]]]]) -> dict[str, str]:
    ranked: list[tuple[float, float, int, dict[str, str]]] = []
    for _, rows in tables:
        for index, row in enumerate(rows):
            try:
                padj = float(row.get("padj") or row.get("p.adjust") or "")
                nes = float(row.get("NES") or "")
            except (TypeError, ValueError):
                continue
            if math.isfinite(padj) and 0.0 <= padj <= 1.0 and math.isfinite(nes):
                ranked.append((padj, -abs(nes), index, row))
    return min(ranked, default=(float("inf"), float("inf"), 0, {}))[-1]


def _value(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return "未登记"


def _probability_value(row: dict[str, str], *keys: str) -> str:
    raw = _value(row, *keys)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return "未登记"
    return raw if math.isfinite(value) and 0.0 <= value <= 1.0 else "未登记"


def _field_items(row: dict[str, str], *keys: str) -> set[str]:
    value = next((str(row.get(key) or "").strip() for key in keys if str(row.get(key) or "").strip()), "")
    return {item for item in re.split(r"[/,;\s]+", value) if item}


def _ordered_field_items(row: dict[str, str], *keys: str) -> list[str]:
    value = next((str(row.get(key) or "").strip() for key in keys if str(row.get(key) or "").strip()), "")
    return list(dict.fromkeys(item for item in re.split(r"[/,;\s]+", value) if item))


def _item_summary(label: str, items: set[str]) -> str:
    ordered = sorted(items)
    if not ordered:
        return ""
    preview = "/".join(ordered[:5])
    return f"{label}总数={len(ordered)}，前5项={preview}"


def _source_table_links(report_path: Path, tables: list[tuple[Path, list[dict[str, str]]]], markers: tuple[str, ...]) -> str:
    matched = [path for path, _ in tables if any(marker in path.name.lower() for marker in markers)]
    links = [
        f"[下载完整数据{' ' + str(index) if len(matched) > 1 else ''}]({_relative_link(report_path, report_path.parent / path.name)})"
        for index, path in enumerate(matched, start=1)
    ]
    return "；" + "、".join(links) if links else ""


def _figure_interpretation(
    figure: dict[str, str], workspace: Path, manifests: dict[str, dict[str, Any]], report_path: Path
) -> list[str]:
    category = figure.get("category", "")
    comparison = _figure_comparison(figure)
    if not category:
        figure_type = _figure_display_type(figure)
        meanings = {
            "pca": "PCA图概括样本整体表达谱的主要差异及样本间距离。",
            "sample_clustering": "样本聚类图按整体表达相似性展示样本之间的层次关系。",
            "sample_correlation": "样本相关性热图展示任意两个样本表达谱的一致程度。",
            "sample_distance": "样本距离热图展示样本之间整体表达差异的大小。",
            "volcano": "火山图同时展示差异基因的变化幅度和统计显著性。",
            "ma_plot": "MA图展示平均表达量与表达变化幅度之间的关系。",
            "heatmap": "热图展示代表基因在不同样本中的相对表达模式。",
            "upset": "UpSet图展示多个比较组差异基因集合的交集关系。",
            "venn": "Venn图展示不同比较组差异基因集合的共有和特有部分。",
            "module_trait": "模块性状相关图展示共表达模块与样本性状之间的相关性。",
            "network": "网络图展示候选基因或共表达模块成员之间的连接关系。",
            "tree": "聚类树展示基因或样本按相似性形成的层次结构。",
            "boxplot": "箱线图展示不同样本或分组中数值的分布范围。",
            "density": "密度图展示各样本表达量的整体分布形态。",
            "other": "结果图展示该分析阶段的主要数据分布或分组关系。",
        }
        readings = {
            "pca": "查看同组重复是否聚集、不同组是否分离，并留意明显偏离的样本。",
            "sample_clustering": "查看同组样本是否优先聚为一支，并关注聚类距离较远的重复。",
            "sample_correlation": "结合颜色图例读取相关系数，颜色越接近高值表示样本越相似。",
            "sample_distance": "结合颜色图例比较样本距离，重点查看组内重复是否小于组间距离。",
            "volcano": "横轴表示log2FoldChange，纵轴表示显著性，结合颜色区分上调、下调和不显著基因。",
            "ma_plot": "横轴表示平均表达水平，纵轴表示log2FoldChange，重点查看显著基因的变化方向。",
            "heatmap": "按颜色图例读取相对高低表达，并比较样本聚类与分组是否一致。",
            "upset": "先看各集合大小，再读取连接点对应的交集柱高。",
            "venn": "读取重叠区域和非重叠区域的基因数量。",
            "module_trait": "结合颜色和相关系数读取正负相关，并同时关注显著性标记。",
            "network": "结合节点、连线和图例查看关键节点及连接较紧密的区域。",
            "tree": "分支越近表示相似性越高，重点查看分组或模块划分位置。",
            "boxplot": "比较中位数、四分位范围和离群点，并查看组间分布差异。",
            "density": "比较曲线峰值和整体形状，检查样本分布是否大体一致。",
            "other": "结合图题、坐标轴、图例和分组标签读取主要结果。",
        }
        metrics = manifests.get(str(figure.get("stage") or ""), {}).get("metrics") or {}
        if figure_type in {"volcano", "ma_plot"}:
            comparison_metrics = (metrics.get("comparisons") or {}).get(comparison, {})
            up_count = _value(comparison_metrics, "up")
            down_count = _value(comparison_metrics, "down")
            total_count = _value(comparison_metrics, "total")
            result = (
                f"比较组 `{comparison or '未识别'}` 的差异基因分布见图；"
                f"上调基因数={up_count}，下调基因数={down_count}，"
                f"显著差异基因总数={total_count}。"
            )
        elif figure_type in {"pca", "sample_clustering", "sample_correlation", "sample_distance"}:
            result = "本次样本的组内重复关系和组间差异见图，具体以图中的样本标签、距离或颜色数值为准。"
        elif comparison:
            result = f"比较组 `{comparison}` 的主要结果见图，具体数值以坐标轴、图例和标注为准。"
        else:
            result = "本次分析的主要结果见图，具体数值以坐标轴、图例和标注为准。"
        return [
            f"图形含义：{meanings.get(figure_type, meanings['other'])}",
            f"阅读方法：{readings.get(figure_type, readings['other'])}",
            f"本次结果：{result}",
        ]
    tables = _supporting_tables(workspace, manifests[figure["stage"]], comparison, category)
    pathway_id = figure.get("pathway_id", "")
    row = (
        _best_fgsea_row(tables)
        if category.startswith("fgsea_")
        else _first_matching_row(tables, pathway_id=pathway_id)
    )
    if not row:
        result = f"比较组 `{comparison or '未识别'}` 证据不足：缺少可匹配的数值表，无法可靠判断本次数据的具体趋势。"
    elif category == "go_ontology":
        ontology_rows: dict[str, dict[str, str]] = {}
        for _, table_rows in tables:
            for candidate in table_rows:
                ontology_value = str(candidate.get("Ontology") or "").strip().lower()
                ontology = {
                    "bp": "BP",
                    "biological process": "BP",
                    "cc": "CC",
                    "cellular component": "CC",
                    "mf": "MF",
                    "molecular function": "MF",
                }.get(ontology_value, "")
                if ontology in {"BP", "CC", "MF"} and ontology not in ontology_rows:
                    ontology_rows[ontology] = candidate
        summaries = [
            f"{ontology}={_value(ontology_rows[ontology], 'Description')}"
            for ontology in ("BP", "CC", "MF")
            if ontology in ontology_rows
        ]
        result = (
            f"比较组 `{comparison}` 的三类本体代表条目为"
            f"{'；'.join(summaries) if summaries else '未登记'}。"
        )
    elif category.startswith("go_"):
        result = (
            f"比较组 `{comparison}` 的代表条目为 {_value(row, 'ID')}（{_value(row, 'Description')}），"
            f"Ontology={_value(row, 'Ontology')}，GeneRatio={_value(row, 'GeneRatio')}，"
            f"Count={_value(row, 'Count')}，p.adjust={_probability_value(row, 'p.adjust')}。"
        )
    elif category in {"kegg_bar", "kegg_dot"}:
        genes = _field_items(row, "geneID", "Genes")
        kos = set(re.findall(r"K\d+", " ".join(str(row.get(key) or "") for key in ("KO", "KOs", "ko"))))
        details = [
            f"GeneRatio={_value(row, 'GeneRatio')}",
            f"Count={_value(row, 'Count')}",
            f"p.adjust={_probability_value(row, 'p.adjust', 'FDR')}",
        ]
        details.extend(summary for summary in (_item_summary('命中基因', genes), _item_summary('KO', kos)) if summary)
        result = (
            f"比较组 `{comparison}` 的代表通路为 {_value(row, 'ID')}（{_value(row, 'Description')}），"
            f"{'，'.join(details)}"
            f"{_source_table_links(report_path, tables, ('.kegg_enrich.txt',))}。"
        )
    elif category == "kegg_pathway":
        pathway_genes: set[str] = set()
        pathway_kos: set[str] = set()
        for _, table_rows in tables:
            for candidate in table_rows:
                identifier = candidate.get("PathwayID") or candidate.get("ID") or candidate.get("pathway") or ""
                if identifier.lower() == pathway_id.lower():
                    pathway_genes.update(
                        gene
                        for gene in re.split(r"[/,;\s]+", candidate.get("Genes") or candidate.get("geneID") or "")
                        if gene
                    )
                    pathway_kos.update(re.findall(r"K\d+", candidate.get("KO") or ""))
        if not pathway_genes:
            pathway_genes.update(
                gene for gene in re.split(r"[/,;\s]+", _value(row, "Genes", "geneID")) if gene != "未登记"
            )
        ko_changes: list[float] = []
        if pathway_kos:
            for table_path, table_rows in tables:
                if "ko_log2fc" not in table_path.name.lower():
                    continue
                for candidate in table_rows:
                    ko = candidate.get("KO") or candidate.get("ko") or ""
                    if ko not in pathway_kos:
                        continue
                    try:
                        ko_changes.append(float(candidate.get("log2FoldChange") or candidate.get("log2FC") or ""))
                    except (TypeError, ValueError):
                        continue
        direction = ""
        if ko_changes:
            direction = f"，KO变化中上调{sum(value > 0 for value in ko_changes)}个、下调{sum(value < 0 for value in ko_changes)}个"
        elif not pathway_kos:
            direction = "，KO对应关系证据不足，未汇总KO表达方向"
        details = [f"p.adjust={_probability_value(row, 'p.adjust', 'padj', 'FDR')}"]
        details.extend(
            summary for summary in (_item_summary('命中基因', pathway_genes), _item_summary('KO', pathway_kos)) if summary
        )
        result = (
            f"比较组 `{comparison}` 的通路 {pathway_id}（{_value(row, 'PathwayName', 'Description', 'pathway_name')}），"
            f"{'，'.join(details)}{direction}"
            f"{_source_table_links(report_path, tables, ('kegg_pathway_gene_hits', 'ko_log2fc'))}。"
        )
    else:
        leading_edge = _ordered_field_items(row, "leadingEdge")
        details = [
            f"NES={_value(row, 'NES')}",
            f"padj={_probability_value(row, 'padj', 'p.adjust')}",
        ]
        if leading_edge:
            details.append(f"leadingEdge总数={len(leading_edge)}，前5项={'/'.join(leading_edge[:5])}")
        result = (
            f"比较组 `{comparison}` 的通路 {_value(row, 'pathway', 'ID')}（{_value(row, 'pathway_name', 'Description')}），"
            f"{'，'.join(details)}"
            f"{_source_table_links(report_path, tables, ('.fgsea_kegg.tsv',))}。"
        )

    meanings = {
        "go_bar": "GO富集条形图概括显著功能条目及其富集强度。",
        "go_dot": "GO富集气泡图同时呈现功能条目、富集比例、基因数和显著性。",
        "go_ontology": "GO本体分面图并列展示BP、CC和MF三类功能条目。",
        "kegg_bar": "KEGG富集条形图概括显著通路及其富集比例。",
        "kegg_dot": "KEGG富集气泡图同时呈现通路富集比例、命中基因数和显著性。",
        "kegg_pathway": "KEGG Pathway图将该比较组的KO表达变化映射到具体通路节点。",
        "fgsea_dot": "FGSEA气泡图汇总基于全基因排序的KEGG通路NES及显著性。",
        "fgsea_curve": "FGSEA富集曲线展示代表通路成员在全基因排序中的累积富集位置。",
    }
    readings = {
        "go_bar": "结合条目名称、柱长、Count及校正后P值阅读。",
        "go_dot": "结合横轴富集比例、点大小所示Count及颜色所示显著性阅读。",
        "go_ontology": "分别比较BP、CC和MF分面中的条目、比例和显著性。",
        "kegg_bar": "结合通路名称、柱长、GeneRatio、Count及校正后P值阅读。",
        "kegg_dot": "结合GeneRatio、点大小所示Count及颜色所示显著性阅读。",
        "kegg_pathway": "按图例读取节点颜色，并结合命中基因、KO及log2FoldChange表判断变化方向。",
        "fgsea_dot": "NES正负表示富集方向，点大小和颜色需结合size与padj阅读。",
        "fgsea_curve": "结合运行富集分数、成员命中位置和leadingEdge基因阅读。",
    }
    boundary = (
        "富集结果表示统计关联，不足以单独证明功能或通路被激活、抑制或具有因果调控关系。"
        if not category.startswith("fgsea_")
        else "FGSEA基于全基因排序，不能与仅使用显著差异基因的ORA结果混为一谈，也不证明因果关系。"
    )
    return [
        f"图形含义：{meanings[category]}",
        f"阅读方法：{readings[category]}",
        f"本次结果：{result}",
    ]


def _relative_link(report_path: Path, target: Path) -> str:
    try:
        return os.path.relpath(target, report_path.parent).replace(os.sep, "/")
    except ValueError:
        return str(target)


def _table_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "/").replace("\r", " ").replace("\n", " ").strip()


def _display_metrics(manifest: dict[str, Any], max_metrics: int = 12) -> list[tuple[str, str]]:
    labels = {
        "total_samples": "样本数",
        "sample_count": "样本数",
        "total_raw_reads": "原始 reads 总数",
        "total_clean_reads": "过滤后 reads 总数",
        "tested_genes": "参与检验的基因数",
        "significant_degs": "显著差异基因数",
        "up_genes": "上调基因数",
        "down_genes": "下调基因数",
    }
    hidden_terms = ("fingerprint", "inventory", "renderer", "source_manifest", "request_", "omitted_")
    rows: list[tuple[str, str]] = []
    for key, value in (manifest.get("metrics") or {}).items():
        lowered = str(key).lower()
        if any(term in lowered for term in hidden_terms):
            continue
        if value is None or isinstance(value, (dict, list, tuple, set)):
            continue
        if isinstance(value, bool):
            display = "是" if value else "否"
        elif isinstance(value, int):
            display = f"{value:,}"
        elif isinstance(value, float):
            if not math.isfinite(value):
                continue
            display = f"{value:,.4g}"
        else:
            display = str(value).strip()
            if not display or len(display) > 160 or "samples={" in display.lower():
                continue
        label = labels.get(lowered, str(key).replace("_", " "))
        rows.append((_table_cell(label), _table_cell(display)))
        if len(rows) >= max_metrics:
            break
    return rows


def _preferred_web_figure(path: Path) -> Path:
    for suffix in (".png", ".webp", ".jpg", ".jpeg", ".svg"):
        candidate = path.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return path


def _sample_metric_table(manifest: dict[str, Any], stage: str, max_columns: int = 8) -> dict[str, Any] | None:
    samples = (manifest.get("metrics") or {}).get("samples")
    if not isinstance(samples, dict) or not samples:
        return None
    available: set[str] = set()
    for values in samples.values():
        if isinstance(values, dict):
            available.update(
                str(key) for key, value in values.items()
                if value is None or isinstance(value, (str, int, float, bool))
            )
    priorities = {
        "rnaseq_qc": ("raw_reads", "clean_reads", "q20", "q30", "gc", "adapter", "low_quality", "duplication"),
        "rnaseq_align": ("total_reads", "alignment_rate", "overall_alignment", "unique", "multiple", "unmapped"),
        "rnaseq_quant": ("assigned_reads", "total_counted_reads", "assigned_percent", "detected_genes", "mapped_reads"),
    }.get(stage, ())
    columns: list[str] = []
    for token in priorities:
        for key in sorted(available):
            if key not in columns and token in key.lower():
                columns.append(key)
    columns.extend(key for key in sorted(available) if key not in columns)
    columns = columns[:max_columns]
    if not columns:
        return None
    rows = []
    for sample_id, values in sorted(samples.items()):
        values = values if isinstance(values, dict) else {}
        rows.append([_table_cell(sample_id), *[_table_cell(values.get(column, "")) for column in columns]])
    return {
        "columns": ["sample_id", *[column.replace("_", " ") for column in columns]],
        "rows": rows,
    }


def _figure_display_type(figure: dict[str, Any]) -> str:
    category = str(figure.get("category") or "").strip().lower()
    if category:
        return category
    name = f"{figure.get('artifact', '')} {Path(str(figure.get('path') or '')).stem}".lower()
    types = (
        ("sample_correlation", ("correlation",)),
        ("sample_distance", ("sample_distance", "distance_heatmap")),
        ("sample_clustering", ("sample_clustering", "cluster")),
        ("pca", ("pca",)),
        ("volcano", ("volcano",)),
        ("ma_plot", ("ma_plot", "maplot")),
        ("heatmap", ("heatmap",)),
        ("upset", ("upset",)),
        ("venn", ("venn",)),
        ("boxplot", ("boxplot",)),
        ("density", ("density",)),
        ("module_trait", ("module_trait",)),
        ("network", ("network",)),
        ("sample_tree", ("sample_tree",)),
        ("gene_dendrogram", ("gene_dendrogram", "dendrogram_modules")),
        ("tree", ("dendrogram", "tree")),
    )
    for figure_type, tokens in types:
        if any(token in name for token in tokens):
            return figure_type
    return "other"


def _figure_comparison(figure: dict[str, Any]) -> str:
    explicit = str(figure.get("comparison") or "").strip()
    if explicit:
        return explicit
    name = Path(str(figure.get("path") or "")).stem
    lowered = name.lower()
    marker = "rnaseq_de."
    start = lowered.find(marker)
    value = name[start + len(marker):] if start >= 0 else name
    for suffix in ("_volcano_plot", ".volcano", "_ma_plot", ".ma_plot", ".top_de", "_heatmap"):
        index = value.lower().find(suffix)
        if index > 0:
            return value[:index]
    return ""


def _select_report_figures(figures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table_only_stages = {"rnaseq_qc", "rnaseq_align", "rnaseq_quant"}
    de_groups: dict[str, set[str]] = {}
    for figure in figures:
        if figure.get("stage") != "rnaseq_de":
            continue
        comparison = _figure_comparison(figure)
        if comparison:
            de_groups.setdefault(comparison, set()).add(_figure_display_type(figure))
    selected_comparison = ""
    if de_groups:
        selected_comparison = sorted(de_groups, key=lambda value: (-len(de_groups[value]), value))[0]

    selected: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    format_rank = {".png": 0, ".webp": 1, ".jpg": 2, ".jpeg": 2, ".svg": 3, ".pdf": 4}
    for figure in figures:
        stage = str(figure.get("stage") or "")
        if stage in table_only_stages:
            continue
        if stage == "rnaseq_de" and selected_comparison and _figure_comparison(figure) != selected_comparison:
            continue
        key = (stage, _figure_display_type(figure))
        current = selected.get(key)
        if current is None:
            selected[key] = figure
            order.append(key)
            continue
        current_rank = format_rank.get(Path(str(current.get("path") or "")).suffix.lower(), 9)
        new_rank = format_rank.get(Path(str(figure.get("path") or "")).suffix.lower(), 9)
        if new_rank < current_rank:
            selected[key] = figure
    return [selected[key] for key in order]


def _stage_table_previews(
    workspace: Path,
    manifest: dict[str, Any],
    report_path: Path,
    *,
    max_tables: int = 2,
    max_rows: int = 10,
    max_columns: int = 8,
) -> list[dict[str, Any]]:
    candidates: list[tuple[int, str, Path]] = []
    priority_terms = ("summary", "candidate", "top", "metric", "stat", "result", "deg", "enrich")
    for name, item in (manifest.get("artifacts") or {}).items():
        if not isinstance(item, dict):
            continue
        path = _artifact_path(workspace, manifest, item)
        if path is None or path.suffix.lower() not in {".csv", ".tsv"}:
            continue
        lowered = f"{name} {path.name}".lower()
        score = sum(1 for term in priority_terms if term in lowered)
        candidates.append((-score, str(name), path))
    previews: list[dict[str, Any]] = []
    for _score, name, path in sorted(candidates, key=lambda item: (item[0], item[2].name)):
        delimiter = "," if path.suffix.lower() == ".csv" else "\t"
        try:
            with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                reader = csv.DictReader(handle, delimiter=delimiter)
                columns = [str(column) for column in (reader.fieldnames or [])[:max_columns] if str(column).strip()]
                if not columns:
                    continue
                rows = []
                for row in reader:
                    rows.append([_table_cell(row.get(column, "")) for column in columns])
                    if len(rows) >= max_rows:
                        break
        except (OSError, csv.Error):
            continue
        if not rows:
            continue
        previews.append(
            {
                "name": name,
                "filename": path.name,
                "columns": columns,
                "rows": rows,
                "link": _relative_link(report_path, path),
            }
        )
        if len(previews) >= max_tables:
            break
    return previews


def _render_deterministic_markdown(
    *,
    prefix: str,
    scope_id: str,
    evidence: dict[str, Any],
    manifests: dict[str, dict[str, Any]],
    expected_stages: list[str],
    stage_statuses: dict[str, str],
    coverage_status: str,
    report_path: Path,
    workspace: Path,
    generated_at: str,
    figures: list[dict[str, Any]],
    model_summary: str = "",
) -> str:
    metadata = evidence.get("metadata") or {}
    included = list(metadata.get("included_stages") or [])
    flow_rows = {row.get("id"): row for row in (evidence.get("stage_flow") or {}).get("stages", [])}
    findings = {row.get("stage"): row for row in evidence.get("key_findings") or []}
    reviews = {row.get("stage"): row for row in evidence.get("stage_reviews") or []}
    evidence_cards = evidence.get("evidence_cards") or []
    risks = evidence.get("risk_flags") or []
    missing = [stage for stage in expected_stages if stage_statuses.get(stage) not in SUCCESS_STATUSES]
    figures = _select_report_figures(figures)
    figure_index = 0
    rendered_figures: set[str] = set()

    section_specs = [
        ("## 3. 测序质量检查", ["rnaseq_qc"], "本节检查测序数据是否稳定、是否存在需要留意的样本。"),
        ("## 4. 参考基因组比对", ["rnaseq_align"], "比对率表示测序片段能在参考基因组中找到位置的比例。"),
        ("## 5. 基因表达定量", ["rnaseq_quant", "expression"], "表达定量用于统计每个基因在各样本中的表达水平。"),
        ("## 6. 样本关系与重复可靠性", ["rnaseq_sample_qc"], "这一部分判断同组重复是否相似，以及是否存在明显离群样本。"),
        ("## 7. 差异表达", ["rnaseq_de"], "上调和下调均相对于对应比较中的对照组，表示表达量升高或降低。"),
        (
            "## 8. GO、KEGG 与全基因排序分析",
            ["annotation", "annotation_go", "annotation_kegg", "rnaseq_go_enrich", "rnaseq_kegg_enrich", "rnaseq_enrich", "rnaseq_fgsea"],
            "本节汇总 GO、KEGG 和全基因排序分析中的主要功能条目及对应图表。",
        ),
        ("## 9. 共表达网络", ["rnaseq_wgcna"], "共表达网络把变化趋势相近的基因归为模块，用于缩小候选范围。"),
        (
            "## 10. 候选基因与候选家族",
            ["rnaseq_candidate_family", "rnaseq_family_score"],
            "本节展示候选基因、候选家族及其排序依据。",
        ),
    ]

    def _stage_name(stage: str) -> str:
        return STAGE_NAMES.get(stage, stage)

    def _status_text(stage: str) -> str:
        status = str((flow_rows.get(stage) or {}).get("status") or manifests.get(stage, {}).get("status") or "unknown")
        return {"success": "已完成", "partial_success": "部分完成", "failed": "失败", "missing": "未完成"}.get(status, status)

    def _render_figure(figure: dict[str, Any]) -> None:
        nonlocal figure_index
        figure_index += 1
        original_path = Path(figure["path"])
        path = report_path.parent / _preferred_web_figure(original_path).name
        rendered_figures.add(str(original_path))
        media_line = (
            f"[{figure['artifact']}]({_relative_link(report_path, path)})"
            if path.suffix.lower() == ".pdf"
            else f"![{figure['artifact']}]({_relative_link(report_path, path)})"
        )
        lines.extend(["", f"### 图 {figure_index}. {figure['artifact']}", "", media_line, ""])
        lines.extend([*_figure_interpretation(figure, workspace, manifests, report_path), ""])

    def _render_stage_group(stage_ids: list[str], empty_text: str) -> None:
        selected = [stage for stage in included if stage in stage_ids]
        if not selected:
            lines.extend(["", empty_text])
            return
        table_stages = {"rnaseq_qc", "rnaseq_align", "rnaseq_quant"}
        for stage in selected:
            show_tables = stage in table_stages
            metrics = _display_metrics(manifests.get(stage, {})) if show_tables else []
            sample_table = _sample_metric_table(manifests.get(stage, {}), stage) if show_tables else None
            lines.extend(["", f"### {_stage_name(stage)}", "", f"状态：{_status_text(stage)}。"])
            if sample_table:
                lines.extend(
                    [
                        "",
                        "逐样本结果：",
                        "",
                        "| " + " | ".join(_table_cell(column) for column in sample_table["columns"]) + " |",
                        "|" + "|".join("---" for _ in sample_table["columns"]) + "|",
                    ]
                )
                for row in sample_table["rows"]:
                    lines.append("| " + " | ".join(row) + " |")
            if metrics:
                lines.extend(["", "本次数据：", "", "| 指标 | 结果 |", "|---|---|"])
                for label, value in metrics:
                    lines.append(f"| {label} | {value} |")
            previews = (
                _stage_table_previews(
                    workspace,
                    manifests.get(stage, {}),
                    report_path,
                    max_tables=3,
                    max_rows=30,
                    max_columns=10,
                )
                if stage in {"rnaseq_candidate_family", "rnaseq_family_score"}
                else []
            )
            for preview in previews:
                lines.extend(
                    [
                        "",
                        f"#### 数据表：{preview['filename']}",
                        "",
                        "| " + " | ".join(_table_cell(column) for column in preview["columns"]) + " |",
                        "|" + "|".join("---" for _ in preview["columns"]) + "|",
                    ]
                )
                for row in preview["rows"]:
                    lines.append("| " + " | ".join(row) + " |")
                lines.extend(["", f"[下载完整数据表]({preview['link']})"])
        for figure in figures:
            if figure.get("stage") in stage_ids:
                _render_figure(figure)

    coverage_text = (
        f"当前计划中的分析阶段已全部完成，共纳入 {len(included)} 个可用阶段。"
        if coverage_status == "complete"
        else f"当前报告基于已经完成的 {len(included)} 个阶段生成，仍有部分分析尚未完成。"
    )
    included_text = "、".join(_stage_name(stage) for stage in included) or "无"
    missing_text = "、".join(_stage_name(stage) for stage in missing) or "无"
    lines = [
        f"# {prefix} RNA-seq 结果解读报告",
        "",
        f"生成时间（北京时间）：{_display_generated_at(generated_at)}  ",
        f"任务编号：`{workspace.name}`",
        "",
        "**任务结果路径：**  ",
        f"`{workspace / 'final'}`",
        "",
        "## 1. 项目概览",
        "",
        "| 项目 | 结果 |",
        "|---|---|",
        f"| 报告前缀 | {_table_cell(prefix)} |",
        f"| 已完成分析 | {_table_cell(included_text)} |",
        f"| 待补充分析 | {_table_cell(missing_text)} |",
        f"| 可用阶段数 | {len(included)} |",
        "",
        coverage_text,
        "",
        "本报告以统计表和结果图为主，并在每个阶段提供简短的读图说明和初步结果概括。",
        "",
        "## 2. 样本与分析设计",
        "",
        f"计划分析：{'、'.join(_stage_name(stage) for stage in expected_stages) if expected_stages else '当前已有 RNA-seq 结果'}。",
        "",
        f"实际完成：{included_text}。",
        "",
        "上调和下调方向按照各差异比较结果表中的实验组与对照组关系展示。",
    ]

    empty_messages = {
        "rnaseq_qc": "当前没有可用的测序质控结果，无法判断原始数据质量。",
        "rnaseq_align": "当前没有可用的比对结果，无法判断测序片段与参考基因组的匹配情况。",
        "rnaseq_quant": "当前没有可用的表达定量结果。",
        "rnaseq_sample_qc": "当前没有可用的样本关系结果，无法判断重复一致性或离群样本。",
        "rnaseq_de": "当前没有可用的差异表达结果。",
        "rnaseq_go_enrich": "当前没有完整的 GO、KEGG 或全基因排序分析结果，不能判断具体功能主题。",
        "rnaseq_wgcna": "当前没有可用的共表达网络结果，本节不作模块判断。",
        "rnaseq_candidate_family": "当前没有可用的候选基因或候选家族结果。",
    }
    for heading, stage_ids, introduction in section_specs:
        lines.extend(["", heading, "", introduction])
        _render_stage_group(stage_ids, empty_messages.get(stage_ids[0], "当前没有可用结果。"))

    remaining_figures = [figure for figure in figures if str(Path(figure["path"])) not in rendered_figures]
    lines.extend(["", "## 11. 结果汇总", "", "| 分析阶段 | 状态 | 主要结果 |", "|---|---|---|"])
    for stage in included:
        artifact_count = len((manifests.get(stage, {}).get("artifacts") or {}))
        figure_count = sum(1 for figure in figures if figure.get("stage") == stage)
        summary = f"已登记 {artifact_count} 个结果文件，报告展示 {figure_count} 张图"
        lines.append(f"| {_table_cell(_stage_name(stage))} | {_table_cell(_status_text(stage))} | {_table_cell(summary)} |")
    if remaining_figures:
        lines.extend(["", "### 其他结果图", ""])
        for figure in remaining_figures:
            _render_figure(figure)
    lines.extend(["", "---", "", "完整证据和来源记录保存在同名 `.evidence.json` 与 `.manifest.json` 文件中。", ""])
    return "\n".join(lines)


def _resolve_html_asset(report_path: Path, raw_target: str) -> Path:
    clean_target = unquote(raw_target.split("#", 1)[0].split("?", 1)[0]).replace("\\", "/")
    if clean_target.startswith("/workspace/"):
        parts = [part for part in clean_target.strip("/").split("/") if part]
        job_root = report_path.parent.parent
        if len(parts) >= 3 and parts[1] == job_root.name:
            return job_root.joinpath(*parts[2:]).resolve()
    target = Path(clean_target)
    if target.is_absolute():
        return target.resolve()
    return (report_path.parent / target).resolve()


def _markdown_inline(text: str, report_path: Path) -> str:
    tokens: list[str] = []

    def stash(value: str) -> str:
        token = f"@@HTMLTOKEN{len(tokens)}@@"
        tokens.append(value)
        return token

    def image_replacement(match: re.Match[str]) -> str:
        alt = match.group(1).strip() or "RNA-seq result figure"
        raw_target = match.group(2).strip().replace("\\", "/")
        source = raw_target
        target = report_path.parent / Path(raw_target).name
        mime = mimetypes.guess_type(target.name)[0] or ""
        if target.is_file() and mime.startswith("image/"):
            encoded = base64.b64encode(target.read_bytes()).decode("ascii")
            source = f"data:{mime};base64,{encoded}"
        return stash(
            f'<figure><img src="{html.escape(source, quote=True)}" '
            f'alt="{html.escape(alt, quote=True)}" loading="lazy">'
            f'<figcaption>{html.escape(alt)}</figcaption></figure>'
        )

    def link_replacement(match: re.Match[str]) -> str:
        label = html.escape(match.group(1).strip())
        raw_target = match.group(2).strip().replace("\\", "/")
        target = html.escape(raw_target, quote=True)
        if Path(raw_target).suffix.lower() == ".pdf":
            return stash(
                f'<figure><iframe class="pdf-frame" src="{target}" title="{label}"></iframe>'
                f'<figcaption><a href="{target}" target="_blank" rel="noopener">{label}</a></figcaption></figure>'
            )
        return stash(f'<a href="{target}" target="_blank" rel="noopener">{label}</a>')

    value = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", image_replacement, text)
    value = re.sub(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)", link_replacement, value)
    value = html.escape(value)
    value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
    for index, token_value in enumerate(tokens):
        value = value.replace(f"@@HTMLTOKEN{index}@@", token_value)
    return value


def _markdown_to_html(markdown_text: str, report_path: Path, title: str) -> str:
    output: list[str] = []
    list_tag = ""
    table_rows: list[list[str]] = []
    in_code = False
    code_lines: list[str] = []

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            output.append(f"</{list_tag}>")
            list_tag = ""

    def flush_table() -> None:
        nonlocal table_rows
        if not table_rows:
            return
        rows = table_rows
        table_rows = []
        if len(rows) > 1 and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in rows[1]):
            header = rows[0]
            body_rows = rows[2:]
        else:
            header = []
            body_rows = rows
        output.append('<div class="table-wrap"><table>')
        if header:
            output.append("<thead><tr>" + "".join(f"<th>{_markdown_inline(cell, report_path)}</th>" for cell in header) + "</tr></thead>")
        output.append("<tbody>")
        for row in body_rows:
            output.append("<tr>" + "".join(f"<td>{_markdown_inline(cell, report_path)}</td>" for cell in row) + "</tr>")
        output.extend(["</tbody>", "</table></div>"])

    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("```"):
            close_list()
            flush_table()
            if in_code:
                output.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if line.startswith("|") and line.endswith("|"):
            close_list()
            table_rows.append([cell.strip() for cell in line.strip("|").split("|")])
            continue
        flush_table()
        if not line.strip():
            close_list()
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            close_list()
            level = len(heading.group(1))
            output.append(f"<h{level}>{_markdown_inline(heading.group(2), report_path)}</h{level}>")
            continue
        if line == "---":
            close_list()
            output.append("<hr>")
            continue
        if line.startswith("> "):
            close_list()
            output.append(f"<blockquote>{_markdown_inline(line[2:], report_path)}</blockquote>")
            continue
        bullet = re.match(r"^-\s+(.+)$", line)
        ordered = re.match(r"^\d+\.\s+(.+)$", line)
        if bullet or ordered:
            wanted = "ul" if bullet else "ol"
            if list_tag != wanted:
                close_list()
                list_tag = wanted
                output.append(f"<{list_tag}>")
            item = bullet.group(1) if bullet else ordered.group(1)
            output.append(f"<li>{_markdown_inline(item, report_path)}</li>")
            continue
        close_list()
        if re.fullmatch(r"!?\[[^\]]+\]\([^)]+\)", line):
            output.append(_markdown_inline(line, report_path))
            continue
        output.append(f"<p>{_markdown_inline(line, report_path)}</p>")
    close_list()
    flush_table()
    if in_code:
        output.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")

    safe_title = html.escape(title)
    body = "\n".join(output)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    :root {{ --ink:#17352c; --muted:#60726b; --paper:#fffdf7; --line:#dce5dc; --accent:#157a57; --soft:#edf6ef; --warm:#f2e5c7; }}
    * {{ box-sizing:border-box; }}
    html {{ background:#e9efe9; scroll-behavior:smooth; }}
    body {{ margin:0; color:var(--ink); font-family:"Noto Serif SC","Source Han Serif SC","Microsoft YaHei",serif; line-height:1.85; background:radial-gradient(circle at top left,#f8edcf 0,transparent 28rem),linear-gradient(150deg,#edf4ec,#f6f2e8); }}
    main {{ width:min(1120px,calc(100% - 32px)); margin:32px auto 64px; padding:clamp(28px,5vw,72px); background:var(--paper); border:1px solid rgba(23,53,44,.12); border-radius:22px; box-shadow:0 24px 70px rgba(35,62,48,.14); }}
    h1 {{ margin:0 0 2rem; font-size:clamp(2rem,5vw,3.5rem); line-height:1.2; letter-spacing:-.04em; color:#0d4f39; }}
    h2 {{ margin:3.2rem 0 1.1rem; padding-top:.7rem; border-top:3px solid var(--warm); font-size:clamp(1.45rem,3vw,2rem); line-height:1.35; }}
    h3 {{ margin:2rem 0 .8rem; color:var(--accent); font-size:1.25rem; }}
    p {{ margin:.75rem 0; }}
    p,h3,a,figcaption,code {{ overflow-wrap:anywhere; word-break:break-word; }}
    ul,ol {{ padding-left:1.45rem; }}
    li {{ margin:.42rem 0; }}
    blockquote {{ margin:1.4rem 0; padding:1rem 1.25rem; border-left:5px solid var(--accent); border-radius:0 12px 12px 0; background:var(--soft); color:#35584c; }}
    code {{ padding:.14em .4em; border-radius:5px; background:#edf0eb; font-family:"Cascadia Mono","Fira Code",monospace; font-size:.9em; }}
    pre {{ overflow:auto; padding:1rem; border-radius:12px; background:#142a23; color:#f3f6f2; }}
    pre code {{ padding:0; background:none; color:inherit; }}
    figure {{ margin:1.6rem 0 2.2rem; padding:clamp(8px,2vw,18px); border:1px solid var(--line); border-radius:16px; background:#fff; box-shadow:0 10px 30px rgba(28,62,48,.09); }}
    img {{ display:block; max-width:100%; height:auto; margin:auto; border-radius:10px; }}
    .pdf-frame {{ display:block; width:100%; height:min(72vh,760px); border:0; border-radius:10px; background:#f3f5f2; }}
    figcaption {{ margin-top:.7rem; text-align:center; color:var(--muted); font-size:.92rem; }}
    a {{ color:#087553; text-decoration-thickness:1px; text-underline-offset:3px; }}
    .table-wrap {{ max-height:36rem; overflow:auto; margin:1.4rem 0; border:1px solid var(--line); border-radius:12px; scrollbar-gutter:stable; }}
    table {{ width:100%; min-width:max-content; border-collapse:separate; border-spacing:0; background:#fff; }}
    th,td {{ padding:.72rem .85rem; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; white-space:nowrap; }}
    th {{ position:sticky; top:0; z-index:2; background:var(--soft); color:#174d3b; }}
    th:first-child,td:first-child {{ position:sticky; left:0; z-index:1; background:#fff; }}
    th:first-child {{ z-index:3; background:var(--soft); }}
    hr {{ margin:3rem 0; border:0; border-top:1px solid var(--line); }}
    @media (max-width:640px) {{ main {{ width:100%; margin:0; padding:24px 18px 48px; border:0; border-radius:0; }} h2 {{ margin-top:2.4rem; }} }}
    @media print {{ html,body {{ background:#fff; }} main {{ width:100%; margin:0; padding:0; border:0; box-shadow:none; }} figure {{ break-inside:avoid; }} }}
  </style>
</head>
<body><main>{body}</main></body>
</html>
"""


def _model_claim_catalog(evidence: dict[str, Any]) -> list[dict[str, str]]:
    catalog: list[dict[str, str]] = []

    def add(claim_id: str, text: Any, source_type: str, stage: str = "") -> None:
        value = str(text or "").strip()
        if value:
            catalog.append(
                {
                    "claim_id": claim_id,
                    "text": value,
                    "source_type": source_type,
                    "stage": stage,
                }
            )

    add("story_1", evidence.get("biological_story"), "biological_story")
    for index, row in enumerate(evidence.get("key_findings") or [], 1):
        if not isinstance(row, dict):
            continue
        add(
            f"finding_{index}",
            row.get("description") or row.get("summary") or row.get("claim") or row.get("text"),
            "key_finding",
            str(row.get("stage") or ""),
        )
    for index, row in enumerate(evidence.get("major_claims") or [], 1):
        if isinstance(row, dict):
            text = row.get("claim") or row.get("text") or row.get("title")
            stage = str(row.get("stage") or "")
        else:
            text = row
            stage = ""
        add(f"claim_{index}", text, "major_claim", stage)
    for index, row in enumerate(evidence.get("risk_flags") or [], 1):
        if isinstance(row, dict):
            text = row.get("message") or row.get("text")
            stage = str(row.get("stage") or "")
        else:
            text = row
            stage = ""
        add(f"risk_{index}", text, "risk_flag", stage)
    return catalog


def _render_claim_groups(
    plan: dict[str, Any], catalog: list[dict[str, str]]
) -> tuple[str, list[str]]:
    groups = plan.get("paragraph_groups")
    if not isinstance(groups, list) or not groups or len(groups) > 4:
        raise ValueError("paragraph_groups must contain between 1 and 4 groups")
    claim_text = {row["claim_id"]: row["text"] for row in catalog}
    selected: list[str] = []
    paragraphs: list[str] = []
    for group in groups:
        if not isinstance(group, list) or not group:
            raise ValueError("each paragraph group must be a non-empty claim-id list")
        sentences: list[str] = []
        for raw_claim_id in group:
            claim_id = str(raw_claim_id or "").strip()
            if claim_id not in claim_text:
                raise ValueError(f"unknown evidence claim id: {claim_id}")
            if claim_id in selected:
                raise ValueError(f"duplicate evidence claim id: {claim_id}")
            selected.append(claim_id)
            text = claim_text[claim_id]
            sentences.append(text if text.endswith(("。", "！", "？", ".", "!", "?")) else f"{text}。")
        paragraphs.append("".join(sentences))
    return "\n\n".join(paragraphs), selected


def _model_enhancement(evidence: dict[str, Any], enabled: bool) -> tuple[str, dict[str, Any]]:
    claim_catalog = _model_claim_catalog(evidence)
    metadata: dict[str, Any] = {
        "schema": "rnaseq_report_model_v2",
        "enabled": enabled,
        "status": "disabled" if not enabled else "pending",
        "provider": "",
        "model": "",
        "content": "",
        "error": "",
        "generated_at": _now(),
        "policy": "evidence_claim_ordering_only",
        "claim_catalog": claim_catalog,
        "organization_plan": {},
        "selected_claim_ids": [],
    }
    if not enabled:
        return "", metadata
    if not claim_catalog:
        metadata.update({"status": "fallback", "error": "no approved evidence claims available"})
        return "", metadata
    try:
        from gfa_core.mas.config import config
        from gfa_core.mas.llm import LLMManager
        from gfa_core.mas.llm.base import LLMMessage

        manager = LLMManager(config.load_llm_config())
        response = manager.call(
            [
                LLMMessage(
                    role="system",
                    content=(
                        "你只能组织已批准的RNA-seq证据语句，不能撰写或改写结论。"
                        "只返回JSON对象：paragraph_groups为1至4个段落，每个段落只能列出输入中的claim_id。"
                        "不得返回自由文本、未知claim_id、数字、基因、通路、显著性、方向、机制或因果推断。"
                    ),
                ),
                LLMMessage(
                    role="user",
                    content=json.dumps(
                        {"task": "order_and_group_evidence_claims", "claim_catalog": claim_catalog},
                        ensure_ascii=False,
                    )[:30000],
                ),
            ],
            allow_fallback=True,
            max_tokens=600,
            track_agent_name="RNASeqReport",
        )
        content = str(response.content or "").strip()
        metadata.update(
            {
                "provider": str(response.provider or ""),
                "model": str(response.model or ""),
                "content": content,
            }
        )
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE)
        plan = json.loads(cleaned)
        if not isinstance(plan, dict):
            raise ValueError("model organization plan must be a JSON object")
        rendered, selected_claim_ids = _render_claim_groups(plan, claim_catalog)
        metadata.update(
            {
                "status": "embedded_evidence_only",
                "organization_plan": plan,
                "selected_claim_ids": selected_claim_ids,
            }
        )
        return rendered, metadata
    except Exception as exc:  # deterministic report remains authoritative
        metadata.update({"status": "fallback", "error": f"{type(exc).__name__}: {exc}"})
        return "", metadata


def _artifact(workspace: Path, path: Path, kind: str) -> dict[str, Any]:
    artifact_type = {".md": "markdown", ".html": "html", ".json": "json"}.get(path.suffix.lower(), "file")
    return {
        "artifact": kind,
        "path": str(path.relative_to(workspace)),
        "full_path": str(path.absolute()),
        "type": artifact_type,
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }


def generate_rnaseq_report(
    workspace: Path | str,
    prefix: str,
    *,
    rnaseq_scope_id: str = "",
    expected_stages: Iterable[str] | None = None,
    use_model: bool = True,
    action: str = "generate",
    job_id: str = "",
) -> RNASeqReportResult:
    workspace = Path(workspace).absolute()
    final_dir = workspace / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    prefix = str(prefix or "").strip()
    if not prefix:
        raise ValueError("prefix is required")
    if action not in {"generate", "open"}:
        raise ValueError("action must be 'generate' or 'open'")

    expected = _normalize_expected_stages(expected_stages)
    resolved_scope, discovered_scopes, all_sources = _source_selection(final_dir, prefix, rnaseq_scope_id)
    successful = {
        stage: manifest
        for stage, manifest in all_sources.items()
        if str(manifest.get("status") or "") in SUCCESS_STATUSES
    }
    if not successful:
        raise NoSuccessfulRNASeqEvidence("暂无可生成的 RNA-seq 结果：未找到当前 prefix/scope 下成功的 RNA-seq 阶段。")

    included = sorted(successful, key=_stage_sort_key)
    source_records = _source_records(all_sources)
    source_fingerprint, request_fingerprint = _fingerprint(source_records, expected, use_model)
    report_path = final_dir / f"{prefix}.rnaseq_report.md"
    html_path = final_dir / f"{prefix}.rnaseq_report.html"
    evidence_path = final_dir / f"{prefix}.rnaseq_report.evidence.json"
    model_path = final_dir / f"{prefix}.rnaseq_report.model.json"
    manifest_path = final_dir / f"{prefix}.rnaseq_report.manifest.json"

    stage_statuses = {stage: str(all_sources.get(stage, {}).get("status") or "missing") for stage in expected}
    coverage_status = "complete" if expected and all(stage_statuses[s] == "success" for s in expected) else "partial"
    if action == "open" and all(path.is_file() for path in (report_path, html_path, evidence_path, model_path, manifest_path)):
        previous = _read_json(manifest_path)
        metrics = previous.get("metrics") or {}
        if (
            previous.get("status") == "success"
            and _identity(previous, "rnaseq_scope_id") == resolved_scope
            and metrics.get("request_fingerprint") == request_fingerprint
            and metrics.get("report_renderer_version") == REPORT_RENDERER_VERSION
        ):
            return RNASeqReportResult(
                report_path,
                evidence_path,
                model_path,
                manifest_path,
                str(metrics.get("coverage_status") or coverage_status),
                tuple(metrics.get("included_stages") or included),
                resolved_scope,
                reused=True,
            )

    evidence = build_rnaseq_evidence_snapshot(
        final_dir,
        prefix,
        rnaseq_scope_id=resolved_scope,
        include_stages=included,
    )
    if not evidence:
        raise NoSuccessfulRNASeqEvidence("暂无可生成的 RNA-seq 结果：成功 manifest 未能构建有效证据。")
    generated_at = _now()
    figures, omitted_figures, selection_mode = _figures(workspace, successful)
    enrichment_figures = [figure for figure in figures if figure.get("category")]

    def inventory_row(figure: dict[str, Any]) -> dict[str, Any]:
        return {
            "stage": figure["stage"],
            "artifact": figure["artifact"],
            "filename": Path(figure["path"]).name,
            "category": figure.get("category", "other"),
            "comparison": figure.get("comparison", ""),
            "pathway_id": figure.get("pathway_id", ""),
            "candidate_families": list(figure.get("candidate_families") or []),
            "support_labels": list(figure.get("support_labels") or []),
            "selection_reason": figure.get("selection_reason", ""),
            "source_tables": list(figure.get("source_tables") or []),
        }

    figure_inventory = [inventory_row(figure) for figure in figures]
    omitted_figure_inventory = [inventory_row(figure) for figure in omitted_figures]
    evidence["formal_report"] = {
        "schema": "rnaseq_formal_report_evidence_v1",
        "report_renderer_version": REPORT_RENDERER_VERSION,
        "generated_at": generated_at,
        "prefix": prefix,
        "rnaseq_scope_id": resolved_scope,
        "discovered_scope_ids": discovered_scopes,
        "expected_stages": expected,
        "stage_statuses": stage_statuses,
        "coverage_status": coverage_status,
        "source_fingerprint": source_fingerprint,
        "request_fingerprint": request_fingerprint,
        "source_manifests": source_records,
        "selection_mode": selection_mode,
        "figure_inventory": figure_inventory,
        "omitted_figure_inventory": omitted_figure_inventory,
        "omitted_figure_count": len(omitted_figure_inventory),
        "figure_count": len(figures),
        "enrichment_figure_count": len(enrichment_figures),
    }
    model_summary, model_metadata = _model_enhancement(evidence, use_model)
    markdown = _render_deterministic_markdown(
        prefix=prefix,
        scope_id=resolved_scope,
        evidence=evidence,
        manifests=successful,
        expected_stages=expected,
        stage_statuses=stage_statuses,
        coverage_status=coverage_status,
        report_path=report_path,
        workspace=workspace,
        generated_at=generated_at,
        figures=figures,
        model_summary=model_summary,
    )
    _write_text_atomic(evidence_path, json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    _write_text_atomic(model_path, json.dumps(model_metadata, ensure_ascii=False, indent=2) + "\n")
    _write_text_atomic(report_path, markdown)
    html_report = _markdown_to_html(markdown, report_path, f"{prefix} RNA-seq 结果解读报告")
    _write_text_atomic(html_path, html_report)

    manifest = {
        "manifest_version": "1.0",
        "stage": REPORT_STAGE,
        "status": "success",
        "job_id": job_id or workspace.name,
        "prefix": prefix,
        "rnaseq_scope_id": resolved_scope,
        "generated_at": generated_at,
        "parameters": {
            "action": action,
            "model_enhancement_requested": use_model,
            "expected_stages": expected,
            "rnaseq_scope_id": resolved_scope,
        },
        "metrics": {
            "coverage_status": coverage_status,
            "included_stage_count": len(included),
            "included_stages": included,
            "expected_stages": expected,
            "stage_statuses": stage_statuses,
            "missing_or_failed_stages": [stage for stage in expected if stage_statuses.get(stage) != "success"],
            "model_status": model_metadata["status"],
            "source_fingerprint": source_fingerprint,
            "request_fingerprint": request_fingerprint,
            "report_renderer_version": REPORT_RENDERER_VERSION,
            "selection_mode": selection_mode,
            "figure_count": len(figures),
            "enrichment_figure_count": len(enrichment_figures),
            "figure_inventory": figure_inventory,
            "omitted_figure_inventory": omitted_figure_inventory,
            "omitted_figure_count": len(omitted_figure_inventory),
            "figure_categories": {
                category: sum(1 for figure in enrichment_figures if figure.get("category") == category)
                for category in sorted({figure.get("category", "") for figure in enrichment_figures})
            },
        },
        "provenance": {"source_manifests": source_records},
        "artifacts": {
            "rnaseq_report": _artifact(workspace, report_path, "rnaseq_report"),
            "rnaseq_report_html": _artifact(workspace, html_path, "rnaseq_report_html"),
            "rnaseq_report_evidence": _artifact(workspace, evidence_path, "rnaseq_report_evidence"),
            "rnaseq_report_model": _artifact(workspace, model_path, "rnaseq_report_model"),
        },
    }
    _write_text_atomic(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return RNASeqReportResult(
        report_path,
        evidence_path,
        model_path,
        manifest_path,
        coverage_status,
        tuple(included),
        resolved_scope,
    )
