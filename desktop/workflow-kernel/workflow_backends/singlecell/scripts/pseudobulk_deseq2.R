#!/usr/bin/env Rscript
# Rscript pseudobulk_deseq2.R counts.tsv samples.tsv control treated outdir [paired|unpaired]
suppressPackageStartupMessages(library(DESeq2))
args <- commandArgs(trailingOnly=TRUE)
if (length(args) < 5) stop("Need counts.tsv samples.tsv control treated outdir [paired|unpaired]")
counts <- as.matrix(read.delim(args[1], row.names=1, check.names=FALSE))
meta <- read.delim(args[2], stringsAsFactors=FALSE, check.names=FALSE)
control <- args[3]; treated <- args[4]; out <- args[5]
mode <- if(length(args) >= 6) args[6] else "unpaired"
if (!mode %in% c("paired", "unpaired") || control == treated) stop("Invalid design or contrast")
if (dir.exists(out) && length(list.files(out, all.files=TRUE, no..=TRUE))) stop("Output must be empty")
if (!all(c("pb_id", "donor", "group", "cell_type") %in% colnames(meta))) stop("Missing metadata")
if (anyDuplicated(meta$pb_id) || !setequal(colnames(counts), meta$pb_id)) stop("Counts/sample mismatch")
if (any(!is.finite(counts)) || any(counts < 0) || any(counts != round(counts))) stop("Need integer raw counts")
if (any(counts > .Machine$integer.max)) stop("Counts exceed R integer range")
dir.create(out, recursive=TRUE, showWarnings=FALSE)
manifest <- list()
types <- sort(unique(meta$cell_type))
for (i in seq_along(types)) {
  ct <- types[i]
  m <- meta[meta$cell_type == ct & meta$group %in% c(control, treated), , drop=FALSE]
  status <- "success"
  if (!all(c(control,treated) %in% m$group)) status <- "missing_group"
  if (status == "success" && any(table(m$group) < 3)) status <- "fewer_than_3_units_per_group"
  if (status == "success" && mode == "unpaired" && anyDuplicated(m$donor)) status <- "repeated_donor_requires_paired_design"
  if (status == "success" && mode == "paired") {
    tab <- table(m$donor, m$group)
    if (ncol(tab) != 2 || any(tab != 1) || nrow(tab) < 3) status <- "requires_3_complete_donor_pairs"
  }
  file <- sprintf("celltype_%03d.tsv", i)
  error_message <- ""
  if (status == "success") {
    tryCatch({
    m$group <- factor(m$group, levels=c(control, treated))
    m$donor <- factor(m$donor); rownames(m) <- m$pb_id
    design <- if(mode == "paired") ~ donor + group else ~ group
    mm <- model.matrix(design, m)
    if (qr(mm)$rank < ncol(mm)) stop("Rank-deficient design")
    mat <- counts[, m$pb_id, drop=FALSE]
    mat <- mat[rowSums(mat >= 10) >= 3, , drop=FALSE]
    if (!nrow(mat)) { status <- "no_genes_after_filter" } else {
      dds <- DESeqDataSetFromMatrix(round(mat), m, design)
      dds <- DESeq(dds, sfType="poscounts")
      res <- as.data.frame(results(dds, contrast=c("group", treated, control), alpha=0.05))
      res$gene_id <- rownames(res)
      write.table(res[order(res$padj),], file.path(out,file), sep="\t", row.names=FALSE, quote=FALSE)
    }
    }, error=function(e) { status <<- "failed"; error_message <<- conditionMessage(e) })
  }
  manifest[[i]] <- data.frame(cell_type=ct, status=status, file=if(status=="success") file else "", error=error_message)
  write.table(do.call(rbind, manifest), file.path(out,"manifest.tsv"), sep="\t", row.names=FALSE, quote=FALSE)
}
manifest <- do.call(rbind,manifest)
write.table(manifest, file.path(out,"manifest.tsv"), sep="\t", row.names=FALSE, quote=FALSE)
capture.output(sessionInfo(), file=file.path(out,"sessionInfo.txt"))
if (!any(manifest$status == "success")) stop("No cell type had a valid comparison; see manifest.tsv")
