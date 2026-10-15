#!/usr/bin/env Rscript

## =========================================================
## tree_subfamily_plot.R
## 左：系统发育树（含基因 ID、bootstrap、tip 按亚家族着色）
## 中：亚家族竖线（不压 gene ID）
##
## 用法:
##   Rscript tree_subfamily_plot.R <treefile> <subfamily_file> [output_pdf] [layout: rectangular|circular|both]
## =========================================================

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) {
  stop("用法:\n  Rscript tree_subfamily_plot.R <treefile> <subfamily_file> [output_pdf]\n", call. = FALSE)
}

treefile       <- args[1]
subfamily_file <- args[2]

if (length(args) >= 3 && nzchar(args[3])) {
  output_pdf <- args[3]
} else {
  prefix <- sub("\\.[^.]*$", "", treefile)
  output_pdf <- paste0(prefix, ".tree_subfamily.pdf")
}
tree_layout <- if (length(args) >= 4 && nzchar(args[4])) tolower(args[4]) else "both"
if (!tree_layout %in% c("rectangular", "circular", "both")) {
  stop("layout 必须是 rectangular、circular 或 both", call. = FALSE)
}
target_marker_species <- if (length(args) >= 5 && nzchar(args[5])) args[5] else ""
reference_marker_species <- if (length(args) >= 6 && nzchar(args[6])) args[6] else ""
num_arg <- function(idx, default) {
  if (length(args) >= idx && nzchar(args[idx])) {
    val <- suppressWarnings(as.numeric(args[idx]))
    if (is.finite(val)) return(val)
  }
  default
}
bool_arg <- function(idx, default) {
  if (length(args) >= idx && nzchar(args[idx])) {
    return(tolower(args[idx]) %in% c("1", "true", "yes", "y"))
  }
  default
}
str_arg <- function(idx, default) {
  if (length(args) >= idx && nzchar(args[idx])) return(args[idx])
  default
}
shape_code <- function(x, default) {
  x <- tolower(as.character(x))
  switch(x, circle = 16, triangle = 17, square = 15, diamond = 18, star = 8, default)
}
label_offset_param <- num_arg(7, 0.4)
tip_label_size <- num_arg(8, 3)
branch_color <- str_arg(9, "black")
branch_width <- num_arg(10, 0.5)
show_bootstrap <- bool_arg(11, TRUE)
bootstrap_size <- num_arg(12, 2)
tip_point_size <- num_arg(13, 2)
target_shape <- shape_code(str_arg(14, "triangle"), 17)
reference_shape <- shape_code(str_arg(15, "circle"), 16)
gene_shape <- shape_code(str_arg(16, "circle"), 16)
circular_label_offset <- num_arg(17, 0.8)
circular_label_size <- num_arg(18, 1.8)
marker_mode <- tolower(str_arg(19, "auto"))
if (!marker_mode %in% c("auto", "tip", "species", "role")) marker_mode <- "auto"
subfamily_arc_enabled <- bool_arg(20, TRUE)
subfamily_arc_gap <- num_arg(21, 1.2)
subfamily_arc_label_gap <- num_arg(22, 0.6)
subfamily_arc_label_size <- num_arg(23, NA_real_)


infer_tip_species <- function(label) {
  label <- as.character(label)
  if (grepl("|", label, fixed = TRUE)) return(strsplit(label, "|", fixed = TRUE)[[1]][1])
  if (grepl("^At[0-9]g", label, ignore.case = FALSE)) return("At")
  if (grepl("^LITCHI", label, ignore.case = FALSE)) return("LITCHI")
  if (grepl("^LOC_Os", label, ignore.case = FALSE)) return("Os")
  m <- regmatches(label, regexpr("^[A-Za-z]+", label))
  if (length(m) && nzchar(m)) return(m)
  "Unknown"
}

species_shape_values <- function(species_levels) {
  palette <- c(target_shape, reference_shape, gene_shape, 15, 18, 8, 3, 4, 7, 9)
  vals <- rep(palette, length.out = length(species_levels))
  names(vals) <- species_levels
  vals
}

classify_tip_role <- function(label) {
  label <- as.character(label)
  if (!nzchar(target_marker_species) && !nzchar(reference_marker_species)) return("gene")
  if (nzchar(reference_marker_species)) {
    # Support both normalized labels such as At|At1g01010 and native
    # reference labels such as At1g01010 used in IQ-TREE outputs.
    if (grepl(paste0("^", reference_marker_species, "\\|"), label) || grepl(paste0("^", reference_marker_species, "[0-9]"), label)) {
      return("reference")
    }
  }
  "target"
}


