#!/usr/bin/env python3
"""RNA-seq enrichment runner.

This runner implements the fast, deterministic path for uploaded gene2go
background files. Full-genome annotation is intentionally kept outside this
interactive step because it can take hours and should be submitted as a
separate background task.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
import sys
import time
import os
import hashlib
import urllib.request
import tempfile
from html import escape
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).absolute().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from typing import Iterable

from cli.rnaseq_env import rnaseq_rscript
from gfa_core.rnaseq_context import apply_rnaseq_context, build_rnaseq_context


GO_RE = re.compile(r"GO:\d{7}")
KO_RE = re.compile(r"K\d{5}")
PATHWAY_RE = re.compile(r"(?:ko|map|path:map)(\d{5})")
RNASEQ_RSCRIPT = rnaseq_rscript()
SPECIES_DB = {
    "ath": "org.At.tair.db",
    "hsa": "org.Hs.eg.db",
    "mmu": "org.Mm.eg.db",
    "rno": "org.Rn.eg.db",
    "dme": "org.Dm.eg.db",
    "sce": "org.Sc.sgd.db",
}
SPECIES_KEYTYPE_HINTS = {
    "ath": ["TAIR", "ENTREZID", "ENSEMBL", "ALIAS"],
    "hsa": ["ENTREZID", "ENSEMBL", "SYMBOL", "ALIAS"],
    "mmu": ["ENTREZID", "ENSEMBL", "SYMBOL", "ALIAS"],
    "rno": ["ENTREZID", "ENSEMBL", "SYMBOL", "ALIAS"],
    "dme": ["FLYBASE", "ENTREZID", "SYMBOL", "ALIAS"],
    "sce": ["ORF", "ENTREZID", "GENENAME", "ALIAS"],
}
GO_NAMESPACE_LABELS = {
    "biological_process": "Biological process",
    "cellular_component": "Cellular component",
    "molecular_function": "Molecular function",
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def compute_file_hash(path: Path) -> str:
    sha256 = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
    except Exception:
        return ""


def abs_text(path: Path | str | None) -> str:
    return str(Path(path).absolute()) if path else ""


def split_terms(value: str) -> list[str]:
    return sorted(set(GO_RE.findall(value)))

def split_kos(value: str) -> list[str]:
    return sorted(set(KO_RE.findall(value)))


GENE_ID_SUFFIX_PATTERNS = [
    re.compile(r"(?i)([._-](?:mrna|rna|transcript|cds|protein|pep|peptide)\d+[A-Za-z]?)$"),
    re.compile(r"(?i)([._-][tp]\d+[A-Za-z]?)$"),
    re.compile(r"(?i)([._-][rp][A-Z])$"),
    re.compile(r"(\.\d+)$"),
]
RAP_TRANSCRIPT_RE = re.compile(r"^(Os\d{2})t(\d+)(?:-\d+)?$", re.IGNORECASE)


def normalize_gene_id(gene_id: str) -> str:
    """Normalize common transcript/protein/version suffixes to gene-level IDs."""
    value = (gene_id or "").strip().strip('"').lstrip(">")
    value = re.split(r"\s+", value, maxsplit=1)[0]
    previous = None
    while value and value != previous:
        previous = value
        for pattern in GENE_ID_SUFFIX_PATTERNS:
            value = pattern.sub("", value)
    rap_match = RAP_TRANSCRIPT_RE.match(value)
    if rap_match:
        value = f"{rap_match.group(1)}g{rap_match.group(2)}"
    return value


def parse_gene2go(path: Path) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    gene_to_terms: dict[str, set[str]] = defaultdict(set)
    term_to_genes: dict[str, set[str]] = defaultdict(set)

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            fields = re.split(r"[\t, ]+", line)
            if not fields:
                continue
            first = normalize_gene_id(fields[0])
            if first.lower() in {"gene", "gene_id", "geneid", "id"}:
                continue

            terms = split_terms(line)
            if not first or not terms:
                continue

            gene_to_terms[first].update(terms)
            for term in terms:
                term_to_genes[term].add(first)

    return dict(gene_to_terms), dict(term_to_genes)


def load_go_ontology(path: Path) -> dict[str, dict[str, str]]:
    """Parse go-basic.obo into GO ID -> name/ontology namespace."""
    if not path.exists():
        return {}

    terms: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    in_term = False

    def flush() -> None:
        if not current:
            return
        go_id = current.get("id", "")
        if not go_id or current.get("is_obsolete") == "true":
            return
        namespace = current.get("namespace", "")
        terms[go_id] = {
            "Description": current.get("name", go_id),
            "Ontology": GO_NAMESPACE_LABELS.get(namespace, namespace or "Unknown"),
        }

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            if line == "[Term]":
                flush()
                current = {}
                in_term = True
                continue
            if line.startswith("[") and line != "[Term]":
                flush()
                current = {}
                in_term = False
                continue
            if not in_term or not line or ": " not in line:
                continue
            key, value = line.split(": ", 1)
            if key in {"id", "name", "namespace", "is_obsolete"}:
                current[key] = value
    flush()
    return terms


def parse_gene2ko(path: Path) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    gene_to_terms: dict[str, set[str]] = defaultdict(set)
    term_to_genes: dict[str, set[str]] = defaultdict(set)

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            fields = re.split(r"[\t, ]+", line)
            if not fields:
                continue
            first = normalize_gene_id(fields[0])
            if first.lower() in {"gene", "gene_id", "geneid", "id", "gid"}:
                continue
            terms = split_kos(line)
            if not first or not terms:
                continue
            gene_to_terms[first].update(terms)
            for term in terms:
                term_to_genes[term].add(first)

    return dict(gene_to_terms), dict(term_to_genes)


def normalize_pathway_id(value: str, organism: str = "") -> str:
    text = (value or "").strip()
    match = PATHWAY_RE.search(text)
    if not match:
        return text
    prefix = organism if organism and re.fullmatch(r"[a-z]{3,4}", organism) else "ko"
    return f"{prefix}{match.group(1)}"


def generic_pathway_id(pathway_id: str) -> str:
    match = re.search(r"(\d{5})", pathway_id or "")
    return f"ko{match.group(1)}" if match else pathway_id


def kegg_cache_dir(args: argparse.Namespace, output_dir: Path) -> Path:
    raw = Path(args.kegg_cache_dir) if getattr(args, "kegg_cache_dir", "") else Path("data/kegg_pathway_cache")
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    if raw.parent.exists() or str(raw).startswith(str(Path.cwd())):
        return raw
    return output_dir / "kegg_pathway_cache"


def fetch_kegg_rest(endpoint: str, cache_file: Path, allow_network: bool) -> str:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    if cache_file.exists() and cache_file.stat().st_size > 0:
        return cache_file.read_text(encoding="utf-8", errors="replace")
    if not allow_network:
        return ""
    url = f"https://rest.kegg.jp/{endpoint.lstrip('/')}"
    with urllib.request.urlopen(url, timeout=60) as response:
        text = response.read().decode("utf-8", errors="replace")
    cache_file.write_text(text, encoding="utf-8")
    return text


def load_kegg_pathway_maps(
    cache_dir: Path,
    organism: str = "",
    allow_network: bool = True,
) -> tuple[dict[str, set[str]], dict[str, dict[str, str]], dict[str, str]]:
    """Load cached or downloaded KO -> KEGG pathway mapping and metadata."""
    ko_to_pathways: dict[str, set[str]] = defaultdict(set)
    pathway_meta: dict[str, dict[str, str]] = {}
    ko_names: dict[str, str] = {}

    list_text = fetch_kegg_rest("list/pathway/ko", cache_dir / "list_pathway_ko.tsv", allow_network)
    for raw_line in list_text.splitlines():
        parts = raw_line.split("\t", 1)
        if len(parts) != 2:
            continue
        pid = normalize_pathway_id(parts[0])
        pathway_meta[pid] = {
            "Description": parts[1].strip(),
            "Class": "Unclassified",
            "Subclass": "Unclassified",
            "GenericPathwayID": generic_pathway_id(pid),
        }

    link_text = fetch_kegg_rest("link/pathway/ko", cache_dir / "link_pathway_ko.tsv", allow_network)
    for raw_line in link_text.splitlines():
        parts = raw_line.split("\t")
        if len(parts) != 2:
            continue
        kos = split_kos(raw_line)
        pathway_hits = [normalize_pathway_id(part) for part in parts if PATHWAY_RE.search(part)]
        if not kos or not pathway_hits:
            continue
        for pathway_id in pathway_hits:
            ko_to_pathways[kos[0]].add(pathway_id)

    brite_text = fetch_kegg_rest("get/br:ko00001", cache_dir / "ko00001.keg", allow_network)
    current_class = "Unclassified"
    current_subclass = "Unclassified"
    for raw_line in brite_text.splitlines():
        line = raw_line.rstrip()
        if not line or line.startswith("!") or line.startswith("+"):
            continue
        level = line[0]
        value = line[1:].strip()
        if level == "A":
            current_class = re.sub(r"^\d+\s+", "", value).strip() or "Unclassified"
        elif level == "B":
            current_subclass = re.sub(r"^\d+\s+", "", value).strip() or "Unclassified"
        elif level == "C":
            match = re.search(r"(\d{5})\s+(.+?)(?:\s+\[PATH:ko\d{5}\])?$", value)
            if not match:
                continue
            pathway_id = f"ko{match.group(1)}"
            meta = pathway_meta.setdefault(
                pathway_id,
                {"Description": match.group(2).strip(), "GenericPathwayID": pathway_id},
            )
            meta["Description"] = meta.get("Description") or match.group(2).strip()
            meta["Class"] = current_class
            meta["Subclass"] = current_subclass
            meta["GenericPathwayID"] = pathway_id

    ko_text = fetch_kegg_rest("list/ko", cache_dir / "ko_list.tsv", allow_network)
    for raw_line in ko_text.splitlines():
        parts = raw_line.split("\t", 1)
        if len(parts) != 2:
            continue
        kos = split_kos(parts[0])
        if kos:
            ko_names[kos[0]] = parts[1].strip()

    organism = (organism or "").strip().lower()
    if organism and re.fullmatch(r"[a-z][a-z0-9]{2,4}", organism):
        species_list_text = fetch_kegg_rest(
            f"list/pathway/{organism}",
            cache_dir / f"list_pathway_{organism}.tsv",
            allow_network,
        )
        species_pathway_meta: dict[str, dict[str, str]] = {}
        for raw_line in species_list_text.splitlines():
            parts = raw_line.split("\t", 1)
            if len(parts) != 2:
                continue
            species_pid = normalize_pathway_id(parts[0], organism)
            generic_pid = generic_pathway_id(species_pid)
            species_pathway_meta[generic_pid] = {
                "Description": parts[1].strip(),
                "Class": pathway_meta.get(generic_pid, {}).get("Class", "Unclassified"),
                "Subclass": pathway_meta.get(generic_pid, {}).get("Subclass", "Unclassified"),
                "GenericPathwayID": generic_pid,
                "SpeciesPathwayID": species_pid,
                "KeggOrganism": organism,
            }
        if species_pathway_meta:
            allowed = set(species_pathway_meta)
            ko_to_pathways = {
                ko: {pid for pid in pathways if generic_pathway_id(pid) in allowed}
                for ko, pathways in ko_to_pathways.items()
            }
            ko_to_pathways = {ko: paths for ko, paths in ko_to_pathways.items() if paths}
            pathway_meta.update(species_pathway_meta)

    return dict(ko_to_pathways), pathway_meta, ko_names


def build_gene_pathway_maps(
    gene_to_kos: dict[str, set[str]],
    ko_to_pathways: dict[str, set[str]],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    gene_to_pathways: dict[str, set[str]] = defaultdict(set)
    pathway_to_genes: dict[str, set[str]] = defaultdict(set)
    for gene, kos in gene_to_kos.items():
        for ko in kos:
            for pathway_id in ko_to_pathways.get(ko, set()):
                gene_to_pathways[gene].add(pathway_id)
                pathway_to_genes[pathway_id].add(gene)
    return dict(gene_to_pathways), dict(pathway_to_genes)


def parse_de_genes(path: Path) -> list[str]:
    genes: list[str] = []
    seen: set[str] = set()

    with path.open("r", encoding="utf-8") as handle:
        reader = csv.reader(handle, delimiter="\t")
        for row in reader:
            if not row:
                continue
            gene = normalize_gene_id(row[0])
            if not gene or gene.startswith("#"):
                continue
            if gene.lower() in {"gene", "gene_id", "geneid", "id", "x"}:
                continue
            if gene not in seen:
                genes.append(gene)
                seen.add(gene)

    return genes


def find_de_files(input_dir: Path, prefix: str, foreground_mode: str = "global", family_name: str = "") -> list[Path]:
    candidates = sorted(input_dir.glob("*DE_genes.txt"))
    if foreground_mode == "family":
        candidates = [p for p in candidates if "family_DE_genes" in p.name]
        if family_name:
            family_token = re.sub(r"[^A-Za-z0-9_.-]+", "_", family_name)
            preferred_family = [p for p in candidates if f".rnaseq_de.{family_token}_" in p.name]
            if preferred_family:
                candidates = preferred_family
    else:
        candidates = [p for p in candidates if "family_DE_genes" not in p.name]
    if prefix:
        preferred = [p for p in candidates if p.name.startswith(f"{prefix}.rnaseq_de.")]
        if preferred:
            candidates = preferred
    return candidates


def comparison_name(path: Path, prefix: str) -> str:
    name = path.name
    for token in (f"{prefix}.rnaseq_de.", f"{prefix}.", "rnaseq_de."):
        if token and name.startswith(token):
            name = name[len(token):]
            break
    if name.endswith(".family_DE_genes.txt"):
        name = name[: -len(".family_DE_genes.txt")]
    elif name.endswith(".DE_genes.txt"):
        name = name[: -len(".DE_genes.txt")]
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name) or "comparison"


def hypergeom_tail(successes: int, term_size: int, foreground_size: int, universe_size: int) -> float:
    max_i = min(term_size, foreground_size)
    if successes <= 0:
        return 1.0
    if successes > max_i or universe_size <= 0 or foreground_size <= 0 or term_size <= 0:
        return 1.0

    min_i = max(successes, foreground_size - (universe_size - term_size))
    if min_i > max_i:
        return 1.0

    def log_choose(n: int, k: int) -> float:
        if k < 0 or k > n:
            return float("-inf")
        return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)

    log_denom = log_choose(universe_size, foreground_size)
    log_terms: list[float] = []
    for i in range(successes, max_i + 1):
        if foreground_size - i > universe_size - term_size:
            continue
        log_terms.append(
            log_choose(term_size, i)
            + log_choose(universe_size - term_size, foreground_size - i)
            - log_denom
        )
    if not log_terms:
        return 1.0
    max_log = max(log_terms)
    total = math.exp(max_log) * sum(math.exp(value - max_log) for value in log_terms)
    return min(total, 1.0)


def bh_adjust(pvalues: list[float]) -> list[float]:
    if not pvalues:
        return []
    indexed = sorted(enumerate(pvalues), key=lambda item: item[1])
    adjusted = [1.0] * len(pvalues)
    running = 1.0
    total = len(pvalues)
    for rank, (index, pvalue) in enumerate(reversed(indexed), start=1):
        original_rank = total - rank + 1
        running = min(running, pvalue * total / original_rank)
        adjusted[index] = min(running, 1.0)
    return adjusted


def enrich_go(
    foreground_genes: Iterable[str],
    gene_to_terms: dict[str, set[str]],
    term_to_genes: dict[str, set[str]],
) -> list[dict[str, object]]:
    universe = set(gene_to_terms)
    foreground = set(foreground_genes) & universe
    universe_size = len(universe)
    foreground_size = len(foreground)
    if universe_size == 0 or foreground_size == 0:
        return []

    rows: list[dict[str, object]] = []
    pvalues: list[float] = []
    for term, genes in sorted(term_to_genes.items()):
        annotated_genes = genes & universe
        hits = foreground & annotated_genes
        if not hits:
            continue
        pvalue = hypergeom_tail(len(hits), len(annotated_genes), foreground_size, universe_size)
        pvalues.append(pvalue)
        rows.append(
            {
                "ID": term,
                "Description": term,
                "GeneRatio": f"{len(hits)}/{foreground_size}",
                "BgRatio": f"{len(annotated_genes)}/{universe_size}",
                "pvalue": pvalue,
                "Count": len(hits),
                "geneID": "/".join(sorted(hits)),
            }
        )

    for row, padj in zip(rows, bh_adjust(pvalues)):
        row["p.adjust"] = padj

    rows.sort(key=lambda row: (float(row["p.adjust"]), float(row["pvalue"]), str(row["ID"])))
    return rows

def enrich_term_table(
    foreground_genes: Iterable[str],
    gene_to_terms: dict[str, set[str]],
    term_to_genes: dict[str, set[str]],
) -> list[dict[str, object]]:
    return enrich_go(foreground_genes, gene_to_terms, term_to_genes)


def foreground_background_overlap(
    foreground_genes: Iterable[str],
    gene_to_terms: dict[str, set[str]],
) -> dict[str, int]:
    foreground = set(foreground_genes)
    background = set(gene_to_terms)
    return {
        "foreground_genes": len(foreground),
        "annotated_background_genes": len(background),
        "overlap_genes": len(foreground & background),
    }


def annotate_go_rows(rows: list[dict[str, object]], go_ontology: dict[str, dict[str, str]]) -> int:
    mapped = 0
    for row in rows:
        info = go_ontology.get(str(row.get("ID", "")))
        if not info:
            continue
        row["Description"] = info.get("Description") or row.get("Description") or row.get("ID")
        row["Ontology"] = info.get("Ontology", "Unknown")
        mapped += 1
    return mapped


def write_go_table(path: Path, rows: list[dict[str, object]], top_pathways: int) -> None:
    columns = ["ID", "Description"]
    if any("Ontology" in row for row in rows):
        columns.append("Ontology")
    columns += ["GeneRatio", "BgRatio", "pvalue", "p.adjust", "Count", "geneID"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        for row in rows[:top_pathways]:
            formatted = dict(row)
            formatted["pvalue"] = f"{float(row['pvalue']):.6g}"
            formatted["p.adjust"] = f"{float(row['p.adjust']):.6g}"
            writer.writerow(formatted)

def write_kegg_table(path: Path, rows: list[dict[str, object]], top_pathways: int) -> None:
    columns = ["ID", "Description"]
    for optional in ("Class", "Subclass", "GenericPathwayID"):
        if any(optional in row for row in rows):
            columns.append(optional)
    columns += ["GeneRatio", "BgRatio", "pvalue", "p.adjust", "Count", "geneID"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows[:top_pathways]:
            formatted = dict(row)
            formatted["pvalue"] = f"{float(row['pvalue']):.6g}"
            formatted["p.adjust"] = f"{float(row['p.adjust']):.6g}"
            writer.writerow(formatted)


def annotate_kegg_pathway_rows(rows: list[dict[str, object]], pathway_meta: dict[str, dict[str, str]]) -> int:
    mapped = 0
    for row in rows:
        pathway_id = str(row.get("ID", ""))
        info = pathway_meta.get(pathway_id) or pathway_meta.get(generic_pathway_id(pathway_id))
        if not info:
            continue
        row["Description"] = info.get("Description") or row.get("Description") or pathway_id
        row["Class"] = info.get("Class", "Unclassified")
        row["Subclass"] = info.get("Subclass", "Unclassified")
        row["GenericPathwayID"] = info.get("GenericPathwayID", generic_pathway_id(pathway_id))
        mapped += 1
    return mapped


PLANT_COMPATIBLE_KEGG_CLASSES = {
    "Metabolism",
    "Genetic Information Processing",
    "Environmental Information Processing",
    "Cellular Processes",
}
PLANT_COMPATIBLE_ORGANISMAL_SUBCLASSES = {"Environmental adaptation"}
ANIMAL_OR_DISEASE_KEGG_PATTERNS = re.compile(
    r"\b(human|cancer|carcinoma|leukemia|diabetes|alzheimer|parkinson|huntington|alcoholism|"
    r"lupus|infection|viral|bacterial invasion|cardiomyopathy|addiction|bile secretion|"
    r"salivary|gastric|pancreatic|endocrine|immune|neutrophil|platelet|synaptic|axon|"
    r"olfactory|taste|thermogenesis|phagosome|longevity regulating pathway - worm|longevity regulating pathway - fly)\b",
    re.IGNORECASE,
)
PLANT_KEEP_KEYWORDS = re.compile(r"\b(plant|photosynthesis|flavonoid|phenylpropanoid|terpenoid|chlorophyll)\b", re.IGNORECASE)


def kegg_is_plant_compatible(row: dict[str, object]) -> bool:
    pathway_class = str(row.get("Class", "") or "Unclassified")
    subclass = str(row.get("Subclass", "") or "Unclassified")
    description = str(row.get("Description", "") or row.get("ID", ""))
    if PLANT_KEEP_KEYWORDS.search(description):
        return True
    if pathway_class == "Human Diseases":
        return False
    if ANIMAL_OR_DISEASE_KEGG_PATTERNS.search(description):
        return False
    if pathway_class in PLANT_COMPATIBLE_KEGG_CLASSES:
        return True
    if pathway_class == "Organismal Systems" and subclass in PLANT_COMPATIBLE_ORGANISMAL_SUBCLASSES:
        return True
    return pathway_class == "Unclassified"


def filter_kegg_rows(rows: list[dict[str, object]], scope: str) -> list[dict[str, object]]:
    if scope == "all":
        return rows
    return [row for row in rows if kegg_is_plant_compatible(row)]


def write_kegg_hit_table(
    path: Path,
    rows: list[dict[str, object]],
    gene_to_kos: dict[str, set[str]],
    ko_to_pathways: dict[str, set[str]],
    pathway_meta: dict[str, dict[str, str]],
    ko_names: dict[str, str],
    top_pathways: int,
) -> None:
    columns = ["PathwayID", "PathwayName", "Class", "Subclass", "KO", "KOName", "GeneCount", "Genes"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        for row in rows[:top_pathways]:
            pathway_id = str(row.get("ID", ""))
            meta = pathway_meta.get(pathway_id) or pathway_meta.get(generic_pathway_id(pathway_id), {})
            hit_genes = [gene for gene in str(row.get("geneID", "")).split("/") if gene]
            ko_to_hit_genes: dict[str, list[str]] = defaultdict(list)
            for gene in hit_genes:
                for ko in sorted(gene_to_kos.get(gene, set())):
                    if pathway_id in ko_to_pathways.get(ko, set()) or generic_pathway_id(pathway_id) in ko_to_pathways.get(ko, set()):
                        ko_to_hit_genes[ko].append(gene)
            for ko, genes in sorted(ko_to_hit_genes.items()):
                writer.writerow(
                    {
                        "PathwayID": pathway_id,
                        "PathwayName": meta.get("Description", row.get("Description", pathway_id)),
                        "Class": meta.get("Class", row.get("Class", "Unclassified")),
                        "Subclass": meta.get("Subclass", row.get("Subclass", "Unclassified")),
                        "KO": ko,
                        "KOName": ko_names.get(ko, ko),
                        "GeneCount": len(set(genes)),
                        "Genes": "/".join(sorted(set(genes))),
                    }
                )


def write_kegg_mapper_files(
    path: Path,
    rows: list[dict[str, object]],
    gene_to_kos: dict[str, set[str]],
    ko_to_pathways: dict[str, set[str]],
    pathway_meta: dict[str, dict[str, str]],
    organism: str,
    top_pathways: int,
) -> None:
    mode = "organism" if organism else "ko"
    columns = ["PathwayID", "PathwayName", "HighlightMode", "KEGGPathwayURL", "KOs", "Genes", "MapperColorData"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        for row in rows[:top_pathways]:
            pathway_id = str(row.get("ID", ""))
            display_id = normalize_pathway_id(pathway_id, organism) if organism else generic_pathway_id(pathway_id)
            meta = pathway_meta.get(pathway_id) or pathway_meta.get(generic_pathway_id(pathway_id), {})
            hit_genes = [gene for gene in str(row.get("geneID", "")).split("/") if gene]
            hit_kos: set[str] = set()
            for gene in hit_genes:
                for ko in gene_to_kos.get(gene, set()):
                    if pathway_id in ko_to_pathways.get(ko, set()) or generic_pathway_id(pathway_id) in ko_to_pathways.get(ko, set()):
                        hit_kos.add(ko)
            writer.writerow(
                {
                    "PathwayID": display_id,
                    "PathwayName": meta.get("Description", row.get("Description", pathway_id)),
                    "HighlightMode": mode,
                    "KEGGPathwayURL": f"https://www.kegg.jp/pathway/{display_id}",
                    "KOs": "/".join(sorted(hit_kos)),
                    "Genes": "/".join(sorted(set(hit_genes))),
                    "MapperColorData": ";".join(f"{ko} red" for ko in sorted(hit_kos)),
                }
            )


def write_enrichment_bar_svg(
    path: Path,
    rows: list[dict[str, object]],
    title: str,
    top_pathways: int,
    fill: str,
    enrich_type: str = "",
) -> None:
    """Write a dependency-free SVG bar plot for top enriched terms."""
    def clean_kegg_plot_label(label: str) -> str:
        if enrich_type.upper() != "KEGG":
            return label
        return re.sub(
            r"\s+-\s+[A-Z][A-Za-z]+(?:\s+[a-z][A-Za-z0-9._-]*){1,4}\s+\([^)]*\)\s*$",
            "",
            label.strip(),
        )

    def wrap_svg_label(label: str, width: int = 34, max_lines: int = 2) -> list[str]:
        words = re.split(r"\s+", label.strip())
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if len(candidate) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = word
            if len(lines) >= max_lines:
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        if not lines:
            lines = [label]
        if len(lines) > max_lines:
            lines = lines[:max_lines]
        if len(" ".join(words)) > len(" ".join(lines)):
            lines[-1] = lines[-1].rstrip(".") + "..."
        return lines

    selected = rows[:top_pathways]
    width = 1100
    row_height = 38
    plot_left = 330
    plot_right = 1040
    top = 80
    bottom = 56
    height = max(220, top + bottom + max(len(selected), 1) * row_height)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width / 2:.0f}" y="34" text-anchor="middle" font-family="Arial, sans-serif" font-size="22" font-weight="700">{escape(title)}</text>',
    ]

    if not selected:
        parts.extend(
            [
                f'<text x="{width / 2:.0f}" y="{height / 2:.0f}" text-anchor="middle" font-family="Arial, sans-serif" font-size="18" fill="#555">No enriched terms passed the current filters</text>',
                "</svg>",
            ]
        )
        path.write_text("\n".join(parts) + "\n", encoding="utf-8")
        return

    scores: list[float] = []
    for row in selected:
        padj = float(row.get("p.adjust", 1.0) or 1.0)
        scores.append(max(-math.log10(max(padj, 1e-300)), 0.0))
    max_score = max(scores) or 1.0

    axis_y = top + len(selected) * row_height + 6
    parts.append(f'<line x1="{plot_left}" y1="{axis_y}" x2="{plot_right}" y2="{axis_y}" stroke="#333" stroke-width="1"/>')
    for tick in range(0, 6):
        value = max_score * tick / 5
        x = plot_left + (plot_right - plot_left) * tick / 5
        parts.append(f'<line x1="{x:.1f}" y1="{axis_y}" x2="{x:.1f}" y2="{axis_y + 5}" stroke="#333" stroke-width="1"/>')
        parts.append(f'<text x="{x:.1f}" y="{axis_y + 22}" text-anchor="middle" font-family="Arial, sans-serif" font-size="12" fill="#333">{value:.1f}</text>')
    parts.append(f'<text x="{(plot_left + plot_right) / 2:.0f}" y="{height - 12}" text-anchor="middle" font-family="Arial, sans-serif" font-size="13" fill="#333">-log10(adjusted p-value)</text>')

    for index, (row, score) in enumerate(zip(selected, scores)):
        y = top + index * row_height
        bar_width = max(1.0, (plot_right - plot_left) * score / max_score)
        term_id = str(row.get("ID", ""))
        desc = str(row.get("Description", "")) or term_id
        desc = clean_kegg_plot_label(desc)
        label = desc if desc == term_id else f"{term_id} {desc}"
        label_lines = wrap_svg_label(label)
        count = row.get("Count", "")
        padj = float(row.get("p.adjust", 1.0) or 1.0)
        parts.append(f'<text x="{plot_left - 12}" y="{y + 13}" text-anchor="end" font-family="Arial, sans-serif" font-size="12" fill="#222">')
        for line_index, label_line in enumerate(label_lines):
            dy = 0 if line_index == 0 else 14
            parts.append(f'<tspan x="{plot_left - 12}" dy="{dy}">{escape(label_line)}</tspan>')
        parts.append("</text>")
        parts.append(f'<rect x="{plot_left}" y="{y + 6}" width="{bar_width:.1f}" height="16" rx="2" fill="{fill}"/>')
        parts.append(f'<text x="{plot_left + bar_width + 6:.1f}" y="{y + 18}" font-family="Arial, sans-serif" font-size="12" fill="#222">Count={escape(str(count))}, FDR={padj:.2g}</text>')

    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def write_enrichment_r_plot(
    enrich_file: Path,
    output_dir: Path,
    comp: str,
    enrich_type: str,
    rscript: Path,
    top_n: int = 20,
    prefix: str = "",
    stage: str = "",
) -> list[Path]:
    """Generate enrichment plots.

    The PlantAI shared script is used only for GO plots so this fix does not
    change KEGG plot naming or behavior.
    """
    if enrich_type.upper() != "GO":
        return write_enrichment_r_plot_legacy(
            enrich_file,
            output_dir,
            comp,
            enrich_type,
            rscript,
            top_n,
            prefix,
            stage,
        )

    if not enrich_file.exists():
        return []

    try:
        with enrich_file.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            if not list(reader):
                return []
    except Exception:
        return []

    script = Path("scripts/plot_rnaseq_go_enrichment.R")
    if not script.exists():
        print(f"[WARN] R enrichment plotting script not found: {script}", file=sys.stderr)
        return []
    if not rscript.exists():
        print(f"[WARN] Rscript not found: {rscript}", file=sys.stderr)
        return []

    output_prefix = enrich_file.with_suffix("")
    cmd = [
        str(rscript),
        str(script),
        str(enrich_file),
        str(output_prefix),
        enrich_type,
        str(top_n),
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            check=False,
        )
    except Exception as exc:
        print(f"[WARN] R enrichment plotting failed for {enrich_file}: {exc}", file=sys.stderr)
        return []

    log_prefix = output_dir / f"plot_{comp}_{enrich_type}"
    try:
        log_prefix.with_suffix(".stdout.log").write_text(result.stdout, encoding="utf-8")
        log_prefix.with_suffix(".stderr.log").write_text(result.stderr, encoding="utf-8")
    except Exception:
        pass

    if result.returncode != 0:
        print(
            f"[WARN] R enrichment plotting failed for {enrich_file}. "
            f"See {log_prefix.with_suffix('.stderr.log')}",
            file=sys.stderr,
        )
        return []

    plot_files: list[Path] = []
    seen: set[str] = set()
    for line in result.stdout.splitlines():
        item = line.strip()
        if not item:
            continue
        path = Path(item)
        if path.exists() and str(path) not in seen:
            plot_files.append(path)
            seen.add(str(path))

    for suffix in (
        "barplot.png",
        "barplot.pdf",
        "dotplot.png",
        "dotplot.pdf",
        "ontology_barplot.png",
        "ontology_barplot.pdf",
    ):
        path = output_prefix.with_name(output_prefix.name + f".{suffix}")
        if path.exists() and str(path) not in seen:
            plot_files.append(path)
            seen.add(str(path))

    return plot_files


def write_enrichment_r_plot_legacy(
    enrich_file: Path,
    output_dir: Path,
    comp: str,
    enrich_type: str,
    rscript: Path,
    top_n: int = 20,
    prefix: str = "",
    stage: str = "",
) -> list[Path]:
    """Keep the pre-PlantAI R plotting behavior for non-GO enrichment."""
    if not enrich_file.exists():
        return []

    try:
        with enrich_file.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            if not list(reader):
                return []
    except Exception:
        return []

    r_code = f"""
