"""Phase-aware gene-family member-set resolution.

This module centralizes the filename policy around identified, curated, and
confirmed member sets.  It deliberately returns provenance with the selected
paths so downstream stages can record what they actually consumed.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal


Phase = Literal[
    "pre_confirm",
    "subfamily_evidence_required",
    "post_confirm_required",
    "auto",
    "legacy_compatible",
]
Require = Literal["id", "pep", "pair"]

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


class MemberResolutionError(RuntimeError):
    """Raised when a requested effective member set cannot be resolved."""


@dataclass(frozen=True)
class MemberResolution:
    path_id: Path | None
    path_pep: Path | None
    source_stage: str
    source_kind: str
    phase: str
    member_set_id: str
    member_count: int
    id_checksum: str = ""
    pep_checksum: str = ""
    warnings: list[str] = field(default_factory=list)


def resolve_effective_members(
    *,
    workspace: Path,
    final_dir: Path,
    input_dir: Path,
    prefix: str,
    phase: Phase = "auto",
    require: Require = "pair",
    species: str = "",
    family: str = "",
    member_set_id: str = "",
) -> MemberResolution:
    """Resolve the effective member set for a workflow phase.

    `post_confirm_required` is intentionally strict: it never falls back to
    identify or curated files, because doing so can silently reroute downstream
    stages from the confirmed member set back to the large identify set.
    """

    workspace = Path(workspace).resolve()
    final_dir = Path(final_dir).resolve()
    input_dir = Path(input_dir).resolve()
    prefix = _require_token(prefix, "prefix")
    species = _optional_token(species, "species")
    family = _optional_token(family or _family_from_prefix(prefix), "family")
    member_set_id = _optional_token(member_set_id, "member_set_id")
    roots = [workspace, final_dir, input_dir, workspace / "identify"]

    if phase == "auto":
        phase = "post_confirm_required" if _confirmed_manifest(final_dir, prefix) else "pre_confirm"

    if phase == "post_confirm_required":
        result = _resolve_stem(
            final_dir=final_dir,
            input_dir=input_dir,
            workspace=workspace,
            roots=roots,
            prefixes=_prefix_candidates(prefix, species, family),
            stem="final_members",
            require=require,
            phase=phase,
            source_kind="confirmed_final_members",
            source_stage="subfamily_confirm",
            member_set_id=member_set_id,
            require_confirmed=True,
        )
        if result:
            return result
        raise MemberResolutionError(f"Missing confirmed final members for {prefix}; rerun subfamily_confirm.")

    if phase == "subfamily_evidence_required":
        result = _resolve_stem(
            final_dir=final_dir,
            input_dir=input_dir,
            workspace=workspace,
            roots=roots,
            prefixes=_prefix_candidates(prefix, species, family),
            stem="curated_members",
            require=require,
            phase=phase,
            source_kind="curated_members",
            source_stage="identify_refine",
            member_set_id=member_set_id,
            require_confirmed=False,
        )
        if result:
            return result
        raise MemberResolutionError(
            f"Missing curated members for {prefix}; complete identify member review before subfamily evidence analysis."
        )

    if phase == "pre_confirm":
        policy = [
            ("curated_members", "curated_members", "identify_refine"),
            ("identified_members", "identified_members", "identify"),
            ("final_members", "legacy_identify_final_members", "identify"),
            ("final_gfmembers", "legacy_internal_final_gfmembers", "identify"),
        ]
    elif phase == "legacy_compatible":
        policy = [
            ("final_members", "confirmed_final_members", "subfamily_confirm"),
            ("curated_members", "curated_members", "identify_refine"),
            ("identified_members", "identified_members", "identify"),
            ("final_members", "legacy_identify_final_members", "identify"),
            ("final_gfmembers", "legacy_internal_final_gfmembers", "identify"),
        ]
    else:
        raise MemberResolutionError(f"Unsupported member-set phase: {phase}")

    for stem, source_kind, source_stage in policy:
        if source_kind == "confirmed_final_members" and not _confirmed_manifest(final_dir, prefix):
            continue
        result = _resolve_stem(
            final_dir=final_dir,
            input_dir=input_dir,
            workspace=workspace,
            roots=roots,
            prefixes=_prefix_candidates(prefix, species, family),
            stem=stem,
            require=require,
            phase=phase,
            source_kind=source_kind,
            source_stage=source_stage,
            member_set_id=member_set_id,
            require_confirmed=False,
        )
        if result:
            return result

    raise MemberResolutionError(f"No effective member set found for {prefix} in phase {phase}.")


def candidate_member_prefixes(final_dir: Path, input_dir: Path, preferred_prefix: str = "") -> list[str]:
    """Return plausible member-set prefixes, with confirmed manifests first."""

    prefixes: list[str] = []

    def add(value: str) -> None:
        value = str(value or "").strip()
        if value and _TOKEN_RE.match(value) and value not in prefixes:
            prefixes.append(value)

    add(preferred_prefix)
    for manifest in sorted(Path(final_dir).glob("*.subfamily_confirm.manifest.json")):
        name = manifest.name
        add(name[: -len(".subfamily_confirm.manifest.json")])
    for root in (Path(final_dir), Path(input_dir)):
        for stem in ("curated_members", "identified_members", "final_members", "final_gfmembers"):
            for path in sorted(root.glob(f"*.{stem}.id")):
                add(path.name[: -len(f".{stem}.id")])
    return prefixes


def _resolve_stem(
    *,
    final_dir: Path,
    input_dir: Path,
    workspace: Path,
    roots: list[Path],
    prefixes: list[str],
    stem: str,
    require: Require,
    phase: str,
    source_kind: str,
    source_stage: str,
    member_set_id: str,
    require_confirmed: bool,
) -> MemberResolution | None:
    for prefix in prefixes:
        for root in (final_dir, input_dir):
            path_id = root / f"{prefix}.{stem}.id"
            path_pep = root / f"{prefix}.{stem}.pep.fa"
            if require_confirmed and not _confirmed_manifest(final_dir, prefix):
                continue
            result = _build_result(
                path_id=path_id,
                path_pep=path_pep,
                roots=roots,
                require=require,
                phase=phase,
                source_stage=source_stage,
                source_kind=source_kind,
                member_set_id=member_set_id,
            )
            if result:
                return result

    if stem == "final_gfmembers":
        for species in _species_candidates(prefixes):
            for path_id, path_pep in (
                (
                    workspace / "identify" / species / "final" / f"{species}.final_gfmembers.id",
                    workspace / "identify" / species / "final" / f"{species}.final_gfmembers.pep.fa",
                ),
                (final_dir / f"{species}.final_gfmembers.id", final_dir / f"{species}.final_gfmembers.pep.fa"),
                (input_dir / f"{species}.final_gfmembers.id", input_dir / f"{species}.final_gfmembers.pep.fa"),
            ):
                result = _build_result(
                    path_id=path_id,
                    path_pep=path_pep,
                    roots=roots,
                    require=require,
                    phase=phase,
                    source_stage=source_stage,
                    source_kind=source_kind,
                    member_set_id=member_set_id,
                )
                if result:
                    return result
    return None


def _build_result(
    *,
    path_id: Path,
    path_pep: Path,
    roots: list[Path],
    require: Require,
    phase: str,
    source_stage: str,
    source_kind: str,
    member_set_id: str,
) -> MemberResolution | None:
    path_id = _safe_existing(path_id, roots) if require in {"id", "pair"} else None
    path_pep = _safe_existing(path_pep, roots) if require in {"pep", "pair"} else None
    if require == "id" and not path_id:
        return None
    if require == "pep" and not path_pep:
        return None
    if require == "pair" and (not path_id or not path_pep):
        return None

    id_list = _read_ids(path_id) if path_id else []
    pep_ids = _read_fasta_ids(path_pep) if path_pep else []
    if path_id and path_pep and sorted(id_list) != sorted(pep_ids):
        raise MemberResolutionError(f"Member ID/FASTA mismatch: {path_id} vs {path_pep}")
    member_count = len(id_list or pep_ids)
    warnings = []
    if source_kind.startswith("legacy_"):
        warnings.append(f"Using legacy member-set source: {source_kind}")

    return MemberResolution(
        path_id=path_id,
        path_pep=path_pep,
        source_stage=source_stage,
        source_kind=source_kind,
        phase=phase,
        member_set_id=member_set_id,
        member_count=member_count,
        id_checksum=_sha256(path_id) if path_id else "",
        pep_checksum=_sha256(path_pep) if path_pep else "",
        warnings=warnings,
    )


def _confirmed_manifest(final_dir: Path, prefix: str) -> bool:
    manifest_path = final_dir / f"{prefix}.subfamily_confirm.manifest.json"
    if not manifest_path.exists() or manifest_path.stat().st_size == 0:
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    params = manifest.get("parameters") if isinstance(manifest.get("parameters"), dict) else {}
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    return (
        manifest.get("stage") == "subfamily_confirm"
        and manifest.get("status") == "success"
        and (
            params.get("final_members_source_stage") == "subfamily_confirm"
            or bool(artifacts.get("final_members_id") and artifacts.get("final_members_pep"))
        )
    )


def _prefix_candidates(prefix: str, species: str = "", family: str = "") -> list[str]:
    candidates: list[str] = []

    def add(value: str) -> None:
        if value and value not in candidates:
            candidates.append(value)

    add(prefix)
    if species and family:
        add(f"Target.{species}.{family}")
        add(f"{species}.{family}")
    parts = [part for part in prefix.split(".") if part]
    if len(parts) == 3 and parts[0] == "Target":
        add(f"{parts[1]}.{parts[2]}")
    elif len(parts) == 2 and parts[0] not in {"Target", "Multi", "Reference", "Comparative"}:
        add(f"Target.{parts[0]}.{parts[1]}")
    return candidates


def _species_candidates(prefixes: Iterable[str]) -> list[str]:
    species: list[str] = []
    for prefix in prefixes:
        parts = [part for part in prefix.split(".") if part]
        value = ""
        if len(parts) >= 3 and parts[0] == "Target":
            value = parts[1]
        elif len(parts) >= 2 and parts[0] not in {"Target", "Multi", "Reference", "Comparative"}:
            value = parts[0]
        if value and value not in species:
            species.append(value)
    return species


def _family_from_prefix(prefix: str) -> str:
    parts = [part for part in prefix.split(".") if part]
    return parts[-1] if parts else prefix


def _require_token(value: str, name: str) -> str:
    value = str(value or "").strip()
    if not value or not _TOKEN_RE.match(value):
        raise MemberResolutionError(f"Invalid {name}: {value!r}")
    return value


def _optional_token(value: str, name: str) -> str:
    value = str(value or "").strip()
    if value and not _TOKEN_RE.match(value):
        raise MemberResolutionError(f"Invalid {name}: {value!r}")
    return value


def _safe_existing(path: Path, roots: list[Path]) -> Path | None:
    path = Path(path).resolve()
    if not any(path == root or root in path.parents for root in roots):
        raise MemberResolutionError(f"Candidate member path escapes allowed roots: {path}")
    if path.exists() and path.is_file() and path.stat().st_size > 0:
        return path
    return None


def _read_ids(path: Path) -> list[str]:
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            ids.append(value.split()[0])
    return ids


def _read_fasta_ids(path: Path) -> list[str]:
    ids: list[str] = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if line.startswith(">"):
                value = line[1:].strip().split()
                if value:
                    ids.append(value[0])
    return ids


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
