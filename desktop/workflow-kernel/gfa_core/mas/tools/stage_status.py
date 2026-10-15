"""
Unified stage status helpers.

The final source of truth for a completed stage is the stage manifest under
workspace/<job_id>/final plus the files referenced by manifest.artifacts.
Runtime task states from Celery/LangGraph are only scheduling states.
"""
from __future__ import annotations

import json
import re
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gfa_core.ppi_string_policy import species_to_string_code
from gfa_core.wgs_upstream_manifest import wgs_workspace_identity_matches


PROJECT_ROOT = Path(__file__).absolute().parents[3]
WORKSPACE_ROOT = PROJECT_ROOT / "workspace"
SUCCESS_STATUSES = {"success", "partial_success"}
RNASEQ_STATUS_STAGES = {
    "rnaseq_qc",
    "rnaseq_align",
    "rnaseq_quant",
    "rnaseq_de",
    "rnaseq_enrich",
    "rnaseq_go_enrich",
    "rnaseq_kegg_enrich",
    "rnaseq_sample_qc",
    "rnaseq_fgsea",
    "rnaseq_wgcna",
    "rnaseq_candidate_family",
    "rnaseq_report",
    "annotation",
    "annotation_go",
    "annotation_kegg",
}
WGS_STATUS_STAGES = {"wgs_qc", "wgs_index", "wgs_align", "wgs_markdup", "wgs_bam_qc", "wgs_haplotype_calling", "wgs_gvcf_combine", "wgs_joint_genotyping", "wgs_variant_select", "wgs_variant_filter", "wgs_impute", "wgs_maf_missing_filter"}
UNSCOPED_STATUS_STAGES = {"genome_prepare", "genome_prepare_v2", "sequence_extract_by_id", *RNASEQ_STATUS_STAGES, *WGS_STATUS_STAGES}
STAGE_ALIASES = {
    "genome_prepare": {"genome_prepare", "genome_prepare_v2"},
    "rnaseq_go_enrich": {"rnaseq_go_enrich", "rnaseq_enrich"},
    "rnaseq_kegg_enrich": {"rnaseq_kegg_enrich", "rnaseq_enrich"},
    "annotation": {"annotation", "annotation_go", "annotation_kegg"},
    "sequence_extract_by_id": {"sequence_extract_by_id", "sequence_extract"},
}

_GWAS_ARTIFACT_HASH_CACHE: dict[tuple[str, int, int, int, int, str], bool] = {}


def _gwas_artifact_sha_matches(path: Path, declared_sha: str) -> bool:
    """Hash once per file identity and metadata tuple during polling."""
    stat = path.stat()
    key = (
        str(path.resolve()), stat.st_dev, stat.st_ino, stat.st_size,
        stat.st_mtime_ns, stat.st_ctime_ns, declared_sha,
    )
    cached = _GWAS_ARTIFACT_HASH_CACHE.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    matches = digest.hexdigest() == declared_sha
    if len(_GWAS_ARTIFACT_HASH_CACHE) >= 256:
        _GWAS_ARTIFACT_HASH_CACHE.clear()
    _GWAS_ARTIFACT_HASH_CACHE[key] = matches
    return matches


@dataclass
class StageStatusReport:
    stage: str
    status: str
    completed: bool
    manifest_path: Path | None = None
    manifest_status: str | None = None
    manifest: dict[str, Any] | None = None
    artifacts_checked: int = 0
    missing_artifacts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    diagnosis_report: dict[str, Any] = field(default_factory=dict)
    binding_report: dict[str, Any] = field(default_factory=dict)
    manifest_quality: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "stage": self.stage,
            "status": self.status,
            "completed": self.completed,
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "manifest_status": self.manifest_status,
            "artifacts_checked": self.artifacts_checked,
            "missing_artifacts": self.missing_artifacts,
            "errors": self.errors,
            "diagnosis_report": self.diagnosis_report,
            "binding_report": self.binding_report,
            "manifest_quality": self.manifest_quality,
        }
        manifest = self.manifest or {}
        if self.stage == "ppi" and isinstance(manifest.get("ppi_provenance"), dict):
            data["ppi_provenance"] = manifest["ppi_provenance"]
        stage_context = manifest.get("stage_context") or manifest.get("stageContext") or {}
        params = manifest.get("parameters") or {}
        for key in (
            "prefix",
            "output_prefix",
            "scope_id",
            "scope_mode",
            "analysis_route",
            "member_set_id",
            "result_group",
            "target_species",
            "reference_species",
            "comparative_species",
            "selected_species",
            "scope_file",
            "id_contract_hash",
            "scope_type",
            "rnaseq_scope_id",
            "genotype_scope_id",
            "phenotype_scope_id",
            "covariate_scope_id",
            "model_scope_id",
            "source_model_scope_id",
            "analysis_scope_id",
            "wgs_sample_set_id",
            "wgs_reference_id",
            "wgs_analysis_id",
            "rnaseq_species",
            "rnaseq_sample_count",
            "rnaseq_condition_count",
            "rnaseq_condition_signature",
            "foreground_mode",
            "family_scope_id",
            "family_members_file",
            "triggered_expression",
            "triggered_expression_manifest",
        ):
            value = manifest.get(key)
            if value in (None, "", []):
                value = params.get(key)
            if value in (None, "", []):
                value = stage_context.get(key)
            if value not in (None, "", []):
                data[key] = value
        return data


def manifest_path_for(
    job_id: str,
    prefix: str,
    stage: str,
    workspace_root: Path = WORKSPACE_ROOT,
    scope_id: str = "",
) -> Path:
    final_dir = workspace_root / job_id / "final"
    if scope_id:
        from gfa_core.gene_family_scope import sanitize_token

        return final_dir / f"{prefix}.{sanitize_token(scope_id)}.{stage}.manifest.json"
    return final_dir / f"{prefix}.{stage}.manifest.json"


def _scope_signature(scope_id: str) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...], str]:
    """Compare gene-family scopes independent of species order."""
    parts = [part for part in str(scope_id or "").split(".") if part]
    family = parts[0] if parts else ""
    targets: list[str] = []
    refs: list[str] = []
    comps: list[str] = []
    mode = ""
    for part in parts[1:]:
        if part.startswith("target-"):
            targets = [x for x in part[len("target-") :].split("-") if x]
        elif part.startswith("ref-"):
            refs = [x for x in part[len("ref-") :].split("-") if x]
        elif part.startswith("comp-"):
            comps = [x for x in part[len("comp-") :].split("-") if x]
        else:
            mode = part
    return (
        family.lower(),
        tuple(sorted(x.lower() for x in targets)),
        tuple(sorted(x.lower() for x in refs)),
        tuple(sorted(x.lower() for x in comps)),
        mode.lower(),
    )


def _scope_matches(requested: tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...], str], candidate: tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...], str], stage: str) -> bool:
    if requested == candidate:
        return True
    if stage == "identify":
        req_family, req_targets, _req_refs, _req_comps, req_mode = requested
        cand_family, cand_targets, _cand_refs, _cand_comps, cand_mode = candidate
        # Identify materializes target family members. Reference/comparative roles
        # are only upstream search context, so legacy scoped manifests that carried
        # ref/comparative labels must still satisfy a target-only prerequisite.
        return (
            req_family == cand_family
            and set(req_targets).issubset(set(cand_targets))
        )
    return False


def _manifest_member_set_id(manifest: dict[str, Any]) -> str:
    return str(
        manifest.get("member_set_id")
        or manifest.get("parameters", {}).get("member_set_id")
        or manifest.get("stage_context", {}).get("member_set_id")
        or manifest.get("stageContext", {}).get("member_set_id")
        or ""
    )


def _member_set_matches(manifest: dict[str, Any], requested_member_set_id: str) -> bool:
    if not requested_member_set_id:
        return True
    manifest_member_set = _manifest_member_set_id(manifest)
    # Older stage manifests are scope-aware but do not persist member_set_id.
    # Treat missing member_set_id as compatible; explicit mismatches remain invalid.
    if not manifest_member_set:
        return True
    if manifest_member_set == requested_member_set_id:
        return True
    manifest_parts = manifest_member_set.split(":")
    requested_parts = requested_member_set_id.split(":")
    if len(manifest_parts) >= 3 and len(requested_parts) >= 3:
        return (
            _scope_signature(manifest_parts[0]) == _scope_signature(requested_parts[0])
            and manifest_parts[1:] == requested_parts[1:]
        )
    return manifest_member_set == requested_member_set_id


def _manifest_analysis_route(manifest: dict[str, Any]) -> str:
    return str(
        manifest.get("analysis_route")
        or manifest.get("parameters", {}).get("analysis_route")
        or manifest.get("stage_context", {}).get("analysis_route")
        or manifest.get("stageContext", {}).get("analysis_route")
        or ""
    )


def _identity_strict_for_route(stage: str, strict_identity: bool, analysis_route: str, member_set_id: str) -> bool:
    if not strict_identity:
        return False
    route = str(analysis_route or "")
    member = str(member_set_id or "")
    return bool(
        route in {"confirmed_subfamily", "subfamily_evidence", "subfamily_prediction", "multi_species"}
        or member
        or stage in {"phylogeny", "motif", "domain", "structure", "combo", "tfbs", "interspecies_synteny", "reference_classification"}
    )


def _find_exact_alias_manifest(
    manifest_path: Path,
    prefix: str,
    stage: str,
    scope_id: str = "",
) -> Path | None:
    allowed_stages = STAGE_ALIASES.get(stage, {stage})
    for filename_stage in sorted((allowed_stages | {stage}) - {stage}):
        if scope_id:
            try:
                from gfa_core.gene_family_scope import sanitize_token

                candidate = manifest_path.parent / f"{prefix}.{sanitize_token(scope_id)}.{filename_stage}.manifest.json"
            except Exception:
                continue
        else:
            candidate = manifest_path.parent / f"{prefix}.{filename_stage}.manifest.json"
        if candidate.exists():
            return candidate
    return None


def _find_latest_stage_manifest(
    final_dir: Path,
    prefix: str,
    stage: str,
    analysis_route: str = "",
    member_set_id: str = "",
    genotype_scope_id: str = "",
    phenotype_scope_id: str = "",
    covariate_scope_id: str = "",
    model_scope_id: str = "",
    source_model_scope_id: str = "",
    analysis_scope_id: str = "",
) -> Path | None:
    """Find the newest manifest for a stage when the caller has no explicit scope.

    This is intentionally only a non-strict fallback. Strict identity callers should
    pass scope_id/route/member_set so the exact scoped manifest is selected.
    """
    allowed_stages = STAGE_ALIASES.get(stage, {stage})
    filename_stages = sorted(allowed_stages | {stage})
    patterns: list[str] = []
    if prefix:
        for filename_stage in filename_stages:
            patterns.extend([
                f"{prefix}.{filename_stage}.manifest.json",
                f"{prefix}.{filename_stage}.*.manifest.json",
                f"{prefix}.*.{filename_stage}.manifest.json",
            ])
    for filename_stage in filename_stages:
        patterns.extend([f"*.{filename_stage}.manifest.json", f"*.{filename_stage}.*.manifest.json"])
    candidates: list[tuple[float, Path]] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for candidate in final_dir.glob(pattern):
            if candidate in seen:
                continue
            seen.add(candidate)
            try:
                candidate, manifest = _load_manifest_resolving_alias(candidate)
            except Exception:
                continue
            manifest_stage = str(manifest.get("stage") or "")
            if manifest_stage and manifest_stage not in allowed_stages:
                continue
            if prefix and str(manifest.get("prefix") or "") != prefix:
                continue
            if analysis_route:
                manifest_route = str(manifest.get("analysis_route") or manifest.get("parameters", {}).get("analysis_route") or "")
                if manifest_route and manifest_route != analysis_route:
                    continue
            if member_set_id and not _member_set_matches(manifest, member_set_id):
                continue
            if genotype_scope_id and str(manifest.get("genotype_scope_id") or "") != genotype_scope_id:
                continue
            if phenotype_scope_id and str(manifest.get("phenotype_scope_id") or "") != phenotype_scope_id:
                continue
            if covariate_scope_id and str(manifest.get("covariate_scope_id") or "") != covariate_scope_id:
                continue
            if model_scope_id and str(manifest.get("model_scope_id") or "") != model_scope_id:
                continue
            if source_model_scope_id and str(manifest.get("source_model_scope_id") or "") != source_model_scope_id:
                continue
            if analysis_scope_id and str(manifest.get("analysis_scope_id") or "") != analysis_scope_id:
                continue
            try:
                mtime = candidate.stat().st_mtime
            except OSError:
                mtime = 0
            candidates.append((mtime, candidate))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _find_equivalent_scoped_manifest(
    final_dir: Path,
    prefix: str,
    stage: str,
    scope_id: str = "",
    analysis_route: str = "",
    member_set_id: str = "",
) -> Path | None:
    if not scope_id:
        return None
    requested_scope = _scope_signature(scope_id)
    candidates: list[tuple[float, Path]] = []
    allowed_stages = STAGE_ALIASES.get(stage, {stage})
    filename_stages = sorted(allowed_stages | {stage})
    patterns = []
    if prefix:
        patterns.extend(f"{prefix}.*.{filename_stage}.manifest.json" for filename_stage in filename_stages)
    patterns.extend(f"*.{filename_stage}.manifest.json" for filename_stage in filename_stages)
    seen: set[Path] = set()
    for pattern in patterns:
        for candidate in final_dir.glob(pattern):
            if candidate in seen:
                continue
            seen.add(candidate)
            try:
                candidate, manifest = _load_manifest_resolving_alias(candidate)
            except Exception:
                continue
            if str(manifest.get("stage") or "") not in STAGE_ALIASES.get(stage, {stage}):
                continue
            manifest_scope = str(manifest.get("scope_id") or manifest.get("stage_context", {}).get("scope_id") or "")
            if not manifest_scope or not _scope_matches(requested_scope, _scope_signature(manifest_scope), stage):
                continue
            if analysis_route:
                manifest_route = str(manifest.get("analysis_route") or manifest.get("parameters", {}).get("analysis_route") or "")
                if manifest_route and manifest_route != analysis_route:
                    continue
            if member_set_id:
                if not _member_set_matches(manifest, member_set_id):
                    continue
            try:
                mtime = candidate.stat().st_mtime
            except OSError:
                mtime = 0
            candidates.append((mtime, candidate))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _canonical_path_from_alias(manifest_path: Path, manifest: dict[str, Any]) -> Path | None:
    for key in ("canonical_manifest", "canonical_manifest_path"):
        value = str(manifest.get(key) or "").strip()
        if not value:
            continue
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = manifest_path.parent / candidate.name if value.startswith("final/") else manifest_path.parent / candidate
        try:
            resolved_candidate = candidate.resolve()
            resolved_manifest = manifest_path.resolve()
            if resolved_candidate.parent != resolved_manifest.parent:
                continue
            if candidate.exists() and resolved_candidate != resolved_manifest:
                return candidate
        except OSError:
            continue

    # Older compatibility copies were byte-for-byte manifest copies and therefore
    # have no alias marker.  Keep exact scoped manifests authoritative; otherwise
    # allow output_prefix to point old aliases back to their canonical manifest.
    stage = str(manifest.get("stage") or "").strip()
    prefix = str(manifest.get("prefix") or "").strip()
    scope_id = str(manifest.get("scope_id") or manifest.get("stage_context", {}).get("scope_id") or "").strip()
    if stage and prefix and scope_id:
        try:
            from gfa_core.gene_family_scope import sanitize_token

            scoped_name = f"{prefix}.{sanitize_token(scope_id)}.{stage}.manifest.json"
            if manifest_path.name == scoped_name:
                return None
        except Exception:
            pass
    output_prefix = str(
        manifest.get("output_prefix")
        or manifest.get("parameters", {}).get("output_prefix")
        or manifest.get("stage_context", {}).get("output_prefix")
        or ""
    ).strip()
    if stage and output_prefix:
        candidate = manifest_path.parent / f"{output_prefix}.{stage}.manifest.json"
        try:
            if candidate.exists() and candidate.resolve() != manifest_path.resolve():
                return candidate
        except OSError:
            pass
    return None


