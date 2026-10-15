#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)

get_arg <- function(name, default = NULL) {
  idx <- which(args == name)
  if (length(idx) == 0 || idx[1] == length(args)) {
    return(default)
  }
  args[idx[1] + 1]
}

samples_file <- get_arg("--samples")
tx2gene_file <- get_arg("--tx2gene")
output_dir <- get_arg("--output")
prefix <- get_arg("--prefix", "rnaseq")
engine <- get_arg("--engine", "salmon")
counts_from_abundance <- get_arg("--counts-from-abundance", "no")

if (is.null(samples_file) || is.null(tx2gene_file) || is.null(output_dir)) {
  cat("Usage: Rscript tximport_abundance.R --samples sample_quant.tsv --tx2gene tx2gene.tsv --output outdir --prefix PREFIX [--engine salmon] [--counts-from-abundance no]\n", file = stderr())
  quit(status = 1)
}

if (!requireNamespace("tximport", quietly = TRUE)) {
  cat("tximport package is not installed in this R environment\n", file = stderr())
  quit(status = 2)
}

samples <- read.table(samples_file, header = TRUE, sep = "\t", quote = "", comment.char = "", check.names = FALSE, stringsAsFactors = FALSE)
required_cols <- c("sample", "quant_file")
if (!all(required_cols %in% colnames(samples))) {
  cat("samples file must contain columns: sample, quant_file\n", file = stderr())
  quit(status = 3)
}

files <- samples$quant_file
names(files) <- samples$sample
missing_files <- files[!file.exists(files)]
if (length(missing_files) > 0) {
  cat("Missing quantification files:\n", file = stderr())
  cat(paste(missing_files, collapse = "\n"), "\n", file = stderr())
  quit(status = 4)
}

tx2gene <- read.table(tx2gene_file, header = TRUE, sep = "\t", quote = "", comment.char = "", check.names = FALSE, stringsAsFactors = FALSE)
if (!all(c("transcript_id", "gene_id") %in% colnames(tx2gene))) {
  cat("tx2gene file must contain columns: transcript_id, gene_id\n", file = stderr())
  quit(status = 5)
}

dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)

txi <- tximport::tximport(
  files,
  type = engine,
  tx2gene = tx2gene[, c("transcript_id", "gene_id")],
  countsFromAbundance = counts_from_abundance,
  dropInfReps = TRUE
)

write_matrix <- function(mat, path) {
  out <- data.frame(gene_id = rownames(mat), mat, check.names = FALSE)
  write.table(out, path, sep = "\t", row.names = FALSE, quote = FALSE)
}

write_matrix(txi$abundance, file.path(output_dir, paste0(prefix, ".", engine, ".tpm_matrix.txt")))
write_matrix(txi$counts, file.path(output_dir, paste0(prefix, ".", engine, ".tximport_counts.txt")))
write_matrix(txi$length, file.path(output_dir, paste0(prefix, ".", engine, ".length_matrix.txt")))

summary <- data.frame(
  metric = c("engine", "samples", "tx2gene_rows", "genes", "counts_from_abundance"),
  value = c(engine, nrow(samples), nrow(tx2gene), nrow(txi$abundance), counts_from_abundance),
  stringsAsFactors = FALSE
)
write.table(summary, file.path(output_dir, paste0(prefix, ".", engine, ".tximport_summary.tsv")), sep = "\t", row.names = FALSE, quote = FALSE)
