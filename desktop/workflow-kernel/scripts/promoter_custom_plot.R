#!/usr/bin/env Rscript
# Custom promoter cis-element replot from existing PlantCARE filtered output.

rm(list = ls())
suppressPackageStartupMessages(library(tidyverse))
suppressPackageStartupMessages(library(ape))
suppressPackageStartupMessages(library(RColorBrewer))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 5) {
  stop("Usage: Rscript promoter_custom_plot.R <tree.nwk> <subfamily.tsv|NA> <plantcare_filtered.tab> <element_desc.csv|NA> <output_prefix> [--elements A,B] [--categories Stress response] [--match-mode exact|contains|regex] [--plot-modes position_distribution,heatmap,barplot] [--top-n 30] [--color-by auto|element|category]")
}

tree_file <- args[1]
subfamily_file <- args[2]
promoter_file <- args[3]
element_desc_file <- args[4]
output_prefix <- args[5]
extra <- args[-seq_len(5)]

parse_opts <- function(tokens) {
  out <- list(
    elements = "",
    categories = "",
    match_mode = "exact",
    plot_modes = "position_distribution,heatmap",
    top_n = "30",
    color_by = "auto"
  )
  i <- 1
  while (i <= length(tokens)) {
    key <- tokens[[i]]
    if (startsWith(key, "--")) {
      name <- sub("^--", "", key)
      value <- ""
      if (i < length(tokens) && !startsWith(tokens[[i + 1]], "--")) {
        value <- tokens[[i + 1]]
        i <- i + 1
      }
      out[[gsub("-", "_", name)]] <- value
    }
    i <- i + 1
  }
  out
}

opts <- parse_opts(extra)

`%||%` <- function(a, b) if (!is.null(a)) a else b

split_csv <- function(x) {
  if (is.null(x) || is.na(x) || !nzchar(trimws(x))) return(character())
  trimws(unlist(strsplit(x, ",", fixed = TRUE)))
}

selected_elements <- split_csv(opts$elements)
selected_categories <- split_csv(opts$categories)
match_mode <- tolower(trimws(opts$match_mode %||% "exact"))
plot_modes <- split_csv(opts$plot_modes)
color_by <- tolower(trimws(opts$color_by %||% "auto"))
top_n <- suppressWarnings(as.integer(opts$top_n))
if (is.na(top_n) || top_n < 1) top_n <- 30
if (!match_mode %in% c("exact", "contains", "regex")) stop("Invalid match mode: ", match_mode)
if (!color_by %in% c("auto", "element", "category")) stop("Invalid color_by: ", color_by)
if (!length(plot_modes)) plot_modes <- c("position_distribution", "heatmap")

cat("=== Promoter custom plot ===\n")
cat("Tree:", tree_file, "\n")
cat("Subfamily:", subfamily_file, "\n")
cat("Promoter:", promoter_file, "\n")
cat("Output prefix:", output_prefix, "\n")
cat("Elements:", paste(selected_elements, collapse = ","), "\n")
cat("Categories:", paste(selected_categories, collapse = ","), "\n")
cat("Match mode:", match_mode, "\n")
cat("Plot modes:", paste(plot_modes, collapse = ","), "\n")
cat("Color by:", color_by, "\n")

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

write_diagnosis <- function(reason, available_elements, available_categories, matched_records = 0) {
  diagnosis_file <- paste0(output_prefix, "_diagnosis.tsv")
  tibble(
    reason = reason,
    selected_elements = paste(selected_elements, collapse = ","),
    selected_categories = paste(selected_categories, collapse = ","),
    match_mode = match_mode,
    matched_records = matched_records,
    available_element_count = length(unique(available_elements)),
    available_categories = paste(unique(available_categories), collapse = ","),
    suggestion = "Reduce filters, choose elements from available PlantCARE terms, or switch match_mode to contains."
  ) %>% write.table(diagnosis_file, sep = "\t", quote = FALSE, row.names = FALSE)
  cat("Diagnosis:", diagnosis_file, "\n")
}

