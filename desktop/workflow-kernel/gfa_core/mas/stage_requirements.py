"""Stage-level requirements resolver for bbav5 orchestration.

This module is intentionally read-only.  It combines stage identity, job
context, stage status and artifact resolution into a small report that planning,
execution and diagnosis layers can consume without each re-implementing file
and scope rules.

Phase 1 covers the core gene-family chain only:
- phylogeny: identify family proteins plus optional reference/comparative family proteins
- pep: target_only identify proteins, or same-scope phylogeny input FASTA for scoped routes

Callers should treat reports as shadow diagnostics unless an explicit feature
flag enables decision-making.
"""
from __future__ import annotations

from copy import copy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from gfa_core.mas.artifact_resolver import ArtifactResolver, ResolveCandidate
from gfa_core.mas.context_index import PROJECT_ROOT, ProjectContextIndex, build_project_context
from gfa_core.mas.stage_capabilities import ArtifactNeed
from gfa_core.mas.tools.stage_identity import StageIdentity, resolve_stage_identity
from gfa_core.mas.tools.stage_status import WORKSPACE_ROOT


@dataclass
class RequirementItem:
    key: str
    kind: str
    role: str
    required: bool = True
    produced_by: list[str] = field(default_factory=list)
    input_only: bool = False
    description: str = ""
    species: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResolvedArtifact:
    key: str
    path: str
    source: str
    producer_stage: str = ""
    scope_id: str = ""
    species: str = ""
    role: str = ""
    kind: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StageRequirementReport:
    stage: str
    identity: StageIdentity
    ready: bool
    requirements: list[RequirementItem] = field(default_factory=list)
    resolved: dict[str, ResolvedArtifact] = field(default_factory=dict)
    missing: list[RequirementItem] = field(default_factory=list)
    upstream_needed: list[str] = field(default_factory=list)
    blocked_reasons: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    shadow_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "identity": self.identity.to_dict(),
            "ready": self.ready,
            "requirements": [item.to_dict() for item in self.requirements],
            "resolved": {key: value.to_dict() for key, value in self.resolved.items()},
            "missing": [item.to_dict() for item in self.missing],
            "upstream_needed": list(self.upstream_needed),
            "blocked_reasons": list(self.blocked_reasons),
            "messages": list(self.messages),
            "shadow_only": self.shadow_only,
        }


CORE_REQUIREMENT_STAGES = {"identify", "phylogeny", "pep", "reference_classification", "motif", "domain", "structure", "combo", "subfamily_confirm", "tfbs"}


def resolve_stage_requirements(
    stage: str,
    prefix: str = "",
    job_id: str = "",
    params: dict[str, Any] | None = None,
    project_root: Path | str = PROJECT_ROOT,
    shadow_only: bool = True,
    project_context: ProjectContextIndex | None = None,
) -> StageRequirementReport:
    """Resolve stage requirements without changing planning or files."""
    params = dict(params or {})
    _normalize_scope_file_param(params, Path(project_root), job_id)
    identity = resolve_stage_identity(
        stage=stage,
        prefix=params.get("prefix") or prefix,
        params=params,
        job_id=job_id,
        workspace_root=Path(project_root) / "workspace",
    )
    report = StageRequirementReport(stage=identity.stage, identity=identity, ready=False, shadow_only=shadow_only)
    if not job_id:
        report.blocked_reasons.append("job_id_missing")
        report.messages.append("Cannot resolve requirements without job_id.")
        return report

    context = project_context
    if context is None:
        context = build_project_context(
            job_id=job_id,
            prefix=identity.prefix,
            project_root=project_root,
            include_stage_status=False,
        )
    else:
        context = copy(context)
    _apply_identity_to_context(context, identity)

    if identity.stage == "identify":
        _resolve_identify_requirements(report, context)
    elif identity.stage == "phylogeny":
        _resolve_phylogeny_requirements(report, context)
    elif identity.stage == "pep":
        _resolve_pep_requirements(report, context)
    elif identity.stage == "reference_classification":
        _resolve_reference_classification_requirements(report, context)
    elif identity.stage == "motif":
        _resolve_motif_requirements(report, context)
    elif identity.stage == "domain":
        _resolve_domain_requirements(report, context)
    elif identity.stage == "structure":
        _resolve_structure_requirements(report, context)
    elif identity.stage == "combo":
        _resolve_combo_requirements(report, context)
    elif identity.stage == "subfamily_confirm":
        _resolve_subfamily_confirm_requirements(report, context)
    else:
        report.blocked_reasons.append("stage_not_supported_in_requirements_phase3")
        report.messages.append(f"Stage requirements are not implemented for {identity.stage} in phase 3.")

    _finalize_report(report)
    return report