suppressPackageStartupMessages(library(ggplot2))
suppressPackageStartupMessages(library(dplyr))

enrich_file <- {r_string(abs_text(enrich_file))}
output_dir <- {r_string(abs_text(output_dir))}
comp <- {r_string(comp)}
enrich_type <- {r_string(enrich_type)}
top_n <- {top_n}
prefix <- {r_string(prefix)}
stage <- {r_string(stage)}

ego <- read.table(enrich_file, header=TRUE, sep="\\t", check.names=FALSE, stringsAsFactors=FALSE)

if (nrow(ego) == 0) {{
  message("No enrichment data to plot for ", comp)
  quit(status=0)
}}

ego_df <- as.data.frame(ego)

parse_ratio <- function(x) {{
  parts <- strsplit(as.character(x), "/")
  sapply(parts, function(p) if (length(p) == 2) as.numeric(p[1]) / as.numeric(p[2]) else 0)
}}

wrap_axis_label <- function(x, width=42, max_lines=2) {{
  vapply(as.character(x), function(label) {{
    if (is.na(label) || label == "") return("")
    lines <- strwrap(label, width=width)
    if (length(lines) == 0) return(label)
    if (length(lines) > max_lines) {{
      lines <- lines[seq_len(max_lines)]
      lines[[max_lines]] <- paste0(sub("\\\\s+$", "", lines[[max_lines]]), "...")
    }}
    paste(lines, collapse="\\n")
  }}, character(1))
}}

