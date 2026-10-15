"""Shared context helpers for gene-family stage execution.

The existing CLI scripts remain the execution entry points.  This module
centralizes the analysis identity that must be shared by planning, execution,
status checks, manifests and result grouping.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from gfa_core.gene_family_scope import (
    build_scope_id,
    build_scope_summary,
    sanitize_token,
    scope_hash,
    selected_species_for_mode,
)


SUBFAMILY_PREDICTION_STAGES = {
    "reference_classification",
}
TREE_STRUCTURE_STAGES = {
    "phylogeny",
    "motif",
    "domain",
    "structure",
    "combo",
}
GENE_FAMILY_SCOPE_STAGES = {
    "identify",
    "phylogeny",
    "reference_classification",
    "subfamily_confirm",
    "motif",
    "domain",
    "structure",
    "combo",
    "promoter",
    "synteny",
    "interspecies_synteny",
    "karyotype",
    "pep",
    "ortholog",
    "ppi",
    "kaks",
    "expression",
}
CUSTOM_PHYLOGENY_ROUTES = {"custom_phylogeny", "comparative_phylogeny"}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass
class StageContext:
    stage: str
    prefix: str
    output_prefix: str
    legacy_output_prefix: str = ""
    scope_summary: str = ""
    scope_hash: str = ""
    family: str = ""
    analysis_route: str = "unscoped"
    result_group: str = "gene_family"
    scope_id: str = ""
    scope_mode: str = ""
    member_set_id: str = "all_members"
    subfamily_source: str = "none"
    action: str = "compute"
    target_species: list[str] = field(default_factory=list)
    reference_species: list[str] = field(default_factory=list)
    comparative_species: list[str] = field(default_factory=list)
    selected_species: list[str] = field(default_factory=list)
    scope_file: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def infer_analysis_route(stage: str, scope_data: dict[str, Any] | None = None) -> str:
    scope_data = scope_data or {}
    scope_mode = str(scope_data.get("scope_mode") or "").strip()
    targets = [str(x) for x in scope_data.get("target_species") or [] if str(x).strip()]
    refs = [str(x) for x in scope_data.get("reference_species") or [] if str(x).strip()]
    comps = [str(x) for x in scope_data.get("comparative_species") or [] if str(x).strip()]

    if stage == "subfamily_confirm":
        return "confirmed_subfamily"
    if stage == "identify":
        if not targets:
            return "unscoped"
        return "single_species" if len(targets) == 1 else "multi_target"
    if stage in SUBFAMILY_PREDICTION_STAGES:
        return "subfamily_prediction"
    if not scope_data:
        return "unscoped"
    if scope_mode == "target_only" and len(targets) == 1:
        return "single_species"
    if scope_mode == "target_only" and len(targets) > 1:
        return "multi_target"
    if refs and scope_mode in {"target_plus_reference", "target_plus_reference_plus_comparative"}:
        return "multi_species"
    if comps or len(targets) > 1:
        return "multi_species"
    return scope_mode or "custom"


def _scope_data_from_scope_id(scope_id: str) -> dict[str, Any]:
    parts = [part for part in str(scope_id or "").split(".") if part]
    data: dict[str, Any] = {"scope_id": str(scope_id or "").strip()}
    if not parts:
        return data
    data["family"] = parts[0]
    for part in parts[1:]:
        if part.startswith("target-"):
            data["target_species"] = [item for item in part[len("target-") :].split("-") if item]
        elif part.startswith("ref-"):
            data["reference_species"] = [item for item in part[len("ref-") :].split("-") if item]
        elif part.startswith("comp-"):
            data["comparative_species"] = [item for item in part[len("comp-") :].split("-") if item]
        else:
            data["scope_mode"] = part
    data.setdefault("target_species", [])
    data.setdefault("reference_species", [])
    data.setdefault("comparative_species", [])
    data["selected_species"] = selected_species_for_mode(
        data.get("target_species") or [],
        data.get("reference_species") or [],
        data.get("comparative_species") or [],
        str(data.get("scope_mode") or ""),
    )
    return data


def scope_data_from_params(prefix: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    params = params or {}

    def _as_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw = value.replace("，", ",").replace(";", ",").split(",")
        elif isinstance(value, (list, tuple, set)):
            raw = value
        else:
            raw = [value]
        return [str(x).strip() for x in raw if str(x).strip()]

    targets = _as_list(params.get("targetSpecies") or params.get("target_species"))
    refs = _as_list(params.get("referenceSpecies") or params.get("reference_species") or params.get("refSpeciesList"))
    comps = _as_list(params.get("comparativeSpecies") or params.get("comparative_species"))
    family = str(params.get("geneFamily") or params.get("gene_family") or (prefix.split(".")[-1] if "." in prefix else "")).strip()
    scope_mode = str(params.get("scopeMode") or params.get("scope_mode") or "").strip()
    if not scope_mode:
        if targets and refs and comps:
            scope_mode = "target_plus_reference_plus_comparative"
        elif targets and refs:
            scope_mode = "target_plus_reference"
        elif targets:
            scope_mode = "target_only"
    explicit_scope_id = str(params.get("scopeId") or params.get("scope_id") or "").strip()
    if explicit_scope_id:
        parsed_scope = _scope_data_from_scope_id(explicit_scope_id)
        parsed_family = str(parsed_scope.get("family") or "").strip()
        return {
            "family": parsed_family or family,
            "scope_mode": str(parsed_scope.get("scope_mode") or "").strip(),
            "scope_id": explicit_scope_id,
            "target_species": list(parsed_scope.get("target_species") or []),
            "reference_species": list(parsed_scope.get("reference_species") or []),
            "comparative_species": list(parsed_scope.get("comparative_species") or []),
            "selected_species": list(parsed_scope.get("selected_species") or []),
        }

    selected_species = selected_species_for_mode(targets, refs, comps, scope_mode)
    scope_id = build_scope_id(family, targets, refs, comps, scope_mode) if family and scope_mode else ""
    return {
        "family": family,
        "scope_mode": scope_mode,
        "scope_id": scope_id,
        "target_species": targets,
        "reference_species": refs,
        "comparative_species": comps,
        "selected_species": selected_species,
    }


def enrich_stage_params_with_context(stage: str, prefix: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return planning/execution params carrying explicit gene-family context."""
    merged = dict(params or {})
    if stage not in GENE_FAMILY_SCOPE_STAGES:
        return merged
    scope_data = scope_data_from_params(prefix, merged)
    for key, camel in (
        ("family", "geneFamily"),
        ("scope_mode", "scopeMode"),
        ("target_species", "targetSpecies"),
        ("reference_species", "referenceSpecies"),
        ("comparative_species", "comparativeSpecies"),
    ):
        value = scope_data.get(key)
        if key == "family":
            if value not in (None, "", []):
                merged.setdefault(camel, value)
        elif value is not None:
            merged[camel] = value
    route = str(merged.get("analysisRoute") or merged.get("analysis_route") or "").strip()
    if stage == "subfamily_confirm":
        source_route = str(merged.get("sourcePredictionRoute") or merged.get("source_prediction_route") or "").strip()
        if not source_route and route and route != "confirmed_subfamily":
            source_route = route
        route = "confirmed_subfamily"
        if source_route:
            merged["sourcePredictionRoute"] = source_route
            merged["source_prediction_route"] = source_route
    if not route:
        route = infer_analysis_route(stage, scope_data)
    if route and route != "unscoped":
        merged["analysisRoute"] = route
        merged["analysis_route"] = route
    scope_id = str(scope_data.get("scope_id") or "").strip()
    if scope_id:
        merged["scopeId"] = scope_id
        merged["scope_id"] = scope_id
    existing_member_set = str(merged.get("memberSetId") or merged.get("member_set_id") or "")
    has_scope_id = bool(scope_id)
    requires_confirmed = _truthy(merged.get("requiresConfirmedSubfamily") or merged.get("requires_confirmed_subfamily"))
    if (
        requires_confirmed
        and stage not in {"subfamily_confirm", "reference_classification"}
        and route not in {"subfamily_evidence", "subfamily_prediction", "confirmed_subfamily"}
    ):
        expected_member_set = confirmed_subfamily_member_set_id(scope_data)
    else:
        expected_member_set = member_set_id_for(prefix, scope_data, route) if has_scope_id and route and route != "unscoped" else ""
    if expected_member_set and (
        stage == "subfamily_confirm"
        or not existing_member_set
        or existing_member_set.startswith("NA:")
        or sanitize_token(scope_id) not in existing_member_set
        or f":{route}:" not in existing_member_set
        or (requires_confirmed and existing_member_set != expected_member_set)
    ):
        merged["memberSetId"] = expected_member_set
        merged["member_set_id"] = expected_member_set
    elif not has_scope_id:
        merged.pop("memberSetId", None)
        merged.pop("member_set_id", None)
    context = build_stage_context(
        stage=stage,
        prefix=prefix,
        scope_data=scope_data,
        scope_file=merged.get("scopeFile") or merged.get("scope_file") or "",
        analysis_route=route,
        member_set_id=merged.get("memberSetId") or merged.get("member_set_id") or "",
    ).to_dict()
    merged["stageContext"] = context
    merged["stage_context"] = context
    return merged


