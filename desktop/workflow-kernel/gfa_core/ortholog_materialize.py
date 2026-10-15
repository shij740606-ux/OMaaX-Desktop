"""Ortholog final artifact materialization shared by backend execution and diagnosis repair."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


ORTHOLOG_DEPENDENCY_OWNERS = {"", "ppi", "kaks"}


def ortholog_output_stem(prefix: str, dependency_owner: str = "") -> str:
    owner = str(dependency_owner or "").strip().lower()
    if owner not in ORTHOLOG_DEPENDENCY_OWNERS:
        raise ValueError(f"unsupported Ortholog dependency owner: {dependency_owner}")
    return f"{prefix}.{owner}" if owner else prefix


@dataclass
class OrthologRunRecord:
    query_species: str
    subject_species: str
    query_fasta: Path
    subject_fasta: Path
    pairs_src: Path
    blast_src: Optional[Path] = None


def ortholog_pairs_has_data(path: Path) -> bool:
    try:
        if not path.exists() or path.stat().st_size == 0:
            return False
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw in handle:
                line = raw.strip()
                if line and not line.startswith("#"):
                    return True
    except Exception:
        return False
    return False


def build_run_record_from_spec(
    ortholog_dir: Path,
    *,
    query_species: str,
    subject_species: str,
    query_fasta: Path,
    subject_fasta: Path,
    run_prefix: str,
) -> Optional[OrthologRunRecord]:
    pairs_src = ortholog_dir / f"{run_prefix}_pairs.txt"
    if not ortholog_pairs_has_data(pairs_src):
        return None
    blast_src = ortholog_dir / f"{run_prefix}.blastp.txt"
    return OrthologRunRecord(
        query_species=query_species,
        subject_species=subject_species,
        query_fasta=query_fasta,
        subject_fasta=subject_fasta,
        pairs_src=pairs_src,
        blast_src=blast_src if blast_src.exists() and blast_src.stat().st_size > 0 else None,
    )


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> Optional[str]:
    try:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return None


def _identity_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def ortholog_run_sidecar_path(mapping_path: Path) -> Path:
    return Path(f"{mapping_path}.identity.json")


def build_ortholog_run_identity(
    *,
    query_species: str,
    reference_taxon_id: str,
    reference_storage_key: str,
    query_fasta: Path,
    reference_pep: Path,
    scope_id: str = "",
    member_set_id: str = "",
    mapping_path: Path,
) -> dict[str, Any]:
    """Build the exact producer identity used to gate intermediate reuse."""
    query_fasta = Path(query_fasta)
    reference_pep = Path(reference_pep)
    mapping_path = Path(mapping_path)
    target_payload = {
        "species": str(query_species or ""),
        "query_pep_path": str(query_fasta.resolve()),
        "query_pep_sha256": _sha256_file(query_fasta) or "",
    }
    member_payload = {
        "scope_id": str(scope_id or ""),
        "member_set_id": str(member_set_id or ""),
        "target_identity": _identity_digest(target_payload),
    }
    core = {
        "reference_taxon_id": str(reference_taxon_id or ""),
        "reference_storage_key": str(reference_storage_key or ""),
        "reference_pep_path": str(reference_pep.resolve()),
        "reference_pep_sha256": _sha256_file(reference_pep) or "",
        "target_identity": _identity_digest(target_payload),
        "member_identity": _identity_digest(member_payload),
        "mapping_path": str(mapping_path.resolve()),
        "mapping_sha256": _sha256_file(mapping_path) or "",
    }
    return {**core, "mapping_identity": _identity_digest(core)}


def write_ortholog_run_sidecar(mapping_path: Path, identity: dict[str, Any]) -> Path:
    sidecar = ortholog_run_sidecar_path(mapping_path)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8")
    return sidecar


def ortholog_run_identity_matches(mapping_path: Path, expected_identity: dict[str, Any]) -> bool:
    mapping_path = Path(mapping_path)
    sidecar = ortholog_run_sidecar_path(mapping_path)
    if not ortholog_pairs_has_data(mapping_path) or not sidecar.is_file():
        return False
    try:
        recorded = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception:
        return False
    keys = (
        "reference_taxon_id",
        "reference_storage_key",
        "reference_pep_path",
        "reference_pep_sha256",
        "target_identity",
        "member_identity",
        "mapping_identity",
        "mapping_path",
        "mapping_sha256",
    )
    return all(recorded.get(key) == expected_identity.get(key) for key in keys)


def materialize_ortholog_final_outputs(
    *,
    final_dir: Path,
    prefix: str,
    dependency_owner: str = "",
    run_records: list[OrthologRunRecord],
    job_id: str,
    workspace: Path,
    reference_species: str,
    reference_taxon_id: str = "",
    reference_storage_key: str = "",
    ortholog_identity: Optional[dict[str, Any]] = None,
    analysis_route: str = "",
    member_set_id: str = "",
    scope_fields: Optional[dict[str, Any]] = None,
    blast_evalue: float = 1e-5,
    cpu: int = 8,
) -> tuple[int, Path]:
    """Write final ortholog mapping files and manifest from CLI intermediate records."""
    if not run_records:
        raise ValueError("ortholog materialize requires at least one run record")

    final_dir.mkdir(parents=True, exist_ok=True)
    scope_fields = dict(scope_fields or {})
    owner = str(dependency_owner or "").strip().lower()
    output_stem = ortholog_output_stem(prefix, owner)

    pairs_dst = final_dir / f"{output_stem}.ortholog_pairs.txt"
    orthologs_dst = final_dir / f"{output_stem}.orthologs.txt"
    raw_pairs_dst = final_dir / f"{output_stem}.ortholog_pairs.raw.txt"

    # run_ortholog.py writes subject<TAB>query<TAB>identity.
    # Downstream PPI expects query/source -> target/reference.
    with raw_pairs_dst.open("w", encoding="utf-8") as raw_handle, \
            pairs_dst.open("w", encoding="utf-8") as pairs_handle, \
            orthologs_dst.open("w", encoding="utf-8") as orthologs_handle:
        raw_handle.write("# Raw ortholog pairs from run_ortholog.py\n")
        raw_handle.write("# Query_Species\tSubject_Species\tSubject_ID\tQuery_ID\tIdentity(%)\n")
        pairs_handle.write("# Ortholog mapping for downstream PPI\n")
        pairs_handle.write("# Format: Source_ID\tTarget_ID\tIdentity(%)\tSource_Species\tTarget_Species\n")
        orthologs_handle.write("# Ortholog mapping for downstream PPI\n")
        orthologs_handle.write("# Format: Source_ID\tTarget_ID\tIdentity(%)\tSource_Species\tTarget_Species\n")
        for record in run_records:
            with record.pairs_src.open("r", encoding="utf-8", errors="ignore") as src_handle:
                for line in src_handle:
                    if not line.strip() or line.startswith("#"):
                        continue
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) >= 2:
                        subject_id = parts[0]
                        query_id = parts[1]
                        rest = parts[2:]
                        source_species = record.query_species or ""
                        target_species_name = record.subject_species or ""
                        raw_handle.write("\t".join([source_species, target_species_name] + parts) + "\n")
                        out_line = "\t".join([query_id, subject_id] + rest + [source_species, target_species_name]) + "\n"
                        pairs_handle.write(out_line)
                        orthologs_handle.write(out_line)

    blast_dst = final_dir / f"{output_stem}.ortholog_blastp.txt"
    with blast_dst.open("w", encoding="utf-8") as blast_handle:
        for record in run_records:
            blast_src = record.blast_src
            if not blast_src or not blast_src.exists():
                continue
            blast_handle.write(
                f"# Query_Species={record.query_species} Subject_Species={record.subject_species} File={blast_src}\n"
            )
            blast_handle.write(blast_src.read_text(encoding="utf-8", errors="ignore"))
            if not blast_handle.tell():
                continue
            blast_handle.write("\n")

    pair_count = 0
    try:
        with pairs_dst.open("r", encoding="utf-8", errors="ignore") as handle:
            pair_count = sum(1 for line in handle if line.strip() and not line.startswith("#"))
    except Exception:
        pair_count = 0
    if pair_count <= 0:
        raise ValueError(f"ortholog materialize produced zero pairs in {pairs_dst}")

    artifacts: dict[str, Any] = {
        "ortholog_pairs": {
            "artifact": "ortholog_pairs",
            "path": f"final/{pairs_dst.name}",
            "full_path": str(pairs_dst.absolute()),
            "type": "table",
            "format": "txt",
            "size": pairs_dst.stat().st_size,
            "sha256": _sha256_file(pairs_dst),
        },
        "ortholog_mapping": {
            "artifact": "ortholog_mapping",
            "path": f"final/{orthologs_dst.name}",
            "full_path": str(orthologs_dst.absolute()),
            "type": "table",
            "format": "txt",
            "size": orthologs_dst.stat().st_size,
            "sha256": _sha256_file(orthologs_dst),
        },
        "raw_ortholog_pairs": {
            "artifact": "raw_ortholog_pairs",
            "path": f"final/{raw_pairs_dst.name}",
            "full_path": str(raw_pairs_dst.absolute()),
            "type": "table",
            "format": "txt",
            "size": raw_pairs_dst.stat().st_size,
            "sha256": _sha256_file(raw_pairs_dst),
        },
    }
    if blast_dst.exists() and blast_dst.stat().st_size > 0:
        artifacts["blastp"] = {
            "artifact": "blastp",
            "path": f"final/{blast_dst.name}",
            "full_path": str(blast_dst.absolute()),
            "type": "blast",
            "format": "txt",
            "size": blast_dst.stat().st_size,
            "sha256": _sha256_file(blast_dst),
        }

    ortholog_manifest = final_dir / f"{output_stem}.ortholog.manifest.json"
    scope_file = str(scope_fields.pop("scope_file", "") or "").strip()
    scope_data = dict(scope_fields)

    identity = dict(ortholog_identity or {})
    identity["mapping_path"] = str(orthologs_dst.resolve())
    identity["mapping_sha256"] = _sha256_file(orthologs_dst) or ""
    if identity:
        identity_core = {key: value for key, value in identity.items() if key != "mapping_identity"}
        identity["mapping_identity"] = _identity_digest(identity_core)
        write_ortholog_run_sidecar(orthologs_dst, identity)

    manifest_data = {
        "manifest_version": "1.0",
        "stage": "ortholog",
        "status": "success",
        "prefix": prefix,
        "dependency_owner": owner,
        "job_id": job_id,
        "workspace": str(workspace),
        "workspace_root": str(workspace),
        "created_at": datetime.now().isoformat(),
        "inputs": [
            {
                "type": "query_protein_fasta",
                "species": record.query_species,
                "path": str(record.query_fasta),
            }
            for record in run_records
        ] + [
            {
                "type": "reference_protein_fasta",
                "species": reference_species,
                "path": str(run_records[0].subject_fasta),
            }
        ],
        "parameters": {
            "prefix": prefix,
            "dependency_owner": owner,
            "blast_evalue": blast_evalue,
            "cpu": cpu,
            "reference_species": reference_species,
            "reference_protein_fasta": str(run_records[0].subject_fasta),
            "reference_taxon_id": str(reference_taxon_id or identity.get("reference_taxon_id") or ""),
            "reference_storage_key": str(reference_storage_key or identity.get("reference_storage_key") or reference_species),
            "reference_pep_sha256": identity.get("reference_pep_sha256") or _sha256_file(run_records[0].subject_fasta) or "",
            "target_identity": identity.get("target_identity") or "",
            "member_identity": identity.get("member_identity") or "",
            "mapping_identity": identity.get("mapping_identity") or "",
            "mapping_path": identity.get("mapping_path") or str(orthologs_dst.resolve()),
        },
        "metrics": {
            "ortholog_pairs": pair_count,
        },
        "artifacts": artifacts,
    }

    from gfa_core.gene_family_stage_context import apply_stage_context, build_stage_context

    stage_context = build_stage_context(
        stage="ortholog",
        prefix=prefix,
        scope_data=scope_data,
        scope_file=scope_file,
        analysis_route=analysis_route,
        member_set_id=member_set_id,
    )
    manifest_data = apply_stage_context(manifest_data, stage_context)

    with ortholog_manifest.open("w", encoding="utf-8") as handle:
        json.dump(manifest_data, handle, ensure_ascii=False, indent=2)

    scope_id = str(manifest_data.get("scope_id") or scope_data.get("scope_id") or "").strip()
    if scope_id:
        from gfa_core.gene_family_scope import sanitize_token

        scoped_manifest = final_dir / f"{output_stem}.{sanitize_token(scope_id)}.ortholog.manifest.json"
        manifest_data["scope_manifest"] = str(scoped_manifest.resolve())
        with scoped_manifest.open("w", encoding="utf-8") as handle:
            json.dump(manifest_data, handle, ensure_ascii=False, indent=2)
        ortholog_manifest = scoped_manifest

    return pair_count, ortholog_manifest