def resolve_many_stage_requirements(
    stages: Iterable[str],
    prefix: str = "",
    job_id: str = "",
    params_by_stage: dict[str, dict[str, Any]] | None = None,
    shared_params: dict[str, Any] | None = None,
    project_root: Path | str = PROJECT_ROOT,
    shadow_only: bool = True,
) -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    params_by_stage = params_by_stage or {}
    shared_params = shared_params or {}
    contexts_by_prefix: dict[str, ProjectContextIndex] = {}
    for stage in stages:
        stage_params = {**shared_params, **(params_by_stage.get(stage) or {})}
        if stage not in CORE_REQUIREMENT_STAGES:
            continue
        try:
            effective_prefix = stage_params.get("prefix") or prefix
            if effective_prefix not in contexts_by_prefix:
                contexts_by_prefix[effective_prefix] = build_project_context(
                    job_id=job_id,
                    prefix=effective_prefix,
                    project_root=project_root,
                    include_stage_status=False,
                )
            report = resolve_stage_requirements(
                stage=stage,
                prefix=effective_prefix,
                job_id=job_id,
                params=stage_params,
                project_root=project_root,
                shadow_only=shadow_only,
                project_context=contexts_by_prefix[effective_prefix],
            )
            reports[stage] = report.to_dict()
        except Exception as exc:
            identity = resolve_stage_identity(stage=stage, prefix=prefix, params=stage_params, job_id=job_id)
            reports[stage] = StageRequirementReport(
                stage=stage,
                identity=identity,
                ready=False,
                blocked_reasons=["requirements_resolver_error"],
                messages=[str(exc)],
                shadow_only=shadow_only,
            ).to_dict()
    return reports


def _normalize_scope_file_param(params: dict[str, Any], project_root: Path, job_id: str) -> None:
    scope_text = str(params.get("scopeFile") or params.get("scope_file") or "").strip()
    if not scope_text:
        return
    path = Path(scope_text).expanduser()
    candidates = [path]
    if not path.is_absolute():
        candidates.extend([
            project_root / path,
            project_root / "workspace" / job_id / path,
            project_root / "input" / job_id / path,
        ])
    for candidate in candidates:
        if candidate.exists():
            resolved = str(candidate.resolve())
            params["scopeFile"] = resolved
            params["scope_file"] = resolved
            return


def _apply_identity_to_context(context: ProjectContextIndex, identity: StageIdentity) -> None:
    if identity.family:
        context.family = identity.family
    if identity.target_species:
        context.target_species = list(identity.target_species)
    if identity.reference_species:
        context.reference_species = list(identity.reference_species)
    if identity.comparative_species:
        context.comparative_species = list(identity.comparative_species)
    setattr(context, "expected_scope_id", identity.scope_id)
    setattr(context, "expected_scope_mode", identity.scope_mode)
    setattr(context, "expected_analysis_route", identity.analysis_route)
    setattr(context, "expected_member_set_id", identity.member_set_id)
    setattr(context, "expected_output_prefix", getattr(identity, "output_prefix", "") or identity.prefix)