def result_group_for(stage: str, analysis_route: str) -> str:
    if stage == "reference_classification" or analysis_route == "subfamily_prediction":
        return "subfamily_confirmation"
    if stage == "phylogeny":
        return f"phylogeny_{analysis_route}"
    if stage in {"motif", "domain", "structure", "combo"}:
        return f"{stage}_{analysis_route}"
    return f"gene_family_{stage}"


def member_set_id_for(prefix: str, scope_data: dict[str, Any] | None, analysis_route: str) -> str:
    scope_data = scope_data or {}
    scope_id = str(scope_data.get("scope_id") or "").strip()
    if scope_id:
        if analysis_route == "subfamily_evidence":
            return f"{sanitize_token(scope_id)}:{analysis_route}:domain_refined"
        return f"{sanitize_token(scope_id)}:{analysis_route}:all_members"
    return f"{sanitize_token(prefix)}:{analysis_route}:all_members"


def confirmed_subfamily_member_set_id(scope_data: dict[str, Any] | None) -> str:
    scope_data = scope_data or {}
    scope_id = str(scope_data.get("scope_id") or "").strip()
    return f"{sanitize_token(scope_id)}:confirmed_subfamily:all_members" if scope_id else ""


def output_prefix_for(prefix: str, scope_data: dict[str, Any] | None = None) -> str:
    """Legacy scoped output prefix kept for compatibility with old artifacts."""
    scope_id = str((scope_data or {}).get("scope_id") or "").strip()
    if scope_id:
        return f"{prefix}.{sanitize_token(scope_id)}"
    return prefix