script_args_all <- commandArgs(trailingOnly = FALSE)
script_file_arg <- grep("^--file=", script_args_all, value = TRUE)
script_dir <- if (length(script_file_arg) > 0) dirname(normalizePath(sub("^--file=", "", script_file_arg[1]))) else getwd()
source(file.path(script_dir, "subfamily_palette.R"))

suppressPackageStartupMessages({
  library(ape)
  library(ggtree)
  library(tidytree)
  library(dplyr)
  library(ggplot2)
})

## ---------- 1) 读入亚家族文件 ----------
subfam <- read_subfamily_plot_table(subfamily_file)

add_confidence_labels <- function(subfam_df, subfamily_file) {
  add_subfamily_confidence_labels(subfam_df, subfamily_file)
}


subfam <- add_confidence_labels(subfam, subfamily_file)

subfam_levels <- sort(unique(subfam$subfamily))
n_subfam <- length(subfam_levels)
if (n_subfam == 0) stop("在亚家族文件中没有检测到任何 subfamily，请检查格式。", call. = FALSE)

## ---------- 2) 生成跨图一致的亚家族配色 ----------
subfam_cols <- make_subfamily_cols(subfam_levels)

## ---------- 3) 画树：bootstrap(fill) + tip 按 subfamily(color) + gene label ----------
plot_iqtree_by_subfamily <- function(treefile, subfam_df, subfam_cols, label_offset = 0.4) {

  tree <- ape::read.tree(treefile)

  tree_df <- tidytree::as_tibble(tree)
  tree_df$isTip <- tree_df$node <= length(tree$tip.label)

  tree_df <- dplyr::left_join(tree_df, subfam_df, by = "label")
  tree_df$display_label <- ifelse(is.na(tree_df$display_label), tree_df$label, tree_df$display_label)

  ## IQ-TREE 内部节点 label 常放 bootstrap；tip 的 label 是基因名
  tree_df$support <- ifelse(
    tree_df$isTip, NA_real_,
    suppressWarnings(as.numeric(tree_df$label))
  )
  tree_df$tip_role <- ifelse(tree_df$isTip, vapply(tree_df$label, classify_tip_role, character(1)), NA_character_)
  tree_df$tip_species <- ifelse(tree_df$isTip, vapply(tree_df$label, infer_tip_species, character(1)), NA_character_)
  species_levels <- sort(unique(na.omit(tree_df$tip_species[tree_df$isTip])))
  effective_marker_mode <- marker_mode
  if (effective_marker_mode == "auto") {
    effective_marker_mode <- ifelse(length(species_levels) > 1, "species", "tip")
  }
  if (effective_marker_mode == "species" && length(species_levels) < 2) effective_marker_mode <- "tip"
  tree_df$tip_marker <- ifelse(effective_marker_mode == "species", tree_df$tip_species, ifelse(effective_marker_mode == "role", tree_df$tip_role, "Gene tip"))

  p <- ggtree(tree, branch.length = "none", color = branch_color, size = branch_width) %<+% tree_df
  if (show_bootstrap) {
    p <- p +
      geom_nodepoint(
        aes(fill = cut(support, c(0, 50, 75, 100))),
        shape = 21,
        size  = bootstrap_size,
        na.rm = TRUE
      ) +
      scale_fill_manual(
        values = c("black", "grey40", "grey80"),
        guide  = "legend",
        name   = "Bootstrap Percentage (BP)",
        breaks = c("(75,100]", "(50,75]", "(0,50]"),
        labels = expression(BP > 75, 50 < BP * "<=75", BP <= 50)
      ) +
      geom_text2(
        aes(label = ifelse(!isTip & !is.na(support), round(support), "")),
        hjust = -0.2, vjust = -0.4, size = max(1, tip_label_size * 0.75),
        na.rm = TRUE, show.legend = FALSE
      )
  }
  shape_values <- if (effective_marker_mode == "species") species_shape_values(species_levels) else if (effective_marker_mode == "role") c(target = target_shape, reference = reference_shape, gene = gene_shape) else c("Gene tip" = gene_shape)
  shape_title <- if (effective_marker_mode == "species") "Species" else if (effective_marker_mode == "role") "Tip role" else "Tip marker"
  p <- p +
    ## tip：按 subfamily 上色（color），按选择的 marker 模式设置 shape。
    geom_tippoint(
      aes(color = subfamily, shape = tip_marker),
      size  = tip_point_size,
      na.rm = TRUE
    ) +
    scale_color_manual(values = subfam_cols, name = "Subfamily", na.translate = FALSE) +
    scale_shape_manual(
      values = shape_values,
      name = shape_title,
      na.translate = FALSE
    ) +
    ## gene ID
    geom_tiplab(aes(label = display_label), offset = label_offset, size = tip_label_size, hjust = 0) +
    theme_tree() +
    theme(
        legend.position   = "right",
#        legend.position = c(1.5, 0.5),
 #       legend.justification = c(1, 0.5),
        legend.background = element_rect(color = "#969696", fill = "#d9d9d9")
    )

  attr(p, "label_offset") <- label_offset
  p
}