def _resolve_identify_requirements(report: StageRequirementReport, context: ProjectContextIndex) -> None:
    identity = report.identity
    targets = identity.target_species or context.target_species or _species_from_prefix(identity.prefix)
    if not targets:
        req = RequirementItem(
            key="target_proteome",
            kind="protein_fasta",
            role="target_proteome",
            produced_by=[],
            input_only=True,
            description="Target species proteome FASTA for Identify.",
        )
        _mark_missing(report, req, "No target species resolved for identify.")
        return
    for species in targets:
        req = RequirementItem(
            key=f"target_proteome:{species}",
            kind="protein_fasta",
            role="target_proteome",
            produced_by=[],
            input_only=True,
            description="Target species proteome FASTA for Identify.",
            species=species,
        )
        report.requirements.append(req)
        artifact = _resolve_target_proteome(context, identity, species)
        if artifact:
            report.resolved[req.key] = artifact
        else:
            _mark_missing(report, req, f"Missing target proteome FASTA for {species}; upload/select that species proteome.")

def _resolve_phylogeny_requirements(report: StageRequirementReport, context: ProjectContextIndex) -> None:
    identity = report.identity
    targets = identity.target_species or context.target_species or _species_from_prefix(identity.prefix)
    if not targets and identity.prefix:
        targets = [identity.prefix.split(".", 1)[0]] if "." in identity.prefix and not identity.prefix.startswith(("Multi.", "Target.")) else []
    if not targets:
        req = RequirementItem(
            key="target_family_pep",
            kind="protein_fasta",
            role="target",
            produced_by=["identify"],
            description="Target family protein FASTA from Identify.",
        )
        _mark_missing(report, req, "No target species resolved for phylogeny.")
    else:
        for species in targets:
            key = f"target_family_pep:{species}"
            req = RequirementItem(
                key=key,
                kind="protein_fasta",
                role="target",
                produced_by=["identify"],
                description="Target family protein FASTA from Identify.",
                species=species,
            )
            report.requirements.append(req)
            artifact = _resolve_family_members_pep(context, identity, species)
            if artifact:
                report.resolved[key] = artifact
            else:
                _mark_missing(report, req, f"Missing target family protein FASTA for {species}; Identify can produce it.")

    selected = set(identity.selected_species or [])
    ref_comp_species: list[tuple[str, str]] = []
    for species in identity.reference_species or []:
        if not selected or species in selected:
            ref_comp_species.append((species, "reference"))
    include_comparative = "comparative" in str(identity.scope_mode or "")
    for species in identity.comparative_species or []:
        if include_comparative and (not selected or species in selected):
            ref_comp_species.append((species, "comparative"))

    for species, role in ref_comp_species:
        key = f"reference_family_pep:{species}"
        req = RequirementItem(
            key=key,
            kind="protein_fasta",
            role=role,
            required=True,
            produced_by=[],
            input_only=True,
            description="Known-family protein FASTA for selected reference/comparative species.",
            species=species,
        )
        report.requirements.append(req)
        artifact = _resolve_known_family_pep(context, identity, species)
        if artifact:
            report.resolved[key] = artifact
        else:
            _mark_missing(report, req, f"Missing known-family protein FASTA for {species}; provide {species}.{identity.family}.pep.fa/.fasta.")


def _resolve_pep_requirements(report: StageRequirementReport, context: ProjectContextIndex) -> None:
    identity = report.identity
    req = RequirementItem(
        key="protein_fasta",
        kind="protein_fasta",
        role="pep_input",
        produced_by=["identify"] if identity.scope_mode in {"", "target_only"} else ["phylogeny"],
        description="Protein FASTA used by PEP analysis.",
    )
    report.requirements.append(req)

    if identity.scope_mode and identity.scope_mode != "target_only":
        artifact = _resolve_scoped_phylogeny_input(context, identity)
        if artifact:
            report.resolved[req.key] = artifact
        else:
            _mark_missing(
                report,
                req,
                "Scoped PEP requires same-scope phylogeny manifest parameters.scope_input_fasta.",
            )
        return

    species = (identity.target_species or context.target_species or _species_from_prefix(identity.prefix) or [""])[0]
    artifact = _resolve_family_members_pep(context, identity, species)
    if artifact:
        report.resolved[req.key] = artifact
    else:
        _mark_missing(report, req, "PEP target_only requires Identify family protein FASTA.")



