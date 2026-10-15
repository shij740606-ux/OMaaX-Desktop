"""Fail-closed resolution of authoritative GWAS upstream artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


GWAS_RESULT_STAGES = {
    "gwas_kinship", "gwas_trait", "gwas_cov", "gwas_mlm", "gwas_plot",
    "gwas_emmax", "gwas_emplot", "gwas_glm",
}

GWAS_STAGE_SCOPE_FIELDS = {
    "gwas_trait": ("phenotype_scope_id", "analysis_scope_id"),
    "gwas_cov": ("genotype_scope_id", "covariate_scope_id", "analysis_scope_id"),
    "gwas_mlm": (
        "genotype_scope_id", "phenotype_scope_id", "covariate_scope_id",
        "model_scope_id", "analysis_scope_id",
    ),
    "gwas_plot": (
        "genotype_scope_id", "phenotype_scope_id", "covariate_scope_id",
        "source_model_scope_id", "analysis_scope_id",
    ),
    "gwas_emmax": (
        "genotype_scope_id", "phenotype_scope_id", "model_scope_id",
        "analysis_scope_id",
    ),
    "gwas_emplot": (
        "genotype_scope_id", "phenotype_scope_id", "source_model_scope_id",
        "analysis_scope_id",
    ),
    "gwas_glm": (
        "genotype_scope_id", "phenotype_scope_id", "covariate_scope_id",
        "model_scope_id", "analysis_scope_id",
    ),
}


class GwasUpstreamError(ValueError):
    """Raised when an upstream Manifest cannot prove one exact artifact."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_upstream_artifact(
    *,
    workspace: str | Path,
    prefix: str,
    producer_stage: str,
    artifact_role: str,
    expected_scopes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    workspace_path = Path(workspace).resolve()
    final_dir = workspace_path / "final"
    candidates = sorted(final_dir.glob(f"{prefix}.{producer_stage}*.manifest.json"))
    if not candidates:
        raise GwasUpstreamError(f"{producer_stage} manifest is missing")

    matches: list[tuple[Path, dict[str, Any]]] = []
    scope_mismatches: list[str] = []
    for manifest_path in candidates:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GwasUpstreamError(f"{producer_stage} manifest is unreadable") from exc
        if manifest.get("stage") != producer_stage or manifest.get("prefix") != prefix:
            continue
        mismatched_scope = next((
            key for key, expected in (expected_scopes or {}).items()
            if expected and str(manifest.get(key) or "") != str(expected)
        ), "")
        if mismatched_scope:
            scope_mismatches.append(mismatched_scope)
            continue
        if manifest.get("status") != "success":
            raise GwasUpstreamError(f"{producer_stage} manifest is not successful")
        matches.append((manifest_path, manifest))
    if not matches:
        if scope_mismatches:
            raise GwasUpstreamError(
                f"{producer_stage} manifest {scope_mismatches[0]} scope mismatch"
            )
        raise GwasUpstreamError(f"{producer_stage} manifest is missing")
    if len(matches) != 1:
        raise GwasUpstreamError(f"multiple {producer_stage} manifests require identity selection")

    manifest_path, manifest = matches[0]
    artifacts = manifest.get("artifacts")
    artifact = artifacts.get(artifact_role) if isinstance(artifacts, dict) else None
    if not isinstance(artifact, dict) or artifact.get("role") != artifact_role:
        raise GwasUpstreamError(f"{producer_stage} manifest is missing artifact role {artifact_role}")
    raw_path = Path(str(artifact.get("path") or ""))
    if raw_path.is_absolute():
        raise GwasUpstreamError(f"{artifact_role} manifest path must be workspace-relative")
    try:
        resolved = (workspace_path / raw_path).resolve(strict=True)
        resolved.relative_to(workspace_path)
    except (OSError, ValueError) as exc:
        raise GwasUpstreamError(f"{artifact_role} path is invalid or escapes workspace") from exc
    if not resolved.is_file():
        raise GwasUpstreamError(f"{artifact_role} file is missing")
    full_path = str(artifact.get("full_path") or "")
    if full_path and Path(full_path).resolve() != resolved:
        raise GwasUpstreamError(f"{artifact_role} full_path mismatch")
    declared_sha = str(artifact.get("sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", declared_sha):
        raise GwasUpstreamError(f"{artifact_role} sha256 is invalid")
    if _sha256(resolved) != declared_sha:
        raise GwasUpstreamError(f"{artifact_role} sha256 mismatch")
    declared_size = artifact.get("size")
    if isinstance(declared_size, bool) or not isinstance(declared_size, int) or declared_size != resolved.stat().st_size:
        raise GwasUpstreamError(f"{artifact_role} size mismatch")

    return {
        "resolved": True,
        "path": str(resolved),
        "sha256": declared_sha,
        "source_manifest": str(manifest_path),
        "producer_stage": producer_stage,
        "artifact_role": artifact_role,
        **{
            key: str(manifest.get(key) or "")
            for key in (
                "genotype_scope_id", "phenotype_scope_id", "covariate_scope_id",
                "model_scope_id", "source_model_scope_id", "analysis_scope_id",
            )
            if manifest.get(key)
        },
    }


def resolve_gwas_plot_upstream(
    *, workspace: str | Path, prefix: str, genotype_scope_id: str,
    phenotype_scope_id: str, covariate_scope_id: str, source_model_scope_id: str,
) -> dict[str, Any]:
    """Resolve both plot inputs and their denominator from one exact MLM Manifest."""
    expected = {
        "genotype_scope_id": genotype_scope_id,
        "phenotype_scope_id": phenotype_scope_id,
        "covariate_scope_id": covariate_scope_id,
        "model_scope_id": source_model_scope_id,
    }
    decisions = {
        role: resolve_upstream_artifact(
            workspace=workspace, prefix=prefix, producer_stage="gwas_mlm",
            artifact_role=role, expected_scopes=expected,
        )
        for role in ("mlm_pvalues", "cmlm_pvalues")
    }
    manifests = {item["source_manifest"] for item in decisions.values()}
    if len(manifests) != 1:
        raise GwasUpstreamError("gwas_plot inputs must come from one gwas_mlm manifest")
    manifest_path = Path(next(iter(manifests)))
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GwasUpstreamError("gwas_mlm manifest is unreadable") from exc
    metrics = manifest.get("metrics")
    count = metrics.get("candidate_marker_count") if isinstance(metrics, dict) else None
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise GwasUpstreamError("gwas_mlm candidate_marker_count is missing or invalid")
    return {**decisions, "candidate_marker_count": count, "source_manifest": str(manifest_path)}


def resolve_gwas_emmax_upstream(
    *, workspace: str | Path, prefix: str, genotype_scope_id: str,
    phenotype_scope_id: str,
) -> dict[str, Any]:
    """Resolve and validate the exact two producer Manifests for Stage 24."""
    decisions = {
        "sorted_vcf": resolve_upstream_artifact(
            workspace=workspace, prefix=prefix, producer_stage="gwas_fmt",
            artifact_role="sorted_vcf",
            expected_scopes={"genotype_scope_id": genotype_scope_id},
        ),
        "trait_table": resolve_upstream_artifact(
            workspace=workspace, prefix=prefix, producer_stage="gwas_trait",
            artifact_role="trait_table",
            expected_scopes={"phenotype_scope_id": phenotype_scope_id},
        ),
    }
    for role, require_analysis_scope in (("sorted_vcf", False), ("trait_table", True)):
        decision = decisions[role]
        manifest_path = Path(str(decision["source_manifest"]))
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GwasUpstreamError(f"{decision['producer_stage']} manifest is unreadable") from exc
        producer_stage = str(decision["producer_stage"])
        if manifest.get("manifest_version") != "1.0":
            raise GwasUpstreamError(f"{producer_stage} manifest version must be 1.0")
        run_id = str(manifest.get("run_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", run_id):
            raise GwasUpstreamError(f"{producer_stage} manifest run_id is empty or unsafe")
        analysis_scope = str(manifest.get("analysis_scope_id") or "")
        if (
            (require_analysis_scope and not analysis_scope)
            or (analysis_scope and not re.fullmatch(r"gwas-analysis:[0-9a-f]{64}", analysis_scope))
        ):
            raise GwasUpstreamError(f"{producer_stage} manifest analysis_scope_id is invalid")
        artifact = (manifest.get("artifacts") or {}).get(role)
        if not isinstance(artifact, dict) or artifact.get("artifact") != role:
            raise GwasUpstreamError(f"{producer_stage} manifest artifact identity is invalid")
    return decisions


def resolve_gwas_emplot_upstream(
    *, workspace: str | Path, prefix: str, genotype_scope_id: str,
    phenotype_scope_id: str, source_model_scope_id: str,
) -> dict[str, Any]:
    """Resolve the exact successful Stage 24 p-value artifact and denominator."""
    decision = resolve_upstream_artifact(
        workspace=workspace, prefix=prefix, producer_stage="gwas_emmax",
        artifact_role="emmax_pvalues", expected_scopes={
            "genotype_scope_id": genotype_scope_id,
            "phenotype_scope_id": phenotype_scope_id,
            "model_scope_id": source_model_scope_id,
        },
    )
    manifest_path = Path(str(decision["source_manifest"]))
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GwasUpstreamError("gwas_emmax manifest is unreadable") from exc
    if manifest.get("manifest_version") != "1.0":
        raise GwasUpstreamError("gwas_emmax manifest version must be 1.0")
    run_id = str(manifest.get("run_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", run_id):
        raise GwasUpstreamError("gwas_emmax manifest run_id is empty or unsafe")
    artifact = (manifest.get("artifacts") or {}).get("emmax_pvalues")
    if not isinstance(artifact, dict) or artifact.get("artifact") != "emmax_pvalues":
        raise GwasUpstreamError("gwas_emmax manifest artifact identity is invalid")
    metrics = manifest.get("metrics")
    count = metrics.get("emmax_tested_count") if isinstance(metrics, dict) else None
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise GwasUpstreamError("gwas_emmax emmax_tested_count is missing or invalid")
    return {
        "emmax_pvalues": decision,
        "emmax_tested_count": count,
        "source_manifest": str(manifest_path),
    }


def resolve_gwas_glm_upstream(
    *, workspace: str | Path, prefix: str, genotype_scope_id: str,
    phenotype_scope_id: str, covariate_scope_id: str,
) -> dict[str, Any]:
    """Resolve the three exact producer artifacts required by Stage 26."""
    requests = {
        "sorted_vcf": ("gwas_fmt", {"genotype_scope_id": genotype_scope_id}, False),
        "trait_table": ("gwas_trait", {"phenotype_scope_id": phenotype_scope_id}, True),
        "covariate_table": ("gwas_cov", {
            "genotype_scope_id": genotype_scope_id,
            "covariate_scope_id": covariate_scope_id,
        }, True),
    }
    decisions: dict[str, Any] = {}
    for role, (producer, scopes, require_analysis_scope) in requests.items():
        decision = resolve_upstream_artifact(
            workspace=workspace, prefix=prefix, producer_stage=producer,
            artifact_role=role, expected_scopes=scopes,
        )
        try:
            manifest = json.loads(Path(decision["source_manifest"]).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GwasUpstreamError(f"{producer} manifest is unreadable") from exc
        if manifest.get("manifest_version") != "1.0":
            raise GwasUpstreamError(f"{producer} manifest version must be 1.0")
        run_id = str(manifest.get("run_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", run_id):
            raise GwasUpstreamError(f"{producer} manifest run_id is empty or unsafe")
        analysis_scope = str(manifest.get("analysis_scope_id") or "")
        if (
            (require_analysis_scope and not analysis_scope)
            or (analysis_scope and not re.fullmatch(r"gwas-analysis:[0-9a-f]{64}", analysis_scope))
        ):
            raise GwasUpstreamError(f"{producer} manifest analysis_scope_id is invalid")
        artifact = (manifest.get("artifacts") or {}).get(role)
        if not isinstance(artifact, dict) or artifact.get("artifact") != role:
            raise GwasUpstreamError(f"{producer} manifest artifact identity is invalid")
        decisions[role] = decision
    return decisions


def validated_result_artifacts(
    manifest: Mapping[str, Any],
    *,
    workspace: str | Path,
    expected_scopes: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Return manifest-authorized artifacts only after path/size/hash validation."""
    workspace_path = Path(workspace).resolve()
    if (
        manifest.get("manifest_version") != "1.0"
        or manifest.get("status") != "success"
        or manifest.get("stage") not in GWAS_RESULT_STAGES
    ):
        return []
    for key, expected in (expected_scopes or {}).items():
        if expected and str(manifest.get(key) or "") != str(expected):
            return []
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        return []
    records: list[dict[str, Any]] = []
    for role, raw_record in artifacts.items():
        if not isinstance(raw_record, dict) or str(raw_record.get("role") or "") != str(role):
            return []
        raw_path = Path(str(raw_record.get("path") or ""))
        if raw_path.is_absolute() or not raw_path.parts or raw_path.parts[0] != "final":
            return []
        try:
            resolved = (workspace_path / raw_path).resolve(strict=True)
            resolved.relative_to(workspace_path / "final")
        except (OSError, ValueError):
            return []
        if not resolved.is_file():
            return []
        if str(raw_record.get("full_path") or "") and Path(str(raw_record["full_path"])).resolve() != resolved:
            return []
        declared_size = raw_record.get("size")
        declared_sha = str(raw_record.get("sha256") or "").lower()
        if (
            isinstance(declared_size, bool)
            or not isinstance(declared_size, int)
            or declared_size != resolved.stat().st_size
            or not re.fullmatch(r"[0-9a-f]{64}", declared_sha)
            or _sha256(resolved) != declared_sha
        ):
            return []
        records.append(dict(raw_record))
    return records
