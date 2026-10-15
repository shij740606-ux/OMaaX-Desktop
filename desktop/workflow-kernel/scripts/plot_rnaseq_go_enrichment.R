#!/usr/bin/env Rscript

suppressPackageStartupMessages(library(ggplot2))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3) {
  stop(
    "Usage: Rscript plot_rnaseq_go_enrichment.R <go_enrich_table.tsv> <output_prefix> <GO> [top_n]\n",
    call. = FALSE
  )
}

input_file <- args[[1]]
output_prefix <- args[[2]]
plot_type <- toupper(args[[3]])
top_n <- if (length(args) >= 4) as.integer(args[[4]]) else 20L
if (is.na(top_n) || top_n <= 0) top_n <- 20L

if (plot_type != "GO") {
  stop("plot_rnaseq_go_enrichment.R is GO-only; KEGG plotting is handled by run_rnaseq_enrich.py legacy logic.", call. = FALSE)
}

if (!file.exists(input_file)) {
  stop(sprintf("Input GO enrichment table not found: %s", input_file), call. = FALSE)
}

enrich <- read.delim(
  input_file,
  header = TRUE,
  sep = "\t",
  quote = "",
  check.names = FALSE,
  stringsAsFactors = FALSE
)

required_cols <- c("ID", "Description", "GeneRatio", "BgRatio", "pvalue", "p.adjust", "Count", "geneID")
missing_cols <- setdiff(required_cols, colnames(enrich))
if (length(missing_cols) > 0) {
  stop(sprintf("Missing columns in GO enrichment table: %s", paste(missing_cols, collapse = ", ")), call. = FALSE)
}

if (nrow(enrich) == 0) {
  message("No enriched GO terms found; skip plotting.")
  quit(status = 0)
}

parse_ratio <- function(x) {
  parts <- strsplit(as.character(x), "/", fixed = TRUE)
  vapply(parts, function(item) {
    if (length(item) != 2) return(NA_real_)
    numerator <- suppressWarnings(as.numeric(item[[1]]))
    denominator <- suppressWarnings(as.numeric(item[[2]]))
    if (is.na(numerator) || is.na(denominator) || denominator == 0) return(NA_real_)
    numerator / denominator
  }, numeric(1))
}

wrap_axis_label <- function(x, width = 42L, max_lines = 2L) {
  vapply(as.character(x), function(label) {
    if (is.na(label) || label == "") return("")
    lines <- strwrap(label, width = width)
    if (length(lines) == 0) return(label)
    if (length(lines) > max_lines) {
      lines <- lines[seq_len(max_lines)]
      lines[[max_lines]] <- paste0(sub("\\s+$", "", lines[[max_lines]]), "...")
    }
    paste(lines, collapse = "\n")
  }, character(1))
}

enrich$p.adjust <- suppressWarnings(as.numeric(enrich$p.adjust))
enrich$pvalue <- suppressWarnings(as.numeric(enrich$pvalue))
enrich$Count <- suppressWarnings(as.numeric(enrich$Count))
enrich$RichFactor <- parse_ratio(enrich$GeneRatio)
enrich$Score <- -log10(pmax(enrich$p.adjust, .Machine$double.xmin))
enrich$Label <- ifelse(
  is.na(enrich$Description) | enrich$Description == "" | enrich$Description == enrich$ID,
  enrich$ID,
  enrich$Description
)

enrich <- enrich[order(enrich$p.adjust, enrich$pvalue, decreasing = FALSE), , drop = FALSE]
plot_data <- head(enrich, top_n)
plot_data$DisplayLabel <- wrap_axis_label(plot_data$Label, width = 44L, max_lines = 2L)
plot_data$DisplayLabel <- make.unique(plot_data$DisplayLabel, sep = " ")
plot_data$DisplayLabel <- factor(plot_data$DisplayLabel, levels = rev(plot_data$DisplayLabel))

base_theme <- theme_bw(base_size = 11) +
  theme(
    panel.grid.major.y = element_blank(),
    panel.grid.minor = element_blank(),
    axis.text.y = element_text(size = 8, lineheight = 0.9),
    plot.title = element_text(face = "bold", hjust = 0.5)
  )

bar_title <- sprintf("Top%d GO enrichment", nrow(plot_data))
bar_plot <- ggplot(plot_data, aes(x = DisplayLabel, y = Score, fill = Count)) +
  geom_col(width = 0.72) +
  coord_flip() +
  scale_fill_gradient(low = "#93C5FD", high = "#1D4ED8") +
  labs(
    title = bar_title,
    x = NULL,
    y = "-log10(adjusted p-value)",
    fill = "Gene count"
  ) +
  base_theme

