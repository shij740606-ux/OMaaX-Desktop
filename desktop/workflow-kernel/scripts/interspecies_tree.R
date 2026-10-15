#!/usr/bin/env Rscript
# 种间共线性分析 - 物种间进化树绘制专用脚本
# 参考 tree_plot.R 的绘图逻辑：物种颜色区分 + 亚家族竖线（不修改基因ID）

suppressPackageStartupMessages({
  library(ape)
  library(ggtree)
  library(tidytree)
  library(ggplot2)
  library(dplyr)
})

script_args_all <- commandArgs(trailingOnly = FALSE)
script_file_arg <- grep("^--file=", script_args_all, value = TRUE)
script_dir <- if (length(script_file_arg) > 0) dirname(normalizePath(sub("^--file=", "", script_file_arg[1]))) else getwd()
source(file.path(script_dir, "subfamily_palette.R"))

args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 1) {
  stop("Usage: Rscript interspecies_tree.R <treefile> [output_dir] [target_species] [ref_species] [target_subfamily] [ref_subfamily] [gene_to_species]", call. = FALSE)
}

treefile <- args[1]
output_dir <- if (length(args) >= 2 && args[2] != "") args[2] else dirname(treefile)
target_species <- if (length(args) >= 3 && args[3] != "") args[3] else "Target"
ref_species <- if (length(args) >= 4 && args[4] != "") args[4] else "Reference"
target_subfamily_file <- if (length(args) >= 5 && args[5] != "") args[5] else NULL
ref_subfamily_file <- if (length(args) >= 6 && args[6] != "") args[6] else NULL
gene_species_file <- if (length(args) >= 7 && args[7] != "") args[7] else NULL
num_arg <- function(idx, default) {
  if (length(args) >= idx && args[idx] != "") {
    value <- suppressWarnings(as.numeric(args[idx]))
    if (!is.na(value)) return(value)
  }
  default
}
bool_arg <- function(idx, default) {
  if (length(args) >= idx && args[idx] != "") {
    value <- tolower(trimws(args[idx]))
    if (value %in% c("true", "t", "1", "yes", "y")) return(TRUE)
    if (value %in% c("false", "f", "0", "no", "n")) return(FALSE)
  }
  default
}
char_arg <- function(idx, default) {
  if (length(args) >= idx && args[idx] != "") args[idx] else default
}
shape_code <- function(value, default) {
  value <- tolower(as.character(value))
  mapping <- c(circle = 16, triangle = 17, square = 15, diamond = 18, star = 8)
  if (value %in% names(mapping)) return(unname(mapping[[value]]))
  parsed <- suppressWarnings(as.numeric(value))
  if (!is.na(parsed)) return(parsed)
  default
}
label_offset <- num_arg(8, 0.4)
tip_label_size <- num_arg(9, 2.8)
branch_color <- char_arg(10, "black")
branch_width <- num_arg(11, 0.5)
target_shape_name <- char_arg(12, "triangle")
ref_shape_name <- char_arg(13, "circle")
tip_point_size <- num_arg(14, 3.0)
bootstrap_size <- num_arg(15, 2.0)
subfamily_arc_enabled <- bool_arg(16, TRUE)
subfamily_arc_gap <- num_arg(17, 1.2)
subfamily_arc_label_gap <- num_arg(18, 0.6)
subfamily_arc_label_size <- num_arg(19, NA_real_)

dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)

# 检查输入文件
if (!file.exists(treefile)) {
  stop(paste0("树文件不存在: ", treefile), call. = FALSE)
}

tree <- read.tree(treefile)
n_tips <- length(tree$tip.label)
max_id_len <- max(nchar(tree$tip.label))

cat("基因数:", n_tips, " 最长ID:", max_id_len, "字符\n")
cat("目标物种:", target_species, " 参考物种:", ref_species, "\n")

# ========== 读取亚家族文件 ==========
read_subfamily <- function(file_path) {
  if (is.null(file_path) || file_path == "" || !file.exists(file_path)) {
    return(NULL)
  }
  # 读取亚家族文件：基因ID 亚家族名称
  df <- read.table(file_path, header = FALSE, stringsAsFactors = FALSE, 
                   col.names = c("label", "subfamily"), fill = TRUE)
  return(df)
}

target_subfamily_df <- read_subfamily(target_subfamily_file)
ref_subfamily_df <- read_subfamily(ref_subfamily_file)

