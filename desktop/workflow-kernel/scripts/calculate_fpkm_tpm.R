#!/usr/bin/env Rscript
#============================================================================
# DESeq2-based FPKM/TPM Calculation Module
# Version: 1.0.0
# DESeq2 Version: 1.40.0 (Bioconductor 3.17)
#============================================================================

# Parse command line arguments
args <- commandArgs(trailingOnly = TRUE)

# Default values
count_file <- NULL
gtf_file <- NULL
output_dir <- NULL
prefix <- "rnaseq"

# Parse arguments
for (i in seq(1, length(args), 2)) {
  if (i < length(args)) {
    arg_name <- args[i]
    arg_value <- args[i + 1]
    
    if (arg_name == "--count") count_file <- arg_value
    if (arg_name == "--gtf") gtf_file <- arg_value
    if (arg_name == "--output") output_dir <- arg_value
    if (arg_name == "--prefix") prefix <- arg_value
  }
}

# Validate arguments
if (is.null(count_file) || is.null(gtf_file) || is.null(output_dir)) {
  cat("Usage: Rscript calculate_fpkm_tpm.R [options]\n")
  cat("Options:\n")
  cat("  --count     Path to count matrix file (TSV format)\n")
  cat("  --gtf       Path to GTF annotation file\n")
  cat("  --output    Output directory\n")
  cat("  --prefix    Output file prefix (default: rnaseq)\n")
  quit(status = 1)
}

# Record start time and session info
start_time <- Sys.time()
cat("=" ,rep("=", 60), "\n", sep="")
cat("DESeq2 FPKM/TPM Calculation Module\n")
cat("=" ,rep("=", 60), "\n", sep="")
cat("Start time:", format(start_time), "\n\n")

# Load required libraries
suppressPackageStartupMessages({
  library(DESeq2)
  library(GenomicFeatures)
  library(rtracklayer)
})

# Print session info for reproducibility
cat("Session Info:\n")
print(sessionInfo())
cat("\n")

#============================================================================
# Step 1: Read count matrix
#============================================================================
cat("Step 1: Reading count matrix...\n")

countdata <- read.table(count_file, header = TRUE, row.names = 1, sep = "\t", check.names = FALSE)
cat("  Count matrix dimensions:", nrow(countdata), "genes x", ncol(countdata), "samples\n")
cat("  Sample names:", paste(colnames(countdata), collapse = ", "), "\n\n")

#============================================================================
# Step 2: Extract gene lengths from GTF
#============================================================================
cat("Step 2: Extracting gene lengths from GTF...\n")

# Create TxDb from GTF
txdb <- makeTxDbFromGFF(gtf_file, format = "gtf")

# Get exons by gene
exons_list <- exonsBy(txdb, by = "gene")

# Calculate gene lengths (sum of exon lengths)
gene_lengths <- sapply(exons_list, function(x) sum(width(reduce(x))))

# Convert to data frame
gene_length_df <- data.frame(
  gene_id = names(gene_lengths),
  length = as.numeric(gene_lengths),
  stringsAsFactors = FALSE
)

cat("  Total genes in GTF:", nrow(gene_length_df), "\n")
cat("  Gene length range:", min(gene_length_df$length), "-", max(gene_length_df$length), "bp\n\n")

#============================================================================
# Step 3: Match genes between count matrix and GTF
#============================================================================
cat("Step 3: Matching genes...\n")

# Find common genes
common_genes <- intersect(rownames(countdata), gene_length_df$gene_id)
cat("  Genes in count matrix:", nrow(countdata), "\n")
cat("  Genes with length info:", length(common_genes), "\n")
cat("  Match rate:", round(length(common_genes) / nrow(countdata) * 100, 2), "%\n\n")

# Filter to common genes
countdata_filtered <- countdata[common_genes, , drop = FALSE]
gene_length_filtered <- gene_length_df$length[match(common_genes, gene_length_df$gene_id)]
names(gene_length_filtered) <- common_genes

#============================================================================
# Step 4: Create DESeq2 object and calculate normalized counts
#============================================================================
cat("Step 4: Creating DESeq2 object...\n")

# Create sample data (dummy design since we only need normalization)
sample_data <- data.frame(
  sample = colnames(countdata_filtered),
  condition = rep("A", ncol(countdata_filtered)),
  row.names = colnames(countdata_filtered),
  stringsAsFactors = FALSE
)

