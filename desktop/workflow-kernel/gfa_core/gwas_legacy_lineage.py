"""Validation for audited legacy population-structure lineage evidence."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable


class LegacyLineageError(ValueError):
    """Raised when legacy lineage cannot prove its declared inputs."""


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checked_file(record: Any, role: str, allowed_roots: tuple[Path, ...]) -> Path:
    if not isinstance(record, dict):
        raise LegacyLineageError(f"{role} record is missing")
    path = Path(str(record.get("path") or ""))
    if not path.is_absolute():
        raise LegacyLineageError(f"{role} path must be absolute")
    resolved = path.resolve()
    if allowed_roots and not any(resolved == root or root in resolved.parents for root in allowed_roots):
        raise LegacyLineageError(f"{role} path is outside allowed roots")
    if not resolved.is_file():
        raise LegacyLineageError(f"{role} file is missing")
    declared = str(record.get("sha256") or "").lower()
    if not _SHA256.fullmatch(declared):
        raise LegacyLineageError(f"{role} sha256 is invalid")
    if _sha256(resolved) != declared:
        raise LegacyLineageError(f"{role} sha256 mismatch")
    return resolved


def _vcf_shape(path: Path) -> tuple[list[str], int]:
    opener = gzip.open if path.suffix == ".gz" else open
    samples: list[str] | None = None
    variants = 0
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#CHROM\t"):
                samples = line.rstrip("\n").split("\t")[9:]
            elif line and not line.startswith("#"):
                variants += 1
    if samples is None:
        raise LegacyLineageError("parent_genotype VCF header is missing")
    if len(samples) != len(set(samples)):
        raise LegacyLineageError("parent_genotype sample IDs are not unique")
    return samples, variants


def _fam_iids(path: Path) -> list[str]:
    ids: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        fields = line.split()
        if len(fields) < 2:
            raise LegacyLineageError(f"sample_fam line {number} has fewer than two columns")
        ids.append(fields[1])
    if not ids or len(ids) != len(set(ids)):
        raise LegacyLineageError("sample_fam IID values must be nonempty and unique")
    return ids


def _validate_q(path: Path, expected_k: int) -> int:
    rows = 0
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            values = [float(value) for value in line.split()]
        except ValueError as exc:
            raise LegacyLineageError(f"admixture_q line {number} is not numeric") from exc
        if len(values) != expected_k:
            raise LegacyLineageError(f"admixture_q line {number} does not have K columns")
        if not all(math.isfinite(value) for value in values):
            raise LegacyLineageError(f"admixture_q line {number} is not finite")
        if abs(sum(values) - 1.0) > 1e-4:
            raise LegacyLineageError(f"admixture_q line {number} does not sum to one")
        rows += 1
    return rows


def validate_legacy_lineage(
    manifest_path: str | Path,
    expected_genotype_sha256: str | None = None,
    allowed_roots: Iterable[str | Path] = (),
) -> dict[str, Any]:
    """Verify an immutable Stage 15 evidence record and its current files."""

    path = Path(manifest_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LegacyLineageError(f"legacy lineage manifest is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise LegacyLineageError("legacy lineage manifest must be an object")
    if data.get("schema_version") != "legacy_lineage.v1":
        raise LegacyLineageError("unsupported legacy lineage schema_version")
    if data.get("stage") != "pop_struct" or data.get("status") != "success":
        raise LegacyLineageError("legacy lineage must be a successful pop_struct audit")

    roots = tuple(Path(root).resolve() for root in allowed_roots)
    parent = data.get("parent_genotype")
    parent_path = _checked_file(parent, "parent_genotype", roots)
    source_sha = str(data.get("source_genotype_sha256") or "").lower()
    if source_sha != parent.get("sha256"):
        raise LegacyLineageError("source genotype and parent_genotype sha256 differ")
    if expected_genotype_sha256 and source_sha != expected_genotype_sha256.lower():
        raise LegacyLineageError("legacy lineage does not match expected genotype")

    artifacts = data.get("artifacts")
    if not isinstance(artifacts, dict):
        raise LegacyLineageError("legacy lineage artifacts are missing")
    required_roles = {"sample_fam", "admixture_q", "source_script", "execution_log"}
    missing = required_roles - artifacts.keys()
    if missing:
        raise LegacyLineageError(f"legacy lineage artifacts missing roles: {', '.join(sorted(missing))}")
    resolved = {role: _checked_file(artifacts[role], role, roots) for role in required_roles}

    method = data.get("method") if isinstance(data.get("method"), dict) else {}
    pruning = method.get("ld_pruning") if isinstance(method.get("ld_pruning"), dict) else {}
    required_pruning = {"window_variants": 50, "step_variants": 10, "r2": 0.2}
    if any(pruning.get(key) != value for key, value in required_pruning.items()):
        raise LegacyLineageError("legacy lineage LD-pruning parameters are incomplete")
    if not str(method.get("plink_version") or "") or not str(method.get("admixture_version") or ""):
        raise LegacyLineageError("legacy lineage tool versions are incomplete")
    try:
        k = int(method.get("admixture_k"))
    except (TypeError, ValueError) as exc:
        raise LegacyLineageError("legacy lineage admixture_k is invalid") from exc
    if k < 2:
        raise LegacyLineageError("legacy lineage admixture_k must be at least two")

    parent_ids, variant_count = _vcf_shape(parent_path)
    fam_ids = _fam_iids(resolved["sample_fam"])
    q_rows = _validate_q(resolved["admixture_q"], k)
    if q_rows != len(fam_ids):
        raise LegacyLineageError("Q/FAM row count mismatch")
    if parent_ids != fam_ids:
        raise LegacyLineageError("parent genotype and FAM IID order mismatch")

    identity = data.get("sample_identity") if isinstance(data.get("sample_identity"), dict) else {}
    digest = hashlib.sha256(("\n".join(fam_ids) + "\n").encode()).hexdigest()
    if identity.get("fam_id_column") != "IID" or identity.get("ordered_iid_sha256") != digest:
        raise LegacyLineageError("sample identity digest mismatch")
    if int(identity.get("count") or -1) != len(fam_ids):
        raise LegacyLineageError("sample identity count mismatch")
    if int(parent.get("sample_count") or -1) != len(parent_ids):
        raise LegacyLineageError("parent genotype sample_count mismatch")
    if int(parent.get("variant_count") or -1) != variant_count:
        raise LegacyLineageError("parent genotype variant_count mismatch")
    return data
