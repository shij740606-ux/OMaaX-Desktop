#!/usr/bin/env python3
"""Score RNA-seq candidate gene families from PlantAI result artifacts.

This script implements the analysis-suitability scoring scheme:
RNA-seq response evidence x family-analysis suitability, with split outputs for
analysis-ready families, expression-only responses, singleton candidates, broad
core responses, and manual-review families.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).absolute().parents[2]
if str(PROJECT_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(PROJECT_ROOT))

try:
    from cli.run_rnaseq_candidate_family import (
        canonical_gene_id,
        classify_hit,
        parse_float,
        parse_interproscan_tsv,
    )
except Exception:
    from run_rnaseq_candidate_family import (  # type: ignore
        canonical_gene_id,
        classify_hit,
        parse_float,
        parse_interproscan_tsv,
    )


EVIDENCE_WEIGHTS = {
    "differential_response_score": 0.35,
    "family_response_score": 0.25,
    "direction_consistency_score": 0.15,
    "wgcna_support_score": 0.15,
    "enrichment_support_score": 0.10,
}


@dataclass
class SampleInfo:
    sample: str
    condition: str = ""
    time: str = ""
    group_role: str = ""


@dataclass
class DeRecord:
    gene_id: str
    comparison: str
    log2fc: float | None
    pvalue: float | None
    padj: float | None
    regulation: str = ""


@dataclass
class WgcnaSupport:
    score: float | None = None
    module: str = ""
    trait: str = ""
    module_cor: float | None = None
    module_p: float | None = None
    module_genes: int = 0
    module_ratio: float | None = None
    cor_score: float | None = None
    p_score: float | None = None
    membership_score: float | None = None


@dataclass
class FunctionalSupport:
    go_score: float | None = None
    kegg_score: float | None = None
    fgsea_score: float | None = None
    enrichment_score: float | None = None
    go_hit_genes: int = 0
    kegg_hit_genes: int = 0
    fgsea_hit_genes: int = 0
    best_go_padj: float | None = None
    best_kegg_padj: float | None = None
    best_fgsea_padj: float | None = None
    best_abs_nes: float | None = None
    specific_pathway_score: float | None = None
    family_hit_ratio_score: float | None = None
    enrichment_significance_score: float | None = None
    broad_pathway_score: float | None = None
    broad_pathway_hits: int = 0
    total_support_terms: int = 0
    specific_pathway_count: int = 0
    only_generic_pathway_support: bool = False
    support_labels: list[str] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def clamp_score(value: float | None) -> float | None:
    if value is None or math.isnan(value):
        return None
    return max(0.0, min(100.0, value))


def safe_neglog_score(value: float | None, denominator: float) -> float:
    if value is None:
        return 0.0
    value = max(value, 1e-300)
    return min(-math.log10(value) / denominator, 1.0) * 100.0


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def fmt_num(value: Any, digits: int = 4) -> str:
    if value is None or value == "":
        return ""
    try:
        val = float(value)
    except Exception:
        return str(value)
    if math.isnan(val):
        return ""
    if abs(val) >= 1000 or (abs(val) > 0 and abs(val) < 0.001):
        return f"{val:.3e}"
    return f"{val:.{digits}f}".rstrip("0").rstrip(".")


def split_genes(text: str) -> set[str]:
    genes: set[str] = set()
    for item in re.split(r"[/;,|\s]+", text or ""):
        gene = canonical_gene_id(item.strip())
        if gene:
            genes.add(gene)
    return genes


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader)


def write_tsv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            clean = {}
            for key in fieldnames:
                value = row.get(key, "")
                if isinstance(value, float):
                    clean[key] = fmt_num(value, 6)
                elif isinstance(value, list):
                    clean[key] = ";".join(str(v) for v in value)
                else:
                    clean[key] = value
            writer.writerow(clean)


def simple_yaml_samples(path: Path) -> dict[str, SampleInfo]:
    samples: dict[str, SampleInfo] = {}
    if not path.exists():
        return samples
    current: str | None = None
    in_samples = False
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            if raw.startswith("samples:"):
                in_samples = True
                continue
            if not in_samples:
                continue
            match = re.match(r"^\s{2}([^:\s][^:]*):\s*$", raw)
            if match:
                current = match.group(1).strip().strip("'\"")
                samples[current] = SampleInfo(sample=current)
                continue
            if current:
                kv = re.match(r"^\s{4}([A-Za-z0-9_]+):\s*(.*?)\s*$", raw)
                if kv:
                    key = kv.group(1)
                    value = kv.group(2).strip().strip("'\"")
                    if key in {"condition", "time", "group_role"}:
                        setattr(samples[current], key, value)
    return samples


def read_samples(path: Path) -> dict[str, SampleInfo]:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore

        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            data = yaml.safe_load(handle) or {}
        result: dict[str, SampleInfo] = {}
        for sample, info in (data.get("samples") or {}).items():
            info = info or {}
            result[str(sample)] = SampleInfo(
                sample=str(sample),
                condition=str(info.get("condition", "")),
                time=str(info.get("time", "")),
                group_role=str(info.get("group_role", "")),
            )
        return result
    except Exception:
        return simple_yaml_samples(path)


def infer_input_dir(workspace: Path) -> Path:
    project_root = workspace.parents[1] if len(workspace.parents) > 1 else workspace.parent
    candidate = project_root / "input" / workspace.name
    if candidate.exists():
        return candidate
    alt = workspace.parent.parent / "input" / workspace.name
    return alt if alt.exists() else workspace


def comparison_from_de_path(path: Path, prefix: str) -> str:
    name = path.name
    name = name.replace(f"{prefix}.rnaseq_de.", "")
    return name.replace(".DE_results.txt", "").replace(".DE_genes.txt", "")


def read_de_records(final_dir: Path, prefix: str, padj_cutoff: float, log2fc_cutoff: float, include_lrt: bool) -> dict[str, dict[str, DeRecord]]:
    records: dict[str, dict[str, DeRecord]] = {}
    for path in sorted(final_dir.glob(f"{prefix}.rnaseq_de.*.DE_results.txt")):
        comparison = comparison_from_de_path(path, prefix)
        if not include_lrt and comparison.endswith("_lrt"):
            continue
        comp_records: dict[str, DeRecord] = {}
        for row in read_tsv(path):
            gene = canonical_gene_id(row.get("gene_id", ""))
            if not gene:
                continue
            log2fc = parse_float(row.get("log2FoldChange"))
            padj = parse_float(row.get("padj"))
            pvalue = parse_float(row.get("pvalue"))
            regulation = (row.get("regulation") or row.get("Regulation") or "").lower()
            if not regulation and padj is not None and log2fc is not None:
                if padj < padj_cutoff and abs(log2fc) >= log2fc_cutoff:
                    regulation = "up" if log2fc > 0 else "down"
                else:
                    regulation = "not"
            comp_records[gene] = DeRecord(gene, comparison, log2fc, pvalue, padj, regulation)
        records[comparison] = comp_records
    return records


def read_tpm_matrix(path: Path) -> tuple[list[str], dict[str, dict[str, float]]]:
    if not path.exists():
        return [], {}
    matrix: dict[str, dict[str, float]] = {}
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames:
            return [], {}
        sample_names = [x for x in reader.fieldnames if x != "gene_id"]
        for row in reader:
            gene = canonical_gene_id(row.get("gene_id", ""))
            if not gene:
                continue
            matrix[gene] = {sample: parse_float(row.get(sample)) or 0.0 for sample in sample_names}
    return sample_names, matrix


def samples_for_comparison(comparison: str, samples: dict[str, SampleInfo], matrix_samples: list[str]) -> list[str]:
    if not samples:
        return matrix_samples
    if comparison.endswith("_lrt"):
        return [s for s in matrix_samples if s in samples] or matrix_samples
    left = ""
    right = ""
    if "_vs_" in comparison:
        left, right = comparison.split("_vs_", 1)
    selected: list[str] = []
    for sample in matrix_samples:
        info = samples.get(sample)
        if not info:
            continue
        values = {info.time, info.condition, info.group_role}
        if left in values or right in values:
            selected.append(sample)
    return selected or [s for s in matrix_samples if s in samples] or matrix_samples


def is_expressed(gene: str, selected_samples: list[str], matrix: dict[str, dict[str, float]], threshold: float) -> bool:
    values = matrix.get(gene)
    if not values:
        return False
    return any(values.get(sample, 0.0) > threshold for sample in selected_samples)


def family_map_from_interpro(interproscan_tsv: Path) -> dict[str, set[str]]:
    family_genes: dict[str, set[str]] = defaultdict(set)
    hits = parse_interproscan_tsv(interproscan_tsv, set())
    for hit in hits:
        family = classify_hit(hit)
        if family:
            family_genes[family].add(hit.gene_id)
    return dict(family_genes)


def family_map_from_gene_annotations(path: Path) -> dict[str, set[str]]:
    family_genes: dict[str, set[str]] = defaultdict(set)
    for row in read_tsv(path):
        gene = canonical_gene_id(row.get("gene_id", ""))
        if not gene:
            continue
        for family in (row.get("candidate_families") or "").split(";"):
            family = family.strip()
            if family:
                family_genes[family].add(gene)
    return dict(family_genes)


def read_wgcna_tables(final_dir: Path, prefix: str) -> tuple[dict[str, str], dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    gene_modules: dict[str, str] = {}
    module_path = final_dir / f"{prefix}.rnaseq_wgcna.gene_modules.tsv"
    for row in read_tsv(module_path):
        gene = canonical_gene_id(row.get("gene_id", ""))
        module = row.get("module", "")
        if gene and module:
            gene_modules[gene] = module

    def read_matrix(path: Path) -> dict[str, dict[str, float]]:
        values: dict[str, dict[str, float]] = {}
        if not path.exists():
            return values
        with path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
            reader = csv.reader(handle, delimiter="\t")
            try:
                header = next(reader)
            except StopIteration:
                return values
            traits = header[1:]
            for row in reader:
                if not row:
                    continue
                module = row[0].replace("ME", "", 1)
                values[module] = {}
                for trait, raw in zip(traits, row[1:]):
                    parsed = parse_float(raw)
                    if parsed is not None:
                        values[module][trait] = parsed
        return values

    cor = read_matrix(final_dir / f"{prefix}.rnaseq_wgcna.module_trait_correlation.tsv")
    pval = read_matrix(final_dir / f"{prefix}.rnaseq_wgcna.module_trait_pvalue.tsv")
    return gene_modules, cor, pval


def infer_trait(comparison: str, samples: dict[str, SampleInfo], available_traits: set[str], requested: str = "") -> str:
    if requested and requested in available_traits:
        return requested
    if "Salt" in available_traits:
        return "Salt"
    if "_vs_" in comparison:
        left, _right = comparison.split("_vs_", 1)
        conditions = {info.condition for info in samples.values() if info.time == left}
        for condition in conditions:
            if condition in available_traits:
                return condition
        if left in available_traits:
            return left
    return sorted(available_traits)[0] if available_traits else ""


def calc_wgcna_support(
    family_genes: set[str],
    expressed_genes: set[str],
    comparison: str,
    samples: dict[str, SampleInfo],
    gene_modules: dict[str, str],
    module_cor: dict[str, dict[str, float]],
    module_p: dict[str, dict[str, float]],
    requested_trait: str,
) -> WgcnaSupport:
    if not gene_modules or not module_cor:
        return WgcnaSupport(score=None)
    genes_for_module = expressed_genes or family_genes
    module_counts = Counter(
        gene_modules[gene]
        for gene in genes_for_module
        if gene in gene_modules and gene_modules[gene].lower() != "grey"
    )
    if not module_counts:
        return WgcnaSupport(score=0.0, module_ratio=0.0)
    traits = {trait for data in module_cor.values() for trait in data}
    trait = infer_trait(comparison, samples, traits, requested_trait)

    best_module = ""
    best_count = -1
    best_abs_cor = -1.0
    for module, count in module_counts.items():
        cor = module_cor.get(module, {}).get(trait)
        abs_cor = abs(cor) if cor is not None else 0.0
        if count > best_count or (count == best_count and abs_cor > best_abs_cor):
            best_module = module
            best_count = count
            best_abs_cor = abs_cor

    cor = module_cor.get(best_module, {}).get(trait)
    pval = module_p.get(best_module, {}).get(trait)
    denominator = max(len(genes_for_module), 1)
    ratio = best_count / denominator
    cor_score = min((abs(cor) if cor is not None else 0.0) / 0.8, 1.0) * 100.0
    p_score = safe_neglog_score(pval, 5.0)
    membership_score = min(ratio / 0.5, 1.0) * 100.0
    score = 0.45 * cor_score + 0.25 * p_score + 0.30 * membership_score
    return WgcnaSupport(
        score=score,
        module=best_module,
        trait=trait,
        module_cor=cor,
        module_p=pval,
        module_genes=best_count,
        module_ratio=ratio,
        cor_score=cor_score,
        p_score=p_score,
        membership_score=membership_score,
    )


def comparison_from_enrich_path(path: Path, prefix: str, kind: str) -> str:
    name = path.name
    if kind == "go":
        return name.replace(f"{prefix}.rnaseq_go_enrich.", "").replace(".GO_enrich.txt", "")
    return name.replace(f"{prefix}.rnaseq_kegg_enrich.", "").replace(".KEGG_enrich.txt", "").replace(".raw", "")


def read_go_support(final_dir: Path, prefix: str, padj_cutoff: float) -> dict[str, list[dict[str, Any]]]:
    by_comp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(final_dir.glob(f"{prefix}.rnaseq_go_enrich.*.GO_enrich.txt")):
        comparison = comparison_from_enrich_path(path, prefix, "go")
        for row in read_tsv(path):
            padj = parse_float(row.get("p.adjust") or row.get("padj") or row.get("qvalue"))
            if padj is None or padj >= padj_cutoff:
                continue
            genes = split_genes(row.get("geneID", ""))
            if genes:
                by_comp[comparison].append({
                    "id": row.get("ID", ""),
                    "description": row.get("Description", ""),
                    "padj": padj,
                    "genes": genes,
                })
    return dict(by_comp)


def read_kegg_support(final_dir: Path, prefix: str, padj_cutoff: float) -> dict[str, list[dict[str, Any]]]:
    by_comp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    enrich_by_comp: dict[str, dict[str, tuple[float, str]]] = defaultdict(dict)
    for path in sorted(final_dir.glob(f"{prefix}.rnaseq_kegg_enrich.*.KEGG_enrich.txt")):
        if path.name.endswith(".raw.txt"):
            continue
        comparison = comparison_from_enrich_path(path, prefix, "kegg")
        for row in read_tsv(path):
            pathway = row.get("ID") or row.get("PathwayID") or row.get("pathway") or ""
            padj = parse_float(row.get("p.adjust") or row.get("padj") or row.get("qvalue"))
            if pathway and padj is not None and padj < padj_cutoff:
                enrich_by_comp[comparison][pathway] = (padj, row.get("Description") or row.get("PathwayName") or pathway)

    for path in sorted(final_dir.glob(f"{prefix}.rnaseq_kegg_enrich.*.KEGG_pathway_gene_hits.txt")):
        comparison = path.name.replace(f"{prefix}.rnaseq_kegg_enrich.", "").replace(".KEGG_pathway_gene_hits.txt", "")
        significant = enrich_by_comp.get(comparison, {})
        if not significant:
            continue
        pathway_genes: dict[str, set[str]] = defaultdict(set)
        for row in read_tsv(path):
            pathway = row.get("PathwayID") or row.get("ID") or ""
            if pathway in significant:
                pathway_genes[pathway].update(split_genes(row.get("Genes", "") or row.get("geneID", "")))
        for pathway, genes in pathway_genes.items():
            padj, desc = significant[pathway]
            if genes:
                by_comp[comparison].append({"id": pathway, "description": desc, "padj": padj, "genes": genes})
    return dict(by_comp)


def read_fgsea_support(final_dir: Path, prefix: str, padj_cutoff: float) -> dict[str, list[dict[str, Any]]]:
    by_comp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(final_dir.glob(f"{prefix}.rnaseq_fgsea.*.fgsea_kegg.tsv")):
        comparison = path.name.replace(f"{prefix}.rnaseq_fgsea.", "").replace(".fgsea_kegg.tsv", "")
        for row in read_tsv(path):
            padj = parse_float(row.get("padj"))
            nes = parse_float(row.get("NES"))
            if padj is None or padj >= padj_cutoff or nes is None:
                continue
            genes = split_genes(row.get("leadingEdge", ""))
            if genes:
                by_comp[comparison].append({
                    "id": row.get("pathway", ""),
                    "description": row.get("Description") or row.get("pathway", ""),
                    "padj": padj,
                    "nes": nes,
                    "genes": genes,
                })
    return dict(by_comp)


def ratio_sig_score(hit_genes: set[str], deg_genes: set[str], best_padj: float | None) -> float:
    hit_ratio = len(hit_genes) / max(len(deg_genes), 1)
    ratio_score = min(hit_ratio / 0.5, 1.0) * 100.0
    sig_score = safe_neglog_score(best_padj, 5.0)
    return 0.60 * ratio_score + 0.40 * sig_score


BROAD_PATHWAY_TERMS = [
    "ko01100",
    "ko01110",
    "metabolic pathways",
    "biosynthesis of secondary metabolites",
    "membrane",
    "atp binding",
    "binding",
    "response to stimulus",
    "response to stress",
    "cellular process",
    "catalytic activity",
]

SPECIFIC_PATHWAY_TERMS = [
    "photosynthesis",
    "mapk signaling",
    "porphyrin",
    "protein processing in endoplasmic reticulum",
    "protein processing in er",
    "plant hormone signal transduction",
    "starch",
    "sucrose",
    "phenylpropanoid",
    "glutathione",
    "carotenoid",
    "flavonoid",
    "alpha-linolenic acid",
]


def support_specificity_score(label: str) -> tuple[float, bool]:
    text = str(label).lower()
    if any(term in text for term in BROAD_PATHWAY_TERMS):
        return 30.0, True
    if any(term in text for term in SPECIFIC_PATHWAY_TERMS):
        return 100.0, False
    if any(term in text for term in ["transporter", "protein kinase", "signal", "hydrolase", "transferase"]):
        return 75.0, False
    return 70.0, False


def calc_functional_support(
    family_deg_genes: set[str],
    comparison: str,
    go_terms: dict[str, list[dict[str, Any]]],
    kegg_terms: dict[str, list[dict[str, Any]]],
    fgsea_terms: dict[str, list[dict[str, Any]]],
    mode: str,
) -> FunctionalSupport:
    has_any_source = bool(go_terms or kegg_terms or fgsea_terms)
    if not has_any_source:
        return FunctionalSupport(enrichment_score=None)

    support = FunctionalSupport()
    labels: list[str] = []
    support_term_scores: list[float] = []
    broad_hits = 0

    go_hit: set[str] = set()
    go_padjs: list[float] = []
    for term in go_terms.get(comparison, []):
        overlap = family_deg_genes & term["genes"]
        if overlap:
            go_hit.update(overlap)
            go_padjs.append(term["padj"])
            label = f"GO:{term.get('id')}:{term.get('description')}"
            labels.append(label)
            term_score, is_broad = support_specificity_score(label)
            support_term_scores.append(term_score)
            broad_hits += int(is_broad)
    if go_terms:
        support.go_hit_genes = len(go_hit)
        support.best_go_padj = min(go_padjs) if go_padjs else None
        support.go_score = ratio_sig_score(go_hit, family_deg_genes, support.best_go_padj) if go_hit else 0.0

    kegg_hit: set[str] = set()
    kegg_padjs: list[float] = []
    for term in kegg_terms.get(comparison, []):
        overlap = family_deg_genes & term["genes"]
        if overlap:
            kegg_hit.update(overlap)
            kegg_padjs.append(term["padj"])
            label = f"KEGG:{term.get('id')}:{term.get('description')}"
            labels.append(label)
            term_score, is_broad = support_specificity_score(label)
            support_term_scores.append(term_score)
            broad_hits += int(is_broad)
    if kegg_terms:
        support.kegg_hit_genes = len(kegg_hit)
        support.best_kegg_padj = min(kegg_padjs) if kegg_padjs else None
        support.kegg_score = ratio_sig_score(kegg_hit, family_deg_genes, support.best_kegg_padj) if kegg_hit else 0.0

    fgsea_hit: set[str] = set()
    fgsea_padjs: list[float] = []
    fgsea_nes: list[float] = []
    for term in fgsea_terms.get(comparison, []):
        overlap = family_deg_genes & term["genes"]
        if overlap:
            fgsea_hit.update(overlap)
            fgsea_padjs.append(term["padj"])
            fgsea_nes.append(abs(term["nes"]))
            label = f"fgsea:{term.get('id')}:{term.get('description')}"
            labels.append(label)
            term_score, is_broad = support_specificity_score(label)
            support_term_scores.append(term_score)
            broad_hits += int(is_broad)
    if fgsea_terms:
        support.fgsea_hit_genes = len(fgsea_hit)
        support.best_fgsea_padj = min(fgsea_padjs) if fgsea_padjs else None
        support.best_abs_nes = max(fgsea_nes) if fgsea_nes else None
        if fgsea_hit and support.best_abs_nes is not None:
            nes_score = min(support.best_abs_nes / 3.0, 1.0) * 100.0
            sig_score = safe_neglog_score(support.best_fgsea_padj, 5.0)
            support.fgsea_score = 0.70 * nes_score + 0.30 * sig_score
        else:
            support.fgsea_score = 0.0

    values = [v for v in [support.go_score, support.kegg_score, support.fgsea_score] if v is not None]
    all_hit = set().union(go_hit, kegg_hit, fgsea_hit)
    best_padj_values = [v for v in [support.best_go_padj, support.best_kegg_padj, support.best_fgsea_padj] if v is not None]
    support.family_hit_ratio_score = min((len(all_hit) / max(len(family_deg_genes), 1)) / 0.5, 1.0) * 100.0 if family_deg_genes else 0.0
    support.enrichment_significance_score = safe_neglog_score(min(best_padj_values) if best_padj_values else None, 5.0)
    support.total_support_terms = len(support_term_scores)
    support.broad_pathway_hits = broad_hits
    support.broad_pathway_score = (broad_hits / max(len(support_term_scores), 1)) * 100.0 if support_term_scores else 0.0
    support.specific_pathway_count = sum(1 for score in support_term_scores if score >= 70.0)
    support.only_generic_pathway_support = bool(support_term_scores and broad_hits == len(support_term_scores))
    if support_term_scores:
        support.specific_pathway_score = max(support_term_scores)
    elif values:
        support.specific_pathway_score = 0.0
    if values:
        support.enrichment_score = (
            0.50 * float(support.specific_pathway_score or 0.0)
            + 0.30 * float(support.family_hit_ratio_score or 0.0)
            + 0.20 * float(support.enrichment_significance_score or 0.0)
        )
    support.support_labels = labels[:5]
    return support


def evidence_score(parts: dict[str, float | None]) -> tuple[float, float]:
    numerator = 0.0
    denominator = 0.0
    for key, weight in EVIDENCE_WEIGHTS.items():
        value = parts.get(key)
        if value is None:
            continue
        numerator += value * weight
        denominator += weight
    if denominator <= 0:
        return 0.0, 0.0
    return numerator / denominator, denominator


def top_direction(up: int, down: int) -> str:
    total = up + down
    if total <= 0:
        return "None"
    if up > down and up / total >= 0.60:
        return "Up"
    if down > up and down / total >= 0.60:
        return "Down"
    return "Mixed"


def database_source(family: str) -> str:
    if family.startswith("Pfam/InterPro:"):
        return "Pfam/InterPro"
    if family.startswith("InterPro:"):
        return "InterPro"
    return "curated_rule"


def annotation_specificity(family: str) -> tuple[float, str, bool]:
    text = family.lower()
    if any(token in text for token in ["domain of unknown function", "unknown protein", " duf", "(duf"]):
        return 0.30, "duf_unknown_or_putative", True
    if any(token in text for token in ["putative", "ig-like", "act-like", "domain profile", "had-like"]):
        return 0.40, "duf_unknown_or_putative", True
    if any(token in text for token in ["superfamily", "large family", "broad domain group"]):
        return 0.40, "superfamily_domain", True
    if any(token in text for token in ["nad(p)h-binding", "binding domain", "generic binding"]):
        return 0.50, "broad_domain", True
    if any(token in text for token in ["hydrolase", "transferase"]):
        return 0.80, "enzyme_family_with_clear_function", False
    if any(token in text for token in ["transporter family", "protein kinase/rlk", "lrr receptor/signaling"]):
        return 0.75, "transport_or_signal_family", False
    if family.startswith("Pfam/InterPro:") or family.startswith("InterPro:"):
        return 0.85, "specific_domain_or_protein", False
    return 1.00, "curated_specific_family", False


def superfamily_annotation_score(family: str) -> float:
    text = family.lower()
    if any(token in text for token in ["superfamily", "large family", "broad domain group"]):
        return 100.0
    if any(token in text for token in ["protein kinase/rlk", "lrr receptor/signaling", "ubiquitin system", "ppr/pentatricopeptide", "aaa atpase"]):
        return 85.0
    if any(token in text for token in ["group 1", "transferase", "transporter family", "hydrolase", "domain profile"]):
        return 60.0
    return 0.0


GENERIC_ANNOTATION_TERMS = [
    "response to stress",
    "response to stimulus",
    "response to abiotic stimulus",
    "response to biotic stimulus",
    "defense response",
    "stress response",
    "cellular response to stimulus",
]


def generic_annotation_ratio(labels: list[str]) -> float:
    if not labels:
        return 0.0
    generic = 0
    for label in labels:
        low = str(label).lower()
        if any(term in low for term in GENERIC_ANNOTATION_TERMS):
            generic += 1
    return generic / max(len(labels), 1)


def percentile_lookup(values: dict[str, int]) -> dict[str, float]:
    ordered = sorted(values.values())
    n = len(ordered)
    if n == 0:
        return {}
    result: dict[str, float] = {}
    for key, value in values.items():
        le_count = 0
        for item in ordered:
            if item <= value:
                le_count += 1
            else:
                break
        result[key] = le_count / n
    return result


def reliability_from_counts(deg_genes: int, expressed_genes: int) -> tuple[float, float, float]:
    deg_reliability = min(math.log2(deg_genes + 1) / math.log2(6), 1.0) if deg_genes > 0 else 0.0
    expressed_reliability = min(math.log2(expressed_genes + 1) / math.log2(11), 1.0) if expressed_genes > 0 else 0.0
    small_family_reliability = deg_reliability * expressed_reliability
    return deg_reliability, expressed_reliability, small_family_reliability


def family_size_suitability(total_family_genes: int) -> float:
    if total_family_genes < 5:
        return 0.0
    if total_family_genes <= 7:
        return 70.0
    if total_family_genes <= 30:
        return 100.0
    if total_family_genes <= 60:
        return 85.0
    if total_family_genes <= 100:
        return 60.0
    return 30.0


def phylogeny_feasibility(total_family_genes: int, annotation_clarity_score: float, redundant: bool = False) -> float:
    if redundant:
        return 20.0
    if annotation_clarity_score < 40.0:
        return 30.0
    if 8 <= total_family_genes <= 60:
        return 100.0
    if 5 <= total_family_genes < 8:
        return 70.0
    if total_family_genes > 60:
        return 50.0
    return 20.0


def downstream_interpretability(
    deg_member_support: float,
    annotation_clarity_score: float,
    wgcna_score: Any,
    enrichment_score: Any,
) -> float:
    return (
        0.35 * deg_member_support
        + 0.25 * annotation_clarity_score
        + 0.20 * float(wgcna_score or 0.0)
        + 0.20 * float(enrichment_score or 0.0)
    )


def calculate_score_cap(row: dict[str, Any]) -> tuple[float, str]:
    caps: list[tuple[float, str]] = []
    if int(row.get("total_family_genes") or 0) < 5:
        caps.append((50.0, "total_family_genes_lt_5"))
    if int(row.get("deg_family_genes") or 0) < 3:
        caps.append((55.0, "deg_family_genes_lt_3"))
    if float(row.get("annotation_clarity_score") or 0.0) < 40.0:
        caps.append((60.0, "annotation_clarity_lt_40"))
    if row.get("redundant_family_flag"):
        caps.append((50.0, "redundant_family"))
    if row.get("superfamily_flag") and float(row.get("family_response_ratio") or 0.0) < 0.20:
        caps.append((65.0, "superfamily_low_response_ratio"))
    if row.get("only_generic_pathway_support"):
        caps.append((70.0, "only_generic_pathway_support"))
    if row.get("manual_review_flag"):
        caps.append((75.0, "manual_review"))
    if not caps:
        return 100.0, ""
    cap = min(value for value, _ in caps)
    reasons = ";".join(reason for value, reason in caps if value == cap)
    return cap, reasons


class DisjointSet:
    def __init__(self, items: list[str]) -> None:
        self.parent = {item: item for item in items}

    def find(self, item: str) -> str:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, a: str, b: str) -> None:
        ra = self.find(a)
        rb = self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def annotate_redundancy(rows: list[dict[str, Any]], family_genes: dict[str, set[str]]) -> None:
    families = [str(row["family_id"]) for row in rows]
    row_by_family = {str(row["family_id"]): row for row in rows}
    ds = DisjointSet(families)
    possible: dict[str, set[str]] = defaultdict(set)
    gene_to_families: dict[str, list[str]] = defaultdict(list)
    for family in families:
        for gene in family_genes.get(family, set()):
            gene_to_families[gene].append(family)

    pair_seen: set[tuple[str, str]] = set()
    for fams in gene_to_families.values():
        if len(fams) < 2:
            continue
        fams = sorted(set(fams))
        for i, a in enumerate(fams):
            genes_a = family_genes.get(a, set())
            for b in fams[i + 1:]:
                pair = (a, b)
                if pair in pair_seen:
                    continue
                pair_seen.add(pair)
                genes_b = family_genes.get(b, set())
                union_size = len(genes_a | genes_b)
                if union_size == 0:
                    continue
                jaccard = len(genes_a & genes_b) / union_size
                if jaccard >= 0.80:
                    ds.union(a, b)
                elif jaccard >= 0.50:
                    possible[a].add(b)
                    possible[b].add(a)

    groups: dict[str, list[str]] = defaultdict(list)
    for family in families:
        groups[ds.find(family)].append(family)

    group_index = 1
    for members in sorted(groups.values(), key=lambda xs: sorted(xs)[0]):
        representative = max(
            members,
            key=lambda fam: (
                float(row_by_family[fam].get("pre_redundancy_score") or 0.0),
                int(row_by_family[fam].get("total_family_genes") or 0),
                -len(str(fam)),
            ),
        )
        group_id = f"NRG{group_index:04d}"
        group_index += 1
        for member in members:
            row = row_by_family[member]
            row["non_redundancy_group"] = group_id
            row["representative_family_id"] = representative
            row["redundant_family_flag"] = member != representative
            row["non_redundancy_factor"] = 1.0 if member == representative else 0.50
            row["possible_redundant_families"] = ";".join(sorted(possible.get(member, set()))[:5])


def make_report(
    path: Path,
    args: argparse.Namespace,
    ranking_rows: list[dict[str, Any]],
    per_rows: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> None:
    family_rows = metadata.get("family_level_rows", [])[: args.top_n_report]
    singleton_rows = metadata.get("singleton_rows", [])[: min(args.top_n_report, 15)]
    manual_rows = metadata.get("manual_review_rows", [])[: min(args.top_n_report, 15)]

    def table(rows: list[dict[str, Any]]) -> list[str]:
        lines = [
            "| Rank | Family | Final | Raw | Best comparison | DEG | Scale | Specificity | Redundancy | Broad factor | Flags |",
            "|---:|---|---:|---:|---|---:|---:|---:|---:|---:|---|",
        ]
        for row in rows:
            flags = []
            for key, label in [
                ("singleton_flag", "singleton"),
                ("small_family_flag", "small"),
                ("superfamily_flag", "superfamily"),
                ("generic_annotation_flag", "generic"),
                ("redundant_family_flag", "redundant"),
                ("manual_review_flag", "review"),
            ]:
                if row.get(key) in {True, "True", "true", 1, "1"}:
                    flags.append(label)
            lines.append(
                "| {rank} | {family} | {final} | {raw} | {best_comp} | {deg} | {scale} | {specific} | {nr} | {broad} | {flags} |".format(
                    rank=row.get("rank", ""),
                    family=str(row.get("family_name", "")).replace("|", "\\|"),
                    final=fmt_num(row.get("final_family_score")),
                    raw=fmt_num(row.get("raw_family_score")),
                    best_comp=row.get("best_comparison", ""),
                    deg=row.get("deg_family_genes", ""),
                    scale=fmt_num(row.get("family_scale_factor")),
                    specific=fmt_num(row.get("annotation_specificity_factor")),
                    nr=fmt_num(row.get("non_redundancy_factor")),
                    broad=fmt_num(row.get("broad_penalty_factor")),
                    flags=",".join(flags),
                )
            )
        return lines

    lines = [
        f"# RNA-seq 候选基因家族修订版评分报告 - {args.prefix}",
        "",
        "## 任务信息",
        "",
        f"- Job ID: {args.job_id or metadata.get('job_id', '')}",
        f"- Workspace: `{metadata.get('workspace', '')}`",
        f"- Final directory: `{metadata.get('final_dir', '')}`",
        f"- Prefix: `{args.prefix}`",
        f"- 计分时间: {now_iso()}",
        "",
        "## 评分说明",
        "",
        "本脚本按用户提供的《候选基因家族综合评分说明（修订版）》重算候选家族评分。",
        "",
        "单比较组 evidence_score 使用可用权重重标化：",
        "",
        "`0.40 * 差异响应强度 + 0.25 * 家族响应比例 + 0.20 * 响应方向一致性 + 0.10 * WGCNA模块支持 + 0.05 * 功能富集支持`",
        "",
        "跨比较组汇总采用：",
        "",
        "`raw_family_score = 0.70 * best_score + 0.30 * top2_mean_score`",
        "",
        "若只有一个响应比较组，`top2_mean_score = (best_score + 0) / 2`，避免单时间点响应获得过高家族级分数。",
        "",
        "最终分数采用修订版乘法修正：",
        "",
        "`final_family_score = raw_family_score * family_scale_factor * annotation_specificity_factor * non_redundancy_factor * broad_penalty_factor`",
        "",
        "## 数据完整度",
        "",
        f"- 全部候选条目数: {len(ranking_rows)}",
        f"- family-level response ranking 条目数: {len(metadata.get('family_level_rows', []))}",
        f"- singleton candidate ranking 条目数: {len(metadata.get('singleton_rows', []))}",
        f"- manual review list 条目数: {len(metadata.get('manual_review_rows', []))}",
        f"- 单比较组评分记录数: {len(per_rows)}",
        f"- DE comparisons: {', '.join(metadata.get('comparisons', []))}",
        f"- TPM matrix: `{metadata.get('tpm_matrix', '')}`",
        f"- InterProScan TSV: `{metadata.get('interproscan_tsv', '')}`",
        f"- WGCNA available: {metadata.get('has_wgcna')}",
        f"- GO available: {metadata.get('has_go')}",
        f"- KEGG available: {metadata.get('has_kegg')}",
        f"- fgsea available: {metadata.get('has_fgsea')}",
        "",
        "## Top Family-Level Response Ranking",
        "",
    ]
    lines.extend(table(family_rows))
    lines.extend(["", "## Top Singleton Candidate Ranking", ""])
    lines.extend(table(singleton_rows))
    lines.extend(["", "## Manual Review Preview", ""])
    lines.extend(table(manual_rows))
    lines.extend([
        "",
        "## 主要修订效果",
        "",
        "- 单个 DEG 支撑的条目进入 singleton candidate ranking，并通过 small_family_reliability 和分数上限降低误判。",
        "- 位于家族规模前 10% 的条目标记 superfamily_flag，并按 family size percentile 与注释宽泛程度计算 superfamily_penalty_factor。",
        "- DUF、unknown、putative、domain profile、Ig-like、ACT-like 等泛注释条目通过 annotation_specificity_factor 降低优先级。",
        "- gene set Jaccard >= 0.80 的高度重叠条目被分到同一 non_redundancy_group，非代表条目乘以 0.85。",
        "- 广谱响应不删除条目，只通过 broad_penalty_factor 最多降低 30%。",
        "",
        "## 输出文件",
        "",
        f"- 全部修订版评分表: `{metadata.get('ranking_path', '')}`",
        f"- family-level response ranking: `{metadata.get('family_level_path', '')}`",
        f"- singleton candidate ranking: `{metadata.get('singleton_path', '')}`",
        f"- manual review list: `{metadata.get('manual_review_path', '')}`",
        f"- 单比较组评分明细: `{metadata.get('per_comparison_path', '')}`",
        f"- 报告: `{path}`",
        "",
        "## 注意事项",
        "",
        "- `expressed_family_genes` 基于当前比较相关样本中 TPM > 阈值的家族成员数计算。",
        "- `time_lrt` 默认不纳入评分，因为其不是普通 pairwise 比较；需要时可使用 `--include-lrt`。",
        "- WGCNA 缺失时记为 NA，并通过可用权重重标化处理；不是按 0 分扣除。",
        "- 功能富集支持默认取 GO、KEGG、fgsea 三者最高分。",
        "- 修订版报告应优先解释 family-level response ranking；singleton 表保留强响应单基因候选，但不直接解释为稳定家族响应。",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_analysis_ready_report(
    path: Path,
    args: argparse.Namespace,
    ranking_rows: list[dict[str, Any]],
    per_rows: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> None:
    analysis_rows = metadata.get("analysis_ready_rows", [])[: args.top_n_report]
    expression_rows = metadata.get("expression_response_rows", [])[: min(args.top_n_report, 15)]
    singleton_rows = metadata.get("singleton_rows", [])[: min(args.top_n_report, 15)]
    manual_rows = metadata.get("manual_review_rows", [])[: min(args.top_n_report, 15)]
    core_rows = metadata.get("core_broad_rows", [])[: min(args.top_n_report, 15)]

    def table(rows: list[dict[str, Any]]) -> list[str]:
        lines = [
            "| Rank | Family | Type | Analysis-ready | Raw response | Suitability | Best comparison | DEG | Ratio | Flags |",
            "|---:|---|---|---:|---:|---:|---|---:|---:|---|",
        ]
        for row in rows:
            flags = []
            for key, label in [
                ("singleton_flag", "singleton"),
                ("small_family_flag", "small"),
                ("superfamily_flag", "superfamily"),
                ("generic_annotation_flag", "generic"),
                ("redundant_family_flag", "redundant"),
                ("manual_review_flag", "review"),
                ("core_broad_response_flag", "core_broad"),
                ("time_specific_flag", "time_specific"),
            ]:
                if row.get(key) in {True, "True", "true", 1, "1"}:
                    flags.append(label)
            lines.append(
                "| {rank} | {family} | {rtype} | {final} | {raw} | {suit} | {best_comp} | {deg} | {ratio} | {flags} |".format(
                    rank=row.get("rank", ""),
                    family=str(row.get("family_name", "")).replace("|", "\\|"),
                    rtype=row.get("ranking_type", ""),
                    final=fmt_num(row.get("analysis_ready_score")),
                    raw=fmt_num(row.get("raw_response_family_score")),
                    suit=fmt_num(row.get("family_analysis_suitability_score")),
                    best_comp=row.get("best_comparison", ""),
                    deg=row.get("deg_family_genes", ""),
                    ratio=fmt_num(row.get("family_response_ratio")),
                    flags=",".join(flags),
                )
            )
        return lines

    lines = [
        f"# RNA-seq 候选基因家族评分报告（分析适合度版）- {args.prefix}",
        "",
        "## 任务信息",
        "",
        f"- Job ID: {args.job_id or metadata.get('job_id', '')}",
        f"- Workspace: `{metadata.get('workspace', '')}`",
        f"- Final directory: `{metadata.get('final_dir', '')}`",
        f"- Prefix: `{args.prefix}`",
        f"- 评分时间: {now_iso()}",
        "",
        "## 评分说明",
        "",
        "本脚本按《候选基因家族综合评分说明（分析适合度版）》重算候选家族评分。",
        "",
        "单比较组 evidence_score 使用可用权重重标化：",
        "",
        "`0.35 * 差异响应强度 + 0.25 * 家族响应比例 + 0.15 * 方向一致性 + 0.15 * WGCNA支持 + 0.10 * 功能富集支持`",
        "",
        "跨比较组汇总采用：",
        "",
        "`raw_response_family_score = 0.70 * best_score + 0.30 * top2_mean_score`",
        "",
        "若只有一个响应比较组，`top2_mean_score = (best_score + 0) / 2`，避免单一比较组强响应被过度抬高。",
        "",
        "最终主分数采用分析适合度修正：",
        "",
        "`analysis_ready_score = raw_response_family_score * family_analysis_suitability_score / 100 * broad_penalty_factor * functional_specificity_factor * non_redundancy_factor`",
        "",
        "并按 score_cap 上限处理小家族、DEG 不足、注释不清、冗余、超家族低响应比例和仅泛通路支持等情况。",
        "",
        "## 数据完整度",
        "",
        f"- 全部候选条目数: {len(ranking_rows)}",
        f"- analysis_ready_family 主榜条目数: {len(metadata.get('analysis_ready_rows', []))}",
        f"- expression_response_family 副榜条目数: {len(metadata.get('expression_response_rows', []))}",
        f"- singleton_candidate 条目数: {len(metadata.get('singleton_rows', []))}",
        f"- manual_review_family 条目数: {len(metadata.get('manual_review_rows', []))}",
        f"- core_broad_response_family 条目数: {len(metadata.get('core_broad_rows', []))}",
        f"- time_specific_family 条目数: {len(metadata.get('time_specific_rows', []))}",
        f"- 单比较组评分记录数: {len(per_rows)}",
        f"- DE comparisons: {', '.join(metadata.get('comparisons', []))}",
        f"- TPM matrix: `{metadata.get('tpm_matrix', '')}`",
        f"- InterProScan TSV: `{metadata.get('interproscan_tsv', '')}`",
        f"- WGCNA available: {metadata.get('has_wgcna')}",
        f"- GO available: {metadata.get('has_go')}",
        f"- KEGG available: {metadata.get('has_kegg')}",
        f"- fgsea available: {metadata.get('has_fgsea')}",
        "",
        "## Top Analysis-Ready Families",
        "",
    ]
    lines.extend(table(analysis_rows))
    lines.extend(["", "## Top Expression-Response Families", ""])
    lines.extend(table(expression_rows))
    lines.extend(["", "## Top Singleton Candidates", ""])
    lines.extend(table(singleton_rows))
    lines.extend(["", "## Core Broad-Response Families", ""])
    lines.extend(table(core_rows))
    lines.extend(["", "## Manual Review Preview", ""])
    lines.extend(table(manual_rows))
    lines.extend([
        "",
        "## 主要修订效果",
        "",
        "- analysis_ready_family 主榜要求 total_family_genes >= 5、expressed_family_genes >= 5、deg_family_genes >= 3、family_response_ratio >= 0.20。",
        "- 家族响应比例按 50% 封顶，避免中大型家族过早满分。",
        "- 方向一致性使用 log2(deg+1)/log2(6) 可靠性修正，降低 DEG 数过少时的虚高。",
        "- 功能支持拆分为具体通路、家族命中比例、显著性和 broad_pathway_score，并用 functional_specificity_factor 降低泛通路支撑。",
        "- 广谱响应惩罚最大扣分降为 15%，但 breadth_score 不再 50% 封顶。",
        "- 高度重叠的结构域条目按 gene set Jaccard >= 0.80 合并到同一 non_redundancy_group，非代表条目乘以 0.50。",
        "- 小家族、DEG 不足、注释不清和仅泛通路支持的条目会进入副表或复核表，并应用 score_cap。",
        "",
        "## 输出文件",
        "",
        f"- 全部评分表: `{metadata.get('ranking_path', '')}`",
        f"- analysis_ready_family 主榜: `{metadata.get('analysis_ready_path', '')}`",
        f"- expression_response_family 副榜: `{metadata.get('expression_response_path', '')}`",
        f"- singleton_candidate: `{metadata.get('singleton_path', '')}`",
        f"- manual_review_family: `{metadata.get('manual_review_path', '')}`",
        f"- core_broad_response_family: `{metadata.get('core_broad_path', '')}`",
        f"- time_specific_family: `{metadata.get('time_specific_path', '')}`",
        f"- 单比较组评分明细: `{metadata.get('per_comparison_path', '')}`",
        f"- 报告: `{path}`",
        "",
        "## 注意事项",
        "",
        "- `expressed_family_genes` 基于当前比较相关样本中 TPM > 阈值的家族成员数计算。",
        "- `time_lrt` 默认不纳入评分，因为其不是普通 pairwise 比较；需要时可使用 `--include-lrt`。",
        "- WGCNA 缺失时记为 NA，并通过可用权重重标化处理，不按 0 分扣除。",
        "- singleton 和 expression_response_family 可用于候选基因或表达响应解释，但不默认作为完整基因家族分析主对象。",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score RNA-seq candidate gene families from existing PlantAI outputs.")
    parser.add_argument("-w", "--workspace", required=True, type=Path, help="workspace/<job_id> directory")
    parser.add_argument("--input-dir", type=Path, default=None, help="input/<job_id> directory containing samples.yaml")
    parser.add_argument("--final-dir", type=Path, default=None, help="final directory; default workspace/final")
    parser.add_argument("-p", "--prefix", required=True, help="Output prefix, e.g. Os")
    parser.add_argument("-j", "--job-id", default="", help="Job ID")
    parser.add_argument("-t", "--cpu", type=int, default=1, help="Accepted for pipeline compatibility; not used")
    parser.add_argument("--sample-config", type=Path, default=None, help="samples.yaml path")
    parser.add_argument("--tpm-matrix", type=Path, default=None, help="TPM matrix path")
    parser.add_argument("--interproscan-tsv", type=Path, default=None, help="Whole-proteome InterProScan TSV")
    parser.add_argument("--expression-threshold", type=float, default=1.0, help="TPM threshold for expressed family genes")
    parser.add_argument("--padj", type=float, default=0.05, help="Adjusted P-value threshold for DEG")
    parser.add_argument("--log2fc", type=float, default=1.0, help="Absolute log2FC threshold for DEG")
    parser.add_argument("--include-lrt", action="store_true", help="Include *_lrt comparisons in scoring")
    parser.add_argument("--wgcna-trait", default="", help="Trait column for WGCNA correlation; default infers Salt if available")
    parser.add_argument("--functional-mode", choices=["max", "top2_mean"], default="max", help="How to combine GO/KEGG/fgsea support")
    parser.add_argument("--top-n-report", type=int, default=30, help="Top N families shown in report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = args.workspace.absolute()
    final_dir = (args.final_dir or (workspace / "final")).absolute()
    input_dir = (args.input_dir or infer_input_dir(workspace)).absolute()
    sample_config = (args.sample_config or (input_dir / "samples.yaml")).absolute()
    if args.tpm_matrix:
        tpm_matrix = args.tpm_matrix
    elif (final_dir / f"{args.prefix}.salmon.tpm_matrix.txt").exists():
        tpm_matrix = final_dir / f"{args.prefix}.salmon.tpm_matrix.txt"
    else:
        tpm_matrix = final_dir / f"{args.prefix}.tpm_matrix.txt"
    tpm_matrix = Path(tpm_matrix).absolute()
    interproscan_tsv = (
        args.interproscan_tsv
        or workspace / "annotation" / "analysis" / f"{args.prefix}.interproscan.tsv"
    )
    interproscan_tsv = Path(interproscan_tsv).absolute()

    final_dir.mkdir(parents=True, exist_ok=True)
    samples = read_samples(sample_config)
    matrix_samples, tpm = read_tpm_matrix(tpm_matrix)
    de_by_comp = read_de_records(final_dir, args.prefix, args.padj, args.log2fc, args.include_lrt)
    if not de_by_comp:
        raise FileNotFoundError(f"No DE result files found in {final_dir} for prefix {args.prefix}")

    if interproscan_tsv.exists():
        family_genes = family_map_from_interpro(interproscan_tsv)
    else:
        family_genes = family_map_from_gene_annotations(final_dir / f"{args.prefix}.rnaseq_candidate_family.gene_annotations.tsv")
    if not family_genes:
        raise FileNotFoundError("No family-gene mapping found from InterProScan TSV or candidate family annotations")

    gene_modules, module_cor, module_p = read_wgcna_tables(final_dir, args.prefix)
    go_terms = read_go_support(final_dir, args.prefix, args.padj)
    kegg_terms = read_kegg_support(final_dir, args.prefix, args.padj)
    fgsea_terms = read_fgsea_support(final_dir, args.prefix, args.padj)

    per_rows: list[dict[str, Any]] = []
    ranking_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for family, genes in sorted(family_genes.items()):
        for comparison, comp_records in de_by_comp.items():
            selected_samples = samples_for_comparison(comparison, samples, matrix_samples)
            expressed_genes = {gene for gene in genes if is_expressed(gene, selected_samples, tpm, args.expression_threshold)}
            denominator_genes = expressed_genes if expressed_genes else genes
            sig_records = [
                rec for gene, rec in comp_records.items()
                if gene in genes
                and rec.padj is not None
                and rec.log2fc is not None
                and rec.padj < args.padj
                and abs(rec.log2fc) >= args.log2fc
            ]
            deg_genes = {rec.gene_id for rec in sig_records}
            up = sum(1 for rec in sig_records if (rec.log2fc or 0.0) > 0)
            down = sum(1 for rec in sig_records if (rec.log2fc or 0.0) < 0)
            mean_abs_log2fc = (sum(abs(rec.log2fc or 0.0) for rec in sig_records) / len(sig_records)) if sig_records else None
            median_padj = median([rec.padj for rec in sig_records if rec.padj is not None])

            log2fc_score = min((mean_abs_log2fc or 0.0) / 3.0, 1.0) * 100.0 if sig_records else 0.0
            padj_score = safe_neglog_score(median_padj, 10.0) if sig_records else 0.0
            differential_response_score = 0.60 * log2fc_score + 0.40 * padj_score if sig_records else 0.0
            family_response_ratio = len(deg_genes) / max(len(denominator_genes), 1)
            family_response_score = min(family_response_ratio / 0.50, 1.0) * 100.0
            direction_consistency_raw = (max(up, down) / max(len(sig_records), 1) * 100.0) if sig_records else 0.0
            direction_reliability = min(math.log2(len(sig_records) + 1) / math.log2(6), 1.0) if sig_records else 0.0
            direction_consistency_score = direction_consistency_raw * direction_reliability
            wgcna = calc_wgcna_support(
                genes,
                denominator_genes,
                comparison,
                samples,
                gene_modules,
                module_cor,
                module_p,
                args.wgcna_trait,
            )
            func = calc_functional_support(deg_genes, comparison, go_terms, kegg_terms, fgsea_terms, args.functional_mode)
            score, completeness = evidence_score({
                "differential_response_score": differential_response_score,
                "family_response_score": family_response_score,
                "direction_consistency_raw": direction_consistency_raw,
                "direction_reliability": direction_reliability,
                "direction_consistency_score": direction_consistency_score,
                "wgcna_support_score": wgcna.score,
                "enrichment_support_score": func.enrichment_score,
            })

            row = {
                "family_id": family,
                "family_name": family,
                "comparison": comparison,
                "total_family_genes": len(genes),
                "expressed_family_genes": len(denominator_genes),
                "deg_family_genes": len(deg_genes),
                "mean_abs_log2fc": mean_abs_log2fc,
                "median_padj": median_padj,
                "up_genes": up,
                "down_genes": down,
                "major_direction": top_direction(up, down),
                "family_response_ratio": family_response_ratio,
                "log2fc_score": log2fc_score,
                "padj_score": padj_score,
                "differential_response_score": differential_response_score,
                "family_response_score": family_response_score,
                "direction_consistency_raw": direction_consistency_raw,
                "direction_reliability": direction_reliability,
                "direction_consistency_score": direction_consistency_score,
                "wgcna_support_score": wgcna.score,
                "wgcna_main_module": wgcna.module,
                "wgcna_trait": wgcna.trait,
                "wgcna_module_cor": wgcna.module_cor,
                "wgcna_module_p": wgcna.module_p,
                "wgcna_module_genes": wgcna.module_genes,
                "wgcna_module_ratio": wgcna.module_ratio,
                "go_support_score": func.go_score,
                "kegg_support_score": func.kegg_score,
                "fgsea_support_score": func.fgsea_score,
                "enrichment_support_score": func.enrichment_score,
                "specific_pathway_score": func.specific_pathway_score,
                "family_hit_ratio_score": func.family_hit_ratio_score,
                "enrichment_significance_score": func.enrichment_significance_score,
                "broad_pathway_score": func.broad_pathway_score,
                "broad_pathway_hits": func.broad_pathway_hits,
                "total_support_terms": func.total_support_terms,
                "specific_pathway_count": func.specific_pathway_count,
                "only_generic_pathway_support": func.only_generic_pathway_support,
                "go_hit_genes": func.go_hit_genes,
                "kegg_hit_genes": func.kegg_hit_genes,
                "fgsea_hit_genes": func.fgsea_hit_genes,
                "best_go_padj": func.best_go_padj,
                "best_kegg_padj": func.best_kegg_padj,
                "best_fgsea_padj": func.best_fgsea_padj,
                "best_abs_nes": func.best_abs_nes,
                "evidence_score": score,
                "evidence_completeness": completeness,
                "has_DE_result": True,
                "has_WGCNA": bool(gene_modules),
                "has_GO": bool(go_terms),
                "has_KEGG": bool(kegg_terms),
                "has_fgsea": bool(fgsea_terms),
                "support_labels": func.support_labels,
            }
            per_rows.append(row)
            if len(deg_genes) > 0 or score > 0:
                ranking_source[family].append(row)

    size_percentiles = percentile_lookup({family: len(genes) for family, genes in family_genes.items()})
    ranking_rows: list[dict[str, Any]] = []
    for family, rows in ranking_source.items():
        rows = sorted(rows, key=lambda row: float(row.get("evidence_score") or 0.0), reverse=True)
        if not rows:
            continue
        best = rows[0]
        best_score = float(best.get("evidence_score") or 0.0)
        response_rows = [
            row for row in rows
            if float(row.get("evidence_score") or 0.0) >= 50.0
            or (int(row.get("deg_family_genes") or 0) >= 2 and float(row.get("family_response_ratio") or 0.0) >= 0.10)
        ]
        if len(response_rows) >= 2:
            top2 = response_rows[:2]
            top2_mean = sum(float(row.get("evidence_score") or 0.0) for row in top2) / 2.0
            top2_comparisons = ";".join(str(row.get("comparison", "")) for row in top2)
        else:
            top2 = [best]
            top2_mean = best_score / 2.0
            top2_comparisons = f"{best.get('comparison', '')};NA"
        raw_score = 0.70 * best_score + 0.30 * top2_mean
        direction_counts = Counter(row.get("major_direction", "") for row in rows if row.get("deg_family_genes", 0))
        major_direction = direction_counts.most_common(1)[0][0] if direction_counts else best.get("major_direction", "")
        response_direction_rows = [
            row for row in response_rows
            if row.get("major_direction") in {"Up", "Down"} and row.get("major_direction") == major_direction
        ]
        cross_direction_consistency = len(response_direction_rows) / max(len(response_rows), 1) * 100.0 if response_rows else 0.0
        final_direction_score = 0.60 * float(best.get("direction_consistency_score") or 0.0) + 0.40 * cross_direction_consistency
        total_family_genes = int(best.get("total_family_genes") or len(family_genes.get(family, set())))
        best_expressed_genes = int(best.get("expressed_family_genes") or 0)
        max_deg_genes = max(int(row.get("deg_family_genes") or 0) for row in rows)
        best_deg_genes = int(best.get("deg_family_genes") or 0)
        best_family_response_ratio = float(best.get("family_response_ratio") or 0.0)
        deg_reliability, expressed_reliability, small_family_reliability = reliability_from_counts(best_deg_genes, best_expressed_genes)
        family_size_percentile = size_percentiles.get(family, 0.0)
        superfamily_size_score = min(max((family_size_percentile - 0.90) / 0.10, 0.0), 1.0) * 100.0
        superfamily_ann_score = superfamily_annotation_score(family)
        low_response_ratio_score = (1.0 - min(best_family_response_ratio / 0.50, 1.0)) * 100.0
        superfamily_penalty = 0.40 * superfamily_size_score + 0.35 * superfamily_ann_score + 0.25 * low_response_ratio_score
        superfamily_penalty_factor = 1.0 - 0.25 * superfamily_penalty / 100.0
        annotation_specificity_factor, specificity_class, generic_flag = annotation_specificity(family)
        annotation_clarity_score = annotation_specificity_factor * 100.0
        all_support_labels: list[str] = []
        for row in response_rows or rows[:2]:
            all_support_labels.extend(row.get("support_labels") or [])
        generic_ratio = generic_annotation_ratio(all_support_labels)
        generic_annotation_score = min(generic_ratio / 0.50, 1.0) * 100.0
        response_breadth = len(response_rows) / max(len(de_by_comp), 1)
        breadth_score = response_breadth * 100.0
        broad_pathway_score = max(float(row.get("broad_pathway_score") or 0.0) for row in response_rows or rows[:1])
        only_generic_pathway_support = any(bool(row.get("only_generic_pathway_support")) for row in response_rows or rows[:1])
        specific_pathway_count = sum(int(row.get("specific_pathway_count") or 0) for row in response_rows or rows[:1])
        broad_response_penalty = 0.60 * breadth_score + 0.25 * generic_annotation_score + 0.15 * broad_pathway_score
        broad_penalty_factor = 1.0 - 0.15 * broad_response_penalty / 100.0
        functional_specificity_factor = 1.0 - 0.20 * broad_pathway_score / 100.0
        family_size_score = family_size_suitability(total_family_genes)
        deg_member_support = min(max_deg_genes / 8.0, 1.0) * 100.0
        expressed_member_support = min(best_expressed_genes / 10.0, 1.0) * 100.0
        non_redundancy_score = 100.0
        phylogeny_score = phylogeny_feasibility(total_family_genes, annotation_clarity_score)
        downstream_score = downstream_interpretability(
            deg_member_support,
            annotation_clarity_score,
            best.get("wgcna_support_score"),
            best.get("enrichment_support_score"),
        )
        family_analysis_suitability_score = (
            0.25 * family_size_score
            + 0.20 * deg_member_support
            + 0.15 * expressed_member_support
            + 0.15 * annotation_clarity_score
            + 0.10 * non_redundancy_score
            + 0.10 * phylogeny_score
            + 0.05 * downstream_score
        )
        mean_other_scores = 0.0
        if len(rows) > 1:
            mean_other_scores = sum(float(row.get("evidence_score") or 0.0) for row in rows[1:]) / (len(rows) - 1)
        temporal_specificity_score = max(0.0, min((best_score - mean_other_scores) / 30.0, 1.0)) * 100.0
        pre_redundancy_score = raw_score * family_analysis_suitability_score / 100.0 * broad_penalty_factor * functional_specificity_factor
        singleton_flag = total_family_genes < 5 or best_deg_genes <= 1
        small_family_flag = total_family_genes < 5 or best_deg_genes < 3 or best_expressed_genes < 5
        superfamily_flag = family_size_percentile >= 0.90
        family_analysis_candidate = (
            total_family_genes >= 5
            and best_expressed_genes >= 5
            and max_deg_genes >= 3
            and best_family_response_ratio >= 0.20
        )
        ranking_rows.append({
            "family_id": family,
            "family_name": family,
            "database_source": database_source(family),
            "total_family_genes": total_family_genes,
            "expressed_family_genes": best_expressed_genes,
            "deg_family_genes": max_deg_genes,
            "best_deg_family_genes": best_deg_genes,
            "family_response_ratio": best_family_response_ratio,
            "best_comparison": best.get("comparison", ""),
            "best_score": best_score,
            "top2_comparisons": top2_comparisons,
            "top2_mean_score": top2_mean,
            "raw_response_family_score": raw_score,
            "raw_family_score": raw_score,
            "major_direction": major_direction,
            "cross_comparison_direction_consistency": cross_direction_consistency,
            "final_direction_score": final_direction_score,
            "response_comparison_count": len(response_rows),
            "total_comparison_count": len(de_by_comp),
            "response_breadth": response_breadth,
            "breadth_score": breadth_score,
            "generic_annotation_ratio": generic_ratio,
            "generic_annotation_score": generic_annotation_score,
            "broad_pathway_score": broad_pathway_score,
            "specific_pathway_count": specific_pathway_count,
            "only_generic_pathway_support": only_generic_pathway_support,
            "broad_response_penalty": broad_response_penalty,
            "broad_penalty_factor": broad_penalty_factor,
            "functional_specificity_factor": functional_specificity_factor,
            "deg_reliability": deg_reliability,
            "expressed_reliability": expressed_reliability,
            "small_family_reliability": small_family_reliability,
            "family_size_percentile": family_size_percentile,
            "superfamily_size_score": superfamily_size_score,
            "low_response_ratio_score": low_response_ratio_score,
            "superfamily_annotation_score": superfamily_ann_score,
            "superfamily_penalty": superfamily_penalty,
            "superfamily_penalty_factor": superfamily_penalty_factor,
            "family_size_suitability": family_size_score,
            "DEG_member_support": deg_member_support,
            "expressed_member_support": expressed_member_support,
            "annotation_clarity_score": annotation_clarity_score,
            "annotation_specificity_factor": annotation_specificity_factor,
            "annotation_specificity_class": specificity_class,
            "non_redundancy_score": non_redundancy_score,
            "phylogeny_feasibility_score": phylogeny_score,
            "downstream_interpretability_score": downstream_score,
            "family_analysis_suitability_score": family_analysis_suitability_score,
            "temporal_specificity_score": temporal_specificity_score,
            "pre_redundancy_score": pre_redundancy_score,
            "best_evidence_completeness": best.get("evidence_completeness", ""),
            "best_differential_response_score": best.get("differential_response_score", ""),
            "best_family_response_score": best.get("family_response_score", ""),
            "best_direction_consistency_raw": best.get("direction_consistency_raw", ""),
            "best_direction_reliability": best.get("direction_reliability", ""),
            "best_direction_consistency_score": best.get("direction_consistency_score", ""),
            "best_wgcna_support_score": best.get("wgcna_support_score", ""),
            "best_enrichment_support_score": best.get("enrichment_support_score", ""),
            "best_wgcna_main_module": best.get("wgcna_main_module", ""),
            "best_support_labels": best.get("support_labels", []),
            "family_analysis_candidate": family_analysis_candidate,
            "singleton_flag": singleton_flag,
            "small_family_flag": small_family_flag,
            "superfamily_flag": superfamily_flag,
            "generic_annotation_flag": generic_flag,
        })

    annotate_redundancy(ranking_rows, family_genes)
    for row in ranking_rows:
        row["non_redundancy_score"] = 100.0 if not row.get("redundant_family_flag") else 50.0
        row["phylogeny_feasibility_score"] = phylogeny_feasibility(
            int(row.get("total_family_genes") or 0),
            float(row.get("annotation_clarity_score") or 0.0),
            bool(row.get("redundant_family_flag")),
        )
        row["family_analysis_suitability_score"] = (
            0.25 * float(row.get("family_size_suitability") or 0.0)
            + 0.20 * float(row.get("DEG_member_support") or 0.0)
            + 0.15 * float(row.get("expressed_member_support") or 0.0)
            + 0.15 * float(row.get("annotation_clarity_score") or 0.0)
            + 0.10 * float(row.get("non_redundancy_score") or 0.0)
            + 0.10 * float(row.get("phylogeny_feasibility_score") or 0.0)
            + 0.05 * float(row.get("downstream_interpretability_score") or 0.0)
        )
        row["pre_redundancy_score"] = (
            float(row.get("raw_response_family_score") or 0.0)
            * float(row.get("family_analysis_suitability_score") or 0.0)
            / 100.0
            * float(row.get("broad_penalty_factor") or 1.0)
            * float(row.get("functional_specificity_factor") or 1.0)
        )
        row["manual_review_flag"] = bool(
            row.get("generic_annotation_flag")
            or row.get("redundant_family_flag")
            or (row.get("superfamily_flag") and float(row.get("family_response_ratio") or 0.0) < 0.20)
            or float(row.get("annotation_clarity_score") or 0.0) < 40.0
            or row.get("only_generic_pathway_support")
            or row.get("possible_redundant_families")
        )
        cap_value, cap_reason = calculate_score_cap(row)
        final_score = float(row.get("pre_redundancy_score") or 0.0) * float(row.get("non_redundancy_factor") or 1.0)
        if final_score > cap_value:
            final_score = cap_value
        row["score_cap"] = cap_value
        row["score_cap_reason"] = cap_reason
        row["analysis_ready_score"] = final_score
        row["final_family_score"] = final_score
        if row.get("singleton_flag"):
            row["ranking_type"] = "singleton_candidate"
        elif row.get("manual_review_flag"):
            row["ranking_type"] = "manual_review_family"
        elif row.get("family_analysis_candidate"):
            row["ranking_type"] = "analysis_ready_family"
        else:
            row["ranking_type"] = "expression_response_family"
        if (
            row.get("ranking_type") == "analysis_ready_family"
            and float(row.get("response_breadth") or 0.0) >= 0.50
            and float(row.get("cross_comparison_direction_consistency") or 0.0) >= 60.0
            and float(row.get("annotation_clarity_score") or 0.0) >= 60.0
        ):
            row["core_broad_response_flag"] = True
        else:
            row["core_broad_response_flag"] = False
        row["time_specific_flag"] = (
            row.get("ranking_type") == "analysis_ready_family"
            and float(row.get("temporal_specificity_score") or 0.0) >= 60.0
        )

    ranking_rows.sort(key=lambda row: float(row.get("final_family_score") or 0.0), reverse=True)
    for idx, row in enumerate(ranking_rows, start=1):
        row["rank"] = idx
        row["overall_rank"] = idx

    def rerank_subset(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        reranked: list[dict[str, Any]] = []
        for idx, row in enumerate(rows, start=1):
            item = dict(row)
            item["overall_rank"] = row.get("overall_rank", row.get("rank", ""))
            item["rank"] = idx
            reranked.append(item)
        return reranked

    analysis_ready_rows = rerank_subset([row for row in ranking_rows if row.get("ranking_type") == "analysis_ready_family"])
    expression_response_rows = rerank_subset([row for row in ranking_rows if row.get("ranking_type") == "expression_response_family"])
    singleton_rows = rerank_subset([row for row in ranking_rows if row.get("ranking_type") == "singleton_candidate"])
    manual_review_rows = rerank_subset([row for row in ranking_rows if row.get("ranking_type") == "manual_review_family"])
    core_broad_rows = rerank_subset([row for row in ranking_rows if row.get("core_broad_response_flag")])
    time_specific_rows = rerank_subset([row for row in ranking_rows if row.get("time_specific_flag")])

    out_prefix = f"{args.prefix}.rnaseq_family_score"
    ranking_path = final_dir / f"{out_prefix}.ranking.tsv"
    analysis_ready_path = final_dir / f"{out_prefix}.analysis_ready_family.tsv"
    expression_response_path = final_dir / f"{out_prefix}.expression_response_family.tsv"
    family_level_path = final_dir / f"{out_prefix}.family_level.tsv"
    singleton_path = final_dir / f"{out_prefix}.singleton_candidate.tsv"
    manual_review_path = final_dir / f"{out_prefix}.manual_review_family.tsv"
    core_broad_path = final_dir / f"{out_prefix}.core_broad_response_family.tsv"
    time_specific_path = final_dir / f"{out_prefix}.time_specific_family.tsv"
    per_path = final_dir / f"{out_prefix}.per_comparison.tsv"
    report_path = final_dir / f"{out_prefix}_report.md"
    manifest_path = final_dir / f"{out_prefix}.manifest.json"

    ranking_fields = [
        "rank", "overall_rank", "ranking_type", "family_id", "family_name", "database_source", "non_redundancy_group",
        "representative_family_id", "total_family_genes", "expressed_family_genes", "deg_family_genes",
        "best_deg_family_genes", "family_response_ratio", "family_analysis_candidate", "best_comparison",
        "best_score", "top2_comparisons", "top2_mean_score", "raw_response_family_score", "raw_family_score",
        "analysis_ready_score", "final_family_score", "major_direction", "cross_comparison_direction_consistency",
        "final_direction_score", "response_comparison_count", "total_comparison_count", "response_breadth",
        "breadth_score", "temporal_specificity_score", "generic_annotation_ratio", "generic_annotation_score",
        "broad_pathway_score", "specific_pathway_count", "only_generic_pathway_support",
        "broad_response_penalty", "broad_penalty_factor", "functional_specificity_factor", "deg_reliability",
        "expressed_reliability", "small_family_reliability", "family_size_percentile", "superfamily_size_score",
        "low_response_ratio_score", "superfamily_annotation_score", "superfamily_penalty",
        "superfamily_penalty_factor", "family_size_suitability", "DEG_member_support",
        "expressed_member_support", "annotation_clarity_score", "annotation_specificity_factor",
        "annotation_specificity_class", "non_redundancy_score", "non_redundancy_factor",
        "phylogeny_feasibility_score", "downstream_interpretability_score",
        "family_analysis_suitability_score", "pre_redundancy_score", "score_cap", "score_cap_reason",
        "best_evidence_completeness",
        "best_differential_response_score", "best_family_response_score", "best_direction_consistency_raw",
        "best_direction_reliability", "best_direction_consistency_score", "best_wgcna_support_score",
        "best_enrichment_support_score", "best_wgcna_main_module", "singleton_flag", "small_family_flag",
        "superfamily_flag", "generic_annotation_flag", "redundant_family_flag", "manual_review_flag",
        "core_broad_response_flag", "time_specific_flag", "possible_redundant_families", "best_support_labels",
    ]
    per_fields = [
        "family_id", "family_name", "comparison", "total_family_genes", "expressed_family_genes", "deg_family_genes",
        "mean_abs_log2fc", "median_padj", "up_genes", "down_genes", "major_direction", "family_response_ratio",
        "log2fc_score", "padj_score", "differential_response_score", "family_response_score",
        "direction_consistency_raw", "direction_reliability", "direction_consistency_score",
        "wgcna_support_score", "wgcna_main_module", "wgcna_trait", "wgcna_module_cor", "wgcna_module_p", "wgcna_module_genes",
        "wgcna_module_ratio", "go_support_score", "kegg_support_score", "fgsea_support_score", "enrichment_support_score",
        "specific_pathway_score", "family_hit_ratio_score", "enrichment_significance_score",
        "broad_pathway_score", "broad_pathway_hits", "total_support_terms", "specific_pathway_count",
        "only_generic_pathway_support",
        "go_hit_genes", "kegg_hit_genes", "fgsea_hit_genes", "best_go_padj", "best_kegg_padj", "best_fgsea_padj",
        "best_abs_nes", "evidence_score", "evidence_completeness", "has_DE_result", "has_WGCNA", "has_GO", "has_KEGG",
        "has_fgsea", "support_labels",
    ]
    write_tsv(ranking_path, ranking_rows, ranking_fields)
    write_tsv(analysis_ready_path, analysis_ready_rows, ranking_fields)
    write_tsv(expression_response_path, expression_response_rows, ranking_fields)
    write_tsv(family_level_path, analysis_ready_rows, ranking_fields)
    write_tsv(singleton_path, singleton_rows, ranking_fields)
    write_tsv(manual_review_path, manual_review_rows, ranking_fields)
    write_tsv(core_broad_path, core_broad_rows, ranking_fields)
    write_tsv(time_specific_path, time_specific_rows, ranking_fields)
    write_tsv(per_path, sorted(per_rows, key=lambda row: (row["family_name"], row["comparison"])), per_fields)

    metadata = {
        "workspace": str(workspace),
        "final_dir": str(final_dir),
        "input_dir": str(input_dir),
        "sample_config": str(sample_config),
        "tpm_matrix": str(tpm_matrix),
        "interproscan_tsv": str(interproscan_tsv),
        "comparisons": sorted(de_by_comp.keys()),
        "has_wgcna": bool(gene_modules),
        "has_go": bool(go_terms),
        "has_kegg": bool(kegg_terms),
        "has_fgsea": bool(fgsea_terms),
        "ranking_path": str(ranking_path),
        "analysis_ready_path": str(analysis_ready_path),
        "expression_response_path": str(expression_response_path),
        "family_level_path": str(family_level_path),
        "singleton_path": str(singleton_path),
        "manual_review_path": str(manual_review_path),
        "core_broad_path": str(core_broad_path),
        "time_specific_path": str(time_specific_path),
        "per_comparison_path": str(per_path),
        "job_id": args.job_id,
        "analysis_ready_rows": analysis_ready_rows,
        "expression_response_rows": expression_response_rows,
        "family_level_rows": analysis_ready_rows,
        "singleton_rows": singleton_rows,
        "manual_review_rows": manual_review_rows,
        "core_broad_rows": core_broad_rows,
        "time_specific_rows": time_specific_rows,
    }
    make_analysis_ready_report(report_path, args, ranking_rows, per_rows, metadata)

    manifest = {
        "manifest_version": "1.0",
        "stage": "rnaseq_family_score",
        "status": "success",
        "prefix": args.prefix,
        "job_id": args.job_id,
        "created_at": now_iso(),
        "parameters": {
            "padj": args.padj,
            "log2fc": args.log2fc,
            "expression_threshold": args.expression_threshold,
            "include_lrt": args.include_lrt,
            "wgcna_trait": args.wgcna_trait,
            "functional_mode": args.functional_mode,
            "penalties_applied": True,
            "scoring_formula": EVIDENCE_WEIGHTS,
            "family_summary_formula": "0.70 * best_score + 0.30 * top2_mean_score",
            "single_response_top2_rule": "top2_mean = best_score / 2 when responsive_comparison_count < 2",
            "family_analysis_candidate_rule": "total_family_genes >= 5 and expressed_family_genes >= 5 and deg_family_genes >= 3 and family_response_ratio >= 0.20",
            "final_formula": "analysis_ready_score = raw_response_family_score * family_analysis_suitability_score / 100 * broad_penalty_factor * functional_specificity_factor * non_redundancy_factor, capped by score_cap",
        },
        "inputs": {
            "workspace": str(workspace),
            "final_dir": str(final_dir),
            "input_dir": str(input_dir),
            "sample_config": str(sample_config),
            "tpm_matrix": str(tpm_matrix),
            "interproscan_tsv": str(interproscan_tsv),
            "comparisons": sorted(de_by_comp.keys()),
            "has_wgcna": bool(gene_modules),
            "has_go": bool(go_terms),
            "has_kegg": bool(kegg_terms),
            "has_fgsea": bool(fgsea_terms),
        },
        "metrics": {
            "family_count": len(ranking_rows),
            "analysis_ready_count": len(analysis_ready_rows),
            "expression_response_count": len(expression_response_rows),
            "singleton_count": len(singleton_rows),
            "manual_review_count": len(manual_review_rows),
            "core_broad_response_count": len(core_broad_rows),
            "time_specific_count": len(time_specific_rows),
            "per_comparison_rows": len(per_rows),
            "comparison_count": len(de_by_comp),
        },
        "artifacts": {
            "ranking": {"artifact": "ranking", "path": f"final/{ranking_path.name}", "type": "tsv"},
            "analysis_ready_family": {"artifact": "analysis_ready_family", "path": f"final/{analysis_ready_path.name}", "type": "tsv"},
            "expression_response_family": {"artifact": "expression_response_family", "path": f"final/{expression_response_path.name}", "type": "tsv"},
            "family_level": {"artifact": "family_level_alias", "path": f"final/{family_level_path.name}", "type": "tsv"},
            "singleton": {"artifact": "singleton", "path": f"final/{singleton_path.name}", "type": "tsv"},
            "manual_review": {"artifact": "manual_review", "path": f"final/{manual_review_path.name}", "type": "tsv"},
            "core_broad_response_family": {"artifact": "core_broad_response_family", "path": f"final/{core_broad_path.name}", "type": "tsv"},
            "time_specific_family": {"artifact": "time_specific_family", "path": f"final/{time_specific_path.name}", "type": "tsv"},
            "per_comparison": {"artifact": "per_comparison", "path": f"final/{per_path.name}", "type": "tsv"},
            "report": {"artifact": "report", "path": f"final/{report_path.name}", "type": "markdown"},
            "manifest": {"artifact": "manifest", "path": f"final/{manifest_path.name}", "type": "json"},
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[OK] ranking: {ranking_path}")
    print(f"[OK] analysis-ready: {analysis_ready_path}")
    print(f"[OK] expression-response: {expression_response_path}")
    print(f"[OK] family-level alias: {family_level_path}")
    print(f"[OK] singleton: {singleton_path}")
    print(f"[OK] manual-review: {manual_review_path}")
    print(f"[OK] core-broad: {core_broad_path}")
    print(f"[OK] time-specific: {time_specific_path}")
    print(f"[OK] per-comparison: {per_path}")
    print(f"[OK] report: {report_path}")
    print(f"[OK] manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