label_offset <- label_offset_param
p_tree <- plot_iqtree_by_subfamily(treefile, subfam, subfam_cols, label_offset = label_offset)

## ---------- 4) 从 ggtree 坐标里计算分区（Panel A / B） ----------
tree_dat <- p_tree$data
tip_dat  <- tree_dat %>%
  dplyr::filter(isTip) %>%
  dplyr::select(label, x, y, subfamily)

if (nrow(tip_dat) == 0) stop("树中没有 tip 节点，请检查 treefile。", call. = FALSE)

x_tree_min <- min(tree_dat$x, na.rm = TRUE)
x_tip_max  <- max(tip_dat$x,   na.rm = TRUE)
max_y      <- max(tree_dat$y,  na.rm = TRUE)

tree_core_width <- x_tip_max - x_tree_min
if (!is.finite(tree_core_width) || tree_core_width <= 0) {
  stop("树的宽度计算异常，请检查 treefile。", call. = FALSE)
}

matched_subfamily_tips <- sum(!is.na(tip_dat$subfamily) & tip_dat$subfamily != "")
cat("Subfamily tips matched:", matched_subfamily_tips, "/", nrow(tip_dat), "\n")
if (matched_subfamily_tips == 0) {
  stop("亚家族文件与树 tip 不匹配：没有任何 tip 匹配到 subfamily。", call. = FALSE)
}

## Panel A：给 gene label 留一块区域；Panel B 只显示右侧亚家族分组竖线和标签。
x_label_left <- x_tip_max + label_offset
subfam_levels_for_layout <- unique(na.omit(tip_dat$subfamily))
max_subfam_len <- ifelse(length(subfam_levels_for_layout) > 0, max(nchar(subfam_levels_for_layout)), 0)
n_subfam_layout <- length(subfam_levels_for_layout)

label_text_width <- max_id_len <- max(nchar(as.character(tip_dat$label)), na.rm = TRUE)
label_panel_width <- max(2.2, tree_core_width * 0.40, max_id_len * tip_label_size * 0.09)

x_A_left  <- x_tree_min
x_A_right <- x_label_left + label_panel_width

gap1 <- max(0.55, tree_core_width * 0.04)
subfamily_text_width <- max(1.8, max_subfam_len * 0.32)
width_B <- max(2.2, tree_core_width * 0.28, subfamily_text_width)

x_B_left  <- x_A_right + gap1
x_B_right <- x_B_left + width_B
x_subfam <- x_B_left + width_B * 0.20
x_right_pad <- max(1.2, tree_core_width * 0.10, subfamily_text_width * 0.45)

## ---------- 5) 计算每个 subfamily 在 y 上的跨度（对齐树 tip y） ----------
subfam_ranges <- make_subfamily_ranges(tip_dat)

## 可选表头（各在自己 panel 上方）
tree_header_x   <- (x_A_left + x_A_right) / 2
subfam_header_x <- (x_B_left + x_B_right) / 2
header_y <- max_y + 1.2

## ---------- 6) 拼合：树 + 亚家族竖线 + 亚家族标签 ----------
p_combo <- p_tree +
  geom_segment(
    data = subfam_ranges,
    inherit.aes = FALSE,
    aes(x = x_subfam, xend = x_subfam, y = ymin, yend = ymax, colour = subfamily),
    linewidth = 1,
    show.legend = FALSE   # 图例用 tip 点来保留（更干净）
  ) +
  geom_text(
    data = subfam_ranges,
    inherit.aes = FALSE,
    aes(x = x_subfam + width_B * 0.05, y = ymid, label = subfamily, colour = subfamily),
    hjust = 0,
    size  = 3,
    show.legend = FALSE
  ) +
  annotate("text", x = tree_header_x,   y = header_y, label = "Phylogenetic tree",
           fontface = "bold", size = 4) +
  annotate("text", x = subfam_header_x, y = header_y, label = "Subfamily",
           fontface = "bold", size = 4) +
  coord_cartesian(xlim = c(x_A_left, x_B_right + x_right_pad), clip = "off") +
  expand_limits(y = header_y + 0.5)

