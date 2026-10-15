#!/usr/bin/env Rscript
# Structure-like promoter cis-element position distribution.

rm(list = ls())
suppressPackageStartupMessages(library(tidyverse))
suppressPackageStartupMessages(library(ape))
suppressPackageStartupMessages(library(RColorBrewer))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 5) {
  stop("Usage: Rscript promoter_position_distribution.R <tree.nwk> <subfamily.tsv> <plantcare_filtered.tab> <element_desc.csv|NA> <output_prefix>")
}

tree_file <- args[1]
subfamily_file <- args[2]
promoter_file <- args[3]
element_desc_file <- args[4]
output_prefix <- args[5]

cat("=== Promoter position distribution plot ===\n")
cat("Tree:", tree_file, "\n")
cat("Subfamily:", subfamily_file, "\n")
cat("Promoter:", promoter_file, "\n")
cat("Output prefix:", output_prefix, "\n")

normalize_gene <- function(x) {
  x %>%
    stringr::str_trim() %>%
    stringr::str_remove("\\.\\d+\\.T\\d+$") %>%
    stringr::str_remove("\\.T\\d+$") %>%
    stringr::str_remove("\\.\\d+$")
}

classify_big7 <- function(element, description = "") {
  text <- paste(element, description)
  dplyr::case_when(
    stringr::str_detect(text, regex("TATA|CAAT|Inr|Y-patch", ignore_case = TRUE)) ~ "Core promoter",
    stringr::str_detect(text, regex("G-box|GT1|I-box|Sp1|Box 4|Box-4|Box.4|GATA|light", ignore_case = TRUE)) ~ "Light response",
    stringr::str_detect(text, regex("ABRE|P-box|GARE|CGTCA|TGACG|TCA|as-1|TGA|AuxRE|ERE|ABA|gibberellin|jasmon|salicylic|auxin|ethylene", ignore_case = TRUE)) ~ "Hormone signaling",
    stringr::str_detect(text, regex("DRE|LTR|ARE|STRE|W.box|W-box|stress|drought|cold|anaerobic", ignore_case = TRUE)) ~ "Stress response",
    stringr::str_detect(text, regex("MYB|MYC|WRKY", ignore_case = TRUE)) ~ "TF binding",
    stringr::str_detect(text, regex("RY|AAGAA|CAT|GCN4|development|meristem|endosperm", ignore_case = TRUE)) ~ "Development",
    TRUE ~ "Other"
  )
}

if (!file.exists(tree_file)) stop("Tree file not found: ", tree_file)
if (!file.exists(promoter_file)) stop("Promoter file not found: ", promoter_file)

tree <- ape::read.tree(tree_file)
tree_labels <- tree$tip.label
tree_keys <- normalize_gene(tree_labels)

subfam <- read.delim(subfamily_file, header = FALSE, stringsAsFactors = FALSE)
if (ncol(subfam) >= 2) {
  colnames(subfam)[1:2] <- c("label", "subfamily")
  subfam <- subfam %>% mutate(gene = normalize_gene(label)) %>% select(gene, subfamily)
} else {
  subfam <- tibble(gene = tree_keys, subfamily = "Unclassified")
}

promoter <- read.delim(promoter_file, header = FALSE, stringsAsFactors = FALSE)
if (ncol(promoter) < 6) stop("Promoter table has fewer than 6 columns: ", promoter_file)
promoter <- promoter[, c(1, 2, 4, 5, 6, min(8, ncol(promoter)))]
colnames(promoter) <- c("gene_raw", "element", "position", "length", "strand", "description")
promoter <- promoter %>%
  mutate(
    gene = normalize_gene(gene_raw),
    position = suppressWarnings(as.numeric(position)),
    length = suppressWarnings(as.numeric(length)),
    description = ifelse(is.na(description), "", as.character(description))
  ) %>%
  filter(gene %in% tree_keys, !is.na(position), !is.na(length), length > 0)

