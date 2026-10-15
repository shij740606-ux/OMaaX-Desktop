"""Deterministic identities for the WGS/VCF 01-12 chain."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence


class WgsIdentityError(ValueError):
    """Raised when an identity lacks authoritative input evidence."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAMPLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_FASTQ_SUFFIXES = (".fastq", ".fastq.gz", ".fq", ".fq.gz")


def _sha256(value: object, *, label: str) -> str:
    normalized = str(value or "").lower()
    if not _SHA256.fullmatch(normalized):
        raise WgsIdentityError(f"{label} must be a lowercase SHA256")
    return normalized


def _identity(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return f"{prefix}:{hashlib.sha256(encoded).hexdigest()}"


def sha256_file(path: str | Path) -> str:
    """Return the SHA256 of one authoritative input file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sample_fastq_path(value: str, *, field: str) -> Path:
    path = Path(value)
    if not path.name.endswith(_FASTQ_SUFFIXES):
        raise WgsIdentityError(f"{field} has an unsupported FASTQ suffix")
    if not path.is_file():
        raise WgsIdentityError(f"{field} must be an existing regular file")
    if path.stat().st_size == 0:
        raise WgsIdentityError(f"{field} must be non-empty")
    return path


def build_wgs_sample_set_id(samples: Sequence[Mapping[str, str]]) -> str:
    """Build an identity from normalized sample IDs and FASTQ content hashes."""
    if not samples:
        raise WgsIdentityError("samples must be non-empty")
    canonical: list[dict[str, str]] = []
    for record in samples:
        if not isinstance(record, Mapping):
            raise WgsIdentityError("samples must be mappings")
        sample_id = record.get("sample_id")
        r1, r2 = record.get("r1"), record.get("r2")
        if not isinstance(sample_id, str) or not _SAMPLE_ID.fullmatch(sample_id):
            raise WgsIdentityError("sample ID is unsafe")
        if not isinstance(r1, str) or not r1 or not isinstance(r2, str) or not r2:
            raise WgsIdentityError("sample records require sample_id, r1, and r2")
        r1_path = _sample_fastq_path(r1, field="r1")
        r2_path = _sample_fastq_path(r2, field="r2")
        if r1_path.resolve() == r2_path.resolve() or r1_path.samefile(r2_path):
            raise WgsIdentityError("sample r1 and r2 must be different")
        canonical.append({"sample_id": sample_id, "r1_sha256": sha256_file(r1_path), "r2_sha256": sha256_file(r2_path)})
    canonical.sort(key=lambda record: record["sample_id"])
    if len({record["sample_id"] for record in canonical}) != len(canonical):
        raise WgsIdentityError("sample IDs must be unique")
    return _identity("wgs-samples", {"samples": canonical})


def build_wgs_reference_id(reference_sha256: str) -> str:
    """Build an identity from the authoritative reference FASTA bytes."""
    return _identity("wgs-reference", {"reference_sha256": _sha256(reference_sha256, label="reference")})


def build_wgs_analysis_id(
    stage: str,
    upstream_sha256s: Iterable[str],
    parameters: Mapping[str, Any],
    tool_versions: Mapping[str, str],
    method: str = "",
) -> str:
    """Build an analysis identity from explicit upstream data and method evidence."""
    upstream = sorted({_sha256(value, label="upstream") for value in upstream_sha256s})
    if not isinstance(tool_versions, Mapping) or not tool_versions or any(
        not isinstance(name, str) or not name.strip() or not isinstance(version, str) or not version.strip()
        for name, version in tool_versions.items()
    ):
        raise WgsIdentityError("tool versions require non-empty tool names and versions")
    if not stage or not upstream or not isinstance(parameters, Mapping) or not method:
        raise WgsIdentityError("analysis identity fields are incomplete")
    return _identity(
        "wgs-analysis",
        {
            "stage": stage,
            "upstream_sha256s": upstream,
            "parameters": dict(parameters),
            "tool_versions": dict(tool_versions),
            "method": method,
        },
    )