if (!file.exists(tree_file)) stop("Tree file not found: ", tree_file)
if (!file.exists(promoter_file)) stop("Promoter file not found: ", promoter_file)

tree <- ape::read.tree(tree_file)
tree_labels <- tree$tip.label
tree_keys <- normalize_gene(tree_labels)

if (!is.na(subfamily_file) && subfamily_file != "NA" && file.exists(subfamily_file)) {
  subfam <- read.delim(subfamily_file, header = FALSE, stringsAsFactors = FALSE)
  if (ncol(subfam) >= 2) {
    colnames(subfam)[1:2] <- c("label", "subfamily")
    subfam <- subfam %>% mutate(gene = normalize_gene(label)) %>% select(gene, subfamily)
  } else {
    subfam <- tibble(gene = tree_keys, subfamily = "Unclassified")
  }
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
    element = as.character(element),
    position = suppressWarnings(as.numeric(position)),
    length = suppressWarnings(as.numeric(length)),
    description = ifelse(is.na(description), "", as.character(description))
  ) %>%
  filter(gene %in% tree_keys, !is.na(position), !is.na(length), length > 0) %>%
  mutate(
    start = pmax(-2000, -2000 + position - 1),
    end = pmin(0, start + length),
    end = ifelse(end <= start, start + 1, end),
    big7 = classify_big7(element, description)
  )

if (nrow(promoter) == 0) {
  write_diagnosis("No promoter records matched tree labels after ID normalization.", character(), character(), 0)
  stop("No promoter records matched tree labels after ID normalization.")
}

available_elements <- sort(unique(promoter$element))
available_categories <- sort(unique(promoter$big7))

filtered <- promoter
if (length(selected_categories)) {
  filtered <- filtered %>% filter(tolower(big7) %in% tolower(selected_categories))
}
if (length(selected_elements)) {
  if (match_mode == "exact") {
    filtered <- filtered %>% filter(tolower(element) %in% tolower(selected_elements))
  } else if (match_mode == "contains") {
    keep <- Reduce(`|`, lapply(selected_elements, function(term) {
      stringr::str_detect(element, fixed(term, ignore_case = TRUE))
    }))
    filtered <- filtered[keep, , drop = FALSE]
  } else {
    keep <- Reduce(`|`, lapply(selected_elements, function(term) {
      stringr::str_detect(element, regex(term, ignore_case = TRUE))
    }))
    filtered <- filtered[keep, , drop = FALSE]
  }
}

if (nrow(filtered) == 0) {
  write_diagnosis("No promoter records matched custom filters.", available_elements, available_categories, 0)
  stop("No promoter records matched custom filters.")
}

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
filtered$big7 <- factor(filtered$big7, levels = big7_order)

make_named_palette <- function(groups) {
  groups <- unique(as.character(groups))
  groups <- groups[nzchar(groups)]
  if (!length(groups)) return(character())
  base <- c(
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b",
    "#e377c2", "#7f7f7f", "#bcbd22", "#17becf", "#4daf4a", "#984ea3",
    "#a65628", "#f781bf", "#999999", "#66c2a5", "#fc8d62", "#8da0cb",
    "#e78ac3", "#a6d854", "#ffd92f", "#e5c494", "#b3b3b3", "#1b9e77"
  )
  if (length(groups) <= length(base)) {
    cols <- base[seq_along(groups)]
  } else {
    cols <- grDevices::colorRampPalette(base)(length(groups))
  }
  stats::setNames(cols, groups)
}

present_elements <- filtered %>% count(element, sort = TRUE) %>% pull(element)
present_categories <- unique(as.character(filtered$big7))
resolved_color_by <- color_by
if (resolved_color_by == "auto") {
  if (length(selected_elements) > 0) {
    resolved_color_by <- "element"
  } else if (length(selected_categories) == 1 && length(present_elements) <= 30) {
    resolved_color_by <- "element"
  } else if (length(present_elements) <= 12) {
    resolved_color_by <- "element"
  } else {
    resolved_color_by <- "category"
  }
}