def _load_manifest_resolving_alias(manifest_path: Path) -> tuple[Path, dict[str, Any]]:
    with open(manifest_path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    canonical = _canonical_path_from_alias(manifest_path, manifest)
    if canonical:
        with open(canonical, "r", encoding="utf-8") as handle:
            return canonical, json.load(handle)
    return manifest_path, manifest


def _load_manifest_for_identity(
    manifest_path: Path,
    *,
    stage: str,
    strict_identity: bool,
    analysis_route: str = "",
    member_set_id: str = "",
) -> tuple[Path, dict[str, Any]]:
    with open(manifest_path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    canonical = _canonical_path_from_alias(manifest_path, manifest)
    if not canonical:
        return manifest_path, manifest
    if _identity_strict_for_route(stage, strict_identity, analysis_route, member_set_id):
        alias_route = _manifest_analysis_route(manifest)
        alias_member = _manifest_member_set_id(manifest)
        if analysis_route and alias_route and alias_route != analysis_route:
            return manifest_path, manifest
        if member_set_id and alias_member and not _member_set_matches(manifest, member_set_id):
            return manifest_path, manifest
    with open(canonical, "r", encoding="utf-8") as handle:
        return canonical, json.load(handle)


def _migrate_legacy_manifest_if_present(manifest_path: Path, prefix: str, stage: str) -> Path | None:
    """Migrate legacy final/{stage}.manifest.json to final/{prefix}.{stage}.manifest.json."""
    legacy_path = manifest_path.parent / f"{stage}.manifest.json"
    if manifest_path.exists() or not legacy_path.exists():
        return None

    try:
        with open(legacy_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except Exception:
        return None

    if manifest.get("stage") != stage:
        return None

    manifest["prefix"] = manifest.get("prefix") or prefix
    manifest["stage"] = stage
    manifest["status"] = manifest.get("status") or "success"

    artifacts = manifest.get("artifacts")
    if isinstance(artifacts, dict) and "manifest" in artifacts and isinstance(artifacts["manifest"], dict):
        artifacts["manifest"]["path"] = f"final/{manifest_path.name}"
        artifacts["manifest"]["full_path"] = str(manifest_path.absolute())

    try:
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
    except Exception:
        return None

    return manifest_path


def _resolve_artifact_path(artifact: dict[str, Any], manifest: dict[str, Any], manifest_path: Path) -> Path | None:
    rel_value = artifact.get("path") or artifact.get("relpath_or_hint") or artifact.get("file")
    final_dir = manifest_path.parent
    workspace_dir = final_dir.parent
    project_root = workspace_dir.parent.parent
    input_dir = project_root / "input" / workspace_dir.name
    preferred_missing_path: Path | None = None

    def _inside_current_job(path: Path) -> bool:
        try:
            resolved = path.resolve()
            return resolved.is_relative_to(workspace_dir.resolve()) or resolved.is_relative_to(input_dir.resolve())
        except OSError:
            return False

    def _rebase_current_job_path(path: Path) -> Path | None:
        parts = path.parts
        for root_name, current_root in (("workspace", workspace_dir), ("input", input_dir)):
            for idx, part in enumerate(parts):
                if part != root_name:
                    continue
                if idx + 1 >= len(parts) or parts[idx + 1] != workspace_dir.name:
                    continue
                suffix = Path(*parts[idx + 2 :]) if idx + 2 < len(parts) else Path()
                candidate = current_root / suffix
                if candidate.exists():
                    return candidate
        return None

    if rel_value:
        rel_path = Path(str(rel_value))
        if rel_path.is_absolute():
            if _inside_current_job(rel_path):
                return rel_path
            rebased = _rebase_current_job_path(rel_path)
            if rebased:
                return rebased
            preferred_missing_path = rel_path
        else:
            workspace_root = manifest.get("workspace_root")
            if workspace_root:
                candidate = Path(workspace_root) / rel_path
                if candidate.exists() and _inside_current_job(candidate):
                    return candidate
                preferred_missing_path = preferred_missing_path or candidate

            workspace = manifest.get("workspace")
            if workspace:
                candidate = Path(workspace) / rel_path.name if str(rel_path).startswith("final/") else Path(workspace) / rel_path
                if candidate.exists() and _inside_current_job(candidate):
                    return candidate
                preferred_missing_path = preferred_missing_path or candidate

            # Most stage manifests live in workspace/<job_id>/final. Artifact paths are
            # historically mixed: final/foo.png is final-relative, while pep/analysis/*
            # and similar paths are workspace-relative.
            if str(rel_path).startswith("final/"):
                candidate = final_dir / rel_path.name
                if candidate.exists():
                    return candidate
                preferred_missing_path = preferred_missing_path or candidate
            else:
                workspace_candidate = workspace_dir / rel_path
                if workspace_candidate.exists():
                    return workspace_candidate
                preferred_missing_path = preferred_missing_path or workspace_candidate
                final_candidate = final_dir / rel_path
                if final_candidate.exists():
                    return final_candidate
                preferred_missing_path = preferred_missing_path or final_candidate

    path_value = artifact.get("full_path")
    if path_value:
        full_path = Path(str(path_value))
        if _inside_current_job(full_path):
            return full_path
        rebased = _rebase_current_job_path(full_path)
        if rebased:
            return rebased
    if preferred_missing_path is not None:
        return preferred_missing_path
    return None


def _rebase_manifest_paths_for_current_job(manifest: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
    """Return a runtime view of a manifest with copied-job paths rebound locally.

    Historical manifests can contain absolute paths to the project that originally
    created them.  When a job is copied into PlantAI-dev, API/status payloads must
    point at the current PlantAI-dev input/workspace trees instead of leaking or
    validating against the source project.
    """
    final_dir = manifest_path.parent
    workspace_dir = final_dir.parent
    project_root = workspace_dir.parent.parent
    input_dir = project_root / "input" / workspace_dir.name
    job_id = re.escape(workspace_dir.name)
    provenance_keys = {
        "external_resource_sources",
        "resource_diagnostics",
        "historical_annotation_roots",
        "shared_resource_roots",
        "checked_roots",
        "missing_roots",
    }

    def _rebase_text(value: str) -> str:
        def repl(match: re.Match[str]) -> str:
            root_kind = match.group("root")
            replacement = workspace_dir if root_kind == "workspace" else input_dir
            return str(replacement) + match.group("trail")

        pattern = re.compile(rf"/[^\s\"']*?/(?P<root>workspace|input)/{job_id}(?P<trail>/?)")
        rebased = pattern.sub(repl, value)
        rebased = re.sub(
            r"/[^\s\"']*/bbav5_0502_Yang_Tihuan\.7880(?:\.bak)?",
            str(project_root),
            rebased,
        )
        return rebased

    def _walk(value: Any, key_path: tuple[str, ...] = ()) -> Any:
        if key_path and key_path[-1] in provenance_keys:
            return value
        if isinstance(value, str):
            return _rebase_text(value)
        if isinstance(value, list):
            return [_walk(item, key_path) for item in value]
        if isinstance(value, tuple):
            return [_walk(item, key_path) for item in value]
        if isinstance(value, dict):
            return {key: _walk(item, (*key_path, str(key))) for key, item in value.items()}
        return value

    return _walk(manifest)


def _iter_artifacts(manifest: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    artifacts = manifest.get("artifacts") or {}
    if isinstance(artifacts, dict):
        items = []
        for name, info in artifacts.items():
            if isinstance(info, dict):
                items.append((str(name), info))
            elif isinstance(info, str) and info.strip():
                items.append((str(name), {"path": info.strip()}))
        return items
    if isinstance(artifacts, list):
        items = []
        for index, info in enumerate(artifacts):
            if isinstance(info, dict):
                name = str(info.get("artifact") or info.get("name") or index)
                items.append((name, info))
            elif isinstance(info, str) and info.strip():
                items.append((str(index), {"path": info.strip()}))
        return items
    return []


def _is_optional_missing_artifact(stage: str, artifact_name: str, artifact: dict[str, Any]) -> bool:
    """Artifacts that are useful for inspection but not required for stage completion.

    RNA-seq QC may keep MultiQC plus markdown summaries while pruning individual
    per-sample FastQC HTML files.  Those per-sample reports are redundant with
    the MultiQC summary and should be surfaced as a quality warning, not as a
    failed prerequisite for downstream RNA-seq stages.
    """
    name = str(artifact_name or "")
    path_text = " ".join(str(artifact.get(key) or "") for key in ("path", "full_path", "file", "relpath_or_hint"))
    lowered = f"{name} {path_text}".lower()
    if stage == "rnaseq_qc" and name.startswith("qc_report_") and ("fastqc" in lowered):
        return True
    if stage == "rnaseq_quant" and name in {"salmon_tximport_summary", "multiqc_report"}:
        return True
    if stage in {"rnaseq_de", "rnaseq_go_enrich", "rnaseq_kegg_enrich", "rnaseq_fgsea", "rnaseq_wgcna"}:
        return any(token in lowered for token in ("_plot", "barplot", "dotplot", "heatmap", ".png", ".pdf"))
    return False


def _manifest_reference_species_code(manifest: dict[str, Any]) -> str:
    for section in (manifest, manifest.get("parameters") or {}, manifest.get("stage_context") or {}, manifest.get("stageContext") or {}):
        value = section.get("reference_species") or section.get("referenceSpecies")
        if isinstance(value, list) and value:
            return species_to_string_code(str(value[0] or ""), "").lower()
        if isinstance(value, str) and value.strip():
            return species_to_string_code(value, "").lower()
    return ""


def _ortholog_mapping_reference_species_code(mapping_path: Path) -> str:
    try:
        with mapping_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 5:
                    return species_to_string_code(parts[4], "").lower()
                return ""
    except OSError:
        return ""
    return ""


def _legacy_rnaseq_status_from_outputs(final_dir: Path, prefix: str, stage: str) -> StageStatusReport | None:
    """Infer status for older RNA-seq runs that produced outputs before manifests.

    This is deliberately narrow and evidence-based.  It does not create or
    rewrite manifests; it only lets the status API represent existing final
    outputs when old jobs lack a stage manifest.
    """
    evidence_specs: dict[str, list[tuple[str, str]]] = {
        "rnaseq_align": [
            ("align_report", f"{prefix}.rnaseq_align_report.md"),
            ("align_multiqc_data", f"{prefix}.rnaseq_align_multiqc_report_data"),
        ],
        "rnaseq_candidate_family": [
            ("recommendation", f"{prefix}.rnaseq_candidate_family.recommendation.tsv"),
            ("evidence_matrix", f"{prefix}.rnaseq_candidate_family.evidence_matrix.tsv"),
            ("family_enrichment", f"{prefix}.rnaseq_candidate_family.family_enrichment.tsv"),
            ("report", f"{prefix}.rnaseq_candidate_family_report.md"),
        ],
    }
    specs = evidence_specs.get(stage)
    if not specs:
        return None
    artifacts: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for artifact_name, filename in specs:
        path = final_dir / filename
        if path.exists():
            artifacts[artifact_name] = {
                "artifact": artifact_name,
                "path": f"final/{filename}",
                "full_path": str(path.absolute()),
            }
        else:
            missing.append(f"{artifact_name}: {path}")
    extra_metrics: dict[str, Any] = {}
    if stage == "rnaseq_align":
        bam_count = len(list(final_dir.glob("*.sorted.bam")))
        flagstat_count = len(list(final_dir.glob("*.flagstat.txt")))
        extra_metrics.update({"sorted_bam_count": bam_count, "flagstat_count": flagstat_count})
        if bam_count <= 0:
            missing.append(f"sorted_bam: {final_dir}/*.sorted.bam")
    if missing:
        return None
    manifest = {
        "manifest_version": "legacy_inferred_v1",
        "stage": stage,
        "status": "legacy_inferred",
        "prefix": prefix,
        "artifacts": artifacts,
        "metrics": extra_metrics,
    }
    representative = next(iter(artifacts.values())).get("full_path") if artifacts else ""
    return StageStatusReport(
        stage=stage,
        status="legacy_inferred",
        completed=True,
        manifest_path=Path(representative) if representative else None,
        manifest_status="legacy_inferred",
        manifest=manifest,
        artifacts_checked=len(artifacts),
        manifest_quality={
            "legacy_inferred": True,
            "reason": "stage manifest is missing but final outputs provide the stage evidence",
        },
    )


def _current_rnaseq_library_strandness_signature(job_id: str, manifest: dict[str, Any], workspace_root: Path) -> str:
    if not job_id:
        return ""
    try:
        from gfa_core.rnaseq_context import resolve_rnaseq_library_strandness
    except Exception:
        return ""
    input_dir = workspace_root.parent / "input" / job_id
    sample_config = input_dir / "samples.yaml"
    if not sample_config.exists():
        sample_config = input_dir / "samples.yml"
    if not sample_config.exists():
        return ""
    sample_names: list[str] = []
    by_sample = manifest.get("rnaseq_library_strandness_by_sample") or manifest.get("parameters", {}).get("library_strandness_by_sample")
    if isinstance(by_sample, dict) and by_sample:
        sample_names = list(map(str, by_sample.keys()))
    else:
        metrics_samples = manifest.get("metrics", {}).get("samples")
        if isinstance(metrics_samples, dict):
            sample_names = list(map(str, metrics_samples.keys()))
    try:
        info = resolve_rnaseq_library_strandness(sample_config, samples=sample_names, require_all=False)
        return str(info.get("library_strandness_signature") or "")
    except Exception:
        return ""


def get_stage_status(
    job_id: str,
    prefix: str,
    stage: str,
    workspace_root: Path = WORKSPACE_ROOT,
    require_artifacts: bool = True,
    scope_id: str = "",
    analysis_route: str = "",
    member_set_id: str = "",
    strict_identity: bool = False,
    allow_legacy_migration: bool = True,
    genotype_scope_id: str = "",
    phenotype_scope_id: str = "",
    covariate_scope_id: str = "",
    model_scope_id: str = "",
    source_model_scope_id: str = "",
    analysis_scope_id: str = "",
    wgs_sample_set_id: str = "",
    wgs_reference_id: str = "",
    wgs_analysis_id: str = "",
    validate_wgs_upstream_authority: bool = True,
) -> StageStatusReport:
    stage = "genome_prepare" if stage == "genome_prepare_v2" else stage
    if stage == "extension_stage":
        try:
            from gfa_core.extension_stage.status import latest_extension_status

            ext = latest_extension_status(job_id, workspace_root)
            return StageStatusReport(
                stage="extension_stage",
                status=str(ext.get("status") or "unknown"),
                completed=bool(ext.get("completed")),
                manifest_path=Path(ext["manifest_path"]) if ext.get("manifest_path") else None,
                manifest=ext.get("manifest") if isinstance(ext.get("manifest"), dict) else None,
                artifacts_checked=int(ext.get("artifacts_checked") or 0),
                missing_artifacts=list(ext.get("missing_artifacts") or []),
                errors=list(ext.get("errors") or []),
                diagnosis_report=ext.get("diagnosis_report") if isinstance(ext.get("diagnosis_report"), dict) else {},
                binding_report=ext.get("binding_report") if isinstance(ext.get("binding_report"), dict) else {},
                manifest_quality=ext.get("manifest_quality") if isinstance(ext.get("manifest_quality"), dict) else {},
            )
        except Exception as exc:
            return StageStatusReport(stage="extension_stage", status="status_error", completed=False, errors=[str(exc)])
    if stage in UNSCOPED_STATUS_STAGES and stage not in WGS_STATUS_STAGES:
        scope_id = ""
        analysis_route = ""
        member_set_id = ""
        strict_identity = False
    if stage == "genome_prepare":
        # genome_prepare is job/species-level preprocessing. UI plans may still
        # pass a family prefix such as Target.SPL, but the stage deliberately
        # materializes as genome_prepare.genome_prepare.manifest.json.
        prefix = "genome_prepare"
    manifest_path = manifest_path_for(job_id, prefix, stage, workspace_root, scope_id=scope_id)
    if stage == "wgs_qc" and (not wgs_sample_set_id or not wgs_analysis_id):
        return StageStatusReport(stage=stage, status="not_found", completed=False, errors=["wgs_qc exact status requires wgs_sample_set_id and wgs_analysis_id"])
    if stage == "wgs_index" and (not wgs_reference_id or not wgs_analysis_id):
        return StageStatusReport(stage=stage, status="not_found", completed=False, errors=["wgs_index exact status requires wgs_reference_id and wgs_analysis_id"])
    if stage == "wgs_align" and (not wgs_sample_set_id or not wgs_reference_id or not wgs_analysis_id):
        return StageStatusReport(stage=stage, status="not_found", completed=False, errors=["wgs_align exact status requires wgs_sample_set_id, wgs_reference_id and wgs_analysis_id"])
    if stage == "wgs_markdup" and (not wgs_sample_set_id or not wgs_reference_id or not wgs_analysis_id):
        return StageStatusReport(stage=stage, status="not_found", completed=False, errors=["wgs_markdup exact status requires wgs_sample_set_id, wgs_reference_id and wgs_analysis_id"])
    if stage == "wgs_bam_qc" and (not wgs_sample_set_id or not wgs_reference_id or not wgs_analysis_id):
        return StageStatusReport(stage=stage, status="not_found", completed=False, errors=["wgs_bam_qc exact status requires wgs_sample_set_id, wgs_reference_id and wgs_analysis_id"])
    if stage == "wgs_haplotype_calling" and (not wgs_sample_set_id or not wgs_reference_id or not wgs_analysis_id):
        return StageStatusReport(stage=stage, status="not_found", completed=False, errors=["wgs_haplotype_calling exact status requires wgs_sample_set_id, wgs_reference_id and wgs_analysis_id"])
    if stage == "wgs_gvcf_combine" and (not wgs_sample_set_id or not wgs_reference_id or not wgs_analysis_id):
        return StageStatusReport(stage=stage, status="not_found", completed=False, errors=["wgs_gvcf_combine exact status requires wgs_sample_set_id, wgs_reference_id and wgs_analysis_id"])
    if stage == "wgs_joint_genotyping" and (not wgs_sample_set_id or not wgs_reference_id or not wgs_analysis_id):
        return StageStatusReport(stage=stage, status="not_found", completed=False, errors=["wgs_joint_genotyping exact status requires wgs_sample_set_id, wgs_reference_id and wgs_analysis_id"])
    if stage in {"wgs_variant_select", "wgs_variant_filter", "wgs_impute", "wgs_maf_missing_filter"} and (not wgs_sample_set_id or not wgs_reference_id or not wgs_analysis_id):
        return StageStatusReport(stage=stage, status="not_found", completed=False, errors=[f"{stage} exact status requires wgs_sample_set_id, wgs_reference_id and wgs_analysis_id"])
    if stage in {"wgs_variant_select", "wgs_variant_filter"}:
        final_dir = workspace_root / job_id / "final"
        matching_authorities: list[Path] = []
        for method in ("gatk", "bcftools"):
            candidate = final_dir / f"{prefix}.{stage}.{method}.manifest.json"
            if not candidate.is_file() or candidate.is_symlink():
                continue
            try:
                candidate_manifest = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                candidate_manifest.get("stage") == stage
                and candidate_manifest.get("method") == method
                and candidate_manifest.get("wgs_sample_set_id") == wgs_sample_set_id
                and candidate_manifest.get("wgs_reference_id") == wgs_reference_id
                and candidate_manifest.get("wgs_analysis_id") == wgs_analysis_id
            ):
                matching_authorities.append(candidate)
        if len(matching_authorities) != 1:
            return StageStatusReport(
                stage=stage,
                status="not_found",
                completed=False,
                manifest_path=manifest_path,
                errors=[f"No unique {stage} method authority matches the requested scientific identity"],
            )
        manifest_path = matching_authorities[0]
    if stage == "subfamily_confirm" and not manifest_path.exists():
        final_dir_for_confirm = workspace_root / job_id / "final"
        confirmed_candidates = []
        if final_dir_for_confirm.exists():
            family_token = prefix.split(".")[-1] if prefix else ""
            species_token = prefix.split(".")[0] if "." in prefix else ""
            for candidate in final_dir_for_confirm.glob("*confirmed_subfamily.tsv"):
                if not candidate.is_file() or candidate.stat().st_size <= 0:
                    continue
                name = candidate.name
                if family_token and family_token not in name:
                    continue
                if species_token and species_token not in name:
                    continue
                confirmed_candidates.append(candidate)
        if confirmed_candidates:
            manifest = {
                "stage": "subfamily_confirm",
                "prefix": prefix,
                "status": "success",
                "artifacts": {"confirmed_subfamily": {"full_path": str(sorted(confirmed_candidates)[-1])}},
                "analysis_route": analysis_route or "confirmed_subfamily",
                "member_set_id": member_set_id or "confirmed_subfamily",
            }
            return StageStatusReport(
                stage=stage,
                status="success",
                completed=True,
                manifest_path=sorted(confirmed_candidates)[-1],
                manifest_status="success",
                manifest=manifest,
                artifacts_checked=1,
            )
    if not manifest_path.exists():
        alias_path = _find_exact_alias_manifest(manifest_path, prefix, stage, scope_id=scope_id)
        if alias_path:
            manifest_path = alias_path
    if not manifest_path.exists() and allow_legacy_migration:
        migrated_path = _migrate_legacy_manifest_if_present(manifest_path, prefix, stage)
        if migrated_path:
            manifest_path = migrated_path
    if not manifest_path.exists():
        equivalent_path = _find_equivalent_scoped_manifest(
            manifest_path.parent,
            prefix,
            stage,
            scope_id=scope_id,
            analysis_route=analysis_route,
            member_set_id=member_set_id,
        )
        if equivalent_path:
            manifest_path = equivalent_path
        else:
            latest_path = None
            if not scope_id and (
                not strict_identity
                or (stage.startswith(("gwas_", "pop_")) and any((
                    genotype_scope_id, phenotype_scope_id, covariate_scope_id,
                    model_scope_id, source_model_scope_id, analysis_scope_id,
                )))
            ):
                latest_path = _find_latest_stage_manifest(
                manifest_path.parent,
                prefix,
                stage,
                analysis_route=analysis_route,
                member_set_id=member_set_id,
                    genotype_scope_id=genotype_scope_id,
                    phenotype_scope_id=phenotype_scope_id,
                    covariate_scope_id=covariate_scope_id,
                    model_scope_id=model_scope_id,
                    source_model_scope_id=source_model_scope_id,
                    analysis_scope_id=analysis_scope_id,
                )
            if latest_path:
                manifest_path = latest_path
            else:
                legacy_report = _legacy_rnaseq_status_from_outputs(manifest_path.parent, prefix, stage)
                if legacy_report:
                    return legacy_report
                return StageStatusReport(
                    stage=stage,
                    status="not_found",
                    completed=False,
                    manifest_path=manifest_path,
                    errors=[f"Manifest not found: {manifest_path}"],
                )

    try:
        manifest_path, manifest = _load_manifest_for_identity(
            manifest_path,
            stage=stage,
            strict_identity=strict_identity,
            analysis_route=analysis_route,
            member_set_id=member_set_id,
        )
    except Exception as exc:
        return StageStatusReport(
            stage=stage,
            status="invalid_manifest",
            completed=False,
            manifest_path=manifest_path,
            errors=[f"Manifest is not valid JSON: {exc}"],
        )
    manifest = _rebase_manifest_paths_for_current_job(manifest, manifest_path)

    if analysis_route:
        manifest_route = str(manifest.get("analysis_route") or manifest.get("parameters", {}).get("analysis_route") or "")
        if manifest_route and manifest_route != analysis_route:
            equivalent_path = _find_equivalent_scoped_manifest(
                manifest_path.parent,
                prefix,
                stage,
                scope_id=scope_id,
                analysis_route=analysis_route,
                member_set_id=member_set_id,
            )
            if equivalent_path and equivalent_path != manifest_path:
                try:
                    manifest_path, manifest = _load_manifest_for_identity(
                        equivalent_path,
                        stage=stage,
                        strict_identity=strict_identity,
                        analysis_route=analysis_route,
                        member_set_id=member_set_id,
                    )
                    manifest = _rebase_manifest_paths_for_current_job(manifest, manifest_path)
                except Exception:
                    pass

    manifest_status = str(manifest.get("status") or "unknown")
    errors: list[str] = []
    missing_artifacts: list[str] = []
    optional_missing_artifacts: list[str] = []
    wgs_bam_qc_bundle: Path | None = None
    wgs_bam_qc_workspace: Path | None = None
    wgs_haplotype_bundle: Path | None = None
    wgs_haplotype_workspace: Path | None = None
    wgs_combine_bundle: Path | None = None
    wgs_combine_workspace: Path | None = None
    wgs_joint_bundle: Path | None = None
    wgs_joint_workspace: Path | None = None
    wgs_select_bundle: Path | None = None
    wgs_select_workspace: Path | None = None
    wgs_filter_bundle: Path | None = None
    wgs_filter_workspace: Path | None = None
    wgs_impute_bundle: Path | None = None
    wgs_impute_workspace: Path | None = None
    wgs_maf_bundle: Path | None = None
    wgs_maf_workspace: Path | None = None

    if stage == "wgs_bam_qc":
        expected_workspace = (workspace_root / job_id).resolve()
        wgs_bam_qc_workspace = expected_workspace
        declared_workspace = str(manifest.get("workspace") or "")
        run_id = str(manifest.get("run_id") or "")
        upstream_ids = manifest.get("upstream_wgs_analysis_ids")
        if manifest.get("manifest_version") != "1.0":
            errors.append("wgs_bam_qc Manifest version must be 1.0")
        if not wgs_workspace_identity_matches(declared_workspace, expected_workspace):
            errors.append("wgs_bam_qc workspace authority mismatch")
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            errors.append("wgs_bam_qc run_id is missing or invalid")
        if (
            not isinstance(upstream_ids, dict)
            or set(upstream_ids) != {"wgs_markdup", "wgs_index"}
            or any(
                not re.fullmatch(r"wgs-analysis:[0-9a-f]{64}", str(value or ""))
                for value in upstream_ids.values()
            )
        ):
            errors.append("wgs_bam_qc upstream analysis identities are incomplete")
        if re.fullmatch(r"[0-9a-f]{32}", run_id):
            raw_bundle = (
                expected_workspace
                / "final"
                / ".wgs_bam_qc_runs"
                / prefix
                / run_id
            )
            current = expected_workspace
            for part in raw_bundle.relative_to(expected_workspace).parts:
                current = current / part
                if current.is_symlink():
                    errors.append(
                        "wgs_bam_qc immutable run bundle contains a symlink"
                    )
                    break
            wgs_bam_qc_bundle = raw_bundle.resolve()
        if validate_wgs_upstream_authority and isinstance(upstream_ids, dict) and set(upstream_ids) == {
            "wgs_markdup", "wgs_index"
        }:
            try:
                from gfa_core.wgs_upstream_manifest import resolve_wgs_artifacts

                resolve_wgs_artifacts(
                    expected_workspace / "final" / f"{prefix}.wgs_markdup.manifest.json",
                    "wgs_markdup",
                    ("markdup_bam", "markdup_bam_index"),
                    expected_sample_set_id=wgs_sample_set_id,
                    expected_reference_id=wgs_reference_id,
                    expected_analysis_id=str(upstream_ids["wgs_markdup"]),
                )
                resolve_wgs_artifacts(
                    expected_workspace / "final" / f"{prefix}.wgs_index.manifest.json",
                    "wgs_index",
                    ("reference_fai",),
                    expected_reference_id=wgs_reference_id,
                    expected_analysis_id=str(upstream_ids["wgs_index"]),
                )
            except (ValueError, OSError) as exc:
                errors.append(f"wgs_bam_qc upstream authority validation failed: {exc}")

    if stage == "wgs_haplotype_calling":
        expected_workspace = (workspace_root / job_id).resolve()
        wgs_haplotype_workspace = expected_workspace
        declared_workspace = str(manifest.get("workspace") or "")
        run_id = str(manifest.get("run_id") or "")
        upstream_ids = manifest.get("upstream_wgs_analysis_ids")
        if manifest.get("manifest_version") != "1.0":
            errors.append("wgs_haplotype_calling Manifest version must be 1.0")
        if not wgs_workspace_identity_matches(declared_workspace, expected_workspace):
            errors.append("wgs_haplotype_calling workspace authority mismatch")
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            errors.append(
                "wgs_haplotype_calling run_id is missing or invalid"
            )
        if (
            not isinstance(upstream_ids, dict)
            or set(upstream_ids)
            != {"wgs_markdup", "wgs_bam_qc", "wgs_index"}
            or any(
                not re.fullmatch(
                    r"wgs-analysis:[0-9a-f]{64}", str(value or "")
                )
                for value in upstream_ids.values()
            )
        ):
            errors.append(
                "wgs_haplotype_calling upstream analysis identities are incomplete"
            )
        if re.fullmatch(r"[0-9a-f]{32}", run_id):
            raw_bundle = (
                expected_workspace
                / "final"
                / ".wgs_haplotype_calling_runs"
                / prefix
                / run_id
            )
            current = expected_workspace
            for part in raw_bundle.relative_to(expected_workspace).parts:
                current = current / part
                if current.is_symlink():
                    errors.append(
                        "wgs_haplotype_calling immutable run bundle contains a symlink"
                    )
                    break
            wgs_haplotype_bundle = raw_bundle.resolve()
        if (
            validate_wgs_upstream_authority
            and isinstance(upstream_ids, dict)
            and set(upstream_ids)
            == {"wgs_markdup", "wgs_bam_qc", "wgs_index"}
        ):
            try:
                bam_qc_authority = (
                    expected_workspace
                    / "final"
                    / f"{prefix}.wgs_bam_qc.manifest.json"
                )
                bam_qc_header = json.loads(
                    bam_qc_authority.read_text(encoding="utf-8")
                )
                from gfa_core.wgs_upstream_manifest import (
                    resolve_wgs_artifacts,
                    validate_wgs_bam_qc_completion,
                )
                resolved_markdup = resolve_wgs_artifacts(
                    expected_workspace
                    / "final"
                    / f"{prefix}.wgs_markdup.manifest.json",
                    "wgs_markdup",
                    ("markdup_bam", "markdup_bam_index"),
                    wgs_sample_set_id,
                    wgs_reference_id,
                    str(upstream_ids["wgs_markdup"]),
                )
                validate_wgs_bam_qc_completion(
                    bam_qc_header,
                    [
                        item.sample_id
                        for item in resolved_markdup["markdup_bam"]
                    ],
                )
                resolve_wgs_artifacts(
                    bam_qc_authority,
                    "wgs_bam_qc",
                    (
                        "bam_flagstat",
                        "bam_depth_summary",
                        "bam_qc_summary",
                        "bam_qc_report",
                    ),
                    wgs_sample_set_id,
                    wgs_reference_id,
                    str(upstream_ids["wgs_bam_qc"]),
                )
                resolve_wgs_artifacts(
                    expected_workspace
                    / "final"
                    / f"{prefix}.wgs_index.manifest.json",
                    "wgs_index",
                    ("reference_fasta",),
                    expected_reference_id=wgs_reference_id,
                    expected_analysis_id=str(upstream_ids["wgs_index"]),
                )
            except (ValueError, OSError) as exc:
                errors.append(
                    "wgs_haplotype_calling upstream authority validation "
                    f"failed: {exc}"
                )

    if stage == "wgs_gvcf_combine":
        expected_workspace = (workspace_root / job_id).resolve()
        wgs_combine_workspace = expected_workspace
        declared_workspace = str(manifest.get("workspace") or "")
        run_id = str(manifest.get("run_id") or "")
        upstream_ids = manifest.get("upstream_wgs_analysis_ids")
        if manifest.get("manifest_version") != "1.0":
            errors.append("wgs_gvcf_combine Manifest version must be 1.0")
        if not wgs_workspace_identity_matches(declared_workspace, expected_workspace):
            errors.append("wgs_gvcf_combine workspace authority mismatch")
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            errors.append("wgs_gvcf_combine run_id is missing or invalid")
        if (
            not isinstance(upstream_ids, dict)
            or set(upstream_ids) != {"wgs_haplotype_calling", "wgs_index"}
            or any(
                not re.fullmatch(
                    r"wgs-analysis:[0-9a-f]{64}", str(value or "")
                )
                for value in upstream_ids.values()
            )
        ):
            errors.append(
                "wgs_gvcf_combine upstream analysis identities are incomplete"
            )
        if re.fullmatch(r"[0-9a-f]{32}", run_id):
            raw_bundle = (
                expected_workspace
                / "final"
                / ".wgs_gvcf_combine_runs"
                / prefix
                / run_id
            )
            current = expected_workspace
            for part in raw_bundle.relative_to(expected_workspace).parts:
                current = current / part
                if current.is_symlink():
                    errors.append(
                        "wgs_gvcf_combine immutable run bundle contains a symlink"
                    )
                    break
            wgs_combine_bundle = raw_bundle.resolve()
        if validate_wgs_upstream_authority and isinstance(upstream_ids, dict) and set(upstream_ids) == {
            "wgs_haplotype_calling",
            "wgs_index",
        }:
            try:
                from gfa_core.wgs_upstream_manifest import resolve_wgs_artifacts

                haplotype_authority = (
                    expected_workspace
                    / "final"
                    / f"{prefix}.wgs_haplotype_calling.manifest.json"
                )
                haplotype_header = json.loads(
                    haplotype_authority.read_text(encoding="utf-8")
                )
                haplotype_upstream = haplotype_header.get(
                    "upstream_wgs_analysis_ids"
                )
                if (
                    not isinstance(haplotype_upstream, dict)
                    or haplotype_upstream.get("wgs_index")
                    != upstream_ids["wgs_index"]
                ):
                    raise ValueError(
                        "wgs_haplotype_calling index lineage mismatch"
                    )
                resolve_wgs_artifacts(
                    haplotype_authority,
                    "wgs_haplotype_calling",
                    ("gvcf", "gvcf_index"),
                    expected_sample_set_id=wgs_sample_set_id,
                    expected_reference_id=wgs_reference_id,
                    expected_analysis_id=str(
                        upstream_ids["wgs_haplotype_calling"]
                    ),
                )
                resolve_wgs_artifacts(
                    expected_workspace
                    / "final"
                    / f"{prefix}.wgs_index.manifest.json",
                    "wgs_index",
                    ("reference_fasta", "reference_fai", "reference_dict"),
                    expected_reference_id=wgs_reference_id,
                    expected_analysis_id=str(upstream_ids["wgs_index"]),
                )
            except (ValueError, OSError) as exc:
                errors.append(
                    "wgs_gvcf_combine upstream authority validation failed: "
                    f"{exc}"
                )

    if stage == "wgs_joint_genotyping":
        expected_workspace = (workspace_root / job_id).resolve()
        wgs_joint_workspace = expected_workspace
        declared_workspace = str(manifest.get("workspace") or "")
        run_id = str(manifest.get("run_id") or "")
        upstream_ids = manifest.get("upstream_wgs_analysis_ids")
        if manifest.get("manifest_version") != "1.0":
            errors.append("wgs_joint_genotyping Manifest version must be 1.0")
        if not wgs_workspace_identity_matches(declared_workspace, expected_workspace):
            errors.append("wgs_joint_genotyping workspace authority mismatch")
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            errors.append("wgs_joint_genotyping run_id is missing or invalid")
        if (
            not isinstance(upstream_ids, dict)
            or set(upstream_ids) != {"wgs_gvcf_combine", "wgs_index"}
            or any(
                not re.fullmatch(
                    r"wgs-analysis:[0-9a-f]{64}", str(value or "")
                )
                for value in upstream_ids.values()
            )
        ):
            errors.append(
                "wgs_joint_genotyping upstream analysis identities are incomplete"
            )
        if re.fullmatch(r"[0-9a-f]{32}", run_id):
            raw_bundle = (
                expected_workspace
                / "final"
                / ".wgs_joint_genotyping_runs"
                / prefix
                / run_id
            )
            current = expected_workspace
            for part in raw_bundle.relative_to(expected_workspace).parts:
                current = current / part
                if current.is_symlink():
                    errors.append(
                        "wgs_joint_genotyping immutable run bundle contains a symlink"
                    )
                    break
            wgs_joint_bundle = raw_bundle.resolve()
        if validate_wgs_upstream_authority and isinstance(upstream_ids, dict) and set(upstream_ids) == {
            "wgs_gvcf_combine",
            "wgs_index",
        }:
            try:
                from gfa_core.wgs_upstream_manifest import resolve_wgs_artifacts

                combine_authority = (
                    expected_workspace
                    / "final"
                    / f"{prefix}.wgs_gvcf_combine.manifest.json"
                )
                combine_header = json.loads(
                    combine_authority.read_text(encoding="utf-8")
                )
                combine_upstream = combine_header.get(
                    "upstream_wgs_analysis_ids"
                )
                if (
                    not isinstance(combine_upstream, dict)
                    or combine_upstream.get("wgs_index")
                    != upstream_ids["wgs_index"]
                ):
                    raise ValueError("wgs_gvcf_combine index lineage mismatch")
                resolve_wgs_artifacts(
                    combine_authority,
                    "wgs_gvcf_combine",
                    ("combined_gvcf", "combined_gvcf_index"),
                    expected_sample_set_id=wgs_sample_set_id,
                    expected_reference_id=wgs_reference_id,
                    expected_analysis_id=str(
                        upstream_ids["wgs_gvcf_combine"]
                    ),
                )
                resolve_wgs_artifacts(
                    expected_workspace
                    / "final"
                    / f"{prefix}.wgs_index.manifest.json",
                    "wgs_index",
                    ("reference_fasta", "reference_fai", "reference_dict"),
                    expected_reference_id=wgs_reference_id,
                    expected_analysis_id=str(upstream_ids["wgs_index"]),
                )
            except (ValueError, OSError) as exc:
                errors.append(
                    "wgs_joint_genotyping upstream authority validation failed: "
                    f"{exc}"
                )

    if stage == "wgs_variant_select":
        expected_workspace = (workspace_root / job_id).resolve()
        wgs_select_workspace = expected_workspace
        declared_workspace = str(manifest.get("workspace") or "")
        run_id = str(manifest.get("run_id") or "")
        method = str(manifest.get("method") or "")
        parameters = manifest.get("parameters") or {}
        upstream_ids = manifest.get("upstream_wgs_analysis_ids")
        if manifest.get("manifest_version") != "1.0":
            errors.append("wgs_variant_select Manifest version must be 1.0")
        if not wgs_workspace_identity_matches(declared_workspace, expected_workspace):
            errors.append("wgs_variant_select workspace authority mismatch")
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            errors.append("wgs_variant_select run_id is missing or invalid")
        if method not in {"gatk", "bcftools"} or parameters.get("method") != method:
            errors.append("wgs_variant_select method identity is invalid")
        if (
            not isinstance(upstream_ids, dict)
            or set(upstream_ids) != {"wgs_joint_genotyping"}
            or not re.fullmatch(
                r"wgs-analysis:[0-9a-f]{64}",
                str((upstream_ids or {}).get("wgs_joint_genotyping") or ""),
            )
        ):
            errors.append(
                "wgs_variant_select upstream analysis identity is incomplete"
            )
        if re.fullmatch(r"[0-9a-f]{32}", run_id) and method in {"gatk", "bcftools"}:
            raw_bundle = (
                expected_workspace
                / "final"
                / ".wgs_variant_select_runs"
                / prefix
                / method
                / run_id
            )
            current = expected_workspace
            for part in raw_bundle.relative_to(expected_workspace).parts:
                current = current / part
                if current.is_symlink():
                    errors.append(
                        "wgs_variant_select immutable run bundle contains a symlink"
                    )
                    break
            wgs_select_bundle = raw_bundle.resolve()
        if validate_wgs_upstream_authority and isinstance(upstream_ids, dict) and set(upstream_ids) == {
            "wgs_joint_genotyping"
        }:
            try:
                from gfa_core.wgs_upstream_manifest import resolve_wgs_artifacts

                resolve_wgs_artifacts(
                    expected_workspace
                    / "final"
                    / f"{prefix}.wgs_joint_genotyping.manifest.json",
                    "wgs_joint_genotyping",
                    ("joint_vcf", "joint_vcf_index"),
                    expected_sample_set_id=wgs_sample_set_id,
                    expected_reference_id=wgs_reference_id,
                    expected_analysis_id=str(
                        upstream_ids["wgs_joint_genotyping"]
                    ),
                )
            except (ValueError, OSError) as exc:
                errors.append(
                    "wgs_variant_select upstream authority validation failed: "
                    f"{exc}"
                )

    if stage == "wgs_variant_filter":
        expected_workspace = (workspace_root / job_id).resolve()
        wgs_filter_workspace = expected_workspace
        declared_workspace = str(manifest.get("workspace") or "")
        run_id = str(manifest.get("run_id") or "")
        method = str(manifest.get("method") or "")
        parameters = manifest.get("parameters") or {}
        upstream_ids = manifest.get("upstream_wgs_analysis_ids")
        if manifest.get("manifest_version") != "1.0":
            errors.append("wgs_variant_filter Manifest version must be 1.0")
        if not wgs_workspace_identity_matches(declared_workspace, expected_workspace):
            errors.append("wgs_variant_filter workspace authority mismatch")
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            errors.append("wgs_variant_filter run_id is missing or invalid")
        if method not in {"gatk", "bcftools"} or parameters.get("method") != method:
            errors.append("wgs_variant_filter method identity is invalid")
        if (
            not isinstance(upstream_ids, dict)
            or set(upstream_ids) != {"wgs_variant_select"}
            or not re.fullmatch(
                r"wgs-analysis:[0-9a-f]{64}",
                str((upstream_ids or {}).get("wgs_variant_select") or ""),
            )
        ):
            errors.append(
                "wgs_variant_filter upstream analysis identity is incomplete"
            )
        if re.fullmatch(r"[0-9a-f]{32}", run_id) and method in {"gatk", "bcftools"}:
            raw_bundle = (
                expected_workspace
                / "final"
                / ".wgs_variant_filter_runs"
                / prefix
                / method
                / run_id
            )
            current = expected_workspace
            for part in raw_bundle.relative_to(expected_workspace).parts:
                current = current / part
                if current.is_symlink():
                    errors.append(
                        "wgs_variant_filter immutable run bundle contains a symlink"
                    )
                    break
            wgs_filter_bundle = raw_bundle.resolve()
        if validate_wgs_upstream_authority and isinstance(upstream_ids, dict) and set(upstream_ids) == {
            "wgs_variant_select"
        }:
            try:
                from gfa_core.wgs_upstream_manifest import resolve_wgs_artifacts

                select_matches = []
                for upstream_method in ("gatk", "bcftools"):
                    candidate = (
                        expected_workspace
                        / "final"
                        / f"{prefix}.wgs_variant_select.{upstream_method}.manifest.json"
                    )
                    if not candidate.is_file():
                        continue
                    try:
                        payload = json.loads(candidate.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    if payload.get("wgs_analysis_id") == upstream_ids["wgs_variant_select"]:
                        select_matches.append((candidate, upstream_method))
                if len(select_matches) != 1:
                    raise ValueError("no unique Stage09 method authority")
                select_authority, upstream_method = select_matches[0]
                resolve_wgs_artifacts(
                    select_authority,
                    "wgs_variant_select",
                    (
                        "selected_snp_vcf",
                        "selected_snp_vcf_index",
                        "selected_indel_vcf",
                        "selected_indel_vcf_index",
                    ),
                    expected_sample_set_id=wgs_sample_set_id,
                    expected_reference_id=wgs_reference_id,
                    expected_analysis_id=str(upstream_ids["wgs_variant_select"]),
                    expected_method=upstream_method,
                )
            except (ValueError, OSError) as exc:
                errors.append(
                    "wgs_variant_filter upstream authority validation failed: "
                    f"{exc}"
                )

    if stage == "wgs_impute":
        expected_workspace = (workspace_root / job_id).resolve()
        wgs_impute_workspace = expected_workspace
        declared_workspace = str(manifest.get("workspace") or "")
        run_id = str(manifest.get("run_id") or "")
        upstream_ids = manifest.get("upstream_wgs_analysis_ids")
        upstream_method = str(manifest.get("upstream_method") or "")
        if manifest.get("manifest_version") != "1.0":
            errors.append("wgs_impute Manifest version must be 1.0")
        if not wgs_workspace_identity_matches(declared_workspace, expected_workspace):
            errors.append("wgs_impute workspace authority mismatch")
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            errors.append("wgs_impute run_id is missing or invalid")
        if manifest.get("method") != "beagle":
            errors.append("wgs_impute method identity is invalid")
        if (
            upstream_method not in {"gatk", "bcftools"}
            or not isinstance(upstream_ids, dict)
            or set(upstream_ids) != {"wgs_variant_filter"}
            or not re.fullmatch(
                r"wgs-analysis:[0-9a-f]{64}",
                str((upstream_ids or {}).get("wgs_variant_filter") or ""),
            )
        ):
            errors.append("wgs_impute upstream analysis identity is incomplete")
        if re.fullmatch(r"[0-9a-f]{32}", run_id):
            raw_bundle = (
                expected_workspace
                / "final"
                / ".wgs_impute_runs"
                / prefix
                / run_id
            )
            current = expected_workspace
            for part in raw_bundle.relative_to(expected_workspace).parts:
                current = current / part
                if current.is_symlink():
                    errors.append(
                        "wgs_impute immutable run bundle contains a symlink"
                    )
                    break
            wgs_impute_bundle = raw_bundle.resolve()
        if (
            validate_wgs_upstream_authority
            and isinstance(upstream_ids, dict)
            and set(upstream_ids) == {"wgs_variant_filter"}
            and upstream_method in {"gatk", "bcftools"}
        ):
            try:
                from gfa_core.wgs_upstream_manifest import resolve_wgs_artifacts

                resolve_wgs_artifacts(
                    expected_workspace
                    / "final"
                    / (
                        f"{prefix}.wgs_variant_filter."
                        f"{upstream_method}.manifest.json"
                    ),
                    "wgs_variant_filter",
                    ("pass_snp_vcf", "pass_snp_vcf_index"),
                    expected_sample_set_id=wgs_sample_set_id,
                    expected_reference_id=wgs_reference_id,
                    expected_analysis_id=str(
                        upstream_ids["wgs_variant_filter"]
                    ),
                    expected_method=upstream_method,
                )
            except (ValueError, OSError) as exc:
                errors.append(
                    "wgs_impute upstream authority validation failed: "
                    f"{exc}"
                )

    if stage == "wgs_maf_missing_filter":
        expected_workspace = (workspace_root / job_id).resolve()
        wgs_maf_workspace = expected_workspace
        declared_workspace = str(manifest.get("workspace") or "")
        run_id = str(manifest.get("run_id") or "")
        upstream_ids = manifest.get("upstream_wgs_analysis_ids")
        parameters = manifest.get("parameters") or {}
        if manifest.get("manifest_version") != "1.0":
            errors.append("wgs_maf_missing_filter Manifest version must be 1.0")
        if not wgs_workspace_identity_matches(declared_workspace, expected_workspace):
            errors.append("wgs_maf_missing_filter workspace authority mismatch")
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            errors.append("wgs_maf_missing_filter run_id is missing or invalid")
        if (
            manifest.get("method") != "bcftools"
            or parameters.get("method") != "bcftools"
            or parameters.get("maf") != 0.05
            or parameters.get("max_missing") != 0.5
            or parameters.get("max_missing_semantics")
            != "maximum_missing_fraction"
            or parameters.get("maf_semantics")
            != "second_most_common_observed_allele_frequency"
        ):
            errors.append("wgs_maf_missing_filter parameter identity is invalid")
        if (
            not isinstance(upstream_ids, dict)
            or set(upstream_ids) != {"wgs_impute"}
            or not re.fullmatch(
                r"wgs-analysis:[0-9a-f]{64}",
                str((upstream_ids or {}).get("wgs_impute") or ""),
            )
        ):
            errors.append(
                "wgs_maf_missing_filter upstream analysis identity is incomplete"
            )
        if re.fullmatch(r"[0-9a-f]{32}", run_id):
            raw_bundle = (
                expected_workspace
                / "final"
                / ".wgs_maf_missing_filter_runs"
                / prefix
                / run_id
            )
            current = expected_workspace
            for part in raw_bundle.relative_to(expected_workspace).parts:
                current = current / part
                if current.is_symlink():
                    errors.append(
                        "wgs_maf_missing_filter immutable run bundle contains "
                        "a symlink"
                    )
                    break
            wgs_maf_bundle = raw_bundle.resolve()
        if (
            validate_wgs_upstream_authority
            and isinstance(upstream_ids, dict)
            and set(upstream_ids) == {"wgs_impute"}
        ):
            try:
                from gfa_core.wgs_upstream_manifest import resolve_wgs_artifacts

                resolve_wgs_artifacts(
                    expected_workspace
                    / "final"
                    / f"{prefix}.wgs_impute.manifest.json",
                    "wgs_impute",
                    (
                        "imputed_snp_vcf",
                        "imputed_snp_vcf_index",
                        "impute_report",
                        "impute_log",
                    ),
                    expected_sample_set_id=wgs_sample_set_id,
                    expected_reference_id=wgs_reference_id,
                    expected_analysis_id=str(upstream_ids["wgs_impute"]),
                )
                resolve_wgs_artifacts(
                    manifest_path,
                    "wgs_maf_missing_filter",
                    (
                        "final_snp_vcf",
                        "final_snp_vcf_index",
                        "maf_missing_report",
                        "maf_missing_stats",
                    ),
                    expected_sample_set_id=wgs_sample_set_id,
                    expected_reference_id=wgs_reference_id,
                    expected_analysis_id=wgs_analysis_id,
                )
            except (ValueError, OSError) as exc:
                errors.append(
                    "wgs_maf_missing_filter authority validation failed: "
                    f"{exc}"
                )

    manifest_stage = manifest.get("stage")
    allowed_stages = STAGE_ALIASES.get(stage, {stage})
    if manifest_stage and manifest_stage not in allowed_stages:
        errors.append(f"Manifest stage mismatch: expected {stage}, got {manifest_stage}")
    if scope_id:
        manifest_scope = str(manifest.get("scope_id") or manifest.get("stage_context", {}).get("scope_id") or "")
        if not manifest_scope:
            if strict_identity:
                errors.append(f"Manifest scope_id missing: expected {scope_id}")
        elif not _scope_matches(_scope_signature(scope_id), _scope_signature(manifest_scope), stage):
            errors.append(f"Manifest scope_id mismatch: expected {scope_id}, got {manifest_scope}")
    if genotype_scope_id:
        manifest_genotype_scope = str(
            manifest.get("genotype_scope_id")
            or manifest.get("parameters", {}).get("genotype_scope_id")
            or manifest.get("stage_context", {}).get("genotype_scope_id")
            or ""
        )
        if not manifest_genotype_scope:
            errors.append(f"Manifest genotype_scope_id missing: expected {genotype_scope_id}")
        elif manifest_genotype_scope != genotype_scope_id:
            errors.append(
                "Manifest genotype_scope_id mismatch: "
                f"expected {genotype_scope_id}, got {manifest_genotype_scope}"
            )
    for scope_key, expected_scope in (
        ("phenotype_scope_id", phenotype_scope_id),
        ("covariate_scope_id", covariate_scope_id),
        ("model_scope_id", model_scope_id),
        ("source_model_scope_id", source_model_scope_id),
        ("analysis_scope_id", analysis_scope_id),
    ):
        if not expected_scope:
            continue
        manifest_scope = str(
            manifest.get(scope_key)
            or manifest.get("parameters", {}).get(scope_key)
            or manifest.get("stage_context", {}).get(scope_key)
            or ""
        )
        if not manifest_scope:
            errors.append(f"Manifest {scope_key} missing: expected {expected_scope}")
        elif manifest_scope != expected_scope:
            errors.append(f"Manifest {scope_key} mismatch: expected {expected_scope}, got {manifest_scope}")
    strict_stage_identity = bool(strict_identity and stage != "identify")
    if analysis_route:
        manifest_route = str(manifest.get("analysis_route") or manifest.get("parameters", {}).get("analysis_route") or "")
        if not manifest_route and strict_stage_identity:
            errors.append(f"Manifest analysis_route missing: expected {analysis_route}")
        elif manifest_route and manifest_route != analysis_route:
            errors.append(f"Manifest analysis_route mismatch: expected {analysis_route}, got {manifest_route}")
    if member_set_id:
        manifest_member_set = _manifest_member_set_id(manifest)
        route_requires_member = _identity_strict_for_route(stage, strict_identity, analysis_route, member_set_id)
        if not manifest_member_set and route_requires_member:
            if stage != "identify":
                errors.append(f"Manifest member_set_id missing: expected {member_set_id}")
        elif manifest_member_set and not _member_set_matches(manifest, member_set_id):
            errors.append(f"Manifest member_set_id mismatch: expected {member_set_id}, got {manifest_member_set}")
    if stage in {"rnaseq_align", "rnaseq_quant"}:
        manifest_strandness_signature = str(
            manifest.get("rnaseq_library_strandness_signature")
            or manifest.get("parameters", {}).get("library_strandness_signature")
            or ""
        )
        current_strandness_signature = _current_rnaseq_library_strandness_signature(job_id, manifest, workspace_root)
        if current_strandness_signature and not manifest_strandness_signature:
            errors.append("Manifest library_strandness_signature missing; rerun with current samples.yaml")
        elif current_strandness_signature and manifest_strandness_signature and current_strandness_signature != manifest_strandness_signature:
            errors.append(
                "Manifest library_strandness_signature mismatch: "
                f"expected {current_strandness_signature}, got {manifest_strandness_signature}"
            )

    if stage == "identify":
        hmm_models = manifest.get("hmm_models") if isinstance(manifest.get("hmm_models"), dict) else {}
        missing_hmms = hmm_models.get("missing_requested_pfams") or []
        hmm_status = str(hmm_models.get("status") or "").lower()
        if missing_hmms or hmm_status == "failed":
            missing_text = ",".join(str(x) for x in missing_hmms) if missing_hmms else "unknown"
            errors.append(f"Identify HMM audit failed: missing requested Pfam model(s): {missing_text}")

    artifacts = _iter_artifacts(manifest)
    artifacts_checked = 0
    required_artifacts_present = 0
    gwas_roles: set[str] = set()
    for artifact_name, artifact in artifacts:
        semantic_role = str(artifact.get("role") or artifact.get("artifact") or artifact_name)
        if stage == "gwas_fmt":
            gwas_roles.add(semantic_role)
        artifact_path = _resolve_artifact_path(artifact, manifest, manifest_path)
        optional_missing = _is_optional_missing_artifact(stage, artifact_name, artifact)
        if artifact_path is None:
            message = f"{artifact_name}: unresolved path"
            if optional_missing:
                optional_missing_artifacts.append(message)
            else:
                missing_artifacts.append(message)
            continue
        artifacts_checked += 1
        if stage == "wgs_bam_qc" and wgs_bam_qc_bundle is not None:
            if wgs_bam_qc_workspace is not None:
                try:
                    relative = artifact_path.relative_to(wgs_bam_qc_workspace)
                except ValueError:
                    relative = None
                if relative is not None:
                    current = wgs_bam_qc_workspace
                    for part in relative.parts:
                        current = current / part
                        if current.is_symlink():
                            errors.append(
                                f"{semantic_role} artifact path contains a symlink"
                            )
                            break
            try:
                artifact_path.resolve().relative_to(wgs_bam_qc_bundle)
            except ValueError:
                errors.append(
                    f"{semantic_role} artifact is outside the immutable run bundle"
                )
        if stage == "wgs_haplotype_calling" and wgs_haplotype_bundle is not None:
            if wgs_haplotype_workspace is not None:
                try:
                    relative = artifact_path.relative_to(wgs_haplotype_workspace)
                except ValueError:
                    errors.append(f"{semantic_role} artifact escapes WGS workspace")
                else:
                    current = wgs_haplotype_workspace
                    for part in relative.parts:
                        current = current / part
                        if current.is_symlink():
                            errors.append(
                                f"{semantic_role} artifact path contains a symlink"
                            )
                            break
            try:
                artifact_path.resolve().relative_to(wgs_haplotype_bundle)
            except ValueError:
                errors.append(
                    f"{semantic_role} artifact is outside the immutable run bundle"
                )
        if stage == "wgs_gvcf_combine" and wgs_combine_bundle is not None:
            if wgs_combine_workspace is not None:
                try:
                    relative = artifact_path.relative_to(
                        wgs_combine_workspace
                    )
                except ValueError:
                    errors.append(
                        f"{semantic_role} artifact escapes WGS workspace"
                    )
                else:
                    current = wgs_combine_workspace
                    for part in relative.parts:
                        current = current / part
                        if current.is_symlink():
                            errors.append(
                                f"{semantic_role} artifact path contains a symlink"
                            )
                            break
            try:
                artifact_path.resolve().relative_to(wgs_combine_bundle)
            except ValueError:
                errors.append(
                    f"{semantic_role} artifact is outside the immutable run bundle"
                )
        if stage == "wgs_joint_genotyping" and wgs_joint_bundle is not None:
            if wgs_joint_workspace is not None:
                try:
                    relative = artifact_path.relative_to(
                        wgs_joint_workspace
                    )
                except ValueError:
                    errors.append(
                        f"{semantic_role} artifact escapes WGS workspace"
                    )
                else:
                    current = wgs_joint_workspace
                    for part in relative.parts:
                        current = current / part
                        if current.is_symlink():
                            errors.append(
                                f"{semantic_role} artifact path contains a symlink"
                            )
                            break
            try:
                artifact_path.resolve().relative_to(wgs_joint_bundle)
            except ValueError:
                errors.append(
                    f"{semantic_role} artifact is outside the immutable run bundle"
                )
        if stage == "wgs_variant_select" and wgs_select_bundle is not None:
            if wgs_select_workspace is not None:
                try:
                    relative = artifact_path.relative_to(wgs_select_workspace)
                except ValueError:
                    errors.append(
                        f"{semantic_role} artifact escapes WGS workspace"
                    )
                else:
                    current = wgs_select_workspace
                    for part in relative.parts:
                        current = current / part
                        if current.is_symlink():
                            errors.append(
                                f"{semantic_role} artifact path contains a symlink"
                            )
                            break
            try:
                artifact_path.resolve().relative_to(wgs_select_bundle)
            except ValueError:
                errors.append(
                    f"{semantic_role} artifact is outside the immutable run bundle"
                )
        if stage == "wgs_variant_filter" and wgs_filter_bundle is not None:
            if wgs_filter_workspace is not None:
                try:
                    relative = artifact_path.relative_to(wgs_filter_workspace)
                except ValueError:
                    errors.append(
                        f"{semantic_role} artifact escapes WGS workspace"
                    )
                else:
                    current = wgs_filter_workspace
                    for part in relative.parts:
                        current = current / part
                        if current.is_symlink():
                            errors.append(
                                f"{semantic_role} artifact path contains a symlink"
                            )
                            break
            try:
                artifact_path.resolve().relative_to(wgs_filter_bundle)
            except ValueError:
                errors.append(
                    f"{semantic_role} artifact is outside the immutable run bundle"
                )
        if stage == "wgs_impute" and wgs_impute_bundle is not None:
            if wgs_impute_workspace is not None:
                try:
                    relative = artifact_path.relative_to(wgs_impute_workspace)
                except ValueError:
                    errors.append(
                        f"{semantic_role} artifact escapes WGS workspace"
                    )
                else:
                    current = wgs_impute_workspace
                    for part in relative.parts:
                        current = current / part
                        if current.is_symlink():
                            errors.append(
                                f"{semantic_role} artifact path contains a symlink"
                            )
                            break
            try:
                artifact_path.resolve().relative_to(wgs_impute_bundle)
            except ValueError:
                errors.append(
                    f"{semantic_role} artifact is outside the immutable run bundle"
                )
        if stage == "wgs_maf_missing_filter" and wgs_maf_bundle is not None:
            if wgs_maf_workspace is not None:
                try:
                    relative = artifact_path.relative_to(wgs_maf_workspace)
                except ValueError:
                    errors.append(
                        f"{semantic_role} artifact escapes WGS workspace"
                    )
                else:
                    current = wgs_maf_workspace
                    for part in relative.parts:
                        current = current / part
                        if current.is_symlink():
                            errors.append(
                                f"{semantic_role} artifact path contains a symlink"
                            )
                            break
            try:
                artifact_path.resolve().relative_to(wgs_maf_bundle)
            except ValueError:
                errors.append(
                    f"{semantic_role} artifact is outside the immutable run bundle"
                )
        if not artifact_path.exists():
            message = f"{artifact_name}: {artifact_path}"
            if optional_missing:
                optional_missing_artifacts.append(message)
            else:
                missing_artifacts.append(message)
            continue
        if stage in WGS_STATUS_STAGES:
            declared_sha = str(artifact.get("sha256") or "").lower()
            if not re.fullmatch(r"[0-9a-f]{64}", declared_sha):
                errors.append(f"{semantic_role} artifact sha256 is missing or invalid")
            elif not _gwas_artifact_sha_matches(artifact_path, declared_sha):
                errors.append(f"{semantic_role} artifact sha256 mismatch")
            declared_size = artifact.get("size")
            declared_size_bytes = artifact.get("size_bytes")
            if (isinstance(declared_size, bool) or not isinstance(declared_size, int)
                    or isinstance(declared_size_bytes, bool) or not isinstance(declared_size_bytes, int)
                    or declared_size <= 0 or declared_size_bytes != declared_size):
                errors.append(f"{semantic_role} artifact size proof is missing or invalid")
            elif artifact_path.stat().st_size != declared_size:
                errors.append(f"{semantic_role} artifact size mismatch")
        if stage.startswith("pop_"):
            declared_sha = str(artifact.get("sha256") or "").lower()
            if not re.fullmatch(r"[0-9a-f]{64}", declared_sha):
                errors.append(f"{semantic_role} artifact sha256 is missing or invalid")
            elif not _gwas_artifact_sha_matches(artifact_path, declared_sha):
                errors.append(f"{semantic_role} artifact sha256 mismatch")
            declared_size = artifact.get("size_bytes", artifact.get("size"))
            if not isinstance(declared_size, int) or declared_size <= 0:
                errors.append(f"{semantic_role} artifact size proof is missing or invalid")
            elif artifact_path.stat().st_size != declared_size:
                errors.append(f"{semantic_role} artifact size mismatch")
        if stage == "gwas_fmt" and semantic_role in {"sorted_vcf", "tassel_hapmap"}:
            declared_sha = str(artifact.get("sha256") or "").lower()
            if not re.fullmatch(r"[0-9a-f]{64}", declared_sha):
                errors.append(f"{semantic_role} artifact sha256 is missing or invalid")
            else:
                if not _gwas_artifact_sha_matches(artifact_path, declared_sha):
                    errors.append(f"{semantic_role} artifact sha256 mismatch")
        if not optional_missing:
            required_artifacts_present += 1
        if stage == "ortholog" and artifact_name == "ortholog_mapping":
            expected_reference_code = _manifest_reference_species_code(manifest)
            observed_reference_code = _ortholog_mapping_reference_species_code(artifact_path)
            if expected_reference_code and observed_reference_code and expected_reference_code != observed_reference_code:
                errors.append(
                    "Ortholog mapping reference species mismatch: "
                    f"expected {expected_reference_code}, got {observed_reference_code}"
                )
        # Combo artifacts may use short user-facing names; scope identity is
        # validated from manifest fields above rather than forced into filenames.

    if require_artifacts and not artifacts:
        errors.append("Manifest has no artifacts")
    if require_artifacts and stage.startswith("pop_"):
        from gfa_core.mas.stage_capabilities import CAPABILITIES

        capability = CAPABILITIES.get(stage)
        required_roles = {
            product.role for product in (capability.provides if capability else [])
            if product.role != "manifest"
        }
        present_roles = {
            str(artifact.get("role") or artifact.get("artifact") or artifact_name)
            for artifact_name, artifact in artifacts
        }
        for role in sorted(required_roles - present_roles):
            errors.append(f"Manifest missing required population artifact role: {role}")
        if stage == "pop_structure":
            metrics = manifest.get("metrics") if isinstance(manifest.get("metrics"), dict) else {}
            for value in metrics.get("requested_k") or []:
                try:
                    k = int(value)
                except (TypeError, ValueError):
                    errors.append(f"pop_structure requested_k contains invalid value: {value}")
                    continue
                for role in (f"admixture_q_k{k}", f"admixture_p_k{k}", f"admixture_log_k{k}"):
                    if role not in present_roles:
                        errors.append(f"Manifest missing required population artifact role: {role}")
            if metrics.get("gwas_k"):
                for role in ("gwas_k_q", "gwas_k_fam", "admixture_q", "sample_fam"):
                    if role not in present_roles:
                        errors.append(f"Manifest missing required population artifact role: {role}")
        if stage == "pop_ld_decay":
            artifact_by_role = {
                str(artifact.get("role") or artifact.get("artifact") or artifact_name): artifact
                for artifact_name, artifact in artifacts
            }
            qc_artifact = artifact_by_role.get("group_sample_qc")
            qc_path = _resolve_artifact_path(qc_artifact, manifest, manifest_path) if qc_artifact else None
            try:
                qc_payload = json.loads(qc_path.read_text(encoding="utf-8")) if qc_path and qc_path.is_file() else {}
                valid_groups = qc_payload.get("valid_groups") if isinstance(qc_payload, dict) else []
                if not isinstance(valid_groups, list):
                    raise ValueError("valid_groups is not a list")
            except Exception as exc:
                valid_groups = []
                errors.append(f"pop_ld_decay group_sample_qc is invalid: {exc}")
            metrics = manifest.get("metrics") if isinstance(manifest.get("metrics"), dict) else {}
            if int(metrics.get("valid_group_count") or 0) != len(valid_groups):
                errors.append("pop_ld_decay valid_group_count does not match group_sample_qc")
            for group in valid_groups:
                slug = str(group.get("slug") or "") if isinstance(group, dict) else ""
                if not slug:
                    errors.append("pop_ld_decay group_sample_qc contains a group without slug")
                    continue
                for role in (f"ld_group_stat_{slug}", f"ld_group_plot_png_{slug}", f"ld_group_plot_pdf_{slug}"):
                    if role not in present_roles:
                        errors.append(f"Manifest missing required population artifact role: {role}")
            if len(valid_groups) >= 2:
                for role in ("ld_multi_plot_png", "ld_multi_plot_pdf"):
                    if role not in present_roles:
                        errors.append(f"Manifest missing required population artifact role: {role}")
    if stage == "gwas_fmt":
        for role in sorted({"sorted_vcf", "tassel_hapmap"} - gwas_roles):
            errors.append(f"Manifest missing required gwas_fmt artifact role: {role}")
        metrics = manifest.get("metrics") if isinstance(manifest.get("metrics"), dict) else {}
        for metric in ("sample_conservation", "variant_conservation"):
            if metrics.get(metric) is not True:
                errors.append(f"gwas_fmt metric {metric} must be true")
        parameters = manifest.get("parameters") if isinstance(manifest.get("parameters"), dict) else {}
        lossy_mask = parameters.get("unsupported_marker_policy") == "mask" and metrics.get("lossy_conversion") is True
        if lossy_mask:
            if metrics.get("strict_genotype_conservation") is not True:
                errors.append("gwas_fmt metric strict_genotype_conservation must be true for mask policy")
            if int(metrics.get("strict_marker_count") or 0) <= 0:
                errors.append("gwas_fmt mask proof requires strict_marker_count > 0")
            if int(metrics.get("genotypes_compared") or 0) <= 0:
                errors.append("gwas_fmt mask proof requires genotypes_compared > 0")
            if int(metrics.get("unsupported_marker_count") or 0) <= 0:
                errors.append("gwas_fmt lossy mask manifest must record unsupported_marker_count > 0")
        elif metrics.get("genotype_conservation") is not True:
            errors.append("gwas_fmt metric genotype_conservation must be true")
        tassel = (manifest.get("software") or {}).get("tassel") if isinstance(manifest.get("software"), dict) else {}
        if not isinstance(tassel, dict) or str(tassel.get("version") or "unknown").strip().lower() == "unknown":
            errors.append("gwas_fmt TASSEL version must be proven")

    if stage == "wgs_qc":
        if manifest.get("wgs_sample_set_id") != wgs_sample_set_id or manifest.get("wgs_analysis_id") != wgs_analysis_id:
            errors.append("wgs_qc scientific identity mismatch")
        required_roles = {"clean_reads_r1", "clean_reads_r2", "fastp_html", "fastp_json"}
        sample_roles: dict[str, set[str]] = {}
        for _, artifact in artifacts:
            sample_id = str(artifact.get("sample_id") or "")
            role = str(artifact.get("role") or artifact.get("artifact") or "")
            if sample_id:
                sample_roles.setdefault(sample_id, set()).add(role)
            if artifact.get("sample_id"):
                sample_roles.setdefault(str(artifact["sample_id"]), set()).add(str(artifact.get("role") or artifact.get("artifact") or ""))
        for sample_id, roles in sample_roles.items():
            missing = required_roles - roles
            if missing:
                errors.append(f"wgs_qc sample {sample_id} missing required roles: {', '.join(sorted(missing))}")
        metrics = manifest.get("metrics") if isinstance(manifest.get("metrics"), dict) else {}
        if not sample_roles or set(metrics.get("input_sample_ids") or []) != set(metrics.get("output_sample_ids") or []):
            errors.append("wgs_qc input/output sample-set conservation failed")

    if stage == "wgs_index":
        if manifest.get("wgs_reference_id") != wgs_reference_id or manifest.get("wgs_analysis_id") != wgs_analysis_id:
            errors.append("wgs_index scientific identity mismatch")
        required_components = {".0123", ".amb", ".ann", ".bwt.2bit.64", ".pac"}
        roles = {str(artifact.get("role") or artifact.get("artifact") or "") for _, artifact in artifacts}
        components = {str(artifact.get("component") or "") for _, artifact in artifacts if str(artifact.get("role") or artifact.get("artifact") or "") == "bwa_mem2_index_component"}
        if not {"reference_fasta", "reference_fai", "reference_dict"}.issubset(roles) or components != required_components:
            errors.append("wgs_index required reference roles/components are incomplete")
    if stage == "wgs_align":
        if (manifest.get("wgs_sample_set_id") != wgs_sample_set_id
                or manifest.get("wgs_reference_id") != wgs_reference_id
                or manifest.get("wgs_analysis_id") != wgs_analysis_id):
            errors.append("wgs_align scientific identity mismatch")
        expected_samples = set((manifest.get("metrics") or {}).get("input_sample_ids") or [])
        output_samples = set((manifest.get("metrics") or {}).get("output_sample_ids") or [])
        by_sample: dict[str, set[str]] = {}
        for item in (manifest.get("artifacts") or {}).values():
            if isinstance(item, dict) and item.get("sample_id"):
                by_sample.setdefault(str(item["sample_id"]), set()).add(str(item.get("role") or item.get("artifact") or ""))
        required = {"sorted_bam", "sorted_bam_index", "align_log"}
        if not expected_samples or expected_samples != output_samples or set(by_sample) != expected_samples:
            errors.append("wgs_align input/output sample-set conservation failed")
        elif any(roles != required for roles in by_sample.values()):
            errors.append("wgs_align sample required roles are incomplete")

    if stage == "wgs_markdup":
        if (manifest.get("wgs_sample_set_id") != wgs_sample_set_id
                or manifest.get("wgs_reference_id") != wgs_reference_id
                or manifest.get("wgs_analysis_id") != wgs_analysis_id):
            errors.append("wgs_markdup scientific identity mismatch")
        expected_samples = set((manifest.get("metrics") or {}).get("input_sample_ids") or [])
        output_samples = set((manifest.get("metrics") or {}).get("output_sample_ids") or [])
        by_sample: dict[str, set[str]] = {}
        for item in (manifest.get("artifacts") or {}).values():
            if isinstance(item, dict) and item.get("sample_id"):
                by_sample.setdefault(str(item["sample_id"]), set()).add(str(item.get("role") or item.get("artifact") or ""))
        required = {"markdup_bam", "markdup_bam_index", "markdup_metrics", "markdup_log"}
        if not expected_samples or expected_samples != output_samples or set(by_sample) != expected_samples:
            errors.append("wgs_markdup input/output sample-set conservation failed")
        elif any(roles != required for roles in by_sample.values()):
            errors.append("wgs_markdup sample required roles are incomplete")
    if stage == "wgs_bam_qc":
        if (manifest.get("wgs_sample_set_id") != wgs_sample_set_id
                or manifest.get("wgs_reference_id") != wgs_reference_id
                or manifest.get("wgs_analysis_id") != wgs_analysis_id):
            errors.append("wgs_bam_qc scientific identity mismatch")
        expected_samples = set((manifest.get("metrics") or {}).get("input_sample_ids") or [])
        output_samples = set((manifest.get("metrics") or {}).get("output_sample_ids") or [])
        by_sample: dict[str, set[str]] = {}
        aggregate_roles: set[str] = set()
        for item in (manifest.get("artifacts") or {}).values():
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or item.get("artifact") or "")
            if item.get("sample_id"):
                by_sample.setdefault(str(item["sample_id"]), set()).add(role)
            else:
                aggregate_roles.add(role)
        required = {"bam_flagstat", "bam_depth_summary"}
        if not expected_samples or expected_samples != output_samples or set(by_sample) != expected_samples:
            errors.append("wgs_bam_qc input/output sample-set conservation failed")
        elif any(roles != required for roles in by_sample.values()):
            errors.append("wgs_bam_qc sample required roles are incomplete")
        if aggregate_roles != {"bam_qc_summary", "bam_qc_report"}:
            errors.append("wgs_bam_qc aggregate roles are incomplete")
        if (manifest.get("parameters") or {}).get("metric_name") != "coverage_ge_1x":
            errors.append("wgs_bam_qc coverage metric name is invalid")
    if stage == "wgs_haplotype_calling":
        if (
            manifest.get("wgs_sample_set_id") != wgs_sample_set_id
            or manifest.get("wgs_reference_id") != wgs_reference_id
            or manifest.get("wgs_analysis_id") != wgs_analysis_id
        ):
            errors.append("wgs_haplotype_calling scientific identity mismatch")
        expected_samples = set(
            (manifest.get("metrics") or {}).get("input_sample_ids") or []
        )
        output_samples = set(
            (manifest.get("metrics") or {}).get("output_sample_ids") or []
        )
        by_sample: dict[str, set[str]] = {}
        for item in (manifest.get("artifacts") or {}).values():
            if isinstance(item, dict) and item.get("sample_id"):
                by_sample.setdefault(str(item["sample_id"]), set()).add(
                    str(item.get("role") or item.get("artifact") or "")
                )
        required = {"gvcf", "gvcf_index", "haplotype_log"}
        if (
            not expected_samples
            or expected_samples != output_samples
            or set(by_sample) != expected_samples
        ):
            errors.append(
                "wgs_haplotype_calling input/output sample-set conservation failed"
            )
        elif any(roles != required for roles in by_sample.values()):
            errors.append(
                "wgs_haplotype_calling sample required roles are incomplete"
            )
    if stage == "wgs_gvcf_combine":
        if (
            manifest.get("wgs_sample_set_id") != wgs_sample_set_id
            or manifest.get("wgs_reference_id") != wgs_reference_id
            or manifest.get("wgs_analysis_id") != wgs_analysis_id
        ):
            errors.append("wgs_gvcf_combine scientific identity mismatch")
        metrics = manifest.get("metrics") or {}
        input_samples = metrics.get("input_sample_ids")
        output_samples = metrics.get("output_sample_ids")
        if (
            not isinstance(input_samples, list)
            or not input_samples
            or input_samples != output_samples
            or len(set(input_samples)) != len(input_samples)
        ):
            errors.append(
                "wgs_gvcf_combine input/output sample-set conservation failed"
            )
        artifacts_payload = manifest.get("artifacts") or {}
        if not isinstance(artifacts_payload, dict) or set(
            artifacts_payload
        ) != {"combined_gvcf", "combined_gvcf_index", "combine_log"}:
            errors.append(
                "wgs_gvcf_combine singleton required roles are incomplete"
            )
        elif any(
            not isinstance(item, dict)
            or str(item.get("role") or item.get("artifact") or "") != key
            or item.get("sample_id")
            for key, item in artifacts_payload.items()
        ):
            errors.append(
                "wgs_gvcf_combine singleton artifact identity is invalid"
            )
    if stage == "wgs_joint_genotyping":
        if (
            manifest.get("wgs_sample_set_id") != wgs_sample_set_id
            or manifest.get("wgs_reference_id") != wgs_reference_id
            or manifest.get("wgs_analysis_id") != wgs_analysis_id
        ):
            errors.append("wgs_joint_genotyping scientific identity mismatch")
        metrics = manifest.get("metrics") or {}
        input_samples = metrics.get("input_sample_ids")
        output_samples = metrics.get("output_sample_ids")
        if (
            not isinstance(input_samples, list)
            or not input_samples
            or input_samples != output_samples
            or len(set(input_samples)) != len(input_samples)
        ):
            errors.append(
                "wgs_joint_genotyping input/output sample-set conservation failed"
            )
        artifacts_payload = manifest.get("artifacts") or {}
        required_roles = {
            "joint_vcf",
            "joint_vcf_index",
            "joint_summary",
            "joint_log",
        }
        if (
            not isinstance(artifacts_payload, dict)
            or set(artifacts_payload) != required_roles
        ):
            errors.append(
                "wgs_joint_genotyping singleton required roles are incomplete"
            )
        elif any(
            not isinstance(item, dict)
            or str(item.get("role") or item.get("artifact") or "") != key
            or item.get("sample_id")
            for key, item in artifacts_payload.items()
        ):
            errors.append(
                "wgs_joint_genotyping singleton artifact identity is invalid"
            )
    if stage == "wgs_variant_select":
        if (
            manifest.get("wgs_sample_set_id") != wgs_sample_set_id
            or manifest.get("wgs_reference_id") != wgs_reference_id
            or manifest.get("wgs_analysis_id") != wgs_analysis_id
        ):
            errors.append("wgs_variant_select scientific identity mismatch")
        metrics = manifest.get("metrics") or {}
        input_samples = metrics.get("input_sample_ids")
        output_samples = metrics.get("output_sample_ids")
        if (
            not isinstance(input_samples, list)
            or not input_samples
            or input_samples != output_samples
            or len(set(input_samples)) != len(input_samples)
        ):
            errors.append(
                "wgs_variant_select input/output sample-set conservation failed"
            )
        artifacts_payload = manifest.get("artifacts") or {}
        required_roles = {
            "selected_snp_vcf",
            "selected_snp_vcf_index",
            "selected_indel_vcf",
            "selected_indel_vcf_index",
            "variant_select_report",
        }
        if (
            not isinstance(artifacts_payload, dict)
            or set(artifacts_payload) != required_roles
        ):
            errors.append(
                "wgs_variant_select singleton required roles are incomplete"
            )
        elif any(
            not isinstance(item, dict)
            or str(item.get("role") or item.get("artifact") or "") != key
            or item.get("sample_id")
            for key, item in artifacts_payload.items()
        ):
            errors.append(
                "wgs_variant_select singleton artifact identity is invalid"
            )
    if stage == "wgs_variant_filter":
        if (
            manifest.get("wgs_sample_set_id") != wgs_sample_set_id
            or manifest.get("wgs_reference_id") != wgs_reference_id
            or manifest.get("wgs_analysis_id") != wgs_analysis_id
        ):
            errors.append("wgs_variant_filter scientific identity mismatch")
        metrics = manifest.get("metrics") or {}
        input_samples = metrics.get("input_sample_ids")
        output_samples = metrics.get("output_sample_ids")
        if (
            not isinstance(input_samples, list)
            or not input_samples
            or input_samples != output_samples
            or len(set(input_samples)) != len(input_samples)
        ):
            errors.append(
                "wgs_variant_filter input/output sample-set conservation failed"
            )
        artifacts_payload = manifest.get("artifacts") or {}
        required_roles = {
            "filtered_snp_vcf",
            "filtered_snp_vcf_index",
            "pass_snp_vcf",
            "pass_snp_vcf_index",
            "filtered_indel_vcf",
            "filtered_indel_vcf_index",
            "pass_indel_vcf",
            "pass_indel_vcf_index",
            "variant_filter_report",
        }
        if (
            not isinstance(artifacts_payload, dict)
            or set(artifacts_payload) != required_roles
        ):
            errors.append(
                "wgs_variant_filter singleton required roles are incomplete"
            )
        elif any(
            not isinstance(item, dict)
            or str(item.get("role") or item.get("artifact") or "") != key
            or item.get("sample_id")
            for key, item in artifacts_payload.items()
        ):
            errors.append(
                "wgs_variant_filter singleton artifact identity is invalid"
            )
    if stage == "wgs_impute":
        if (
            manifest.get("wgs_sample_set_id") != wgs_sample_set_id
            or manifest.get("wgs_reference_id") != wgs_reference_id
            or manifest.get("wgs_analysis_id") != wgs_analysis_id
        ):
            errors.append("wgs_impute scientific identity mismatch")
        metrics = manifest.get("metrics") or {}
        input_samples = metrics.get("input_sample_ids")
        output_samples = metrics.get("output_sample_ids")
        input_sites = metrics.get("input_site_keys")
        output_sites = metrics.get("output_site_keys")
        if (
            not isinstance(input_samples, list)
            or not input_samples
            or input_samples != output_samples
            or len(set(input_samples)) != len(input_samples)
        ):
            errors.append("wgs_impute input/output sample-set conservation failed")
        if (
            not isinstance(input_sites, list)
            or not input_sites
            or input_sites != output_sites
        ):
            errors.append("wgs_impute ordered site-key conservation failed")
        artifacts_payload = manifest.get("artifacts") or {}
        required_roles = {
            "imputed_snp_vcf",
            "imputed_snp_vcf_index",
            "impute_report",
            "impute_log",
        }
        if (
            not isinstance(artifacts_payload, dict)
            or set(artifacts_payload) != required_roles
        ):
            errors.append("wgs_impute singleton required roles are incomplete")
        elif any(
            not isinstance(item, dict)
            or str(item.get("role") or item.get("artifact") or "") != key
            or item.get("sample_id")
            for key, item in artifacts_payload.items()
        ):
            errors.append("wgs_impute singleton artifact identity is invalid")
    if stage == "wgs_maf_missing_filter":
        if (
            manifest.get("wgs_sample_set_id") != wgs_sample_set_id
            or manifest.get("wgs_reference_id") != wgs_reference_id
            or manifest.get("wgs_analysis_id") != wgs_analysis_id
        ):
            errors.append("wgs_maf_missing_filter scientific identity mismatch")
        metrics = manifest.get("metrics") or {}
        input_samples = metrics.get("input_sample_ids")
        output_samples = metrics.get("output_sample_ids")
        retained_sites = metrics.get("retained_sites")
        if (
            not isinstance(input_samples, list)
            or not input_samples
            or input_samples != output_samples
            or len(set(input_samples)) != len(input_samples)
        ):
            errors.append(
                "wgs_maf_missing_filter sample-set conservation failed"
            )
        if (
            isinstance(retained_sites, bool)
            or not isinstance(retained_sites, int)
            or retained_sites < 1
        ):
            errors.append(
                "wgs_maf_missing_filter has no usable GWAS SNP variants"
            )
        artifacts_payload = manifest.get("artifacts") or {}
        required_roles = {
            "final_snp_vcf",
            "final_snp_vcf_index",
            "maf_missing_report",
            "maf_missing_stats",
        }
        if (
            not isinstance(artifacts_payload, dict)
            or set(artifacts_payload) != required_roles
        ):
            errors.append(
                "wgs_maf_missing_filter singleton required roles are incomplete"
            )
        elif any(
            not isinstance(item, dict)
            or str(item.get("role") or item.get("artifact") or "") != key
            or item.get("sample_id")
            for key, item in artifacts_payload.items()
        ):
            errors.append(
                "wgs_maf_missing_filter singleton artifact identity is invalid"
            )
        else:
            vcf_path = Path(
                str(artifacts_payload["final_snp_vcf"].get("full_path") or "")
            )
            index_path = Path(
                str(
                    artifacts_payload["final_snp_vcf_index"].get(
                        "full_path"
                    )
                    or ""
                )
            )
            if index_path != Path(f"{vcf_path}.tbi"):
                errors.append(
                    "wgs_maf_missing_filter TBI is not the final VCF sidecar"
                )
            elif vcf_path.is_file() and index_path.is_file():
                try:
                    import pysam

                    with pysam.VariantFile(str(vcf_path)) as vcf:
                        first = next(iter(vcf), None)
                    if first is None:
                        raise ValueError("VCF contains zero variants")
                    with pysam.VariantFile(str(vcf_path)) as indexed:
                        next(
                            indexed.fetch(str(first.contig), 0, None),
                            None,
                        )
                except Exception as exc:
                    errors.append(
                        "wgs_maf_missing_filter final VCF/TBI query failed: "
                        f"{exc}"
                    )
    success_statuses = set(SUCCESS_STATUSES)
    # genome_prepare partial_success means at least one species failed. Treat it as
    # a visible result, but not as a completed prerequisite, so users can upload
    # missing files and rerun preprocessing without manually deleting manifests.
    if stage == "genome_prepare":
        success_statuses.discard("partial_success")

    completed = (
        manifest_status in success_statuses
        and not errors
        and not missing_artifacts
        and (required_artifacts_present > 0 or not require_artifacts)
    )

    status = manifest_status if completed else "invalid_outputs"
    if manifest_status not in SUCCESS_STATUSES:
        status = manifest_status

    return StageStatusReport(
        stage=stage,
        status=status,
        completed=completed,
        manifest_path=manifest_path,
        manifest_status=manifest_status,
        manifest=manifest,
        artifacts_checked=artifacts_checked,
        missing_artifacts=missing_artifacts,
        manifest_quality={"optional_missing_artifacts": optional_missing_artifacts} if optional_missing_artifacts else {},
        errors=errors,
    )


def is_stage_completed(
    job_id: str,
    prefix: str,
    stage: str,
    workspace_root: Path = WORKSPACE_ROOT,
    scope_id: str = "",
) -> bool:
    return get_stage_status(job_id, prefix, stage, workspace_root, scope_id=scope_id).completed


def list_completed_stages(job_id: str, prefix: str = "", workspace_root: Path = WORKSPACE_ROOT) -> list[str]:
    final_dir = workspace_root / job_id / "final"
    if not final_dir.exists():
        return []

    completed: list[str] = []
    pattern = f"{prefix}.*.manifest.json" if prefix else "*.manifest.json"
    for manifest_path in sorted(final_dir.glob(pattern)):
        try:
            with open(manifest_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
        except Exception:
            continue

        manifest_prefix = str(manifest.get("prefix") or prefix)
        stage = str(manifest.get("stage") or "")
        if not stage:
            name = manifest_path.name
            if manifest_prefix and name.startswith(f"{manifest_prefix}."):
                stage = name[len(manifest_prefix) + 1 : -len(".manifest.json")]
            else:
                parts = name[: -len(".manifest.json")].split(".")
                stage = parts[-1] if parts else ""
        if not manifest_prefix or not stage:
            continue

        status = get_stage_status(job_id, manifest_prefix, stage, workspace_root)
        if status.completed and stage not in completed:
            completed.append(stage)
    return completed