# 合并亚家族信息
subfamily_df <- rbind(target_subfamily_df, ref_subfamily_df)
if (!is.null(subfamily_df) && nrow(subfamily_df) > 0) {
  cat("亚家族信息:", nrow(subfamily_df), "个基因\n")
  cat("亚家族:", paste(sort(unique(subfamily_df$subfamily)), collapse = ", "), "\n")
} else {
  cat("警告: 未找到亚家族信息\n")
}

# ========== 提取物种信息 ==========
tree_df <- as_tibble(tree)
tree_df$isTip <- tree_df$node <= n_tips

# Extract species label and target/ref group
get_species_from_id <- function(id) {
  if (grepl("^AT[1-5CM]G", id, ignore.case = TRUE)) {
    return("AT")
  }
  m <- regmatches(id, regexpr("^[A-Za-z]{2}", id))
  if (length(m) > 0 && nzchar(m)) return(m)
  return(target_species)
}

tree_df$species <- ifelse(tree_df$isTip,
                          sapply(tree_df$label, get_species_from_id),
                          NA_character_)
tree_df$group <- ifelse(tree_df$isTip,
                        ifelse(grepl("^AT[1-5CM]G", tree_df$label, ignore.case = TRUE), "ref", "target"),
                        NA_character_)

if (!is.null(gene_species_file) && file.exists(gene_species_file)) {
  gene_species_df <- read.table(gene_species_file, header = TRUE, sep = "\t", stringsAsFactors = FALSE)
  if (all(c("gene_id", "species", "group") %in% colnames(gene_species_df))) {
    gene_species_df$label <- gene_species_df$gene_id
    gene_species_df <- gene_species_df[, c("label", "species", "group")]
    tree_df <- dplyr::left_join(tree_df, gene_species_df, by = "label", suffix = c("", ".map"))
    tree_df$species <- ifelse(!is.na(tree_df$species.map), tree_df$species.map, tree_df$species)
    tree_df$group <- ifelse(!is.na(tree_df$group.map), tree_df$group.map, tree_df$group)
    tree_df$species.map <- NULL
    tree_df$group.map <- NULL
    cat("Gene-to-species map:", gene_species_file, "\n")
  } else {
    cat("Warning: gene_to_species file lacks gene_id/species/group columns; using fallback rules\n")
  }
}
tree_df$group <- factor(tree_df$group, levels = c("target", "ref"))

# Merge subfamily info without changing gene IDs
if (!is.null(subfamily_df) && nrow(subfamily_df) > 0) {
  tree_df <- dplyr::left_join(tree_df, subfamily_df, by = "label")
} else {
  tree_df$subfamily <- NA_character_
}

# 提取bootstrap
tree_df$support <- ifelse(
  tree_df$isTip,
  NA_real_,
  suppressWarnings(as.numeric(tree_df$label))
)

# 亚家族颜色
generate_subfamily_colors <- function(n) {
  if (n <= 0) return(character())
  if (exists("hcl.colors", where = asNamespace("grDevices"), inherits = FALSE)) {
    grDevices::hcl.colors(n, palette = "Dark 3")
  } else {
    scales::hue_pal()(n)
  }
}

if (!is.null(subfamily_df) && nrow(subfamily_df) > 0) {
  subfam_levels <- sort(unique(subfamily_df$subfamily))
  n_subfam <- length(subfam_levels)
  subfam_cols_vec <- generate_subfamily_colors(n_subfam)
  subfam_cols <- setNames(subfam_cols_vec, subfam_levels)
} else {
  subfam_cols <- c()
}

# Species/group legend
species_levels <- sort(unique(na.omit(tree_df$species[tree_df$isTip])))
group_labels <- tree_df %>%
  dplyr::filter(isTip, !is.na(group), !is.na(species)) %>%
  dplyr::group_by(group) %>%
  dplyr::summarise(label = paste(sort(unique(species)), collapse = ", "), .groups = "drop")
group_label_map <- setNames(group_labels$label, as.character(group_labels$group))
shape_values <- c(target = shape_code(target_shape_name, 17), ref = shape_code(ref_shape_name, 16))
shape_labels <- c(
  target = ifelse("target" %in% names(group_label_map), paste0(group_label_map[["target"]], " (target)"), "Target"),
  ref = ifelse("ref" %in% names(group_label_map), paste0(group_label_map[["ref"]], " (ref)"), "Reference")
)
cat("Species labels:", paste(species_levels, collapse = ", "), "\n")

# ========== Basic plot ========== 