def species_prefix_for(species: str, family: str) -> str:
    """Canonical user-facing prefix for one species in one gene family."""
    clean_species = sanitize_token(str(species or "").strip())
    clean_family = sanitize_token(str(family or "").strip())
    if clean_species and clean_family:
        return f"{clean_species}.{clean_family}"
    return clean_species or clean_family


def _role_part(role: str, values: list[str], max_items: int = 3) -> str:
    clean = [sanitize_token(str(x)) for x in values if str(x).strip()]
    if not clean:
        return ""
    head = clean[:max_items]
    suffix = f"-plus{len(clean) - max_items}" if len(clean) > max_items else ""
    return f"{role}-" + "-".join(head) + suffix


def _family_from_prefix(prefix: str, scope_data: dict[str, Any]) -> str:
    family = str(scope_data.get("family") or "").strip()
    if family:
        return sanitize_token(family)
    if "." in prefix:
        return sanitize_token(prefix.split(".")[-1])
    return sanitize_token(prefix or "family")


def _target_family_base(family: str, targets: list[str]) -> str:
    clean_targets = [sanitize_token(str(x)) for x in targets if str(x).strip()]
    if len(clean_targets) == 1:
        return species_prefix_for(clean_targets[0], family)
    return f"Target.{family}" if family else "Target"


