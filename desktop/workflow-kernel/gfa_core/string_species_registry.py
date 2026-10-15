"""Read-only STRING species snapshot loader and PPI species resolver."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from scripts.prepare_string_species_snapshot import (
    PARSER_SCHEMA_VERSION,
    TreeNode,
    read_tree_nodes,
)


VIRIDIPLANTAE_TAXON_ID = "33090"
ARABIDOPSIS_TAXON_ID = "3702"
PROJECT_SPECIES_ALIASES = {
    "at": ARABIDOPSIS_TAXON_ID,
    "ft": "62330",
    "os": "39947",
}


class StringSnapshotError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class StringSpeciesRecord:
    taxon_id: str
    string_type: str
    compact_name: str
    official_name: str
    domain: str


@dataclass(frozen=True, slots=True)
class StringSpeciesSnapshot:
    root: Path
    string_version: str
    snapshot_id: str
    nodes: Mapping[str, TreeNode]
    registry: Mapping[str, StringSpeciesRecord]
    name_to_taxa: Mapping[str, tuple[str, ...]]
    plant_string_taxa: tuple[str, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_name(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


def _read_registry(path: Path) -> dict[str, StringSpeciesRecord]:
    records: dict[str, StringSpeciesRecord] = {}
    with path.open("r", encoding="utf-8", errors="strict") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.rstrip("\r\n")
            if not line:
                continue
            fields = line.split("\t")
            if fields[0].lstrip("#").strip().lower() == "taxon_id":
                continue
            if raw.startswith("#"):
                continue
            if len(fields) < 5 or not fields[0].strip().isdigit():
                raise ValueError(f"invalid STRING species row at line {line_number}")
            taxon_id = fields[0].strip()
            if taxon_id in records:
                raise ValueError(f"duplicate STRING registry taxon ID: {taxon_id}")
            records[taxon_id] = StringSpeciesRecord(
                taxon_id=taxon_id,
                string_type=fields[1].strip(),
                compact_name=fields[2].strip(),
                official_name=fields[3].strip(),
                domain=fields[4].strip(),
            )
    if not records:
        raise ValueError("STRING species registry is empty")
    return records


def _lineage_contains(
    nodes: Mapping[str, TreeNode], taxon_id: str, ancestor_taxon_id: str
) -> bool:
    current = taxon_id
    while True:
        if current == ancestor_taxon_id:
            return True
        node = nodes[current]
        if node.parent_taxon_id == current:
            return False
        current = node.parent_taxon_id


def _validate_file(path: Path, metadata: object) -> None:
    if not path.is_file() or not isinstance(metadata, dict):
        raise StringSnapshotError(
            f"STRING snapshot file unavailable: {path}",
            code="string_registry_unavailable",
        )
    expected_size = metadata.get("size")
    expected_sha256 = metadata.get("sha256")
    if (
        not isinstance(expected_size, int)
        or not isinstance(expected_sha256, str)
        or path.stat().st_size != expected_size
        or _sha256(path) != expected_sha256
    ):
        raise StringSnapshotError(
            f"STRING snapshot integrity check failed: {path}",
            code="string_registry_integrity_failed",
        )


def load_string_snapshot(root: Path) -> StringSpeciesSnapshot:
    root = Path(root).resolve()
    manifest_path = root / "source_manifest.json"
    if not manifest_path.is_file():
        raise StringSnapshotError(
            f"STRING snapshot manifest unavailable: {manifest_path}",
            code="string_registry_unavailable",
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        snapshot_id = str(manifest["snapshot_id"])
        string_version = str(manifest["string_version"])
        files = manifest["files"]
        if not isinstance(files, dict):
            raise ValueError("files must be an object")
        species_metadata = files.get("species.v12.0.txt")
        tree_metadata = files.get("species.tree.v12.0.txt")
        if not isinstance(species_metadata, dict) or not isinstance(tree_metadata, dict):
            raise ValueError("snapshot file metadata must be objects")
        species_sha256 = str(species_metadata["sha256"])
        tree_sha256 = str(tree_metadata["sha256"])
        species_size = int(species_metadata["size"])
        tree_size = int(tree_metadata["size"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StringSnapshotError(
            f"invalid STRING snapshot manifest: {manifest_path}: {exc}",
            code="string_registry_integrity_failed",
        ) from exc
    return _load_string_snapshot_cached(
        str(root),
        string_version,
        snapshot_id,
        species_sha256,
        tree_sha256,
        species_size,
        tree_size,
    )


@lru_cache(maxsize=4)
def _load_string_snapshot_cached(
    root_text: str,
    string_version: str,
    snapshot_id: str,
    species_sha256: str,
    tree_sha256: str,
    species_size: int,
    tree_size: int,
) -> StringSpeciesSnapshot:
    root = Path(root_text)
    species_path = root / "species.v12.0.txt"
    tree_path = root / "species.tree.v12.0.txt"
    _validate_file(
        species_path,
        {"size": species_size, "sha256": species_sha256},
    )
    _validate_file(
        tree_path,
        {"size": tree_size, "sha256": tree_sha256},
    )
    expected_snapshot_id = hashlib.sha256(
        (
            PARSER_SCHEMA_VERSION
            + "\n"
            + species_sha256
            + "\n"
            + tree_sha256
        ).encode("utf-8")
    ).hexdigest()
    if snapshot_id != expected_snapshot_id:
        raise StringSnapshotError(
            "STRING snapshot_id does not match its file hashes",
            code="string_registry_integrity_failed",
        )
    try:
        registry = _read_registry(species_path)
        nodes = read_tree_nodes(tree_path)
        registry_taxa = set(registry)
        tree_string_taxa = {
            taxon_id for taxon_id, node in nodes.items() if node.is_string_species
        }
        if registry_taxa != tree_string_taxa:
            raise ValueError("STRING registry/tree membership mismatch")
        if VIRIDIPLANTAE_TAXON_ID not in nodes or ARABIDOPSIS_TAXON_ID not in registry:
            raise ValueError("required plant reference nodes are missing")
    except (OSError, UnicodeError, ValueError) as exc:
        raise StringSnapshotError(
            f"STRING snapshot parsing failed: {exc}",
            code="string_registry_integrity_failed",
        ) from exc

    names: dict[str, set[str]] = {}
    for taxon_id, node in nodes.items():
        normalized = _normalize_name(node.taxon_name)
        if normalized:
            names.setdefault(normalized, set()).add(taxon_id)
    for taxon_id, record in registry.items():
        for value in (record.compact_name, record.official_name):
            normalized = _normalize_name(value)
            if normalized:
                names.setdefault(normalized, set()).add(taxon_id)

    plant_string_taxa = tuple(
        sorted(
            (
                taxon_id
                for taxon_id in registry
                if _lineage_contains(nodes, taxon_id, VIRIDIPLANTAE_TAXON_ID)
            ),
            key=int,
        )
    )
    return StringSpeciesSnapshot(
        root=root,
        string_version=string_version,
        snapshot_id=snapshot_id,
        nodes=MappingProxyType(nodes),
        registry=MappingProxyType(registry),
        name_to_taxa=MappingProxyType(
            {
                name: tuple(sorted(taxa, key=int))
                for name, taxa in names.items()
            }
        ),
        plant_string_taxa=plant_string_taxa,
    )


def _unresolved_result(
    snapshot: StringSpeciesSnapshot,
    *,
    resolution_source: str,
    allow_model_fallback: bool,
) -> dict[str, object]:
    if allow_model_fallback:
        arabidopsis = snapshot.nodes[ARABIDOPSIS_TAXON_ID]
        return {
            "selection_mode": "model_fallback",
            "target_tree_taxon_id": "",
            "target_tree_name": "",
            "selected_string_taxon_id": ARABIDOPSIS_TAXON_ID,
            "selected_string_name": arabidopsis.taxon_name,
            "target_string_supported": False,
            "requires_user_confirmation": True,
            "resolution_source": resolution_source,
            "candidate_source": "explicit_model_fallback",
            "candidates": [],
            "issue_code": "ppi_model_fallback_confirmation_required",
            "string_version": snapshot.string_version,
            "snapshot_id": snapshot.snapshot_id,
        }
    return {
        "selection_mode": "unresolved",
        "target_tree_taxon_id": "",
        "target_tree_name": "",
        "selected_string_taxon_id": "",
        "selected_string_name": "",
        "target_string_supported": False,
        "requires_user_confirmation": False,
        "resolution_source": resolution_source,
        "candidate_source": "",
        "candidates": [],
        "issue_code": "target_string_tree_unresolved",
        "string_version": snapshot.string_version,
        "snapshot_id": snapshot.snapshot_id,
    }


def _resolve_target_taxon(
    snapshot: StringSpeciesSnapshot,
    project_species_code: str,
    target_scientific_name: str,
    explicit_taxon_id: str,
) -> tuple[str, str]:
    explicit = str(explicit_taxon_id or "").strip()
    if explicit:
        return (explicit, "explicit_tree_taxon_id") if explicit in snapshot.nodes else ("", "explicit_tree_taxon_id")

    scientific_name = _normalize_name(target_scientific_name)
    if scientific_name:
        matches = snapshot.name_to_taxa.get(scientific_name, ())
        return (matches[0], "scientific_name") if len(matches) == 1 else ("", "scientific_name")

    alias = str(project_species_code or "").strip().casefold()
    taxon_id = PROJECT_SPECIES_ALIASES.get(alias, "")
    return (taxon_id, "project_alias") if taxon_id in snapshot.nodes else ("", "project_alias")


def _candidate_for(
    snapshot: StringSpeciesSnapshot,
    target_ancestors: Mapping[str, int],
    candidate_taxon_id: str,
) -> dict[str, object]:
    current = candidate_taxon_id
    candidate_edges = 0
    while current not in target_ancestors:
        parent = snapshot.nodes[current].parent_taxon_id
        if parent == current:
            break
        current = parent
        candidate_edges += 1
    lca_taxon_id = current
    target_edges = int(target_ancestors[lca_taxon_id])
    node = snapshot.nodes[candidate_taxon_id]
    return {
        "selected_string_taxon_id": candidate_taxon_id,
        "selected_string_name": node.taxon_name,
        "lca_taxon_id": lca_taxon_id,
        "lca_name": snapshot.nodes[lca_taxon_id].taxon_name,
        "target_to_lca_edges": target_edges,
        "candidate_to_lca_edges": candidate_edges,
        "tree_distance": target_edges + candidate_edges,
    }


def resolve_ppi_string_species(
    snapshot: StringSpeciesSnapshot,
    project_species_code: str,
    target_scientific_name: str = "",
    ppi_target_tree_taxon_id: str = "",
    top_n: int = 3,
    allow_model_fallback: bool = False,
) -> dict[str, object]:
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1:
        raise ValueError("top_n must be a positive integer")
    target_taxon_id, resolution_source = _resolve_target_taxon(
        snapshot,
        project_species_code,
        target_scientific_name,
        ppi_target_tree_taxon_id,
    )
    if not target_taxon_id:
        return _unresolved_result(
            snapshot,
            resolution_source=resolution_source,
            allow_model_fallback=allow_model_fallback,
        )

    target_node = snapshot.nodes[target_taxon_id]
    if target_node.is_string_species and target_taxon_id in snapshot.registry:
        return {
            "selection_mode": "exact_string",
            "target_tree_taxon_id": target_taxon_id,
            "target_tree_name": target_node.taxon_name,
            "selected_string_taxon_id": target_taxon_id,
            "selected_string_name": target_node.taxon_name,
            "target_string_supported": True,
            "requires_user_confirmation": False,
            "resolution_source": resolution_source,
            "candidate_source": "string_species_registry",
            "candidates": [],
            "issue_code": "",
            "string_version": snapshot.string_version,
            "snapshot_id": snapshot.snapshot_id,
        }

    target_ancestors: dict[str, int] = {}
    current = target_taxon_id
    distance = 0
    while current not in target_ancestors:
        target_ancestors[current] = distance
        parent = snapshot.nodes[current].parent_taxon_id
        if parent == current:
            break
        current = parent
        distance += 1
    candidates = [
        _candidate_for(snapshot, target_ancestors, candidate_taxon_id)
        for candidate_taxon_id in snapshot.plant_string_taxa
    ]
    candidates.sort(
        key=lambda row: (
            int(row["tree_distance"]),
            int(row["selected_string_taxon_id"]),
        )
    )
    candidates = candidates[:top_n]
    if not candidates:
        return _unresolved_result(
            snapshot,
            resolution_source=resolution_source,
            allow_model_fallback=allow_model_fallback,
        )
    return {
        "selection_mode": "tree_nearest",
        "target_tree_taxon_id": target_taxon_id,
        "target_tree_name": target_node.taxon_name,
        "selected_string_taxon_id": "",
        "selected_string_name": "",
        "target_string_supported": False,
        "requires_user_confirmation": True,
        "resolution_source": resolution_source,
        "candidate_source": "string_species_tree",
        "candidates": candidates,
        "issue_code": "ppi_reference_confirmation_required",
        "string_version": snapshot.string_version,
        "snapshot_id": snapshot.snapshot_id,
    }
