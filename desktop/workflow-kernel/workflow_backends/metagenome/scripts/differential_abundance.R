#!/usr/bin/env Rscript
# Rscript differential_abundance.R counts.tsv samples.csv control outdir
# One factor, independent donors; no percentage or log-normalized input.
suppressPackageStartupMessages(library(ANCOMBC))
a <- commandArgs(trailingOnly=TRUE)
if (length(a) != 4) stop("Need counts.tsv samples.csv reference_group outdir")
x <- as.matrix(read.delim(a[1], row.names=1, check.names=FALSE))
m <- read.csv(a[2], stringsAsFactors=FALSE)
if (!all(c("sample_id","donor","group") %in% names(m))) stop("Missing metadata")
if (anyDuplicated(m$sample_id) || anyDuplicated(m$donor)) stop("Requires independent donors")
if (!setequal(colnames(x), m$sample_id)) stop("Sample mismatch")
if (any(!is.finite(x)) || any(x < 0) || any(x != round(x)) || any(colSums(x) == 0)) stop("Need nonnegative unnormalized read counts")
rownames(m) <- m$sample_id
m <- m[colnames(x),,drop=FALSE]
if (length(unique(m$group)) != 2 || any(table(m$group) < 3) || !a[3] %in% m$group) stop("Need two groups, >=3 independent donors/group, valid reference")
m$group <- relevel(factor(m$group), ref=a[3])
if (dir.exists(a[4]) && length(list.files(a[4],all.files=TRUE,no..=TRUE))) stop("Output must be empty")
dir.create(a[4], recursive=TRUE, showWarnings=FALSE)
if (any(table(m$group) <= 3)) warning("Only 3 donors in a group: limited power; interpret as exploratory")
writeLines("Prevalence cutoff: 0.30. With 6 samples, taxa must occur in at least 2. Small groups have limited power.", file.path(a[4], "analysis_notes.txt"))
set.seed(42)
tse <- TreeSummarizedExperiment::TreeSummarizedExperiment(
    assays=list(counts=x), colData=S4Vectors::DataFrame(m))
result <- ancombc2(data=tse, assay_name="counts", fix_formula="group", rand_formula=NULL,
                  p_adj_method="BH", pseudo_sens=TRUE, prv_cut=0.30, lib_cut=0,
                  group="group", struc_zero=FALSE, neg_lb=FALSE, alpha=0.05,
                  n_cl=1, global=FALSE, pairwise=FALSE, dunnet=FALSE, trend=FALSE)
write.table(result$res, file.path(a[4],"ancombc2_results.tsv"), sep="\t", row.names=FALSE, quote=FALSE)
saveRDS(result,file.path(a[4],"ancombc2_model.rds"))
capture.output(sessionInfo(),file=file.path(a[4],"sessionInfo.txt"))