if (resolved_color_by == "element") {
  selected_present <- present_elements[match(tolower(selected_elements), tolower(present_elements), nomatch = 0)]
  selected_present <- selected_present[!is.na(selected_present) & nzchar(selected_present)]
  if (length(selected_present)) {
    legend_levels <- c(selected_present, setdiff(present_elements, selected_present))
  } else {
    legend_levels <- present_elements
  }
  filtered$legend_group <- factor(filtered$element, levels = legend_levels)
  legend_colors <- make_named_palette(legend_levels)
  legend_title <- "Cis-element"
} else {
  legend_levels <- big7_order[big7_order %in% present_categories]
  filtered$legend_group <- factor(as.character(filtered$big7), levels = legend_levels)
  legend_colors <- big7_colors[legend_levels]
  legend_title <- "Element category"
}
cat("Resolved color by:", resolved_color_by, "\n")
cat("Legend groups:", paste(levels(filtered$legend_group), collapse = ","), "\n")

gene_order <- rev(tree_keys)
gene_labels <- setNames(tree_labels, tree_keys)
filtered$gene <- factor(filtered$gene, levels = gene_order)
subfam <- subfam %>% filter(gene %in% tree_keys)
subfam$gene <- factor(subfam$gene, levels = gene_order)

matches_file <- paste0(output_prefix, "_matches.tsv")
filtered %>%
  mutate(color_by = resolved_color_by, legend_group = as.character(legend_group)) %>%
  select(gene_raw, gene, element, big7, color_by, legend_group, position, length, start, end, strand, description) %>%
  arrange(gene, start, element) %>%
  write.table(matches_file, sep = "\t", quote = FALSE, row.names = FALSE)

element_counts_file <- paste0(output_prefix, "_element_counts.tsv")
filtered %>%
  count(big7, element, legend_group, name = "instances") %>%
  arrange(match(big7, big7_order), desc(instances), element) %>%
  write.table(element_counts_file, sep = "\t", quote = FALSE, row.names = FALSE)

if ("position_distribution" %in% plot_modes) {
  bg <- tibble(gene = factor(gene_order, levels = gene_order), xmin = -2000, xmax = 0)
  plot_h <- max(7, length(gene_order) * 0.28 + 2.4)
  p <- ggplot() +
    geom_segment(data = bg, aes(x = xmin, xend = xmax, y = gene, yend = gene), color = "grey82", linewidth = 2.2, lineend = "round") +
    geom_rect(data = filtered, aes(xmin = start, xmax = end, ymin = as.numeric(gene) - 0.32, ymax = as.numeric(gene) + 0.32, fill = legend_group), color = NA, alpha = 0.88) +
    geom_vline(xintercept = c(-2000, -1500, -1000, -500, 0), color = "grey90", linewidth = 0.25) +
    scale_x_continuous(limits = c(-2000, 0), breaks = c(-2000, -1500, -1000, -500, 0), expand = expansion(mult = c(0.01, 0.02))) +
    scale_y_discrete(labels = gene_labels[gene_order], expand = expansion(add = 0.5)) +
    scale_fill_manual(values = legend_colors, drop = TRUE, name = legend_title) +
    labs(x = "Position upstream of TSS (bp)", y = NULL, title = "Custom promoter cis-element position distribution") +
    theme_bw(base_size = 11) +
    theme(panel.grid.major.y = element_blank(), panel.grid.minor = element_blank(), axis.text.y = element_text(size = 7), plot.title = element_text(hjust = 0.5, face = "bold"), legend.position = "right")
  ggsave(paste0(output_prefix, "_position_distribution.pdf"), p, width = 14, height = plot_h, limitsize = FALSE)
  png(paste0(output_prefix, "_position_distribution.png"), width = 1960, height = round(plot_h * 140), res = 140, bg = "white")
  print(p)
  dev.off()
}

