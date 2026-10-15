"""Canonical identity helpers for gene-family stage planning/execution/status.

A stage is only the action name.  The runnable/result identity also includes
the gene-family scope, analysis route and member-set.  This module is the
shared adapter used by planning, execution, polling and diagnostics so they do
not each reconstruct that identity differently.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from gfa_core.gene_family_scope import build_scope_id, selected_species_for_mode
from gfa_core.gene_family_stage_context import (
    build_stage_context,
    infer_analysis_route,
    member_set_id_for,
    scope_data_from_params,
)
from gfa_core.mas.tools.stage_status import WORKSPACE_ROOT, get_stage_status, StageStatusReport, UNSCOPED_STATUS_STAGES

POPULATION_STAGES = {"pop_pca", "pop_phylogeny", "pop_structure", "pop_ld_decay", "pop_snp_density"}



def _clean(value: Any) -> str:
    return str(value or "").strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.replace("，", ",").replace("；", ",").replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw = value
    else:
        raw = [value]
    return [str(x).strip() for x in raw if str(x).strip()]


def _normalize_species_alias(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", " ", str(value or "")).strip().lower()


def _species_alias_map_for_job(job_id: str, workspace_root: Path) -> dict[str, str]:
    if not job_id:
        return {}
    config_path = workspace_root.parent / "input" / job_id / "config.xlsx"
    if not config_path.exists():
        return {}
    try:
        from gfa_core.gene_family_scope import read_config_workbook

        rows, _analysis = read_config_workbook(config_path)
    except Exception:
        return {}
    aliases: dict[str, str] = {}

    def add(alias: Any, code: str) -> None:
        text = str(alias or "").strip()
        if not text or not code:
            return
        aliases[text.lower()] = code
        aliases[_normalize_species_alias(text)] = code

    for row in rows:
        code = str(row.get("Species_Code") or row.get("Species") or row.get("species") or "").strip()
        if not code:
            continue
        add(code, code)
        for key in (
            "Species_Name",
            "Scientific_Name",
            "Latin_Name",
            "Species",
            "species_name",
            "scientific_name",
            "latin_name",
        ):
            add(row.get(key), code)
    return aliases


def _canonicalize_species_for_job(job_id: str, workspace_root: Path, values: list[str]) -> list[str]:
    aliases = _species_alias_map_for_job(job_id, workspace_root)
    if not aliases:
        return values
    canonical: list[str] = []
    for value in values:
        raw = str(value or "").strip()
        mapped = aliases.get(raw.lower()) or aliases.get(_normalize_species_alias(raw)) or raw
        if mapped and mapped not in canonical:
            canonical.append(mapped)
    return canonical


def _scope_data_from_scope_id(scope_id: str) -> dict[str, Any]:
    parts = [part for part in str(scope_id or "").split(".") if part]
    data: dict[str, Any] = {"scope_id": str(scope_id or "").strip()}
    if not parts:
        return data
    data["family"] = parts[0]
    for part in parts[1:]:
        if part.startswith("target-"):
            data["target_species"] = [x for x in part[len("target-") :].split("-") if x]
        elif part.startswith("ref-"):
            data["reference_species"] = [x for x in part[len("ref-") :].split("-") if x]
        elif part.startswith("comp-"):
            data["comparative_species"] = [x for x in part[len("comp-") :].split("-") if x]
        else:
            data["scope_mode"] = part
    selected = []
    selected.extend(data.get("target_species") or [])
    selected.extend(data.get("reference_species") or [])
    selected.extend(data.get("comparative_species") or [])
    if selected:
        data["selected_species"] = selected
    return data


def _route_hint_from_prefix(stage: str, prefix: str, params: dict[str, Any]) -> str:
    if stage == "subfamily_confirm":
        return "confirmed_subfamily"
    haystack = " ".join(
        str(value or "")
        for value in (
            prefix,
            params.get("outputPrefix"),
            params.get("output_prefix"),
            params.get("scopePrefix"),
            params.get("scope_prefix"),
        )
    ).lower()
    if "subfamily_evidence" in haystack:
        return "subfamily_evidence"
    if "subfamily_prediction" in haystack:
        return "subfamily_prediction"
    if "confirmed_subfamily" in haystack:
        return "confirmed_subfamily"
    return ""


@dataclass
class StageIdentity:
    stage: str
    prefix: str
    scope_id: str = ""
    scope_file: str = ""
    scope_mode: str = ""
    analysis_route: str = ""
    member_set_id: str = ""
    result_group: str = ""
    family: str = ""
    target_species: list[str] = field(default_factory=list)
    reference_species: list[str] = field(default_factory=list)
    comparative_species: list[str] = field(default_factory=list)
    selected_species: list[str] = field(default_factory=list)
    genotype_scope_id: str = ""
    phenotype_scope_id: str = ""
    covariate_scope_id: str = ""
    model_scope_id: str = ""
    source_model_scope_id: str = ""
    analysis_scope_id: str = ""
    run_id: str = ""
    wgs_sample_set_id: str = ""
    wgs_reference_id: str = ""
    wgs_analysis_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_scope_file(scope_file: str | Path | None, workspace: str | Path | None = None) -> dict[str, Any]:
    if not scope_file:
        return {}
    raw_path = Path(str(scope_file)).expanduser()
    candidates: list[Path] = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        candidates.append(raw_path)
        if workspace:
            workspace_path = Path(workspace)
            candidates.append(workspace_path / raw_path)
            # workspace is normally <project>/workspace/<job_id>.  API params may
            # already carry paths relative to <project>, such as workspace/<job>/final/x.scope.json.
            candidates.append(workspace_path.parent / raw_path)
            candidates.append(workspace_path.parent.parent / raw_path)
            candidates.append(workspace_path / "final" / raw_path)
            candidates.append(workspace_path / "final" / raw_path.name)
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def params_from_identity(identity: StageIdentity) -> dict[str, Any]:
    return {
        "scopeFile": identity.scope_file,
        "scopeId": identity.scope_id,
        "scope_id": identity.scope_id,
        "scopeMode": identity.scope_mode,
        "scope_mode": identity.scope_mode,
        "analysisRoute": identity.analysis_route,
        "analysis_route": identity.analysis_route,
        "memberSetId": identity.member_set_id,
        "member_set_id": identity.member_set_id,
        "geneFamily": identity.family,
        "targetSpecies": list(identity.target_species),
        "referenceSpecies": list(identity.reference_species),
        "comparativeSpecies": list(identity.comparative_species),
        "genotypeScopeId": identity.genotype_scope_id,
        "genotype_scope_id": identity.genotype_scope_id,
        "phenotypeScopeId": identity.phenotype_scope_id,
        "phenotype_scope_id": identity.phenotype_scope_id,
        "covariateScopeId": identity.covariate_scope_id,
        "covariate_scope_id": identity.covariate_scope_id,
        "modelScopeId": identity.model_scope_id,
        "model_scope_id": identity.model_scope_id,
        "sourceModelScopeId": identity.source_model_scope_id,
        "source_model_scope_id": identity.source_model_scope_id,
        "analysisScopeId": identity.analysis_scope_id,
        "analysis_scope_id": identity.analysis_scope_id,
        "runId": identity.run_id,
        "run_id": identity.run_id,
    }


def merge_manifest_identity_params(params: dict[str, Any] | None, manifest: dict[str, Any] | None) -> dict[str, Any]:
    """Merge post-run identity fields, preferring the manifest actually written."""
    merged = dict(params or {})
    if not isinstance(manifest, dict):
        return merged

    stage_context = manifest.get("stage_context") or manifest.get("stageContext") or {}
    if not isinstance(stage_context, dict):
        stage_context = {}
    manifest_params = manifest.get("parameters") or {}
    if not isinstance(manifest_params, dict):
        manifest_params = {}

    def first_nonempty(*values: Any) -> Any:
        for value in values:
            if value not in (None, "", []):
                return value
        return None

    field_pairs = (
        ("genotype_scope_id", "genotypeScopeId"),
        ("phenotype_scope_id", "phenotypeScopeId"),
        ("covariate_scope_id", "covariateScopeId"),
        ("model_scope_id", "modelScopeId"),
        ("source_model_scope_id", "sourceModelScopeId"),
        ("analysis_scope_id", "analysisScopeId"),
        ("run_id", "runId"),
        ("scope_id", "scopeId"),
        ("scope_mode", "scopeMode"),
        ("analysis_route", "analysisRoute"),
        ("member_set_id", "memberSetId"),
        ("target_species", "targetSpecies"),
        ("reference_species", "referenceSpecies"),
        ("comparative_species", "comparativeSpecies"),
        ("wgs_sample_set_id", "wgsSampleSetId"),
        ("wgs_reference_id", "wgsReferenceId"),
        ("wgs_analysis_id", "wgsAnalysisId"),
        ("upstream_wgs_analysis_ids", "upstreamWgsAnalysisIds"),
    )
    for manifest_key, camel_key in field_pairs:
        value = first_nonempty(
            manifest.get(manifest_key),
            stage_context.get(manifest_key),
            manifest_params.get(manifest_key),
        )
        if value in (None, "", []):
            merged.pop(manifest_key, None)
            merged.pop(camel_key, None)
            continue
        merged[manifest_key] = value
        merged[camel_key] = value

    scope_file = first_nonempty(
        manifest.get("scope_file"),
        stage_context.get("scope_file"),
        manifest_params.get("scope_file"),
    )
    if scope_file:
        merged["scope_file"] = scope_file
        merged["scopeFile"] = scope_file
    else:
        merged.pop("scope_file", None)
        merged.pop("scopeFile", None)

    family = first_nonempty(
        manifest.get("gene_family"),
        manifest.get("family"),
        stage_context.get("family"),
        manifest_params.get("gene_family"),
        manifest_params.get("family"),
    )
    if family:
        merged["geneFamily"] = family
        merged["gene_family"] = family
    return merged


def resolve_stage_identity(
    stage: str,
    prefix: str = "",
    params: dict[str, Any] | None = None,
    job_id: str = "",
    workspace_root: Path = WORKSPACE_ROOT,
) -> StageIdentity:
    params = dict(params or {})
    prefix = _clean(params.get("prefix") or prefix)
    stage = _clean(stage)
    if stage in POPULATION_STAGES:
        return StageIdentity(
            stage=stage,
            prefix=prefix,
            result_group="population_genomics",
            genotype_scope_id=_clean(params.get("genotypeScopeId") or params.get("genotype_scope_id")),
            analysis_scope_id=_clean(params.get("analysisScopeId") or params.get("analysis_scope_id")),
            run_id=_clean(params.get("runId") or params.get("run_id")),
        )
    if stage == "wgs_qc":
        return StageIdentity(stage=stage, prefix=prefix, result_group="wgs_qc", wgs_sample_set_id=_clean(params.get("wgsSampleSetId") or params.get("wgs_sample_set_id")), wgs_analysis_id=_clean(params.get("wgsAnalysisId") or params.get("wgs_analysis_id")))
    if stage == "wgs_index":
        return StageIdentity(stage=stage, prefix=prefix, result_group="wgs_index", wgs_reference_id=_clean(params.get("wgsReferenceId") or params.get("wgs_reference_id")), wgs_analysis_id=_clean(params.get("wgsAnalysisId") or params.get("wgs_analysis_id")))
    if stage == "wgs_align":
        return StageIdentity(stage=stage, prefix=prefix, result_group="wgs_align",
                             wgs_sample_set_id=_clean(params.get("wgsSampleSetId") or params.get("wgs_sample_set_id")),
                             wgs_reference_id=_clean(params.get("wgsReferenceId") or params.get("wgs_reference_id")),
                             wgs_analysis_id=_clean(params.get("wgsAnalysisId") or params.get("wgs_analysis_id")))
    if stage == "wgs_markdup":
        return StageIdentity(stage=stage, prefix=prefix, result_group="wgs_markdup",
                             wgs_sample_set_id=_clean(params.get("wgsSampleSetId") or params.get("wgs_sample_set_id")),
                             wgs_reference_id=_clean(params.get("wgsReferenceId") or params.get("wgs_reference_id")),
                             wgs_analysis_id=_clean(params.get("wgsAnalysisId") or params.get("wgs_analysis_id")))
    if stage == "wgs_bam_qc":
        return StageIdentity(stage=stage, prefix=prefix, result_group="wgs_bam_qc",
                             wgs_sample_set_id=_clean(params.get("wgsSampleSetId") or params.get("wgs_sample_set_id")),
                             wgs_reference_id=_clean(params.get("wgsReferenceId") or params.get("wgs_reference_id")),
                             wgs_analysis_id=_clean(params.get("wgsAnalysisId") or params.get("wgs_analysis_id")))
    if stage == "wgs_haplotype_calling":
        return StageIdentity(stage=stage, prefix=prefix, result_group="wgs_haplotype_calling",
                             wgs_sample_set_id=_clean(params.get("wgsSampleSetId") or params.get("wgs_sample_set_id")),
                             wgs_reference_id=_clean(params.get("wgsReferenceId") or params.get("wgs_reference_id")),
                             wgs_analysis_id=_clean(params.get("wgsAnalysisId") or params.get("wgs_analysis_id")))
    if stage == "wgs_gvcf_combine":
        return StageIdentity(stage=stage, prefix=prefix, result_group="wgs_gvcf_combine",
                             wgs_sample_set_id=_clean(params.get("wgsSampleSetId") or params.get("wgs_sample_set_id")),
                             wgs_reference_id=_clean(params.get("wgsReferenceId") or params.get("wgs_reference_id")),
                             wgs_analysis_id=_clean(params.get("wgsAnalysisId") or params.get("wgs_analysis_id")))
    if stage == "wgs_joint_genotyping":
        return StageIdentity(stage=stage, prefix=prefix, result_group="wgs_joint_genotyping",
                             wgs_sample_set_id=_clean(params.get("wgsSampleSetId") or params.get("wgs_sample_set_id")),
                             wgs_reference_id=_clean(params.get("wgsReferenceId") or params.get("wgs_reference_id")),
                             wgs_analysis_id=_clean(params.get("wgsAnalysisId") or params.get("wgs_analysis_id")))
    if stage == "wgs_variant_select":
        return StageIdentity(stage=stage, prefix=prefix, result_group="wgs_variant_select",
                             wgs_sample_set_id=_clean(params.get("wgsSampleSetId") or params.get("wgs_sample_set_id")),
                             wgs_reference_id=_clean(params.get("wgsReferenceId") or params.get("wgs_reference_id")),
                             wgs_analysis_id=_clean(params.get("wgsAnalysisId") or params.get("wgs_analysis_id")))
    if stage in {"wgs_variant_filter", "wgs_impute", "wgs_maf_missing_filter"}:
        return StageIdentity(stage=stage, prefix=prefix, result_group=stage,
                             wgs_sample_set_id=_clean(params.get("wgsSampleSetId") or params.get("wgs_sample_set_id")),
                             wgs_reference_id=_clean(params.get("wgsReferenceId") or params.get("wgs_reference_id")),
                             wgs_analysis_id=_clean(params.get("wgsAnalysisId") or params.get("wgs_analysis_id")))
    if stage == "gwas_fmt" or stage in {
        "gwas_kinship",
        "gwas_trait",
        "gwas_cov",
        "gwas_mlm",
        "gwas_plot",
        "gwas_emmax",
        "gwas_emplot",
        "gwas_glm",
    }:
        return StageIdentity(
            stage=stage,
            prefix=prefix,
            result_group="gwas_fmt_genotype" if stage == "gwas_fmt" else f"{stage}_gwas",
            genotype_scope_id=_clean(params.get("genotypeScopeId") or params.get("genotype_scope_id")),
            phenotype_scope_id=_clean(params.get("phenotypeScopeId") or params.get("phenotype_scope_id")),
            covariate_scope_id=_clean(params.get("covariateScopeId") or params.get("covariate_scope_id")),
            model_scope_id=_clean(params.get("modelScopeId") or params.get("model_scope_id")),
            source_model_scope_id=_clean(params.get("sourceModelScopeId") or params.get("source_model_scope_id")),
            analysis_scope_id=_clean(params.get("analysisScopeId") or params.get("analysis_scope_id")),
            run_id=_clean(params.get("runId") or params.get("run_id")),
        )
    if stage in UNSCOPED_STATUS_STAGES:
        canonical_stage = "genome_prepare" if stage == "genome_prepare_v2" else stage
        return StageIdentity(
            stage=canonical_stage,
            prefix=prefix,
            scope_id="",
            scope_file="",
            scope_mode="",
            analysis_route="",
            member_set_id="",
            result_group=f"{canonical_stage}_global",
            family="" if canonical_stage.startswith("rnaseq") or canonical_stage == "annotation" else _clean(params.get("geneFamily") or params.get("gene_family") or (prefix.split(".")[-1] if "." in prefix else "")),
        )
    workspace = workspace_root / job_id if job_id else None
    scope_file = _clean(params.get("scopeFile") or params.get("scope_file"))
    scope_data = load_scope_file(scope_file, workspace) if scope_file else {}
    if not scope_data:
        scope_data = scope_data_from_params(prefix, params)

    explicit_scope_id = _clean(params.get("scopeId") or params.get("scope_id"))
    if explicit_scope_id:
        parsed_scope = _scope_data_from_scope_id(explicit_scope_id)
        scope_data["scope_id"] = explicit_scope_id
        parsed_family = _clean(parsed_scope.get("family"))
        if parsed_family:
            scope_data["family"] = parsed_family
        # scopeId is the canonical identity.  If params also carry stale
        # scopeMode/reference fields from a broader plan, do not let those
        # fields rebuild a different scope around the explicit scopeId.
        scope_data["scope_mode"] = _clean(parsed_scope.get("scope_mode"))
        scope_data["target_species"] = list(parsed_scope.get("target_species") or [])
        scope_data["reference_species"] = list(parsed_scope.get("reference_species") or [])
        scope_data["comparative_species"] = list(parsed_scope.get("comparative_species") or [])
        scope_data["selected_species"] = list(parsed_scope.get("selected_species") or [])
    family = _clean(scope_data.get("family") or params.get("geneFamily") or params.get("gene_family") or (prefix.split(".")[-1] if "." in prefix else ""))
    scope_data["family"] = family

    if explicit_scope_id:
        targets = _as_list(scope_data.get("target_species"))
        refs = _as_list(scope_data.get("reference_species"))
        comps = _as_list(scope_data.get("comparative_species"))
        scope_mode = _clean(scope_data.get("scope_mode"))
    else:
        targets = _as_list(scope_data.get("target_species") or params.get("targetSpecies") or params.get("target_species"))
        refs = _as_list(scope_data.get("reference_species") or params.get("referenceSpecies") or params.get("reference_species") or params.get("refSpeciesList"))
        comps = _as_list(scope_data.get("comparative_species") or params.get("comparativeSpecies") or params.get("comparative_species"))
        scope_mode = _clean(scope_data.get("scope_mode") or params.get("scopeMode") or params.get("scope_mode"))
    if not scope_mode:
        if targets and refs and comps:
            scope_mode = "target_plus_reference_plus_comparative"
        elif targets and refs:
            scope_mode = "target_plus_reference"
        elif targets:
            scope_mode = "target_only"
    scope_data.update({
        "target_species": _canonicalize_species_for_job(job_id, workspace_root, targets),
        "reference_species": _canonicalize_species_for_job(job_id, workspace_root, refs),
        "comparative_species": _canonicalize_species_for_job(job_id, workspace_root, comps),
        "scope_mode": scope_mode,
    })
    targets = list(scope_data.get("target_species") or [])
    refs = list(scope_data.get("reference_species") or [])
    comps = list(scope_data.get("comparative_species") or [])
    scope_data["selected_species"] = selected_species_for_mode(targets, refs, comps, scope_mode)
    if not explicit_scope_id and family and scope_mode:
        scope_data["scope_id"] = build_scope_id(family, targets, refs, comps, scope_mode)
    elif not _clean(scope_data.get("scope_id")) and family and scope_mode:
        scope_data["scope_id"] = build_scope_id(family, targets, refs, comps, scope_mode)

    route = _clean(params.get("analysisRoute") or params.get("analysis_route"))
    if not route:
        route = _route_hint_from_prefix(stage, prefix, params)
    if not route:
        route = infer_analysis_route(stage, scope_data)
    if stage == "subfamily_confirm":
        route = "confirmed_subfamily"
    member_set = _clean(params.get("memberSetId") or params.get("member_set_id"))
    requires_confirmed = _truthy(params.get("requiresConfirmedSubfamily") or params.get("requires_confirmed_subfamily"))
    scope_id = _clean(scope_data.get("scope_id"))
    if (
        requires_confirmed
        and not member_set
        and scope_id
        and stage not in {"subfamily_confirm", "reference_classification"}
        and route not in {"subfamily_evidence", "subfamily_prediction", "confirmed_subfamily"}
    ):
        member_set = f"{scope_id}:confirmed_subfamily:all_members"
    if stage == "subfamily_confirm" and member_set and f":{route}:" not in member_set:
        member_set = ""
    if not member_set and route and route != "unscoped" and scope_id:
        member_set = member_set_id_for(prefix, scope_data, route)

    context = build_stage_context(
        stage=stage,
        prefix=prefix,
        scope_data=scope_data,
        scope_file=scope_file,
        analysis_route=route,
        member_set_id=member_set,
    )
    selected = _as_list(scope_data.get("selected_species") or context.selected_species)
    return StageIdentity(
        stage=stage,
        prefix=prefix,
        scope_id=_clean(scope_data.get("scope_id") or context.scope_id),
        scope_file=scope_file,
        scope_mode=scope_mode or context.scope_mode,
        analysis_route=route or context.analysis_route,
        member_set_id=member_set or (context.member_set_id if _clean(scope_data.get("scope_id")) else ""),
        result_group=context.result_group,
        family=family or context.family,
        target_species=targets or list(context.target_species),
        reference_species=refs or list(context.reference_species),
        comparative_species=comps or list(context.comparative_species),
        selected_species=selected or list(context.selected_species),
    )


def get_stage_status_for_identity(
    job_id: str,
    identity: StageIdentity,
    workspace_root: Path = WORKSPACE_ROOT,
    require_artifacts: bool = True,
    strict_identity: bool = True,
) -> StageStatusReport:
    if identity.stage in POPULATION_STAGES:
        population_identity = (identity.genotype_scope_id, identity.analysis_scope_id)
        if not all(population_identity):
            return StageStatusReport(
                stage=identity.stage,
                status="not_found",
                completed=False,
                errors=["population stage exact status requires both genotype_scope_id and analysis_scope_id"],
            )
    if identity.stage == "gwas_mlm":
        model_identity = (
            identity.genotype_scope_id, identity.phenotype_scope_id,
            identity.covariate_scope_id, identity.model_scope_id,
            identity.analysis_scope_id,
        )
        if any(model_identity) and not all(model_identity):
            return StageStatusReport(
                stage=identity.stage, status="not_found", completed=False,
                errors=["gwas_mlm exact status requires all five scientific scope IDs"],
            )
    if identity.stage == "gwas_plot":
        plot_identity = (
            identity.genotype_scope_id, identity.phenotype_scope_id,
            identity.covariate_scope_id, identity.source_model_scope_id,
            identity.analysis_scope_id,
        )
        if any(plot_identity) and not all(plot_identity):
            return StageStatusReport(
                stage=identity.stage, status="not_found", completed=False,
                errors=["gwas_plot exact status requires all five scientific scope IDs"],
            )
    if identity.stage == "gwas_emmax":
        emmax_identity = (
            identity.genotype_scope_id, identity.phenotype_scope_id,
            identity.model_scope_id, identity.analysis_scope_id,
        )
        if any(emmax_identity) and not all(emmax_identity):
            return StageStatusReport(
                stage=identity.stage, status="not_found", completed=False,
                errors=["gwas_emmax exact status requires all four scientific scope IDs"],
            )
    if identity.stage == "gwas_emplot":
        emplot_identity = (
            identity.genotype_scope_id, identity.phenotype_scope_id,
            identity.source_model_scope_id, identity.analysis_scope_id,
        )
        if any(emplot_identity) and not all(emplot_identity):
            return StageStatusReport(
                stage=identity.stage, status="not_found", completed=False,
                errors=["gwas_emplot exact status requires all four scientific scope IDs"],
            )
    if identity.stage == "gwas_glm":
        glm_identity = (
            identity.genotype_scope_id, identity.phenotype_scope_id,
            identity.covariate_scope_id, identity.model_scope_id,
            identity.analysis_scope_id,
        )
        if any(glm_identity) and not all(glm_identity):
            return StageStatusReport(
                stage=identity.stage, status="not_found", completed=False,
                errors=["gwas_glm exact status requires all five scientific scope IDs"],
            )
    gwas_scoped_stage = identity.stage.startswith("gwas_")
    scientific_scoped_stage = gwas_scoped_stage or identity.stage in POPULATION_STAGES
    return get_stage_status(
        job_id=job_id,
        prefix=identity.prefix,
        stage=identity.stage,
        workspace_root=workspace_root,
        require_artifacts=require_artifacts,
        scope_id=identity.scope_id,
        analysis_route=identity.analysis_route,
        member_set_id=identity.member_set_id,
        strict_identity=strict_identity,
        genotype_scope_id=identity.genotype_scope_id,
        phenotype_scope_id=identity.phenotype_scope_id if gwas_scoped_stage else "",
        covariate_scope_id=identity.covariate_scope_id if gwas_scoped_stage else "",
        model_scope_id=identity.model_scope_id if gwas_scoped_stage else "",
        source_model_scope_id=identity.source_model_scope_id if gwas_scoped_stage else "",
        analysis_scope_id=identity.analysis_scope_id if scientific_scoped_stage else "",
        wgs_sample_set_id=identity.wgs_sample_set_id,
        wgs_reference_id=identity.wgs_reference_id,
        wgs_analysis_id=identity.wgs_analysis_id,
    )


def explain_status_mismatch(identity: StageIdentity, report: StageStatusReport) -> str:
    found_scope = ""
    found_route = ""
    found_member = ""
    if isinstance(report.manifest, dict):
        found_scope = _clean(report.manifest.get("scope_id") or report.manifest.get("stage_context", {}).get("scope_id"))
        found_route = _clean(report.manifest.get("analysis_route") or report.manifest.get("parameters", {}).get("analysis_route"))
        found_member = _clean(report.manifest.get("member_set_id") or report.manifest.get("parameters", {}).get("member_set_id"))
    parts = [
        f"请求阶段: {identity.stage}",
        f"期望 scope: {identity.scope_id or 'unscoped'}",
        f"期望 route: {identity.analysis_route or 'unscoped'}",
        f"期望 member_set: {identity.member_set_id or 'unscoped'}",
    ]
    if report.manifest_path:
        parts.append(f"检查 manifest: {report.manifest_path}")
    if found_scope or found_route or found_member:
        parts.append(f"发现 scope/route/member_set: {found_scope or 'missing'} / {found_route or 'missing'} / {found_member or 'missing'}")
    if report.errors:
        parts.append("错误: " + "; ".join(str(x) for x in report.errors))
    if report.missing_artifacts:
        parts.append("缺失产物: " + "; ".join(str(x) for x in report.missing_artifacts[:5]))
    return "\n".join(parts)
