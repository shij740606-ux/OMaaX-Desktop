#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(ape)
  library(ggtree)
  library(tidytree)
  library(ggplot2)
})

args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 1) {
  stop("用法: Rscript plot_tree.R <treefile> [output_dir] [layout: rectangular|circular|both]", call. = FALSE)
}

treefile <- args[1]
output_dir <- if (length(args) >= 2 && args[2] != "") args[2] else dirname(treefile)
tree_layout <- if (length(args) >= 3 && args[3] != "") tolower(args[3]) else "both"
output_prefix <- if (length(args) >= 4 && args[4] != "") args[4] else ""
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
label_offset <- num_arg(5, 0.02)
tip_label_size <- num_arg(6, 2.8)
branch_color <- str_arg(7, "black")
branch_width <- num_arg(8, 0.5)
show_bootstrap <- bool_arg(9, TRUE)
bootstrap_size <- num_arg(10, 2)
tip_point_size <- num_arg(11, 0)
tip_shape <- shape_code(str_arg(12, "circle"), 16)
circular_label_offset <- num_arg(13, 0.8)
circular_label_size <- num_arg(14, 1.8)
marker_mode <- tolower(str_arg(15, "auto"))
if (!marker_mode %in% c("auto", "tip", "species", "role")) marker_mode <- "auto"
if (!tree_layout %in% c("rectangular", "circular", "both")) {
  stop("layout 必须是 rectangular、circular 或 both", call. = FALSE)
}
dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)

tree <- read.tree(treefile)
n_tips <- length(tree$tip.label)
max_id_len <- max(nchar(tree$tip.label))

cat("基因数:", n_tips, " 最长ID:", max_id_len, "字符\n")


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
  palette <- c(tip_shape, 16, 15, 18, 8, 3, 4, 7, 9)
  vals <- rep(palette, length.out = length(species_levels))
  names(vals) <- species_levels
  vals
}

# 提取bootstrap
tree_df <- as_tibble(tree)
tree_df$isTip <- tree_df$node <= n_tips
tree_df$support <- ifelse(
  tree_df$isTip,
  NA_real_,
  suppressWarnings(as.numeric(tree_df$label))
)
tree_df$tip_species <- ifelse(tree_df$isTip, vapply(tree_df$label, infer_tip_species, character(1)), NA_character_)
species_levels <- sort(unique(na.omit(tree_df$tip_species[tree_df$isTip])))
effective_marker_mode <- marker_mode
if (effective_marker_mode == "auto") effective_marker_mode <- ifelse(length(species_levels) > 1, "species", "tip")
if (effective_marker_mode == "species" && length(species_levels) < 2) effective_marker_mode <- "tip"
tree_df$tip_marker <- ifelse(effective_marker_mode == "species", tree_df$tip_species, "Gene tip")
shape_values <- if (effective_marker_mode == "species") species_shape_values(species_levels) else c("Gene tip" = tip_shape)
shape_title <- if (effective_marker_mode == "species") "Species" else "Tip marker"

# 基础图
p_base <- ggtree(tree, layout = "rectangular", branch.length = "none", color = branch_color, size = branch_width) %<+% tree_df
if (show_bootstrap) {
  p_base <- p_base +
    geom_nodepoint(
      aes(fill = cut(support, c(0, 50, 75, 100))),
      shape = 21, size = bootstrap_size, na.rm = TRUE
    ) +
    scale_fill_manual(
      values = c("black", "grey", "white"),
      name = "Bootstrap",
      breaks = c("(75,100]", "(50,75]", "(0,50]")
    )
}
if (tip_point_size > 0) {
  p_base <- p_base +
    geom_tippoint(aes(shape = tip_marker), size = tip_point_size, color = branch_color, na.rm = TRUE) +
    scale_shape_manual(values = shape_values, name = shape_title, na.translate = FALSE)
}
p_base <- p_base + geom_tiplab(size = tip_label_size, offset = label_offset, hjust = 0) + theme_tree2()

