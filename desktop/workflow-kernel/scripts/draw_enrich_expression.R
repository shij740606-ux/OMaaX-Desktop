#!/usr/bin/env Rscript
#============================================================================
# Draw Expression Heatmap for Enriched Pathways
# 为富集通路中的基因绘制表达热图
#============================================================================

library(argparser, quietly=TRUE)
library(pheatmap)
library(tidyverse)

# Parse arguments
p <- arg_parser("Draw expression heatmap for enriched pathways")
p <- add_argument(p, "--fpkm", help="FPKM matrix file", type="character")
p <- add_argument(p, "--enrich", help="Enrichment result file (GO or KEGG)", type="character")
p <- add_argument(p, "--sampleinfo", help="Sample info file", type="character")
p <- add_argument(p, "--output", help="Output PDF file", type="character")
p <- add_argument(p, "--top", help="Number of top pathways to plot", type="integer", default=20)
p <- add_argument(p, "--type", help="Enrichment type (GO or KEGG)", type="character", default="GO")

argv <- parse_args(p)

fpkm_file <- argv$fpkm
enrich_file <- argv$enrich
sample_file <- argv$sampleinfo
output_file <- argv$output
top_n <- argv$top
type <- argv$type

cat("=" ,rep("=", 60), "\n", sep="")
cat("Enrichment Expression Heatmap Generator\n")
cat("=" ,rep("=", 60), "\n\n")

cat("Parameters:\n")
cat("  FPKM file:", fpkm_file, "\n")
cat("  Enrichment file:", enrich_file, "\n")
cat("  Sample info:", sample_file, "\n")
cat("  Output:", output_file, "\n")
cat("  Top N pathways:", top_n, "\n")
cat("  Type:", type, "\n\n")

#============================================================================
# Step 1: Read FPKM matrix
#============================================================================
cat("Step 1: Reading FPKM matrix...\n")
fpkm <- read.table(fpkm_file, header = TRUE, row.names = 1, sep = "\t", check.names = FALSE)
cat("  FPKM matrix:", nrow(fpkm), "genes x", ncol(fpkm), "samples\n\n")

#============================================================================
# Step 2: Read sample info
#============================================================================
cat("Step 2: Reading sample info...\n")
sample_info <- read.table(sample_file, header = FALSE, row.names = 1, stringsAsFactors = FALSE)
colnames(sample_info) <- c("group")
sample_info$sample <- rownames(sample_info)
cat("  Samples:", nrow(sample_info), "\n")
cat("  Groups:", paste(unique(sample_info$group), collapse = ", "), "\n\n")

#============================================================================
# Step 3: Read enrichment results
#============================================================================
cat("Step 3: Reading enrichment results...\n")
enrich <- read.table(enrich_file, header = TRUE, sep = "\t", stringsAsFactors = FALSE, quote = "")

# Select top N pathways
if (nrow(enrich) > top_n) {
  enrich_top <- enrich[1:top_n, ]
  cat("  Selected top", top_n, "pathways from", nrow(enrich), "total\n")
} else {
  enrich_top <- enrich
  cat("  Using all", nrow(enrich), "pathways\n")
}

#============================================================================
# Step 4: Process each pathway
#============================================================================
cat("\nStep 4: Processing pathways...\n")

# Determine gene column name
gene_col <- ifelse("geneID" %in% colnames(enrich_top), "geneID", 
                   ifelse("GeneID" %in% colnames(enrich_top), "GeneID",
                          ifelse("genes" %in% colnames(enrich_top), "genes", "geneID")))

# Create multi-page PDF
pdf(output_file, width = 12, height = 10)

pathway_plots <- list()

for (i in 1:nrow(enrich_top)) {
  pathway_id <- enrich_top$ID[i]
  pathway_desc <- enrich_top$Description[i]
  pvalue <- enrich_top$pvalue[i]
  
  # Get genes in this pathway
  if (gene_col %in% colnames(enrich_top)) {
    genes_str <- enrich_top[[gene_col]][i]
    # Split by multiple separators
    genes <- unlist(strsplit(genes_str, "[,;/]"))
    genes <- trimws(genes)
    genes <- genes[genes != ""]
  } else {
    genes <- character(0)
  }
  
  cat("  Pathway", i, ":", pathway_desc, "\n")
  cat("    Genes in pathway:", length(genes), "\n")
  
  if (length(genes) == 0) {
    cat("    Skipping (no genes)\n")
    next
  }
  
  # Extract expression for these genes
  exp_sub <- fpkm[rownames(fpkm) %in% genes, , drop = FALSE]
  
  cat("    Genes found in FPKM:", nrow(exp_sub), "\n")
  
  if (nrow(exp_sub) == 0) {
    cat("    Skipping (no matching genes in FPKM)\n")
    next
  }
  
  # Filter low expression genes
  exp_sub <- exp_sub[apply(exp_sub, 1, sum) >= 1, , drop = FALSE]
  
  if (nrow(exp_sub) == 0) {
    cat("    Skipping (all genes filtered out)\n")
    next
  }
  
  cat("    Genes after filtering:", nrow(exp_sub), "\n")
  
  # Calculate mean expression per group
  sample_groups <- split(sample_info$sample, sample_info$group)
  group_means <- sapply(sample_groups, function(samples) {
    rowMeans(exp_sub[, samples, drop = FALSE], na.rm = TRUE)
  })
  
  # Create annotation for heatmap
  annotation_col <- data.frame(
    Group = sample_info$group,
    row.names = sample_info$sample
  )
  
  # Plot title
  title_text <- paste0(type, ": ", pathway_desc, "\n",
                       "p-value: ", format(pvalue, digits = 3), 
                       " | Genes: ", nrow(exp_sub))
  
  # Draw heatmap
  tryCatch({
    pheatmap(
      log2(exp_sub + 1),
      color = colorRampPalette(c("blue", "white", "red"))(100),
      cluster_rows = TRUE,
      cluster_cols = TRUE,
      show_rownames = nrow(exp_sub) <= 50,  # Only show gene names if <= 50 genes
      show_colnames = TRUE,
      annotation_col = annotation_col,
      main = title_text,
      fontsize_row = 8,
      fontsize_col = 10,
      fontsize = 10
    )
    pathway_plots[[pathway_id]] <- TRUE
    cat("    ✓ Plot generated\n")
  }, error = function(e) {
    cat("    ✗ Error:", e$message, "\n")
  })
}

dev.off()

cat("\n" ,rep("=", 60), "\n", sep="")
cat("Completed!\n")
cat("Total pathways plotted:", length(pathway_plots), "\n")
cat("Output file:", output_file, "\n")
cat("=" ,rep("=", 60), "\n", sep="")

quit(status = 0)