if ("heatmap" %in% plot_modes) {
  top_elements <- filtered %>% count(element, sort = TRUE) %>% slice_head(n = top_n) %>% pull(element)
  heat_data <- expand.grid(gene = gene_order, element = top_elements, stringsAsFactors = FALSE) %>%
    left_join(filtered %>% filter(element %in% top_elements) %>% count(gene = as.character(gene), element, name = "count"), by = c("gene", "element")) %>%
    mutate(count = replace_na(count, 0), gene = factor(gene, levels = gene_order), element = factor(element, levels = rev(top_elements)))
  heat_file <- paste0(output_prefix, "_heatmap_matrix.tsv")
  heat_data %>% write.table(heat_file, sep = "\t", quote = FALSE, row.names = FALSE)
  plot_h <- max(6, length(gene_order) * 0.22 + 2)
  plot_w <- max(8, length(top_elements) * 0.22 + 5)
  p_heat <- ggplot(heat_data, aes(x = element, y = gene, fill = count)) +
    geom_tile(color = "white", linewidth = 0.1) +
    scale_y_discrete(labels = gene_labels[gene_order], expand = c(0, 0)) +
    scale_fill_gradient(low = "white", high = "#2166ac", name = "Count") +
    labs(x = NULL, y = NULL, title = "Custom promoter element count heatmap") +
    theme_bw(base_size = 10) +
    theme(axis.text.x = element_text(angle = 45, hjust = 1, vjust = 1, size = 8), axis.text.y = element_text(size = 7), panel.grid = element_blank(), plot.title = element_text(hjust = 0.5, face = "bold"))
  ggsave(paste0(output_prefix, "_heatmap.pdf"), p_heat, width = plot_w, height = plot_h, limitsize = FALSE)
  png(paste0(output_prefix, "_heatmap.png"), width = round(plot_w * 140), height = round(plot_h * 140), res = 140, bg = "white")
  print(p_heat)
  dev.off()
}

if ("barplot" %in% plot_modes) {
  bar_data <- filtered %>% count(big7, element, legend_group, name = "instances") %>% arrange(desc(instances)) %>% slice_head(n = top_n)
  plot_h <- max(5, nrow(bar_data) * 0.28 + 2)
  p_bar <- ggplot(bar_data, aes(x = reorder(element, instances), y = instances, fill = legend_group)) +
    geom_col(width = 0.74) +
    coord_flip() +
    scale_fill_manual(values = legend_colors, drop = TRUE, name = legend_title) +
    labs(x = NULL, y = "Instances", title = "Custom promoter element counts") +
    theme_bw(base_size = 11) +
    theme(panel.grid.major.y = element_blank(), plot.title = element_text(hjust = 0.5, face = "bold"))
  ggsave(paste0(output_prefix, "_barplot.pdf"), p_bar, width = 10, height = plot_h, limitsize = FALSE)
  png(paste0(output_prefix, "_barplot.png"), width = 1400, height = round(plot_h * 140), res = 140, bg = "white")
  print(p_bar)
  dev.off()
}

metadata_file <- paste0(output_prefix, "_metadata.tsv")
tibble(
  metric = c("matched_records", "matched_genes", "matched_elements", "selected_elements", "selected_categories", "match_mode", "plot_modes", "requested_color_by", "resolved_color_by", "legend_groups"),
  value = c(
    nrow(filtered),
    length(unique(as.character(filtered$gene))),
    length(unique(filtered$element)),
    paste(selected_elements, collapse = ","),
    paste(selected_categories, collapse = ","),
    match_mode,
    paste(plot_modes, collapse = ","),
    color_by,
    resolved_color_by,
    paste(levels(filtered$legend_group), collapse = ",")
  )
) %>% write.table(metadata_file, sep = "\t", quote = FALSE, row.names = FALSE)

cat("Records plotted:", nrow(filtered), "\n")
cat("Genes plotted:", length(unique(as.character(filtered$gene))), "\n")
cat("Elements plotted:", length(unique(filtered$element)), "\n")
cat("Matches:", matches_file, "\n")
cat("Metadata:", metadata_file, "\n")