def _resolve_target_proteome(context: ProjectContextIndex, identity: StageIdentity, species: str) -> ResolvedArtifact | None:
    input_dir = Path(context.input_dir)
    final_dir = Path(context.final_dir)
    family = identity.family or context.family
    candidates: list[Path] = []
    if species:
        roots = [input_dir, input_dir / species, final_dir]
        for root in roots:
            candidates.extend([
                root / f"{species}.pep.fa",
                root / f"{species}.pep.fasta",
                root / f"{species}.protein.fa",
                root / f"{species}.protein.fasta",
                root / f"{species}.faa",
            ])
            if family:
                candidates.extend([
                    root / f"{species}.{family}.proteome.fa",
                    root / f"{species}.{family}.protein.fa",
                    root / f"{species}.{family}.pep.fa",
                ])
    for candidate in candidates:
        if _usable(candidate):
            return _artifact("target_proteome", candidate, "canonical:target_proteome", "input", identity.scope_id, species, "target_proteome", "protein_fasta")
    for file in context.files:
        if file.kind != "protein_fasta" or not file.exists:
            continue
        name = Path(file.path).name.lower()
        file_species = str(file.species or "")
        if file_species and file_species.lower() != species.lower():
            continue
        if species.lower() in name and any(token in name for token in ("pep", "protein", "proteome", "faa")):
            if "final_members" in name or ".msa." in name:
                continue
            return _artifact("target_proteome", Path(file.path), f"context:{file.source}", "input", identity.scope_id, species, "target_proteome", "protein_fasta")
    return None

def _resolve_family_members_pep(context: ProjectContextIndex, identity: StageIdentity, species: str = "") -> ResolvedArtifact | None:
    family = identity.family or context.family
    candidates: list[Path] = []
    final_dir = Path(context.final_dir)
    workspace = Path(context.workspace)
    input_dir = Path(context.input_dir)
    if species and family:
        candidates.extend([
            final_dir / f"{species}.{family}.final_members.pep.fa",
            input_dir / f"{species}.{family}.final_members.pep.fa",
            final_dir / f"Target.{species}.{family}.final_members.pep.fa",
            input_dir / f"Target.{species}.{family}.final_members.pep.fa",
            workspace / "identify" / species / "final" / f"{species}.final_gfmembers.pep.fa",
            final_dir / f"{species}.final_gfmembers.pep.fa",
            input_dir / f"{species}.final_gfmembers.pep.fa",
        ])
    if identity.prefix:
        candidates.extend([
            final_dir / f"{identity.prefix}.final_members.pep.fa",
            input_dir / f"{identity.prefix}.final_members.pep.fa",
        ])
    for candidate in candidates:
        if _usable(candidate):
            return _artifact("family_members_pep", candidate, "canonical:identify_family_pep", "identify", identity.scope_id, species, "target", "protein_fasta")

    need = ArtifactNeed("family_members_pep", "protein_fasta", alternatives=["identify_manifest", "final_members_pep"])
    decision = ArtifactResolver(context).resolve_need(identity.stage, need, species=species, family=family)
    if decision.resolved and decision.selected:
        return _from_candidate("family_members_pep", decision.selected, "identify", identity.scope_id)
    return None


def _resolve_known_family_pep(context: ProjectContextIndex, identity: StageIdentity, species: str) -> ResolvedArtifact | None:
    family = identity.family or context.family
    input_dir = Path(context.input_dir)
    final_dir = Path(context.final_dir)
    candidates = []
    if species and family:
        for root in (input_dir, final_dir, input_dir / species):
            candidates.extend([
                root / f"{species}.{family}.pep.fa",
                root / f"{species}.{family}.pep.fasta",
                root / f"{species}.{family}.family_pep.fa",
                root / f"{species}.{family}.family_pep.fasta",
                root / f"{species}.{family}.sequence_extract.pep.fa",
                root / f"{species}.{family}.sequence_extract.pep.fasta",
                root / f"{species}.{family}.ncbi_fetch.protein.fa",
            ])
    for candidate in candidates:
        if _usable(candidate):
            return _artifact("reference_family_pep", candidate, "canonical:known_family_pep", "input", identity.scope_id, species, "reference", "protein_fasta")

    for file in context.files:
        if file.kind != "protein_fasta" or not file.exists:
            continue
        name = Path(file.path).name.lower()
        file_species = str(file.species or "")
        file_family = str(file.family or "")
        if file_species and file_species != species:
            continue
        if file_family and family and file_family != family:
            continue
        if any(token in name for token in (".msa.", ".tree.", "phylogeny", "alignment")):
            continue
        if species.lower() in name and family.lower() in name and any(token in name for token in ("pep", "protein", "family")):
            return _artifact("reference_family_pep", Path(file.path), f"context:{file.source}", "input", identity.scope_id, species, "reference", "protein_fasta")
    return None


