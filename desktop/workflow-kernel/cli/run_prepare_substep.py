#!/usr/bin/env python3
"""Small genome data preparation substeps.

This module exposes reusable pieces of run_genome_prepare.py as independent
stages so agents can call them directly from natural language.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cli.run_genome_prepare import (
    MultiSpeciesDetector,
    MultiSpeciesGenomePreparer,
    compute_file_hash,
    generate_species_config,
    get_tool_versions,
    merge_species_files,
    organize_files_by_config,
    prepare_directories,
)


STAGE_TO_OPERATION = {
    "prepare_organize": "organize",
    "prepare_normalize_gff": "normalize_gff",
    "prepare_longest_transcript": "longest_transcript",
    "prepare_extract_pep": "extract_pep",
    "prepare_extract_cds": "extract_cds",
    "prepare_filter_fasta": "filter_fasta",
    "prepare_simplify_ncbi": "simplify_ncbi",
}

OPERATION_TO_STAGE = {value: key for key, value in STAGE_TO_OPERATION.items()}


def _artifact(path: Path, name: str, artifact_type: str = "file") -> dict[str, Any]:
    return {
        "artifact": name,
        "path": str(path.name),
        "full_path": str(path.absolute()),
        "type": artifact_type,
        "size": path.stat().st_size if path.exists() else 0,
        "sha256": compute_file_hash(path) if path.exists() else "",
        "mtime": path.stat().st_mtime if path.exists() else None,
    }


def _write_manifest(
    workspace: Path,
    input_dir: Path,
    prefix: str,
    stage: str,
    operation: str,
    start_time: datetime,
    artifacts: dict[str, dict[str, Any]],
    metrics: dict[str, Any],
    parameters: dict[str, Any],
    status: str = "success",
    error: str = "",
) -> Path:
    final_dir = workspace / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    end_time = datetime.now()
    manifest = {
        "manifest_version": "1.0",
        "stage": stage,
        "status": status,
        "prefix": prefix,
        "operation": operation,
        "timestamp": end_time.isoformat(),
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "elapsed_sec": round((end_time - start_time).total_seconds(), 2),
        "workspace_root": str(workspace.resolve()),
        "workspace": str(final_dir.resolve()),
        "input_dir": str(input_dir.resolve()),
        "parameters": parameters,
        "metrics": metrics,
        "artifacts": artifacts,
        "tool_versions": get_tool_versions(),
    }
    if error:
        manifest["error"] = error
    manifest_path = final_dir / f"{prefix}.{stage}.manifest.json"
    manifest["artifacts"]["manifest"] = {
        "artifact": "manifest",
        "path": f"final/{manifest_path.name}",
        "full_path": str(manifest_path.absolute()),
        "type": "json",
        "size": 0,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    input_final = input_dir / "final"
    input_final.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(manifest_path, input_final / manifest_path.name)
    except Exception:
        pass
    return manifest_path


def _preparer(input_dir: Path, workspace: Path) -> MultiSpeciesGenomePreparer:
    paths = prepare_directories(input_dir)
    paths["work_dir"] = input_dir / "genome_prepare"
    paths["work_dir"].mkdir(exist_ok=True)
    return MultiSpeciesGenomePreparer(input_dir, workspace, Path("scripts"), paths)


def _iter_species(input_dir: Path):
    return MultiSpeciesDetector.scan_input_dir(input_dir).values()


def _run_organize(input_dir: Path, workspace: Path) -> tuple[dict, dict, dict]:
    org = organize_files_by_config(input_dir)
    artifacts: dict[str, dict[str, Any]] = {}
    metrics = {
        "organized": bool(org.get("organized")),
        "moved_files": len(org.get("moved_files", [])),
        "missing_files": len(org.get("missing_files", [])),
        "target_species": org.get("target_species", []),
        "ref_species": org.get("ref_species", []),
    }
    genome_prepare_dir = input_dir / "genome_prepare"
    genome_prepare_dir.mkdir(exist_ok=True)
    if org.get("organized"):
        merge = merge_species_files(input_dir, genome_prepare_dir, org)
        cfg = generate_species_config(input_dir, genome_prepare_dir, org)
        artifacts["species_config"] = _artifact(cfg, "species_config", "json")
        for key, value in merge.get("merged_files", {}).items():
            path = Path(value)
            if path.exists():
                artifacts[key] = _artifact(path, key, "fasta")
        metrics["merged_files"] = list(merge.get("merged_files", {}))
    return artifacts, metrics, org


def _run_normalize_gff(input_dir: Path, workspace: Path) -> tuple[dict, dict]:
    prep = _preparer(input_dir, workspace)
    artifacts: dict[str, dict[str, Any]] = {}
    count = 0
    for species in _iter_species(input_dir):
        if not species.gff:
            continue
        gff = prep.decompress_if_needed(species.gff)
        db_type = prep.detect_db_type_from_gff(gff)
        out = input_dir / f"{species.species_code}.normalized.gff3"
        prep._preprocess_gff(gff, out, db_type)
        artifacts[f"{species.species_code}_normalized_gff"] = _artifact(out, f"{species.species_code}_normalized_gff", "gff3")
        count += 1
    return artifacts, {"normalized_gff": count}


def _run_longest_transcript(input_dir: Path, workspace: Path) -> tuple[dict, dict]:
    prep = _preparer(input_dir, workspace)
    longest_script = prep._ensure_script("gff_filter_longest.py")
    artifacts: dict[str, dict[str, Any]] = {}
    count = 0
    for species in _iter_species(input_dir):
        if not species.gff:
            continue
        gff = prep.decompress_if_needed(species.gff)
        db_type = prep.detect_db_type_from_gff(gff)
        gff_tmp = prep.work_dir / f"{species.species_code}.gff3.tmp"
        prep._preprocess_gff(gff, gff_tmp, db_type)
        table = prep.work_dir / f"{species.species_code}_gene_mrna_cds.ids"
        out_gff = input_dir / f"{species.species_code}.longest.gff3"
        rc, _, stderr = prep.run_command(
            [sys.executable, str(longest_script), str(gff_tmp.resolve()), str(table.resolve()), str(out_gff.resolve())],
            f"Select longest transcript for {species.species_code}",
        )
        if rc != 0:
            raise RuntimeError(f"Longest transcript failed for {species.species_code}: {stderr}")
        ids = input_dir / f"{species.species_code}.longest.ids"
        prep._write_ids_from_longest_table(table, ids)
        artifacts[f"{species.species_code}_longest_gff"] = _artifact(out_gff, f"{species.species_code}_longest_gff", "gff3")
        artifacts[f"{species.species_code}_longest_ids"] = _artifact(ids, f"{species.species_code}_longest_ids", "txt")
        count += 1
    return artifacts, {"species_with_longest_transcript": count}


def _run_gffread(input_dir: Path, workspace: Path, mode: str) -> tuple[dict, dict]:
    prep = _preparer(input_dir, workspace)
    prep._ensure_tool("gffread")
    artifacts: dict[str, dict[str, Any]] = {}
    count = 0
    flag = "-y" if mode == "pep" else "-x"
    suffix = "pep.fasta" if mode == "pep" else "cds.fasta"
    for species in _iter_species(input_dir):
        if not species.gff or not species.genome:
            continue
        gff = prep.decompress_if_needed(species.gff)
        genome = prep.decompress_if_needed(species.genome)
        out = input_dir / f"{species.species_code}.{suffix}"
        rc, _, stderr = prep.run_command(
            ["gffread", flag, str(out), "-g", str(genome), str(gff)],
            f"Extract {mode.upper()} for {species.species_code}",
        )
        if rc != 0 or not out.exists() or out.stat().st_size == 0:
            raise RuntimeError(f"gffread {mode} extraction failed for {species.species_code}: {stderr}")
        artifacts[f"{species.species_code}_{mode}"] = _artifact(out, f"{species.species_code}_{mode}", "fasta")
        count += 1
    return artifacts, {f"extracted_{mode}_species": count}


def _run_filter_fasta(input_dir: Path, workspace: Path) -> tuple[dict, dict]:
    prep = _preparer(input_dir, workspace)
    prep._ensure_tool("seqtk")
    artifacts: dict[str, dict[str, Any]] = {}
    count = 0
    for ids in sorted(input_dir.glob("*.ids")) + sorted(input_dir.glob("*_mRNA.id")):
        species_code = ids.name.split(".", 1)[0].split("_", 1)[0]
        candidates = sorted(input_dir.glob(f"{species_code}*.fasta")) + sorted(input_dir.glob(f"{species_code}*.fa"))
        for fasta in candidates:
            if ids.resolve() == fasta.resolve() or ".subset." in fasta.name:
                continue
            out = input_dir / f"{fasta.stem}.subset.fasta"
            rc, _, stderr = prep.run_command_to_file(["seqtk", "subseq", str(fasta), str(ids)], out, f"Filter {fasta.name} by {ids.name}")
            if rc != 0:
                raise RuntimeError(f"seqtk subseq failed for {fasta.name}: {stderr}")
            artifacts[f"{out.stem}"] = _artifact(out, out.stem, "fasta")
            count += 1
    return artifacts, {"filtered_fasta_files": count}


def _run_simplify_ncbi(input_dir: Path, workspace: Path) -> tuple[dict, dict]:
    prep = _preparer(input_dir, workspace)
    artifacts: dict[str, dict[str, Any]] = {}
    count = 0
    for species in _iter_species(input_dir):
        if species.gff:
            gff = prep.decompress_if_needed(species.gff)
            out_gff = input_dir / f"{species.species_code}.ncbi_simplified.gff3"
            shutil.copy2(gff, out_gff)
            prep._simplify_ncbi_gff_attrs(out_gff)
            artifacts[f"{species.species_code}_ncbi_simplified_gff"] = _artifact(out_gff, f"{species.species_code}_ncbi_simplified_gff", "gff3")
            count += 1
        if species.cds:
            cds = prep.decompress_if_needed(species.cds)
            out_cds = input_dir / f"{species.species_code}.ncbi_normalized.cds.fasta"
            prep._normalize_ncbi_cds_headers(cds, out_cds)
            artifacts[f"{species.species_code}_ncbi_normalized_cds"] = _artifact(out_cds, f"{species.species_code}_ncbi_normalized_cds", "fasta")
    return artifacts, {"simplified_ncbi_species": count}


def run_operation(operation: str, input_dir: Path, workspace: Path) -> tuple[dict, dict, dict]:
    if operation == "organize":
        artifacts, metrics, params = _run_organize(input_dir, workspace)
    elif operation == "normalize_gff":
        artifacts, metrics = _run_normalize_gff(input_dir, workspace)
        params = {}
    elif operation == "longest_transcript":
        artifacts, metrics = _run_longest_transcript(input_dir, workspace)
        params = {}
    elif operation == "extract_pep":
        artifacts, metrics = _run_gffread(input_dir, workspace, "pep")
        params = {}
    elif operation == "extract_cds":
        artifacts, metrics = _run_gffread(input_dir, workspace, "cds")
        params = {}
    elif operation == "filter_fasta":
        artifacts, metrics = _run_filter_fasta(input_dir, workspace)
        params = {}
    elif operation == "simplify_ncbi":
        artifacts, metrics = _run_simplify_ncbi(input_dir, workspace)
        params = {}
    else:
        raise ValueError(f"Unknown operation: {operation}")
    return artifacts, metrics, params


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a single genome data preparation substep")
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--prefix", default="data_prep")
    parser.add_argument("--stage", choices=sorted(STAGE_TO_OPERATION), default="")
    parser.add_argument("--operation", choices=sorted(OPERATION_TO_STAGE), default="")
    parser.add_argument("--cpu", default=4)
    args = parser.parse_args()

    operation = args.operation or STAGE_TO_OPERATION.get(args.stage)
    if not operation:
        parser.error("--operation or --stage is required")
    stage = args.stage or OPERATION_TO_STAGE[operation]
    start = datetime.now()
    try:
        args.input_dir.mkdir(parents=True, exist_ok=True)
        args.workspace.mkdir(parents=True, exist_ok=True)
        artifacts, metrics, params = run_operation(operation, args.input_dir, args.workspace)
        params.update({"operation": operation, "cpu": args.cpu})
        manifest = _write_manifest(args.workspace, args.input_dir, args.prefix, stage, operation, start, artifacts, metrics, params)
        print(f"Completed {stage}; manifest: {manifest}")
        return 0
    except Exception as exc:
        manifest = _write_manifest(
            args.workspace,
            args.input_dir,
            args.prefix,
            stage,
            operation,
            start,
            {},
            {},
            {"operation": operation, "cpu": args.cpu},
            status="failed",
            error=str(exc),
        )
        print(f"Failed {stage}: {exc}", file=sys.stderr)
        print(f"Manifest: {manifest}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
