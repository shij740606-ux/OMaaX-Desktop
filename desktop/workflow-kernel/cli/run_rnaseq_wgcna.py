#!/usr/bin/env python3
"""RNA-seq WGCNA stage.

Generic WGCNA workflow for RNA-seq expression matrices.  It can reuse an
existing VST/rlog/log2 expression matrix, or build a DESeq2 VST matrix from raw
counts.  It intentionally keeps gene filtering configurable because full TOM
construction over tens of thousands of genes can be impractical.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


PROJECT_ROOT = Path(__file__).absolute().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cli.rnaseq_env import capped_threads, rnaseq_rscript
from gfa_core.rnaseq_context import (
    apply_rnaseq_context,
    build_rnaseq_context,
    find_upstream_rnaseq_context,
    merge_rnaseq_context,
)

RNASEQ_RSCRIPT = rnaseq_rscript()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def abs_text(path: str | Path) -> str:
    return str(Path(path).absolute())


def relpath(path: Path, workspace: Path) -> str:
    path = path.absolute()
    workspace = workspace.absolute()
    try:
        return str(path.relative_to(workspace))
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
    for pattern in ("*.rnaseq_sample_qc.manifest.json", "*.rnaseq_quant.manifest.json", "*.rnaseq_de.manifest.json"):
        for manifest in sorted(final_dir.glob(pattern)):
            data = read_json(manifest)
            if data.get("prefix"):
                return str(data["prefix"])
            token = "." + pattern[2:]
            if manifest.name.endswith(token):
                return manifest.name[: -len(token)]
    for matrix in sorted(final_dir.glob("*.count_matrix.txt")):
        return matrix.name[: -len(".count_matrix.txt")]
    raise FileNotFoundError(f"Cannot infer prefix from {final_dir}")


def find_matrix(final_dir: Path, prefix: str, explicit_expr: str = "", explicit_counts: str = "") -> tuple[Path, str]:
    if explicit_expr:
        path = Path(explicit_expr).absolute()
        if not path.exists():
            raise FileNotFoundError(f"Expression matrix not found: {path}")
        return path, "expression"
    vst = final_dir / f"{prefix}.rnaseq_sample_qc.vst_matrix.tsv"
    if vst.exists() and vst.stat().st_size > 0:
        return vst.absolute(), "expression"
    if explicit_counts:
        path = Path(explicit_counts).absolute()
        if not path.exists():
            raise FileNotFoundError(f"Count matrix not found: {path}")
        return path, "counts"
    counts = final_dir / f"{prefix}.count_matrix.txt"
    if counts.exists() and counts.stat().st_size > 0:
        return counts.absolute(), "counts"
    for path in sorted(final_dir.glob("*.count_matrix.txt")):
        if path.stat().st_size > 0:
            return path.absolute(), "counts"
    raise FileNotFoundError(f"No VST/expression matrix or count matrix found in {final_dir}")


def sample_names_from_matrix(path: Path, matrix_type: str) -> list[str]:
    with path.open("r", encoding="utf-8") as handle:
        header = handle.readline().rstrip("\n").split("\t")
    samples = header[1:]
    featurecounts_meta = {"Chr", "Start", "End", "Strand", "Length"}
    if matrix_type == "counts" and featurecounts_meta.issubset(samples):
        samples = [s for s in samples if s not in featurecounts_meta]
        samples = [re.sub(r"\.sorted$", "", Path(s).stem) for s in samples]
    if len(samples) < 2:
        raise ValueError("WGCNA requires at least two samples")
    return samples


def infer_condition(sample: str) -> str:
    for pattern in (r"^(.+?)[._-](?:rep|r)?\d+$", r"^(.+?)[._-]\d+$", r"^([A-Za-z]+)\d+$"):
        match = re.match(pattern, sample, flags=re.IGNORECASE)
        if match and match.group(1):
            return match.group(1)
    return sample


def load_groups(config: str, sample_table: str, samples: list[str]) -> tuple[dict[str, str], dict[str, str], str]:
    groups: dict[str, str] = {}
    times: dict[str, str] = {}
    source = "inferred_from_sample_names"
    if sample_table:
        source = sample_table
        with Path(sample_table).open("r", encoding="utf-8") as handle:
            header = handle.readline().rstrip("\n").split("\t")
            if "sample" not in header or "condition" not in header:
                raise ValueError("sample table must contain sample and condition columns")
            s_idx = header.index("sample")
            c_idx = header.index("condition")
            t_idx = next((header.index(key) for key in ("time", "timepoint", "hour", "day") if key in header), -1)
            for line in handle:
                parts = line.rstrip("\n").split("\t")
                if len(parts) > max(s_idx, c_idx):
                    groups[parts[s_idx]] = parts[c_idx]
                    if t_idx >= 0 and len(parts) > t_idx and parts[t_idx].strip():
                        times[parts[s_idx]] = parts[t_idx].strip()
    elif config:
        if yaml is None:
            raise RuntimeError("PyYAML is required to parse --config")
        source = config
        cfg = yaml.safe_load(Path(config).read_text(encoding="utf-8")) or {}
        raw_groups = cfg.get("groups") or {}
        if isinstance(raw_groups, dict):
            groups.update({str(k): str(v) for k, v in raw_groups.items()})
        samples_cfg = cfg.get("samples") or {}
        if isinstance(samples_cfg, dict):
            for sample, value in samples_cfg.items():
                if isinstance(value, dict):
                    cond = value.get("condition") or value.get("group") or value.get("treatment")
                    if cond is not None:
                        groups[str(sample)] = str(cond)
                    time_value = value.get("time") or value.get("timepoint") or value.get("hour") or value.get("day")
                    if time_value not in (None, ""):
                        times[str(sample)] = str(time_value)
    for sample in samples:
        groups.setdefault(sample, infer_condition(sample))
    return groups, times, source


def write_sample_table(path: Path, samples: list[str], groups: dict[str, str], times: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        include_time = any(times.get(sample) for sample in samples)
        header = "sample\tcondition"
        if include_time:
            header += "\ttime"
        handle.write(header + "\n")
        for sample in samples:
            row = f"{sample}\t{groups[sample]}"
            if include_time:
                row += f"\t{times.get(sample, '')}"
            handle.write(row + "\n")


def write_r_script(path: Path, threads: int = 12) -> None:
    threads = capped_threads(threads)
    code = r'''
suppressPackageStartupMessages({
  library(WGCNA)
  library(DESeq2)
  library(ggplot2)
  library(pheatmap)
})
options(stringsAsFactors = FALSE)
wgcna_threads <- __WGCNA_THREADS__
Sys.setenv(
  OMP_NUM_THREADS = as.character(wgcna_threads),
  OPENBLAS_NUM_THREADS = as.character(wgcna_threads),
  MKL_NUM_THREADS = as.character(wgcna_threads),
  BLAS_NUM_THREADS = as.character(wgcna_threads)
)
if (requireNamespace("RhpcBLASctl", quietly = TRUE)) {{
  RhpcBLASctl::omp_set_num_threads(wgcna_threads)
  RhpcBLASctl::blas_set_num_threads(wgcna_threads)
}}
allowWGCNAThreads(nThreads = wgcna_threads)

args <- commandArgs(trailingOnly = TRUE)
kv <- list()
for (arg in args) {
  parts <- strsplit(arg, "=", fixed = TRUE)[[1]]
  if (length(parts) == 2) kv[[parts[1]]] <- parts[2]
}
required <- c("matrix", "matrix_type", "sample_table", "out_prefix", "min_mean", "max_genes",
              "min_module_size", "merge_cut_height")
missing <- required[!vapply(required, function(x) !is.null(kv[[x]]) && nzchar(kv[[x]]), logical(1))]
if (length(missing) > 0) stop("Missing arguments: ", paste(missing, collapse = ", "))

read_matrix <- function(path) {
  read.table(path, header = TRUE, row.names = 1, sep = "\t",
             check.names = FALSE, quote = "", comment.char = "")
}

mat <- read_matrix(kv$matrix)
sampleTable <- read.table(kv$sample_table, header = TRUE, sep = "\t", check.names = FALSE, stringsAsFactors = FALSE)
if (!all(c("sample", "condition") %in% colnames(sampleTable))) stop("sample_table must contain sample and condition columns")

featurecounts_meta_cols <- c("Chr", "Start", "End", "Strand", "Length")
if (kv$matrix_type == "counts" && all(featurecounts_meta_cols %in% colnames(mat))) {
  mat <- mat[, setdiff(colnames(mat), featurecounts_meta_cols), drop = FALSE]
  colnames(mat) <- sub("\\.sorted$", "", tools::file_path_sans_ext(basename(colnames(mat))))
}
mat[] <- lapply(mat, function(x) as.numeric(as.character(x)))
if (anyNA(mat)) {
  bad_cols <- colnames(mat)[vapply(mat, function(x) any(is.na(x)), logical(1))]
  stop(paste("Matrix contains non-numeric columns:", paste(bad_cols, collapse = ", ")))
}

missing_samples <- setdiff(colnames(mat), sampleTable$sample)
if (length(missing_samples) > 0) stop("sample_table is missing samples: ", paste(missing_samples, collapse = ", "))
sampleTable <- sampleTable[match(colnames(mat), sampleTable$sample), , drop = FALSE]
row.names(sampleTable) <- sampleTable$sample
sampleTable$condition <- factor(sampleTable$condition)

if (kv$matrix_type == "counts") {
  countData <- round(as.matrix(mat))
  countData <- countData[rowSums(countData) > 0, , drop = FALSE]
  dds <- DESeqDataSetFromMatrix(countData = countData, colData = sampleTable, design = ~ 1)
  sizeFactors(dds) <- estimateSizeFactorsForMatrix(counts(dds))
  vsd <- vst(dds, blind = TRUE)
  exprMatrix <- assay(vsd)
} else {
  exprMatrix <- as.matrix(mat)
}

min_mean <- as.numeric(kv$min_mean)
exprMatrix <- exprMatrix[rowMeans(exprMatrix, na.rm = TRUE) > min_mean, , drop = FALSE]
gene_var <- apply(exprMatrix, 1, var, na.rm = TRUE)
exprMatrix <- exprMatrix[is.finite(gene_var) & gene_var > 0, , drop = FALSE]
gene_var <- gene_var[rownames(exprMatrix)]
max_genes <- as.integer(kv$max_genes)
if (max_genes > 0 && nrow(exprMatrix) > max_genes) {
  keep <- names(sort(gene_var, decreasing = TRUE))[seq_len(max_genes)]
  exprMatrix <- exprMatrix[keep, , drop = FALSE]
}
if (nrow(exprMatrix) < as.integer(kv$min_module_size)) {
  stop("Too few genes remain for WGCNA after filtering: ", nrow(exprMatrix))
}
datExpr <- as.data.frame(t(exprMatrix))

gsg <- goodSamplesGenes(datExpr, verbose = 3)
if (!gsg$allOK) {
  if (sum(!gsg$goodGenes) > 0) datExpr <- datExpr[, gsg$goodGenes, drop = FALSE]
  if (sum(!gsg$goodSamples) > 0) datExpr <- datExpr[gsg$goodSamples, , drop = FALSE]
}
sampleTable <- sampleTable[rownames(datExpr), , drop = FALSE]

sampleTree <- hclust(dist(datExpr), method = "average")
png(paste0(kv$out_prefix, ".sample_tree.png"), width = 1800, height = 1200, res = 220)
plot(sampleTree, main = "Sample clustering for WGCNA", xlab = "", sub = "")
dev.off()
pdf(paste0(kv$out_prefix, ".sample_tree.pdf"), width = 8, height = 5)
plot(sampleTree, main = "Sample clustering for WGCNA", xlab = "", sub = "")
dev.off()

powers <- as.integer(strsplit(kv$powers, ",", fixed = TRUE)[[1]])
sft <- pickSoftThreshold(datExpr, powerVector = powers, verbose = 5)
fit <- as.data.frame(sft$fitIndices)
write.table(fit, file = paste0(kv$out_prefix, ".soft_threshold.tsv"), sep = "\t", quote = FALSE, row.names = FALSE)

softPower <- suppressWarnings(as.numeric(sft$powerEstimate))
if (is.na(softPower) || softPower < 1) {
  fit$absFit <- abs(fit$SFT.R.sq)
  softPower <- fit$Power[which.max(fit$absFit)]
}
if (is.na(softPower) || softPower < 1) softPower <- 6

png(paste0(kv$out_prefix, ".soft_threshold.png"), width = 1800, height = 900, res = 220)
par(mfrow = c(1, 2))
plot(fit$Power, fit$SFT.R.sq, xlab = "Soft Threshold (power)", ylab = "Scale Free Topology Model Fit, signed R^2", type = "n")
text(fit$Power, fit$SFT.R.sq, labels = fit$Power, col = "red")
abline(h = 0.8, col = "red")
plot(fit$Power, fit$mean.k., xlab = "Soft Threshold (power)", ylab = "Mean Connectivity", type = "n")
text(fit$Power, fit$mean.k., labels = fit$Power, col = "red")
dev.off()
pdf(paste0(kv$out_prefix, ".soft_threshold.pdf"), width = 10, height = 5)
par(mfrow = c(1, 2))
plot(fit$Power, fit$SFT.R.sq, xlab = "Soft Threshold (power)", ylab = "Scale Free Topology Model Fit, signed R^2", type = "n")
text(fit$Power, fit$SFT.R.sq, labels = fit$Power, col = "red")
abline(h = 0.8, col = "red")
plot(fit$Power, fit$mean.k., xlab = "Soft Threshold (power)", ylab = "Mean Connectivity", type = "n")
text(fit$Power, fit$mean.k., labels = fit$Power, col = "red")
dev.off()

adjacencyMat <- adjacency(datExpr, power = softPower)
TOM <- TOMsimilarity(adjacencyMat)
dissTOM <- 1 - TOM
geneTree <- hclust(as.dist(dissTOM), method = "average")
dynamicMods <- cutreeDynamic(dendro = geneTree, distM = dissTOM,
                             deepSplit = 2, pamRespectsDendro = FALSE,
                             minClusterSize = as.integer(kv$min_module_size))
moduleColors <- labels2colors(dynamicMods)
MEs0 <- moduleEigengenes(datExpr, colors = moduleColors)$eigengenes
MEs0 <- orderMEs(MEs0)
MEDiss <- 1 - cor(MEs0, use = "p")
METree <- hclust(as.dist(MEDiss), method = "average")
merge <- mergeCloseModules(datExpr, moduleColors, cutHeight = as.numeric(kv$merge_cut_height), verbose = 3)
moduleColors <- merge$colors
MEs <- orderMEs(merge$newMEs)

png(paste0(kv$out_prefix, ".gene_dendrogram_modules.png"), width = 2200, height = 1400, res = 220)
plotDendroAndColors(geneTree, cbind(dynamic = labels2colors(dynamicMods), merged = moduleColors),
                    c("Dynamic Tree Cut", "Merged modules"), dendroLabels = FALSE,
                    hang = 0.03, addGuide = TRUE, guideHang = 0.05,
                    main = "Gene dendrogram and module colors")
dev.off()
pdf(paste0(kv$out_prefix, ".gene_dendrogram_modules.pdf"), width = 11, height = 7)
plotDendroAndColors(geneTree, cbind(dynamic = labels2colors(dynamicMods), merged = moduleColors),
                    c("Dynamic Tree Cut", "Merged modules"), dendroLabels = FALSE,
                    hang = 0.03, addGuide = TRUE, guideHang = 0.05,
                    main = "Gene dendrogram and module colors")
dev.off()

traitData <- data.frame(row.names = rownames(sampleTable))
condition_mm <- model.matrix(~ 0 + condition, data = sampleTable)
colnames(condition_mm) <- sub("^condition", "", colnames(condition_mm))
traitData <- as.data.frame(condition_mm)
moduleTraitCor <- cor(MEs, traitData, use = "p")
moduleTraitPvalue <- corPvalueStudent(moduleTraitCor, nSamples = nrow(datExpr))
moduleTraitPadj <- matrix(
  p.adjust(as.vector(moduleTraitPvalue), method = "BH"),
  nrow = nrow(moduleTraitPvalue), ncol = ncol(moduleTraitPvalue),
  dimnames = dimnames(moduleTraitPvalue)
)
write.table(moduleTraitCor, file = paste0(kv$out_prefix, ".module_trait_correlation.tsv"), sep = "\t", quote = FALSE, col.names = NA)
write.table(moduleTraitPvalue, file = paste0(kv$out_prefix, ".module_trait_pvalue.tsv"), sep = "\t", quote = FALSE, col.names = NA)
write.table(moduleTraitPadj, file = paste0(kv$out_prefix, ".module_trait_padj.tsv"), sep = "\t", quote = FALSE, col.names = NA)
write.table(MEs, file = paste0(kv$out_prefix, ".module_eigengenes.tsv"), sep = "\t", quote = FALSE, col.names = NA)

textMatrix <- paste(signif(moduleTraitCor, 2), "\n(FDR ", signif(moduleTraitPadj, 2), ")", sep = "")
dim(textMatrix) <- dim(moduleTraitCor)
png(paste0(kv$out_prefix, ".module_trait_heatmap.png"), width = 1800, height = 1600, res = 220)
labeledHeatmap(Matrix = moduleTraitCor, xLabels = colnames(traitData), yLabels = names(MEs),
               ySymbols = names(MEs), colorLabels = FALSE, colors = blueWhiteRed(50),
               textMatrix = textMatrix, setStdMargins = FALSE, cex.text = 0.7,
               zlim = c(-1, 1), main = "Module-trait relationships (BH-adjusted P values)")
dev.off()
pdf(paste0(kv$out_prefix, ".module_trait_heatmap.pdf"), width = 8, height = 7)
labeledHeatmap(Matrix = moduleTraitCor, xLabels = colnames(traitData), yLabels = names(MEs),
               ySymbols = names(MEs), colorLabels = FALSE, colors = blueWhiteRed(50),
               textMatrix = textMatrix, setStdMargins = FALSE, cex.text = 0.7,
               zlim = c(-1, 1), main = "Module-trait relationships (BH-adjusted P values)")
dev.off()

geneModule <- data.frame(gene_id = colnames(datExpr), module = moduleColors)
write.table(geneModule, file = paste0(kv$out_prefix, ".gene_modules.tsv"), sep = "\t", quote = FALSE, row.names = FALSE)
moduleDir <- paste0(kv$out_prefix, ".modules")
dir.create(moduleDir, showWarnings = FALSE, recursive = TRUE)
for (color in sort(unique(moduleColors))) {
  genes <- geneModule$gene_id[geneModule$module == color]
  write.table(genes, file = file.path(moduleDir, paste0(color, "_module_genes.txt")),
              row.names = FALSE, col.names = FALSE, quote = FALSE)
}

moduleSizes <- as.data.frame(table(moduleColors))
colnames(moduleSizes) <- c("module", "genes")
write.table(moduleSizes, file = paste0(kv$out_prefix, ".module_sizes.tsv"), sep = "\t", quote = FALSE, row.names = FALSE)

saveRDS(list(datExpr = datExpr, moduleColors = moduleColors, MEs = MEs, softPower = softPower,
             moduleTraitCor = moduleTraitCor, moduleTraitPvalue = moduleTraitPvalue,
             moduleTraitPadj = moduleTraitPadj),
        file = paste0(kv$out_prefix, ".wgcna_result.rds"))

summary <- data.frame(
  metric = c("samples", "input_genes", "genes_used", "soft_power", "modules", "min_module_size", "merge_cut_height"),
  value = c(nrow(datExpr), nrow(mat), ncol(datExpr), softPower, length(unique(moduleColors)),
            as.integer(kv$min_module_size), as.numeric(kv$merge_cut_height))
)
write.table(summary, file = paste0(kv$out_prefix, ".wgcna_summary.tsv"), sep = "\t", quote = FALSE, row.names = FALSE)
'''.replace("__WGCNA_THREADS__", str(threads)).lstrip()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")


def r_versions() -> dict[str, str]:
    cmd = [
        RNASEQ_RSCRIPT,
        "-e",
        "cat(R.version.string, '\\n'); for (p in c('WGCNA','DESeq2','ggplot2','pheatmap')) "
        "cat(p, as.character(utils::packageVersion(p)), '\\n')",
    ]
    versions: dict[str, str] = {}
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=60, check=False)
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


def read_summary(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) == 2:
            try:
                val: Any = float(parts[1])
                if val.is_integer():
                    val = int(val)
            except Exception:
                val = parts[1]
            out[parts[0]] = val
    return out


def add_artifact(artifacts: dict[str, dict[str, Any]], key: str, path: Path, workspace: Path, typ: str) -> None:
    if path.exists():
        path = path.absolute()
        artifacts[key] = {
            "artifact": key,
            "path": relpath(path, workspace),
            "full_path": abs_text(path),
            "type": typ,
            "size": path.stat().st_size,
            "sha256": sha256(path),
        }


def write_report(path: Path, manifest: dict[str, Any]) -> None:
    metrics = manifest.get("metrics", {})
    lines = [
        f"# RNA-seq WGCNA Report: {manifest.get('prefix', '')}",
        "",
        f"- Status: {manifest.get('status')}",
        f"- Samples: {metrics.get('samples', 0)}",
        f"- Genes used: {metrics.get('genes_used', 0)}",
        f"- Soft power: {metrics.get('soft_power', '')}",
        f"- Modules: {metrics.get('modules', 0)}",
        "",
        "## Notes",
        "",
        "- WGCNA is most reliable with larger sample sizes; interpret very small RNA-seq designs cautiously.",
        "- Genes are filtered by expression and variance before TOM construction to keep the analysis tractable.",
        "- Module-trait heatmap labels use Benjamini-Hochberg adjusted P values (FDR) across all displayed module-trait tests; raw P values are retained as a separate artifact.",
        "",
        "## Artifacts",
        "",
    ]
    for key, artifact in manifest.get("artifacts", {}).items():
        lines.append(f"- {key}: `{artifact.get('path')}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cleanup_previous_outputs(final_dir: Path, stage_dir: Path, prefix: str) -> None:
    for path in final_dir.glob(f"{prefix}.rnaseq_wgcna*"):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    for path in stage_dir.glob("*"):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run RNA-seq WGCNA")
    parser.add_argument("-w", "--workspace", required=True, help="workspace/<job_id> directory")
    parser.add_argument("--input-dir", default="", help="Optional input/<job_id> directory for manifest provenance")
    parser.add_argument("-p", "--prefix", default="", help="Output prefix")
    parser.add_argument("-j", "--job-id", default="", help="Job ID")
    parser.add_argument("-c", "--config", default="", help="Optional YAML group config")
    parser.add_argument("--sample-table", default="", help="Optional sample table with sample/condition columns")
    parser.add_argument("--expression-matrix", default="", help="VST/rlog/log2 expression matrix, genes x samples")
    parser.add_argument("--count-matrix", default="", help="Raw count matrix, genes x samples")
    parser.add_argument("--min-mean", type=float, default=1.0, help="Minimum row mean in expression matrix")
    parser.add_argument("--max-genes", type=int, default=8000, help="Top variable genes to keep; 0 keeps all")
    parser.add_argument("--min-module-size", type=int, default=30)
    parser.add_argument("--merge-cut-height", type=float, default=0.25)
    parser.add_argument("--powers", default="1,2,3,4,5,6,7,8,9,10,12,14,16,18,20")
    parser.add_argument("-t", "--cpu", type=int, default=12, help="Maximum total threads for WGCNA and BLAS; capped at 12")
    parser.add_argument("--threads", type=int, default=None, help="Alias for --cpu")
    args = parser.parse_args(argv)

    start = time.time()
    start_time = now_iso()
    workspace = Path(args.workspace).absolute()
    final_dir = workspace / "final"
    stage_dir = workspace / "rnaseq_wgcna"
    logs_dir = workspace / "logs"
    final_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    prefix = infer_prefix(final_dir, args.prefix)
    job_id = args.job_id or workspace.name
    input_dir = Path(args.input_dir).absolute() if args.input_dir else PROJECT_ROOT / "input" / job_id
    workspace_root = workspace.parent
    cleanup_previous_outputs(final_dir, stage_dir, prefix)
    stage_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = final_dir / f"{prefix}.rnaseq_wgcna.manifest.json"
    report_path = final_dir / f"{prefix}.rnaseq_wgcna_report.md"
    out_prefix = final_dir / f"{prefix}.rnaseq_wgcna"
    sample_table_path = stage_dir / "sample_table.tsv"
    r_script = stage_dir / "run_wgcna.R"
    commands_path = stage_dir / "commands.sh"
    env_path = stage_dir / "environment.txt"
    log_path = logs_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_rnaseq_wgcna.log"
    artifacts: dict[str, dict[str, Any]] = {}
    cmd: list[str] = []
    threads = capped_threads(args.threads if args.threads is not None else args.cpu)

    try:
        matrix, matrix_type = find_matrix(final_dir, prefix, args.expression_matrix, args.count_matrix)
        samples = sample_names_from_matrix(matrix, matrix_type)
        groups, times, group_source = load_groups(args.config, args.sample_table, samples)
        write_sample_table(sample_table_path, samples, groups, times)
        write_r_script(r_script, threads=threads)
        env_path.write_text("\n".join(f"{k}: {v}" for k, v in r_versions().items()) + "\n", encoding="utf-8")
        cmd = [
            RNASEQ_RSCRIPT,
            str(r_script),
            f"matrix={matrix}",
            f"matrix_type={matrix_type}",
            f"sample_table={sample_table_path}",
            f"out_prefix={out_prefix}",
            f"min_mean={args.min_mean}",
            f"max_genes={args.max_genes}",
            f"min_module_size={args.min_module_size}",
            f"merge_cut_height={args.merge_cut_height}",
            f"powers={args.powers}",
            f"threads={threads}",
        ]
        commands_path.write_text(" ".join(shlex.quote(part) for part in cmd) + "\n", encoding="utf-8")
        with log_path.open("w", encoding="utf-8") as log_handle:
            log_handle.write("$ " + " ".join(shlex.quote(part) for part in cmd) + "\n")
            proc = subprocess.run(cmd, text=True, stdout=log_handle, stderr=subprocess.STDOUT, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"WGCNA failed; see log: {log_path}")

        files = {
            "soft_threshold": (Path(f"{out_prefix}.soft_threshold.tsv"), "tsv"),
            "soft_threshold_png": (Path(f"{out_prefix}.soft_threshold.png"), "image"),
            "soft_threshold_pdf": (Path(f"{out_prefix}.soft_threshold.pdf"), "pdf"),
            "sample_tree_png": (Path(f"{out_prefix}.sample_tree.png"), "image"),
            "sample_tree_pdf": (Path(f"{out_prefix}.sample_tree.pdf"), "pdf"),
            "gene_dendrogram_modules_png": (Path(f"{out_prefix}.gene_dendrogram_modules.png"), "image"),
            "gene_dendrogram_modules_pdf": (Path(f"{out_prefix}.gene_dendrogram_modules.pdf"), "pdf"),
            "module_trait_correlation": (Path(f"{out_prefix}.module_trait_correlation.tsv"), "tsv"),
            "module_trait_pvalue": (Path(f"{out_prefix}.module_trait_pvalue.tsv"), "tsv"),
            "module_trait_padj": (Path(f"{out_prefix}.module_trait_padj.tsv"), "tsv"),
            "module_trait_heatmap_png": (Path(f"{out_prefix}.module_trait_heatmap.png"), "image"),
            "module_trait_heatmap_pdf": (Path(f"{out_prefix}.module_trait_heatmap.pdf"), "pdf"),
            "module_eigengenes": (Path(f"{out_prefix}.module_eigengenes.tsv"), "tsv"),
            "gene_modules": (Path(f"{out_prefix}.gene_modules.tsv"), "tsv"),
            "module_sizes": (Path(f"{out_prefix}.module_sizes.tsv"), "tsv"),
            "wgcna_result_rds": (Path(f"{out_prefix}.wgcna_result.rds"), "rds"),
            "summary": (Path(f"{out_prefix}.wgcna_summary.tsv"), "tsv"),
            "sample_table": (sample_table_path, "tsv"),
            "commands": (commands_path, "shell"),
            "environment": (env_path, "text"),
            "r_script": (r_script, "rscript"),
        }
        for key, (path, typ) in files.items():
            add_artifact(artifacts, key, path, workspace, typ)
        modules_dir = Path(f"{out_prefix}.modules")
        if modules_dir.exists():
            artifacts["module_gene_lists_dir"] = {
                "artifact": "module_gene_lists_dir",
                "path": relpath(modules_dir, workspace),
                "full_path": abs_text(modules_dir),
                "type": "directory",
            }

        metrics = read_summary(Path(f"{out_prefix}.wgcna_summary.tsv"))
        metrics["conditions_detail"] = {sample: groups[sample] for sample in samples}
        metrics["matrix_type"] = matrix_type
        metrics["module_trait_pvalue_adjustment"] = "BH_global"
        manifest = {
            "manifest_version": "1.0",
            "stage": "rnaseq_wgcna",
            "status": "success",
            "prefix": prefix,
            "job_id": job_id,
            "workspace_root": abs_text(workspace_root),
            "workspace": abs_text(workspace),
            "input_dir": abs_text(input_dir),
            "timestamp": now_iso(),
            "start_time": start_time,
            "end_time": now_iso(),
            "elapsed_sec": time.time() - start,
            "cmd": cmd,
            "parameters": {
                "workspace": abs_text(workspace),
                "input_dir": abs_text(input_dir),
                "analysis_dir": abs_text(stage_dir),
                "final_dir": abs_text(final_dir),
                "prefix": prefix,
                "matrix": abs_text(matrix),
                "matrix_type": matrix_type,
                "config": args.config,
                "sample_table": args.sample_table,
                "group_source": group_source,
                "min_mean": args.min_mean,
                "max_genes": args.max_genes,
                "min_module_size": args.min_module_size,
                "merge_cut_height": args.merge_cut_height,
                "powers": args.powers,
                "module_trait_pvalue_adjustment": "BH_global",
                "cpu": threads,
                "threads": threads,
            },
            "inputs": {
                "matrix": abs_text(matrix),
                "sample_table": abs_text(sample_table_path),
            },
            "metrics": metrics,
            "artifacts": artifacts,
            "software": r_versions(),
        }
        context = merge_rnaseq_context(
            find_upstream_rnaseq_context(final_dir, prefix, stage="rnaseq_wgcna"),
            build_rnaseq_context(
                prefix,
                sample_config=args.config,
                sample_table=sample_table_path,
                count_matrix=matrix,
                foreground_mode="global",
                stage="rnaseq_wgcna",
            ),
            stage="rnaseq_wgcna",
        )
        apply_rnaseq_context(manifest, context)
        artifacts["report"] = {"artifact": "report", "path": relpath(report_path, workspace), "full_path": abs_text(report_path), "type": "markdown"}
        artifacts["manifest"] = {"artifact": "manifest", "path": relpath(manifest_path, workspace), "full_path": abs_text(manifest_path), "type": "json"}
        write_report(report_path, manifest)
        artifacts["report"]["size"] = report_path.stat().st_size
        artifacts["report"]["sha256"] = sha256(report_path)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"✅ rnaseq_wgcna completed: {manifest_path}")
        return 0
    except Exception as exc:
        manifest = {
            "manifest_version": "1.0",
            "stage": "rnaseq_wgcna",
            "status": "failed",
            "prefix": prefix if "prefix" in locals() else args.prefix,
            "job_id": job_id if "job_id" in locals() else args.job_id,
            "workspace_root": abs_text(workspace_root) if "workspace_root" in locals() else "",
            "workspace": abs_text(workspace) if "workspace" in locals() else "",
            "input_dir": abs_text(input_dir) if "input_dir" in locals() else "",
            "timestamp": now_iso(),
            "start_time": start_time,
            "end_time": now_iso(),
            "elapsed_sec": time.time() - start,
            "cmd": cmd,
            "parameters": vars(args),
            "artifacts": artifacts,
            "error": str(exc),
        }
        if "final_dir" in locals():
            context = merge_rnaseq_context(
                find_upstream_rnaseq_context(final_dir, manifest.get("prefix", ""), stage="rnaseq_wgcna"),
                build_rnaseq_context(
                    manifest.get("prefix", ""),
                    sample_config=getattr(args, "config", None),
                    sample_table=getattr(args, "sample_table", None),
                    count_matrix=locals().get("matrix"),
                    foreground_mode="global",
                    stage="rnaseq_wgcna",
                ),
                stage="rnaseq_wgcna",
            )
            apply_rnaseq_context(manifest, context)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"❌ rnaseq_wgcna failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
