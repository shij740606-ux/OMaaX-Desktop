#!/usr/bin/env python3
"""RNA-seq fgsea stage for plant KEGG gene sets.

The human example based on msigdbr/org.Hs.eg.db is not appropriate for rice or
most non-model plant runs in this workspace.  This runner builds KEGG pathways
from the job's local gene2ko file and the local KEGG KO pathway cache, then runs
fgsea on DESeq2 log2FoldChange ranks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).absolute().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cli.rnaseq_env import rnaseq_rscript
from gfa_core.rnaseq_context import apply_rnaseq_context, find_upstream_rnaseq_context

RNASEQ_RSCRIPT = rnaseq_rscript()

SPECIES_TO_KEGG_ORGANISM = {
    "arabidopsis thaliana": "ath",
    "oryza sativa": "osa",
    "oryza sativa japonica": "osa",
    "rice": "osa",
    "zea mays": "zma",
    "maize": "zma",
    "glycine max": "gmx",
    "soybean": "gmx",
    "solanum lycopersicum": "sly",
    "tomato": "sly",
    "triticum aestivum": "tae",
    "wheat": "tae",
    "hordeum vulgare": "hvu",
    "barley": "hvu",
    "sorghum bicolor": "sbi",
    "sorghum": "sbi",
}

PLANT_KEEP_KEYWORDS = re.compile(
    r"\b(plant|photosynthesis|phenylpropanoid|flavonoid|terpenoid|chlorophyll|starch|sucrose|linolenic|jasmonic|"
    r"brassinosteroid|carotenoid|porphyrin|carbon fixation|nitrogen|sulfur|glutathione)\b",
    re.IGNORECASE,
)
NON_PLANT_PATHWAY_PATTERNS = re.compile(
    r"\b(human|disease|cancer|carcinoma|leukemia|alzheimer|parkinson|huntington|diabetes|insulin|vasopressin|"
    r"viral|infection|immune|endocrine|synaptic|axon|neuro|olfactory|taste|salivary|gastric|pancreatic|"
    r"cardiomyopathy|addiction|thermogenesis|p53|fly|worm|bile secretion|platelet|neutrophil|"
    r"phagosome|quorum sensing)\b",
    re.IGNORECASE,
)
NON_PLANT_PATHWAY_ID_PATTERNS = re.compile(r"^ko(049|050|051|052|053|054|046|047)")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def file_sha256(path: Path) -> str:
    sha = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                sha.update(chunk)
        return sha.hexdigest()
    except Exception:
        return ""


def abs_text(path: Path | str | None) -> str:
    return str(Path(path).absolute()) if path else ""


def relpath(path: Path, workspace: Path) -> str:
    try:
        return str(path.absolute().relative_to(workspace.absolute()))
    except Exception:
        return str(path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def infer_prefix(final_dir: Path, requested: str = "") -> str:
    if requested:
        return requested
    for pattern in ("*.rnaseq_de.manifest.json", "*.rnaseq_quant.manifest.json", "*.annotation.manifest.json"):
        for manifest in sorted(final_dir.glob(pattern)):
            data = read_json(manifest)
            prefix = data.get("prefix")
            if prefix:
                return str(prefix)
            suffix = "." + pattern[2:]
            if manifest.name.endswith(suffix):
                return manifest.name[: -len(suffix)]
    for de_file in sorted(final_dir.glob("*.rnaseq_de.*.DE_results.txt")):
        token = ".rnaseq_de."
        if token in de_file.name:
            return de_file.name.split(token, 1)[0]
    raise FileNotFoundError(f"Cannot infer prefix from {final_dir}")


def find_de_results(final_dir: Path, prefix: str, comparison: str = "") -> list[Path]:
    def fgsea_eligible(path: Path) -> bool:
        # M4 LRT tests whether an interaction pattern exists but has no signed
        # direction.  Keep its Wald coefficient for FGSEA and exclude the LRT.
        return ".interaction_LRT." not in path.name

    if comparison:
        candidate = final_dir / f"{prefix}.rnaseq_de.{comparison}.DE_results.txt"
        if candidate.exists():
            if not fgsea_eligible(candidate):
                raise ValueError("M4 interaction LRT results are unsigned and are not eligible for FGSEA")
            return [candidate]
        raise FileNotFoundError(f"DE results not found: {candidate}")
    files = [
        path for path in sorted(final_dir.glob(f"{prefix}.rnaseq_de.*.DE_results.txt"))
        if fgsea_eligible(path)
    ]
    if not files:
        raise FileNotFoundError(f"No DESeq2 results found: {final_dir}/{prefix}.rnaseq_de.*.DE_results.txt")
    return files


def comparison_from_de_file(path: Path, prefix: str) -> str:
    name = path.name
    prefix_token = f"{prefix}.rnaseq_de."
    suffix = ".DE_results.txt"
    if name.startswith(prefix_token) and name.endswith(suffix):
        return name[len(prefix_token) : -len(suffix)]
    return path.stem


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def infer_kegg_organism(species_name: str = "", requested: str = "") -> str:
    requested = (requested or "").strip().lower()
    if re.fullmatch(r"[a-z][a-z0-9]{2,4}", requested):
        return requested
    key = re.sub(r"\s+", " ", (species_name or "").strip().lower())
    return SPECIES_TO_KEGG_ORGANISM.get(key, "")


def fetch_url_text(url: str, timeout: int = 60) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def ensure_species_kegg_cache(cache_root: Path, organism: str, allow_network: bool = True) -> dict[str, Any]:
    """Return species-level KEGG pathway cache files when available.

    KEGG species pathway sets avoid KO-global human/animal pathways. Missing or
    unreachable KEGG REST is not fatal; the caller can fall back to KO mode.
    """
    organism = (organism or "").strip().lower()
    result: dict[str, Any] = {
        "organism": organism,
        "status": "not_requested" if not organism else "missing",
        "cache_dir": "",
        "link_pathway": "",
        "list_pathway": "",
        "error": "",
    }
    if not organism:
        return result
    cache_dir = cache_root / organism
    cache_dir.mkdir(parents=True, exist_ok=True)
    result["cache_dir"] = str(cache_dir)
    link_pathway = cache_dir / f"link_pathway_{organism}.tsv"
    list_pathway = cache_dir / f"list_pathway_{organism}.tsv"
    meta_path = cache_dir / "metadata.json"
    try:
        if (not link_pathway.exists() or link_pathway.stat().st_size == 0) and allow_network:
            text = fetch_url_text(f"https://rest.kegg.jp/link/pathway/{organism}")
            link_pathway.write_text(text, encoding="utf-8")
        if (not list_pathway.exists() or list_pathway.stat().st_size == 0) and allow_network:
            text = fetch_url_text(f"https://rest.kegg.jp/list/pathway/{organism}")
            list_pathway.write_text(text, encoding="utf-8")
        if link_pathway.exists() and list_pathway.exists() and link_pathway.stat().st_size > 0 and list_pathway.stat().st_size > 0:
            result.update({
                "status": "available",
                "link_pathway": str(link_pathway),
                "list_pathway": str(list_pathway),
            })
            meta_path.write_text(json.dumps({
                "organism": organism,
                "source": "KEGG REST",
                "link_pathway": link_pathway.name,
                "list_pathway": list_pathway.name,
                "updated_at": now_iso(),
            }, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            result["status"] = "missing"
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
    return result


def make_r_script(path: Path) -> None:
    script = r'''
suppressPackageStartupMessages({
  library(data.table)
  library(fgsea)
  library(ggplot2)
})

args <- commandArgs(trailingOnly = TRUE)
kv <- list()
for (arg in args) {
  parts <- strsplit(arg, "=", fixed = TRUE)[[1]]
  if (length(parts) == 2) kv[[parts[1]]] <- parts[2]
}
required <- c("de_file", "gene2ko", "link_pathway_ko", "list_pathway_ko", "out_prefix",
              "min_size", "max_size", "nperm", "seed")
missing <- required[!vapply(required, function(x) !is.null(kv[[x]]) && nzchar(kv[[x]]), logical(1))]
if (length(missing) > 0) stop("Missing arguments: ", paste(missing, collapse = ", "))

normalize_gene_id <- function(x) {
  x <- trimws(as.character(x))
  x <- sub("^>", "", x)
  x <- sub("^[a-z][a-z0-9]{2,4}:", "", x, perl = TRUE)
  x <- sub("\\s.*$", "", x)
  patterns <- c("(?i)([._-](mrna|rna|transcript|cds|protein|pep|peptide)[0-9A-Za-z]*)$",
                "(?i)([._-][tp][0-9A-Za-z]*)$",
                "(?i)([._-][rp][A-Z])$",
                "(\\.[0-9]+)$")
  changed <- TRUE
  while (changed) {
    old <- x
    for (pat in patterns) x <- sub(pat, "", x, perl = TRUE)
    changed <- !identical(old, x)
  }
  rap <- regexec("^(Os[0-9]{2})t([0-9]+)(-[0-9]+)?$", x, ignore.case = TRUE, perl = TRUE)
  rap_parts <- regmatches(x, rap)
  x <- vapply(seq_along(x), function(i) {
    parts <- rap_parts[[i]]
    if (length(parts) >= 3) {
      paste0(parts[[2]], "g", parts[[3]])
    } else {
      x[[i]]
    }
  }, character(1))
  x
}

extract_ko <- function(x) {
  x <- as.character(x)
  m <- regexpr("K[0-9]{5}", x, perl = TRUE)
  out <- rep(NA_character_, length(x))
  hit <- m > 0
  out[hit] <- regmatches(x, m)[hit]
  out
}

extract_pathway <- function(x) {
  x <- as.character(x)
  m <- regexpr("([a-z][a-z0-9]{2,4}|ko|map)[0-9]{5}", x, perl = TRUE)
  out <- rep(NA_character_, length(x))
  hit <- m > 0
  out[hit] <- sub("^map", "ko", regmatches(x, m)[hit])
  out
}

generic_pathway <- function(x) {
  x <- as.character(x)
  m <- regexpr("[0-9]{5}", x, perl = TRUE)
  out <- x
  hit <- m > 0
  out[hit] <- paste0("ko", regmatches(x, m)[hit])
  out
}

is_plant_compatible <- function(pathway, description) {
  desc <- ifelse(is.na(description), "", as.character(description))
  pid <- generic_pathway(pathway)
  keep_hit <- grepl("plant|photosynthesis|phenylpropanoid|flavonoid|terpenoid|chlorophyll|starch|sucrose|linolenic|jasmonic|brassinosteroid|carotenoid|porphyrin|carbon fixation|nitrogen|sulfur|glutathione", desc, ignore.case = TRUE, perl = TRUE)
  bad_desc <- grepl("human|disease|cancer|carcinoma|leukemia|alzheimer|parkinson|huntington|diabetes|insulin|vasopressin|viral|infection|immune|endocrine|synaptic|axon|neuro|olfactory|taste|salivary|gastric|pancreatic|cardiomyopathy|addiction|thermogenesis|p53|fly|worm|bile secretion|platelet|neutrophil|phagosome|quorum sensing", desc, ignore.case = TRUE, perl = TRUE)
  bad_id <- grepl("^ko(049|050|051|052|053|054|046|047)", pid, perl = TRUE)
  keep_hit | (!bad_desc & !bad_id)
}

de <- fread(kv$de_file)
if (!("gene_id" %in% names(de))) {
  setnames(de, names(de)[1], "gene_id")
}
if (!("log2FoldChange" %in% names(de))) stop("DE file lacks log2FoldChange column: ", kv$de_file)
de[, gene_norm := normalize_gene_id(gene_id)]
de[, log2FoldChange := as.numeric(log2FoldChange)]
de <- de[is.finite(log2FoldChange) & nzchar(gene_norm)]
de[, abs_lfc := abs(log2FoldChange)]
setorder(de, gene_norm, -abs_lfc)
de <- de[!duplicated(gene_norm)]
stats <- de$log2FoldChange
names(stats) <- de$gene_norm
stats <- sort(stats, decreasing = TRUE)
if (length(stats) < 2) stop("Not enough ranked genes for fgsea")

species_link <- kv[["species_link_pathway"]]
species_list <- kv[["species_list_pathway"]]
kegg_mode <- ifelse(is.null(kv[["kegg_mode"]]) || !nzchar(kv[["kegg_mode"]]), "auto", kv[["kegg_mode"]])
kegg_filter_scope <- ifelse(is.null(kv[["kegg_filter_scope"]]) || !nzchar(kv[["kegg_filter_scope"]]), "plant", kv[["kegg_filter_scope"]])
used_kegg_mode <- "ko"

if (!is.null(species_link) && nzchar(species_link) && file.exists(species_link) &&
    !is.null(species_list) && nzchar(species_list) && file.exists(species_list) &&
    kegg_mode %in% c("auto", "species")) {
  species_pathway_names <- fread(species_list, header = FALSE, sep = "\t", fill = TRUE)
  if (ncol(species_pathway_names) >= 2) {
    setnames(species_pathway_names, c("pathway", "Description"))
    species_pathway_names[, pathway := extract_pathway(pathway)]
    species_pathway_names <- unique(species_pathway_names[!is.na(pathway), .(pathway, Description)])
  } else {
    species_pathway_names <- data.table(pathway = character(), Description = character())
  }
  species_links <- fread(species_link, header = FALSE, fill = TRUE)
  if (ncol(species_links) >= 2) {
    setnames(species_links, c("gene_raw", "pathway_raw"))
    species_links[, pathway := extract_pathway(pathway_raw)]
    species_links[, gene := normalize_gene_id(gene_raw)]
    gene_pathway <- unique(species_links[!is.na(pathway) & nzchar(gene), .(pathway, gene)])
    pathway_names <- species_pathway_names
    used_kegg_mode <- "species"
  }
}

if (exists("gene_pathway")) {
  gene_pathway <- gene_pathway[gene %in% names(stats)]
  if (nrow(gene_pathway) == 0) rm(gene_pathway)
}

if (!exists("gene_pathway") || nrow(gene_pathway) == 0) {
  gene2ko <- fread(kv$gene2ko, header = FALSE, fill = TRUE)
  if (ncol(gene2ko) < 2) stop("gene2ko must contain at least two columns")
  setnames(gene2ko, c("gene_raw", "ko_raw"))
  gene2ko[, gene := normalize_gene_id(gene_raw)]
  gene2ko[, ko := extract_ko(ko_raw)]
  gene2ko <- unique(gene2ko[!is.na(ko) & nzchar(gene), .(gene, ko)])

  links <- fread(kv$link_pathway_ko, header = FALSE, fill = TRUE)
  if (ncol(links) < 2) stop("link_pathway_ko must contain two columns")
  setnames(links, c("ko_raw", "pathway_raw"))
  links[, ko := extract_ko(ko_raw)]
  links[, pathway := extract_pathway(pathway_raw)]
  links <- unique(links[!is.na(ko) & !is.na(pathway) & grepl("^ko", pathway), .(ko, pathway)])

  if (exists("species_pathway_names") && nrow(species_pathway_names) > 0 && kegg_mode %in% c("auto", "species")) {
    pathway_names <- copy(species_pathway_names)
    pathway_names[, pathway := generic_pathway(pathway)]
    pathway_names <- unique(pathway_names[!is.na(pathway), .(pathway, Description)])
    if (kegg_filter_scope == "plant") {
      pathway_names <- pathway_names[is_plant_compatible(pathway, Description)]
    }
    links <- links[pathway %in% pathway_names$pathway]
    used_kegg_mode <- "species_filtered_ko"
  } else {
    pathway_names <- fread(kv$list_pathway_ko, header = FALSE, sep = "\t", fill = TRUE)
    if (ncol(pathway_names) >= 2) {
      setnames(pathway_names, c("pathway", "Description"))
      pathway_names[, pathway := extract_pathway(pathway)]
      pathway_names <- unique(pathway_names[!is.na(pathway), .(pathway, Description)])
    } else {
      pathway_names <- data.table(pathway = character(), Description = character())
    }
    if (kegg_filter_scope == "plant") {
      pathway_names <- pathway_names[is_plant_compatible(pathway, Description)]
      links <- links[pathway %in% pathway_names$pathway]
    }
    used_kegg_mode <- "ko_filtered"
  }

  gene_pathway <- unique(merge(gene2ko, links, by = "ko", allow.cartesian = TRUE)[, .(pathway, gene)])
}

gene_pathway <- gene_pathway[gene %in% names(stats)]
pathway_sizes <- gene_pathway[, .N, by = pathway]
pathway_sizes <- pathway_sizes[N >= as.integer(kv$min_size) & N <= as.integer(kv$max_size)]
gene_pathway <- gene_pathway[pathway %in% pathway_sizes$pathway]
pathways <- split(gene_pathway$gene, gene_pathway$pathway)
pathways <- lapply(pathways, unique)
if (length(pathways) == 0) stop("No KEGG pathway gene sets passed min/max size filters")

set.seed(as.integer(kv$seed))
fg <- suppressWarnings(fgsea(pathways = pathways,
                            stats = stats,
                            minSize = as.integer(kv$min_size),
                            maxSize = as.integer(kv$max_size),
                            nperm = as.integer(kv$nperm)))
fg <- as.data.table(fg)
if (nrow(fg) > 0) {
  fg[, leadingEdge := vapply(leadingEdge, paste, collapse = ";", FUN.VALUE = character(1))]
  fg <- merge(fg, pathway_names, by.x = "pathway", by.y = "pathway", all.x = TRUE)
  fg[is.na(Description), Description := pathway]
  setorder(fg, padj, pval)
}

out_tsv <- paste0(kv$out_prefix, ".fgsea_kegg.tsv")
fwrite(fg, out_tsv, sep = "\t", quote = FALSE, na = "")

summary_dt <- data.table(
  metric = c("ranked_genes", "genes_with_pathway_in_ranks", "pathways_tested", "significant_padj_0.05", "kegg_gene_set_mode"),
  value = c(length(stats), length(unique(gene_pathway$gene)), length(pathways), sum(fg$padj <= 0.05, na.rm = TRUE), used_kegg_mode)
)
fwrite(summary_dt, paste0(kv$out_prefix, ".fgsea_summary.tsv"), sep = "\t", quote = FALSE)

plot_dt <- fg[is.finite(NES)]
if (nrow(plot_dt) > 0) {
  top_dt <- rbind(
    plot_dt[NES > 0][order(padj, -NES)][1:min(.N, 10)],
    plot_dt[NES < 0][order(padj, NES)][1:min(.N, 10)]
  )
  top_dt <- unique(top_dt, by = "pathway")
  top_dt[, label := ifelse(nchar(Description) > 70, paste0(substr(Description, 1, 67), "..."), Description)]
  top_dt[, label := factor(label, levels = rev(top_dt[order(NES)]$label))]
  p <- ggplot(top_dt, aes(x = NES, y = label, color = padj, size = size)) +
    geom_point() +
    scale_color_continuous(trans = "reverse") +
    labs(x = "Normalized enrichment score (NES)", y = NULL, color = "padj", size = "Genes",
         title = "KEGG fgsea") +
    theme_bw(base_size = 11) +
    theme(plot.title = element_text(hjust = 0.5))
  ggsave(paste0(kv$out_prefix, ".fgsea_kegg_dotplot.png"), p, width = 9, height = 6, dpi = 300)
  ggsave(paste0(kv$out_prefix, ".fgsea_kegg_dotplot.pdf"), p, width = 9, height = 6)

  best <- fg[order(padj, -abs(NES))][1]$pathway
  if (length(best) == 1 && !is.na(best) && best %in% names(pathways)) {
    ep <- plotEnrichment(pathways[[best]], stats) +
      labs(title = paste0(best, " - ", fg[pathway == best][1]$Description))
    ggsave(paste0(kv$out_prefix, ".fgsea_kegg_top_enrichment.png"), ep, width = 8, height = 5, dpi = 300)
    ggsave(paste0(kv$out_prefix, ".fgsea_kegg_top_enrichment.pdf"), ep, width = 8, height = 5)
  }
}
'''.lstrip()
    write_text(path, script)


def get_r_versions() -> dict[str, str]:
    cmd = [
        RNASEQ_RSCRIPT,
        "-e",
        "cat(R.version.string, '\\n'); for (p in c('fgsea','data.table','ggplot2')) "
        "cat(p, as.character(utils::packageVersion(p)), '\\n')",
    ]
    versions: dict[str, str] = {}
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=30, check=False)
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        if lines:
            versions["R"] = lines[0]
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 2:
                versions[parts[0]] = parts[1]
    except Exception as exc:
        versions["error"] = str(exc)
    return versions


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def write_report(path: Path, manifest: dict[str, Any]) -> None:
    metrics = manifest.get("metrics", {})
    artifacts = manifest.get("artifacts", {})
    params = manifest.get("parameters", {})
    species_cache = params.get("kegg_species_cache") if isinstance(params.get("kegg_species_cache"), dict) else {}
    lines = [
        f"# RNA-seq fgsea Report: {manifest.get('prefix', '')}",
        "",
        f"- Status: {manifest.get('status')}",
        f"- Stage: {manifest.get('stage')}",
        f"- Collection: {params.get('collection')}",
        f"- KEGG organism: {params.get('kegg_organism') or 'not provided'}",
        f"- Species name: {params.get('species_name') or 'not provided'}",
        f"- KEGG species cache: {species_cache.get('status', 'not used')}",
        f"- KEGG gene set mode: {', '.join(metrics.get('kegg_gene_set_modes', [])) or 'not recorded'}",
        f"- Comparisons: {metrics.get('comparisons', 0)}",
        f"- Ranked genes: {metrics.get('ranked_genes', 0)}",
        f"- Ranked genes with pathway mapping: {metrics.get('genes_with_pathway_in_ranks', 0)}",
        f"- Pathways tested: {metrics.get('pathways_tested', 0)}",
        f"- Significant pathways (padj <= 0.05): {metrics.get('significant_pathways_padj_0_05', 0)}",
        "",
        "## Notes",
        "",
        "- KEGG gene sets prefer the requested species pathway catalog when available.",
        "- If species-level KEGG gene IDs do not match local gene IDs, species pathways are used as an allowlist over local gene2ko and KO-to-pathway mappings.",
        "- Plant filtering is applied before fgsea so adjusted P values are calculated on the tested pathway universe.",
        "- Human-only msigdbr and org.Hs.eg.db mappings were not used.",
        "",
        "## Artifacts",
        "",
    ]
    for key, artifact in artifacts.items():
        if isinstance(artifact, dict):
            lines.append(f"- {key}: `{artifact.get('path')}`")
    write_text(path, "\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run plant-compatible RNA-seq KEGG fgsea")
    parser.add_argument("-w", "--workspace", required=True, help="workspace/<job_id> directory")
    parser.add_argument("-p", "--prefix", default="", help="Output prefix; inferred from rnaseq_de manifest if omitted")
    parser.add_argument("-j", "--job-id", default="", help="Job ID")
    parser.add_argument("--comparison", default="", help="Comparison name, e.g. Drought_vs_Control")
    parser.add_argument("--gene2ko", default="", help="gene2ko file; default final/{prefix}.gene2ko.txt or final/gene2ko.txt")
    parser.add_argument("--kegg-cache-dir", default=str(PROJECT_ROOT / "data" / "kegg_pathway_cache"))
    parser.add_argument("--kegg-species-cache-dir", default=str(PROJECT_ROOT / "data" / "kegg_species_cache"))
    parser.add_argument("--kegg-organism", default="", help="KEGG organism code, e.g. osa for Oryza sativa")
    parser.add_argument("--species-name", default="", help="Latin/common species name used to infer KEGG organism")
    parser.add_argument("--kegg-mode", default="auto", choices=["auto", "species", "ko"], help="Prefer species-level KEGG gene sets, or force KO fallback")
    parser.add_argument("--kegg-filter-scope", default="plant", choices=["plant", "all"], help="Filter KO-global pathway gene sets before fgsea")
    parser.add_argument("--no-kegg-network", action="store_true", help="Do not download missing KEGG species cache files")
    parser.add_argument("--collection", default="kegg", choices=["kegg"], help="Gene-set collection")
    parser.add_argument("--min-size", type=int, default=15)
    parser.add_argument("--max-size", type=int, default=500)
    parser.add_argument("--nperm", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)

    start = time.time()
    start_time = now_iso()
    workspace = Path(args.workspace).absolute()
    final_dir = workspace / "final"
    stage_dir = workspace / "rnaseq_fgsea"
    logs_dir = workspace / "logs"
    final_dir.mkdir(parents=True, exist_ok=True)
    stage_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    prefix = infer_prefix(final_dir, args.prefix)
    job_id = args.job_id or workspace.name
    manifest_path = final_dir / f"{prefix}.rnaseq_fgsea.manifest.json"
    report_path = final_dir / f"{prefix}.rnaseq_fgsea_report.md"
    commands_path = stage_dir / "commands.sh"
    env_path = stage_dir / "environment.txt"
    r_script = stage_dir / "run_fgsea_kegg.R"
    log_path = logs_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_rnaseq_fgsea.log"

    artifacts: dict[str, dict[str, Any]] = {}
    metrics: dict[str, Any] = {
        "comparisons": 0,
        "ranked_genes": 0,
        "genes_with_pathway_in_ranks": 0,
        "pathways_tested": 0,
        "significant_pathways_padj_0_05": 0,
        "kegg_gene_set_modes": [],
    }
    cmd_log: list[list[str]] = []

    try:
        de_files = find_de_results(final_dir, prefix, args.comparison)
        gene2ko = Path(args.gene2ko).absolute() if args.gene2ko else final_dir / f"{prefix}.gene2ko.txt"
        if not gene2ko.exists():
            gene2ko = final_dir / "gene2ko.txt"
        if not gene2ko.exists():
            raise FileNotFoundError(f"gene2ko not found: {final_dir}/{prefix}.gene2ko.txt or {final_dir}/gene2ko.txt")

        cache_dir = Path(args.kegg_cache_dir).absolute()
        link_pathway = cache_dir / "link_pathway_ko.tsv"
        list_pathway = cache_dir / "list_pathway_ko.tsv"
        if not link_pathway.exists() or not list_pathway.exists():
            raise FileNotFoundError(f"KEGG cache lacks link_pathway_ko.tsv/list_pathway_ko.tsv: {cache_dir}")

        kegg_organism = infer_kegg_organism(args.species_name, args.kegg_organism)
        species_cache = {"status": "not_requested", "organism": kegg_organism}
        if args.kegg_mode in {"auto", "species"} and kegg_organism:
            species_cache = ensure_species_kegg_cache(
                Path(args.kegg_species_cache_dir).absolute(),
                kegg_organism,
                allow_network=not args.no_kegg_network,
            )
            if args.kegg_mode == "species" and species_cache.get("status") != "available":
                raise FileNotFoundError(
                    f"KEGG species pathway cache unavailable for {kegg_organism}: {species_cache.get('error') or species_cache.get('status')}"
                )

        make_r_script(r_script)
        write_text(env_path, "\n".join(f"{k}: {v}" for k, v in get_r_versions().items()) + "\n")

        commands: list[str] = []
        for de_file in de_files:
            comparison = comparison_from_de_file(de_file, prefix)
            out_prefix = final_dir / f"{prefix}.rnaseq_fgsea.{comparison}"
            cmd = [
                RNASEQ_RSCRIPT,
                str(r_script),
                f"de_file={de_file}",
                f"gene2ko={gene2ko}",
                f"link_pathway_ko={link_pathway}",
                f"list_pathway_ko={list_pathway}",
                f"out_prefix={out_prefix}",
                f"min_size={args.min_size}",
                f"max_size={args.max_size}",
                f"nperm={args.nperm}",
                f"seed={args.seed}",
                f"kegg_mode={args.kegg_mode}",
                f"kegg_filter_scope={args.kegg_filter_scope}",
            ]
            if species_cache.get("status") == "available":
                cmd.extend([
                    f"species_link_pathway={species_cache.get('link_pathway')}",
                    f"species_list_pathway={species_cache.get('list_pathway')}",
                ])
            cmd_log.append(cmd)
            commands.append(" ".join(shlex.quote(part) for part in cmd))
            with log_path.open("a", encoding="utf-8") as log_handle:
                log_handle.write("$ " + commands[-1] + "\n")
                proc = subprocess.run(cmd, text=True, stdout=log_handle, stderr=subprocess.STDOUT, check=False)
            if proc.returncode != 0:
                raise RuntimeError(f"fgsea failed for {comparison}; see log: {log_path}")

            result_tsv = Path(f"{out_prefix}.fgsea_kegg.tsv")
            summary_tsv = Path(f"{out_prefix}.fgsea_summary.tsv")
            if not result_tsv.exists():
                raise FileNotFoundError(f"fgsea result not generated: {result_tsv}")

            artifacts[f"{comparison}_fgsea_kegg"] = {
                "path": relpath(result_tsv, workspace),
                "full_path": abs_text(result_tsv),
                "type": "tsv",
                "sha256": file_sha256(result_tsv),
            }
            if summary_tsv.exists():
                artifacts[f"{comparison}_summary"] = {
                    "path": relpath(summary_tsv, workspace),
                    "full_path": abs_text(summary_tsv),
                    "type": "tsv",
                    "sha256": file_sha256(summary_tsv),
                }
                summary = {}
                for line in summary_tsv.read_text(encoding="utf-8").splitlines()[1:]:
                    parts = line.split("\t")
                    if len(parts) == 2:
                        try:
                            summary[parts[0]] = int(float(parts[1]))
                        except ValueError:
                            summary[parts[0]] = parts[1]
                metrics["ranked_genes"] += summary.get("ranked_genes", 0)
                metrics["genes_with_pathway_in_ranks"] += summary.get("genes_with_pathway_in_ranks", 0)
                metrics["pathways_tested"] += summary.get("pathways_tested", 0)
                metrics["significant_pathways_padj_0_05"] += summary.get("significant_padj_0.05", 0)
                mode = summary.get("kegg_gene_set_mode")
                if mode and mode not in metrics["kegg_gene_set_modes"]:
                    metrics["kegg_gene_set_modes"].append(mode)
            metrics["comparisons"] += 1

            for suffix, artifact_type in [
                (".fgsea_kegg_dotplot.png", "png"),
                (".fgsea_kegg_dotplot.pdf", "pdf"),
                (".fgsea_kegg_top_enrichment.png", "png"),
                (".fgsea_kegg_top_enrichment.pdf", "pdf"),
            ]:
                plot_path = Path(f"{out_prefix}{suffix}")
                if plot_path.exists():
                    artifacts[f"{comparison}{suffix.replace('.', '_')}"] = {
                        "path": relpath(plot_path, workspace),
                        "full_path": abs_text(plot_path),
                        "type": artifact_type,
                        "sha256": file_sha256(plot_path),
                    }

        write_text(commands_path, "\n".join(commands) + "\n")
        artifacts["commands"] = {
            "path": relpath(commands_path, workspace),
            "full_path": abs_text(commands_path),
            "type": "shell",
            "sha256": file_sha256(commands_path),
        }
        artifacts["environment"] = {
            "path": relpath(env_path, workspace),
            "full_path": abs_text(env_path),
            "type": "text",
            "sha256": file_sha256(env_path),
        }

        elapsed = time.time() - start
        manifest = {
            "manifest_version": "1.0",
            "stage": "rnaseq_fgsea",
            "status": "success",
            "prefix": prefix,
            "job_id": job_id,
            "timestamp": now_iso(),
            "start_time": start_time,
            "end_time": now_iso(),
            "elapsed_sec": elapsed,
            "workspace_root": abs_text(workspace.parent),
            "workspace": abs_text(workspace),
            "input_dir": abs_text(workspace.parent.parent / "input" / job_id),
            "parameters": {
                "workspace": abs_text(workspace),
                "prefix": prefix,
                "collection": args.collection,
                "comparison": args.comparison,
                "min_size": args.min_size,
                "max_size": args.max_size,
                "nperm": args.nperm,
                "seed": args.seed,
                "gene2ko": abs_text(gene2ko),
                "kegg_cache_dir": abs_text(cache_dir),
                "kegg_species_cache_dir": abs_text(Path(args.kegg_species_cache_dir)),
                "kegg_organism": kegg_organism,
                "species_name": args.species_name,
                "kegg_mode": args.kegg_mode,
                "kegg_filter_scope": args.kegg_filter_scope,
                "kegg_species_cache": species_cache,
            },
            "inputs": {
                "de_results": [abs_text(path) for path in de_files],
                "gene2ko": abs_text(gene2ko),
                "link_pathway_ko": abs_text(link_pathway),
                "list_pathway_ko": abs_text(list_pathway),
            },
            "metrics": metrics,
            "artifacts": artifacts,
            "cmd": cmd_log,
            "software": get_r_versions(),
        }
        apply_rnaseq_context(manifest, find_upstream_rnaseq_context(final_dir, prefix, stage="rnaseq_fgsea"))
        artifacts["report"] = {
            "path": relpath(report_path, workspace),
            "full_path": abs_text(report_path),
            "type": "markdown",
        }
        artifacts["manifest"] = {
            "path": relpath(manifest_path, workspace),
            "full_path": abs_text(manifest_path),
            "type": "json",
        }
        write_report(report_path, manifest)
        artifacts["report"]["sha256"] = file_sha256(report_path)
        write_manifest(manifest_path, manifest)
        print(f"✅ rnaseq_fgsea completed: {manifest_path}")
        return 0
    except Exception as exc:
        elapsed = time.time() - start
        manifest = {
            "manifest_version": "1.0",
            "stage": "rnaseq_fgsea",
            "status": "failed",
            "prefix": prefix if "prefix" in locals() else args.prefix,
            "job_id": job_id if "job_id" in locals() else args.job_id,
            "timestamp": now_iso(),
            "start_time": start_time,
            "end_time": now_iso(),
            "elapsed_sec": elapsed,
            "workspace_root": abs_text(workspace.parent) if "workspace" in locals() else "",
            "workspace": abs_text(workspace) if "workspace" in locals() else "",
            "input_dir": abs_text(workspace.parent.parent / "input" / job_id) if "workspace" in locals() and "job_id" in locals() else "",
            "parameters": vars(args),
            "metrics": metrics,
            "artifacts": artifacts,
            "error": str(exc),
            "cmd": cmd_log,
        }
        if "final_dir" in locals():
            apply_rnaseq_context(manifest, find_upstream_rnaseq_context(final_dir, manifest.get("prefix", ""), stage="rnaseq_fgsea"))
        write_manifest(manifest_path if "manifest_path" in locals() else Path("rnaseq_fgsea.manifest.json"), manifest)
        print(f"❌ rnaseq_fgsea failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