clean_kegg_plot_label <- function(x) {{
  if (toupper(enrich_type) != "KEGG") return(as.character(x))
  sub("\\\\s+-\\\\s+[A-Z][A-Za-z]+(?:\\\\s+[a-z][A-Za-z0-9._-]*){{1,4}}\\\\s+\\\\([^)]*\\\\)\\\\s*$", "", as.character(x), perl=TRUE)
}}

ego_df$GeneRatioNum <- parse_ratio(ego_df$GeneRatio)
ego_df <- ego_df[order(ego_df$p.adjust), ]
ego_df <- head(ego_df, top_n)
ego_df$PlotDescription <- clean_kegg_plot_label(ego_df$Description)
ego_df$DisplayDescription <- wrap_axis_label(ego_df$PlotDescription, width=44, max_lines=2)
ego_df$DisplayDescription <- make.unique(ego_df$DisplayDescription, sep=" ")
ego_df$DisplayDescription <- factor(ego_df$DisplayDescription, levels=rev(ego_df$DisplayDescription))

p_bar <- ggplot(ego_df, aes(x=GeneRatioNum, y=DisplayDescription, fill=-log10(p.adjust))) +
  geom_bar(stat="identity") +
  scale_fill_gradient(low="#4a90d9", high="#d94a4a") +
  labs(x="Gene Ratio", y="", title=paste(comp, enrich_type, "enrichment"),
       fill="-log10(FDR)") +
  theme_minimal() +
  theme(axis.text.y = element_text(size=8, lineheight=0.9),
        plot.title = element_text(hjust=0.5, size=12, face="bold"))