def user_output_prefix_for(
    stage: str,
    prefix: str,
    scope_data: dict[str, Any] | None = None,
    analysis_route: str = "",
    member_set_id: str = "",
) -> str:
    """Return the short, user-facing output prefix for new artifacts.

    Full machine identity remains in scope_id/analysis_route/member_set_id in
    manifests.  This function deliberately avoids embedding the full scope_id in
    every filename, while keeping enough role information to distinguish target,
    reference and comparative analyses.
    """
    scope_data = scope_data or {}
    scope_id = str(scope_data.get("scope_id") or "").strip()
    if not scope_id:
        return prefix

    route = analysis_route or infer_analysis_route(stage, scope_data)
    family = _family_from_prefix(prefix, scope_data)
    mode = str(scope_data.get("scope_mode") or "").strip()
    targets = [str(x) for x in scope_data.get("target_species") or [] if str(x).strip()]
    refs = [str(x) for x in scope_data.get("reference_species") or [] if str(x).strip()]
    comps = [str(x) for x in scope_data.get("comparative_species") or [] if str(x).strip()]

    target_part = _role_part("target", targets)
    ref_part = _role_part("ref", refs)
    comp_part = _role_part("comp", comps)

    if stage == "identify":
        if len(targets) == 1:
            return species_prefix_for(targets[0], family)
        if len(targets) > 1:
            return ".".join(part for part in [f"Target.{family}", target_part] if part)
        return prefix

    if route in {"subfamily_prediction", "subfamily_evidence"} or stage == "reference_classification":
        label = "subfamily_evidence" if route == "subfamily_evidence" else "subfamily_prediction"
        parts = [f"Target.{family}", target_part, ref_part, comp_part, label]
        return ".".join(part for part in parts if part)

    if route == "confirmed_subfamily":
        base = _target_family_base(family, targets)
        if len(targets) > 1 and target_part:
            base = f"{base}.{target_part}"
        return f"{base}.confirmed_subfamily"

    if route == "subset_subfamily" or str(member_set_id).startswith("subfamily-") or ":subfamily-" in str(member_set_id):
        subset = "subset"
        for token in str(member_set_id).replace(":", ".").split("."):
            if token.startswith("subfamily-"):
                subset = sanitize_token(token)
                break
        return f"{_target_family_base(family, targets)}.{subset}"

    if mode == "target_only" and len(targets) == 1:
        return _target_family_base(family, targets)

    if mode == "target_only" and len(targets) > 1:
        return ".".join(part for part in [f"Target.{family}", target_part] if part)

    if route in CUSTOM_PHYLOGENY_ROUTES:
        parts = [f"Target.{family}", target_part, ref_part, comp_part, sanitize_token(route)]
        return ".".join(part for part in parts if part)

    parts = [f"Target.{family}", target_part, ref_part, comp_part]
    return ".".join(part for part in parts if part)


def legacy_manifest_path_for(final_dir: str | Path, prefix: str, scope_id: str, stage: str) -> Path | None:
    scope_id = str(scope_id or "").strip()
    if not scope_id:
        return None
    return Path(final_dir) / f"{prefix}.{sanitize_token(scope_id)}.{stage}.manifest.json"


def write_legacy_manifest_copy(manifest_path: str | Path, prefix: str, scope_id: str, stage: str) -> Path | None:
    """Write a compatibility manifest using the old scoped filename.

    New stage manifests use the short user-facing output prefix.  Some older
    status/result paths still probe ``prefix.scope_id.stage.manifest.json``;
    this copy keeps those paths readable while marking the file as an alias so
    callers can redirect to the canonical manifest when possible.
    """
    legacy_path = legacy_manifest_path_for(Path(manifest_path).parent, prefix, scope_id, stage)
    if legacy_path is None:
        return None
    manifest_path = Path(manifest_path)
    if legacy_path.absolute() == manifest_path.absolute():
        return legacy_path

    try:
        import json

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["compatibility_alias"] = True
        manifest["alias_type"] = "legacy_scoped_manifest"
        manifest["canonical_manifest"] = f"final/{manifest_path.name}"
        manifest["canonical_manifest_path"] = str(manifest_path.absolute())
        manifest.setdefault("parameters", {})["compatibility_alias"] = True
        legacy_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception:
        legacy_path.write_text(manifest_path.read_text(encoding="utf-8"), encoding="utf-8")
    return legacy_path