def _resolve_scoped_phylogeny_input(context: ProjectContextIndex, identity: StageIdentity) -> ResolvedArtifact | None:
    expected_scope = identity.scope_id
    for manifest in context.manifests:
        if str(manifest.get("stage") or "") != "phylogeny":
            continue
        if str(manifest.get("status") or "") not in {"success", "partial_success"}:
            continue
        manifest_scope = str(manifest.get("scope_id") or "")
        if expected_scope and manifest_scope != expected_scope:
            continue
        params = manifest.get("parameters") if isinstance(manifest.get("parameters"), dict) else {}
        fasta_text = str(params.get("scope_input_fasta") or "")
        if not fasta_text:
            continue
        fasta = Path(fasta_text)
        if not fasta.is_absolute():
            fasta = Path(context.workspace) / fasta
        if _usable(fasta):
            return _artifact("protein_fasta", fasta, "manifest:phylogeny:scope_input_fasta", "phylogeny", manifest_scope, "", "pep_input", "protein_fasta")
    return None



def _resolve_reference_classification_requirements(report: StageRequirementReport, context: ProjectContextIndex) -> None:
    identity = report.identity
    _resolve_required_artifact(
        report,
        context,
        key="tree",
        role="tree",
        kind="tree",
        produced_by=["phylogeny"],
        description="Same-scope phylogeny tree for reference-based subfamily transfer.",
    )
    ref_species = _first(identity.reference_species or context.reference_species or [""])
    _resolve_required_artifact(
        report,
        context,
        key=f"reference_subfamily:{ref_species}" if ref_species else "reference_subfamily",
        role="reference_subfamily",
        kind="table",
        produced_by=[],
        input_only=True,
        description="Reference species subfamily label table, for example At.SPL.subfamily.",
        species=ref_species,
    )


def _resolve_motif_requirements(report: StageRequirementReport, context: ProjectContextIndex) -> None:
    identity = report.identity
    for species in _target_species_list(identity, context):
        _resolve_required_artifact(
            report,
            context,
            key=f"family_members_pep:{species}" if species else "family_members_pep",
            role="family_members_pep",
            kind="protein_fasta",
            produced_by=["identify"],
            description="Family member protein FASTA for motif analysis.",
            species=species,
        )
    _resolve_required_artifact(report, context, "tree", "tree", "tree", ["phylogeny"], "Phylogeny tree used to order motif plot.")
    _resolve_optional_artifact(report, context, "gene_subfamily", "gene_subfamily", "table", ["reference_classification", "subfamily_confirm"], "Optional subfamily labels for motif grouping.")


def _resolve_domain_requirements(report: StageRequirementReport, context: ProjectContextIndex) -> None:
    identity = report.identity
    for species in _target_species_list(identity, context):
        _resolve_required_artifact(
            report,
            context,
            key=f"family_members_pep:{species}" if species else "family_members_pep",
            role="family_members_pep",
            kind="protein_fasta",
            produced_by=["identify"],
            description="Family member protein FASTA for domain analysis.",
            species=species,
        )
    _resolve_required_artifact(report, context, "tree", "tree", "tree", ["phylogeny"], "Phylogeny tree used to order domain plot.")
    _resolve_required_artifact(report, context, "pfam_database", "pfam_database", "hmm_database", [], "Pfam HMM database for hmmscan.", input_only=True)
    _resolve_optional_artifact(report, context, "gene_subfamily", "gene_subfamily", "table", ["reference_classification", "subfamily_confirm"], "Optional subfamily labels for domain grouping.")


