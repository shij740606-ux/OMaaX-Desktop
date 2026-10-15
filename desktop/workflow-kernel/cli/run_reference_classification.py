#!/usr/bin/env python3
"""Run reference-guided subfamily classification from a joint phylogenetic tree."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
if CONDA_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = CONDA_BIN + os.pathsep + os.environ.get("PATH", "")

from gfa_core.gene_family_scope import read_scope, sanitize_token
from gfa_core.gene_family_stage_context import apply_stage_context, build_stage_context, user_output_prefix_for, write_legacy_manifest_copy


def resolve_path(path_text: str | None, base_dirs: list[Path]) -> Path | None:
    if not path_text:
        return None
    path = Path(path_text)
    if path.is_absolute() and path.exists():
        return path
    candidates = [path]
    for base in base_dirs:
        candidates.append(base / path)
        candidates.append(base / path.name)
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def has_two_column_data(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8", errors="ignore") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                return len(line.split()) >= 2
    except OSError:
        return False
    return False


def read_reference_gene_ids(path: Path) -> set[str]:
    genes: set[str] = set()
    try:
        with path.open(encoding="utf-8", errors="ignore") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    genes.add(parts[0])
                    genes.add(parts[0].lower())
    except OSError:
        pass
    return genes


def read_reference_gene_labels(path: Path, reference_prefix: str) -> set[str]:
    labels: set[str] = set()
    for gene in read_reference_gene_ids(path):
        labels.add(gene)
        labels.add(gene.lower())
        labels.add(f"{reference_prefix}|{gene}")
        labels.add(f"{reference_prefix}|{gene}".lower())
    return labels


def fasta_records(path: Path) -> Iterable[tuple[str, str]]:
    header = ""
    seq_parts: list[str] = []
    with path.open(encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(">"):
                if header:
                    yield header, "".join(seq_parts)
                header = line[1:].strip()
                seq_parts = []
            else:
                seq_parts.append(line)
    if header:
        yield header, "".join(seq_parts)


def fasta_id(header: str) -> str:
    return header.split(None, 1)[0].strip()


def read_fasta_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists() or path.stat().st_size == 0:
        return ids
    for header, _seq in fasta_records(path):
        gene = fasta_id(header)
        if gene:
            ids.add(gene)
    return ids



def gene_id_aliases(gene_id: str) -> set[str]:
    gene = str(gene_id or "").strip()
    if not gene:
        return set()
    aliases = {gene}
    aliases.add(gene.replace(":", "_"))
    aliases.add(gene.replace("_", ":", 1))
    aliases.add(gene.replace("|", "_"))
    aliases.add(gene.replace("_", "|", 1))
    return {item for item in aliases if item}


def clean_protein_sequence(seq: str) -> str:
    return "".join(ch for ch in seq.replace("*", "").upper() if ch.isalpha() or ch == "-")


def find_reference_subfamily(
    workspace: Path,
    input_dir: Path,
    prefix: str,
    reference_prefix: str = "",
) -> Path | None:
    final_dir = workspace / "final"
    search_dirs = [input_dir, final_dir]
    candidates: list[Path] = []
    family = prefix.split(".")[-1] if "." in prefix else prefix
    preferred_names = []
    if reference_prefix and family:
        preferred_names.extend([
            f"{reference_prefix}.{family}.subfamily",
            f"{reference_prefix}.{family}.subfamily.tsv",
            f"{reference_prefix}.{family}.geneID.subfamily",
        ])
    for directory in search_dirs:
        for name in preferred_names:
            candidate = directory / name
            if candidate.exists() and has_two_column_data(candidate):
                return candidate.resolve()

    for directory in search_dirs:
        if not directory.exists():
            continue
        for pattern in ("*.subfamily", "*.subfamily.tsv", "*subfamily*.tsv", "*subfamily*.txt"):
            candidates.extend(directory.glob(pattern))

    generated_names = {
        f"{prefix}.geneID.subfamily",
        f"{prefix}.reference_classification.subfamily.tsv",
    }
    filtered = []
    for candidate in candidates:
        if candidate.name in generated_names:
            continue
        if ".reference_classification." in candidate.name:
            continue
        if has_two_column_data(candidate):
            filtered.append(candidate)

    if not filtered:
        return None
    return sorted(filtered, key=lambda p: (p.stat().st_mtime, str(p)), reverse=True)[0].resolve()


def find_target_proteins(final_dir: Path, prefix: str, workspace: Path | None = None, scope_data: dict | None = None) -> Path | None:
    candidates = [
        final_dir / f"{prefix}.final_members.pep.clean.fa",
        final_dir / f"{prefix}.final_members.pep.fa",
        final_dir / f"{prefix}.final_members.pep.fasta",
    ]
    if workspace is not None and scope_data:
        family = str(scope_data.get("family") or (prefix.split(".")[-1] if "." in prefix else "")).strip()
        targets = [str(x) for x in scope_data.get("target_species") or [] if str(x).strip()]
        if family and len(targets) == 1:
            species = targets[0]
            candidates[0:0] = [
                final_dir / f"Target.{species}.{family}.final_members.pep.clean.fa",
                final_dir / f"Target.{species}.{family}.final_members.pep.fa",
                final_dir / f"{species}.{family}.final_members.pep.clean.fa",
                final_dir / f"{species}.{family}.final_members.pep.fa",
            ]
        scope_id = str(scope_data.get("scope_id") or "").strip()
        manifest_candidates = []
        if scope_id:
            manifest_candidates.append(final_dir / f"{prefix}.{sanitize_token(scope_id)}.phylogeny.manifest.json")
        else:
            manifest_candidates.append(final_dir / f"{prefix}.phylogeny.manifest.json")
        for manifest_path in manifest_candidates:
            if not manifest_path.exists() or manifest_path.stat().st_size == 0:
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            scope_input = str(manifest.get("parameters", {}).get("scope_input_fasta") or "").strip()
            if scope_input:
                path = Path(scope_input)
                if not path.is_absolute():
                    path = workspace / path
                candidates.append(path)
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


def find_reference_proteins(workspace: Path, input_dir: Path, reference_prefix: str, family: str) -> Path | None:
    final_dir = workspace / "final"
    search_dirs = [final_dir, input_dir]
    names = [
        f"{reference_prefix}.{family}.pep.fa",
        f"{reference_prefix}.{family}.pep.fasta",
        f"{reference_prefix}.{family}.sequence_extract.pep.fa",
        f"{reference_prefix}.{family}.sequence_extract.pep.fasta",
        f"{reference_prefix}.{family}.ncbi_fetch.protein.fa",
        f"{reference_prefix}.{family}.protein.fa",
        f"{reference_prefix}.{family}.protein.fasta",
    ]
    for directory in search_dirs:
        for name in names:
            candidate = directory / name
            if candidate.exists() and candidate.stat().st_size > 0:
                return candidate
    for directory in search_dirs:
        for pattern in (
            f"{reference_prefix}.{family}*.sequence_extract.pep.fa",
            f"{reference_prefix}.{family}*.protein.fa",
            f"{reference_prefix}.{family}*.pep.fa",
            f"{reference_prefix}.{family}*.fa",
            f"{reference_prefix}.{family}*.fasta",
        ):
            for candidate in sorted(directory.glob(pattern)):
                if candidate.exists() and candidate.stat().st_size > 0:
                    return candidate
    return None


def resolve_manifest_path(workspace: Path, relative_or_absolute: str) -> Path | None:
    if not relative_or_absolute:
        return None
    path = Path(relative_or_absolute)
    candidates = [path] if path.is_absolute() else [workspace / path, workspace / "final" / path, path]
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate.resolve()
    return None


def collect_target_ids_from_scope(workspace: Path, prefix: str, scope_data: dict | None) -> tuple[set[str], dict[str, int], list[str]]:
    if not scope_data:
        return set(), {}, []
    target_species = [str(item) for item in scope_data.get("target_species") or [] if str(item).strip()]
    comparative_species = [str(item) for item in scope_data.get("comparative_species") or [] if str(item).strip()]
    for item in comparative_species:
        if item not in target_species:
            target_species.append(item)
    ids: set[str] = set()
    counts: dict[str, int] = {}
    warnings: list[str] = []

    family = str(scope_data.get("family") or (prefix.split(".")[-1] if "." in prefix else "")).strip()

    for species in target_species:
        refined_paths = [
            workspace / "final" / f"Target.{species}.{family}.final_members.pep.fa",
            workspace / "final" / f"{species}.{family}.final_members.pep.fa",
            workspace / "final" / f"{species}.final_members.pep.fa",
        ]
        for path in refined_paths:
            if path.exists() and path.stat().st_size > 0:
                species_ids = read_fasta_ids(path)
                ids.update(species_ids)
                counts[species] = len(species_ids)
                break

    manifest_candidates = [workspace / "final" / f"{prefix}.identify.manifest.json"]
    scope_id = str(scope_data.get("scope_id") or "").strip()
    if scope_id:
        manifest_candidates.append(workspace / "final" / f"{prefix}.{sanitize_token(scope_id)}.identify.manifest.json")

    identify_manifest = next((path for path in manifest_candidates if path.exists() and path.stat().st_size > 0), None)
    if identify_manifest:
        try:
            manifest = json.loads(identify_manifest.read_text(encoding="utf-8"))
            for result in manifest.get("species_results", []):
                species = str(result.get("species") or "").strip()
                if species not in target_species:
                    continue
                if species in counts:
                    continue
                final_pep = resolve_manifest_path(workspace, str(result.get("final_pep_file") or ""))
                if final_pep:
                    species_ids = read_fasta_ids(final_pep)
                    ids.update(species_ids)
                    counts[species] = len(species_ids)
        except Exception as exc:
            warnings.append(f"Failed to read identify manifest for scope filtering: {exc}")

    for species in target_species:
        if species in counts:
            continue
        fallback_paths = [
            workspace / "identify" / species / "final" / f"{species}.final_gfmembers.pep.fa",
            workspace / "final" / f"{species}.final_gfmembers.pep.fa",
            workspace / "final" / f"{species}.final_members.pep.fa",
        ]
        for path in fallback_paths:
            if path.exists() and path.stat().st_size > 0:
                species_ids = read_fasta_ids(path)
                ids.update(species_ids)
                counts[species] = len(species_ids)
                break
        if species not in counts:
            warnings.append(f"No target member FASTA found for scope species {species}")

    return ids, counts, warnings


def collect_target_ids_by_species(workspace: Path, prefix: str, scope_data: dict | None) -> tuple[dict[str, set[str]], list[str]]:
    """Return target member IDs grouped by target species for per-species outputs."""
    if not scope_data:
        return {}, []
    target_species = [str(item) for item in scope_data.get("target_species") or [] if str(item).strip()]
    comparative_species = [str(item) for item in scope_data.get("comparative_species") or [] if str(item).strip()]
    for item in comparative_species:
        if item not in target_species:
            target_species.append(item)
    grouped: dict[str, set[str]] = {species: set() for species in target_species}
    warnings: list[str] = []
    family = str(scope_data.get("family") or (prefix.split(".")[-1] if "." in prefix else "")).strip()

    for species in target_species:
        refined_paths = [
            workspace / "final" / f"Target.{species}.{family}.final_members.pep.fa",
            workspace / "final" / f"{species}.{family}.final_members.pep.fa",
            workspace / "final" / f"{species}.final_members.pep.fa",
        ]
        for path in refined_paths:
            if path.exists() and path.stat().st_size > 0:
                grouped[species].update(read_fasta_ids(path))
                break

    manifest_candidates = [workspace / "final" / f"{prefix}.identify.manifest.json"]
    scope_id = str(scope_data.get("scope_id") or "").strip()
    if scope_id:
        manifest_candidates.append(workspace / "final" / f"{prefix}.{sanitize_token(scope_id)}.identify.manifest.json")

    identify_manifest = next((path for path in manifest_candidates if path.exists() and path.stat().st_size > 0), None)
    if identify_manifest:
        try:
            manifest = json.loads(identify_manifest.read_text(encoding="utf-8"))
            for result in manifest.get("species_results", []):
                species = str(result.get("species") or "").strip()
                if species not in grouped or grouped[species]:
                    continue
                final_pep = resolve_manifest_path(workspace, str(result.get("final_pep_file") or ""))
                if final_pep:
                    grouped[species].update(read_fasta_ids(final_pep))
        except Exception as exc:
            warnings.append(f"Failed to read identify manifest for per-species subfamily outputs: {exc}")

    for species in target_species:
        if grouped.get(species):
            continue
        fallback_paths = [
            workspace / "identify" / species / "final" / f"{species}.final_gfmembers.pep.fa",
            workspace / "final" / f"Target.{species}.{scope_data.get('family', '')}.final_members.pep.fa",
            workspace / "final" / f"{species}.{scope_data.get('family', '')}.final_members.pep.fa",
            workspace / "final" / f"{species}.final_members.pep.fa",
        ]
        for path in fallback_paths:
            if path.exists() and path.stat().st_size > 0:
                grouped[species].update(read_fasta_ids(path))
                break
        if not grouped.get(species):
            warnings.append(f"No target member FASTA found for per-species subfamily output: {species}")
    return grouped, warnings


def rewrite_tsv_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def filter_reference_classification_outputs(
    assignment_out: Path,
    subfamily_out: Path,
    reference_subfamily: Path,
    reference_prefix: str,
    allowed_target_ids: set[str],
) -> dict[str, int | bool]:
    if not allowed_target_ids:
        return {"applied": False, "assignment_rows_before": 0, "assignment_rows_after": 0, "dropped_assignment_rows": 0}

    with assignment_out.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    allowed_target_aliases = set()
    for gene_id in allowed_target_ids:
        allowed_target_aliases.update(gene_id_aliases(gene_id))

    filtered_rows = [row for row in rows if row.get("target_gene", "") in allowed_target_aliases]
    if fieldnames:
        rewrite_tsv_rows(assignment_out, fieldnames, filtered_rows)

    reference_labels = read_reference_gene_labels(reference_subfamily, reference_prefix)
    subfamily_rows: list[tuple[str, str]] = []
    before_subfamily = 0
    with subfamily_out.open(encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            before_subfamily += 1
            parts = line.split()
            if len(parts) < 2:
                continue
            label, subfamily = parts[0], parts[1]
            label_aliases = gene_id_aliases(label)
            if label_aliases & allowed_target_aliases or label in reference_labels or label.lower() in reference_labels:
                subfamily_rows.append((label, subfamily))
    with subfamily_out.open("w", encoding="utf-8") as handle:
        for label, subfamily in sorted(subfamily_rows):
            handle.write(f"{label}\t{subfamily}\n")

    return {
        "applied": True,
        "assignment_rows_before": len(rows),
        "assignment_rows_after": len(filtered_rows),
        "dropped_assignment_rows": len(rows) - len(filtered_rows),
        "subfamily_rows_before": before_subfamily,
        "subfamily_rows_after": len(subfamily_rows),
    }


def write_per_species_subfamily_outputs(
    *,
    workspace: Path,
    final_dir: Path,
    prefix: str,
    output_prefix: str,
    assignment_out: Path,
    subfamily_out: Path,
    confidence_out: Path,
    reference_subfamily: Path,
    reference_prefix: str,
    scope_data: dict | None,
) -> dict:
    """Write one subfamily result set per target species.

    The combined output remains the canonical full-scope artifact. These
    per-species files make downstream single-species plotting and manual file
    inspection unambiguous.
    """
    if not scope_data:
        return {"written": 0, "files": [], "warnings": []}
    family = str(scope_data.get("family") or "").strip() or (prefix.split(".")[-1] if "." in prefix else "")
    target_species = [str(item) for item in scope_data.get("target_species") or [] if str(item).strip()]
    if not family or not target_species:
        return {"written": 0, "files": [], "warnings": ["missing family or target_species for per-species outputs"]}

    ids_by_species, warnings = collect_target_ids_by_species(workspace, prefix, scope_data)
    reference_labels = read_reference_gene_labels(reference_subfamily, reference_prefix)

    with assignment_out.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assignment_fields = list(reader.fieldnames or [])
        assignment_rows = list(reader)
    with confidence_out.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        confidence_fields = list(reader.fieldnames or [])
        confidence_rows = list(reader)

    subfamily_rows: list[tuple[str, str]] = []
    with subfamily_out.open(encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                subfamily_rows.append((parts[0], parts[1]))

    written_files: list[str] = []
    for species in target_species:
        species_ids = ids_by_species.get(species) or set()
        if not species_ids:
            continue
        species_id_aliases = set()
        for gene_id in species_ids:
            species_id_aliases.update(gene_id_aliases(gene_id))
        species_prefix = f"{species}.{family}.target_plus_reference"
        species_assignment = final_dir / f"{species_prefix}.reference_classification.assignment.tsv"
        species_subfamily = final_dir / f"{species_prefix}.reference_classification.subfamily.tsv"
        species_confidence = final_dir / f"{species_prefix}.reference_classification.confidence.tsv"
        species_compat = final_dir / f"{species_prefix}.geneID.subfamily"
        species_manifest = final_dir / f"{species_prefix}.reference_classification.manifest.json"

        species_assignment_rows = [row for row in assignment_rows if row.get("target_gene", "") in species_id_aliases]
        species_confidence_rows = [row for row in confidence_rows if row.get("gene_id", "") in species_id_aliases]
        species_subfamily_rows = [
            (label, subfamily)
            for label, subfamily in subfamily_rows
            if gene_id_aliases(label) & species_id_aliases or label in reference_labels or label.lower() in reference_labels
        ]
        if assignment_fields:
            rewrite_tsv_rows(species_assignment, assignment_fields, species_assignment_rows)
        if confidence_fields:
            rewrite_tsv_rows(species_confidence, confidence_fields, species_confidence_rows)
        with species_subfamily.open("w", encoding="utf-8") as handle:
            for label, subfamily in sorted(species_subfamily_rows):
                handle.write(f"{label}\t{subfamily}\n")
        shutil.copyfile(species_subfamily, species_compat)

        manifest_payload = {
            "manifest_version": "1.0",
            "stage": "reference_classification",
            "status": "success",
            "prefix": f"{species}.{family}",
            "output_prefix": species_prefix,
            "parent_output_prefix": output_prefix,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "target_species": [species],
            "reference_species": scope_data.get("reference_species") or [],
            "comparative_species": [],
            "scope_mode": "target_plus_reference",
            "scope_id": f"{family}.target-{sanitize_token(species)}.ref-{'-'.join(sanitize_token(x) for x in scope_data.get('reference_species') or [])}.target_plus_reference",
            "analysis_route": "subfamily_prediction",
            "artifacts": {
                "assignment_table": {"path": f"final/{species_assignment.name}", "type": "tsv"},
                "subfamily_file": {"path": f"final/{species_subfamily.name}", "type": "tsv"},
                "geneID_subfamily_compat": {"path": f"final/{species_compat.name}", "type": "tsv"},
                "confidence_table": {"path": f"final/{species_confidence.name}", "type": "tsv"},
            },
            "metrics": {
                "target_gene_count": len(species_assignment_rows),
                "subfamily_rows": len(species_subfamily_rows),
                "source": "split_from_parent_reference_classification",
            },
        }
        species_manifest.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written_files.extend([
            str(species_assignment),
            str(species_subfamily),
            str(species_confidence),
            str(species_compat),
            str(species_manifest),
        ])

    return {"written": len(written_files), "files": written_files, "warnings": warnings}


def tree_tip_counts(tree_file: Path, reference_subfamily: Path, target_prefix: str, reference_prefix: str) -> dict[str, int]:
    try:
        from Bio import Phylo
        ref_genes = read_reference_gene_ids(reference_subfamily)
        tree = Phylo.read(tree_file, "newick")
        terminals = tree.get_terminals()
    except Exception:
        return {"total": 0, "target": 0, "reference": 0}
    reference = 0
    target = 0
    for tip in terminals:
        name = tip.name or ""
        raw = name.split("|", 1)[1] if "|" in name else name
        is_ref = name.startswith(f"{reference_prefix}|") or raw in ref_genes or raw.lower() in ref_genes
        if is_ref:
            reference += 1
        elif name.startswith(f"{target_prefix}|") or not name.startswith(f"{reference_prefix}|"):
            target += 1
    return {"total": len(terminals), "target": target, "reference": reference}


def write_joint_fasta(
    target_proteins: Path,
    reference_proteins: Path,
    reference_subfamily: Path,
    reference_prefix: str,
    out_fasta: Path,
) -> tuple[int, int]:
    ref_genes = read_reference_gene_ids(reference_subfamily)
    target_count = 0
    ref_count = 0
    seen: set[str] = set()
    out_fasta.parent.mkdir(parents=True, exist_ok=True)
    with out_fasta.open("w", encoding="utf-8") as out:
        for header, seq in fasta_records(target_proteins):
            gene = fasta_id(header)
            clean_seq = clean_protein_sequence(seq)
            if not gene or not clean_seq:
                continue
            if gene in seen:
                continue
            seen.add(gene)
            out.write(f">{gene}\n{clean_seq}\n")
            target_count += 1
        for header, seq in fasta_records(reference_proteins):
            gene = fasta_id(header)
            if gene not in ref_genes and gene.lower() not in ref_genes:
                continue
            clean_seq = clean_protein_sequence(seq)
            if not gene or not clean_seq:
                continue
            label = f"{reference_prefix}|{gene}"
            if label in seen:
                continue
            seen.add(label)
            out.write(f">{label}\n{clean_seq}\n")
            ref_count += 1
    return target_count, ref_count


def run_logged(cmd: list[str], stdout_path: Path | None = None) -> None:
    print("[CMD]", " ".join(cmd))
    if stdout_path is None:
        result = subprocess.run(cmd, text=True, capture_output=True, cwd=str(PROJECT_ROOT), check=False)
    else:
        with stdout_path.open("w", encoding="utf-8") as out_handle:
            result = subprocess.run(cmd, text=True, stdout=out_handle, stderr=subprocess.PIPE, cwd=str(PROJECT_ROOT), check=False)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(cmd)}")


def run_subfamily_tree_plot(
    tree_file: Path,
    subfamily_file: Path,
    final_dir: Path,
    prefix: str,
    layout: str = "both",
    target_species: str = "",
    reference_species: str = "",
) -> dict[str, object]:
    """Draw the final phylogeny again with reference-guided subfamily labels."""
    script = PROJECT_ROOT / "scripts" / "tree_plot.R"
    rscript = Path(shutil.which("Rscript") or "Rscript")
    output_pdf = final_dir / f"{prefix}.reference_classification.tree.pdf"
    expected = [
        output_pdf,
        output_pdf.with_suffix(".png"),
        final_dir / f"{prefix}.reference_classification.tree.circular_tree.pdf",
        final_dir / f"{prefix}.reference_classification.tree.circular_tree.png",
    ]

    if not script.exists():
        return {"success": False, "error": f"Tree plot script not found: {script}", "output_files": []}
    if not tree_file.exists():
        return {"success": False, "error": f"Tree file not found: {tree_file}", "output_files": []}
    if not subfamily_file.exists():
        return {"success": False, "error": f"Subfamily file not found: {subfamily_file}", "output_files": []}

    cmd = [
        str(rscript),
        str(script),
        str(tree_file.resolve()),
        str(subfamily_file.resolve()),
        str(output_pdf.resolve()),
        layout,
    ]
    if target_species or reference_species:
        cmd.extend([target_species, reference_species])
    print("[CMD]", " ".join(cmd))
    result = subprocess.run(cmd, text=True, capture_output=True, cwd=str(final_dir), check=False)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    output_files = [str(path.resolve()) for path in expected if path.exists() and path.stat().st_size > 0]
    return {
        "success": result.returncode == 0,
        "command": " ".join(cmd),
        "returncode": result.returncode,
        "stdout": result.stdout[-1000:] if result.stdout else "",
        "stderr": result.stderr[-1000:] if result.stderr else "",
        "output_files": output_files,
        "layout": layout,
        "tree_file": str(tree_file.resolve()),
        "subfamily_file": str(subfamily_file.resolve()),
        "error": "" if result.returncode == 0 else (result.stderr or result.stdout or "tree plotting failed")[-1000:],
    }


def read_subfamily_labels(subfamily_file: Path) -> set[str]:
    labels: set[str] = set()
    with subfamily_file.open(encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if parts:
                labels.add(parts[0])
    return labels



def find_publication_phylogeny_tree(final_dir: Path, species_prefix: str, reference_species: str) -> Path | None:
    species = species_prefix.split(".", 1)[0]
    patterns = [
        f"{species_prefix}.*target-{species}.ref-{reference_species}.target_plus_reference.tree.nwk",
        f"{species_prefix}.*target-{species}.ref-{reference_species}.target_plus_reference.iqtree.treefile",
        f"{species_prefix}*target-{species}*ref-{reference_species}*target_plus_reference.tree.nwk",
    ]
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(final_dir.glob(pattern))
    candidates = [p for p in candidates if p.exists() and p.stat().st_size > 0 and "reference_classification" not in p.name]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (".tree.nwk" not in item.name, -item.stat().st_mtime))
    return candidates[0]


def normalize_reference_tip_labels_for_plot(tree_file: Path, reference_subfamily: Path, reference_prefix: str, output_tree: Path) -> tuple[Path, dict[str, object]]:
    from Bio import Phylo

    ref_genes: set[str] = set()
    with reference_subfamily.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            label = line.split()[0]
            if label.startswith(f"{reference_prefix}|"):
                gene = label.split("|", 1)[1]
                ref_genes.add(gene)
                ref_genes.add(gene.lower())
    tree = Phylo.read(tree_file, "newick")
    renamed = 0
    for tip in tree.get_terminals():
        name = tip.name or ""
        if name.startswith(f"{reference_prefix}|"):
            continue
        raw = name.split("|", 1)[1] if "|" in name else name
        if raw in ref_genes or raw.lower() in ref_genes:
            tip.name = f"{reference_prefix}|{raw}"
            renamed += 1
    output_tree.parent.mkdir(parents=True, exist_ok=True)
    Phylo.write(tree, output_tree, "newick")
    return output_tree, {"source_tree": str(tree_file), "renamed_reference_tips": renamed}

def prune_tree_to_labels(tree_file: Path, keep_labels: set[str], output_tree: Path) -> tuple[bool, int, int, str]:
    """Write a tree containing only tips listed in keep_labels."""
    try:
        from Bio import Phylo

        tree = Phylo.read(tree_file, "newick")
        original_tips = list(tree.get_terminals())
        keep_names = {tip.name for tip in original_tips if tip.name in keep_labels}
        if len(keep_names) < 3:
            return False, len(original_tips), len(keep_names), "fewer than 3 matching tree tips"
        pruned = copy.deepcopy(tree)
        for tip in list(pruned.get_terminals()):
            if tip.name not in keep_names:
                pruned.prune(tip)
        output_tree.parent.mkdir(parents=True, exist_ok=True)
        Phylo.write(pruned, output_tree, "newick")
        return True, len(original_tips), len(keep_names), ""
    except Exception as exc:
        return False, 0, 0, str(exc)


def tree_label_match_summary(tree_file: Path, keep_labels: set[str]) -> dict[str, object]:
    """Summarize whether a tree exactly matches the expected subfamily labels."""
    try:
        from Bio import Phylo

        tree = Phylo.read(tree_file, "newick")
        tips = [tip.name or "" for tip in tree.get_terminals()]
        tip_set = set(tips)
        missing = sorted(label for label in keep_labels if label not in tip_set)
        extra = sorted(name for name in tip_set if name not in keep_labels)
        return {
            "success": True,
            "total_tips": len(tips),
            "matched_tips": sum(1 for name in tips if name in keep_labels),
            "expected_tips": len(keep_labels),
            "missing_labels": missing,
            "extra_labels": extra,
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "total_tips": 0,
            "matched_tips": 0,
            "expected_tips": len(keep_labels),
            "missing_labels": sorted(keep_labels),
            "extra_labels": [],
        }


def run_per_species_subfamily_tree_plots(
    *,
    tree_file: Path,
    final_dir: Path,
    per_species_outputs: dict,
    layout: str = "both",
    reference_species: str = "",
    output_prefix: str = "",
) -> dict:
    """Create short-name target+reference subfamily confirmation plots per target species."""
    files = [Path(str(path)) for path in per_species_outputs.get("files") or []]
    subfamily_files = [
        path for path in files
        if path.name.endswith(".target_plus_reference.geneID.subfamily")
        or path.name.endswith(".target_plus_reference.reference_classification.subfamily.tsv")
    ]
    # Prefer the compatibility name; it is what downstream single-species stages
    # already search for.
    preferred: dict[str, Path] = {}
    for path in subfamily_files:
        if not path.exists() or path.stat().st_size == 0:
            continue
        prefix = path.name
        if ".target_plus_reference." not in prefix:
            continue
        species_prefix = prefix.split(".target_plus_reference.", 1)[0]
        if species_prefix not in preferred or path.name.endswith(".geneID.subfamily"):
            preferred[species_prefix] = path

    results: list[dict[str, object]] = []
    for species_prefix, subfamily_file in sorted(preferred.items()):
        keep_labels = read_subfamily_labels(subfamily_file)
        plot_prefix = output_prefix if output_prefix and len(preferred) == 1 else f"{species_prefix}.target_plus_reference"
        pruned_tree = final_dir / f"{plot_prefix}.reference_classification.tree.nwk"
        bootstrap_source_tree = find_publication_phylogeny_tree(final_dir, species_prefix, reference_species)
        tree_source_info: dict[str, object] = {}
        if bootstrap_source_tree is not None:
            normalized_tree = final_dir / f"{species_prefix}.target_plus_reference.reference_classification.tree.normalized_tmp.nwk"
            try:
                _, tree_source_info = normalize_reference_tip_labels_for_plot(
                    bootstrap_source_tree,
                    subfamily_file,
                    reference_species,
                    normalized_tree,
                )
                ok, original_tips, kept_tips, error = prune_tree_to_labels(normalized_tree, keep_labels, pruned_tree)
                tree_source_info.update({
                    "normalized_tree": str(normalized_tree),
                    "pruned_tree": str(pruned_tree),
                    "expected_tips": len(keep_labels),
                })
            except Exception as exc:
                ok, original_tips, kept_tips, error = prune_tree_to_labels(tree_file, keep_labels, pruned_tree)
                tree_source_info = {"source_tree": str(tree_file), "fallback_reason": str(exc)}
            finally:
                try:
                    normalized_tree.unlink(missing_ok=True)
                except Exception:
                    pass
        else:
            ok, original_tips, kept_tips, error = prune_tree_to_labels(tree_file, keep_labels, pruned_tree)
            tree_source_info = {"source_tree": str(tree_file), "fallback_reason": "publication phylogeny tree not found"}
        if ok and kept_tips != len(keep_labels):
            ok = False
            error = f"pruned tree kept {kept_tips}/{len(keep_labels)} expected subfamily labels"
        if not ok:
            results.append({
                "prefix": species_prefix,
                "success": False,
                "subfamily_file": str(subfamily_file),
                "tree_file": str(tree_file),
                "original_tips": original_tips,
                "kept_tips": kept_tips,
                "error": error,
            })
            continue
        target_species = species_prefix.split(".", 1)[0]
        plot_result = run_subfamily_tree_plot(
            tree_file=pruned_tree,
            subfamily_file=subfamily_file,
            final_dir=final_dir,
            prefix=plot_prefix,
            layout=layout,
            target_species=target_species,
            reference_species=reference_species,
        )
        plot_result.update({
            "prefix": species_prefix,
            "pruned_tree": str(pruned_tree),
            "original_tips": original_tips,
            "kept_tips": kept_tips,
            "tree_source": tree_source_info,
        })
        results.append(plot_result)

        manifest_path = final_dir / f"{plot_prefix}.reference_classification.manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                artifacts = manifest.setdefault("artifacts", {})
                artifacts["subfamily_tree"] = {
                    "path": f"final/{pruned_tree.name}",
                    "type": "newick",
                    "description": "Per-target target+reference tree used for subfamily confirmation plot",
                }
                for output_file in plot_result.get("output_files", []) or []:
                    p = Path(str(output_file))
                    plot_kind = "circular" if ".circular_tree" in p.name else "rectangular"
                    artifacts[f"subfamily_tree_plot_{plot_kind}_{p.suffix.lower().lstrip('.')}"] = {
                        "path": f"final/{p.name}",
                        "type": "image" if p.suffix.lower() == ".png" else "pdf",
                        "description": "Per-target reference-guided subfamily confirmation tree",
                    }
                metrics = manifest.setdefault("metrics", {})
                metrics["subfamily_tree_plot_success"] = bool(plot_result.get("success"))
                metrics["subfamily_tree_kept_tips"] = kept_tips
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except Exception as exc:
                print(f"[WARNING] Failed to update per-species manifest {manifest_path}: {exc}", file=sys.stderr)

    return {
        "success": any(bool(item.get("success")) for item in results),
        "count": len(results),
        "results": results,
    }


def latest_stage_manifest(final_dir: Path, output_prefix: str, stage: str) -> Path | None:
    exact = final_dir / f"{output_prefix}.{stage}.manifest.json"
    if exact.exists() and exact.stat().st_size > 0:
        return exact
    candidates = sorted(
        final_dir.glob(f"*.{stage}.manifest.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        if output_prefix in path.name and path.stat().st_size > 0:
            return path
    for path in candidates:
        if path.stat().st_size <= 0:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("stage") == stage and data.get("status") in {"success", "partial_success"}:
            return path
    return None


def latest_species_evidence_manifest(final_dir: Path, species_prefix: str, stage: str) -> Path | None:
    """Find motif/domain/structure evidence manifest for subfamily confirmation.

    Prefer the target+reference evidence scope so reference genes appear in the
    combo evidence panels.  Fall back to target-only products when a reference
    structure input is unavailable.
    """
    species = species_prefix.split(".", 1)[0]
    candidates = sorted(
        final_dir.glob(f"*.{stage}.manifest.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    fallback: Path | None = None
    for path in candidates:
        if path.stat().st_size <= 0:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("stage") != stage or data.get("status") not in {"success", "partial_success"}:
            continue
        target_species = [str(x) for x in data.get("target_species") or data.get("scope", {}).get("target_species") or []]
        reference_species = [str(x) for x in data.get("reference_species") or data.get("scope", {}).get("reference_species") or []]
        scope_mode = str(data.get("scope_mode") or data.get("scope", {}).get("scope_mode") or "")
        analysis_route = str(data.get("analysis_route") or data.get("parameters", {}).get("analysis_route") or "")
        output_prefix = str(data.get("output_prefix") or data.get("parameters", {}).get("output_prefix") or "")
        prefix = str(data.get("prefix") or "")
        name = path.name
        species_match = (
            target_species == [species]
            or species in target_species
            or output_prefix.startswith(f"{species_prefix}.")
            or prefix == species_prefix
        )
        evidence_match = (
            analysis_route == "subfamily_evidence"
            or (scope_mode == "target_plus_reference" and bool(reference_species))
            or ".target_plus_reference" in output_prefix
        )
        target_only_match = scope_mode == "target_only" or ".target_only" in output_prefix or ".target_only" in name
        if species_match and evidence_match:
            return path
        if fallback is None and species_match:
            if target_only_match or not reference_species:
                fallback = path
    return fallback


def manifest_artifact_path(workspace: Path, manifest: dict, keys: list[str]) -> Path | None:
    artifacts = manifest.get("artifacts") or {}
    if not isinstance(artifacts, dict):
        return None
    for key in keys:
        item = artifacts.get(key)
        if not isinstance(item, dict):
            continue
        text = str(item.get("full_path") or item.get("path") or "").strip()
        if not text:
            continue
        path = Path(text)
        if not path.is_absolute():
            path = workspace / path
        if path.exists() and path.stat().st_size > 0:
            return path.resolve()
    return None



def _strip_species_alias(label: str) -> str:
    text = str(label or "").strip().strip("'\"")
    if "|" in text:
        text = text.split("|", 1)[1]
    return text


def _canonical_evidence_id(label: str) -> str:
    text = _strip_species_alias(label)
    text = text.strip().strip("'\"")
    # Tree/Newick writers replace ':' with '_' for labels such as mRNA:HanXR...,
    # while MEME/hmmscan/GFF keep the original ':' form.  Treat both as the same
    # typed accession and strip repeated type wrappers like transcript:mRNA:.
    while True:
        updated = re.sub(r"^(gene|transcript|CDS|mRNA)[:_]", "", text, flags=re.IGNORECASE)
        if updated == text:
            break
        text = updated
    text = re.sub(r"_[PT][0-9]+$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\.[0-9]+$", "", text)
    return text.lower()


def parse_meme_sequence_ids(meme_xml: Path) -> set[str]:
    try:
        import xml.etree.ElementTree as ET

        root = ET.parse(meme_xml).getroot()
    except Exception:
        return set()
    ids: set[str] = set()
    for elem in root.iter():
        if not str(elem.tag).endswith("sequence"):
            continue
        for key in ("name", "id"):
            value = elem.attrib.get(key)
            if value:
                ids.add(_canonical_evidence_id(value))
                break
    return ids


def parse_domtblout_sequence_ids(domtblout_file: Path) -> set[str]:
    ids: set[str] = set()
    try:
        with domtblout_file.open(encoding="utf-8", errors="ignore") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 4:
                    ids.add(_canonical_evidence_id(parts[3]))
    except OSError:
        return set()
    return ids


def parse_gff_gene_ids(gff3_file: Path) -> set[str]:
    ids: set[str] = set()
    try:
        with gff3_file.open(encoding="utf-8", errors="ignore") as handle:
            for raw in handle:
                if not raw.strip() or raw.startswith("#"):
                    continue
                parts = raw.rstrip("\n").split("\t")
                if len(parts) < 9:
                    continue
                attrs = {}
                for item in parts[8].split(";"):
                    if "=" in item:
                        k, v = item.split("=", 1)
                        attrs[k] = v
                for key in ("ID", "Parent", "Name", "gene_id", "transcript_id", "protein_id"):
                    value = attrs.get(key)
                    if value:
                        for token in value.split(","):
                            ids.add(_canonical_evidence_id(token))
    except OSError:
        return set()
    return ids


def evidence_id_match_summary(
    inputs: dict[str, Path],
    expected_labels: Iterable[str],
    reference_species: str = "",
) -> dict[str, object]:
    expected = {_canonical_evidence_id(x) for x in expected_labels if str(x).strip()}
    ref_tokens = [x.lower() for x in re.split(r"[,;\s]+", reference_species or "") if x]
    def is_reference(label: str) -> bool:
        lowered = str(label or "").lower()
        return any(lowered == token or lowered.startswith(token + "|") or lowered.startswith(token) for token in ref_tokens)
    target_expected = {label for label in expected if not is_reference(label)} if ref_tokens else expected
    summaries: dict[str, object] = {
        "expected_count": len(expected),
        "target_expected_count": len(target_expected),
        "stages": {},
    }
    parsers = {
        "meme_xml": parse_meme_sequence_ids,
        "domtblout_file": parse_domtblout_sequence_ids,
        "gff3_file": parse_gff_gene_ids,
    }
    ok = True
    for key, parser in parsers.items():
        path = inputs.get(key)
        ids = parser(path) if path else set()
        matched = expected & ids
        matched_target = target_expected & ids
        stage_ok = bool(ids) and len(matched) > 0 and len(matched_target) > 0
        ok = ok and stage_ok
        summaries["stages"][key] = {
            "path": str(path) if path else "",
            "id_count": len(ids),
            "matched_expected": len(matched),
            "matched_target_expected": len(matched_target),
            "missing_expected": max(0, len(expected - ids)),
            "missing_target_expected": max(0, len(target_expected - ids)),
        }
    summaries["success"] = ok
    return summaries


def resolve_combo_evidence_inputs(workspace: Path, final_dir: Path, species_prefix: str) -> tuple[dict[str, Path], list[str]]:
    warnings: list[str] = []
    resolved: dict[str, Path] = {}
    specs = {
        "motif": ("meme_xml", ["meme_xml"]),
        "structure": ("gff3_file", ["structure_gff3_filtered"]),
        "domain": ("domtblout_file", ["domtblout"]),
    }
    for stage, (out_key, artifact_keys) in specs.items():
        manifest_path = latest_species_evidence_manifest(final_dir, species_prefix, stage)
        if not manifest_path:
            warnings.append(f"Missing {stage} evidence manifest for {species_prefix} evidence combo")
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            warnings.append(f"Failed to read {stage} manifest {manifest_path}: {exc}")
            continue
        path = manifest_artifact_path(workspace, manifest, artifact_keys)
        if not path:
            warnings.append(f"Missing {stage} artifact {artifact_keys}")
            continue
        resolved[out_key] = path
    return resolved, warnings


def run_evidence_combo_plot(
    *,
    tree_file: Path,
    subfamily_file: Path,
    meme_xml: Path,
    gff3_file: Path,
    domtblout_file: Path,
    output_pdf: Path,
    target_species: str = "",
    reference_species: str = "",
) -> dict[str, object]:
    script = PROJECT_ROOT / "scripts" / "combo_tree_motif_structure_domain.R"
    rscript = Path(shutil.which("Rscript") or "Rscript")
    if not script.exists():
        return {"success": False, "error": f"Combo script not found: {script}", "output_files": []}
    cmd = [
        str(rscript),
        str(script),
        str(tree_file.resolve()),
        str(subfamily_file.resolve()),
        str(meme_xml.resolve()),
        str(gff3_file.resolve()),
        str(domtblout_file.resolve()),
        "1e-5",
        "0",
        str(output_pdf.resolve()),
        "motif,structure,domain",
        target_species,
        reference_species,
    ]
    print("[CMD]", " ".join(cmd))
    result = subprocess.run(cmd, text=True, capture_output=True, cwd=str(PROJECT_ROOT), check=False, timeout=300)
    if result.stdout:
        print(result.stdout[-2000:])
    if result.stderr:
        print(result.stderr[-2000:], file=sys.stderr)
    output_png = output_pdf.with_suffix(".png")
    output_files = [str(path.resolve()) for path in (output_pdf, output_png) if path.exists() and path.stat().st_size > 0]
    return {
        "success": bool(output_files) and result.returncode == 0,
        "command": " ".join(cmd),
        "returncode": result.returncode,
        "stdout": result.stdout[-1000:] if result.stdout else "",
        "stderr": result.stderr[-1000:] if result.stderr else "",
        "output_files": output_files,
        "error": "" if result.returncode == 0 else (result.stderr or result.stdout or "combo plotting failed")[-1000:],
    }


def run_per_species_evidence_combo_plots(
    *,
    workspace: Path,
    final_dir: Path,
    per_species_tree_plots: dict,
    reference_species: str = "",
) -> dict[str, object]:
    warnings: list[str] = []
    results: list[dict[str, object]] = []
    for item in per_species_tree_plots.get("results") or []:
        if not item.get("success"):
            continue
        species_prefix = str(item.get("prefix") or "").strip()
        inputs, species_warnings = resolve_combo_evidence_inputs(workspace, final_dir, species_prefix)
        warnings.extend(species_warnings)
        required = {"meme_xml", "gff3_file", "domtblout_file"}
        if set(inputs) != required:
            results.append({
                "prefix": species_prefix,
                "success": False,
                "error": "; ".join(species_warnings) or "missing target-only evidence inputs",
                "output_files": [],
            })
            continue
        pruned_tree = Path(str(item.get("pruned_tree") or ""))
        subfamily_file = Path(str(item.get("subfamily_file") or ""))
        if not species_prefix or not pruned_tree.exists() or not subfamily_file.exists():
            continue
        keep_labels = read_subfamily_labels(subfamily_file)
        tree_match = tree_label_match_summary(pruned_tree, keep_labels)
        if (
            not tree_match.get("success")
            or tree_match.get("total_tips") != tree_match.get("expected_tips")
            or tree_match.get("matched_tips") != tree_match.get("expected_tips")
        ):
            results.append({
                "prefix": species_prefix,
                "success": False,
                "error": (
                    f"combo tree/subfamily scope mismatch: tree tips={tree_match.get('total_tips')} "
                    f"matched={tree_match.get('matched_tips')} expected={tree_match.get('expected_tips')}"
                ),
                "tree_match": tree_match,
                "output_files": [],
            })
            continue
        evidence_match = evidence_id_match_summary(inputs, keep_labels, reference_species=reference_species)
        if not evidence_match.get("success"):
            results.append({
                "prefix": species_prefix,
                "success": False,
                "error": f"combo evidence scope mismatch: {json.dumps(evidence_match, ensure_ascii=False)}",
                "evidence_match": evidence_match,
                "output_files": [],
            })
            continue
        output_pdf = final_dir / f"{species_prefix}.target_plus_reference.combo.pdf"
        target_species = species_prefix.split(".", 1)[0]
        combo_result = run_evidence_combo_plot(
            tree_file=pruned_tree,
            subfamily_file=subfamily_file,
            meme_xml=inputs["meme_xml"],
            gff3_file=inputs["gff3_file"],
            domtblout_file=inputs["domtblout_file"],
            output_pdf=output_pdf,
            target_species=target_species,
            reference_species=reference_species,
        )
        combo_result.update({"prefix": species_prefix})
        results.append(combo_result)

        manifest_path = final_dir / f"{species_prefix}.target_plus_reference.reference_classification.manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                artifacts = manifest.setdefault("artifacts", {})
                for output_file in combo_result.get("output_files", []) or []:
                    p = Path(str(output_file))
                    artifacts[f"evidence_combo_{p.suffix.lower().lstrip('.')}"] = {
                        "path": f"final/{p.name}",
                        "type": "image" if p.suffix.lower() == ".png" else "pdf",
                        "description": "Per-target tree + subfamily + motif/structure/domain combo",
                    }
                metrics = manifest.setdefault("metrics", {})
                metrics["evidence_combo_success"] = bool(combo_result.get("success"))
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except Exception as exc:
                warnings.append(f"Failed to update evidence combo manifest {manifest_path}: {exc}")

    return {
        "success": any(bool(item.get("success")) for item in results),
        "count": len(results),
        "results": results,
        "warnings": warnings,
    }


def build_neighbor_joining_tree(msa_path: Path, tree_path: Path) -> None:
    from Bio import AlignIO, Phylo
    from Bio.Phylo.TreeConstruction import DistanceCalculator, DistanceTreeConstructor

    alignment = AlignIO.read(msa_path, "fasta")
    calculator = DistanceCalculator("identity")
    distance_matrix = calculator.get_distance(alignment)
    constructor = DistanceTreeConstructor()
    tree = constructor.nj(distance_matrix)
    tree_path.parent.mkdir(parents=True, exist_ok=True)
    Phylo.write(tree, tree_path, "newick")


def build_joint_tree_if_needed(
    tree: Path,
    workspace: Path,
    input_dir: Path,
    analysis_dir: Path,
    prefix: str,
    target_prefix: str,
    reference_prefix: str,
    reference_subfamily: Path,
    scope_data: dict | None = None,
    output_prefix: str | None = None,
) -> tuple[Path, dict[str, str | int | bool]]:
    counts = tree_tip_counts(tree, reference_subfamily, target_prefix, reference_prefix)
    info: dict[str, str | int | bool] = {
        "auto_built_joint_tree": False,
        "input_tree_total_tips": counts["total"],
        "input_tree_target_tips": counts["target"],
        "input_tree_reference_tips": counts["reference"],
    }
    if counts["target"] > 0 and counts["reference"] > 0:
        return tree, info

    family = prefix.split(".", 1)[1] if "." in prefix else ""
    final_dir = workspace / "final"
    output_prefix = output_prefix or prefix
    final_joint_tree = final_dir / f"{output_prefix}.reference_joint.tree.nwk"
    if final_joint_tree.exists() and final_joint_tree.stat().st_size > 0:
        joint_counts = tree_tip_counts(final_joint_tree, reference_subfamily, target_prefix, reference_prefix)
        if joint_counts["target"] > 0 and joint_counts["reference"] > 0:
            info.update({
                "auto_built_joint_tree": True,
                "reused_joint_tree": True,
                "joint_tree": str(final_joint_tree),
                "joint_tree_target_tips": joint_counts["target"],
                "joint_tree_reference_tips": joint_counts["reference"],
            })
            return final_joint_tree, info

    target_proteins = find_target_proteins(final_dir, prefix, workspace=workspace, scope_data=scope_data)
    reference_proteins = find_reference_proteins(workspace, input_dir, reference_prefix, family)
    if target_proteins is None or reference_proteins is None:
        missing = []
        if target_proteins is None:
            missing.append(f"{prefix}.final_members.pep.clean.fa or scoped phylogeny input FASTA")
        if reference_proteins is None:
            missing.append(f"{reference_prefix}.{family}.ncbi_fetch.protein.fa or {reference_prefix}.{family}.pep.fa")
        raise SystemExit(
            "Reference classification requires a joint tree containing target and reference genes. "
            f"The current tree has target={counts['target']}, reference={counts['reference']}; "
            f"auto-build could not proceed because missing: {', '.join(missing)}."
        )

    joint_fasta = analysis_dir / f"{output_prefix}.reference_joint.pep.fa"
    target_count, ref_count = write_joint_fasta(target_proteins, reference_proteins, reference_subfamily, reference_prefix, joint_fasta)
    if target_count == 0 or ref_count == 0:
        raise SystemExit(
            "Reference classification could not build a joint FASTA: "
            f"target sequences={target_count}, reference sequences matching subfamily table={ref_count}."
        )

    joint_msa = analysis_dir / f"{output_prefix}.reference_joint.msa.fa"
    run_logged(["mafft", "--auto", "--thread", "-1", str(joint_fasta)], stdout_path=joint_msa)
    built_tree = analysis_dir / f"{output_prefix}.reference_joint.nj.treefile"
    # Classification only needs a target+reference topology to transfer labels.
    # The publication-grade tree remains the responsibility of the phylogeny stage.
    build_neighbor_joining_tree(joint_msa, built_tree)
    if not built_tree.exists():
        raise SystemExit(f"Auto-built joint tree missing: {built_tree}")
    shutil.copyfile(built_tree, final_joint_tree)
    info.update({
        "auto_built_joint_tree": True,
        "joint_fasta": str(joint_fasta),
        "joint_tree": str(final_joint_tree),
        "joint_target_sequences": target_count,
        "joint_reference_sequences": ref_count,
    })
    return final_joint_tree, info


def write_confidence_table(assignment_tsv: Path, confidence_tsv: Path) -> int:
    rows_written = 0
    with assignment_tsv.open(encoding="utf-8", newline="") as in_handle, confidence_tsv.open(
        "w", encoding="utf-8", newline=""
    ) as out_handle:
        reader = csv.DictReader(in_handle, delimiter="\t")
        writer = csv.DictWriter(out_handle, fieldnames=["gene_id", "subfamily", "confidence", "note"], delimiter="\t")
        writer.writeheader()
        for row in reader:
            writer.writerow(
                {
                    "gene_id": row.get("target_gene", ""),
                    "subfamily": row.get("assigned_subfamily", ""),
                    "confidence": row.get("confidence", ""),
                    "note": row.get("classification_note", ""),
                }
            )
            rows_written += 1
    return rows_written


def summarize_assignment_table(assignment_tsv: Path) -> dict[str, object]:
    assigned = Counter()
    confidence = Counter()
    notes = Counter()
    rows = 0
    with assignment_tsv.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            rows += 1
            if row.get("assigned_subfamily"):
                assigned[row["assigned_subfamily"]] += 1
            if row.get("confidence"):
                confidence[row["confidence"]] += 1
            if row.get("classification_note"):
                notes[row["classification_note"]] += 1
    return {
        "target_gene_count": rows,
        "confidence_counts": dict(sorted(confidence.items())),
        "assigned_subfamily_counts": dict(sorted(assigned.items())),
        "classification_note_counts": dict(sorted(notes.items())),
    }


def infer_target_prefix(tree_file: Path, reference_prefix: str, fallback_prefix: str) -> str:
    try:
        from Bio import Phylo

        tree = Phylo.read(tree_file, "newick")
        counts = Counter()
        for tip in tree.get_terminals():
            name = tip.name or ""
            if "|" not in name:
                continue
            species_prefix = name.split("|", 1)[0]
            if species_prefix != reference_prefix:
                counts[species_prefix] += 1
        if len(counts) == 1:
            return next(iter(counts))
    except Exception:
        pass
    return fallback_prefix


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--input-dir", type=Path)
    parser.add_argument("--tree", type=str, default="")
    parser.add_argument("--scope-file", type=str, default="", help="Gene-family scope JSON from resolve_scope.py")
    parser.add_argument("--analysis-route", default="", help="Explicit analysis route identity for manifest/status")
    parser.add_argument("--member-set-id", default="", help="Explicit member-set identity for manifest/status")
    parser.add_argument("--reference-subfamily", type=str, default="")
    parser.add_argument("--target-prefix", default="")
    parser.add_argument("--reference-prefix", default="At")
    parser.add_argument("--write-gene-subfamily", action="store_true", default=True)
    parser.add_argument("--no-write-gene-subfamily", action="store_false", dest="write_gene_subfamily")
    parser.add_argument("--overwrite-gene-subfamily", action="store_true")
    parser.add_argument("--log-file", type=Path)
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    input_dir = (args.input_dir or (PROJECT_ROOT / "input" / workspace.name)).resolve()
    final_dir = workspace / "final"
    analysis_dir = workspace / "reference_classification" / "analysis"
    final_dir.mkdir(parents=True, exist_ok=True)
    analysis_dir.mkdir(parents=True, exist_ok=True)

    base_dirs = [PROJECT_ROOT, workspace, input_dir, final_dir]
    scope_file = resolve_path(args.scope_file, base_dirs)
    scope_data: dict | None = None
    if args.scope_file:
        if scope_file is None:
            raise SystemExit(f"Scope file not found: {args.scope_file}")
        scope_data = read_scope(scope_file)
    scope_id = str((scope_data or {}).get("scope_id") or "").strip()
    output_prefix = user_output_prefix_for(
        "reference_classification",
        args.prefix,
        scope_data or {},
        analysis_route=args.analysis_route or "subfamily_prediction",
        member_set_id=args.member_set_id,
    ) if scope_id else args.prefix

    tree = resolve_path(args.tree, base_dirs) if args.tree else final_dir / f"{args.prefix}.tree.nwk"
    if not tree or not tree.exists():
        raise SystemExit(f"Tree file not found: {tree}")

    reference_subfamily = resolve_path(args.reference_subfamily, base_dirs)
    if reference_subfamily is None:
        reference_subfamily = find_reference_subfamily(workspace, input_dir, args.prefix, args.reference_prefix)
    if reference_subfamily is None or not reference_subfamily.exists():
        raise SystemExit(
            "Reference subfamily file not found. Provide --reference-subfamily, "
            "or upload a two-column *.subfamily/TSV file to input/final."
        )

    target_prefix = args.target_prefix.strip()
    if not target_prefix:
        target_prefix = infer_target_prefix(tree, args.reference_prefix, args.prefix.split(".")[0])

    tree, joint_info = build_joint_tree_if_needed(
        tree,
        workspace,
        input_dir,
        analysis_dir,
        args.prefix,
        target_prefix,
        args.reference_prefix,
        reference_subfamily,
        scope_data=scope_data,
        output_prefix=output_prefix,
    )

    assignment_out = final_dir / f"{output_prefix}.reference_classification.assignment.tsv"
    subfamily_out = final_dir / f"{output_prefix}.reference_classification.subfamily.tsv"
    confidence_out = final_dir / f"{output_prefix}.reference_classification.confidence.tsv"
    manifest_out = final_dir / f"{output_prefix}.reference_classification.manifest.json"
    report_out = final_dir / f"{output_prefix}.reference_classification_report.md"

    script = PROJECT_ROOT / "scripts" / "reference_classification_from_tree.py"
    cmd = [
        sys.executable,
        str(script),
        "--tree",
        str(tree),
        "--reference-subfamily",
        str(reference_subfamily),
        "--target-prefix",
        target_prefix,
        "--reference-prefix",
        args.reference_prefix,
        "--assignment-out",
        str(assignment_out),
        "--subfamily-out",
        str(subfamily_out),
        "--manifest-out",
        str(manifest_out),
        "--report-out",
        str(report_out),
    ]
    print("[CMD]", " ".join(cmd))
    result = subprocess.run(cmd, text=True, capture_output=True, cwd=str(PROJECT_ROOT), check=False)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.returncode != 0:
        return result.returncode

    allowed_target_ids, scope_target_counts, scope_filter_warnings = collect_target_ids_from_scope(workspace, args.prefix, scope_data)
    filter_info = filter_reference_classification_outputs(
        assignment_out,
        subfamily_out,
        reference_subfamily,
        args.reference_prefix,
        allowed_target_ids,
    )
    confidence_rows = write_confidence_table(assignment_out, confidence_out)
    assignment_summary = summarize_assignment_table(assignment_out)
    per_species_outputs = write_per_species_subfamily_outputs(
        workspace=workspace,
        final_dir=final_dir,
        prefix=args.prefix,
        output_prefix=output_prefix,
        assignment_out=assignment_out,
        subfamily_out=subfamily_out,
        confidence_out=confidence_out,
        reference_subfamily=reference_subfamily,
        reference_prefix=args.reference_prefix,
        scope_data=scope_data,
    )

    compatibility_out = final_dir / f"{output_prefix}.geneID.subfamily"
    compatibility_written = False
    if args.write_gene_subfamily:
        should_overwrite_compat = args.overwrite_gene_subfamily or args.analysis_route == "subfamily_prediction"
        if should_overwrite_compat or not compatibility_out.exists():
            shutil.copyfile(subfamily_out, compatibility_out)
            compatibility_written = True
        else:
            print(f"[INFO] Compatibility subfamily exists, not overwritten: {compatibility_out}")

    target_species_for_scope = [str(item) for item in (scope_data or {}).get("target_species") or [] if str(item).strip()]
    if target_species_for_scope:
        tree_plot_result = {
            "success": False,
            "skipped": True,
            "reason": "reference_classification now emits per-classifiable-species confirmation plots only",
            "output_files": [],
        }
    else:
        plot_subfamily_file = compatibility_out if compatibility_out.exists() else subfamily_out
        tree_plot_result = run_subfamily_tree_plot(
            tree_file=tree,
            subfamily_file=plot_subfamily_file,
            final_dir=final_dir,
            prefix=output_prefix,
            layout="both",
            target_species=target_prefix,
            reference_species=args.reference_prefix,
        )
        if not tree_plot_result.get("success"):
            print(f"[WARNING] Reference classification tree plot failed: {tree_plot_result.get('error', '')}", file=sys.stderr)

    per_species_tree_plots = run_per_species_subfamily_tree_plots(
        tree_file=tree,
        final_dir=final_dir,
        per_species_outputs=per_species_outputs,
        layout="both",
        reference_species=args.reference_prefix,
        output_prefix=output_prefix,
    )
    per_species_evidence_combos = run_per_species_evidence_combo_plots(
        workspace=workspace,
        final_dir=final_dir,
        per_species_tree_plots=per_species_tree_plots,
        reference_species=args.reference_prefix,
    )

    manifest = json.loads(manifest_out.read_text(encoding="utf-8"))
    manifest["prefix"] = args.prefix
    manifest["output_prefix"] = output_prefix
    artifacts = manifest.setdefault("artifacts", {})
    artifacts["confidence_table"] = {
        "path": f"final/{confidence_out.name}",
        "type": "tsv",
        "description": "Target-gene confidence and classification note table",
    }
    if compatibility_written or compatibility_out.exists():
        artifacts["geneID_subfamily_compat"] = {
            "path": f"final/{compatibility_out.name}",
            "type": "tsv",
            "description": "Backward-compatible two-column subfamily file for existing plotting stages",
        }
    if tree_plot_result.get("success"):
        for output_file in tree_plot_result.get("output_files", []):
            plot_path = Path(str(output_file))
            key = (
                "subfamily_tree_"
                + plot_path.stem.replace(f"{args.prefix}.", "").replace(".", "_")
                + plot_path.suffix.lower().replace(".", "_")
            )
            artifacts[key] = {
                "path": f"final/{plot_path.name}",
                "type": "image" if plot_path.suffix.lower() == ".png" else "pdf",
                "description": "Reference-guided phylogenetic tree with subfamily labels",
            }
    for item in per_species_tree_plots.get("results") or []:
        for output_file in item.get("output_files", []) or []:
            plot_path = Path(str(output_file))
            key = "per_species_subfamily_tree_" + plot_path.stem.replace(".", "_") + plot_path.suffix.lower().replace(".", "_")
            artifacts[key] = {
                "path": f"final/{plot_path.name}",
                "type": "image" if plot_path.suffix.lower() == ".png" else "pdf",
                "description": "Per-target reference-guided subfamily confirmation tree",
            }
        if item.get("pruned_tree"):
            p = Path(str(item["pruned_tree"]))
            artifacts["per_species_subfamily_tree_" + p.stem.replace(".", "_")] = {
                "path": f"final/{p.name}",
                "type": "newick",
                "description": "Per-target target+reference tree used for subfamily confirmation plot",
            }
    for item in per_species_evidence_combos.get("results") or []:
        for output_file in item.get("output_files", []) or []:
            plot_path = Path(str(output_file))
            key = "per_species_evidence_combo_" + plot_path.stem.replace(".", "_") + plot_path.suffix.lower().replace(".", "_")
            artifacts[key] = {
                "path": f"final/{plot_path.name}",
                "type": "image" if plot_path.suffix.lower() == ".png" else "pdf",
                "description": "Per-target tree + subfamily + motif/structure/domain combo",
            }
    metrics = manifest.setdefault("metrics", {})
    metrics.update(assignment_summary)
    metrics["confidence_rows"] = confidence_rows
    metrics["scope_filter"] = filter_info
    metrics["per_species_subfamily_outputs"] = per_species_outputs
    metrics["per_species_subfamily_tree_plots"] = per_species_tree_plots
    metrics["per_species_evidence_combo_plots"] = per_species_evidence_combos
    metrics["joint_subfamily_tree_plot_success"] = bool(tree_plot_result.get("success"))
    metrics["per_species_subfamily_tree_plot_success"] = bool(per_species_tree_plots.get("success"))
    metrics["subfamily_tree_plot_success"] = bool(tree_plot_result.get("success") or per_species_tree_plots.get("success"))
    if scope_target_counts:
        metrics["scope_target_member_counts"] = scope_target_counts
    if scope_filter_warnings:
        manifest.setdefault("warnings", []).extend(scope_filter_warnings)
    if per_species_outputs.get("warnings"):
        manifest.setdefault("warnings", []).extend(per_species_outputs.get("warnings") or [])
    for index, file_path in enumerate(per_species_outputs.get("files") or [], start=1):
        p = Path(str(file_path))
        artifacts[f"per_species_subfamily_{index}"] = {
            "path": f"final/{p.name}",
            "type": "tsv" if p.suffix.lower() in {".tsv", ".subfamily"} or "subfamily" in p.name.lower() else "json",
            "description": "Per-target-species subfamily output split from reference classification",
        }
    manifest.setdefault("parameters", {})["target_prefix"] = target_prefix
    manifest.setdefault("parameters", {})["output_prefix"] = output_prefix
    manifest.setdefault("parameters", {})["reference_prefix"] = args.reference_prefix
    manifest.setdefault("parameters", {})["reference_subfamily_file"] = str(reference_subfamily)
    manifest.setdefault("parameters", {})["subfamily_tree_plot"] = tree_plot_result
    if scope_data:
        manifest["scope_id"] = scope_data.get("scope_id", "")
        manifest["scope_mode"] = scope_data.get("scope_mode", "")
        manifest["scope_file"] = str(scope_file)
        manifest["target_species"] = scope_data.get("target_species", [])
        manifest["reference_species"] = scope_data.get("reference_species", [])
        manifest["comparative_species"] = scope_data.get("comparative_species", [])
        manifest["selected_species"] = scope_data.get("selected_species", [])
        manifest.setdefault("inputs", {})["target_species"] = scope_data.get("target_species", [])
        manifest.setdefault("inputs", {})["reference_species"] = scope_data.get("reference_species", [])
        manifest.setdefault("inputs", {})["comparative_species"] = scope_data.get("comparative_species", [])
        manifest.setdefault("inputs", {})["selected_species"] = scope_data.get("selected_species", [])
        manifest["scope"] = {
            "family": scope_data.get("family", ""),
            "prefix": scope_data.get("prefix", ""),
            "scope_id": scope_data.get("scope_id", ""),
            "scope_mode": scope_data.get("scope_mode", ""),
            "target_species": scope_data.get("target_species", []),
            "reference_species": scope_data.get("reference_species", []),
            "comparative_species": scope_data.get("comparative_species", []),
            "selected_species": scope_data.get("selected_species", []),
        }
    manifest.setdefault("parameters", {}).update(joint_info)
    context = build_stage_context(
        stage="reference_classification",
        prefix=args.prefix,
        scope_data=scope_data or {},
        scope_file=scope_file or "",
        output_prefix=output_prefix,
        subfamily_file=subfamily_out,
        analysis_route=args.analysis_route,
        member_set_id=args.member_set_id,
    )
    apply_stage_context(manifest, context)
    if joint_info.get("auto_built_joint_tree"):
        manifest.setdefault("artifacts", {})["joint_tree"] = {
            "path": f"final/{Path(str(joint_info['joint_tree'])).name}",
            "type": "newick",
            "description": "Auto-built target + reference tree used for reference classification",
        }
    manifest_out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if scope_data and scope_data.get("scope_id"):
        scoped_manifest = write_legacy_manifest_copy(manifest_out, args.prefix, str(scope_data.get("scope_id") or ""), "reference_classification")
        if scoped_manifest:
            print(f"legacy_manifest={scoped_manifest}")

    print(f"assignment_out={assignment_out}")
    print(f"subfamily_out={subfamily_out}")
    print(f"confidence_out={confidence_out}")
    print(f"manifest_out={manifest_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
