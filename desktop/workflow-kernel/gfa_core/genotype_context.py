"""Stable identity helpers for externally supplied genotype datasets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def genotype_scope_id_from_sha256(digest: str) -> str:
    normalized = str(digest or "").strip().lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError("A complete SHA256 digest is required for genotype identity")
    return f"genotype:{normalized}"


def genotype_scope_id_for_vcf(path: str | Path) -> str:
    return genotype_scope_id_from_sha256(sha256_file(path))


def build_population_analysis_scope_id(
    *,
    stage: str,
    genotype_scope_id: str,
    input_artifact_hashes: dict[str, str],
    parameters: dict[str, Any],
    method_version: str,
    contract_version: str,
    tool_versions: dict[str, str],
) -> str:
    """Build the scientific identity for one population-genomics analysis."""
    stage = str(stage or "").strip()
    if not stage.startswith("pop_"):
        raise ValueError("Population analysis stage must start with pop_")
    genotype_scope_id = str(genotype_scope_id or "").strip()
    if not genotype_scope_id.startswith("genotype:"):
        raise ValueError("A genotype_scope_id is required for population analysis")
    genotype_scope_id_from_sha256(genotype_scope_id.removeprefix("genotype:"))
    normalized_hashes: dict[str, str] = {}
    for role, digest in sorted((input_artifact_hashes or {}).items()):
        normalized = str(digest or "").strip().lower()
        genotype_scope_id_from_sha256(normalized)
        normalized_hashes[str(role)] = normalized
    if "population_vcf" not in normalized_hashes:
        raise ValueError("population_vcf SHA256 is required for population analysis")
    payload = {
        "stage": stage,
        "genotype_scope_id": genotype_scope_id,
        "input_artifact_hashes": normalized_hashes,
        "parameters": parameters or {},
        "method_version": str(method_version or "").strip(),
        "contract_version": str(contract_version or "").strip(),
        "tool_versions": tool_versions or {},
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "population-analysis:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