p_tree <- ggtree(tree, branch.length = "none", color = branch_color, size = branch_width) %<+% tree_df +
  geom_nodepoint(
    aes(fill = cut(support, c(0, 50, 75, 100))),
    shape = 21, size = bootstrap_size, na.rm = TRUE
  ) +
  geom_text2(
    aes(label = ifelse(!isTip & !is.na(support), round(support), "")),
    hjust = -0.2, vjust = -0.4, size = 2.0,
    na.rm = TRUE, show.legend = FALSE
  ) +
  scale_fill_manual(
    values = c("black", "grey40", "grey80"),
    guide = "legend",
    name = "Bootstrap Percentage (BP)",
    breaks = c("(75,100]", "(50,75]", "(0,50]"),
    labels = expression(BP > 75, 50 < BP * "<=75", BP <= 50)
  )

if (length(subfam_cols) > 0) {
  p_tree <- p_tree +
    geom_tippoint(
      aes(color = subfamily, shape = group),
      size = tip_point_size, alpha = 0.8, na.rm = TRUE
    ) +
    scale_color_manual(values = subfam_cols, name = "Subfamily", na.translate = FALSE)
} else {
  p_tree <- p_tree +
    geom_tippoint(
      aes(shape = group),
      color = "#4C78A8", size = tip_point_size, alpha = 0.8, na.rm = TRUE,
      show.legend = TRUE
    )
}

p_tree <- p_tree +
  scale_shape_manual(
    values = shape_values,
    breaks = names(shape_labels),
    labels = shape_labels,
    name = "Species"
  ) +
  geom_tiplab(offset = label_offset, size = tip_label_size, hjust = 0) +
  theme_tree() +
  theme(
    legend.position = "right",
    legend.background = element_rect(color = "#969696", fill = "#d9d9d9")
  )

# ========== 计算亚家族竖线位置 ==========
tree_dat <- p_tree$data
tip_dat <- tree_dat %>%
  dplyr::filter(isTip) %>%
  dplyr::select(label, x, y, subfamily, species, group)

# Shared layout metrics are used by both the subfamily-panel and simplified branches.
x_tree_min <- min(tree_dat$x, na.rm = TRUE)
x_tip_max <- max(tip_dat$x, na.rm = TRUE)
max_y <- max(tree_dat$y, na.rm = TRUE)
tree_core_width <- x_tip_max - x_tree_min
if (!is.finite(tree_core_width) || tree_core_width <= 0) {
  tree_core_width <- 1
}

matched_subfamily_tips <- sum(!is.na(tip_dat$subfamily) & tip_dat$subfamily != "")
has_subfamily_panel <- nrow(tip_dat) > 0 && matched_subfamily_tips > 0
cat("Subfamily tips matched:", matched_subfamily_tips, "/", nrow(tip_dat), "\n")

