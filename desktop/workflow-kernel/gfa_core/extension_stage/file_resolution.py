"""Shared filename and input-path resolution helpers for extension stages."""

from __future__ import annotations

from pathlib import Path

ID_SUFFIXES = (".id", ".ids", ".geneid", ".geneids")
FASTA_SUFFIXES = (".fa", ".fasta", ".faa", ".fsa", ".pep")
FAMILY_SUFFIXES = (
    ".final_gfmembers.id",
    "final_gfmembers.id",
    ".gfmembers.id",
    "gfmembers.id",
    ".geneids",
    ".geneid",
    ".ids",
    ".id",
    ".pep.fasta",
    ".pep.fa",
    ".faa",
    ".fasta",
    ".fa",
    ".pep",
)


def input_path_equivalents(path: Path) -> list[Path]:
    """Return equivalent filenames for common extension-stage user inputs."""

    variants = [path]
    name = path.name
    replacements = [
        (".ids", ".id"),
        (".id", ".ids"),
        (".geneids", ".geneID"),
        (".geneids", ".ids"),
        (".geneids", ".id"),
        (".geneid", ".geneID"),
        (".geneid", ".id"),
        (".geneid", ".ids"),
        (".geneld", ".geneID"),
        (".geneld", ".id"),
        (".geneld", ".ids"),
        (".fasta", ".fa"),
        (".fa", ".fasta"),
        (".pep.fasta", ".pep.fa"),
        (".pep.fa", ".pep.fasta"),
    ]
    for old, new in replacements:
        if name.lower().endswith(old):
            variants.append(path.with_name(name[: -len(old)] + new))
    out: list[Path] = []
    seen: set[str] = set()
    for item in variants:
        key = item.as_posix()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def resolve_input_path_variant(base_dir: Path, raw: str) -> Path | None:
    """Resolve a user-mentioned file against base_dir with suffix equivalents."""

    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = base_dir / raw
    for path in input_path_equivalents(candidate):
        if path.exists() and path.is_file():
            return path
    return candidate


def looks_like_id_file(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(ID_SUFFIXES) or "id" in path.suffix.lower() or "ids" in name


def looks_like_fasta_file(path: Path) -> bool:
    return path.name.lower().endswith(FASTA_SUFFIXES)


def infer_species_family_from_filename(filename: str) -> tuple[str, str]:
    """Infer Species and optional Gene_Family from names like At.NAC.id."""

    name = Path(filename).name
    if "." not in name:
        return "", ""
    species, rest = name.split(".", 1)
    if not species or not rest:
        return "", ""
    for suffix in FAMILY_SUFFIXES:
        if rest.lower().endswith(suffix):
            rest = rest[: -len(suffix)]
            break
    family = rest.strip("._-")
    return species, family


def family_from_species_filename(filename: str, species: str) -> str:
    inferred_species, family = infer_species_family_from_filename(filename)
    if inferred_species.lower() != str(species or "").lower():
        return ""
    return family