bar_png <- paste0(output_prefix, ".barplot.png")
bar_pdf <- paste0(output_prefix, ".barplot.pdf")
ggsave(bar_png, bar_plot, width = 10.5, height = max(4.5, 0.38 * nrow(plot_data) + 1.5), dpi = 300)
ggsave(bar_pdf, bar_plot, width = 10.5, height = max(4.5, 0.38 * nrow(plot_data) + 1.5))

dot_title <- sprintf("Top%d GO enrichment", nrow(plot_data))
dot_plot <- ggplot(plot_data, aes(x = RichFactor, y = DisplayLabel)) +
  geom_point(aes(size = Count, color = Score), alpha = 0.9) +
  scale_color_gradient(low = "#22C55E", high = "#DC2626") +
  labs(
    title = dot_title,
    x = "Rich factor",
    y = NULL,
    color = "-log10(adj.P)",
    size = "Gene count"
  ) +
  base_theme

dot_png <- paste0(output_prefix, ".dotplot.png")
dot_pdf <- paste0(output_prefix, ".dotplot.pdf")
ggsave(dot_png, dot_plot, width = 10.5, height = max(4.5, 0.38 * nrow(plot_data) + 1.5), dpi = 300)
ggsave(dot_pdf, dot_plot, width = 10.5, height = max(4.5, 0.38 * nrow(plot_data) + 1.5))

cat(paste(c(bar_png, bar_pdf, dot_png, dot_pdf), collapse = "\n"), "\n", sep = "")

if ("Ontology" %in% colnames(enrich)) {
  ontology_data <- enrich
  ontology_data$Ontology <- as.character(ontology_data$Ontology)
  ontology_data$Ontology <- dplyr::recode(
    ontology_data$Ontology,
    "Biological process" = "BP",
    "Cellular component" = "CC",
    "Molecular function" = "MF",
    .default = ontology_data$Ontology
  )
  ontology_data <- ontology_data[
    !is.na(ontology_data$Ontology) &
      ontology_data$Ontology != "" &
      ontology_data$Ontology != "Unknown",
    ,
    drop = FALSE
  ]

  if (nrow(ontology_data) > 0) {
    ontology_data$Percentage <- parse_ratio(ontology_data$GeneRatio) * 100
    ontology_data <- ontology_data[order(ontology_data$Ontology, ontology_data$p.adjust, ontology_data$pvalue), , drop = FALSE]
    ontology_top <- do.call(rbind, lapply(split(ontology_data, ontology_data$Ontology), function(df) {
      head(df, min(10L, nrow(df)))
    }))

    ontology_top$Term <- ifelse(
      is.na(ontology_top$Description) | ontology_top$Description == "" | ontology_top$Description == ontology_top$ID,
      ontology_top$ID,
      ontology_top$Description
    )
    ontology_top$DisplayTerm <- wrap_axis_label(ontology_top$Term, width = 40L, max_lines = 2L)
    ontology_top$DisplayTerm <- make.unique(ontology_top$DisplayTerm, sep = " ")
    ontology_top$DisplayTerm <- factor(ontology_top$DisplayTerm, levels = rev(unique(ontology_top$DisplayTerm)))

    ontology_plot <- ggplot(ontology_top, aes(x = DisplayTerm, y = Percentage, fill = Ontology)) +
      geom_col(width = 0.72) +
      coord_flip() +
      facet_grid(Ontology ~ ., scales = "free_y", space = "free_y") +
      labs(
        title = "GO enrichment by ontology",
        x = NULL,
        y = "Foreground genes (%)",
        fill = "Ontology"
      ) +
      base_theme +
      theme(
        legend.position = "bottom",
        strip.text.y = element_text(angle = 0, face = "bold")
      )

    ontology_png <- paste0(output_prefix, ".ontology_barplot.png")
    ontology_pdf <- paste0(output_prefix, ".ontology_barplot.pdf")
    ontology_height <- max(5, 0.34 * nrow(ontology_top) + 2)
    ggsave(ontology_png, ontology_plot, width = 11, height = ontology_height, dpi = 300)
    ggsave(ontology_pdf, ontology_plot, width = 11, height = ontology_height)
    cat(paste(c(ontology_png, ontology_pdf), collapse = "\n"), "\n", sep = "")
  }
}
