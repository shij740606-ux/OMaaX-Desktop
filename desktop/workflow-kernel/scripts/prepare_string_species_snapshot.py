#!/usr/bin/env python3
"""Download and validate the fixed STRING v12.0 species snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


STRING_VERSION = "12.0"
PARSER_SCHEMA_VERSION = "1"
SOURCE_URLS = {
    "species.v12.0.txt": "https://stringdb-downloads.org/download/species.v12.0.txt",
    "species.tree.v12.0.txt": "https://stringdb-downloads.org/download/species.tree.v12.0.txt",
}


@dataclass(frozen=True, slots=True)
class TreeNode:
    taxon_id: str
    parent_taxon_id: str
    taxon_name: str
    is_string_species: bool


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_tf(value: str, *, line_number: int) -> bool:
    if value == "t":
        return True
    if value == "f":
        return False
    raise ValueError(
        f"invalid is_STRING_species value at line {line_number}: {value!r}"
    )


def read_registry_taxa(path: Path) -> set[str]:
    taxa: set[str] = set()
    with path.open("r", encoding="utf-8", errors="strict") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.rstrip("\r\n")
            if not line:
                continue
            fields = line.split("\t")
            first = fields[0].lstrip("#").strip().lower()
            if first == "taxon_id":
                continue
            if raw.startswith("#"):
                continue
            if len(fields) < 5 or not fields[0].strip().isdigit():
                raise ValueError(f"invalid STRING species row at line {line_number}")
            taxon_id = fields[0].strip()
            if taxon_id in taxa:
                raise ValueError(f"duplicate STRING registry taxon ID: {taxon_id}")
            taxa.add(taxon_id)
    if not taxa:
        raise ValueError("STRING species registry is empty")
    return taxa


def read_tree_nodes(path: Path) -> dict[str, TreeNode]:
    nodes: dict[str, TreeNode] = {}
    with path.open("r", encoding="utf-8", errors="strict") as handle:
        for line_number, raw in enumerate(handle, start=1):
            line = raw.rstrip("\r\n")
            if not line:
                continue
            fields = line.split("\t")
            first = fields[0].lstrip("#").strip().lower()
            if first == "taxon_id":
                continue
            if raw.startswith("#"):
                continue
            if len(fields) != 4:
                raise ValueError(f"invalid STRING tree row at line {line_number}")
            taxon_id, parent_taxon_id, taxon_name, raw_is_string = (
                field.strip() for field in fields
            )
            if not taxon_id.isdigit() or not parent_taxon_id.isdigit():
                raise ValueError(f"invalid STRING tree taxon ID at line {line_number}")
            if taxon_id in nodes:
                raise ValueError(f"duplicate STRING tree taxon ID: {taxon_id}")
            nodes[taxon_id] = TreeNode(
                taxon_id=taxon_id,
                parent_taxon_id=parent_taxon_id,
                taxon_name=taxon_name,
                is_string_species=_parse_tf(raw_is_string, line_number=line_number),
            )
    if not nodes:
        raise ValueError("STRING species tree is empty")
    return nodes


def _validate_parent_graph(nodes: dict[str, TreeNode]) -> None:
    unresolved = sorted(
        {
            node.parent_taxon_id
            for node in nodes.values()
            if node.parent_taxon_id not in nodes
        },
        key=int,
    )
    if unresolved:
        raise ValueError(f"unresolved parent taxon IDs: {unresolved[:10]}")

    completed: set[str] = set()
    for start in nodes:
        if start in completed:
            continue
        path: list[str] = []
        positions: dict[str, int] = {}
        current = start
        while current not in completed:
            if current in positions:
                cycle = path[positions[current] :]
                if len(cycle) == 1 and cycle[0] == "1":
                    break
                raise ValueError(f"non-root cycle in STRING species tree: {cycle[:10]}")
            positions[current] = len(path)
            path.append(current)
            parent = nodes[current].parent_taxon_id
            if parent == current:
                if current != "1":
                    raise ValueError(
                        f"non-root cycle in STRING species tree: {[current]}"
                    )
                break
            current = parent
        completed.update(path)


def validate_snapshot(species_path: Path, tree_path: Path) -> dict[str, object]:
    registry_taxa = read_registry_taxa(species_path)
    nodes = read_tree_nodes(tree_path)
    _validate_parent_graph(nodes)

    for required in {"33090", "3702"}:
        if required not in nodes:
            raise ValueError(f"required STRING tree node missing: {required}")

    tree_string_taxa = {
        taxon_id for taxon_id, node in nodes.items() if node.is_string_species
    }
    missing_from_tree = sorted(registry_taxa - tree_string_taxa, key=int)
    missing_from_registry = sorted(tree_string_taxa - registry_taxa, key=int)
    if missing_from_tree or missing_from_registry:
        raise ValueError(
            "STRING registry/tree STRING species mismatch: "
            f"registry_only={missing_from_tree[:10]}, tree_only={missing_from_registry[:10]}"
        )
    if "3702" not in registry_taxa:
        raise ValueError("Arabidopsis 3702 is not a STRING species")

    return {
        "viridiplantae_taxon_id": "33090",
        "arabidopsis_taxon_id": "3702",
        "registry_species_count": len(registry_taxa),
        "tree_node_count": len(nodes),
        "string_species_count": len(tree_string_taxa),
        "root_taxon_id": "1",
    }


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "PlantAI-STRING-snapshot/1"})
    with urllib.request.urlopen(request, timeout=300) as response:
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output)


def prepare_snapshot(
    destination: Path,
    version: str = STRING_VERSION,
    source_directory: Path | None = None,
) -> dict[str, object]:
    if version != STRING_VERSION:
        raise ValueError(f"only STRING version {STRING_VERSION} is supported")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="string-snapshot-", dir=destination) as tmp:
        temporary = Path(tmp)
        for name, url in SOURCE_URLS.items():
            if source_directory is None:
                _download(url, temporary / name)
            else:
                source = Path(source_directory) / name
                if not source.is_file():
                    raise ValueError(f"local STRING source file missing: {source}")
                shutil.copy2(source, temporary / name)
        validation = validate_snapshot(
            temporary / "species.v12.0.txt",
            temporary / "species.tree.v12.0.txt",
        )
        for name in SOURCE_URLS:
            os.replace(temporary / name, destination / name)

    files = {
        name: {
            "source_url": url,
            "size": (destination / name).stat().st_size,
            "sha256": _sha256(destination / name),
        }
        for name, url in SOURCE_URLS.items()
    }
    snapshot_id = hashlib.sha256(
        (
            PARSER_SCHEMA_VERSION
            + "\n"
            + files["species.v12.0.txt"]["sha256"]
            + "\n"
            + files["species.tree.v12.0.txt"]["sha256"]
        ).encode("utf-8")
    ).hexdigest()
    manifest: dict[str, object] = {
        "string_version": version,
        "source_urls": dict(SOURCE_URLS),
        "transfer_mode": "local_files" if source_directory is not None else "download",
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "parser_schema_version": PARSER_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "files": files,
        "validation": validation,
    }
    manifest_path = destination / "source_manifest.json"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=destination, delete=False
    ) as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary_manifest = Path(handle.name)
    os.replace(temporary_manifest, manifest_path)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default=STRING_VERSION)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--source-directory", type=Path)
    args = parser.parse_args()
    manifest = prepare_snapshot(
        args.destination,
        version=args.version,
        source_directory=args.source_directory,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