## ---- 确保输出文件后缀合法（避免 .SPL 这种被误当作 device） ----
fix_output_ext <- function(x, default_ext = "pdf") {
  ext <- tolower(tools::file_ext(x))
  ok  <- ext %in% c("pdf", "png", "svg", "tif", "tiff", "jpg", "jpeg")
  if (!ok) x <- paste0(x, ".", default_ext)
  x
}

output_pdf <- fix_output_ext(output_pdf, default_ext = "pdf")

save_plot_pair <- function(plot_obj, pdf_file, width, height, res = 150) {
  dir.create(dirname(pdf_file), showWarnings = FALSE, recursive = TRUE)
  ggsave(pdf_file, plot_obj, width = width, height = height, limitsize = FALSE)
  output_png <- paste0(tools::file_path_sans_ext(pdf_file), ".png")
  png(filename = output_png, width = width, height = height, units = "in", res = res, bg = "white")
  print(plot_obj)
  dev.off()
  cat("已输出: ", pdf_file, "\n")
  cat("同时输出PNG: ", output_png, "\n")
}

if (tree_layout %in% c("rectangular", "both")) {
  tree_inches <- max(4.5, tree_core_width * 0.75)
  label_inches <- max(3.0, max_id_len * tip_label_size * 0.11)
  subfamily_inches <- max(2.0, max_subfam_len * 0.30 + n_subfam_layout * 0.12)
  legend_inches <- max(1.8, n_subfam_layout * 0.18 + 0.8)
  rect_plot_width <- tree_inches + label_inches + subfamily_inches + legend_inches + 2.0
  rect_tip_spacing <- max(0.34, tip_label_size * 0.12)
  rect_plot_height <- max(8, 3.5 + nrow(tip_dat) * rect_tip_spacing)
  save_plot_pair(p_combo, output_pdf, width = rect_plot_width, height = rect_plot_height)
}