def short_output_prefix_for(prefix: str, scope_data: dict[str, Any] | None = None) -> str:
    scope_data = scope_data or {}
    scope_id = str(scope_data.get("scope_id") or "").strip()
    if not scope_id:
        return prefix
    summary = build_scope_summary(
        str(scope_data.get("family") or (prefix.split(".")[-1] if "." in prefix else "")),
        [str(x) for x in scope_data.get("target_species") or []],
        [str(x) for x in scope_data.get("reference_species") or []],
        [str(x) for x in scope_data.get("comparative_species") or []],
        str(scope_data.get("scope_mode") or ""),
    )
    return f"{prefix}.{sanitize_token(summary)}.h{scope_hash(scope_id)}"


def build_stage_context(
    stage: str,
    prefix: str,
    scope_data: dict[str, Any] | None = None,
    scope_file: str | Path | None = None,
    output_prefix: str | None = None,
    subfamily_file: str | Path | None = None,
    action: str = "compute",
    analysis_route: str = "",
    member_set_id: str = "",
) -> StageContext:
    scope_data = scope_data or {}
    route = analysis_route or infer_analysis_route(stage, scope_data)
    legacy_out_prefix = output_prefix_for(prefix, scope_data)
    out_prefix = output_prefix or user_output_prefix_for(stage, prefix, scope_data, route, member_set_id)
    scope_id = str(scope_data.get("scope_id") or "")
    summary = ""
    shash = ""
    if scope_id:
        summary = build_scope_summary(
            str(scope_data.get("family") or (prefix.split(".")[-1] if "." in prefix else "")),
            [str(x) for x in scope_data.get("target_species") or []],
            [str(x) for x in scope_data.get("reference_species") or []],
            [str(x) for x in scope_data.get("comparative_species") or []],
            str(scope_data.get("scope_mode") or ""),
        )
        shash = scope_hash(scope_id)
    subfamily_source = "none"
    subfamily_token = str(subfamily_file or "").strip()
    if subfamily_token and subfamily_token.lower() not in {"none", "skip", "disabled", "na", "n/a"}:
        name = Path(subfamily_token).name
        if "confirmed_subfamily" in name:
            subfamily_source = "confirmed"
        elif "reference_classification" in name or "geneID.subfamily" in name:
            subfamily_source = "predicted"
        else:
            subfamily_source = "user_uploaded"

    return StageContext(
        stage=stage,
        prefix=prefix,
        output_prefix=out_prefix,
        legacy_output_prefix=legacy_out_prefix,
        scope_summary=summary,
        scope_hash=shash,
        family=str(scope_data.get("family") or (prefix.split(".")[-1] if "." in prefix else "")),
        analysis_route=route,
        result_group=result_group_for(stage, route),
        scope_id=scope_id,
        scope_mode=str(scope_data.get("scope_mode") or ""),
        member_set_id=member_set_id or member_set_id_for(prefix, scope_data, route),
        subfamily_source=subfamily_source,
        action=action,
        target_species=[str(x) for x in scope_data.get("target_species") or []],
        reference_species=[str(x) for x in scope_data.get("reference_species") or []],
        comparative_species=[str(x) for x in scope_data.get("comparative_species") or []],
        selected_species=[str(x) for x in scope_data.get("selected_species") or []],
        scope_file=str(scope_file or ""),
    )


def apply_stage_context(manifest: dict[str, Any], context: StageContext) -> dict[str, Any]:
    data = context.to_dict()
    for key in (
        "analysis_route",
        "result_group",
        "member_set_id",
        "subfamily_source",
        "action",
        "output_prefix",
        "legacy_output_prefix",
        "scope_summary",
        "scope_hash",
        "scope_id",
        "scope_mode",
        "target_species",
        "reference_species",
        "comparative_species",
        "selected_species",
        "scope_file",
    ):
        value = data.get(key)
        if value not in (None, "", []):
            manifest[key] = value
    manifest["stage_context"] = data
    parameters = manifest.setdefault("parameters", {})
    for key in (
        "analysis_route",
        "result_group",
        "member_set_id",
        "subfamily_source",
        "action",
        "output_prefix",
        "legacy_output_prefix",
        "scope_summary",
        "scope_hash",
    ):
        parameters.setdefault(key, data.get(key))
    return manifest
