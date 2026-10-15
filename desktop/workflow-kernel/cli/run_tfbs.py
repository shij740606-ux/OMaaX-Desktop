#!/usr/bin/env python3
"""TF binding-site prediction stage using promoter FASTA and MEME/FIMO.

This stage is intentionally independent from the PlantCARE promoter stage:
it extracts family and background promoters from GFF3 + genome FASTA, selects
a plant TF motif database, scans with FIMO, summarizes hits, and writes a
standard bbav5 manifest.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cli.run_promoter import (  # noqa: E402
    find_gene_id_file,
    find_genome_file,
    find_gff3_file,
    find_scope_target_final_gff3,
    prepare_promoter_sequences,
    promoter_gene_like_id,
    read_scope,
)

try:
    from gfa_core.gene_family_scope import sanitize_token
except Exception:  # pragma: no cover - fallback for standalone execution
    def sanitize_token(value: str) -> str:
        token = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())
        return token.strip(".-_") or "NA"


DEFAULT_PLANTTFDB_ROOT = PROJECT_ROOT / "data" / "PlantTFDB_TF_binding_motifs_157_organisms"
DEFAULT_JASPAR_MEME = PROJECT_ROOT / "data" / "tfbs" / "jaspar2026" / "combined" / "JASPAR2026_CORE_plants_non-redundant.meme"
DEFAULT_JASPAR_METADATA = PROJECT_ROOT / "data" / "tfbs" / "jaspar2026" / "raw" / "ultimate_metadata_table_CORE.tsv"
DEFAULT_TAXDUMP = PROJECT_ROOT / "data" / "taxdump.tar.gz"
DEFAULT_FIMO = Path(shutil.which("fimo") or "fimo")
RANK_CLOSENESS = {
    "species": 0,
    "subspecies": 0,
    "forma": 0,
    "varietas": 0,
    "genus": 1,
    "subgenus": 1,
    "tribe": 2,
    "subfamily": 2,
    "family": 3,
    "superfamily": 3,
    "parvorder": 4,
    "infraorder": 4,
    "suborder": 4,
    "order": 4,
    "superorder": 4,
    "class": 5,
    "phylum": 6,
    "kingdom": 7,
    "superkingdom": 8,
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def relpath(path: Path, workspace: Path) -> str:
    try:
        return str(path.resolve().relative_to(workspace.resolve()))
    except Exception:
        return str(path)


def artifact(path: Path, workspace: Path, kind: str) -> dict[str, Any]:
    info = {
        "path": relpath(path, workspace),
        "full_path": str(path.resolve()),
        "type": kind,
        "size": path.stat().st_size if path.exists() else 0,
    }
    if path.exists() and path.is_file():
        info["sha256"] = sha256_file(path)
    return info


def run_cmd(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    print("[CMD] " + " ".join(str(x) for x in cmd))
    proc = subprocess.run(
        [str(x) for x in cmd],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.stdout.strip():
        print(proc.stdout)
    if proc.stderr.strip():
        print(proc.stderr)
    return proc.returncode, proc.stdout, proc.stderr


def count_fasta_records(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if line.startswith(">"):
                count += 1
    return count


def fasta_ids(path: Path) -> list[str]:
    ids: list[str] = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if line.startswith(">"):
                ids.append(line[1:].strip().split()[0])
    return ids


def write_fasta_subset(source: Path, wanted: set[str], dest: Path) -> int:
    kept = 0
    writing = False
    with source.open("r", encoding="utf-8", errors="ignore") as src, dest.open("w", encoding="utf-8") as out:
        for raw in src:
            if raw.startswith(">"):
                seq_id = raw[1:].strip().split()[0]
                gene_like = promoter_gene_like_id(seq_id)
                writing = seq_id not in wanted and gene_like not in wanted
                if writing:
                    kept += 1
                    out.write(raw)
            elif writing:
                out.write(raw)
    return kept


def parse_config_species(input_dir: Path) -> dict[str, str]:
    config = input_dir / "config.xlsx"
    if not config.exists():
        return {}
    try:
        import pandas as pd

        df = pd.read_excel(config)
    except Exception as exc:
        print(f"[WARN] Could not read config.xlsx for species names: {exc}")
        return {}
    mapping: dict[str, str] = {}
    for _, row in df.iterrows():
        code = str(row.get("Species_Code") or row.get("species_code") or row.get("Species") or "").strip()
        name = str(row.get("Species_Name") or row.get("species_name") or row.get("Latin_Name") or "").strip()
        if code and name and name.lower() != "nan":
            mapping[code] = name
    return mapping


def resolve_existing_path(raw: str | Path | None, input_dir: Path, workspace: Path) -> Path | None:
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    candidates = [path]
    if not path.is_absolute():
        candidates.extend([input_dir / path, workspace / path, workspace / "final" / path.name])
    candidates.extend([
        input_dir / path.name,
        input_dir / (path.name[:-3] if path.name.endswith(".gz") else path.name),
        workspace / "final" / path.name,
        workspace / "promoter" / "analysis" / path.name,
        workspace / "tfbs" / "analysis" / path.name,
    ])
    for candidate in candidates:
        if candidate and candidate.exists() and candidate.stat().st_size > 0:
            return candidate.resolve()
    # Old manifests may contain absolute paths from another mount.  Use basename
    # lookup inside the current job input/workspace as a compatibility repair.
    for root in (input_dir, workspace / "final", workspace):
        if root.exists():
            try:
                matches = [p for p in root.rglob(path.name) if p.is_file() and p.stat().st_size > 0]
            except Exception:
                matches = []
            if matches:
                return sorted(matches, key=lambda p: p.stat().st_mtime, reverse=True)[0].resolve()
    return None


def resolve_scope_inputs(scope_file: Path | None, input_dir: Path, workspace: Path) -> tuple[dict[str, Any], Path | None, Path | None, str]:
    if not scope_file:
        return {}, None, None, ""
    scope_data = read_scope(scope_file)
    targets = [str(x).strip() for x in (scope_data.get("target_species") or []) if str(x).strip()]
    if len(targets) != 1:
        raise RuntimeError(f"TFBS stage expects exactly one target species in scope; got {targets or 'empty'}")
    species = targets[0]
    gff3 = find_scope_target_final_gff3(input_dir, workspace, scope_data)
    genome = None
    files = scope_data.get("files") or {}
    species_files = files.get(species) or {}
    for key in ("gff", "gff3"):
        if not gff3:
            for record in species_files.get(key, []) or []:
                gff3 = resolve_existing_path(record.get("path") or record.get("full_path"), input_dir, workspace)
                if gff3:
                    break
    for key in ("genome", "genome_fasta"):
        for record in species_files.get(key, []) or []:
            genome = resolve_existing_path(record.get("path") or record.get("full_path"), input_dir, workspace)
            if genome:
                break
        if genome:
            break
    return scope_data, gff3, genome, species


def parse_planttfdb_manifest(root: Path) -> list[dict[str, Any]]:
    manifest = root / "planttfdb_motif_download_manifest.tsv"
    if not manifest.exists():
        return []
    entries: dict[str, dict[str, Any]] = {}
    with manifest.open("r", encoding="utf-8", errors="ignore") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            if row.get("category") != "all_motifs_meme":
                continue
            species = str(row.get("species") or "").strip()
            filename = str(row.get("filename") or "").strip()
            if not species or not filename:
                continue
            local = root / "all_motifs_meme" / filename
            entries[species.lower()] = {
                "species": species,
                "filename": filename,
                "motif_path": local,
                "source_url": row.get("url") or "",
                "sha256": row.get("sha256") or "",
            }
    return sorted(entries.values(), key=lambda x: x["species"])


def read_taxdump(taxdump: Path) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    names: dict[str, str] = {}
    nodes: dict[str, tuple[str, str]] = {}
    if not taxdump.exists():
        return names, nodes
    with tarfile.open(taxdump, "r:gz") as tar:
        try:
            names_member = tar.getmember("names.dmp")
            with tar.extractfile(names_member) as handle:
                assert handle is not None
                for raw in handle:
                    line = raw.decode("utf-8", errors="replace")
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 4 and parts[3] == "scientific name":
                        names[parts[1].lower()] = parts[0]
        except KeyError:
            pass
        try:
            nodes_member = tar.getmember("nodes.dmp")
            with tar.extractfile(nodes_member) as handle:
                assert handle is not None
                for raw in handle:
                    line = raw.decode("utf-8", errors="replace")
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 3:
                        nodes[parts[0]] = (parts[1], parts[2])
        except KeyError:
            pass
    return names, nodes


def lineage(taxid: str, nodes: dict[str, tuple[str, str]]) -> list[str]:
    order: list[str] = []
    current = str(taxid or "")
    seen = set()
    while current and current not in seen and current in nodes:
        seen.add(current)
        order.append(current)
        parent = nodes[current][0]
        if parent == current:
            break
        current = parent
    return order


def common_lineage_choice(
    target_species: str,
    planttfdb_entries: list[dict[str, Any]],
    taxdump: Path,
    max_rank: str,
) -> dict[str, Any] | None:
    names, nodes = read_taxdump(taxdump)
    if not names or not nodes:
        return None
    target_taxid = names.get(target_species.lower())
    if not target_taxid:
        return None
    target_lineage = lineage(target_taxid, nodes)
    target_index = {taxid: idx for idx, taxid in enumerate(target_lineage)}
    max_allowed = RANK_CLOSENESS.get(max_rank, RANK_CLOSENESS["order"])
    best: dict[str, Any] | None = None
    for entry in planttfdb_entries:
        taxid = names.get(str(entry["species"]).lower())
        if not taxid:
            continue
        candidate_lineage = lineage(taxid, nodes)
        for idx, anc in enumerate(candidate_lineage):
            if anc not in target_index:
                continue
            rank = nodes.get(anc, ("", "no rank"))[1]
            closeness = RANK_CLOSENESS.get(rank, 99)
            if closeness > max_allowed:
                break
            distance = idx + target_index[anc]
            score = (distance, closeness, entry["species"])
            candidate = {
                **entry,
                "selection": "planttfdb_nearest_taxonomy",
                "target_taxid": target_taxid,
                "selected_taxid": taxid,
                "lca_taxid": anc,
                "lca_rank": rank,
                "taxonomic_distance": distance,
            }
            if best is None or score < best["_score"]:
                candidate["_score"] = score
                best = candidate
            break
    if best:
        best.pop("_score", None)
    return best


def motif_count(path: Path) -> int:
    opener = gzip.open if path.suffix == ".gz" else open
    count = 0
    with opener(path, "rt", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if line.startswith("MOTIF "):
                count += 1
    return count


def materialize_motif_file(source: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix == ".gz":
        with gzip.open(source, "rb") as src, dest.open("wb") as out:
            shutil.copyfileobj(src, out)
    else:
        shutil.copy2(source, dest)
    return dest


def select_motif_database(args: argparse.Namespace, target_species_name: str, analysis_dir: Path) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    decisions: list[dict[str, Any]] = []
    plant_entries = parse_planttfdb_manifest(args.planttfdb_root)
    exact = next((x for x in plant_entries if x["species"].lower() == target_species_name.lower()), None)
    selected: dict[str, Any] | None = None
    mode = str(args.motif_db_mode or "auto").lower()

    if mode in {"auto", "planttfdb"} and exact and exact["motif_path"].exists():
        selected = {**exact, "selection": "planttfdb_exact_species"}
    elif mode in {"auto", "planttfdb"} and target_species_name:
        selected = common_lineage_choice(target_species_name, plant_entries, args.taxdump, args.max_nearest_rank)
        if selected and not selected["motif_path"].exists():
            selected = None

    if selected:
        dest = materialize_motif_file(selected["motif_path"], analysis_dir / "selected_motifs.planttfdb.meme")
        info = {
            **selected,
            "database": "PlantTFDB",
            "motif_path": str(selected.get("motif_path") or ""),
            "selected_motif_file": str(dest),
            "motif_count": motif_count(dest),
            "method_note": "PlantTFDB projected TF binding motif set; nearest species selected by NCBI taxdump lineage when exact species is unavailable.",
        }
        decisions.append(info)
        if mode != "both":
            return dest, info, decisions

    if mode in {"auto", "jaspar", "both"}:
        if not args.jaspar_meme.exists():
            raise RuntimeError(f"JASPAR motif file not found: {args.jaspar_meme}")
        dest = materialize_motif_file(args.jaspar_meme, analysis_dir / "selected_motifs.jaspar2026_plants.meme")
        info = {
            "database": "JASPAR2026_CORE_plants",
            "selection": "jaspar_plants_core",
            "species": "plants",
            "motif_path": str(args.jaspar_meme),
            "selected_motif_file": str(dest),
            "motif_count": motif_count(dest),
            "metadata": str(args.jaspar_metadata) if args.jaspar_metadata.exists() else "",
            "method_note": "JASPAR CORE plants non-redundant PFMs in MEME format.",
        }
        decisions.append(info)
        if mode == "both" and selected:
            combined = analysis_dir / "selected_motifs.combined_planttfdb_jaspar.meme"
            with combined.open("w", encoding="utf-8") as out:
                for idx, part in enumerate([Path(selected["selected_motif_file"]) if "selected_motif_file" in selected else analysis_dir / "selected_motifs.planttfdb.meme", dest]):
                    text = part.read_text(encoding="utf-8", errors="ignore")
                    if idx == 0:
                        out.write(text.rstrip() + "\n")
                    else:
                        out.write("\n".join(line for line in text.splitlines() if not line.startswith(("MEME version", "ALPHABET=", "strands:", "Background letter frequencies"))).strip() + "\n")
            combo_info = {
                "database": "PlantTFDB+JASPAR2026_CORE_plants",
                "selection": "combined",
                "selected_motif_file": str(combined),
                "motif_count": motif_count(combined),
            }
            decisions.append(combo_info)
            return combined, combo_info, decisions
        return dest, info, decisions

    raise RuntimeError(
        f"No usable motif database selected for species '{target_species_name}'. "
        "Use --motif-db-mode jaspar or provide PlantTFDB resources."
    )


def run_fimo(args: argparse.Namespace, motif_file: Path, fasta: Path, out_dir: Path) -> Path:
    fimo = args.fimo_bin if args.fimo_bin else (DEFAULT_FIMO if DEFAULT_FIMO.exists() else Path(shutil.which("fimo") or "fimo"))
    out_dir.mkdir(parents=True, exist_ok=True)
    existing_txt = out_dir / "fimo.txt"
    existing_tsv = out_dir / "fimo.tsv"
    existing_log = out_dir / "fimo.run.log"
    if not args.force:
        if existing_txt.exists() and existing_txt.stat().st_size > 0:
            print(f"[TFBS] Reusing existing FIMO output: {existing_txt}")
            return existing_txt
        if existing_tsv.exists() and existing_tsv.stat().st_size > 0:
            print(f"[TFBS] Reusing existing FIMO output: {existing_tsv}")
            return existing_tsv
        if existing_log.exists() and existing_log.stat().st_size > 0:
            head = existing_log.read_text(encoding="utf-8", errors="replace")[:1000]
            if "#pattern name" in head or "motif_id" in head:
                print(f"[TFBS] Reusing existing FIMO text output: {existing_log}")
                return existing_log
    cmd = [
        str(fimo),
        "--verbosity", "1",
        "--thresh", str(args.fimo_pvalue),
        "--max-stored-scores", str(args.max_stored_scores),
        "--skip-matched-sequence",
        "--oc", str(out_dir),
    ]
    if not args.scan_both_strands:
        cmd.append("--norc")
    cmd.extend([str(motif_file), str(fasta)])
    print("[CMD] " + " ".join(str(x) for x in cmd))
    run_log = out_dir / "fimo.run.log"
    with run_log.open("w", encoding="utf-8") as log_handle:
        proc = subprocess.run(
            [str(x) for x in cmd],
            cwd=str(PROJECT_ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    rc = proc.returncode
    if rc != 0:
        stderr = run_log.read_text(encoding="utf-8", errors="replace")[-4000:] if run_log.exists() else ""
        raise RuntimeError(f"FIMO failed for {fasta}: {stderr}")
    txt = out_dir / "fimo.txt"
    tsv = out_dir / "fimo.tsv"
    if txt.exists():
        return txt
    if tsv.exists():
        return tsv
    if run_log.exists() and run_log.stat().st_size > 0:
        head = run_log.read_text(encoding="utf-8", errors="replace")[:1000]
        if "#pattern name" in head or "motif_id" in head:
            return run_log
    raise RuntimeError(f"FIMO completed but no fimo.txt/fimo.tsv found in {out_dir}")


def parse_fimo(path: Path, p_cutoff: float, q_cutoff: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        data_lines = []
        header_seen = False
        for raw in handle:
            if raw.startswith("#pattern name"):
                raw = raw.lstrip("#")
                header_seen = True
            elif raw.startswith("#"):
                continue
            elif raw.lower().startswith("warning:"):
                continue
            elif not header_seen and not raw.startswith("motif_id"):
                continue
            else:
                header_seen = True
            data_lines.append(raw)
        if not data_lines:
            return rows
        reader = csv.DictReader(data_lines, delimiter="\t")
        for row in reader:
            if not row:
                continue
            try:
                pval = float(row.get("p-value") or row.get("p_value") or 1.0)
            except ValueError:
                pval = 1.0
            q_raw = row.get("q-value") or row.get("q_value") or ""
            try:
                qval = float(q_raw) if q_raw not in ("", None) else math.nan
            except ValueError:
                qval = math.nan
            if pval > p_cutoff:
                continue
            if not math.isnan(qval) and qval > q_cutoff:
                continue
            rows.append({
                "motif_id": row.get("motif_id") or row.get("motif name") or row.get("pattern name") or "",
                "motif_alt_id": row.get("motif_alt_id") or row.get("motif_alt") or "",
                "sequence_name": row.get("sequence_name") or row.get("sequence name") or row.get("sequence") or "",
                "start": int(float(row.get("start") or 0)),
                "stop": int(float(row.get("stop") or 0)),
                "strand": row.get("strand") or "",
                "score": float(row.get("score") or 0),
                "p_value": pval,
                "q_value": qval,
                "matched_sequence": row.get("matched_sequence") or row.get("matched sequence") or "",
            })
    return rows


def write_table(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def bh_adjust(pvals: list[float]) -> list[float]:
    n = len(pvals)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvals[i])
    adjusted = [1.0] * n
    prev = 1.0
    for rank, idx in enumerate(reversed(order), start=1):
        real_rank = n - rank + 1
        val = min(prev, pvals[idx] * n / max(1, real_rank))
        adjusted[idx] = min(1.0, val)
        prev = val
    return adjusted


def summarize_hits(
    family_rows: list[dict[str, Any]],
    background_rows: list[dict[str, Any]],
    family_ids: list[str],
    background_ids: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    family_total = len(set(family_ids))
    background_total = len(set(background_ids))
    by_motif: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_motif_bg: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in family_rows:
        by_motif[row["motif_id"]].append(row)
    for row in background_rows:
        by_motif_bg[row["motif_id"]].append(row)

    summary: list[dict[str, Any]] = []
    enrichment: list[dict[str, Any]] = []
    all_motifs = sorted(set(by_motif) | set(by_motif_bg))
    raw_pvals: list[float] = []
    for motif in all_motifs:
        fam = by_motif.get(motif, [])
        bg = by_motif_bg.get(motif, [])
        fam_genes = {x["sequence_name"] for x in fam}
        bg_genes = {x["sequence_name"] for x in bg}
        q_values = [x["q_value"] for x in fam if not math.isnan(x["q_value"])]
        p_values = [x["p_value"] for x in fam if x["p_value"] > 0]
        summary.append({
            "motif_id": motif,
            "motif_alt_id": fam[0].get("motif_alt_id", "") if fam else (bg[0].get("motif_alt_id", "") if bg else ""),
            "family_sites": len(fam),
            "family_genes_with_site": len(fam_genes),
            "background_sites": len(bg),
            "background_genes_with_site": len(bg_genes),
            "min_p_value": min(p_values) if p_values else "",
            "min_q_value": min(q_values) if q_values else "",
            "mean_start": sum(x["start"] for x in fam) / len(fam) if fam else "",
        })
        p = 1.0
        odds = math.nan
        if background_total > 0:
            try:
                from scipy.stats import fisher_exact

                table = [
                    [len(fam_genes), max(0, family_total - len(fam_genes))],
                    [len(bg_genes), max(0, background_total - len(bg_genes))],
                ]
                odds, p = fisher_exact(table, alternative="greater")
            except Exception:
                p = 1.0
        raw_pvals.append(float(p))
        enrichment.append({
            "motif_id": motif,
            "family_gene_hits": len(fam_genes),
            "family_gene_total": family_total,
            "background_gene_hits": len(bg_genes),
            "background_gene_total": background_total,
            "odds_ratio": odds,
            "p_value": p,
            "family_ratio": len(fam_genes) / max(1, family_total),
            "background_ratio": len(bg_genes) / max(1, background_total),
        })
    for row, q in zip(enrichment, bh_adjust(raw_pvals)):
        row["q_value"] = q

    motif_order = [row["motif_id"] for row in sorted(summary, key=lambda x: int(x["family_sites"] or 0), reverse=True)]
    matrix: list[dict[str, Any]] = []
    counts: dict[tuple[str, str], int] = Counter((row["sequence_name"], row["motif_id"]) for row in family_rows)
    for gene in sorted(set(family_ids)):
        record = {"gene_id": gene}
        for motif in motif_order:
            record[motif] = counts.get((gene, motif), 0)
        matrix.append(record)
    return summary, enrichment, matrix


def tree_leaf_order(tree_path: Path, family_ids: list[str]) -> list[str]:
    if not tree_path.exists():
        return []
    try:
        from Bio import Phylo

        tree = Phylo.read(str(tree_path), "newick")
        leaves = [str(term.name or "") for term in tree.get_terminals()]
    except Exception:
        return []
    family_set = set(family_ids)
    ordered = [leaf for leaf in leaves if leaf in family_set]
    if len(ordered) < max(2, min(5, len(family_set))):
        normalized = {promoter_gene_like_id(gene): gene for gene in family_ids}
        ordered = [normalized[leaf] for leaf in leaves if leaf in normalized]
    return ordered


def make_plots(
    analysis_dir: Path,
    final_dir: Path,
    prefix: str,
    summary: list[dict[str, Any]],
    enrichment: list[dict[str, Any]],
    matrix: list[dict[str, Any]],
    family_rows: list[dict[str, Any]],
    top_n: int,
    motif_source: dict[str, Any],
    upstream_length: int,
) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import pandas as pd
    import seaborn as sns

    figures: list[Path] = []
    saved_paths: set[Path] = set()

    def save_figure(fig, stem: str, *, add_pdf: bool = True, legacy_stems: list[str] | None = None) -> None:
        stems = [stem] + list(legacy_stems or [])
        for item in stems:
            png = final_dir / f"{prefix}.{item}.png"
            fig.savefig(png, dpi=300)
            if png not in saved_paths:
                figures.append(png)
                saved_paths.add(png)
            if add_pdf:
                pdf = final_dir / f"{prefix}.{item}.pdf"
                fig.savefig(pdf)
                if pdf not in saved_paths:
                    figures.append(pdf)
                    saved_paths.add(pdf)

    family_ids = [str(row.get("gene_id") or "") for row in matrix if row.get("gene_id")]
    tree_path = final_dir / f"{prefix}.tree.nwk"
    phylo_order = tree_leaf_order(tree_path, family_ids)
    gene_order = phylo_order or sorted(set(family_ids))
    top_summary = sorted(summary, key=lambda x: int(x.get("family_sites") or 0), reverse=True)[:top_n]

    if top_summary:
        df = pd.DataFrame(top_summary)
        fig, ax = plt.subplots(figsize=(9, max(4, min(14, len(df) * 0.28))))
        sns.barplot(data=df, y="motif_id", x="family_sites", color="#4C78A8", ax=ax)
        ax.set_xlabel("FIMO sites in family promoters")
        ax.set_ylabel("Motif")
        ax.set_title("Top TF motifs by site count")
        fig.tight_layout()
        save_figure(fig, "tfbs.top_motifs_barplot", add_pdf=False)
        plt.close(fig)

    if matrix and top_summary:
        motifs = [row["motif_id"] for row in top_summary]
        mdf = pd.DataFrame(matrix).set_index("gene_id")
        if gene_order:
            mdf = mdf.reindex([gene for gene in gene_order if gene in mdf.index])
        mdf = mdf[[m for m in motifs if m in mdf.columns]]
        if not mdf.empty:
            fig_w = max(6, min(18, 0.35 * len(mdf.columns) + 3))
            fig_h = max(4, min(18, 0.25 * len(mdf.index) + 2))
            fig, ax = plt.subplots(figsize=(fig_w, fig_h))
            sns.heatmap(mdf, cmap="YlGnBu", linewidths=0.1, linecolor="white", ax=ax)
            ax.set_xlabel("Motif")
            ax.set_ylabel("Gene")
            ax.set_title("Family promoter TF motif matrix")
            fig.tight_layout()
            save_figure(fig, "tfbs.gene_motif_heatmap", add_pdf=False)
            plt.close(fig)

    if matrix:
        all_df = pd.DataFrame(matrix).set_index("gene_id")
        all_df = all_df.loc[:, (all_df.sum(axis=0) > 0)]
        if gene_order:
            all_df = all_df.reindex([gene for gene in gene_order if gene in all_df.index])
        if not all_df.empty:
            col_order = all_df.sum(axis=0).sort_values(ascending=False).index.tolist()
            all_df = all_df[col_order]
            fig_w = max(10, min(28, 0.12 * len(all_df.columns) + 5))
            fig_h = max(5, min(16, 0.28 * len(all_df.index) + 2.5))
            fig, ax = plt.subplots(figsize=(fig_w, fig_h))
            sns.heatmap(all_df, cmap="YlGnBu", linewidths=0.02, linecolor="white", ax=ax, cbar_kws={"label": "FIMO site count"})
            ax.set_xlabel("Motif")
            ax.set_ylabel("Gene")
            ax.set_title("All detected TF motif counts across family promoters")
            ax.tick_params(axis="x", labelsize=5, rotation=90)
            ax.tick_params(axis="y", labelsize=7)
            fig.tight_layout()
            save_figure(fig, "tfbs_all_motif_count_heatmap")
            plt.close(fig)

    if tree_path.exists() and matrix and top_summary:
        motifs = [row["motif_id"] for row in top_summary[: min(top_n, 30)]]
        heat_df = pd.DataFrame(matrix).set_index("gene_id")
        if phylo_order:
            heat_df = heat_df.reindex([gene for gene in phylo_order if gene in heat_df.index])
        heat_df = heat_df[[m for m in motifs if m in heat_df.columns]]
        if not heat_df.empty:
            try:
                from Bio import Phylo

                tree = Phylo.read(str(tree_path), "newick")
                fig = plt.figure(figsize=(max(11, min(22, 0.35 * len(heat_df.columns) + 8)), max(6, min(18, 0.32 * len(heat_df.index) + 3))))
                gs = fig.add_gridspec(1, 2, width_ratios=[1.4, 2.4], wspace=0.02)
                ax_tree = fig.add_subplot(gs[0, 0])
                ax_heat = fig.add_subplot(gs[0, 1])
                Phylo.draw(tree, axes=ax_tree, do_show=False, show_confidence=False)
                ax_tree.set_title("Phylogeny")
                ax_tree.set_xlabel("")
                ax_tree.set_ylabel("")
                sns.heatmap(heat_df, cmap="YlGnBu", linewidths=0.05, linecolor="white", ax=ax_heat, cbar_kws={"label": "FIMO site count"})
                ax_heat.set_title("TFBS motif count")
                ax_heat.set_xlabel("Motif")
                ax_heat.set_ylabel("")
                ax_heat.tick_params(axis="x", labelsize=6, rotation=90)
                ax_heat.tick_params(axis="y", labelsize=6)
                fig.suptitle("Phylogeny and promoter TFBS profile", y=0.995)
                fig.tight_layout()
                save_figure(fig, "tfbs_phylo_heatmap")
                plt.close(fig)
            except Exception:
                plt.close("all")

    enriched = [row for row in enrichment if row.get("background_gene_total")]
    if enriched:
        edf = pd.DataFrame(enriched)
        edf["neg_log10_q"] = edf["q_value"].apply(lambda x: -math.log10(max(float(x), 1e-300)))
        edf = edf.sort_values(["q_value", "family_ratio"], ascending=[True, False]).head(top_n)
        fig, ax = plt.subplots(figsize=(9, max(4, min(12, len(edf) * 0.3))))
        ax.scatter(edf["family_ratio"], edf["motif_id"], s=(edf["family_gene_hits"] + 1) * 18, c=edf["neg_log10_q"], cmap="viridis")
        ax.set_xlabel("Family gene hit ratio")
        ax.set_ylabel("Motif")
        ax.set_title("TF motif enrichment versus annotated-promoter background")
        cbar = fig.colorbar(ax.collections[0], ax=ax)
        cbar.set_label("-log10(BH q-value)")
        fig.tight_layout()
        save_figure(fig, "tfbs.enrichment_bubble", add_pdf=False)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(9, max(4, min(12, len(edf) * 0.3))))
        plot_x = edf["odds_ratio"].replace([math.inf, -math.inf], math.nan).fillna(edf["family_ratio"])
        ax.scatter(plot_x, edf["motif_id"], s=(edf["family_gene_hits"] + 1) * 20, c=edf["neg_log10_q"], cmap="viridis")
        ax.set_xlabel("Odds ratio (fallback: family hit ratio)")
        ax.set_ylabel("Motif")
        ax.set_title("TF motif enrichment dot plot")
        cbar = fig.colorbar(ax.collections[0], ax=ax)
        cbar.set_label("-log10(BH q-value)")
        fig.tight_layout()
        save_figure(fig, "tfbs_enrichment_dotplot")
        plt.close(fig)

    if family_rows:
        starts = [int(row["start"]) for row in family_rows if int(row.get("start") or 0) > 0]
        if starts:
            fig, ax = plt.subplots(figsize=(8, 4.5))
            ax.hist(starts, bins=40, color="#59A14F", edgecolor="white")
            ax.set_xlabel("Position in extracted upstream promoter sequence")
            ax.set_ylabel("FIMO site count")
            ax.set_title("TFBS positional distribution")
            fig.tight_layout()
            save_figure(fig, "tfbs.position_distribution", add_pdf=False)
            plt.close(fig)

            rel_positions = [min(0, max(-upstream_length, int(row["start"]) - upstream_length)) for row in family_rows if int(row.get("start") or 0) > 0]
            fig, ax = plt.subplots(figsize=(8.5, 4.8))
            sns.kdeplot(x=rel_positions, fill=True, color="#4C78A8", ax=ax, bw_adjust=0.7, clip=(-upstream_length, 0))
            ax.set_xlim(-upstream_length, 0)
            ax.set_xlabel("Position relative to TSS (bp)")
            ax.set_ylabel("Density")
            ax.set_title("TFBS positional density around TSS")
            fig.tight_layout()
            save_figure(fig, "tfbs_position_density")
            plt.close(fig)

    if family_rows and top_summary:
        top_motifs = [row["motif_id"] for row in top_summary[: min(20, len(top_summary))]]
        color_map = dict(zip(top_motifs, sns.color_palette("tab20", n_colors=len(top_motifs))))
        rows_by_gene: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in family_rows:
            if row.get("motif_id") in color_map:
                rows_by_gene[str(row.get("sequence_name") or "")].append(row)
        arch_genes = [gene for gene in gene_order if gene in rows_by_gene] or sorted(rows_by_gene)
        if arch_genes:
            fig_h = max(6, min(18, 0.32 * len(arch_genes) + 2.5))
            fig, ax = plt.subplots(figsize=(12, fig_h))
            y_lookup = {gene: idx for idx, gene in enumerate(reversed(arch_genes))}
            for gene, y in y_lookup.items():
                ax.hlines(y, -upstream_length, 0, color="#555555", linewidth=1)
                for row in rows_by_gene.get(gene, []):
                    start = max(-upstream_length, int(row.get("start") or 0) - upstream_length)
                    stop = min(0, int(row.get("stop") or 0) - upstream_length)
                    width = max(8, stop - start)
                    color = color_map.get(row["motif_id"], "#999999")
                    ax.add_patch(mpatches.Rectangle((start, y - 0.22), width, 0.44, color=color, alpha=0.9))
            ax.axvline(0, color="#222222", linewidth=1)
            ax.set_xlim(-upstream_length, 40)
            ax.set_ylim(-1, len(arch_genes))
            ax.set_yticks(list(y_lookup.values()))
            ax.set_yticklabels(list(y_lookup.keys()), fontsize=7)
            ax.set_xlabel("Position relative to TSS (bp)")
            ax.set_title("Promoter architecture of predicted TF binding sites")
            handles = [mpatches.Patch(color=color_map[motif], label=motif) for motif in top_motifs[:12]]
            ax.legend(handles=handles, title="Top motifs", bbox_to_anchor=(1.01, 1), loc="upper left", fontsize=7, title_fontsize=8)
            fig.tight_layout()
            save_figure(fig, "tfbs_promoter_architecture")
            plt.close(fig)

    if family_rows and top_summary:
        try:
            import networkx as nx

            top_motifs = [row["motif_id"] for row in top_summary[: min(20, len(top_summary))]]
            counts = Counter((str(row.get("motif_id") or ""), str(row.get("sequence_name") or "")) for row in family_rows if row.get("motif_id") in top_motifs)
            graph = nx.Graph()
            for motif in top_motifs:
                graph.add_node(motif, bipartite="motif")
            target_genes = sorted({gene for (_motif, gene), count in counts.items() if count > 0})
            for gene in target_genes:
                graph.add_node(gene, bipartite="gene")
            for (motif, gene), count in counts.items():
                if count > 0:
                    graph.add_edge(motif, gene, weight=count)
            if graph.number_of_edges() > 0:
                fig_h = max(7, min(18, 0.24 * max(len(top_motifs), len(target_genes)) + 5))
                fig, ax = plt.subplots(figsize=(13, fig_h))
                motif_y = {motif: idx for idx, motif in enumerate(reversed(top_motifs))}
                gene_y = {gene: idx for idx, gene in enumerate(reversed(target_genes))}
                pos = {motif: (0, y) for motif, y in motif_y.items()}
                pos.update({gene: (1, y) for gene, y in gene_y.items()})
                edge_widths = [0.4 + 0.25 * graph.edges[edge].get("weight", 1) for edge in graph.edges()]
                nx.draw_networkx_edges(graph, pos, ax=ax, width=edge_widths, alpha=0.35, edge_color="#666666")
                nx.draw_networkx_nodes(graph, pos, nodelist=top_motifs, node_color="#4C78A8", node_size=240, ax=ax)
                nx.draw_networkx_nodes(graph, pos, nodelist=target_genes, node_color="#F28E2B", node_size=240, ax=ax)
                for motif, y in motif_y.items():
                    ax.text(-0.03, y, motif, ha="right", va="center", fontsize=7)
                for gene, y in gene_y.items():
                    ax.text(1.03, y, gene, ha="left", va="center", fontsize=7)
                ax.set_xlim(-0.45, 1.45)
                ax.set_ylim(-1, max(len(top_motifs), len(target_genes)))
                ax.set_axis_off()
                ax.set_title("Predicted TF motif-gene bipartite network")
                fig.tight_layout()
                save_figure(fig, "tfbs_tf_gene_network")
                plt.close(fig)
        except Exception:
            plt.close("all")

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(["motif database"], [int(motif_source.get("motif_count") or 0)], color="#F28E2B")
    ax.set_ylabel("Motif count")
    title = str(motif_source.get("database") or "Motif database")
    selection = str(motif_source.get("selection") or "")
    ax.set_title(f"{title}\n{selection}")
    fig.tight_layout()
    save_figure(fig, "tfbs_motif_source_summary", legacy_stems=["tfbs.motif_source_summary"])
    plt.close(fig)

    return figures


def write_report(path: Path, prefix: str, args: argparse.Namespace, motif_source: dict[str, Any], metrics: dict[str, Any]) -> None:
    lines = [
        f"# {prefix} TF binding-site prediction report",
        "",
        "## Method",
        "",
        "- Promoters were extracted from genome FASTA and GFF3 annotation using the same promoter extraction logic as the bbav5 promoter stage.",
        "- TF binding sites were scanned with MEME Suite FIMO using a MEME-format plant TF motif database.",
        "- Motif source selection and thresholds are recorded in the manifest for reproducibility.",
        "",
        "## Parameters",
        "",
        f"- Upstream promoter length: {args.upstream_length} bp",
        f"- FIMO p-value threshold: {args.fimo_pvalue}",
        f"- FIMO q-value filter: {args.fimo_qvalue}",
        f"- Background mode: {args.background_mode}",
        f"- Scan both strands: {args.scan_both_strands}",
        "",
        "## Motif Source",
        "",
        f"- Database: {motif_source.get('database', '')}",
        f"- Selection: {motif_source.get('selection', '')}",
        f"- Selected species: {motif_source.get('species', '')}",
        f"- Motif count: {motif_source.get('motif_count', '')}",
        "",
        "## Summary",
        "",
        f"- Family promoter sequences: {metrics.get('family_promoter_count', 0)}",
        f"- Background promoter sequences: {metrics.get('background_promoter_count', 0)}",
        f"- Significant TFBS sites in family promoters: {metrics.get('family_site_count', 0)}",
        f"- Motifs detected in family promoters: {metrics.get('family_motif_count', 0)}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_manifest(
    args: argparse.Namespace,
    workspace: Path,
    prefix: str,
    scope_data: dict[str, Any],
    target_species_code: str,
    target_species_name: str,
    motif_source: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    figures: list[Path],
    metrics: dict[str, Any],
    command: list[str],
) -> dict[str, Any]:
    scope_id = str(scope_data.get("scope_id") or "")
    scope_mode = str(scope_data.get("scope_mode") or "")
    family = str(scope_data.get("family") or (prefix.split(".")[-1] if "." in prefix else prefix))
    analysis_route = args.analysis_route or ("single_species" if scope_id else "")
    member_set_id = args.member_set_id or (f"{scope_id}:{analysis_route}:all_members" if scope_id and analysis_route else "")
    stage_context = {
        "scope_id": scope_id,
        "scope_mode": scope_mode,
        "scope_file": str(args.scope_file or ""),
        "analysis_route": analysis_route,
        "member_set_id": member_set_id,
        "family": family,
        "target_species": [target_species_code] if target_species_code else [],
        "result_group": "tfbs",
    }
    return {
        "manifest_version": "1.0",
        "stage": "tfbs",
        "status": "success",
        "created_at": now_iso(),
        "prefix": prefix,
        "gene_family": family,
        "workspace": str(workspace.resolve()),
        "scope_id": scope_id,
        "scope_mode": scope_mode,
        "analysis_route": analysis_route,
        "member_set_id": member_set_id,
        "target_species": [target_species_code] if target_species_code else [],
        "target_species_name": target_species_name,
        "stage_context": stage_context,
        "parameters": {
            "upstream_length": args.upstream_length,
            "motif_db_mode": args.motif_db_mode,
            "fimo_pvalue": args.fimo_pvalue,
            "fimo_qvalue": args.fimo_qvalue,
            "background_mode": args.background_mode,
            "max_nearest_rank": args.max_nearest_rank,
            "top_motifs": args.top_motifs,
            "scan_both_strands": args.scan_both_strands,
            "max_stored_scores": args.max_stored_scores,
            "analysis_route": analysis_route,
            "member_set_id": member_set_id,
        },
        "methodology": {
            "primary_tools": ["MEME Suite FIMO"],
            "motif_source": motif_source,
            "official_sources": [
                "https://meme-suite.org/meme/doc/fimo.html",
                "https://jaspar.elixir.no/downloads/",
                "https://planttfdb.gao-lab.org/download.php",
                "https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz",
            ],
        },
        "command": command,
        "artifacts": artifacts,
        "figures": [artifact(path, workspace, "figure") for path in figures],
        "metrics": metrics,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Predict TF binding sites in family promoters with FIMO.")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--cpu", type=int, default=4)
    parser.add_argument("--scope-file", type=Path, default=None)
    parser.add_argument("--gff3", type=Path, default=None)
    parser.add_argument("--genome", type=Path, default=None)
    parser.add_argument("--gene-ids", type=Path, default=None)
    parser.add_argument("--target-species-name", default="")
    parser.add_argument("--upstream-length", type=int, default=2000)
    parser.add_argument("--motif-db-mode", choices=["auto", "planttfdb", "jaspar", "both"], default="auto")
    parser.add_argument("--planttfdb-root", type=Path, default=DEFAULT_PLANTTFDB_ROOT)
    parser.add_argument("--jaspar-meme", type=Path, default=DEFAULT_JASPAR_MEME)
    parser.add_argument("--jaspar-metadata", type=Path, default=DEFAULT_JASPAR_METADATA)
    parser.add_argument("--taxdump", type=Path, default=DEFAULT_TAXDUMP)
    parser.add_argument("--max-nearest-rank", default="order")
    parser.add_argument("--fimo-pvalue", type=float, default=1e-4)
    parser.add_argument("--fimo-qvalue", type=float, default=0.05)
    parser.add_argument("--background-mode", choices=["all_annotated_promoters", "family_promoters", "none"], default="none")
    parser.add_argument("--top-motifs", type=int, default=30)
    parser.add_argument("--scan-both-strands", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--max-stored-scores", type=int, default=1000000)
    parser.add_argument("--fimo-bin", type=Path, default=DEFAULT_FIMO)
    parser.add_argument("--analysis-route", default="")
    parser.add_argument("--member-set-id", default="")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = args.workspace.resolve()
    input_dir = args.input_dir.resolve()
    prefix = args.prefix.strip()
    final_dir = workspace / "final"
    analysis_dir = workspace / "tfbs" / "analysis"
    family_dir = analysis_dir / "family"
    background_dir = analysis_dir / "background"
    final_dir.mkdir(parents=True, exist_ok=True)
    analysis_dir.mkdir(parents=True, exist_ok=True)

    scope_file = resolve_existing_path(args.scope_file, input_dir, workspace) if args.scope_file else None
    scope_data, scope_gff, scope_genome, target_species_code = resolve_scope_inputs(scope_file, input_dir, workspace)
    gff3 = resolve_existing_path(args.gff3, input_dir, workspace) or scope_gff or find_gff3_file(input_dir, prefix)
    genome = resolve_existing_path(args.genome, input_dir, workspace) or scope_genome or find_genome_file(input_dir, prefix)
    gene_ids = resolve_existing_path(args.gene_ids, input_dir, workspace) or find_gene_id_file(input_dir, final_dir, prefix)
    if not gff3:
        raise RuntimeError("TFBS requires a target GFF3/GFF file.")
    if not genome:
        raise RuntimeError("TFBS requires a target genome FASTA file.")
    if not gene_ids:
        raise RuntimeError("TFBS requires family gene IDs, usually final/{prefix}.final_members.id.")

    if not target_species_code:
        target_species_code = prefix.split(".")[0] if "." in prefix else ""
    species_names = parse_config_species(input_dir)
    target_species_name = args.target_species_name.strip() or species_names.get(target_species_code, "")

    print(f"[TFBS] prefix={prefix}")
    print(f"[TFBS] gff3={gff3}")
    print(f"[TFBS] genome={genome}")
    print(f"[TFBS] gene_ids={gene_ids}")
    print(f"[TFBS] target_species={target_species_code} {target_species_name}")

    family_promoters = prepare_promoter_sequences(gff3, genome, gene_ids, family_dir, args.upstream_length)
    family_promoter_final = analysis_dir / f"{prefix}.family_promoters.fa"
    shutil.copy2(family_promoters, family_promoter_final)
    family_ids = fasta_ids(family_promoter_final)

    motif_file, motif_source, decisions = select_motif_database(args, target_species_name, analysis_dir)
    decision_file = final_dir / f"{prefix}.tfbs.motif_source_decision.tsv"
    decision_fields = sorted({key for item in decisions for key in item.keys() if key != "_score"})
    write_table(decision_file, decisions, decision_fields or ["database", "selection"])

    family_fimo_raw = run_fimo(args, motif_file, family_promoter_final, analysis_dir / "fimo_family")
    family_rows = parse_fimo(family_fimo_raw, args.fimo_pvalue, args.fimo_qvalue)

    background_rows: list[dict[str, Any]] = []
    background_ids: list[str] = []
    background_promoter_final = analysis_dir / f"{prefix}.background_promoters.fa"
    background_fimo_raw = None
    if args.background_mode == "all_annotated_promoters":
        all_promoters = prepare_promoter_sequences(gff3, genome, None, background_dir, args.upstream_length)
        wanted = set(family_ids) | {promoter_gene_like_id(x) for x in family_ids}
        kept_bg = write_fasta_subset(all_promoters, wanted, background_promoter_final)
        print(f"[TFBS] background promoters excluding family: {kept_bg}")
        background_ids = fasta_ids(background_promoter_final)
        if background_ids:
            background_fimo_raw = run_fimo(args, motif_file, background_promoter_final, analysis_dir / "fimo_background")
            background_rows = parse_fimo(background_fimo_raw, args.fimo_pvalue, args.fimo_qvalue)
    elif args.background_mode == "family_promoters":
        shutil.copy2(family_promoter_final, background_promoter_final)
        background_ids = list(family_ids)
        background_rows = list(family_rows)

    sites_file = final_dir / f"{prefix}.tfbs.sites.tsv"
    site_fields = ["motif_id", "motif_alt_id", "sequence_name", "start", "stop", "strand", "score", "p_value", "q_value", "matched_sequence"]
    write_table(sites_file, family_rows, site_fields)

    summary, enrichment, matrix = summarize_hits(family_rows, background_rows, family_ids, background_ids)
    motif_summary_file = final_dir / f"{prefix}.tfbs.motif_summary.tsv"
    enrichment_file = final_dir / f"{prefix}.tfbs.enrichment.tsv"
    matrix_file = final_dir / f"{prefix}.tfbs.gene_motif_matrix.tsv"
    write_table(motif_summary_file, summary, ["motif_id", "motif_alt_id", "family_sites", "family_genes_with_site", "background_sites", "background_genes_with_site", "min_p_value", "min_q_value", "mean_start"])
    write_table(enrichment_file, enrichment, ["motif_id", "family_gene_hits", "family_gene_total", "background_gene_hits", "background_gene_total", "odds_ratio", "p_value", "q_value", "family_ratio", "background_ratio"])
    matrix_fields = ["gene_id"] + [row["motif_id"] for row in sorted(summary, key=lambda x: int(x.get("family_sites") or 0), reverse=True)]
    write_table(matrix_file, matrix, matrix_fields)

    metrics = {
        "family_promoter_count": len(set(family_ids)),
        "background_promoter_count": len(set(background_ids)),
        "family_site_count": len(family_rows),
        "background_site_count": len(background_rows),
        "family_motif_count": len({row["motif_id"] for row in family_rows}),
        "motif_database": motif_source.get("database", ""),
        "motif_selection": motif_source.get("selection", ""),
        "selected_motif_species": motif_source.get("species", ""),
        "selected_motif_taxid": motif_source.get("selected_taxid", ""),
        "target_taxid": motif_source.get("target_taxid", ""),
        "lca_taxid": motif_source.get("lca_taxid", ""),
        "lca_rank": motif_source.get("lca_rank", ""),
        "taxonomic_distance": motif_source.get("taxonomic_distance", ""),
        "motif_count": motif_source.get("motif_count", 0),
    }
    figures = make_plots(analysis_dir, final_dir, prefix, summary, enrichment, matrix, family_rows, args.top_motifs, motif_source, args.upstream_length)
    report = final_dir / f"{prefix}.tfbs.report.md"
    write_report(report, prefix, args, motif_source, metrics)

    scope_id = str(scope_data.get("scope_id") or "")
    manifest_name = f"{prefix}.{sanitize_token(scope_id)}.tfbs.manifest.json" if scope_id else f"{prefix}.tfbs.manifest.json"
    manifest_path = final_dir / manifest_name
    artifacts = {
        "family_promoters": artifact(family_promoter_final, workspace, "fasta"),
        "selected_motifs": artifact(motif_file, workspace, "meme"),
        "motif_source_decision": artifact(decision_file, workspace, "table"),
        "fimo_family_raw": artifact(family_fimo_raw, workspace, "table"),
        "sites": artifact(sites_file, workspace, "table"),
        "motif_summary": artifact(motif_summary_file, workspace, "table"),
        "gene_motif_matrix": artifact(matrix_file, workspace, "table"),
        "enrichment": artifact(enrichment_file, workspace, "table"),
        "report": artifact(report, workspace, "markdown"),
    }
    if args.background_mode != "none" and background_promoter_final.exists():
        artifacts["background_promoters"] = artifact(background_promoter_final, workspace, "fasta")
    if background_fimo_raw and background_fimo_raw.exists():
        artifacts["fimo_background_raw"] = artifact(background_fimo_raw, workspace, "table")

    manifest = build_manifest(
        args=args,
        workspace=workspace,
        prefix=prefix,
        scope_data=scope_data,
        target_species_code=target_species_code,
        target_species_name=target_species_name,
        motif_source=motif_source,
        artifacts=artifacts,
        figures=figures,
        metrics=metrics,
        command=sys.argv,
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[TFBS] Manifest written: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