pdf_file <- file.path(output_dir, paste0(prefix, ".", stage, ".", comp, "_", enrich_type, "_enrich.pdf"))
pdf(pdf_file, width=13.5, height=max(8, nrow(ego_df) * 0.46))
print(p_bar)
dev.off()

png_file <- file.path(output_dir, paste0(prefix, ".", stage, ".", comp, "_", enrich_type, "_enrich.png"))
png(png_file, width=2025, height=max(1200, nrow(ego_df) * 69), res=150)
print(p_bar)
dev.off()

p_dot <- ggplot(ego_df, aes(x=GeneRatioNum, y=reorder(DisplayDescription, -log10(p.adjust)),
                            size=Count, color=-log10(p.adjust))) +
  geom_point() +
  scale_color_gradient(low="#4a90d9", high="#d94a4a") +
  scale_size(range=c(3, 8)) +
  labs(x="Gene Ratio", y="", title=paste(comp, enrich_type, "dot plot"),
       color="-log10(FDR)", size="Count") +
  theme_minimal() +
  theme(axis.text.y = element_text(size=8, lineheight=0.9),
        plot.title = element_text(hjust=0.5, size=12, face="bold"))

pdf_file_dot <- file.path(output_dir, paste0(prefix, ".", stage, ".", comp, "_", enrich_type, "_dotplot.pdf"))
pdf(pdf_file_dot, width=13.5, height=max(8, nrow(ego_df) * 0.46))
print(p_dot)
dev.off()

png_file_dot <- file.path(output_dir, paste0(prefix, ".", stage, ".", comp, "_", enrich_type, "_dotplot.png"))
png(png_file_dot, width=2025, height=max(1200, nrow(ego_df) * 69), res=150)
print(p_dot)
dev.off()
"""

    r_script = output_dir / f"plot_{comp}_{enrich_type}.R"
    r_script.write_text(r_code, encoding="utf-8")

    result = subprocess.run(
        [str(rscript), abs_text(r_script)],
        cwd=output_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        (output_dir / f"plot_{comp}_{enrich_type}.stdout.log").write_text(result.stdout, encoding="utf-8")
        (output_dir / f"plot_{comp}_{enrich_type}.stderr.log").write_text(result.stderr, encoding="utf-8")
    except Exception:
        pass
    if result.returncode != 0:
        print(f"[WARN] R enrichment plotting failed for {enrich_file}", file=sys.stderr)
        return []

    plot_files: list[Path] = []
    seen: set[str] = set()
    for ext in ("pdf", "png"):
        for plot_type in ("enrich", "dotplot"):
            path = output_dir / f"{prefix}.{stage}.{comp}_{enrich_type}_{plot_type}.{ext}"
            if path.exists() and str(path) not in seen:
                plot_files.append(path)
                seen.add(str(path))

    return plot_files


def parse_de_results_log2fc(path: Path) -> dict[str, float]:
    """Parse a DESeq2 results table (TSV) and return gene_id -> log2FoldChange."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames:
            return {}
        key_col = "gene_id" if "gene_id" in reader.fieldnames else reader.fieldnames[0]
        fc_col = "log2FoldChange" if "log2FoldChange" in reader.fieldnames else None
        if fc_col is None:
            return {}
        out: dict[str, float] = {}
        for row in reader:
            gene = normalize_gene_id(str(row.get(key_col, "")).strip())
            if not gene:
                continue
            raw_fc = str(row.get(fc_col, "")).strip()
            if not raw_fc or raw_fc.upper() == "NA":
                continue
            try:
                out[gene] = float(raw_fc)
            except ValueError:
                continue
        return out


