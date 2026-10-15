"""Fail-closed discovery of one authoritative population-analysis Manifest."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


POPULATION_STAGES = {
    "pop_pca",
    "pop_phylogeny",
    "pop_structure",
    "pop_ld_decay",
    "pop_snp_density",
}
_GENOTYPE_SCOPE = re.compile(r"genotype:[0-9a-f]{64}")
_ANALYSIS_SCOPE = re.compile(r"population-analysis:[0-9a-f]{64}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class PopulationManifestError(ValueError):
    """Raised when a population Manifest cannot prove one exact identity."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _input_hash(manifest: dict[str, Any], role: str) -> str:
    for item in manifest.get("inputs") or []:
        if isinstance(item, dict) and item.get("role") == role:
            return str(item.get("sha256") or "").lower()
    return ""


def _full_path_matches_relative_artifact(full_path: str, raw_path: Path) -> bool:
    if not full_path:
        return True
    full_parts = Path(full_path).parts
    relative_parts = raw_path.parts
    return (
        bool(relative_parts)
        and len(full_parts) >= len(relative_parts)
        and full_parts[-len(relative_parts):] == relative_parts
    )


def _validate_artifacts(workspace: Path, manifest: dict[str, Any]) -> None:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise PopulationManifestError("population manifest has no artifacts")
    for role, record in artifacts.items():
        if not isinstance(record, dict):
            raise PopulationManifestError(f"population artifact {role} is invalid")
        raw_path = Path(str(record.get("path") or ""))
        if not raw_path or raw_path.is_absolute():
            raise PopulationManifestError(f"population artifact {role} path is invalid")
        try:
            path = (workspace / raw_path).resolve(strict=True)
            path.relative_to(workspace)
        except (OSError, ValueError) as exc:
            raise PopulationManifestError(
                f"population artifact {role} path is invalid or escapes workspace"
            ) from exc
        if not path.is_file():
            raise PopulationManifestError(f"population artifact {role} is missing")
        full_path = str(record.get("full_path") or "")
        if not _full_path_matches_relative_artifact(full_path, raw_path):
            raise PopulationManifestError(
                f"population artifact {role} full_path does not identify the declared relative artifact"
            )
        declared_size = record.get("size")
        if (
            isinstance(declared_size, bool)
            or not isinstance(declared_size, int)
            or declared_size != path.stat().st_size
        ):
            raise PopulationManifestError(f"population artifact {role} size mismatch")
        declared_sha = str(record.get("sha256") or "").lower()
        if not _SHA256.fullmatch(declared_sha):
            raise PopulationManifestError(f"population artifact {role} sha256 is invalid")
        if _sha256(path) != declared_sha:
            raise PopulationManifestError(f"population artifact {role} sha256 mismatch")


def resolve_population_manifest_identity(
    *,
    workspace: str | Path,
    stage: str,
    prefix: str,
    genotype_scope_id: str = "",
    analysis_scope_id: str = "",
    expected_vcf_sha256: str = "",
    expected_group_sha256: str = "",
) -> dict[str, str]:
    """Return one exact population identity, or an empty dict when none exists."""
    workspace_path = Path(workspace).resolve()
    stage = str(stage or "").strip()
    prefix = str(prefix or "").strip()
    genotype_scope_id = str(genotype_scope_id or "").strip()
    analysis_scope_id = str(analysis_scope_id or "").strip()
    if stage not in POPULATION_STAGES:
        raise PopulationManifestError(f"unsupported population stage: {stage}")
    if bool(genotype_scope_id) != bool(analysis_scope_id):
        raise PopulationManifestError(
            "population genotype and analysis scopes must be both empty or both complete"
        )
    if genotype_scope_id and analysis_scope_id:
        return {
            "genotypeScopeId": genotype_scope_id,
            "analysisScopeId": analysis_scope_id,
        }

    final_dir = workspace_path / "final"
    if not final_dir.is_dir():
        return {}
    relevant: list[tuple[Path, dict[str, Any]]] = []
    pattern = (
        f"{prefix}.{stage}*.manifest.json"
        if prefix
        else f"*.{stage}*.manifest.json"
    )
    for manifest_path in sorted(final_dir.glob(pattern)):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PopulationManifestError("population manifest is unreadable") from exc
        if manifest.get("stage") != stage:
            continue
        if prefix and manifest.get("prefix") != prefix:
            continue
        genotype = str(manifest.get("genotype_scope_id") or "")
        analysis = str(manifest.get("analysis_scope_id") or "")
        if not _GENOTYPE_SCOPE.fullmatch(genotype) or not _ANALYSIS_SCOPE.fullmatch(analysis):
            raise PopulationManifestError("population manifest scope identity is invalid")
        if expected_vcf_sha256:
            input_sha = _input_hash(manifest, "population_vcf")
            if input_sha != str(expected_vcf_sha256).lower():
                continue
            if genotype != f"genotype:{str(expected_vcf_sha256).lower()}":
                raise PopulationManifestError("population manifest genotype lineage mismatch")
        if expected_group_sha256:
            group_sha = _input_hash(manifest, "sample_group")
            if group_sha != str(expected_group_sha256).lower():
                continue
        relevant.append((manifest_path, manifest))
    if not relevant:
        return {}

    latest_by_identity: dict[
        tuple[str, str, str],
        tuple[tuple[str, float, str], Path, dict[str, Any]],
    ] = {}
    for manifest_path, manifest in relevant:
        key = (
            str(manifest["prefix"]),
            str(manifest["genotype_scope_id"]),
            str(manifest["analysis_scope_id"]),
        )
        try:
            mtime = manifest_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        stamp = (str(manifest.get("start_time") or ""), mtime, manifest_path.name)
        if stamp > latest_by_identity.get(key, (("", -1.0, ""), manifest_path, manifest))[0]:
            latest_by_identity[key] = (stamp, manifest_path, manifest)
    if len(latest_by_identity) != 1:
        raise PopulationManifestError("multiple population manifests require identity selection")

    _stamp, manifest_path, manifest = next(iter(latest_by_identity.values()))
    if manifest.get("status") != "success":
        raise PopulationManifestError("latest population manifest is not successful")
    _validate_artifacts(workspace_path, manifest)
    return {
        "genotypeScopeId": str(manifest["genotype_scope_id"]),
        "analysisScopeId": str(manifest["analysis_scope_id"]),
        "runId": str(manifest.get("run_id") or ""),
        "sourceManifest": str(manifest_path),
    }
