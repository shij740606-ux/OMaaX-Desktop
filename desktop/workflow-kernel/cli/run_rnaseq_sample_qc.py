#!/usr/bin/env python3
"""RNA-seq sample quality assessment.

Implements a generic version of the common DESeq2 workflow:
vst(dds) -> PCA -> sample correlation heatmap -> sample distance heatmap ->
hierarchical clustering.  Inputs are a count matrix and optional sample group
configuration; sample groups are never hard-coded to a specific dataset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).absolute().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from typing import Any

from cli.rnaseq_env import rnaseq_rscript
from gfa_core.rnaseq_context import apply_rnaseq_context, build_rnaseq_context

try:
    import yaml
except Exception:  # pragma: no cover - handled at runtime
    yaml = None


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


def abs_text(path: Path | str) -> str:
    return str(Path(path).absolute())


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
    for pattern in ("*.rnaseq_quant.manifest.json", "*.rnaseq_de.manifest.json", "*.genome_prepare.manifest.json"):
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


def find_count_matrix(final_dir: Path, prefix: str, explicit: str = "") -> Path:
    if explicit:
        path = Path(explicit)
        if path.exists():
            return path.absolute()
        raise FileNotFoundError(f"Count matrix not found: {path}")
    candidates = [
        final_dir / f"{prefix}.count_matrix.txt",
        final_dir / "count_matrix.txt",
    ]
    candidates.extend(sorted(final_dir.glob("*.count_matrix.txt")))
    for path in candidates:
        if path.exists() and path.stat().st_size > 0:
            return path.absolute()
    raise FileNotFoundError(f"No count matrix found in {final_dir}")


def sample_names_from_count_matrix(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8") as handle:
        header = handle.readline().rstrip("\n").split("\t")
    if len(header) < 3:
        raise ValueError(f"Count matrix must contain gene_id plus at least two samples: {path}")
    samples = header[1:]
    featurecounts_meta = {"Chr", "Start", "End", "Strand", "Length"}
    if featurecounts_meta.issubset(samples):
        samples = [s for s in samples if s not in featurecounts_meta]
        samples = [re.sub(r"\.sorted$", "", Path(s).stem) for s in samples]
    return samples


def infer_condition(sample: str) -> str:
    value = sample.strip()
    patterns = [
        r"^(.+?)[._-](?:rep|r)?\d+$",
        r"^(.+?)[._-]\d+$",
    ]
    for pattern in patterns:
        match = re.match(pattern, value, flags=re.IGNORECASE)
        if match and match.group(1):
            return match.group(1)
    return value


def normalize_condition(condition: Any, replicate: Any = "") -> tuple[str, bool]:
    """Remove replicate suffixes that were accidentally included in condition.

    Auto-filled RNA-seq configs can produce conditions like Peel.1, Peel.2,
    Peel.3 while also carrying replicate=1/2/3. For sample-level QC the
    biological condition should be Peel and the replicate remains metadata.
    """
    value = str(condition or "").strip()
    rep = str(replicate or "").strip()
    if not value:
        return value, False
    if rep:
        pattern = rf"^(.+?)[._-](?:rep|r)?{re.escape(rep)}$"
        match = re.match(pattern, value, flags=re.IGNORECASE)
        if match and match.group(1).strip():
            return match.group(1).strip(), True
    stripped = infer_condition(value)
    if stripped and stripped != value:
        return stripped, True
    return value, False


def load_groups(config: str, samples: list[str]) -> tuple[dict[str, str], str]:
    groups: dict[str, str] = {}
    source = "inferred_from_sample_names"
    normalized = 0
    if config:
        if yaml is None:
            raise RuntimeError("PyYAML is required to parse --config")
        cfg = yaml.safe_load(Path(config).read_text(encoding="utf-8")) or {}
        raw_groups = cfg.get("groups") or {}
        samples_cfg = cfg.get("samples") or {}
        if isinstance(raw_groups, dict):
            sample_set = set(samples)
            if raw_groups and all(str(k) in sample_set for k in raw_groups.keys()):
                groups.update({str(k): str(v) for k, v in raw_groups.items()})
            else:
                for condition, members in raw_groups.items():
                    if isinstance(members, (list, tuple)):
                        for sample in members:
                            groups[str(sample)] = str(condition)
        if isinstance(samples_cfg, dict):
            for sample, value in samples_cfg.items():
                if isinstance(value, dict):
                    cond = value.get("condition") or value.get("group") or value.get("treatment")
                    if cond is not None:
                        condition, changed = normalize_condition(cond, value.get("replicate", ""))
                        groups[str(sample)] = condition
                        normalized += int(changed)
        for sample, condition in list(groups.items()):
            replicate = ""
            value = samples_cfg.get(sample) if isinstance(samples_cfg, dict) else None
            if isinstance(value, dict):
                replicate = value.get("replicate", "")
            if replicate:
                clean_condition, changed = normalize_condition(condition, replicate)
            else:
                clean_condition, changed = condition, False
            groups[sample] = clean_condition
            normalized += int(changed)
        source = str(config)
        if normalized:
            source = f"{source} (normalized_replicate_suffixes={normalized})"

    missing = [sample for sample in samples if sample not in groups]
    for sample in missing:
        groups[sample] = "Unassigned"
    if len(missing) == len(samples):
        source = "not_declared"
    return groups, source


def load_sample_times(config: str, samples: list[str]) -> dict[str, str]:
    if not config or yaml is None:
        return {}
    cfg = yaml.safe_load(Path(config).read_text(encoding="utf-8")) or {}
    samples_cfg = cfg.get("samples") or {}
    if not isinstance(samples_cfg, dict):
        return {}
    times: dict[str, str] = {}
    for sample in samples:
        meta = samples_cfg.get(sample)
        if not isinstance(meta, dict):
            continue
        for key in ("time", "timepoint", "hour", "day"):
            if meta.get(key) not in (None, ""):
                times[sample] = str(meta[key])
                break
    return times


def write_sample_table(path: Path, samples: list[str], groups: dict[str, str], sample_times: dict[str, str]) -> None:
    has_time = any(sample_times.get(sample, "") for sample in samples)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write("sample\tcondition" + ("\ttime" if has_time else "") + "\n")
        for sample in samples:
            row = f"{sample}\t{groups[sample]}"
            if has_time:
                row += f"\t{sample_times.get(sample, '')}"
            handle.write(row + "\n")


def write_r_script(path: Path) -> None:
    code = r'''
suppressPackageStartupMessages({
  library(DESeq2)
  library(ggplot2)
  library(pheatmap)
})

args <- commandArgs(trailingOnly = TRUE)
kv <- list()
for (arg in args) {
  parts <- strsplit(arg, "=", fixed = TRUE)[[1]]
  if (length(parts) == 2) kv[[parts[1]]] <- parts[2]
}
required <- c("count_matrix", "sample_table", "out_prefix")
missing <- required[!vapply(required, function(x) !is.null(kv[[x]]) && nzchar(kv[[x]]), logical(1))]
if (length(missing) > 0) stop("Missing arguments: ", paste(missing, collapse = ", "))

countData <- read.table(kv$count_matrix, header = TRUE, row.names = 1, sep = "\t",
                        check.names = FALSE, quote = "", comment.char = "")
featurecounts_meta_cols <- c("Chr", "Start", "End", "Strand", "Length")
if (all(featurecounts_meta_cols %in% colnames(countData))) {
  countData <- countData[, setdiff(colnames(countData), featurecounts_meta_cols), drop = FALSE]
  colnames(countData) <- sub("\\.sorted$", "", tools::file_path_sans_ext(basename(colnames(countData))))
}
countData[] <- lapply(countData, function(x) as.numeric(as.character(x)))
if (anyNA(countData)) {
  bad_cols <- colnames(countData)[vapply(countData, function(x) any(is.na(x)), logical(1))]
  stop(paste("Count matrix contains non-numeric columns:", paste(bad_cols, collapse = ", ")))
}
countData <- round(as.matrix(countData))
keep <- rowSums(countData) > 0
countData <- countData[keep, , drop = FALSE]
if (nrow(countData) < 2) stop("Not enough expressed genes after removing all-zero rows")
if (ncol(countData) < 2) stop("At least two samples are required")

sampleTable <- read.table(kv$sample_table, header = TRUE, sep = "\t", check.names = FALSE, stringsAsFactors = FALSE)
if (!all(c("sample", "condition") %in% colnames(sampleTable))) stop("sample_table must contain sample and condition columns")
missing <- setdiff(colnames(countData), sampleTable$sample)
if (length(missing) > 0) stop("sample_table is missing samples: ", paste(missing, collapse = ", "))
sampleTable <- sampleTable[match(colnames(countData), sampleTable$sample), , drop = FALSE]
row.names(sampleTable) <- sampleTable$sample
sampleTable$condition <- factor(sampleTable$condition)

# Sample QC is exploratory. Use blind VST so PCA/correlation are not tied to
# a differential-expression design and still work when a config has no true
# biological replicates per condition.
dds <- DESeqDataSetFromMatrix(countData = countData, colData = sampleTable, design = ~ 1)
sizeFactors(dds) <- estimateSizeFactorsForMatrix(counts(dds))
# The fast VST needs >=1000 genes with normalized mean >5.
# Small reference panels remain valid for exploratory QC: use DESeq2's
# full transformation rather than inventing rows or changing sample design.
eligible_vst_genes <- sum(rowMeans(counts(dds, normalized = TRUE)) > 5)
if (eligible_vst_genes < 1000) {
  message("Sample QC: full varianceStabilizingTransformation; eligible genes=", eligible_vst_genes)
  vsd <- varianceStabilizingTransformation(dds, blind = TRUE)
} else {
  message("Sample QC: fast vst; eligible genes=", eligible_vst_genes)
  vsd <- vst(dds, blind = TRUE)
}
vst_mat <- assay(vsd)
write.table(vst_mat, file = paste0(kv$out_prefix, ".vst_matrix.tsv"), sep = "\t", quote = FALSE, col.names = NA)

pcaData <- plotPCA(vsd, intgroup = "condition", returnData = TRUE)
percentVar <- round(100 * attr(pcaData, "percentVar"), 2)
write.table(pcaData, file = paste0(kv$out_prefix, ".pca_data.tsv"), sep = "\t", quote = FALSE, row.names = FALSE)
write.table(data.frame(PC = paste0("PC", seq_along(percentVar)), percentVar = percentVar),
            file = paste0(kv$out_prefix, ".pca_variance.tsv"), sep = "\t", quote = FALSE, row.names = FALSE)

p <- ggplot(pcaData, aes(x = PC1, y = PC2, color = condition, label = name)) +
  geom_point(size = 4) +
  geom_text(vjust = -0.8, size = 3, show.legend = FALSE) +
  xlab(paste0("PC1: ", percentVar[1], "% variance")) +
  ylab(paste0("PC2: ", percentVar[2], "% variance")) +
  theme_bw(base_size = 12) +
  theme(legend.position = "right")
ggsave(paste0(kv$out_prefix, ".pca.png"), p, width = 7, height = 5, dpi = 300)
ggsave(paste0(kv$out_prefix, ".pca.pdf"), p, width = 7, height = 5)

sampleCor <- cor(vst_mat, method = "pearson")
write.table(sampleCor, file = paste0(kv$out_prefix, ".sample_correlation.tsv"), sep = "\t", quote = FALSE, col.names = NA)
annotation_col <- data.frame(condition = sampleTable$condition)
row.names(annotation_col) <- row.names(sampleTable)
png(paste0(kv$out_prefix, ".sample_correlation_heatmap.png"), width = 1800, height = 1600, res = 220)
pheatmap(sampleCor, annotation_col = annotation_col, annotation_row = annotation_col,
         main = "Sample correlation")
dev.off()
pdf(paste0(kv$out_prefix, ".sample_correlation_heatmap.pdf"), width = 8, height = 7)
pheatmap(sampleCor, annotation_col = annotation_col, annotation_row = annotation_col,
         main = "Sample correlation")
dev.off()

sampleDists <- dist(t(vst_mat))
sampleDistMatrix <- as.matrix(sampleDists)
write.table(sampleDistMatrix, file = paste0(kv$out_prefix, ".sample_distance.tsv"), sep = "\t", quote = FALSE, col.names = NA)
png(paste0(kv$out_prefix, ".sample_distance_heatmap.png"), width = 1800, height = 1600, res = 220)
pheatmap(sampleDistMatrix, annotation_col = annotation_col, annotation_row = annotation_col,
         main = "Sample distance")
dev.off()
pdf(paste0(kv$out_prefix, ".sample_distance_heatmap.pdf"), width = 8, height = 7)
pheatmap(sampleDistMatrix, annotation_col = annotation_col, annotation_row = annotation_col,
         main = "Sample distance")
dev.off()

hc <- hclust(sampleDists)
png(paste0(kv$out_prefix, ".sample_clustering.png"), width = 1800, height = 1200, res = 220)
plot(as.dendrogram(hc), main = "Hierarchical clustering of samples", xlab = "", ylab = "Distance")
dev.off()
pdf(paste0(kv$out_prefix, ".sample_clustering.pdf"), width = 8, height = 5)
plot(as.dendrogram(hc), main = "Hierarchical clustering of samples", xlab = "", ylab = "Distance")
dev.off()

summary <- data.frame(
  metric = c("input_genes", "expressed_genes", "samples", "conditions", "pc1_percent", "pc2_percent"),
  value = c(nrow(read.table(kv$count_matrix, header = TRUE, row.names = 1, sep = "\t",
                            check.names = FALSE, quote = "", comment.char = "")),
            nrow(vst_mat), ncol(vst_mat), length(levels(sampleTable$condition)), percentVar[1], percentVar[2])
)
write.table(summary, file = paste0(kv$out_prefix, ".sample_qc_summary.tsv"), sep = "\t", quote = FALSE, row.names = FALSE)
'''.lstrip()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")


def r_versions() -> dict[str, str]:
    cmd = [
        RNASEQ_RSCRIPT,
        "-e",
        "cat(R.version.string, '\\n'); for (p in c('DESeq2','ggplot2','pheatmap')) "
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
    values: dict[str, Any] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) == 2:
            try:
                val: Any = float(parts[1])
                if val.is_integer():
                    val = int(val)
            except Exception:
                val = parts[1]
            values[parts[0]] = val
    return values


def add_artifact(artifacts: dict[str, dict[str, Any]], key: str, path: Path, workspace: Path, typ: str) -> None:
    if path.exists():
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
        f"# RNA-seq Sample Quality Assessment: {manifest.get('prefix', '')}",
        "",
        f"- Status: {manifest.get('status')}",
        f"- Samples: {metrics.get('samples', 0)}",
        f"- Conditions: {metrics.get('conditions', 0)}",
        f"- Expressed genes used for VST: {metrics.get('expressed_genes', 0)}",
        f"- PC1 variance: {metrics.get('pc1_percent', 0)}%",
        f"- PC2 variance: {metrics.get('pc2_percent', 0)}%",
        f"- Group source: {manifest.get('parameters', {}).get('group_source', '')}",
        "",
        "## Method",
        "",
        "DESeq2 VST was applied to the count matrix, followed by PCA, Pearson sample correlation, sample distance heatmap, and hierarchical clustering.",
        "",
        "## Artifacts",
        "",
    ]
    for key, artifact in manifest.get("artifacts", {}).items():
        lines.append(f"- {key}: `{artifact.get('path')}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RNA-seq sample quality assessment")
    parser.add_argument("-w", "--workspace", required=True, help="workspace/<job_id> directory")
    parser.add_argument("-p", "--prefix", default="", help="Output prefix")
    parser.add_argument("-j", "--job-id", default="", help="Job ID")
    parser.add_argument("-c", "--config", default="", help="Optional YAML with groups or samples condition")
    parser.add_argument("--count-matrix", default="", help="Optional count matrix path")
    args = parser.parse_args(argv)

    start = time.time()
    start_time = now_iso()
    workspace = Path(args.workspace).absolute()
    final_dir = workspace / "final"
    stage_dir = workspace / "rnaseq_sample_qc"
    logs_dir = workspace / "logs"
    stage_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    prefix = infer_prefix(final_dir, args.prefix)
    job_id = args.job_id or workspace.name
    manifest_path = final_dir / f"{prefix}.rnaseq_sample_qc.manifest.json"
    report_path = final_dir / f"{prefix}.rnaseq_sample_qc_report.md"
    out_prefix = final_dir / f"{prefix}.rnaseq_sample_qc"
    sample_table = stage_dir / "sample_table.tsv"
    r_script = stage_dir / "run_sample_qc.R"
    commands_path = stage_dir / "commands.sh"
    env_path = stage_dir / "environment.txt"
    log_path = logs_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_rnaseq_sample_qc.log"
    artifacts: dict[str, dict[str, Any]] = {}
    cmd: list[str] = []

    try:
        count_matrix = find_count_matrix(final_dir, prefix, args.count_matrix)
        samples = sample_names_from_count_matrix(count_matrix)
        groups, group_source = load_groups(args.config, samples)
        sample_times = load_sample_times(args.config, samples)
        write_sample_table(sample_table, samples, groups, sample_times)
        write_r_script(r_script)
        env_path.write_text("\n".join(f"{k}: {v}" for k, v in r_versions().items()) + "\n", encoding="utf-8")

        cmd = [
            RNASEQ_RSCRIPT,
            str(r_script),
            f"count_matrix={count_matrix}",
            f"sample_table={sample_table}",
            f"out_prefix={out_prefix}",
        ]
        commands_path.write_text(" ".join(shlex.quote(part) for part in cmd) + "\n", encoding="utf-8")
        with log_path.open("w", encoding="utf-8") as log_handle:
            log_handle.write("$ " + " ".join(shlex.quote(part) for part in cmd) + "\n")
            proc = subprocess.run(cmd, text=True, stdout=log_handle, stderr=subprocess.STDOUT, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"R sample QC failed; see log: {log_path}")

        files = {
            "vst_matrix": (Path(f"{out_prefix}.vst_matrix.tsv"), "tsv"),
            "pca_data": (Path(f"{out_prefix}.pca_data.tsv"), "tsv"),
            "pca_variance": (Path(f"{out_prefix}.pca_variance.tsv"), "tsv"),
            "pca_png": (Path(f"{out_prefix}.pca.png"), "image"),
            "pca_pdf": (Path(f"{out_prefix}.pca.pdf"), "pdf"),
            "sample_correlation": (Path(f"{out_prefix}.sample_correlation.tsv"), "tsv"),
            "sample_correlation_heatmap_png": (Path(f"{out_prefix}.sample_correlation_heatmap.png"), "image"),
            "sample_correlation_heatmap_pdf": (Path(f"{out_prefix}.sample_correlation_heatmap.pdf"), "pdf"),
            "sample_distance": (Path(f"{out_prefix}.sample_distance.tsv"), "tsv"),
            "sample_distance_heatmap_png": (Path(f"{out_prefix}.sample_distance_heatmap.png"), "image"),
            "sample_distance_heatmap_pdf": (Path(f"{out_prefix}.sample_distance_heatmap.pdf"), "pdf"),
            "sample_clustering_png": (Path(f"{out_prefix}.sample_clustering.png"), "image"),
            "sample_clustering_pdf": (Path(f"{out_prefix}.sample_clustering.pdf"), "pdf"),
            "summary": (Path(f"{out_prefix}.sample_qc_summary.tsv"), "tsv"),
            "sample_table": (sample_table, "tsv"),
            "commands": (commands_path, "shell"),
            "environment": (env_path, "text"),
            "r_script": (r_script, "rscript"),
        }
        for key, (path, typ) in files.items():
            add_artifact(artifacts, key, path, workspace, typ)

        metrics = read_summary(Path(f"{out_prefix}.sample_qc_summary.tsv"))
        metrics["conditions_detail"] = {sample: groups[sample] for sample in samples}
        metrics["groups_declared"] = group_source != "not_declared"
        context = build_rnaseq_context(
            prefix=prefix,
            sample_config=args.config,
            count_matrix=count_matrix,
            sample_table=sample_table if group_source != "not_declared" else None,
            foreground_mode="global",
            stage="rnaseq_sample_qc",
        )
        manifest = {
            "manifest_version": "1.0",
            "stage": "rnaseq_sample_qc",
            "status": "success",
            "prefix": prefix,
            "job_id": job_id,
            "timestamp": now_iso(),
            "start_time": start_time,
            "end_time": now_iso(),
            "elapsed_sec": time.time() - start,
            "cmd": cmd,
            "workspace_root": abs_text(workspace.parent),
            "workspace": abs_text(workspace),
            "input_dir": abs_text(workspace.parent.parent / "input" / job_id),
            "parameters": {
                "workspace": abs_text(workspace),
                "prefix": prefix,
                "count_matrix": abs_text(count_matrix),
                "config": args.config,
                "group_source": group_source,
            },
            "inputs": {
                "count_matrix": abs_text(count_matrix),
                "sample_table": abs_text(sample_table),
            },
            "metrics": metrics,
            "artifacts": artifacts,
            "software": r_versions(),
        }
        apply_rnaseq_context(manifest, context)
        artifacts["report"] = {
            "artifact": "report",
            "path": relpath(report_path, workspace),
            "full_path": abs_text(report_path),
            "type": "markdown",
        }
        artifacts["manifest"] = {
            "artifact": "manifest",
            "path": relpath(manifest_path, workspace),
            "full_path": abs_text(manifest_path),
            "type": "json",
        }
        write_report(report_path, manifest)
        artifacts["report"]["size"] = report_path.stat().st_size
        artifacts["report"]["sha256"] = sha256(report_path)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"✅ rnaseq_sample_qc completed: {manifest_path}")
        return 0
    except Exception as exc:
        manifest = {
            "manifest_version": "1.0",
            "stage": "rnaseq_sample_qc",
            "status": "failed",
            "prefix": prefix if "prefix" in locals() else args.prefix,
            "job_id": job_id if "job_id" in locals() else args.job_id,
            "timestamp": now_iso(),
            "start_time": start_time,
            "end_time": now_iso(),
            "elapsed_sec": time.time() - start,
            "cmd": cmd,
            "parameters": vars(args),
            "artifacts": artifacts,
            "error": str(exc),
        }
        try:
            context = build_rnaseq_context(
                prefix=manifest.get("prefix", args.prefix),
                sample_config=args.config,
                count_matrix=args.count_matrix,
                foreground_mode="global",
                stage="rnaseq_sample_qc",
            )
            apply_rnaseq_context(manifest, context)
        except Exception:
            pass
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"❌ rnaseq_sample_qc failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