def build_ko_log2fc(
    genes: list[str],
    gene_to_kos: dict[str, set[str]],
    gene_log2fc: dict[str, float],
) -> dict[str, float]:
    """Aggregate gene log2FC values to KO level (mean across genes mapped to that KO)."""
    ko_values: dict[str, list[float]] = defaultdict(list)
    for gene in genes:
        fc = gene_log2fc.get(normalize_gene_id(gene))
        if fc is None:
            continue
        for ko in gene_to_kos.get(gene, set()):
            if ko:
                ko_values[ko].append(fc)
    return {ko: (sum(vals) / len(vals)) for ko, vals in ko_values.items() if vals}


def strip_pathway_prefix(pathway_id: str) -> str:
    pid = pathway_id.strip()
    match = re.search(r"(\d{5})$", pid)
    return match.group(1) if match else pid


def run_pathview_kegg_plots(
    rows: list[dict[str, object]],
    genes: list[str],
    de_results_file: Path,
    gene_to_kos: dict[str, set[str]],
    output_dir: Path,
    prefix: str,
    output_tag: str,
    comp: str,
    rscript: str,
    top_pathways: int,
) -> list[Path]:
    """
    Generate KEGG pathway maps with pathview using KO-level log2FC.

    Output follows KEGG naming conventions:
    {prefix}.{stage}.{comparison}.KEGG_pathway_map.koXXXXX.png
    """
    if not rows:
        return []

    gene_log2fc = parse_de_results_log2fc(de_results_file)
    ko_log2fc = build_ko_log2fc(genes, gene_to_kos, gene_log2fc)
    if not ko_log2fc:
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    section_tag = "KEGG_pathway_map"
    ko_fc_path = output_dir / f"{prefix}.{output_tag}.{comp}.{section_tag}.ko_log2fc.tsv"
    with ko_fc_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["KO", "log2FoldChange"])
        for ko, fc in sorted(ko_log2fc.items()):
            writer.writerow([ko, f"{fc:.6g}"])

    plot_ids = [strip_pathway_prefix(str(row.get("ID", ""))) for row in rows[:top_pathways] if row.get("ID")]
    plot_ids = [pid for pid in dict.fromkeys(plot_ids) if pid]
    if not plot_ids:
        return []

    r_code = f"""
suppressPackageStartupMessages({{
  library(pathview)
}})

df <- read.table({json.dumps(str(ko_fc_path))}, header=TRUE, sep="\\t", quote="", comment.char="")
gene.data <- df$log2FoldChange
names(gene.data) <- df$KO

out.dir <- {json.dumps(str(output_dir))}
dir.create(out.dir, recursive=TRUE, showWarnings=FALSE)
setwd(out.dir)

plot_ids <- c({", ".join(json.dumps(pid) for pid in plot_ids)})

for (pid in plot_ids) {{
  tryCatch({{
    pathview(
      gene.data = gene.data,
      pathway.id = pid,
      species = "ko",
      kegg.native = TRUE,
      gene.idtype = "kegg",
      out.suffix = {json.dumps(section_tag)}
    )
  }}, error=function(e) {{
    message("pathview failed for ", pid, ": ", e$message)
  }})
}}
cat("PATHVIEW_DONE\\n")
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".R", delete=False) as handle:
        script_path = Path(handle.name)
        handle.write(r_code)

    try:
        proc = subprocess.run(
            [rscript, str(script_path)],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        (output_dir / f"{prefix}.{output_tag}.{comp}.{section_tag}.stdout.log").write_text(proc.stdout, encoding="utf-8")
        (output_dir / f"{prefix}.{output_tag}.{comp}.{section_tag}.stderr.log").write_text(proc.stderr, encoding="utf-8")
        if proc.returncode != 0:
            return []
    finally:
        try:
            script_path.unlink(missing_ok=True)
        except Exception:
            pass

    produced: list[Path] = []
    for ext in ("png", "pdf"):
        for raw in sorted(output_dir.glob(f"ko*.{section_tag}.{ext}")):
            match = re.match(rf"^(ko\d{{5}})\.{re.escape(section_tag)}\.{ext}$", raw.name)
            if not match:
                continue
            pathway_id = match.group(1)
            target = output_dir / f"{prefix}.{output_tag}.{comp}.{section_tag}.{pathway_id}.{ext}"
            try:
                if target.exists():
                    target.unlink()
                raw.rename(target)
                produced.append(target)
            except Exception as exc:
                print(f"[WARN] rename pathview output failed: {raw.name} -> {target.name}: {exc}", file=sys.stderr)
                produced.append(raw)
    return produced


def r_string(value: Path | str) -> str:
    return json.dumps(str(value))


def build_orgdb_r_script(
    r_script: Path,
    de_files: list[Path],
    output_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Path]:
    org_db = SPECIES_DB[args.species]
    keytype_hints = SPECIES_KEYTYPE_HINTS.get(args.species, ["ENTREZID", "ENSEMBL", "SYMBOL", "ALIAS"])
    output_files: dict[str, Path] = {}

    de_entries = []
    for de_file in de_files:
        comp = comparison_name(de_file, args.prefix)
        go_file = output_dir / f"{args.prefix}.{result_prefix(args)}.{comp}.GO_enrich.txt"
        output_files[comp] = go_file
        de_entries.append(
            "list(name=%s, path=%s, output=%s)"
            % (r_string(comp), r_string(de_file), r_string(go_file))
        )

    r_code = f"""
suppressPackageStartupMessages(library(clusterProfiler))
suppressPackageStartupMessages(library(AnnotationDbi))
suppressPackageStartupMessages(library({org_db}))
suppressPackageStartupMessages(library(ggplot2))
suppressPackageStartupMessages(library(enrichplot))

orgdb <- get("{org_db}")
de_files <- list({", ".join(de_entries)})
pvalue_cutoff <- {args.pvalue_cutoff}
qvalue_cutoff <- {args.qvalue_cutoff}
top_n <- {args.top_pathways}
requested_keytype <- {r_string(args.key_type)}
keytype_hints <- c({", ".join(r_string(item) for item in keytype_hints)})

read_de_genes <- function(path) {{
  table <- read.table(path, header=TRUE, sep="\\t", quote="", check.names=FALSE, stringsAsFactors=FALSE)
  genes <- table[[1]]
  genes <- unique(as.character(genes[!is.na(genes) & genes != ""]))
  genes
}}

choose_keytype <- function(genes) {{
  available <- AnnotationDbi::keytypes(orgdb)
  if (requested_keytype != "auto") {{
    if (!(requested_keytype %in% available)) {{
      stop(paste("Requested keyType not available:", requested_keytype))
    }}
    return(requested_keytype)
  }}
  candidates <- unique(c(keytype_hints, available))
  candidates <- candidates[candidates %in% available]
  best_keytype <- candidates[[1]]
  best_hits <- -1
  sample_genes <- head(genes, 200)
  for (candidate in candidates) {{
    hit_count <- tryCatch({{
      mapped <- suppressWarnings(AnnotationDbi::select(orgdb, keys=sample_genes, columns=c("GO"), keytype=candidate))
      length(unique(mapped[!is.na(mapped$GO), 1]))
    }}, error=function(e) -1)
    if (hit_count > best_hits) {{
      best_hits <- hit_count
      best_keytype <- candidate
    }}
  }}
  best_keytype
}}

write_empty_result <- function(path) {{
  empty <- data.frame(ID=character(), Description=character(), GeneRatio=character(), BgRatio=character(),
                      pvalue=numeric(), p.adjust=numeric(), qvalue=numeric(), geneID=character(), Count=integer())
  write.table(empty, file=path, sep="\\t", quote=FALSE, row.names=FALSE)
}}