if (has_subfamily_panel) {
  # 计算亚家族范围
  subfam_ranges <- make_subfamily_ranges(tip_dat)
  
  # 计算坐标范围
  x_tree_min <- min(tree_dat$x, na.rm = TRUE)
  x_tip_max <- max(tip_dat$x, na.rm = TRUE)
  max_y <- max(tree_dat$y, na.rm = TRUE)
  
  tree_core_width <- x_tip_max - x_tree_min
  
  # Panel A：树 + 基因标签。使用原来的紧凑比例，Subfamily 只在右侧分组面板标注。
  x_label_left <- x_tip_max + label_offset
  subfam_levels_for_layout <- unique(na.omit(tip_dat$subfamily))
  max_subfam_len <- ifelse(length(subfam_levels_for_layout) > 0, max(nchar(subfam_levels_for_layout)), 0)
  n_subfam_layout <- length(subfam_levels_for_layout)
  n_species_layout <- length(unique(na.omit(tip_dat$species)))

  label_text_width <- max_id_len * tip_label_size * 0.09
  label_panel_width <- max(2.2, tree_core_width * 0.40, label_text_width)
  x_A_left <- x_tree_min
  x_A_right <- x_label_left + label_panel_width
  gap1 <- max(0.55, tree_core_width * 0.04)
  subfamily_text_width <- max(1.8, max_subfam_len * 0.32)
  width_B <- max(2.2, tree_core_width * 0.28, subfamily_text_width)
  x_B_left <- x_A_right + gap1
  x_B_right <- x_B_left + width_B
  x_subfam <- x_B_left + width_B * 0.20
  x_subfam_label <- x_subfam + width_B * 0.05
  x_right_pad <- max(1.2, tree_core_width * 0.10, subfamily_text_width * 0.45)

  tree_inches <- max(4.5, tree_core_width * 0.75)
  label_inches <- max(3.0, max_id_len * tip_label_size * 0.11)
  subfamily_inches <- max(2.0, max_subfam_len * 0.30 + n_subfam_layout * 0.12)
  legend_inches <- max(1.8, n_species_layout * 0.35 + n_subfam_layout * 0.18)
  rect_plot_width <- tree_inches + label_inches + subfamily_inches + legend_inches + 2.0
  rect_plot_height <- max(8, 3.5 + n_tips * max(0.22, tip_label_size * 0.08))
  
  # 添加亚家族竖线和标签
  # 注意：p_tree 已经定义了 scale_color_manual 用于亚家族，这里不要再重新定义
  p_final <- p_tree +
    geom_segment(
      data = subfam_ranges,
      inherit.aes = FALSE,
      aes(x = x_subfam, xend = x_subfam, y = ymin, yend = ymax, colour = subfamily),
      linewidth = 1,
      show.legend = FALSE
    ) +
    geom_text(
      data = subfam_ranges,
      inherit.aes = FALSE,
      aes(x = x_subfam_label, y = ymid, label = subfamily, colour = subfamily),
      hjust = 0,
      size = 3,
      show.legend = FALSE
    ) +
    annotate("text", x = (x_A_left + x_A_right) / 2, y = max_y + 1.2, 
             label = "Phylogenetic tree", fontface = "bold", size = 4) +
    annotate("text", x = (x_B_left + x_B_right) / 2, y = max_y + 1.2, 
             label = "Subfamily", fontface = "bold", size = 4) +
    coord_cartesian(xlim = c(x_A_left, x_B_right + x_right_pad), clip = "off") +
    expand_limits(y = max_y + 1.5) +
    ggtitle(paste0("Interspecies Phylogeny: ", target_species, " vs ", ref_species)) +
    theme(
      plot.title = element_text(hjust = 0.5, size = 14, face = "bold"),
      plot.margin = margin(8, 36, 8, 8)
    )
} else {
  x_label_left <- x_tip_max + label_offset
  label_text_width <- max_id_len * tip_label_size * 0.12
  label_panel_width <- max(3.0, tree_core_width * 0.35, label_text_width)
  x_A_right <- x_label_left + label_panel_width
  x_right_pad <- max(1.5, tree_core_width * 0.12)
  tree_inches <- max(4.5, tree_core_width * 0.65)
  label_inches <- max(4.0, max_id_len * tip_label_size * 0.14)
  legend_inches <- max(1.8, length(species_levels) * 0.35)
  rect_plot_width <- tree_inches + label_inches + legend_inches + 2.5
  rect_plot_height <- max(8, 3.5 + n_tips * max(0.22, tip_label_size * 0.08))
  p_final <- p_tree +
    coord_cartesian(xlim = c(x_tree_min, x_A_right + x_right_pad), clip = "off") +
    expand_limits(y = max_y + 1.5) +
    ggtitle(paste0("Interspecies Phylogeny: ", target_species, " vs ", ref_species)) +
    theme(
      plot.title = element_text(hjust = 0.5, size = 14, face = "bold"),
      plot.margin = margin(8, 36, 8, 8)
    )
}

# ========== 输出 ==========
output_base <- file.path(output_dir, "interspecies_tree")

# 保存PDF
tryCatch({
  ggsave(paste0(output_base, ".pdf"), p_final, width = rect_plot_width, height = rect_plot_height, limitsize = FALSE)
  cat("PDF已保存:", paste0(output_base, ".pdf"), "\n")
}, error = function(e) {
  cat("PDF保存失败:", conditionMessage(e), "\n")
})

# 保存PNG
tryCatch({
  png(filename = paste0(output_base, ".png"), width = rect_plot_width, height = rect_plot_height, units = "in", res = 150, bg = "white")
  print(p_final)
  dev.off()
  cat("PNG已保存:", paste0(output_base, ".png"), "\n")
}, error = function(e) {
  cat("PNG保存失败:", conditionMessage(e), "\n")
})