# 获取范围
pb <- ggplot_build(p_base)
panel_ranges <- pb$layout$panel_params[[1]]

# 计算x轴范围
id_width <- max_id_len * max(0.05, tip_label_size * 0.045)
x_max_tree <- max(panel_ranges$x.range)

# 关键：右侧留足够空间给图例（图例宽度 + ID末尾到图例的距离）
legend_space <- 3.5  # 图例占据约3.5个x单位
x_max_needed <- x_max_tree + id_width + legend_space

y_min <- min(panel_ranges$y.range)
y_max <- max(panel_ranges$y.range)

cat("需要x_max:", round(x_max_needed, 2), "\n")

# 最终图
p_final <- p_base +
  coord_cartesian(
    xlim = c(0, x_max_needed),
    ylim = c(y_min, y_max),
    clip = "off"
  ) +
  # 图例放最右边，垂直居中
  theme(
    legend.position = c(0.98, 0.5),      # x=0.98最右，y=0.5垂直居中
    legend.justification = c("right", "center"),  # 右对齐，垂直居中
    legend.background = element_rect(color = "grey50", fill = "white", size = 0.3),
    plot.margin = margin(5, 5, 5, 5)
  )

stem <- if (nzchar(output_prefix)) output_prefix else tools::file_path_sans_ext(basename(treefile))

save_plot_pair <- function(plot_obj, png_file, pdf_file, width, height) {
  png(png_file, width = width, height = height, units = "in", res = 300, bg = "white")
  print(plot_obj)
  dev.off()
  cat("PNG saved:", png_file, "\n")
  ggsave(pdf_file, plot_obj, width = width, height = height, limitsize = FALSE)
  cat("PDF saved:", pdf_file, "\n")
}

# 输出矩形树：保留旧文件名，避免破坏既有前端和 manifest 识别。
rect_tip_spacing <- max(0.34, tip_label_size * 0.12)
min_height <- max(8, 2.5 + n_tips * rect_tip_spacing)
if (tree_layout %in% c("rectangular", "both")) {
  save_plot_pair(
    p_final,
    file.path(output_dir, paste0(stem, ".png")),
    file.path(output_dir, paste0(stem, ".pdf")),
    width = max(8.5, 6.5 + max_id_len * tip_label_size * 0.04),
    height = min_height
  )
}

if (tree_layout %in% c("circular", "both")) {
  p_circular <- ggtree(tree, layout = "circular", branch.length = "none", size = branch_width, color = branch_color) %<+% tree_df
  if (show_bootstrap) {
    p_circular <- p_circular +
      geom_nodepoint(
        aes(fill = cut(support, c(0, 50, 75, 100))),
        shape = 21, size = bootstrap_size, na.rm = TRUE
      ) +
      scale_fill_manual(
        values = c("black", "grey", "white"),
        name = "Bootstrap",
        breaks = c("(75,100]", "(50,75]", "(0,50]")
      )
  }
  if (tip_point_size > 0) {
    p_circular <- p_circular +
      geom_tippoint(aes(shape = tip_marker), size = tip_point_size, color = branch_color, na.rm = TRUE) +
      scale_shape_manual(values = shape_values, name = shape_title, na.translate = FALSE)
  }
  p_circular <- p_circular +
    geom_tiplab2(size = circular_label_size, offset = circular_label_offset, align = TRUE, linesize = 0.1) +
    theme_tree() +
    theme(
      legend.position = "right",
      legend.background = element_rect(color = "grey50", fill = "white", size = 0.3),
      plot.margin = margin(5, 5, 5, 5)
    )

  circular_size <- ifelse(n_tips > 120, 12, 10)
  save_plot_pair(
    p_circular,
    file.path(output_dir, paste0(stem, ".circular_tree.png")),
    file.path(output_dir, paste0(stem, ".circular_tree.pdf")),
    width = circular_size,
    height = circular_size
  )
}

cat("完成\n")