for (entry in de_files) {{
  genes <- read_de_genes(entry$path)
  if (length(genes) == 0) {{
    write_empty_result(entry$output)
    next
  }}
  keytype <- choose_keytype(genes)
  message("Processing ", entry$name, " genes=", length(genes), " keyType=", keytype)
  ego <- tryCatch({{
    enrichGO(
      gene=genes,
      OrgDb=orgdb,
      keyType=keytype,
      ont="ALL",
      pvalueCutoff=pvalue_cutoff,
      pAdjustMethod="BH",
      qvalueCutoff=qvalue_cutoff,
      readable=FALSE
    )
  }}, error=function(e) {{
    message("enrichGO failed for ", entry$name, ": ", e$message)
    NULL
  }})
  if (is.null(ego) || nrow(as.data.frame(ego)) == 0) {{
    write_empty_result(entry$output)
    next
  }}
  result <- head(as.data.frame(ego), top_n)
  write.table(result, file=entry$output, sep="\\t", quote=FALSE, row.names=FALSE)
}}
"""
    r_script.write_text(r_code, encoding="utf-8")
    return output_files


def run_orgdb_enrichment(
    de_files: list[Path],
    final_dir: Path,
    output_dir: Path,
    args: argparse.Namespace,
) -> tuple[list[Path], int, int]:
    if args.species not in SPECIES_DB:
        supported = ", ".join(sorted(SPECIES_DB))
        raise ValueError(f"Unsupported built-in species '{args.species}'. Supported species: {supported}")

    rscript = Path(args.rscript)
    if not rscript.exists():
        raise FileNotFoundError(f"Rscript not found: {rscript}")

    r_script = output_dir / "run_enrichment_orgdb.R"
    output_files = build_orgdb_r_script(r_script, de_files, final_dir, args)
    result = subprocess.run(
        [str(rscript), str(r_script)],
        cwd=output_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    (output_dir / "run_enrichment_orgdb.stdout.log").write_text(result.stdout, encoding="utf-8")
    (output_dir / "run_enrichment_orgdb.stderr.log").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            f"OrgDb enrichment failed with exit code {result.returncode}. "
            f"See {output_dir / 'run_enrichment_orgdb.stderr.log'}"
        )

    go_files = list(output_files.values())
    term_count = 0
    for go_file in go_files:
        if not go_file.exists():
            raise FileNotFoundError(f"Expected GO enrichment output not found: {go_file}")
        with go_file.open("r", encoding="utf-8") as handle:
            term_count += max(sum(1 for _ in handle) - 1, 0)
    return go_files, term_count, len(de_files)


def rel_artifact(final_dir: Path, file_path: Path, artifact_type: str) -> dict[str, str]:
    return {
        "path": f"final/{file_path.name}",
        "full_path": abs_text(file_path),
        "type": artifact_type,
    }


def cleanup_previous_outputs(final_dir: Path, output_dir: Path, prefix: str, stage_id: str) -> None:
    for pattern in (
        f"{prefix}.{stage_id}.*",
        f"{prefix}.{stage_id}_report.md",
        f"plot_*_GO.*.log",
        f"plot_*_KEGG.*.log",
    ):
        for path in final_dir.glob(pattern):
            if path.is_file():
                path.unlink()
    for pattern in ("commands.sh", "environment.yml", "checksums.sha256"):
        path = output_dir / pattern
        if path.is_file():
            path.unlink()


def stage_id_from_args(args: argparse.Namespace) -> str:
    if getattr(args, "stage", ""):
        return args.stage
    if args.enrich_type == "go":
        return "rnaseq_go_enrich"
    if args.enrich_type == "kegg":
        return "rnaseq_kegg_enrich"
    return "rnaseq_enrich"


def result_prefix(args: argparse.Namespace) -> str:
    return stage_id_from_args(args)


def write_reproducibility(
    output_dir: Path,
    cmd: list[str],
    files: list[Path],
    environment_name: str = "rnaseq",
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    commands_file = output_dir / "commands.sh"
    with commands_file.open("w", encoding="utf-8") as handle:
        handle.write("#!/bin/bash\nset -euo pipefail\n\n")
        handle.write(" ".join(subprocess.list2cmdline([part]) for part in cmd) + "\n")
    os.chmod(commands_file, 0o755)

    env_file = output_dir / "environment.yml"
    env_file.write_text(
        f"name: {environment_name}\n"
        "channels:\n"
        "  - bioconda\n"
        "  - conda-forge\n"
        "dependencies:\n"
        "  - python\n"
        "  - r-base\n"
        "  - bioconductor-clusterprofiler\n",
        encoding="utf-8",
    )

    checksums_file = output_dir / "checksums.sha256"
    with checksums_file.open("w", encoding="utf-8") as handle:
        for path in files:
            if path and path.exists() and path.is_file():
                handle.write(f"{compute_file_hash(path)}  {path}\n")

    return commands_file, env_file, checksums_file


def build_manifest(
    args: argparse.Namespace,
    status: str,
    start_time: str,
    elapsed_sec: float,
    final_dir: Path,
    artifacts: dict[str, dict[str, str]],
    metrics: dict[str, object],
    error: str | None = None,
) -> dict[str, object]:
    output_dir = Path(args.output).absolute()
    job_workspace = output_dir.parent if output_dir.name == stage_id_from_args(args) else final_dir.parent
    sample_table = job_workspace / "rnaseq_sample_qc" / "sample_table.tsv"
    count_matrix = final_dir / f"{args.prefix}.count_matrix.txt"
    manifest = {
        "manifest_version": "1.0",
        "stage": stage_id_from_args(args),
        "status": status,
        "prefix": args.prefix,
        "job_id": args.job_id,
        "timestamp": now_iso(),
        "start_time": start_time,
        "end_time": now_iso(),
        "elapsed_sec": round(elapsed_sec, 3),
        "workspace_root": abs_text(job_workspace.parent),
        "workspace": abs_text(job_workspace),
        "input_dir": abs_text(job_workspace.parent.parent / "input" / args.job_id),
        "parameters": {
            "cpu": args.cpu,
            "prefix": args.prefix,
            "species": args.species,
            "top_pathways": args.top_pathways,
            "gene2go": abs_text(args.gene2go) if args.gene2go else "",
            "background_source": args.background_source_effective,
            "org_db": SPECIES_DB.get(args.species, ""),
            "key_type": args.key_type,
            "pvalue_cutoff": args.pvalue_cutoff,
            "qvalue_cutoff": args.qvalue_cutoff,
            "auto_annotate_requested": bool(args.auto_annotate),
            "foreground_mode": args.foreground_mode,
            "family_name": args.family_name,
            "enrich_type": args.enrich_type,
            "kegg_organism": args.kegg_organism,
            "kegg_level": args.kegg_level,
            "kegg_filter_scope": args.kegg_filter_scope,
            "kegg_highlight_mode": args.kegg_highlight_mode,
            "no_kegg_network": bool(args.no_kegg_network),
            "skip_pathview": bool(args.skip_pathview),
            "plot_type": args.plot_type,
            "kegg_cache_dir": abs_text(kegg_cache_dir(args, Path(args.output))) if args.enrich_type in {"kegg", "both"} else "",
            "gene2ko": abs_text(args.gene2ko) if args.gene2ko else "",
            "go_obo": abs_text(args.go_obo) if args.go_obo else "",
            "input_final_dir": abs_text(final_dir),
            "analysis_dir": abs_text(output_dir),
            "stage": stage_id_from_args(args),
        },
        "metrics": metrics,
        "artifacts": artifacts,
        "cmd": sys.argv,
    }
    context = build_rnaseq_context(
        prefix=args.prefix,
        count_matrix=count_matrix if count_matrix.exists() else None,
        sample_table=sample_table if sample_table.exists() else None,
        foreground_mode=args.foreground_mode,
        family_label=args.family_name,
        stage=stage_id_from_args(args),
    )
    apply_rnaseq_context(manifest, context)
    if error:
        manifest["error"] = error
    return manifest


def write_report(
    path: Path,
    args: argparse.Namespace,
    metrics: dict[str, object],
    go_files: list[Path],
    kegg_files: list[Path],
    plot_files: list[Path],
) -> None:
    lines = [
        f"# {stage_id_from_args(args)} Report",
        "",
        f"- Prefix: {args.prefix}",
        f"- Job ID: {args.job_id}",
        f"- Species code: {args.species}",
        f"- Background source: {args.background_source_effective}",
        f"- Foreground mode: {args.foreground_mode}",
        f"- Family name: {args.family_name}",
        f"- Enrich type: {args.enrich_type}",
        f"- OrgDb: {SPECIES_DB.get(args.species, '')}",
        f"- KEGG organism code: {args.kegg_organism}",
        f"- Comparisons: {metrics.get('comparisons', 0)}",
        f"- DE genes input: {metrics.get('de_genes_input', 0)}",
        f"- GO enriched terms reported: {metrics.get('go_enriched_terms', 0)}",
        f"- KEGG enriched pathways reported: {metrics.get('kegg_enriched_pathways', 0)}",
        "",
        "## Output Files",
    ]
    for file_path in go_files:
        lines.append(f"- {file_path.name}")
    for file_path in kegg_files:
        lines.append(f"- {file_path.name}")
    for file_path in plot_files:
        lines.append(f"- {file_path.name}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run RNA-seq GO enrichment from DE genes and gene2go background.")
    parser.add_argument("-i", "--input", required=True, help="Input final directory containing *.DE_genes.txt")
    parser.add_argument("-s", "--species", default="", help="Built-in OrgDb species code")
    parser.add_argument("-o", "--output", required=True, help="Intermediate output directory")
    parser.add_argument("-p", "--prefix", required=True, help="Analysis prefix")
    parser.add_argument("-j", "--job-id", required=True, help="Job ID")
    parser.add_argument("-t", "--cpu", type=int, default=4, help="CPU cores")
    parser.add_argument("--top-pathways", type=int, default=20, help="Maximum terms to report per comparison")
    parser.add_argument("-g", "--gene2go", default="", help="Uploaded gene2go background file")
    parser.add_argument("--gene2ko", default="", help="Uploaded gene2ko background file")
    parser.add_argument("--go-obo", default="data/go/go-basic.obo", help="GO OBO file used to add term name and BP/CC/MF ontology")
    parser.add_argument(
        "--background-source",
        choices=["auto", "gene2go", "gene2ko", "orgdb"],
        default="auto",
        help="Background source: uploaded gene2go/gene2ko or built-in OrgDb",
    )
    parser.add_argument("--key-type", default="auto", help="OrgDb keyType, or auto")
    parser.add_argument("--pvalue-cutoff", type=float, default=0.05, help="GO enrichment p-value cutoff")
    parser.add_argument("--qvalue-cutoff", type=float, default=0.05, help="GO enrichment q-value cutoff")
    parser.add_argument("--rscript", default=RNASEQ_RSCRIPT, help="Rscript executable for OrgDb enrichment")
    parser.add_argument("--kegg-organism", default="", help="KEGG organism code (e.g. ath, hsa)")
    parser.add_argument(
        "--kegg-level",
        choices=["pathway", "ko"],
        default="pathway",
        help="KEGG enrichment level. pathway uses KO-to-pathway mapping; ko keeps legacy KO-term enrichment.",
    )
    parser.add_argument(
        "--kegg-filter-scope",
        choices=["plant", "all"],
        default="plant",
        help="Default KEGG display filter. plant removes human/animal disease pathways but writes raw results separately.",
    )
    parser.add_argument(
        "--kegg-highlight-mode",
        choices=["auto", "ko", "organism"],
        default="auto",
        help="Pathway highlight output mode. auto uses organism mode only when --kegg-organism is provided.",
    )
    parser.add_argument("--kegg-cache-dir", default="data/kegg_pathway_cache", help="Local cache for KEGG REST pathway mappings")
    parser.add_argument("--no-kegg-network", action="store_true", help="Do not download missing KEGG pathway mapping cache files")
    parser.add_argument("--skip-pathview", action="store_true", help="Skip pathview native KEGG pathway map rendering")
    parser.add_argument("--auto-annotate", action="store_true", help="Accepted for compatibility; not run here")
    parser.add_argument(
        "--enrich-type",
        choices=["go", "kegg", "both"],
        default="go",
        help="Run GO enrichment, KEGG enrichment, or both",
    )
    parser.add_argument(
        "--foreground-mode",
        choices=["global", "family"],
        default="global",
        help="Foreground genes: global DE genes or gene-family-specific DE genes",
    )
    parser.add_argument("--family-name", default="", help="Family name used to select family_DE_genes files")
    parser.add_argument(
        "--stage",
        choices=["rnaseq_go_enrich", "rnaseq_kegg_enrich", "rnaseq_enrich"],
        default="",
        help="Manifest/output stage name. Defaults from --enrich-type.",
    )
    parser.add_argument(
        "--plot-type",
        choices=["svg", "r_plot"],
        default="r_plot",
        help="Plot type: svg (Python-generated) or r_plot (R enrichplot)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.background_source_effective = "missing"
    start = time.time()
    start_time = now_iso()
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    final_dir = input_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)

    stage_id = stage_id_from_args(args)
    output_tag = result_prefix(args)
    cleanup_previous_outputs(final_dir, output_dir, args.prefix, stage_id)
    manifest_path = final_dir / f"{args.prefix}.{stage_id}.manifest.json"
    artifacts: dict[str, dict[str, str]] = {}
    metrics: dict[str, object] = {
        "de_genes_input": 0,
        "go_enriched_terms": 0,
        "kegg_enriched_pathways": 0,
        "comparisons": 0,
        "annotated_background_genes": 0,
        "foreground_mode": args.foreground_mode,
        "family_name": args.family_name,
        "go_ontology_terms": 0,
        "go_ontology_mapped_terms": 0,
        "kegg_pathway_mapped_terms": 0,
        "kegg_raw_pathways": 0,
        "kegg_filtered_pathways": 0,
        "kegg_level": args.kegg_level,
        "kegg_filter_scope": args.kegg_filter_scope,
        "pathview_skipped": False,
        "go_foreground_background_overlap": {},
    }

    try:
        de_files = find_de_files(input_dir, args.prefix, args.foreground_mode, args.family_name)
        if not de_files:
            if args.foreground_mode == "family":
                raise FileNotFoundError(
                    f"No family-specific DE genes files found under {input_dir}. "
                    "Run RNA-seq DE with foreground mode 'family' first, or choose foreground mode 'global'."
                )
            raise FileNotFoundError(f"No global DE genes files found under {input_dir}")
        metrics["de_source_files"] = {
            comparison_name(path, args.prefix): abs_text(path)
            for path in de_files
        }

        go_files: list[Path] = []
        kegg_files: list[Path] = []
        plot_files: list[Path] = []
        total_de_genes = 0
        total_terms = 0
        total_kegg_terms = 0

        run_go = args.enrich_type in {"go", "both"}
        run_kegg = args.enrich_type in {"kegg", "both"}
        go_ontology = load_go_ontology(Path(args.go_obo)) if run_go else {}
        metrics["go_ontology_terms"] = len(go_ontology)
        use_gene2go = args.background_source in {"auto", "gene2go", "gene2ko"} and (
            bool(args.gene2go) or (run_kegg and bool(args.gene2ko))
        )
        use_orgdb = args.background_source == "orgdb" or (
            args.background_source == "auto" and not args.gene2go and args.species in SPECIES_DB
        )

        if use_gene2go:
            args.background_source_effective = "uploaded_gene2go"
            gene_to_terms: dict[str, set[str]] = {}
            term_to_genes: dict[str, set[str]] = {}
            annotated_background_genes = 0
            if run_go:
                gene2go_path = Path(args.gene2go)
                if not gene2go_path.exists():
                    raise FileNotFoundError(f"gene2go file not found: {gene2go_path}")

                gene_to_terms, term_to_genes = parse_gene2go(gene2go_path)
                if not gene_to_terms:
                    raise ValueError(f"No valid GO annotations found in {gene2go_path}")
                annotated_background_genes = len(gene_to_terms)

            for de_file in de_files:
                genes = parse_de_genes(de_file)
                total_de_genes += len(genes)
                comp = comparison_name(de_file, args.prefix)
                if run_go:
                    overlap_stats = foreground_background_overlap(genes, gene_to_terms)
                    metrics["go_foreground_background_overlap"][comp] = overlap_stats
                    if genes and gene_to_terms and overlap_stats["overlap_genes"] == 0:
                        examples = {
                            "foreground_examples": genes[:5],
                            "background_examples": sorted(gene_to_terms)[:5],
                        }
                        raise ValueError(
                            "GO foreground/background ID mismatch: no DE genes overlap uploaded gene2go "
                            f"background for {comp}. examples={json.dumps(examples, ensure_ascii=False)}"
                        )
                    rows = enrich_term_table(genes, gene_to_terms, term_to_genes)
                    metrics["go_ontology_mapped_terms"] = int(metrics.get("go_ontology_mapped_terms", 0)) + annotate_go_rows(rows, go_ontology)
                    go_file = final_dir / f"{args.prefix}.{output_tag}.{comp}.GO_enrich.txt"
                    write_go_table(go_file, rows, args.top_pathways)
                    go_files.append(go_file)
                    
                    # Generate plots based on plot_type
                    if args.plot_type == "r_plot":
                        rscript = Path(args.rscript) if args.rscript else Path(RNASEQ_RSCRIPT)
                        stage_name = "rnaseq_go_enrich" if "go" in args.enrich_type else output_tag
                        go_plot_files = write_enrichment_r_plot(
                            go_file, final_dir, comp, "GO", rscript, args.top_pathways, args.prefix, stage_name
                        )
                        plot_files.extend(go_plot_files)
                        for pf in go_plot_files:
                            suffix = "pdf" if pf.suffix == ".pdf" else "png"
                            artifacts[f"go_enrich_plot_{comp}_{pf.stem}_{pf.suffix.lstrip('.')}"] = rel_artifact(final_dir, pf, suffix)
                    else:
                        go_plot = final_dir / f"{args.prefix}.{output_tag}.{comp}.GO_enrich.svg"
                        write_enrichment_bar_svg(go_plot, rows, f"{comp} GO enrichment", args.top_pathways, "#2f7ed8")
                        plot_files.append(go_plot)
                        artifacts[f"go_enrich_plot_{comp}"] = rel_artifact(final_dir, go_plot, "svg")
                    
                    total_terms += min(len(rows), args.top_pathways)
                    artifacts[f"go_enrich_{comp}"] = rel_artifact(final_dir, go_file, "tsv")
            if run_kegg:
                if not args.gene2ko:
                    raise ValueError(
                        "KEGG enrichment requires gene2ko background file. Please upload gene2ko "
                        "or run annotation to generate gene2ko."
                    )
                gene2ko_path = Path(args.gene2ko)
                if not gene2ko_path.exists():
                    raise FileNotFoundError(f"gene2ko file not found: {gene2ko_path}")
                gene_to_kos, ko_to_genes = parse_gene2ko(gene2ko_path)
                if not gene_to_kos:
                    raise ValueError(f"No valid KO annotations found in {gene2ko_path}")
                if not annotated_background_genes:
                    annotated_background_genes = len(gene_to_kos)
                ko_to_pathways: dict[str, set[str]] = {}
                pathway_meta: dict[str, dict[str, str]] = {}
                ko_names: dict[str, str] = {}
                gene_to_kegg_terms = gene_to_kos
                kegg_term_to_genes = ko_to_genes
                if args.kegg_level == "pathway":
                    ko_to_pathways, pathway_meta, ko_names = load_kegg_pathway_maps(
                        kegg_cache_dir(args, output_dir),
                        organism=args.kegg_organism,
                        allow_network=not args.no_kegg_network,
                    )
                    if not ko_to_pathways:
                        raise ValueError(
                            "KEGG pathway mapping is unavailable. Allow KEGG REST cache download or provide an existing "
                            "data/kegg_pathway_cache/link_pathway_ko.tsv cache file."
                        )
                    gene_to_kegg_terms, kegg_term_to_genes = build_gene_pathway_maps(gene_to_kos, ko_to_pathways)
                    if not gene_to_kegg_terms:
                        raise ValueError("gene2ko has KOs, but none map to KEGG pathways.")
                for de_file in de_files:
                    genes = parse_de_genes(de_file)
                    comp = comparison_name(de_file, args.prefix)
                    krows = enrich_term_table(genes, gene_to_kegg_terms, kegg_term_to_genes)
                    if args.kegg_level == "pathway":
                        metrics["kegg_pathway_mapped_terms"] = int(metrics.get("kegg_pathway_mapped_terms", 0)) + annotate_kegg_pathway_rows(krows, pathway_meta)
                        metrics["kegg_raw_pathways"] = int(metrics.get("kegg_raw_pathways", 0)) + len(krows)
                        raw_kegg_file = final_dir / f"{args.prefix}.{output_tag}.{comp}.KEGG_enrich.raw.txt"
                        write_kegg_table(raw_kegg_file, krows, max(args.top_pathways, len(krows)))
                        kegg_files.append(raw_kegg_file)
                        artifacts[f"kegg_enrich_{comp}_raw"] = rel_artifact(final_dir, raw_kegg_file, "tsv")
                        krows = filter_kegg_rows(krows, args.kegg_filter_scope)
                        metrics["kegg_filtered_pathways"] = int(metrics.get("kegg_filtered_pathways", 0)) + len(krows)

                    kegg_file = final_dir / f"{args.prefix}.{output_tag}.{comp}.KEGG_enrich.txt"
                    write_kegg_table(kegg_file, krows, args.top_pathways)
                    kegg_files.append(kegg_file)
                    
                    # Generate plots based on plot_type
                    if args.plot_type == "r_plot":
                        rscript = Path(args.rscript) if args.rscript else Path(RNASEQ_RSCRIPT)
                        stage_name = "rnaseq_kegg_enrich" if "kegg" in args.enrich_type else output_tag
                        kegg_plot_files = write_enrichment_r_plot(
                            kegg_file, final_dir, comp, "KEGG", rscript, args.top_pathways, args.prefix, stage_name
                        )
                        plot_files.extend(kegg_plot_files)
                        for pf in kegg_plot_files:
                            suffix = "pdf" if pf.suffix == ".pdf" else "png"
                            artifacts[f"kegg_enrich_plot_{comp}_{pf.stem}_{pf.suffix.lstrip('.')}"] = rel_artifact(final_dir, pf, suffix)
                    else:
                        kegg_plot = final_dir / f"{args.prefix}.{output_tag}.{comp}.KEGG_enrich.svg"
                        write_enrichment_bar_svg(kegg_plot, krows, f"{comp} KEGG enrichment", args.top_pathways, "#36a166", "KEGG")
                        plot_files.append(kegg_plot)
                        artifacts[f"kegg_enrich_plot_{comp}"] = rel_artifact(final_dir, kegg_plot, "svg")
                    
                    total_kegg_terms += min(len(krows), args.top_pathways)
                    artifacts[f"kegg_enrich_{comp}"] = rel_artifact(final_dir, kegg_file, "tsv")

                    if args.kegg_level == "pathway":
                        hit_file = final_dir / f"{args.prefix}.{output_tag}.{comp}.KEGG_pathway_gene_hits.txt"
                        write_kegg_hit_table(hit_file, krows, gene_to_kos, ko_to_pathways, pathway_meta, ko_names, args.top_pathways)
                        kegg_files.append(hit_file)
                        artifacts[f"kegg_enrich_{comp}_pathway_gene_hits"] = rel_artifact(final_dir, hit_file, "tsv")
                        mapper_organism = args.kegg_organism if args.kegg_highlight_mode in {"auto", "organism"} else ""
                        if args.kegg_highlight_mode == "auto" and not args.kegg_organism:
                            mapper_organism = ""
                        mapper_file = final_dir / f"{args.prefix}.{output_tag}.{comp}.KEGG_pathway_mapper.tsv"
                        write_kegg_mapper_files(
                            mapper_file, krows, gene_to_kos, ko_to_pathways, pathway_meta, mapper_organism, args.top_pathways
                        )
                        kegg_files.append(mapper_file)
                        artifacts[f"kegg_enrich_{comp}_pathway_mapper"] = rel_artifact(final_dir, mapper_file, "tsv")

                        # pathview renders native KEGG pathway images and may access KEGG online assets.
                        # In no-network validation, keep deterministic table/mapper outputs and skip it.
                        if args.skip_pathview or args.no_kegg_network:
                            metrics["pathview_skipped"] = True
                        else:
                            de_results_file = Path(
                                str(de_file)
                                .replace(".family_DE_genes.txt", ".family_DE_results.txt")
                                .replace(".DE_genes.txt", ".DE_results.txt")
                            )
                            for pv_file in run_pathview_kegg_plots(
                                krows,
                                genes,
                                de_results_file,
                                gene_to_kos,
                                final_dir,
                                args.prefix,
                                output_tag,
                                comp,
                                args.rscript,
                                min(args.top_pathways, 10),
                            ):
                                plot_files.append(pv_file)
                                ko_match = re.search(r"\.(ko\d{5})$", pv_file.stem)
                                ko_tag = ko_match.group(1) if ko_match else pv_file.stem
                                artifacts[f"kegg_enrich_{comp}_pathway_map_{pv_file.suffix.lstrip('.')}__{ko_tag}"] = rel_artifact(
                                    final_dir, pv_file, "image" if pv_file.suffix == ".png" else "pdf"
                                )

        elif use_orgdb:
            if run_kegg:
                raise ValueError(
                    "KEGG enrichment uses KEGG organism code and KEGG ID mapping, not OrgDb keyType directly. "
                    "Please use gene2ko background (uploaded or from annotation) for KEGG, or run GO only."
                )
            args.background_source_effective = "builtin_orgdb"
            for de_file in de_files:
                total_de_genes += len(parse_de_genes(de_file))
            if run_go:
                go_files, total_terms, _ = run_orgdb_enrichment(de_files, final_dir, output_dir, args)
            for go_file in go_files:
                comp = go_file.name
                comp = comp.replace(f"{args.prefix}.{output_tag}.", "").replace(".GO_enrich.txt", "")
                artifacts[f"go_enrich_{comp}"] = rel_artifact(final_dir, go_file, "tsv")
                try:
                    rows = []
                    with go_file.open("r", encoding="utf-8") as handle:
                        reader = csv.DictReader(handle, delimiter="\t")
                        rows = [dict(row) for row in reader]
                    
                    # Generate plots based on plot_type
                    if args.plot_type == "r_plot":
                        rscript = Path(args.rscript) if args.rscript else Path(RNASEQ_RSCRIPT)
                        stage_name = output_tag  # Use output_tag for OrgDb mode
                        go_plot_files = write_enrichment_r_plot(
                            go_file, final_dir, comp, "GO", rscript, args.top_pathways, args.prefix, stage_name
                        )
                        plot_files.extend(go_plot_files)
                        for pf in go_plot_files:
                            suffix = "pdf" if pf.suffix == ".pdf" else "png"
                            artifacts[f"go_enrich_plot_{comp}_{pf.stem}_{pf.suffix.lstrip('.')}"] = rel_artifact(final_dir, pf, suffix)
                    else:
                        go_plot = final_dir / f"{args.prefix}.{output_tag}.{comp}.GO_enrich.svg"
                        write_enrichment_bar_svg(go_plot, rows, f"{comp} GO enrichment", args.top_pathways, "#2f7ed8")
                        plot_files.append(go_plot)
                        artifacts[f"go_enrich_plot_{comp}"] = rel_artifact(final_dir, go_plot, "svg")
                except Exception as plot_exc:
                    print(f"Warning: failed to draw GO plot for {comp}: {plot_exc}", file=sys.stderr)
            annotated_background_genes = 0

        else:
            raise ValueError(
                "Background annotation is required. Upload a gene2go file, explicitly choose a supported built-in "
                "OrgDb species, or run a separate background annotation task."
            )

        report_path = final_dir / f"{args.prefix}.{stage_id}_report.md"
        metrics.update(
            {
                "de_genes_input": total_de_genes,
                "go_enriched_terms": total_terms,
                "kegg_enriched_pathways": total_kegg_terms,
                "comparisons": len(de_files),
                "annotated_background_genes": annotated_background_genes,
            }
        )
        write_report(report_path, args, metrics, go_files, kegg_files, plot_files)
        artifacts["report"] = rel_artifact(final_dir, report_path, "markdown")
        commands_file, env_file, checksums_file = write_reproducibility(
            output_dir,
            sys.argv,
            [*de_files, *go_files, *kegg_files, Path(args.gene2go) if args.gene2go else Path(), Path(args.gene2ko) if args.gene2ko else Path(), report_path],
        )
        artifacts["commands"] = {
            "path": str(commands_file.relative_to(final_dir.parent)),
            "full_path": abs_text(commands_file),
            "type": "shell",
        }
        artifacts["environment"] = {
            "path": str(env_file.relative_to(final_dir.parent)),
            "full_path": abs_text(env_file),
            "type": "yaml",
        }
        artifacts["checksums"] = {
            "path": str(checksums_file.relative_to(final_dir.parent)),
            "full_path": abs_text(checksums_file),
            "type": "sha256",
        }
        artifacts["manifest"] = {
            "path": f"final/{manifest_path.name}",
            "full_path": abs_text(manifest_path),
            "type": "json",
        }

        manifest = build_manifest(args, "success", start_time, time.time() - start, final_dir, artifacts, metrics)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"RNA-seq enrichment completed: {manifest_path}")
        return 0

    except Exception as exc:
        error = str(exc)
        manifest = build_manifest(
            args,
            "failed",
            start_time,
            time.time() - start,
            final_dir,
            artifacts,
            metrics,
            error=error,
        )
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"RNA-seq enrichment failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
