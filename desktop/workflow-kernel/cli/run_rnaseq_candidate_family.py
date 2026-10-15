#!/usr/bin/env python3
"""RNA-seq candidate family prioritization.

This stage connects RNA-seq DE results with protein-domain annotation.  It is
not a replacement for the formal identify stage; it ranks candidate
families/domains that are enriched among DE genes so users can decide which
gene family to validate next.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).absolute().parents[2]
if str(PROJECT_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.rnaseq_context import (
    apply_rnaseq_context,
    build_experiment_context,
    find_upstream_rnaseq_context,
    write_experiment_context,
)


DEFAULT_INTERPROSCAN = (
    str(PROJECT_ROOT / "data" / "tools" / "interproscan5.77" / "interproscan-5.77-108.0" / "interproscan.sh")
)


TF_DOMAIN_RULES: list[tuple[str, set[str], list[str]]] = [
    ("SPL/SBP", {"PF03110"}, ["squamosa promoter binding", "sbp domain"]),
    ("MYB", {"PF00249", "PF08914", "PF13921"}, ["myb", "sant"]),
    ("WRKY", {"PF03106"}, ["wrky"]),
    ("bHLH", {"PF00010"}, ["helix-loop-helix", "bhlh"]),
    ("bZIP", {"PF00170", "PF07716", "PF03131"}, ["bzip", "basic-leucine zipper"]),
    ("NAC/NAM", {"PF02365"}, ["no apical meristem", "nam domain", "nac domain"]),
    ("AP2/ERF", {"PF00847"}, ["ap2 domain", "ap2/erf", "ethylene-responsive"]),
    ("MADS-box", {"PF00319", "PF01486"}, ["mads-box", "k-box"]),
    ("TCP", {"PF03634"}, ["tcp family"]),
    ("GRAS", {"PF03514"}, ["gras family"]),
    ("C2H2 zinc finger", {"PF00096", "PF13912"}, ["c2h2", "zinc finger c2h2"]),
    ("Homeobox", {"PF00046"}, ["homeobox", "homeodomain"]),
    ("GATA", {"PF00320"}, ["gata zinc finger"]),
    ("HSF", {"PF00447"}, ["heat shock factor"]),
    ("ARF", {"PF06507", "PF02362"}, ["auxin response factor", "b3 dna binding"]),
]

GENERAL_DOMAIN_RULES: list[tuple[str, set[str], list[str]]] = [
    ("Protein kinase/RLK", {"PF00069", "PF07714", "PF13855"}, ["protein kinase", "tyrosine kinase", "serine/threonine"]),
    ("LRR receptor/signaling", {"PF00560", "PF07723", "PF12799", "PF13306", "PF13516"}, ["leucine rich repeat", "lrr"]),
    ("NBS-LRR/NLR resistance", {"PF00931", "PF01582", "PF05659"}, ["nb-arc", "disease resistance", "nbs-lrr"]),
    ("F-box", {"PF00646"}, ["f-box"]),
    ("Ubiquitin system", {"PF00179", "PF00240", "PF00627", "PF02207"}, ["ubiquitin", "ubc", "ring finger", "ring-type"]),
    ("Cytochrome P450", {"PF00067"}, ["cytochrome p450"]),
    ("PPR/pentatricopeptide repeat", {"PF01535", "PF13041"}, ["pentatricopeptide", "ppr repeat"]),
    ("ABC transporter", {"PF00005"}, ["abc transporter"]),
    ("Sugar/transporter", {"PF00083", "PF07690"}, ["sugar transporter", "major facilitator", "mfs"]),
    ("AAA ATPase", {"PF00004", "PF07728", "PF14363"}, ["aaa", "atpase"]),
]

LOW_INFORMATION_SIGNATURES = {"PS51257"}
LOW_INFORMATION_KEYWORDS = ["prokaryotic membrane lipoprotein lipid attachment"]

SPECIES_RESOURCE_ALIASES = {
    "os": ["Os", "Oryza", "Oryza_sativa", "rice"],
    "ft": ["Ft", "Fagopyrum", "Fagopyrum_tataricum"],
    "at": ["At", "Arabidopsis", "Arabidopsis_thaliana"],
}


@dataclass
class DegRecord:
    gene_id: str
    comparison: str
    log2fc: float | None = None
    padj: float | None = None
    regulation: str = ""


@dataclass
class Hit:
    protein_id: str
    gene_id: str
    analysis: str
    signature: str
    signature_desc: str
    interpro: str
    interpro_desc: str
    go_terms: str = ""
    pathways: str = ""
    score: str = ""
    start: str = ""
    stop: str = ""


@dataclass
class FamilyEvidence:
    family: str
    genes: set[str] = field(default_factory=set)
    proteins: set[str] = field(default_factory=set)
    signatures: set[str] = field(default_factory=set)
    interpros: set[str] = field(default_factory=set)
    descriptions: set[str] = field(default_factory=set)
    comparisons: set[str] = field(default_factory=set)
    up: int = 0
    down: int = 0
    abs_log2fc_sum: float = 0.0
    best_padj: float | None = None
    enrichment_terms: set[str] = field(default_factory=set)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def abs_text(path: str | Path) -> str:
    return str(Path(path).absolute())


def artifact(workspace: Path, path: Path, name: str, typ: str) -> dict[str, Any]:
    path = path.absolute()
    workspace = workspace.absolute()
    try:
        rel = str(path.relative_to(workspace))
    except ValueError:
        rel = str(path)
    return {
        "artifact": name,
        "path": rel,
        "full_path": abs_text(path),
        "type": typ,
        "size": path.stat().st_size if path.exists() else 0,
        "sha256": sha256(path),
    }


def localize_external_resource(path: Path | None, workspace: Path, analysis_dir: Path, filename: str) -> tuple[Path | None, str]:
    """Expose an external read-only resource through the current job workspace."""
    if not path:
        return None, ""
    path = path.absolute()
    try:
        path.relative_to(workspace.absolute())
        return path, ""
    except ValueError:
        pass
    analysis_dir.mkdir(parents=True, exist_ok=True)
    link_path = analysis_dir / filename
    if link_path.exists() or link_path.is_symlink():
        try:
            if link_path.resolve() == path.resolve():
                return link_path, abs_text(path)
        except OSError:
            pass
        link_path.unlink()
    os.symlink(path, link_path)
    return link_path, abs_text(path)


def canonical_gene_id(value: str) -> str:
    value = (value or "").strip().lstrip(">")
    value = value.split()[0] if value else ""
    value = value.split("|", 1)[0]
    value = re.sub(r"\.mRNA\d+$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\.T\d+$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\.\d+$", "", value)
    value = re.sub(r"-\d+$", "", value)
    rap_match = re.match(r"^(Os\d{2})t(\d+)$", value, flags=re.IGNORECASE)
    if rap_match:
        value = f"{rap_match.group(1)}g{rap_match.group(2)}"
    return value


def id_variants(value: str) -> set[str]:
    raw = (value or "").strip().lstrip(">")
    first = raw.split()[0] if raw else ""
    variants = {raw, first, canonical_gene_id(first)}
    if "|" in first:
        variants.add(first.split("|", 1)[0])
    variants.update({v for v in [re.sub(r"\.\d+$", "", first), re.sub(r"-\d+$", "", first)] if v})
    return {v for v in variants if v}


def build_variant_map(ids: set[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for gene in ids:
        for variant in id_variants(gene):
            mapping.setdefault(variant, gene)
    return mapping


def read_de_files(final_dir: Path, prefix: str, explicit: str = "") -> tuple[list[DegRecord], list[Path]]:
    files: list[Path] = []
    if explicit:
        files = [Path(p).absolute() for p in explicit.split(",") if p.strip()]
    else:
        patterns = [
            f"{prefix}.rnaseq_de.*.DE_genes.txt" if prefix else "*.rnaseq_de.*.DE_genes.txt",
            "*.DE_genes.txt",
        ]
        for pattern in patterns:
            files.extend(sorted(final_dir.glob(pattern)))
    seen = set()
    unique_files = []
    for path in files:
        if path.exists() and path not in seen and "family_DE_genes" not in path.name:
            seen.add(path)
            unique_files.append(path)
    records: list[DegRecord] = []
    for path in unique_files:
        comparison = path.name
        comparison = comparison.replace(f"{prefix}.rnaseq_de.", "") if prefix else comparison
        comparison = comparison.replace(".DE_genes.txt", "")
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if not reader.fieldnames:
                continue
            gene_col = "gene_id" if "gene_id" in reader.fieldnames else reader.fieldnames[0]
            for row in reader:
                gene = (row.get(gene_col) or "").strip()
                if not gene or gene.lower() in {"gene", "gene_id"}:
                    continue
                records.append(
                    DegRecord(
                        gene_id=canonical_gene_id(gene),
                        comparison=comparison,
                        log2fc=parse_float(row.get("log2FoldChange")),
                        padj=parse_float(row.get("padj")),
                        regulation=(row.get("regulation") or "").strip(),
                    )
                )
    return records, unique_files


def parse_float(value: Any) -> float | None:
    try:
        if value is None or value == "" or str(value).upper() == "NA":
            return None
        return float(value)
    except Exception:
        return None


def fasta_iter(path: Path):
    header = None
    seq: list[str] = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            if line.startswith(">"):
                if header is not None:
                    yield header, "".join(seq)
                header = line[1:].strip()
                seq = []
            else:
                seq.append(line.strip())
        if header is not None:
            yield header, "".join(seq)


def prefix_species_family(prefix: str) -> tuple[str, str]:
    parts = [part for part in str(prefix or "").split(".") if part]
    species = parts[0] if parts else ""
    family = parts[1] if len(parts) > 1 else ""
    return species, family


def species_aliases(species: str) -> list[str]:
    species = str(species or "").strip()
    aliases = [species] if species else []
    aliases.extend(SPECIES_RESOURCE_ALIASES.get(species.lower(), []))
    out: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        key = alias.lower()
        if alias and key not in seen:
            seen.add(key)
            out.append(alias)
    return out


def is_family_subset_protein(path: Path, family: str = "") -> bool:
    name = path.name.lower()
    lowered_family = family.lower()
    if "final_members" in name or "curated_members" in name or "candidate_members" in name:
        return True
    if name.startswith("ref_combined"):
        return True
    if lowered_family and f".{lowered_family}." in name:
        return True
    return False


def is_species_resource(path: Path, aliases: list[str]) -> bool:
    if not aliases:
        return True
    name = path.name.lower()
    return any(alias.lower() in name for alias in aliases)


def shared_resource_roots(input_dir: Path, workspace: Path) -> list[Path]:
    roots = [
        input_dir,
        workspace / "annotation" / "analysis",
        PROJECT_ROOT / "input",
        PROJECT_ROOT / "data",
        PROJECT_ROOT / "resources",
        PROJECT_ROOT / "reference",
        PROJECT_ROOT / "references",
    ]
    roots.extend(historical_annotation_roots(input_dir))
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        root = root.absolute()
        if root.exists() and root not in seen:
            seen.add(root)
            out.append(root)
    return out


def historical_annotation_roots(input_dir: Path) -> list[Path]:
    """Discover same-machine legacy workspace annotation dirs without hard-coding one job."""
    bases: list[Path] = []
    for root in (PROJECT_ROOT, input_dir):
        try:
            bases.append(root.absolute().parent)
            bases.append(root.resolve().parent)
        except OSError:
            bases.append(root.absolute().parent)
    try:
        bases.append((PROJECT_ROOT / "input").resolve().parent.parent)
    except OSError:
        pass

    roots: list[Path] = []
    seen_bases: set[Path] = set()
    for base in bases:
        if not base.exists() or base in seen_bases:
            continue
        seen_bases.add(base)
        for project_dir in sorted(base.glob("*.bak")) + sorted(base.glob("*backup*")):
            workspace_root = project_dir / "workspace"
            if not workspace_root.exists():
                continue
            roots.extend(sorted(workspace_root.glob("job_*/annotation/analysis")))
    job_id = input_dir.name
    return sorted(
        [root.absolute() for root in roots if root.exists()],
        key=lambda root: (0 if job_id and job_id in root.parts else 1, str(root)),
    )


def resource_diagnostics(input_dir: Path, workspace: Path, prefix: str) -> dict[str, Any]:
    species, family = prefix_species_family(prefix)
    aliases = species_aliases(species)
    roots = shared_resource_roots(input_dir, workspace)
    expected_roots = [
        input_dir.absolute(),
        (workspace / "annotation" / "analysis").absolute(),
        PROJECT_ROOT / "input",
        PROJECT_ROOT / "data",
        PROJECT_ROOT / "resources",
        PROJECT_ROOT / "reference",
        PROJECT_ROOT / "references",
    ]
    history_roots = historical_annotation_roots(input_dir)
    return {
        "species": species,
        "family": family,
        "accepted_species_aliases": aliases,
        "satisfied": False,
        "requested_input_dir": abs_text(input_dir),
        "checked_roots": [abs_text(root) for root in roots],
        "historical_annotation_roots": [abs_text(root) for root in history_roots],
        "missing_expected_roots": [abs_text(root) for root in expected_roots if not root.exists()],
        "rejected_inputs": [
            "family-member FASTA/TSV such as *.final_members.*, *.curated_members.*, *.candidate_members.*",
            "reference-combined family FASTA such as Ref_combined*.pep.fasta",
            "resources whose filename does not match the requested species aliases",
        ],
        "required_one_of": [
            f"whole target proteome FASTA named like {species}.pep.fasta or {species}.faa",
            f"genome-wide InterProScan TSV named like {species}.interproscan.tsv",
        ],
        "next_action": (
            f"Place a genome-wide {species or 'target'} proteome FASTA or InterProScan TSV under "
            f"{abs_text(input_dir)} or a project shared resource directory, then rerun rnaseq_candidate_family."
        ),
    }


def find_protein_fasta(input_dir: Path, final_dir: Path, explicit: str = "", prefix: str = "") -> Path | None:
    if explicit:
        path = Path(explicit).absolute()
        return path if path.exists() else None
    species, family = prefix_species_family(prefix)
    patterns = [
        "*.pep.fasta", "*.pep.fa", "*.protein.fasta", "*.protein.fa", "*.faa",
        "*protein*.fasta", "*protein*.fa", "*annotation_safe.pep.fasta",
    ]
    candidates: list[Path] = []
    preferred: list[Path] = []
    if species:
        preferred.extend(
            [
                input_dir / f"{species}.pep.fasta",
                input_dir / f"{species}.pep.fa",
                input_dir / species / f"{species}.pep.fasta",
                input_dir / species / f"{species}.pep.fa",
                input_dir / f"{species}.protein.fasta",
                input_dir / f"{species}.protein.fa",
                input_dir / species / f"{species}.protein.fasta",
                input_dir / species / f"{species}.protein.fa",
                input_dir / f"{species}.faa",
                input_dir / species / f"{species}.faa",
            ]
        )
    aliases = species_aliases(species)
    roots = [input_dir, final_dir, final_dir.parent / "annotation" / "analysis"]
    roots.extend(shared_resource_roots(input_dir, final_dir.parent))
    for child in sorted(input_dir.iterdir()) if input_dir.exists() else []:
        if child.is_dir():
            roots.append(child)
    deduped_roots: list[Path] = []
    seen_roots: set[Path] = set()
    for root in roots:
        root = root.absolute()
        if root.exists() and root not in seen_roots:
            seen_roots.add(root)
            deduped_roots.append(root)
    roots = deduped_roots
    shared_roots = set(shared_resource_roots(input_dir, final_dir.parent))
    local_roots = {input_dir.absolute(), final_dir.absolute(), (final_dir.parent / "annotation" / "analysis").absolute()}
    for root in roots:
        globber = root.rglob if root in shared_roots and root not in local_roots else root.glob
        for pattern in patterns:
            candidates.extend(sorted(globber(pattern)))
    candidates = [
        p
        for p in [*preferred, *candidates]
        if p.exists()
        and p.stat().st_size > 0
        and not is_family_subset_protein(p, family)
        and is_species_resource(p, aliases)
    ]
    return candidates[0] if candidates else None


def is_family_member_interproscan(path: Path) -> bool:
    name = path.name.lower()
    return any(
        marker in name
        for marker in (
            "final_members.interproscan",
            "curated_members.interproscan",
            "candidate_members.interproscan",
        )
    )


def write_deg_protein_subset(protein_fasta: Path | None, deg_genes: set[str], output: Path, max_records: int) -> tuple[int, int]:
    if not protein_fasta:
        return 0, 0
    deg_variant_map = build_variant_map(deg_genes)
    output.parent.mkdir(parents=True, exist_ok=True)
    matched = 0
    scanned = 0
    seen_genes: set[str] = set()
    with output.open("w", encoding="utf-8") as out:
        for header, seq in fasta_iter(protein_fasta):
            scanned += 1
            variants = id_variants(header)
            match = next((deg_variant_map[v] for v in variants if v in deg_variant_map), "")
            if match and match not in seen_genes:
                seen_genes.add(match)
                clean_seq = seq.replace("*", "")
                out.write(f">{header}\n")
                for i in range(0, len(clean_seq), 60):
                    out.write(clean_seq[i:i + 60] + "\n")
                matched += 1
                if matched >= max_records:
                    break
    if matched == 0 and output.exists():
        output.unlink()
    return matched, scanned


def find_interproscan_tsv(workspace: Path, final_dir: Path, prefix: str, explicit: str = "") -> Path | None:
    if explicit:
        path = Path(explicit).absolute()
        return path if path.exists() else None
    candidates = [
        workspace / "annotation" / "analysis" / f"{prefix}.interproscan.tsv",
        workspace / "rnaseq_candidate_family" / "analysis" / f"{prefix}.rnaseq_candidate_family.interproscan.tsv",
    ]
    species, _family = prefix_species_family(prefix)
    aliases = species_aliases(species)
    candidates.extend(sorted((workspace / "annotation" / "analysis").glob("*interproscan.tsv")))
    candidates.extend(sorted(final_dir.glob("*interproscan*.tsv")))
    for root in shared_resource_roots(final_dir.parent.parent / "input" / workspace.name, workspace):
        if root in {workspace.absolute(), final_dir.absolute()}:
            continue
        candidates.extend(sorted(root.rglob("*interproscan*.tsv")))
    candidates = [
        p
        for p in candidates
        if p.exists()
        and p.stat().st_size > 0
        and not is_family_member_interproscan(p)
        and is_species_resource(p, aliases)
    ]
    return candidates[0] if candidates else None


def run_interproscan(input_fasta: Path, output_tsv: Path, interproscan: Path, cpu: int, applications: str) -> dict[str, Any]:
    result = {"status": "skipped", "cmd": "", "returncode": None, "stderr": ""}
    if not input_fasta.exists():
        result["stderr"] = f"DEG protein FASTA not found: {input_fasta}"
        return result
    if not interproscan.exists():
        result["stderr"] = f"interproscan.sh not found: {interproscan}"
        return result
    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    output_base = output_tsv.with_suffix("")
    cmd = [
        str(interproscan),
        "-i", str(input_fasta),
        "-f", "TSV",
        "-o", str(output_tsv),
        "-cpu", str(cpu),
    ]
    if applications.strip():
        cmd.extend(["-appl", applications.strip()])
    tmp_dir = output_tsv.parent / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    cmd.extend(["-T", str(tmp_dir)])
    start = time.time()
    conda_sh = Path(os.environ.get("BBA_CONDA_SH", ""))
    conda_env = os.environ.get("BBA_INTERPROSCAN_ENV", "interproscanpy311")
    if conda_sh.exists() and conda_env:
        shell_cmd = (
            f"source {shlex.quote(str(conda_sh))} && "
            f"conda activate {shlex.quote(conda_env)} && "
            + " ".join(shlex.quote(x) for x in cmd)
            + " && conda deactivate"
        )
        proc = subprocess.run(["bash", "-lc", shell_cmd], capture_output=True, text=True)
        cmd_text = shell_cmd
    else:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        cmd_text = " ".join(shlex.quote(x) for x in cmd)
    result.update({
        "status": "success" if proc.returncode == 0 and output_tsv.exists() else "failed",
        "cmd": cmd_text,
        "conda_env": conda_env if conda_sh.exists() else "",
        "returncode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
        "elapsed_sec": round(time.time() - start, 2),
        "output_base": str(output_base),
    })
    return result


def parse_interproscan_tsv(path: Path | None, deg_genes: set[str]) -> list[Hit]:
    if not path or not path.exists():
        return []
    deg_variant_map = build_variant_map(deg_genes)
    hits: list[Hit] = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            if not raw.strip() or raw.startswith("#"):
                continue
            cols = raw.rstrip("\n").split("\t")
            if len(cols) < 6:
                continue
            protein = cols[0].strip()
            gene = canonical_gene_id(protein)
            if deg_genes and gene not in deg_genes:
                variants = id_variants(protein)
                mapped_gene = next((deg_variant_map[v] for v in variants if v in deg_variant_map), "")
                if not mapped_gene:
                    continue
                gene = mapped_gene
            hits.append(
                Hit(
                    protein_id=protein,
                    gene_id=gene,
                    analysis=cols[3] if len(cols) > 3 else "",
                    signature=cols[4] if len(cols) > 4 else "",
                    signature_desc=cols[5] if len(cols) > 5 else "",
                    start=cols[6] if len(cols) > 6 else "",
                    stop=cols[7] if len(cols) > 7 else "",
                    score=cols[8] if len(cols) > 8 else "",
                    interpro=cols[11] if len(cols) > 11 else "",
                    interpro_desc=cols[12] if len(cols) > 12 else "",
                    go_terms=cols[13] if len(cols) > 13 else "",
                    pathways=cols[14] if len(cols) > 14 else "",
                )
            )
    return hits


def classify_hit(hit: Hit) -> str:
    signature = hit.signature.strip()
    text = f"{hit.signature_desc} {hit.interpro_desc}".lower()
    if signature in LOW_INFORMATION_SIGNATURES or any(keyword in text for keyword in LOW_INFORMATION_KEYWORDS):
        return ""
    for label, pfams, keywords in TF_DOMAIN_RULES + GENERAL_DOMAIN_RULES:
        if signature in pfams or any(keyword in text for keyword in keywords):
            return label
    if signature:
        desc = (hit.signature_desc or hit.interpro_desc or "unclassified domain").strip()
        desc = re.sub(r"\s+", " ", desc)
        return f"Pfam/InterPro:{signature} {desc[:60]}"
    if hit.interpro:
        return f"InterPro:{hit.interpro} {(hit.interpro_desc or 'unclassified')[:60]}"
    return "Unclassified annotated protein"


def read_enrichment_terms(final_dir: Path, prefix: str) -> list[dict[str, Any]]:
    files = sorted(final_dir.glob(f"{prefix}.rnaseq_go_enrich.*.GO_enrich.txt")) + sorted(final_dir.glob(f"{prefix}.rnaseq_kegg_enrich.*.KEGG_enrich.txt"))
    if not files:
        files = sorted(final_dir.glob("*GO_enrich.txt")) + sorted(final_dir.glob("*KEGG_enrich.txt"))
    terms: list[dict[str, Any]] = []
    for path in files:
        kind = "KEGG" if "KEGG" in path.name else "GO"
        comparison = path.name
        comparison = comparison.replace(f"{prefix}.rnaseq_go_enrich.", "").replace(f"{prefix}.rnaseq_kegg_enrich.", "")
        comparison = comparison.replace(".GO_enrich.txt", "").replace(".KEGG_enrich.txt", "")
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if not reader.fieldnames:
                continue
            for row in reader:
                genes = set()
                for item in (row.get("geneID") or "").replace(",", "/").split("/"):
                    item = canonical_gene_id(item)
                    if item:
                        genes.add(item)
                terms.append({
                    "kind": kind,
                    "comparison": comparison,
                    "id": row.get("ID", ""),
                    "description": row.get("Description", ""),
                    "padj": parse_float(row.get("p.adjust") or row.get("padj")),
                    "genes": genes,
                    "file": path.name,
                })
    terms.sort(key=lambda x: x["padj"] if x["padj"] is not None else 1.0)
    return terms


def build_evidence(records: list[DegRecord], hits: list[Hit], enrichment_terms: list[dict[str, Any]]) -> tuple[list[FamilyEvidence], list[dict[str, Any]]]:
    deg_by_gene: dict[str, list[DegRecord]] = defaultdict(list)
    for rec in records:
        deg_by_gene[rec.gene_id].append(rec)
    families: dict[str, FamilyEvidence] = {}
    gene_rows: list[dict[str, Any]] = []
    hits_by_gene: dict[str, list[Hit]] = defaultdict(list)
    for hit in hits:
        hits_by_gene[hit.gene_id].append(hit)

    for gene, gene_hits in hits_by_gene.items():
        recs = deg_by_gene.get(gene, [])
        hit_families = []
        for hit in gene_hits:
            label = classify_hit(hit)
            if not label:
                continue
            hit_families.append(label)
            ev = families.setdefault(label, FamilyEvidence(label))
            ev.genes.add(gene)
            ev.proteins.add(hit.protein_id)
            if hit.signature:
                ev.signatures.add(hit.signature)
            if hit.interpro:
                ev.interpros.add(hit.interpro)
            if hit.signature_desc:
                ev.descriptions.add(hit.signature_desc)
            if hit.interpro_desc:
                ev.descriptions.add(hit.interpro_desc)
            for rec in recs:
                ev.comparisons.add(rec.comparison)
                if rec.regulation.lower() == "up":
                    ev.up += 1
                elif rec.regulation.lower() == "down":
                    ev.down += 1
                if rec.log2fc is not None:
                    ev.abs_log2fc_sum += abs(rec.log2fc)
                if rec.padj is not None:
                    ev.best_padj = rec.padj if ev.best_padj is None else min(ev.best_padj, rec.padj)
        gene_rows.append({
            "gene_id": gene,
            "comparisons": ";".join(sorted({r.comparison for r in recs})),
            "regulation": ";".join(sorted({r.regulation for r in recs if r.regulation})),
            "best_padj": min([r.padj for r in recs if r.padj is not None], default=""),
            "max_abs_log2fc": max([abs(r.log2fc) for r in recs if r.log2fc is not None], default=""),
            "candidate_families": ";".join(sorted(set(hit_families))),
            "signatures": ";".join(sorted({h.signature for h in gene_hits if h.signature})),
            "interpro_ids": ";".join(sorted({h.interpro for h in gene_hits if h.interpro})),
            "domain_descriptions": ";".join(sorted({h.signature_desc or h.interpro_desc for h in gene_hits if h.signature_desc or h.interpro_desc})),
        })

    for ev in families.values():
        for term in enrichment_terms:
            overlap = ev.genes & term["genes"]
            if overlap:
                ev.enrichment_terms.add(f"{term['kind']}:{term['id']}:{term['description']}")
    ranked = sorted(
        families.values(),
        key=lambda ev: (
            len(ev.genes),
            len(ev.enrichment_terms),
            ev.abs_log2fc_sum,
            -(ev.best_padj or 1.0),
        ),
        reverse=True,
    )
    return ranked, sorted(gene_rows, key=lambda r: (str(r["best_padj"]) == "", r["best_padj"]))


BROAD_REFINEMENT_FAMILIES = {
    "Protein kinase/RLK",
    "LRR receptor/signaling",
    "Ubiquitin system",
    "PPR/pentatricopeptide repeat",
    "NBS-LRR/NLR resistance",
    "AAA ATPase",
}


def first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def bh_adjust(pvals: list[float | None]) -> list[float | None]:
    indexed = [(idx, p) for idx, p in enumerate(pvals) if p is not None]
    if not indexed:
        return [None for _ in pvals]
    indexed.sort(key=lambda item: item[1])
    m = len(indexed)
    adjusted = [None for _ in pvals]
    running = 1.0
    for rank, (idx, p) in enumerate(reversed(indexed), start=1):
        true_rank = m - rank + 1
        running = min(running, p * m / true_rank)
        adjusted[idx] = min(running, 1.0)
    return adjusted


def hypergeom_sf(observed: int, family_size: int, draws: int, universe: int) -> float | None:
    if observed <= 0 or family_size <= 0 or draws <= 0 or universe <= 0:
        return None
    if observed > family_size or observed > draws or family_size > universe or draws > universe:
        return None
    max_k = min(family_size, draws)
    denom = math.lgamma(universe + 1) - math.lgamma(draws + 1) - math.lgamma(universe - draws + 1)
    total = 0.0
    for k in range(observed, max_k + 1):
        logp = (
            math.lgamma(family_size + 1) - math.lgamma(k + 1) - math.lgamma(family_size - k + 1)
            + math.lgamma(universe - family_size + 1) - math.lgamma(draws - k + 1) - math.lgamma(universe - family_size - draws + k + 1)
            - denom
        )
        total += math.exp(logp)
    return min(total, 1.0)


def build_family_background(hits: list[Hit]) -> tuple[dict[str, set[str]], set[str]]:
    family_genes: dict[str, set[str]] = defaultdict(set)
    background_genes: set[str] = set()
    for hit in hits:
        label = classify_hit(hit)
        if not label:
            continue
        background_genes.add(hit.gene_id)
        family_genes[label].add(hit.gene_id)
    return family_genes, background_genes


def comparison_from_fgsea_name(path: Path, prefix: str) -> str:
    name = path.name
    leading = f"{prefix}.rnaseq_fgsea."
    if name.startswith(leading):
        name = name[len(leading):]
    return name.replace(".fgsea_kegg.tsv", "")


def read_fgsea_support(final_dir: Path, prefix: str, family_genes: dict[str, set[str]], padj_cutoff: float = 0.05) -> dict[str, dict[str, Any]]:
    files = sorted(final_dir.glob(f"{prefix}.rnaseq_fgsea.*.fgsea_kegg.tsv"))
    support: dict[str, dict[str, Any]] = {family: {"pathways": [], "genes": set()} for family in family_genes}
    for path in files:
        comparison = comparison_from_fgsea_name(path, prefix)
        try:
            handle = path.open("r", encoding="utf-8", errors="ignore")
        except Exception:
            continue
        with handle:
            reader = csv.DictReader(handle, delimiter="\t")
            for row in reader:
                padj = parse_float(row.get("padj"))
                if padj is None or padj > padj_cutoff:
                    continue
                leading = {canonical_gene_id(x) for x in (row.get("leadingEdge") or "").replace(",", ";").split(";") if x.strip()}
                if not leading:
                    continue
                desc = row.get("Description") or row.get("pathway") or "fgsea pathway"
                nes = row.get("NES") or ""
                for family, genes in family_genes.items():
                    overlap = genes & leading
                    if not overlap:
                        continue
                    item = support.setdefault(family, {"pathways": [], "genes": set()})
                    item["genes"].update(overlap)
                    item["pathways"].append({
                        "comparison": comparison,
                        "pathway": row.get("pathway", ""),
                        "description": desc,
                        "padj": padj,
                        "NES": nes,
                        "overlap": len(overlap),
                    })
    for item in support.values():
        item["pathways"].sort(key=lambda x: (x.get("padj") if x.get("padj") is not None else 1.0, -int(x.get("overlap") or 0)))
    return support


def read_wgcna_support(final_dir: Path, prefix: str, family_genes: dict[str, set[str]]) -> dict[str, dict[str, Any]]:
    gene_modules_path = final_dir / f"{prefix}.rnaseq_wgcna.gene_modules.tsv"
    cor_path = final_dir / f"{prefix}.rnaseq_wgcna.module_trait_correlation.tsv"
    pval_path = final_dir / f"{prefix}.rnaseq_wgcna.module_trait_pvalue.tsv"
    support: dict[str, dict[str, Any]] = {family: {"genes": set(), "modules": []} for family in family_genes}
    if not gene_modules_path.exists():
        return support
    gene_to_module: dict[str, str] = {}
    with gene_modules_path.open("r", encoding="utf-8", errors="ignore") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            gene = canonical_gene_id(row.get("gene_id") or row.get("gene") or "")
            module = (row.get("module") or "").strip()
            if gene and module and module != "grey":
                gene_to_module[gene] = module
    module_traits: dict[str, dict[str, Any]] = {}
    if cor_path.exists() and pval_path.exists():
        cor_rows: dict[str, dict[str, str]] = {}
        with cor_path.open("r", encoding="utf-8", errors="ignore") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            first = reader.fieldnames[0] if reader.fieldnames else ""
            for row in reader:
                module = (row.get(first) or row.get("") or "").replace("ME", "", 1)
                cor_rows[module] = row
        with pval_path.open("r", encoding="utf-8", errors="ignore") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            first = reader.fieldnames[0] if reader.fieldnames else ""
            for row in reader:
                module = (row.get(first) or row.get("") or "").replace("ME", "", 1)
                best = None
                for trait, value in row.items():
                    if trait == first or trait == "":
                        continue
                    pval = parse_float(value)
                    if pval is None or pval > 0.05:
                        continue
                    cor = parse_float(cor_rows.get(module, {}).get(trait))
                    score = abs(cor) if cor is not None else 0.0
                    if best is None or score > best["score"]:
                        best = {"trait": trait, "pvalue": pval, "correlation": cor, "score": score}
                if best:
                    module_traits[module] = best
    significant_modules = set(module_traits)
    for family, genes in family_genes.items():
        module_counts: dict[str, int] = defaultdict(int)
        supported_genes = set()
        for gene in genes:
            module = gene_to_module.get(gene)
            if not module:
                continue
            if significant_modules and module not in significant_modules:
                continue
            module_counts[module] += 1
            supported_genes.add(gene)
        modules = []
        for module, count in sorted(module_counts.items(), key=lambda item: item[1], reverse=True)[:5]:
            trait = module_traits.get(module, {})
            modules.append({
                "module": module,
                "genes": count,
                "trait": trait.get("trait", ""),
                "correlation": trait.get("correlation", ""),
                "pvalue": trait.get("pvalue", ""),
            })
        support[family] = {"genes": supported_genes, "modules": modules}
    return support


def make_family_enrichment_rows(ranked: list[FamilyEvidence], background: dict[str, set[str]], background_genes: set[str], annotated_deg_genes: set[str]) -> list[dict[str, Any]]:
    universe = len(background_genes)
    draws = len(annotated_deg_genes & background_genes) if background_genes else 0
    use_background = universe > draws > 0
    rows: list[dict[str, Any]] = []
    pvals: list[float | None] = []
    for ev in ranked:
        family_bg = background.get(ev.family, set())
        family_size = len(family_bg) if family_bg else len(ev.genes)
        observed = len(ev.genes)
        pval = hypergeom_sf(observed, family_size, draws, universe) if use_background and family_size >= observed else None
        pvals.append(pval)
        rows.append({
            "candidate_family": ev.family,
            "deg_gene_count": observed,
            "background_gene_count": family_size,
            "deg_ratio": round(observed / family_size, 6) if family_size else "",
            "universe_genes": universe if use_background else "",
            "draws_deg_genes": draws if use_background else "",
            "p_value": pval if pval is not None else "",
            "background_mode": "whole_annotation" if use_background else "deg_only_or_unavailable",
        })
    adjusted = bh_adjust(pvals)
    for row, padj in zip(rows, adjusted):
        row["p_adjust"] = padj if padj is not None else ""
    return rows


def tier_for_family(ev: FamilyEvidence, enrich: dict[str, Any], fgsea: dict[str, Any], wgcna: dict[str, Any]) -> str:
    deg_count = len(ev.genes)
    padj = parse_float(enrich.get("p_adjust"))
    fgsea_count = len(fgsea.get("pathways") or [])
    wgcna_gene_count = len(wgcna.get("genes") or [])
    go_kegg_count = len(ev.enrichment_terms)
    if ev.family in BROAD_REFINEMENT_FAMILIES and deg_count >= 100:
        return "high_response_needs_refinement"
    primary_support = (padj is not None and padj <= 0.05) or fgsea_count > 0
    secondary_support = wgcna_gene_count >= 5 and go_kegg_count >= 3
    if deg_count >= 20 and (primary_support or secondary_support):
        return "priority_recommended"
    if deg_count < 20 and ((padj is not None and padj <= 0.05) or fgsea_count > 0 or (wgcna_gene_count >= 3 and go_kegg_count >= 2)):
        return "mechanism_relevant_limited_size"
    if deg_count >= 50:
        return "context_neutral_candidate"
    return "candidate_pool"


def evidence_summary(ev: FamilyEvidence, enrich: dict[str, Any], fgsea: dict[str, Any], wgcna: dict[str, Any]) -> str:
    parts = [f"DEG={len(ev.genes)}", f"comparisons={len(ev.comparisons)}"]
    padj = enrich.get("p_adjust")
    if padj not in (None, ""):
        parts.append(f"family_FDR={padj}")
    pathways = fgsea.get("pathways") or []
    if pathways:
        top = pathways[0]
        parts.append(f"fgsea={top.get('description')} ({top.get('comparison')})")
    modules = wgcna.get("modules") or []
    if modules:
        top = modules[0]
        parts.append(f"WGCNA={top.get('module')}:{top.get('genes')} genes")
    if ev.enrichment_terms:
        parts.append(f"GO/KEGG_terms={len(ev.enrichment_terms)}")
    return "; ".join(parts)


def write_candidate_extension_tables(
    prefix: str,
    final_dir: Path,
    ranked: list[FamilyEvidence],
    gene_rows: list[dict[str, Any]],
    family_enrichment_rows: list[dict[str, Any]],
    fgsea_support: dict[str, dict[str, Any]],
    wgcna_support: dict[str, dict[str, Any]],
    experiment_context: dict[str, Any],
) -> tuple[Path, Path, Path, Path]:
    enrichment_by_family = {row["candidate_family"]: row for row in family_enrichment_rows}
    family_enrich_path = final_dir / f"{prefix}.rnaseq_candidate_family.family_enrichment.tsv"
    evidence_path = final_dir / f"{prefix}.rnaseq_candidate_family.evidence_matrix.tsv"
    recommendation_path = final_dir / f"{prefix}.rnaseq_candidate_family.recommendation.tsv"
    key_genes_path = final_dir / f"{prefix}.rnaseq_candidate_family.key_genes.tsv"

    with family_enrich_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = ["candidate_family", "deg_gene_count", "background_gene_count", "deg_ratio", "universe_genes", "draws_deg_genes", "p_value", "p_adjust", "background_mode"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for row in family_enrichment_rows:
            writer.writerow(row)

    evidence_rows: list[dict[str, Any]] = []
    for idx, ev in enumerate(ranked, 1):
        enrich = enrichment_by_family.get(ev.family, {})
        fgsea = fgsea_support.get(ev.family, {})
        wgcna = wgcna_support.get(ev.family, {})
        pathways = fgsea.get("pathways") or []
        modules = wgcna.get("modules") or []
        tier = tier_for_family(ev, enrich, fgsea, wgcna)
        evidence_rows.append({
            "rank": idx,
            "candidate_family": ev.family,
            "recommendation_tier": tier,
            "deg_gene_count": len(ev.genes),
            "background_gene_count": enrich.get("background_gene_count", ""),
            "deg_ratio": enrich.get("deg_ratio", ""),
            "family_enrichment_fdr": enrich.get("p_adjust", ""),
            "comparison_count": len(ev.comparisons),
            "has_time_lrt": "yes" if "time_lrt" in ev.comparisons else "no",
            "up_records": ev.up,
            "down_records": ev.down,
            "best_padj": ev.best_padj if ev.best_padj is not None else "",
            "go_kegg_linked_terms": len(ev.enrichment_terms),
            "fgsea_pathway_count": len(pathways),
            "fgsea_top_pathways": "; ".join([f"{p.get('comparison')}:{p.get('description')}" for p in pathways[:3]]),
            "fgsea_leading_edge_genes": len(fgsea.get("genes") or []),
            "wgcna_supported_genes": len(wgcna.get("genes") or []),
            "wgcna_top_modules": "; ".join([f"{m.get('module')}:{m.get('genes')}" for m in modules[:3]]),
            "recommendation_mode": experiment_context.get("rnaseq_recommendation_mode", "context_neutral"),
            "experiment_design": experiment_context.get("rnaseq_experiment_design", "group_comparison"),
            "evidence_summary": evidence_summary(ev, enrich, fgsea, wgcna),
        })

    with evidence_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = list(evidence_rows[0].keys()) if evidence_rows else ["rank", "candidate_family", "recommendation_tier"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for row in evidence_rows:
            writer.writerow(row)

    tier_order = {"high_response_needs_refinement": 0, "priority_recommended": 1, "mechanism_relevant_limited_size": 2, "context_neutral_candidate": 3, "candidate_pool": 4}
    recommendation_rows = sorted(evidence_rows, key=lambda row: (tier_order.get(row["recommendation_tier"], 9), int(row["rank"])))
    with recommendation_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = ["recommendation_rank", "candidate_family", "recommendation_tier", "deg_gene_count", "comparison_count", "family_enrichment_fdr", "fgsea_pathway_count", "wgcna_supported_genes", "recommendation_mode", "evidence_summary"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for rec_rank, row in enumerate(recommendation_rows[:50], 1):
            writer.writerow({key: row.get(key, "") for key in fieldnames} | {"recommendation_rank": rec_rank})

    recommended_families = {row["candidate_family"] for row in recommendation_rows[:50] if row.get("recommendation_tier") != "candidate_pool"}
    with key_genes_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = ["candidate_family", "gene_id", "comparisons", "regulation", "best_padj", "max_abs_log2fc", "signatures", "domain_descriptions"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for family in sorted(recommended_families):
            rows = []
            for row in gene_rows:
                families = set((row.get("candidate_families") or "").split(";"))
                if family in families:
                    rows.append(row)
            rows.sort(key=lambda row: (parse_float(row.get("best_padj")) if parse_float(row.get("best_padj")) is not None else 1.0, -(parse_float(row.get("max_abs_log2fc")) or 0.0)))
            for row in rows[:10]:
                writer.writerow({
                    "candidate_family": family,
                    "gene_id": row.get("gene_id", ""),
                    "comparisons": row.get("comparisons", ""),
                    "regulation": row.get("regulation", ""),
                    "best_padj": row.get("best_padj", ""),
                    "max_abs_log2fc": row.get("max_abs_log2fc", ""),
                    "signatures": row.get("signatures", ""),
                    "domain_descriptions": row.get("domain_descriptions", ""),
                })
    return family_enrich_path, evidence_path, recommendation_path, key_genes_path


def write_tables(prefix: str, final_dir: Path, ranked: list[FamilyEvidence], gene_rows: list[dict[str, Any]], enrichment_terms: list[dict[str, Any]]) -> tuple[Path, Path, Path]:
    ranking_path = final_dir / f"{prefix}.rnaseq_candidate_family.ranking.tsv"
    gene_path = final_dir / f"{prefix}.rnaseq_candidate_family.gene_annotations.tsv"
    enrich_path = final_dir / f"{prefix}.rnaseq_candidate_family.enrichment_context.tsv"
    with ranking_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow([
            "rank", "candidate_family", "deg_gene_count", "protein_count", "up_records", "down_records",
            "comparisons", "best_padj", "abs_log2fc_sum", "signatures", "interpro_ids",
            "top_descriptions", "linked_enrichment_terms",
        ])
        for idx, ev in enumerate(ranked, 1):
            writer.writerow([
                idx, ev.family, len(ev.genes), len(ev.proteins), ev.up, ev.down,
                ";".join(sorted(ev.comparisons)), ev.best_padj if ev.best_padj is not None else "",
                round(ev.abs_log2fc_sum, 4), ";".join(sorted(ev.signatures)),
                ";".join(sorted(ev.interpros)), ";".join(sorted(ev.descriptions)[:5]),
                ";".join(sorted(ev.enrichment_terms)[:8]),
            ])
    with gene_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "gene_id", "comparisons", "regulation", "best_padj", "max_abs_log2fc",
            "candidate_families", "signatures", "interpro_ids", "domain_descriptions",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for row in gene_rows:
            writer.writerow(row)
    with enrich_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["kind", "comparison", "term_id", "description", "p_adjust", "source_file"])
        for term in enrichment_terms[:100]:
            writer.writerow([term["kind"], term["comparison"], term["id"], term["description"], term["padj"], term["file"]])
    return ranking_path, gene_path, enrich_path


def read_tsv_rows(path: Path | None, limit: int | None = None) -> list[dict[str, str]]:
    if not path or not path.exists():
        return []
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            rows.append({str(k): str(v or "") for k, v in row.items()})
            if limit is not None and len(rows) >= limit:
                break
    return rows


def read_json_file(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def markdown_file_link(path: Path | None, label: str = "file") -> str:
    if not path:
        return label
    return f"[{label}](./{path.name})"


def md_escape(value: Any) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("|", "/") or "-"


def short_text(value: Any, limit: int = 180) -> str:
    text = md_escape(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def fmt_number(value: Any, digits: int = 3) -> str:
    number = parse_float(value)
    if number is None:
        return "-"
    if abs(number) != 0 and abs(number) < 0.001:
        return f"{number:.2e}"
    return f"{number:.{digits}g}"


def row_int(row: dict[str, Any], key: str) -> int:
    try:
        return int(float(row.get(key) or 0))
    except Exception:
        return 0


def tier_label(tier: str) -> str:
    return {
        "priority_recommended": "优先推荐",
        "high_response_needs_refinement": "强响应但需细分",
        "mechanism_relevant_limited_size": "机制相关小家族",
        "context_neutral_candidate": "背景中性候选",
        "candidate_pool": "候选池",
    }.get(tier or "", tier or "-")


def is_generic_domain_family(family: str) -> bool:
    text = str(family or "").lower()
    return text.startswith("pfam/interpro:") or " profile" in text or "domain," in text


def unique_rows_by_family(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = set()
    out = []
    for row in rows:
        family = row.get("candidate_family", "")
        if family and family not in seen:
            seen.add(family)
            out.append(row)
    return out


def evidence_layers(row: dict[str, str]) -> list[str]:
    layers = ["DEG"]
    fdr = parse_float(row.get("family_enrichment_fdr"))
    if fdr is not None and fdr <= 0.05:
        layers.append("家族富集")
    if row_int(row, "fgsea_pathway_count") > 0:
        layers.append("fgsea leading-edge")
    if row_int(row, "wgcna_supported_genes") > 0:
        layers.append("WGCNA")
    if row_int(row, "go_kegg_linked_terms") > 0:
        layers.append("GO/KEGG")
    return layers


def summarize_support(row: dict[str, str]) -> str:
    parts = [
        f"DEG {row.get('deg_gene_count') or '-'} 个",
        f"比较 {row.get('comparison_count') or '-'} 个",
        f"家族富集FDR {fmt_number(row.get('family_enrichment_fdr'))}",
    ]
    fgsea_count = row_int(row, "fgsea_pathway_count")
    if fgsea_count:
        parts.append(f"fgsea通路 {fgsea_count} 个")
    wgcna_genes = row_int(row, "wgcna_supported_genes")
    if wgcna_genes:
        parts.append(f"WGCNA支持基因 {wgcna_genes} 个")
    terms = row_int(row, "go_kegg_linked_terms")
    if terms:
        parts.append(f"GO/KEGG关联 {terms} 项")
    return "; ".join(parts)


def key_gene_map(rows: list[dict[str, str]], max_genes: int = 5) -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        family = row.get("candidate_family", "")
        if family and len(out[family]) < max_genes:
            out[family].append(row)
    return out


def family_lookup(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row.get("candidate_family", ""): row for row in rows if row.get("candidate_family")}


def build_rag_queries(
    prefix: str,
    final_dir: Path,
    recommendation_path: Path,
    evidence_path: Path,
    key_genes_path: Path,
    experiment_context: dict[str, Any],
    limit: int = 12,
) -> Path:
    recommendations = read_tsv_rows(recommendation_path, limit=80)
    evidence_by_family = family_lookup(read_tsv_rows(evidence_path))
    genes_by_family = key_gene_map(read_tsv_rows(key_genes_path), max_genes=5)
    species = experiment_context.get("rnaseq_species_name") or prefix
    treatment = experiment_context.get("rnaseq_treatment") or ""
    treatment_keywords = experiment_context.get("rnaseq_treatment_keywords") or []
    if isinstance(treatment_keywords, str):
        treatment_keywords = [treatment_keywords]
    primary_context = treatment or (treatment_keywords[0] if treatment_keywords else "RNA-seq response")
    topic_terms_raw = [species, primary_context, *treatment_keywords]
    topic_terms = []
    seen_topic_terms = set()
    for term in topic_terms_raw:
        clean = str(term or "").strip()
        key = clean.lower()
        if clean and key not in seen_topic_terms:
            seen_topic_terms.add(key)
            topic_terms.append(clean)

    def query_priority(row: dict[str, str]) -> tuple[int, int]:
        family = row.get("candidate_family", "")
        tier = row.get("recommendation_tier", "")
        if tier == "priority_recommended" and not is_generic_domain_family(family):
            return (0, int(row.get("recommendation_rank") or row.get("rank") or 9999))
        if tier == "mechanism_relevant_limited_size" and not is_generic_domain_family(family):
            return (1, int(row.get("recommendation_rank") or row.get("rank") or 9999))
        if tier == "context_neutral_candidate" and not is_generic_domain_family(family):
            return (2, int(row.get("recommendation_rank") or row.get("rank") or 9999))
        if tier == "high_response_needs_refinement":
            return (3, int(row.get("recommendation_rank") or row.get("rank") or 9999))
        if is_generic_domain_family(family):
            return (4, int(row.get("recommendation_rank") or row.get("rank") or 9999))
        return (5, int(row.get("recommendation_rank") or row.get("rank") or 9999))

    global_queries = []
    if treatment or treatment_keywords:
        global_query_templates = [
            [primary_context, "plant transcriptome"],
            [primary_context, "RNA-seq", "gene family"],
            [primary_context, "salinity", "NaCl", "stress response"],
            [primary_context, "WGCNA", "fgsea", "transcriptome"],
            ["plant", primary_context, "transcription factor", "transporter", "cytochrome P450"],
            [species, primary_context, "time series transcriptome"],
        ]
    else:
        global_query_templates = [["RNA-seq", "candidate gene family", "transcriptome"]]
    seen_global = set()
    for parts in global_query_templates:
        query = " ".join(str(x).strip() for x in parts if str(x).strip())
        key = query.lower()
        if query and key not in seen_global:
            seen_global.add(key)
            global_queries.append(query)

    query_items: list[dict[str, Any]] = []
    for row in sorted(recommendations, key=query_priority):
        family = row.get("candidate_family", "")
        if not family or row.get("recommendation_tier") == "candidate_pool":
            continue
        merged = dict(evidence_by_family.get(family, {}))
        merged.update(row)
        if is_generic_domain_family(family) and len(query_items) >= max(4, limit // 2):
            continue
        key_genes = [g.get("gene_id") for g in genes_by_family.get(family, [])[:5] if g.get("gene_id")]
        base = " ".join([*topic_terms, family, "gene family", "transcriptome"])
        query_items.append({
            "candidate_family": family,
            "recommendation_tier": merged.get("recommendation_tier", ""),
            "evidence_summary": merged.get("evidence_summary", ""),
            "key_genes": key_genes,
            "queries": [
                base,
                " ".join([family, primary_context, "plant gene family"]),
                " ".join([family, "salinity NaCl stress response"]),
                " ".join([species, family, primary_context, "stress response mechanism"]),
                " ".join([species, family, "RNA-seq WGCNA fgsea", primary_context]),
            ],
            "rag_role": "external_literature_support_only",
        })
        if len(query_items) >= limit:
            break

    payload = {
        "schema": "rnaseq_candidate_family_rag_plan_v1",
        "prefix": prefix,
        "species": species,
        "kegg_organism": experiment_context.get("rnaseq_kegg_organism", ""),
        "recommendation_mode": experiment_context.get("rnaseq_recommendation_mode", "context_neutral"),
        "experiment_design": experiment_context.get("rnaseq_experiment_design", ""),
        "treatment": treatment,
        "treatment_keywords": treatment_keywords,
        "detected_axes": experiment_context.get("rnaseq_detected_axes", []),
        "global_queries": global_queries,
        "guardrails": [
            "RAG evidence is external literature support and must not alter DE/family statistics.",
            "Use literature hits to explain or prioritize hypotheses, not to fabricate validation.",
            "If RAG service is unavailable, keep candidate recommendations evidence-based and mark literature support as pending.",
        ],
        "queries": query_items,
    }
    path = final_dir / f"{prefix}.rnaseq_candidate_family.rag_queries.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def build_rag_references_stub(prefix: str, final_dir: Path, rag_queries_path: Path) -> Path:
    path = final_dir / f"{prefix}.rnaseq_candidate_family.rag_references.json"
    if path.exists():
        existing = read_json_file(path)
        if existing.get("references"):
            return path
    payload = {
        "schema": "rnaseq_candidate_family_rag_references_v1",
        "status": "pending_retrieval",
        "message": "No RAG retrieval results have been attached yet. Run /api/agent/rag with job_id, prefix, stage=rnaseq_candidate_family, and use_candidate_context=true to populate references.",
        "source_plan": str(rag_queries_path),
        "references": [],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_family_detail(lines: list[str], row: dict[str, str], genes_by_family: dict[str, list[dict[str, str]]]) -> None:
    family = row.get("candidate_family", "")
    lines.extend([
        f"### {md_escape(family)}",
        "",
        f"- 推荐层级：{tier_label(row.get('recommendation_tier', ''))}",
        f"- 证据层：{', '.join(evidence_layers(row))}",
        f"- 定量摘要：{summarize_support(row)}",
    ])
    if row.get("fgsea_top_pathways"):
        lines.append(f"- fgsea 支持：{short_text(row.get('fgsea_top_pathways'), 260)}")
    if row.get("wgcna_top_modules"):
        lines.append(f"- WGCNA 支持：{short_text(row.get('wgcna_top_modules'), 180)}")
    if row.get("best_padj"):
        lines.append(f"- 家族内 DEG 最小 padj：{fmt_number(row.get('best_padj'))}")
    key_genes = genes_by_family.get(family, [])
    if key_genes:
        gene_bits = []
        for gene in key_genes[:5]:
            fc = fmt_number(gene.get("max_abs_log2fc"), 2)
            padj = fmt_number(gene.get("best_padj"))
            gene_bits.append(f"{gene.get('gene_id')} (max_abs_log2FC={fc}, padj={padj})")
        lines.append(f"- 代表基因：{md_escape('; '.join(gene_bits))}")
    lines.append("")


def write_report(
    path: Path,
    prefix: str,
    metrics: dict[str, Any],
    ranked: list[FamilyEvidence],
    experiment_context: dict[str, Any] | None = None,
    recommendation_path: Path | None = None,
    evidence_path: Path | None = None,
    key_genes_path: Path | None = None,
    rag_queries_path: Path | None = None,
    rag_references_path: Path | None = None,
) -> None:
    experiment_context = experiment_context or {}
    mode = experiment_context.get("rnaseq_recommendation_mode", "context_neutral")
    design = experiment_context.get("rnaseq_experiment_design", "group_comparison")
    treatment = experiment_context.get("rnaseq_treatment", "")
    axes = ", ".join(experiment_context.get("rnaseq_detected_axes", []) or []) or "not detected"
    species_name = experiment_context.get("rnaseq_species_name", "") or "not provided"
    kegg_org = experiment_context.get("rnaseq_kegg_organism", "") or "not provided"
    rag_plan = read_json_file(rag_queries_path)
    rag_refs = read_json_file(rag_references_path)

    evidence_rows = read_tsv_rows(evidence_path)
    recommendation_rows = read_tsv_rows(recommendation_path)
    key_rows = read_tsv_rows(key_genes_path)
    evidence_by_family = family_lookup(evidence_rows)
    genes_by_family = key_gene_map(key_rows)

    enriched_recs = []
    for row in recommendation_rows:
        family = row.get("candidate_family", "")
        merged = dict(evidence_by_family.get(family, {}))
        merged.update(row)
        if family:
            enriched_recs.append(merged)

    tier_counts: dict[str, int] = defaultdict(int)
    for row in evidence_rows:
        tier_counts[row.get("recommendation_tier", "candidate_pool")] += 1

    priority = [r for r in enriched_recs if r.get("recommendation_tier") == "priority_recommended"]
    mechanism = [r for r in enriched_recs if r.get("recommendation_tier") == "mechanism_relevant_limited_size"]
    broad = [r for r in enriched_recs if r.get("recommendation_tier") == "high_response_needs_refinement"]
    neutral = [r for r in enriched_recs if r.get("recommendation_tier") == "context_neutral_candidate"]
    named_priority = [r for r in priority if not is_generic_domain_family(r.get("candidate_family", ""))]
    named_mechanism = [r for r in mechanism if not is_generic_domain_family(r.get("candidate_family", ""))]
    named_neutral = [r for r in neutral if not is_generic_domain_family(r.get("candidate_family", ""))]
    generic_priority = [r for r in priority if is_generic_domain_family(r.get("candidate_family", ""))]
    main_candidates = unique_rows_by_family(named_priority + named_mechanism + named_neutral)[:10]

    lines = [
        "# RNA-seq Candidate Family Recommendation Report",
        "",
        "## 结论定位",
        "",
        "本报告是 `rnaseq_candidate_family` 阶段的候选家族推荐报告。它综合 RNA-seq 差异表达、蛋白家族/结构域注释、GO/KEGG 富集、fgsea leading-edge 支持和 WGCNA 模块支持，用于筛选后续可做正式基因家族鉴定、系统发育和功能验证的目标家族。",
        "",
        "它不是最终 Result Agent 的整篇生物学论文式解读，也不替代正式家族成员鉴定。",
        "",
        "## 实验上下文",
        "",
        f"- 物种：{md_escape(species_name)}",
        f"- KEGG organism：{md_escape(kegg_org)}",
        f"- 推荐模式：{mode}",
        f"- 检测到的实验设计：{design}",
        f"- 检测到的样本轴：{axes}",
        f"- 处理/研究主题关键词：{md_escape(treatment) if treatment else '未提供'}",
        "",
    ]
    if mode == "context_neutral":
        lines.extend([
            "当前样本上下文没有提供明确处理主题，因此推荐只基于 RNA-seq 内部证据和注释证据，不声称这些家族一定对应盐胁迫、干旱、组织发育或其他特定生物学主题。",
            "",
        ])

    lines.extend([
        "## RAG 文献检索与参考入口",
        "",
        f"- RAG 检索计划：{markdown_file_link(rag_queries_path, 'rag_queries.json')}",
        f"- RAG 参考文献文件：{markdown_file_link(rag_references_path, 'rag_references.json')}",
    ])
    global_queries = rag_plan.get("global_queries") or []
    if global_queries:
        lines.extend(["- 全局处理主题检索词：", *[f"  - {md_escape(q)}" for q in global_queries[:8]]])
    references = rag_refs.get("references") or rag_refs.get("sources") or []
    if references:
        lines.extend(["", "### 已链接参考文献", ""])
        for idx, ref in enumerate(references[:12], 1):
            title = md_escape(ref.get("title") or ref.get("name") or ref.get("source") or f"Reference {idx}")
            url = ref.get("url") or ref.get("link") or ""
            meta = md_escape(ref.get("journal") or ref.get("year") or ref.get("meta") or ref.get("source") or "")
            if url:
                lines.append(f"{idx}. [{title}]({url}) {meta}".rstrip())
            else:
                lines.append(f"{idx}. {title} {meta}".rstrip())
    else:
        status = rag_refs.get("status") or "pending_retrieval"
        lines.append(f"- 参考文献状态：{status}。当前报告没有伪造参考文献；运行 RAG 检索后会写入参考文献文件。")
    lines.append("")

    lines.extend([
        "## 证据覆盖",
        "",
        f"- DE 记录数：{metrics.get('deg_records', 0)}",
        f"- DE 基因数：{metrics.get('deg_genes', 0)}",
        f"- 用于排序的已注释 DEG 基因数：{metrics.get('annotated_deg_genes', 0)}",
        f"- InterProScan DEG 命中数：{metrics.get('interproscan_hits_for_deg', 0)}",
        f"- 背景已注释基因数：{metrics.get('background_annotated_genes', 0)}",
        f"- 候选家族/结构域总数：{metrics.get('candidate_family_count', 0)}",
        f"- GO/KEGG 关联条目数：{metrics.get('enrichment_terms', 0)}",
        f"- 有 fgsea leading-edge 支持的家族数：{metrics.get('fgsea_supported_families', 0)}",
        f"- 有 WGCNA 模块支持的家族数：{metrics.get('wgcna_supported_families', 0)}",
        "",
        "## 推荐分层概览",
        "",
        "| 层级 | 含义 | 家族数 |",
        "|---|---|---:|",
    ])
    tier_desc = {
        "priority_recommended": "DEG 规模足够，并有家族富集、fgsea、WGCNA 或 GO/KEGG 等客观证据支持，适合作为优先候选。",
        "high_response_needs_refinement": "响应很强但家族过宽，适合先做亚家族、hub gene 或功能分支细分。",
        "mechanism_relevant_limited_size": "规模较小但机制证据集中，可作为精准候选。",
        "context_neutral_candidate": "DEG 较多但上下文或外部证据不足，保留为背景中性候选。",
        "candidate_pool": "透明保留，不作为当前主推。",
    }
    for tier in ["priority_recommended", "high_response_needs_refinement", "mechanism_relevant_limited_size", "context_neutral_candidate", "candidate_pool"]:
        lines.append(f"| {tier_label(tier)} | {tier_desc[tier]} | {tier_counts.get(tier, 0)} |")

    lines.extend(["", "## 优先推荐家族", ""])
    if main_candidates:
        lines.append("这些家族更适合作为下一步正式基因家族分析或功能验证的入口，因为它们不是仅靠 DEG 数量上榜，而是叠加了至少一类通路、模块或富集证据。")
        lines.append("")
        for row in main_candidates:
            write_family_detail(lines, row, genes_by_family)
    else:
        lines.append("当前没有达到优先推荐规则的家族。建议先检查 DE、fgsea、WGCNA 或样本上下文是否完整。")
        lines.append("")

    if generic_priority:
        lines.extend(["## 其他优先结构域候选", ""])
        lines.append("这些条目是统计证据较强的 Pfam/InterPro 结构域候选，但名称通常不是一个清晰的经典基因家族。它们适合作为功能线索或后续聚类入口，不建议直接作为最终家族名称。")
        lines.append("")
        lines.extend(["| 结构域候选 | DEG | fgsea通路数 | WGCNA支持基因 | 家族FDR |", "|---|---:|---:|---:|---:|"])
        for row in generic_priority[:10]:
            lines.append(
                f"| {md_escape(row.get('candidate_family'))} | {row.get('deg_gene_count', '-')} | {row.get('fgsea_pathway_count', '-')} | "
                f"{row.get('wgcna_supported_genes', '-')} | {fmt_number(row.get('family_enrichment_fdr'))} |"
            )
        lines.append("")

    lines.extend(["## 强响应但需要细分的家族", ""])
    if broad:
        lines.append("这些家族 DEG 数量很高，但家族范围通常较宽。直接把整个大类作为论文对象容易过泛，建议先做亚家族、结构域组合、表达模块或 key gene 筛选后再确定正式目标。")
        lines.append("")
        lines.extend(["| 家族 | DEG基因 | fgsea通路数 | WGCNA支持基因 | 家族富集FDR | 建议 |", "|---|---:|---:|---:|---:|---|"])
        for row in broad[:12]:
            lines.append(
                f"| {md_escape(row.get('candidate_family'))} | {row.get('deg_gene_count', '-')} | {row.get('fgsea_pathway_count', '-')} | "
                f"{row.get('wgcna_supported_genes', '-')} | {fmt_number(row.get('family_enrichment_fdr'))} | 先细分亚家族/模块/key genes |"
            )
        lines.append("")
    else:
        lines.append("没有检测到需要单独标记的大型强响应家族。")
        lines.append("")

    lines.extend(["## Top 家族证据表", ""])
    lines.extend(["| 排名 | 家族 | 层级 | DEG | 比较数 | 家族FDR | fgsea | WGCNA基因 | 证据层 |", "|---:|---|---|---:|---:|---:|---:|---:|---|"])
    for row in enriched_recs[:20]:
        lines.append(
            f"| {row.get('recommendation_rank', row.get('rank', '-'))} | {md_escape(row.get('candidate_family'))} | {tier_label(row.get('recommendation_tier', ''))} | "
            f"{row.get('deg_gene_count', '-')} | {row.get('comparison_count', '-')} | {fmt_number(row.get('family_enrichment_fdr'))} | "
            f"{row.get('fgsea_pathway_count', '-')} | {row.get('wgcna_supported_genes', '-')} | {md_escape(', '.join(evidence_layers(row)))} |"
        )

    lines.extend(["", "## 代表基因", ""])
    if key_rows:
        lines.extend(["| 家族 | 代表基因 | 比较 | 调控 | best padj | max_abs_log2FC | 结构域/签名 |", "|---|---|---|---|---:|---:|---|"])
        family_order = []
        for row in main_candidates + broad + generic_priority:
            family = row.get("candidate_family", "")
            if family and family not in family_order:
                family_order.append(family)
        ordered_key_rows = []
        used_ids = set()
        for family in family_order:
            for row in genes_by_family.get(family, [])[:3]:
                key = (row.get("candidate_family", ""), row.get("gene_id", ""))
                if key not in used_ids:
                    used_ids.add(key)
                    ordered_key_rows.append(row)
        if len(ordered_key_rows) < 30:
            for row in key_rows:
                key = (row.get("candidate_family", ""), row.get("gene_id", ""))
                if key not in used_ids:
                    used_ids.add(key)
                    ordered_key_rows.append(row)
                if len(ordered_key_rows) >= 30:
                    break
        for row in ordered_key_rows[:30]:
            lines.append(
                f"| {md_escape(row.get('candidate_family'))} | {md_escape(row.get('gene_id'))} | {short_text(row.get('comparisons'), 80)} | "
                f"{md_escape(row.get('regulation'))} | {fmt_number(row.get('best_padj'))} | {fmt_number(row.get('max_abs_log2fc'), 2)} | {short_text(row.get('signatures'), 80)} |"
            )
        lines.append("")
    else:
        lines.append("未生成代表基因表。")
        lines.append("")

    lines.extend([
        "## 如何使用这些结果",
        "",
        "1. 优先从“优先推荐家族”中选择目标，因为这些家族同时有 DEG 和至少一类机制/模块/富集证据。",
        "2. 对“强响应但需要细分”的大类，不建议直接把整个大类作为最终论文家族；应先结合 key genes、WGCNA 模块、结构域组合或亚家族进一步缩小范围。",
        "3. 如果用户补充处理关键词，例如盐胁迫、干旱、热、镉、组织或发育阶段，后续 Result/Discovery Agent 可以用这些关键词做 RAG 检索，把外部文献证据和本阶段证据对齐。",
        "4. 正式进入基因家族研究前，还需要运行 family identify、domain QC、系统发育、motif、gene structure 等家族级流程确认成员边界。",
        "",
        "## 边界和注意事项",
        "",
        "- 本阶段的排序是规则化证据汇总，不是隐藏打分模型。",
        "- fgsea 是通路层证据，不能单独证明某个家族就是核心家族。",
        "- WGCNA 是模块层候选证据，样本量较小时应作为探索性结果。",
        "- context-neutral 模式下不做具体胁迫/组织/发育主题断言。",
        "- 多结构域蛋白可能同时进入多个候选家族，最终家族定义需以后续正式鉴定为准。",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cleanup_previous_outputs(final_dir: Path, analysis_dir: Path, prefix: str) -> None:
    patterns = [
        f"{prefix}.rnaseq_candidate_family.*",
        f"{prefix}.rnaseq_candidate_family_report.md",
        f"{prefix}.rnaseq_candidate_family.manifest.json",
        f"{prefix}.rnaseq_experiment_context.json",
    ]
    for pattern in patterns:
        for path in final_dir.glob(pattern):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    for path in analysis_dir.glob(f"{prefix}.rnaseq_candidate_family*"):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="Prioritize RNA-seq candidate families from DE genes and InterProScan/Pfam evidence.")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--input-dir", type=Path, default=None)
    parser.add_argument("-p", "--prefix", required=True)
    parser.add_argument("-j", "--job-id", default="")
    parser.add_argument("-t", "--cpu", type=int, default=4)
    parser.add_argument("--de-files", default="", help="Comma-separated explicit DE_genes files")
    parser.add_argument("--protein-fasta", default="", help="Whole proteome FASTA used to extract DEG proteins")
    parser.add_argument("--interproscan-tsv", default="", help="Existing InterProScan TSV to reuse")
    parser.add_argument("--interproscan", default=DEFAULT_INTERPROSCAN)
    parser.add_argument("--applications", default="Pfam,SMART,PANTHER,ProSiteProfiles")
    parser.add_argument("--run-interproscan", choices=["auto", "always", "never"], default="auto")
    parser.add_argument("--max-deg-proteins", type=int, default=500)
    args = parser.parse_args()

    start = datetime.now()
    workspace = args.workspace.absolute()
    input_dir = (args.input_dir or workspace).absolute()
    workspace_root = workspace.parent
    final_dir = workspace / "final"
    analysis_dir = workspace / "rnaseq_candidate_family" / "analysis"
    final_dir.mkdir(parents=True, exist_ok=True)
    analysis_dir.mkdir(parents=True, exist_ok=True)
    cleanup_previous_outputs(final_dir, analysis_dir, args.prefix)
    manifest_path = final_dir / f"{args.prefix}.rnaseq_candidate_family.manifest.json"
    sample_config = first_existing([
        input_dir / "samples.yaml",
        input_dir / "samples.yml",
        final_dir / "samples.yaml",
        final_dir / "samples.yml",
    ])

    status = "success"
    error = ""
    run_result: dict[str, Any] = {"status": "not_needed"}
    artifacts: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    experiment_context: dict[str, Any] = {}
    experiment_context_path: Path | None = None
    diagnostics = resource_diagnostics(input_dir, workspace, args.prefix)

    try:
        records, de_files = read_de_files(final_dir, args.prefix, args.de_files)
        if not records:
            raise RuntimeError("No DE genes found. Run rnaseq_de first or provide --de-files.")
        deg_genes = {rec.gene_id for rec in records if rec.gene_id}
        deg_id_file = analysis_dir / f"{args.prefix}.rnaseq_candidate_family.deg.ids"
        deg_id_file.write_text("\n".join(sorted(deg_genes)) + "\n", encoding="utf-8")

        protein_fasta = find_protein_fasta(input_dir, final_dir, args.protein_fasta, args.prefix)
        protein_fasta, external_protein_source = localize_external_resource(
            protein_fasta,
            workspace,
            analysis_dir,
            f"{args.prefix}.rnaseq_candidate_family.source.pep.fasta",
        )
        deg_pep = analysis_dir / f"{args.prefix}.rnaseq_candidate_family.deg.pep.fa"
        matched_proteins, scanned_proteins = write_deg_protein_subset(protein_fasta, deg_genes, deg_pep, args.max_deg_proteins)

        interpro_tsv = find_interproscan_tsv(workspace, final_dir, args.prefix, args.interproscan_tsv)
        interpro_tsv, external_interproscan_source = localize_external_resource(
            interpro_tsv,
            workspace,
            analysis_dir,
            f"{args.prefix}.rnaseq_candidate_family.source.interproscan.tsv",
        )
        if not protein_fasta and not interpro_tsv:
            raise RuntimeError(
                "RNA-seq candidate family requires a whole target proteome FASTA or a genome-wide InterProScan TSV. "
                "Family-member InterProScan files such as *.final_members.interproscan.tsv are not valid for genome-wide RNA-seq candidate family ranking. "
                f"Checked roots: {', '.join(diagnostics['checked_roots'])}. "
                f"Missing expected roots: {', '.join(diagnostics['missing_expected_roots']) or 'none'}. "
                f"Next action: {diagnostics['next_action']}"
            )
        candidate_tsv = analysis_dir / f"{args.prefix}.rnaseq_candidate_family.interproscan.tsv"
        should_run = args.run_interproscan == "always" or (
            args.run_interproscan == "auto" and interpro_tsv is None and matched_proteins > 0
        )
        if should_run:
            run_result = run_interproscan(deg_pep, candidate_tsv, Path(args.interproscan), args.cpu, args.applications)
            if run_result.get("status") == "success":
                interpro_tsv = candidate_tsv
        elif interpro_tsv:
            run_result = {"status": "reused", "source": abs_text(interpro_tsv)}

        hits = parse_interproscan_tsv(interpro_tsv, deg_genes)
        background_hits = parse_interproscan_tsv(interpro_tsv, set())
        if not hits:
            raise RuntimeError(
                "No InterPro/Pfam hits were available for DE genes. "
                "Run InterProScan or provide a non-empty --interproscan-tsv before candidate family ranking."
            )
        enrichment_terms = read_enrichment_terms(final_dir, args.prefix)
        ranked, gene_rows = build_evidence(records, hits, enrichment_terms)
        ranking_path, gene_path, enrich_path = write_tables(args.prefix, final_dir, ranked, gene_rows, enrichment_terms)
        report_path = final_dir / f"{args.prefix}.rnaseq_candidate_family_report.md"

        background_by_family, background_genes = build_family_background(background_hits)
        annotated_deg_genes = {h.gene_id for h in hits}
        family_enrichment_rows = make_family_enrichment_rows(ranked, background_by_family, background_genes, annotated_deg_genes)
        family_deg_genes = {ev.family: set(ev.genes) for ev in ranked}
        fgsea_support = read_fgsea_support(final_dir, args.prefix, family_deg_genes)
        wgcna_support = read_wgcna_support(final_dir, args.prefix, family_deg_genes)
        experiment_context = build_experiment_context(
            prefix=args.prefix,
            sample_config=sample_config,
            input_dir=input_dir,
            final_dir=final_dir,
        )
        experiment_context_path = write_experiment_context(final_dir, args.prefix, experiment_context)
        experiment_context["rnaseq_experiment_context_path"] = abs_text(experiment_context_path)
        family_enrich_path, evidence_path, recommendation_path, key_genes_path = write_candidate_extension_tables(
            args.prefix,
            final_dir,
            ranked,
            gene_rows,
            family_enrichment_rows,
            fgsea_support,
            wgcna_support,
            experiment_context,
        )
        rag_queries_path = build_rag_queries(
            args.prefix,
            final_dir,
            recommendation_path,
            evidence_path,
            key_genes_path,
            experiment_context,
        )
        rag_references_path = build_rag_references_stub(args.prefix, final_dir, rag_queries_path)

        metrics = {
            "deg_records": len(records),
            "deg_genes": len(deg_genes),
            "de_files": len(de_files),
            "protein_fasta": abs_text(protein_fasta) if protein_fasta else "",
            "proteins_scanned": scanned_proteins,
            "deg_proteins_matched": matched_proteins,
            "interproscan_tsv": abs_text(interpro_tsv) if interpro_tsv else "",
            "interproscan_run_status": run_result.get("status"),
            "external_protein_source": external_protein_source,
            "external_interproscan_source": external_interproscan_source,
            "interproscan_hits_for_deg": len(hits),
            "interproscan_hits_background": len(background_hits),
            "annotated_deg_genes": len(annotated_deg_genes),
            "background_annotated_genes": len(background_genes),
            "candidate_family_count": len(ranked),
            "enrichment_terms": len(enrichment_terms),
            "fgsea_supported_families": sum(1 for v in fgsea_support.values() if v.get("pathways")),
            "wgcna_supported_families": sum(1 for v in wgcna_support.values() if v.get("modules")),
            "recommendation_mode": experiment_context.get("rnaseq_recommendation_mode", "context_neutral"),
            "experiment_design": experiment_context.get("rnaseq_experiment_design", "group_comparison"),
            "max_deg_proteins": args.max_deg_proteins,
        }
        diagnostics["satisfied"] = bool(metrics.get("protein_fasta") or metrics.get("interproscan_tsv"))
        if diagnostics["satisfied"]:
            reused = []
            if external_protein_source:
                reused.append("protein_fasta")
            if external_interproscan_source:
                reused.append("interproscan_tsv")
            resource_note = f" Reused external resources: {', '.join(reused)}." if reused else ""
            diagnostics["next_action"] = (
                "No additional resource placement is required; genome-wide candidate-family resources "
                "are available through current-job paths."
                + resource_note
            )
        write_report(report_path, args.prefix, metrics, ranked, experiment_context, recommendation_path, evidence_path, key_genes_path, rag_queries_path, rag_references_path)

        artifacts = {
            "deg_ids": artifact(workspace, deg_id_file, "deg_ids", "id_list"),
            "candidate_family_ranking": artifact(workspace, ranking_path, "candidate_family_ranking", "tsv"),
            "gene_annotations": artifact(workspace, gene_path, "gene_annotations", "tsv"),
            "enrichment_context": artifact(workspace, enrich_path, "enrichment_context", "tsv"),
            "family_enrichment": artifact(workspace, family_enrich_path, "family_enrichment", "tsv"),
            "evidence_matrix": artifact(workspace, evidence_path, "evidence_matrix", "tsv"),
            "recommendation": artifact(workspace, recommendation_path, "recommendation", "tsv"),
            "key_genes": artifact(workspace, key_genes_path, "key_genes", "tsv"),
            "experiment_context": artifact(workspace, experiment_context_path, "experiment_context", "json"),
            "rag_queries": artifact(workspace, rag_queries_path, "rag_queries", "json"),
            "rag_references": artifact(workspace, rag_references_path, "rag_references", "json"),
            "report": artifact(workspace, report_path, "report", "markdown"),
        }
        if deg_pep.exists():
            artifacts["deg_proteins"] = artifact(workspace, deg_pep, "deg_proteins", "fasta")
        if interpro_tsv and interpro_tsv.exists():
            artifacts["interproscan_tsv"] = artifact(workspace, interpro_tsv, "interproscan_tsv", "tsv")
        if candidate_tsv.exists():
            artifacts["candidate_interproscan_tsv"] = artifact(workspace, candidate_tsv, "candidate_interproscan_tsv", "tsv")
    except Exception as exc:
        status = "failed"
        error = str(exc)

    manifest = {
        "manifest_version": "1.0",
        "stage": "rnaseq_candidate_family",
        "status": status,
        "prefix": args.prefix,
        "job_id": args.job_id,
        "timestamp": now_iso(),
        "start_time": start.isoformat(timespec="seconds"),
        "end_time": datetime.now().isoformat(timespec="seconds"),
        "elapsed_sec": round((datetime.now() - start).total_seconds(), 2),
        "workspace_root": abs_text(workspace_root),
        "workspace": abs_text(workspace),
        "input_dir": abs_text(input_dir),
        "parameters": {
            "cpu": args.cpu,
            "applications": args.applications,
            "run_interproscan": args.run_interproscan,
            "max_deg_proteins": args.max_deg_proteins,
            "de_files": args.de_files,
            "protein_fasta": args.protein_fasta,
            "interproscan_tsv": args.interproscan_tsv,
            "analysis_dir": abs_text(analysis_dir),
            "final_dir": abs_text(final_dir),
        },
        "inputs": {
            "de_files": [abs_text(p) for p in locals().get("de_files", [])],
            "protein_fasta": metrics.get("protein_fasta", ""),
            "interproscan_tsv": metrics.get("interproscan_tsv", ""),
            "external_resource_sources": {
                "protein_fasta": metrics.get("external_protein_source", ""),
                "interproscan_tsv": metrics.get("external_interproscan_source", ""),
            },
            "sample_config": abs_text(sample_config) if sample_config else "",
            "fgsea_files": [abs_text(p) for p in sorted(final_dir.glob(f"{args.prefix}.rnaseq_fgsea.*.fgsea_kegg.tsv"))],
            "wgcna_gene_modules": abs_text(final_dir / f"{args.prefix}.rnaseq_wgcna.gene_modules.tsv") if (final_dir / f"{args.prefix}.rnaseq_wgcna.gene_modules.tsv").exists() else "",
        },
        "interproscan": run_result,
        "metrics": metrics,
        "artifacts": artifacts,
        "input_requirements": {
            "requires": "whole target proteome FASTA or genome-wide InterProScan TSV",
            "why": "Candidate family ranking needs genome/background annotation for DE genes; family-member annotations are post-identify evidence and cannot rank genome-wide candidates.",
            "satisfied": bool(diagnostics.get("satisfied")),
        },
        "resource_diagnostics": diagnostics,
    }
    apply_rnaseq_context(manifest, find_upstream_rnaseq_context(final_dir, args.prefix, stage="rnaseq_candidate_family"))
    if experiment_context:
        apply_rnaseq_context(manifest, experiment_context)
    if error:
        manifest["error"] = error
    write_manifest(manifest_path, manifest)
    manifest["artifacts"]["manifest"] = artifact(workspace, manifest_path, "manifest", "json")
    write_manifest(manifest_path, manifest)
    if status == "success":
        print(f"RNA-seq candidate family completed: {manifest_path}")
        return 0
    print(f"RNA-seq candidate family failed: {error}", file=os.sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