# Create DESeqDataSet
dds <- DESeqDataSetFromMatrix(
  countData = round(countdata_filtered),
  colData = sample_data,
  design = ~ 1  # No design, just for normalization
)

# Calculate size factors (normalization)
dds <- estimateSizeFactors(dds)
cat("  Size factors:", paste(round(sizeFactors(dds), 4), collapse = ", "), "\n\n")

#============================================================================
# Step 5: Calculate FPKM
#============================================================================
cat("Step 5: Calculating FPKM...\n")

# Get normalized counts
normalized_counts <- counts(dds, normalized = TRUE)

# Calculate FPKM
# FPKM = (Normalized Counts / Gene Length in kb) / (Total Normalized Counts / 10^6)
gene_lengths_kb <- gene_length_filtered / 1000

fpkm_matrix <- t(t(normalized_counts) / gene_lengths_kb) / (colSums(normalized_counts) / 1e6)

cat("  FPKM matrix dimensions:", nrow(fpkm_matrix), "x", ncol(fpkm_matrix), "\n")
cat("  FPKM range:", round(min(fpkm_matrix), 6), "-", round(max(fpkm_matrix), 2), "\n\n")

#============================================================================
# Step 6: Calculate TPM
#============================================================================
cat("Step 6: Calculating TPM...\n")

# Calculate TPM
# TPM = (Normalized Counts / Gene Length in kb) * (10^6 / sum(Normalized Counts / Gene Length in kb))
rate <- normalized_counts / gene_lengths_kb
tpm_matrix <- t(t(rate) / colSums(rate)) * 1e6

cat("  TPM matrix dimensions:", nrow(tpm_matrix), "x", ncol(tpm_matrix), "\n")
cat("  TPM range:", round(min(tpm_matrix), 6), "-", round(max(tpm_matrix), 2), "\n\n")

# Verify TPM sums to 1 million
cat("  TPM column sums (should be ~1e6):\n")
print(round(colSums(tpm_matrix), 2))
cat("\n")

#============================================================================
# Step 7: Save output files
#============================================================================
cat("Step 7: Saving output files...\n")

# Create output directory if not exists
if (!dir.exists(output_dir)) {
  dir.create(output_dir, recursive = TRUE)
}

# Save FPKM matrix
fpkm_file <- file.path(output_dir, paste0(prefix, ".fpkm_matrix.txt"))
write.table(
  data.frame(gene_id = rownames(fpkm_matrix), fpkm_matrix, check.names = FALSE),
  fpkm_file,
  sep = "\t",
  row.names = FALSE,
  quote = FALSE
)
cat("  FPKM saved to:", fpkm_file, "\n")

# Save TPM matrix
tpm_file <- file.path(output_dir, paste0(prefix, ".tpm_matrix.txt"))
write.table(
  data.frame(gene_id = rownames(tpm_matrix), tpm_matrix, check.names = FALSE),
  tpm_file,
  sep = "\t",
  row.names = FALSE,
  quote = FALSE
)
cat("  TPM saved to:", tpm_file, "\n")

# Save gene lengths
gene_length_file <- file.path(output_dir, paste0(prefix, ".gene_lengths.txt"))
write.table(
  gene_length_df,
  gene_length_file,
  sep = "\t",
  row.names = FALSE,
  quote = FALSE
)
cat("  Gene lengths saved to:", gene_length_file, "\n")

# Save normalization info
norm_info_file <- file.path(output_dir, paste0(prefix, ".normalization_info.txt"))
norm_info <- data.frame(
  sample = colnames(countdata_filtered),
  size_factor = sizeFactors(dds),
  total_counts = colSums(countdata_filtered),
  total_normalized_counts = colSums(normalized_counts)
)
write.table(
  norm_info,
  norm_info_file,
  sep = "\t",
  row.names = FALSE,
  quote = FALSE
)
cat("  Normalization info saved to:", norm_info_file, "\n\n")

#============================================================================
# Step 8: Record completion
#============================================================================
end_time <- Sys.time()
elapsed <- as.numeric(difftime(end_time, start_time, units = "secs"))

cat("=" ,rep("=", 60), "\n", sep="")
cat("Calculation completed successfully!\n")
cat("End time:", format(end_time), "\n")
cat("Elapsed time:", round(elapsed, 2), "seconds\n")
cat("=" ,rep("=", 60), "\n", sep="")

# Return success
quit(status = 0)