def _resolve_structure_requirements(report: StageRequirementReport, context: ProjectContextIndex) -> None:
    identity = report.identity
    for species in _target_species_list(identity, context):
        _resolve_required_artifact(
            report,
            context,
            key=f"family_members_id:{species}" if species else "family_members_id",
            role="family_members_id",
            kind="id_list",
            produced_by=["identify"],
            description="Family member gene ID list for structure analysis.",
            species=species,
        )
        _resolve_required_artifact(
            report,
            context,
            key=f"target_gff:{species}" if species else "target_gff",
            role="target_gff",
            kind="gff",
            produced_by=["genome_prepare_v2"],
            description="Target species annotation GFF/GFF3 for structure analysis.",
            species=species,
        )
    _resolve_required_artifact(report, context, "tree", "tree", "tree", ["phylogeny"], "Phylogeny tree used to order structure plot.")
    _resolve_optional_artifact(report, context, "gene_subfamily", "gene_subfamily", "table", ["reference_classification", "subfamily_confirm"], "Optional subfamily labels for structure grouping.")


def _resolve_combo_requirements(report: StageRequirementReport, context: ProjectContextIndex) -> None:
    _resolve_required_artifact(report, context, "tree", "tree", "tree", ["phylogeny"], "Phylogeny tree used to order combo plot.")
    panel_keys = []
    for key, role in [("motif_result", "motif_result"), ("domain_result", "domain_result"), ("structure_result", "structure_result")]:
        artifact = _resolve_optional_artifact(report, context, key, role, "table", [role.split("_", 1)[0]], f"Optional {role} table for combo plot.")
        if artifact:
            panel_keys.append(key)
    _resolve_optional_artifact(report, context, "gene_subfamily", "gene_subfamily", "table", ["reference_classification", "subfamily_confirm"], "Optional subfamily labels for combo grouping.")
    if not panel_keys:
        report.blocked_reasons.append("annotation_panel_missing")
        for upstream in ("motif", "domain", "structure"):
            if upstream not in report.upstream_needed:
                report.upstream_needed.append(upstream)
        report.messages.append("Combo requires at least one available motif/domain/structure result table.")


def _resolve_subfamily_confirm_requirements(report: StageRequirementReport, context: ProjectContextIndex) -> None:
    _resolve_required_artifact(
        report,
        context,
        key="predicted_subfamily",
        role="gene_subfamily",
        kind="table",
        produced_by=["reference_classification"],
        description="Predicted subfamily table to freeze after user review.",
    )


def _target_species_list(identity: StageIdentity, context: ProjectContextIndex) -> list[str]:
    targets = identity.target_species or context.target_species or _species_from_prefix(identity.prefix)
    if not targets:
        return [""]
    return list(dict.fromkeys(str(item).strip() for item in targets if str(item).strip())) or [""]


def _first(values: Iterable[Any]) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _resolve_required_artifact(
    report: StageRequirementReport,
    context: ProjectContextIndex,
    key: str,
    role: str,
    kind: str,
    produced_by: list[str],
    description: str,
    species: str = "",
    input_only: bool = False,
) -> ResolvedArtifact | None:
    req = RequirementItem(
        key=key,
        kind=kind,
        role=role,
        required=True,
        produced_by=produced_by,
        input_only=input_only,
        description=description,
        species=species,
    )
    report.requirements.append(req)
    artifact = _resolve_semantic_artifact(context, report.identity, key, role, kind, species=species)
    if artifact:
        report.resolved[key] = artifact
        return artifact
    message = f"Missing {role} for {report.stage}."
    if species:
        message = f"Missing {role} for {species}; {report.stage} requires it."
    _mark_missing(report, req, message)
    return None


def _resolve_optional_artifact(
    report: StageRequirementReport,
    context: ProjectContextIndex,
    key: str,
    role: str,
    kind: str,
    produced_by: list[str],
    description: str,
    species: str = "",
) -> ResolvedArtifact | None:
    req = RequirementItem(
        key=key,
        kind=kind,
        role=role,
        required=False,
        produced_by=produced_by,
        description=description,
        species=species,
    )
    report.requirements.append(req)
    artifact = _resolve_semantic_artifact(context, report.identity, key, role, kind, species=species)
    if artifact:
        report.resolved[key] = artifact
    return artifact


