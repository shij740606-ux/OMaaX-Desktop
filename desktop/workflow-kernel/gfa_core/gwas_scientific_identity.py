"""Deterministic scientific identities for the PlantAI GWAS chain."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


class GwasIdentityError(ValueError):
    """Raised when an identity cannot be proven from authoritative evidence."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MODEL_STAGES = {"gwas_mlm", "gwas_emmax", "gwas_glm"}
_PLOT_STAGES = {"gwas_plot", "gwas_emplot"}


def _digest(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return f"{prefix}:{hashlib.sha256(encoded).hexdigest()}"


def _valid_sha(value: str, label: str) -> str:
    normalized = str(value or "").lower()
    if not _SHA256.fullmatch(normalized):
        raise GwasIdentityError(f"{label} must be a lowercase SHA256")
    return normalized


def build_phenotype_scope_id(
    *,
    phenotype_sha256: str,
    sample_column: str,
    trait_column: str,
    trait_name: str,
    retained_sample_ids: Iterable[str],
) -> str:
    sample_ids = [str(value) for value in retained_sample_ids]
    if not sample_ids or any(not value for value in sample_ids) or len(sample_ids) != len(set(sample_ids)):
        raise GwasIdentityError("retained phenotype sample IDs must be nonempty and unique")
    sample_digest = hashlib.sha256(("\n".join(sample_ids) + "\n").encode()).hexdigest()
    return _digest(
        "phenotype",
        {
            "phenotype_sha256": _valid_sha(phenotype_sha256, "phenotype sha256"),
            "sample_column": str(sample_column),
            "trait_column": str(trait_column),
            "trait_name": str(trait_name),
            "missing_policy": "reject",
            "retained_sample_digest": sample_digest,
        },
    )


def build_covariate_scope_id(
    *,
    q_sha256: str,
    fam_sha256: str,
    source_genotype_scope_id: str,
    k: int,
) -> str:
    source = str(source_genotype_scope_id or "")
    if not re.fullmatch(r"genotype:[0-9a-f]{64}", source):
        raise GwasIdentityError("verified source genotype scope is required")
    if int(k) < 2:
        raise GwasIdentityError("covariate K must be at least two")
    return _digest(
        "covariate",
        {
            "q_sha256": _valid_sha(q_sha256, "Q sha256"),
            "fam_sha256": _valid_sha(fam_sha256, "FAM sha256"),
            "source_genotype_scope_id": source,
            "k": int(k),
            "fam_id_column": "IID",
            "omitted_covariate": f"Q{int(k)}",
        },
    )


def build_identity_recipe(
    *,
    stage: str,
    required_artifact_roles: Iterable[str],
    parameters: Mapping[str, Any],
    method_version: str,
    contract_version: str,
    tool_versions: Mapping[str, str],
) -> dict[str, Any]:
    roles = sorted({str(role) for role in required_artifact_roles if str(role)})
    if not stage or not roles or not method_version or not contract_version:
        raise GwasIdentityError("identity recipe fields are incomplete")
    return {
        "schema_version": "gwas_identity_recipe.v1",
        "stage": str(stage),
        "required_artifact_roles": roles,
        "parameters": dict(parameters),
        "method_version": str(method_version),
        "contract_version": str(contract_version),
        "tool_versions": dict(tool_versions),
    }


def materialize_stage_identity(
    recipe: Mapping[str, Any],
    upstream_artifacts: Mapping[str, Mapping[str, Any]],
    data_scopes: Mapping[str, str],
) -> dict[str, str]:
    if recipe.get("schema_version") != "gwas_identity_recipe.v1":
        raise GwasIdentityError("unsupported GWAS identity recipe")
    roles = list(recipe.get("required_artifact_roles") or [])
    artifact_hashes: dict[str, str] = {}
    for role in roles:
        artifact = upstream_artifacts.get(role)
        if not isinstance(artifact, Mapping):
            raise GwasIdentityError(f"missing authoritative artifact: {role}")
        try:
            artifact_hashes[role] = _valid_sha(str(artifact.get("sha256") or ""), f"{role} sha256")
        except GwasIdentityError as exc:
            raise GwasIdentityError(f"missing authoritative artifact hash: {role}") from exc

    stage = str(recipe.get("stage") or "")
    scopes = {str(key): str(value) for key, value in data_scopes.items() if value}
    base = {
        "stage": stage,
        "artifact_sha256": artifact_hashes,
        "data_scopes": scopes,
        "parameters": dict(recipe.get("parameters") or {}),
        "method_version": str(recipe.get("method_version") or ""),
        "contract_version": str(recipe.get("contract_version") or ""),
        "tool_versions": dict(recipe.get("tool_versions") or {}),
    }
    identity = dict(scopes)
    if stage in _MODEL_STAGES:
        identity["model_scope_id"] = _digest("gwas-model", base)
    elif stage in _PLOT_STAGES:
        source = scopes.get("source_model_scope_id", "")
        if not re.fullmatch(r"gwas-model:[0-9a-f]{64}", source):
            raise GwasIdentityError("plot stage requires source model scope")
        identity["source_model_scope_id"] = source
    identity["analysis_scope_id"] = _digest("gwas-analysis", base)
    return identity


def new_run_id(stage: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stage}_{timestamp}_{uuid.uuid4().hex[:8]}"