## 同时输出一张圈图。圈图不使用右侧亚家族竖线面板，避免环形布局坐标与矩形 panel 混用；
## 保留 tip 的亚家族颜色、目标/参考物种形状和 bootstrap 节点。
tryCatch({
  circular_label_size <- ifelse(n_tips > 300, 1.4, ifelse(n_tips > 200, 1.8, 2.2))
  circular_label_offset <- ifelse(n_tips > 200, 1.4, 1.0)
  circular_canvas <- ifelse(n_tips > 300, 18, ifelse(n_tips > 200, 16, 13))

  p_circular <- ggtree(tree, layout = "circular", branch.length = "none", color = branch_color, size = max(0.18, branch_width * 0.36)) %<+% tree_df
  circular_tip_dat <- p_circular$data %>%
    dplyr::filter(isTip) %>%
    dplyr::select(label, x, y, subfamily)
  circular_tip_dat$display_label <- circular_tip_dat$label
  max_display_label_len <- max(nchar(as.character(circular_tip_dat$display_label)), na.rm = TRUE)
  circular_gene_label_width <- max(2.8, max_display_label_len * circular_label_size * 0.16)
  circular_arc_gap <- max(0, subfamily_arc_gap)
  circular_arc_label_gap <- max(0.2, subfamily_arc_label_gap)
  circular_arc_radius <- max(circular_tip_dat$x, na.rm = TRUE) + circular_label_offset + circular_gene_label_width + circular_arc_gap
  circular_arc_label_radius <- circular_arc_radius + circular_arc_label_gap
  circular_arc_label_size_effective <- ifelse(is.finite(subfamily_arc_label_size), subfamily_arc_label_size, max(1.6, circular_label_size * 1.25))
  circular_arc <- make_circular_subfamily_arc_paths(circular_tip_dat, n_tips, circular_arc_radius, label_radius = circular_arc_label_radius)
  has_circular_arc <- isTRUE(subfamily_arc_enabled) && nrow(circular_arc$paths) > 0
  cat("Circular subfamily arc enabled:", has_circular_arc, "segments:", length(unique(circular_arc$paths$subfamily_run_id)), "\n")
  cat("Circular outer arc radius:", round(circular_arc_radius, 2), "label radius:", round(circular_arc_label_radius, 2), "gene label width estimate:", round(circular_gene_label_width, 2), "\n")

  p_circular <- p_circular +
    geom_nodepoint(
      aes(fill = cut(support, c(0, 50, 75, 100))),
      shape = 21, size = max(1.0, bootstrap_size * 0.6), na.rm = TRUE
    ) +
    geom_text2(
      aes(label = ifelse(!isTip & !is.na(support), round(support), "")),
      hjust = -0.15, vjust = -0.35, size = 1.8,
      na.rm = TRUE, show.legend = FALSE
    ) +
    scale_fill_manual(
      values = c("black", "grey40", "grey80"),
      guide = "legend",
      name = "Bootstrap Percentage (BP)",
      breaks = c("(75,100]", "(50,75]", "(0,50]"),
      labels = expression(BP > 75, 50 < BP * "<=75", BP <= 50)
    ) +
    geom_tippoint(
      aes(color = subfamily, shape = group),
      size = max(1.2, tip_point_size * 0.5), alpha = 0.85, na.rm = TRUE
    ) +
    scale_color_manual(values = subfam_cols, name = "Subfamily") +
    scale_shape_manual(
      values = shape_values,
      breaks = names(shape_labels),
      labels = shape_labels,
      name = "Species"
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
    geom_tiplab2(size = circular_label_size, offset = circular_label_offset, align = TRUE, linesize = 0.1) +
    ggtitle(paste0("Interspecies Phylogeny: ", target_species, " vs ", ref_species)) +
    theme_tree() +
    theme(
      plot.title = element_text(hjust = 0.5, size = 14, face = "bold"),
      legend.position = "right",
      legend.background = element_rect(color = "#969696", fill = "white"),
      plot.margin = margin(10, 44, 10, 10)
    ) +
    xlim(0, circular_xlim_right)

  circular_pdf <- paste0(output_base, ".circular_tree.pdf")
  circular_png <- paste0(output_base, ".circular_tree.png")
  ggsave(circular_pdf, p_circular, width = circular_canvas, height = circular_canvas, limitsize = FALSE)
  png(filename = circular_png, width = circular_canvas, height = circular_canvas, units = "in", res = 220, bg = "white")
  print(p_circular)
  dev.off()
  cat("圈图PDF已保存:", circular_pdf, "\n")
  cat("圈图PNG已保存:", circular_png, "\n")
}, error = function(e) {
  cat("圈图保存失败:", conditionMessage(e), "\n")
})

cat("完成\n")