def _resolve_semantic_artifact(context: ProjectContextIndex, identity: StageIdentity, key: str, role: str, kind: str, species: str = "") -> ResolvedArtifact | None:
    species = species or _first(identity.target_species or context.target_species or [])
    family = identity.family or context.family
    need = ArtifactNeed(role, kind)
    decision = ArtifactResolver(context).resolve_need(identity.stage, need, species=species, family=family)
    if decision.resolved and decision.selected:
        return _from_candidate(key, decision.selected, _producer_for_candidate(role, decision.selected), identity.scope_id)
    return None


def _producer_for_candidate(role: str, candidate: ResolveCandidate) -> str:
    source = str(candidate.source or "")
    parts = source.split(":")
    if len(parts) >= 2 and parts[0] in {"artifact", "manifest"} and parts[1]:
        return parts[1]
    name = Path(candidate.path).name.lower()
    if role == "gene_subfamily":
        if "confirmed_subfamily" in name:
            return "subfamily_confirm"
        if "reference_classification" in name or "geneid.subfamily" in name:
            return "reference_classification"
    return _producer_for_role(role)


def _producer_for_role(role: str) -> str:
    return {
        "family_members_pep": "identify",
        "family_members_id": "identify",
        "tree": "phylogeny",
        "gene_subfamily": "reference_classification",
        "reference_subfamily": "input",
        "target_gff": "genome_prepare_v2",
        "pfam_database": "input",
        "motif_result": "motif",
        "domain_result": "domain",
        "structure_result": "structure",
    }.get(role, "")

def _mark_missing(report: StageRequirementReport, req: RequirementItem, message: str) -> None:
    if req not in report.requirements:
        report.requirements.append(req)
    if req.required and req not in report.missing:
        report.missing.append(req)
    if req.input_only:
        report.blocked_reasons.append(req.key)
    for upstream in req.produced_by:
        if upstream and upstream not in report.upstream_needed:
            report.upstream_needed.append(upstream)
    report.messages.append(message)


def _finalize_report(report: StageRequirementReport) -> None:
    report.blocked_reasons = list(dict.fromkeys(report.blocked_reasons))
    report.upstream_needed = list(dict.fromkeys(report.upstream_needed))
    report.ready = not report.missing and not report.blocked_reasons
    if report.ready:
        report.messages.append(f"{report.stage} requirements ready.")


def _artifact(key: str, path: Path, source: str, producer_stage: str, scope_id: str, species: str, role: str, kind: str) -> ResolvedArtifact:
    return ResolvedArtifact(
        key=key,
        path=str(path),
        source=source,
        producer_stage=producer_stage,
        scope_id=scope_id,
        species=species,
        role=role,
        kind=kind,
    )


def _from_candidate(key: str, candidate: ResolveCandidate, producer_stage: str, scope_id: str) -> ResolvedArtifact:
    return ResolvedArtifact(
        key=key,
        path=candidate.path,
        source=candidate.source,
        producer_stage=producer_stage,
        scope_id=scope_id,
        species=candidate.species,
        role=candidate.role,
        kind=candidate.kind,
    )


def _usable(path: Path) -> bool:
    try:
        return path.exists() and path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _species_from_prefix(prefix: str) -> list[str]:
    if not prefix or "." not in prefix:
        return []
    species = prefix.split(".", 1)[0]
    if species in {"Multi", "Target", "Ref", "Reference", "Comparative"}:
        return []
    return [species]


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Resolve bbav5 stage requirements")
    parser.add_argument("stage")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--prefix", default="")
    parser.add_argument("--params-json", default="{}")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    args = parser.parse_args()
    params = json.loads(args.params_json or "{}")
    report = resolve_stage_requirements(
        stage=args.stage,
        prefix=args.prefix,
        job_id=args.job_id,
        params=params,
        project_root=args.project_root,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