if (nrow(promoter) == 0) {
  stop("No promoter records matched tree labels after ID normalization; skip position distribution plot.")
}

promoter <- promoter %>%
  mutate(
    start = pmax(-2000, -2000 + position - 1),
    end = pmin(0, start + length),
    end = ifelse(end <= start, start + 1, end),
    big7 = classify_big7(element, description)
  )

# Keep tree order so the plot aligns with phylogeny/structure-style panels.
gene_order <- rev(tree_keys)
gene_labels <- setNames(tree_labels, tree_keys)
promoter$gene <- factor(promoter$gene, levels = gene_order)

subfam <- subfam %>% filter(gene %in% tree_keys)
subfam$gene <- factor(subfam$gene, levels = gene_order)

big7_order <- c("Core promoter", "Light response", "Hormone signaling", "Stress response", "TF binding", "Development", "Other")
big7_colors <- c(
  "Core promoter" = "#d73027",
  "Light response" = "#4575b4",
  "Hormone signaling" = "#1a9850",
  "Stress response" = "#984ea3",
  "TF binding" = "#ff7f00",
  "Development" = "#e78ac3",
  "Other" = "#999999"
)
promoter$big7 <- factor(promoter$big7, levels = big7_order)

bg <- tibble(gene = factor(gene_order, levels = gene_order), xmin = -2000, xmax = 0)

label_df <- tibble(gene = factor(gene_order, levels = gene_order), label = gene_labels[gene_order]) %>%
  left_join(subfam %>% mutate(gene_chr = as.character(gene)) %>% select(gene_chr, subfamily), by = c("gene" = "gene_chr"))

plot_h <- max(7, length(gene_order) * 0.28 + 2.4)
plot_w <- 14

p <- ggplot() +
  geom_segment(data = bg, aes(x = xmin, xend = xmax, y = gene, yend = gene), color = "grey82", linewidth = 2.2, lineend = "round") +
  geom_rect(data = promoter, aes(xmin = start, xmax = end, ymin = as.numeric(gene) - 0.32, ymax = as.numeric(gene) + 0.32, fill = big7),
            color = NA, alpha = 0.88) +
  geom_vline(xintercept = c(-2000, -1500, -1000, -500, 0), color = "grey90", linewidth = 0.25) +
  scale_x_continuous(limits = c(-2000, 0), breaks = c(-2000, -1500, -1000, -500, 0), expand = expansion(mult = c(0.01, 0.02))) +
  scale_y_discrete(labels = gene_labels[gene_order], expand = expansion(add = 0.5)) +
  scale_fill_manual(values = big7_colors, drop = FALSE, name = "Element category") +
  labs(x = "Position upstream of TSS (bp)", y = NULL, title = "Promoter cis-element position distribution") +
  theme_bw(base_size = 11) +
  theme(
    panel.grid.major.y = element_blank(),
    panel.grid.minor = element_blank(),
    axis.text.y = element_text(size = 7),
    plot.title = element_text(hjust = 0.5, face = "bold"),
    legend.position = "right"
  )

pdf_file <- paste0(output_prefix, "_position_distribution.pdf")
png_file <- paste0(output_prefix, "_position_distribution.png")
ggsave(pdf_file, p, width = plot_w, height = plot_h, limitsize = FALSE)
png(png_file, width = round(plot_w * 140), height = round(plot_h * 140), res = 140, bg = "white")
print(p)
dev.off()

summary_file <- paste0(output_prefix, "_position_distribution_summary.tsv")
promoter %>%
  count(big7, name = "element_instances") %>%
  arrange(match(big7, big7_order)) %>%
  write.table(summary_file, sep = "\t", quote = FALSE, row.names = FALSE)

cat("Records plotted:", nrow(promoter), "\n")
cat("Genes plotted:", length(unique(as.character(promoter$gene))), "\n")
cat("PDF:", pdf_file, "\n")
cat("PNG:", png_file, "\n")
cat("Summary:", summary_file, "\n")