if (tree_layout %in% c("circular", "both")) {
  tree <- ape::read.tree(treefile)
  tree_df <- tidytree::as_tibble(tree)
  tree_df$isTip <- tree_df$node <= length(tree$tip.label)
  tree_df <- dplyr::left_join(tree_df, subfam, by = "label")
  tree_df$display_label <- ifelse(is.na(tree_df$display_label), tree_df$label, tree_df$display_label)
  tree_df$support <- ifelse(
    tree_df$isTip, NA_real_,
    suppressWarnings(as.numeric(tree_df$label))
  )
  tree_df$tip_role <- ifelse(tree_df$isTip, vapply(tree_df$label, classify_tip_role, character(1)), NA_character_)
  tree_df$tip_species <- ifelse(tree_df$isTip, vapply(tree_df$label, infer_tip_species, character(1)), NA_character_)
  species_levels <- sort(unique(na.omit(tree_df$tip_species[tree_df$isTip])))
  effective_marker_mode <- marker_mode
  if (effective_marker_mode == "auto") {
    effective_marker_mode <- ifelse(length(species_levels) > 1, "species", "tip")
  }
  if (effective_marker_mode == "species" && length(species_levels) < 2) effective_marker_mode <- "tip"
  tree_df$tip_marker <- ifelse(effective_marker_mode == "species", tree_df$tip_species, ifelse(effective_marker_mode == "role", tree_df$tip_role, "Gene tip"))
  n_tips <- length(tree$tip.label)

  p_circular <- ggtree(tree, layout = "circular", branch.length = "none", size = branch_width, color = branch_color) %<+% tree_df
  if (show_bootstrap) {
    p_circular <- p_circular +
      geom_nodepoint(
        aes(fill = cut(support, c(0, 50, 75, 100))),
        shape = 21,
        size = bootstrap_size,
        na.rm = TRUE
      ) +
      geom_text2(
        aes(label = ifelse(!isTip & !is.na(support), round(support), "")),
        hjust = -0.15, vjust = -0.35, size = max(1, tip_label_size * 0.6),
        na.rm = TRUE, show.legend = FALSE
      ) +
      scale_fill_manual(
        values = c("black", "grey40", "grey80"),
        guide = "legend",
        name = "Bootstrap Percentage (BP)",
        breaks = c("(75,100]", "(50,75]", "(0,50]"),
        labels = expression(BP > 75, 50 < BP * "<=75", BP <= 50)
      )
  }
  shape_values <- if (effective_marker_mode == "species") species_shape_values(species_levels) else if (effective_marker_mode == "role") c(target = target_shape, reference = reference_shape, gene = gene_shape) else c("Gene tip" = gene_shape)
  shape_title <- if (effective_marker_mode == "species") "Species" else if (effective_marker_mode == "role") "Tip role" else "Tip marker"
  circular_effective_label_size <- ifelse(n_tips > 120, min(circular_label_size, 1.2), circular_label_size)
  circular_tip_dat <- p_circular$data %>%
    dplyr::filter(isTip) %>%
    dplyr::select(label, x, y, subfamily, display_label)
  max_display_label_len <- max(nchar(as.character(circular_tip_dat$display_label)), na.rm = TRUE)
  circular_gene_label_width <- max(2.8, max_display_label_len * circular_effective_label_size * 0.16)
  circular_arc_gap <- max(0, subfamily_arc_gap)
  circular_arc_label_gap <- max(0.2, subfamily_arc_label_gap)
  circular_arc_radius <- max(circular_tip_dat$x, na.rm = TRUE) + circular_label_offset + circular_gene_label_width + circular_arc_gap
  circular_arc_label_radius <- circular_arc_radius + circular_arc_label_gap
  circular_arc_label_size_effective <- ifelse(is.finite(subfamily_arc_label_size), subfamily_arc_label_size, max(1.6, circular_effective_label_size * 1.25))
  circular_arc <- make_circular_subfamily_arc_paths(circular_tip_dat, n_tips, circular_arc_radius, label_radius = circular_arc_label_radius)
  has_circular_arc <- isTRUE(subfamily_arc_enabled) && nrow(circular_arc$paths) > 0
  cat("Circular subfamily arc enabled:", has_circular_arc, "segments:", length(unique(circular_arc$paths$subfamily_run_id)), "
")
  cat("Circular outer arc radius:", round(circular_arc_radius, 2), "label radius:", round(circular_arc_label_radius, 2), "gene label width estimate:", round(circular_gene_label_width, 2), "
")

  p_circular <- p_circular +
    geom_tippoint(
      aes(color = subfamily, shape = tip_marker),
      size = tip_point_size,
      na.rm = TRUE
    ) +
    scale_color_manual(values = subfam_cols, name = "Subfamily", na.translate = FALSE) +
    scale_shape_manual(
      values = shape_values,
      name = shape_title,
      na.translate = FALSE
    )
  if (has_circular_arc) {
    p_circular <- p_circular +
      geom_path(
        data = circular_arc$paths,
        inherit.aes = FALSE,
        aes(x = x, y = y, colour = subfamily, group = interaction(subfamily, subfamily_run_id)),
        linewidth = 1.2,
        lineend = "round",
        show.legend = FALSE
      ) +
      geom_text(
        data = circular_arc$labels,
        inherit.aes = FALSE,
        aes(x = x, y = y, label = subfamily, colour = subfamily, angle = angle, hjust = hjust),
        size = circular_arc_label_size_effective,
        vjust = 0.5,
        show.legend = FALSE
      )
  }
  circular_xlim_right <- if (has_circular_arc) {
    circular_arc_label_radius + max(2.6, max(nchar(as.character(circular_arc$labels$subfamily)), na.rm = TRUE) * circular_arc_label_size_effective * 0.10)
  } else {
    max(circular_tip_dat$x, na.rm = TRUE) + circular_label_offset + circular_gene_label_width + 2
  }
  p_circular <- p_circular +
    geom_tiplab2(aes(label = display_label), size = circular_effective_label_size, offset = circular_label_offset, align = TRUE, linesize = 0.1) +
    theme_tree() +
    theme(
      legend.position = "right",
      legend.background = element_rect(color = "#969696", fill = "white"),
      plot.margin = margin(10, 44, 10, 10)
    ) +
    xlim(0, circular_xlim_right)

  circular_pdf <- paste0(tools::file_path_sans_ext(output_pdf), ".circular_tree.pdf")
  circular_size <- ifelse(n_tips > 120, 12, 10)
  save_plot_pair(p_circular, circular_pdf, width = circular_size, height = circular_size)
}
