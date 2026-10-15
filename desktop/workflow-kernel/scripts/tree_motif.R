#!/usr/bin/env Rscript

## =========================================================
## plot_motif.R
## 左：系统发育树（含基因 ID）
## 中：亚家族竖线
## 右：MEME motif 矩形条
## =========================================================

args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 3) {
  stop(
    "用法:\n",
    "  Rscript plot_motif.R <treefile> <subfamily_file> <meme_xml> [output_prefix]\n\n",
    "说明:\n",
    "  treefile       : IQ-TREE Newick 树文件\n",
    "  subfamily_file : 亚家族文件路径（可为空字符串）\n",
    "  meme_xml       : MEME XML 结果文件\n",
    "  output_prefix  : 输出文件前缀（可选，默认与 treefile 同名）\n",
    call. = FALSE
  )
}

treefile       <- args[1]
subfamily_file <- args[2]
meme_xml       <- args[3]

# 第四个参数：输出前缀
if (length(args) >= 4 && nzchar(args[4])) {
  output_prefix <- args[4]
} else {
  output_prefix <- sub("\\.[^.]*$", "", treefile)
}

cat("树文件:         ", treefile,       "\n")
cat("亚家族文件:     ", ifelse(nzchar(subfamily_file), subfamily_file, "(未提供)"), "\n")
cat("MEME XML 文件:  ", meme_xml,       "\n")
cat("输出前缀:       ", output_prefix,  "\n\n")

## ===================== 2. 加载 R 包 =====================


script_args_all <- commandArgs(trailingOnly = FALSE)
script_file_arg <- grep("^--file=", script_args_all, value = TRUE)
script_dir <- if (length(script_file_arg) > 0) dirname(normalizePath(sub("^--file=", "", script_file_arg[1]))) else getwd()
source(file.path(script_dir, "subfamily_palette.R"))

suppressPackageStartupMessages({
  library(ape)
  library(ggtree)
  library(tidytree)
  library(dplyr)
  library(RColorBrewer)
  library(ggplot2)
  library(xml2)
  library(ggnewscale)
})

make_distinct_cols <- function(n) {
  if (n <= 0) return(character(0))
  base <- c(
    "#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00",
    "#56B4E9", "#F0E442", "#000000", "#882255", "#44AA99",
    "#AA4499", "#117733", "#332288", "#DDCC77", "#88CCEE"
  )
  if (n <= length(base)) return(base[seq_len(n)])
  if (exists("hcl.colors", where = asNamespace("grDevices"), inherits = FALSE)) {
    return(grDevices::hcl.colors(n, palette = "Dark 3"))
  }
  grDevices::colorRampPalette(base)(n)
}

## ===================== 3. 读亚家族文件 + 深色配色 =====================

add_confidence_labels <- function(subfam_df, subfamily_file) {
  add_subfamily_confidence_labels(subfam_df, subfamily_file)
}


# 检查亚家族文件是否存在
has_subfamily <- nzchar(subfamily_file) && file.exists(subfamily_file)

if (has_subfamily) {
  subfam <- read_subfamily_plot_table(subfamily_file)
  subfam <- add_confidence_labels(subfam, subfamily_file)
  
  subfam_levels <- sort(unique(subfam$subfamily))
  n_subfam      <- length(subfam_levels)
  
  if (n_subfam == 0) {
    warning("在亚家族文件中没有检测到任何 subfamily，将使用默认着色")
    has_subfamily <- FALSE
  } else {
    subfam_cols <- make_subfamily_cols(subfam_levels)
    cat("检测到", n_subfam, "个亚家族\n")
  }
} else {
  cat("未提供亚家族文件，将使用默认着色\n")
  # 创建空的 subfam 数据框
  subfam <- data.frame(label = character(), subfamily = character(), low_confidence = logical(), display_label = character(), stringsAsFactors = FALSE)
  subfam_levels <- character()
  subfam_cols <- character()
}

## ===================== 4. 树绘图函数 =====================

plot_iqtree_by_subfamily <- function(treefile, subfam_df, subfam_cols, label_offset = 0.4) {
  tree <- read.tree(treefile)
  tree_df <- as_tibble(tree)
  tree_df$isTip <- tree_df$node <= length(tree$tip.label)
  
  if (nrow(subfam_df) > 0) {
    tree_df <- dplyr::left_join(tree_df, subfam_df, by = "label")
  } else {
    tree_df$subfamily <- NA_character_
    tree_df$display_label <- tree_df$label
  }
  tree_df$display_label <- ifelse(is.na(tree_df$display_label), tree_df$label, tree_df$display_label)

  tree_df$support <- ifelse(
    tree_df$isTip,
    NA_real_,
    suppressWarnings(as.numeric(tree_df$label))
  )

  p <- ggtree(tree, branch.length = "none") %<+% tree_df +
    ## bootstrap
    geom_nodepoint(
      aes(fill = cut(support, c(0, 50, 75, 100))),
      shape = 21,
      size  = 2,
      na.rm = TRUE
    ) +
    scale_fill_manual(
      values = c("black", "grey40", "grey80"),
      guide  = "legend",
      name   = "Bootstrap Percentage (BP)",
      breaks = c("(75,100]", "(50,75]", "(0,50]"),
      labels = expression(BP > 75,
                          50 < BP * "<=75",
                          BP <= 50)
    ) +
    geom_text2(
      aes(label = ifelse(!isTip & !is.na(support), round(support), "")),
      hjust = -0.2, vjust = -0.4, size = 2.2,
      na.rm = TRUE, show.legend = FALSE
    )
  
  # 只有提供亚家族时才着色
  if (nrow(subfam_df) > 0) {
    p <- p + geom_tippoint(
        aes(color = subfamily),
        size  = 2,
        na.rm = TRUE
      ) +
      scale_color_manual(
        values = subfam_cols,
        name   = "Subfamily",
        na.value = "grey70"
      )
  } else {
    p <- p + geom_tippoint(size = 2, color = "grey50")
  }
  
  p <- p + geom_tiplab(aes(label = display_label), offset = label_offset, size = 3, hjust = 0) +
    ggtitle(NULL) +
    theme_tree() +
    theme(
      legend.position   = "right",
      legend.background = element_rect(color = "#969696", fill = "#d9d9d9")
    )

  attr(p, "label_offset") <- label_offset
  return(p)
}

## ===================== 5. 解析 MEME XML =====================

parse_meme_xml <- function(meme_file) {

  doc <- xml2::read_xml(meme_file)

  ## 所有序列的 id/name/length
  seq_nodes <- xml2::xml_find_all(doc, ".//sequence")
  seq_ids   <- xml2::xml_attr(seq_nodes, "id")
  seq_names <- xml2::xml_attr(seq_nodes, "name")

  ## 兼容可能出现的 Length / length 大小写问题
  seq_lengths <- xml2::xml_attr(seq_nodes, "length")
  if (any(is.na(seq_lengths))) {
    seq_lengths2 <- xml2::xml_attr(seq_nodes, "Length")
    idx <- which(is.na(seq_lengths) & !is.na(seq_lengths2))
    seq_lengths[idx] <- seq_lengths2[idx]
  }
  seq_lengths <- suppressWarnings(as.numeric(seq_lengths))

  name_map   <- setNames(seq_names,   seq_ids)   # id -> gene
  length_map <- setNames(seq_lengths, seq_names) # gene -> seqlen

  ## motif + site 信息
  motif_nodes <- xml2::xml_find_all(doc, ".//motif")
  site_list   <- list()
  motif_index <- 1L

  for (m in motif_nodes) {
    motif_id <- xml2::xml_attr(m, "id")
    if (is.na(motif_id) || motif_id == "") {
      motif_id <- xml2::xml_attr(m, "name")
    }
    if (is.na(motif_id) || motif_id == "") {
      motif_id <- paste0("Motif_", motif_index)
    }

    motif_width <- xml2::xml_attr(m, "width")
    motif_width <- suppressWarnings(as.numeric(motif_width))
    if (is.na(motif_width)) motif_width <- 10

    sites <- xml2::xml_find_all(m, ".//contributing_site")
    if (length(sites) == 0) next

    seq_id <- xml2::xml_attr(sites, "sequence_id")
    start  <- xml2::xml_attr(sites, "position")
    start  <- suppressWarnings(as.numeric(start))

    width  <- rep(motif_width, length(sites))

    df <- data.frame(
      seq_id = seq_id,
      start  = start,
      width  = width,
      motif  = motif_id,
      stringsAsFactors = FALSE
    )

    site_list[[length(site_list) + 1L]] <- df
    motif_index <- motif_index + 1L
  }

  if (length(site_list) == 0) {
    stop("在 MEME XML 中没有找到任何 motif site，请检查文件或 XML 结构。", call. = FALSE)
  }

  sites <- dplyr::bind_rows(site_list)

  ## 序列名 & 序列长度来自 XML 中的 sequence 区
  sites$gene   <- unname(name_map[sites$seq_id])
  sites$seqlen <- length_map[sites$gene]

  ## motif 终点坐标（同样用 XML 的真实坐标）
  sites$end    <- sites$start + sites$width - 1

  sites
}

## ===================== 6. 画树 & 基本坐标信息 =====================

label_offset <- 0.4
p_tree <- plot_iqtree_by_subfamily(treefile, subfam, subfam_cols, label_offset = label_offset)

tree_dat <- p_tree$data
tip_dat  <- tree_dat %>%
  dplyr::filter(isTip) %>%
  dplyr::select(label, x, y, subfamily)

if (nrow(tip_dat) == 0) {
  stop("树中没有 tip 节点，请检查 treefile。", call. = FALSE)
}

x_tree_min <- min(tree_dat$x, na.rm = TRUE)
x_tip_max  <- max(tip_dat$x,   na.rm = TRUE)
max_y      <- max(tree_dat$y,  na.rm = TRUE)

## 树的"主干宽度"（不算标签）
tree_core_width <- x_tip_max - x_tree_min
if (!is.finite(tree_core_width) || tree_core_width <= 0) {
  stop("树的宽度计算异常，请检查 treefile。", call. = FALSE)
}

## gene ID 左边界（文本起点）
x_label_left <- x_tip_max + label_offset

## 给 gene ID 单独留一块"标签区域"，宽度与树宽成比例，且不少于 1.5
label_panel_width <- max(1.5, tree_core_width * 0.5)

## Panel A（树 + gene ID）的右边界
x_A_right <- x_label_left + label_panel_width
x_A_left  <- x_tree_min

## ===================== 7. 解析 MEME XML，并与树对齐（Y 对齐） =====================

motif_sites <- parse_meme_xml(meme_xml)

motif_sites <- motif_sites %>%
  dplyr::filter(!is.na(gene)) %>%
  dplyr::filter(gene %in% tip_dat$label)

if (nrow(motif_sites) == 0) {
  stop("在 MEME 结果中没有找到与树 tip.label 匹配的基因。", call. = FALSE)
}

## 合并 tip 的 y / subfamily
motif_sites <- motif_sites %>%
  dplyr::left_join(tip_dat, by = c("gene" = "label"))

## 只保留 seqlen 不缺失的条目（完全依赖 XML 的 length）
motif_sites <- motif_sites %>%
  dplyr::filter(!is.na(seqlen))

## 检查长度范围
cat("motif 对应序列长度范围: ",
    min(motif_sites$seqlen, na.rm = TRUE), " ~ ",
    max(motif_sites$seqlen, na.rm = TRUE), "\n")

## ===================== 8. motif 高对比配色（不循环复用） =====================

motif_levels <- sort(unique(motif_sites$motif))
n_motif      <- length(motif_levels)

motif_cols_vec <- make_distinct_cols(n_motif)
motif_cols   <- setNames(motif_cols_vec, motif_levels)

motif_sites$motif <- factor(motif_sites$motif, levels = motif_levels)

## ===================== 9. 三个 Panel 的 X 布局 =====================

## 间隔和比例（可调）：
gap1 <- max(0.3, tree_core_width * 0.1)   # Panel A 和 B 之间空隙
gap2 <- max(0.3, tree_core_width * 0.1)   # Panel B 和 C 之间空隙
r_B  <- 0.4                               # Panel B 宽度 = 树宽 * 0.4
r_C  <- 1.6                               # Panel C 宽度 = 树宽 * 1.6

## Panel B（亚家族竖线区域）
x_B_left   <- x_A_right + gap1
width_B    <- tree_core_width * r_B
x_B_right  <- x_B_left + width_B
x_subfam   <- (x_B_left + x_B_right) / 2

## Panel C（motif 区域）
x_C_left   <- x_B_right + gap2
width_C    <- tree_core_width * r_C
x_C_right  <- x_C_left + width_C

## 用"XML 中最长的序列长度"作为统一尺子
max_len <- max(motif_sites$seqlen, na.rm = TRUE)
scale_factor <- width_C / max_len   # 每 1 个 aa/nt 对应多少 x 轴单位

cat("最长序列长度: ", max_len,
    "；motif 面板宽度: ", width_C,
    "；scale_factor: ", scale_factor, "\n")

## ===================== 10. 基因主干线 + motif 矩形坐标 =====================

## 基因主干线：从 0 到 seqlen（在 Panel C 中）
gene_tracks <- motif_sites %>%
  dplyr::distinct(gene, y, seqlen) %>%
  dplyr::mutate(
    x_gene_start = x_C_left,
    x_gene_end   = x_C_left + seqlen * scale_factor
  )

## motif 矩形：start / end 完全用 XML 的坐标，乘以全局比例尺
motif_rect <- motif_sites %>%
  dplyr::mutate(
    xmin = x_C_left + (start - 1) * scale_factor,
    xmax = x_C_left + end        * scale_factor,
    ymin = y - 0.25,
    ymax = y + 0.25
  )

## ===================== 11. 亚家族竖线的 Y 范围（完全跟树对齐） =====================

if (has_subfamily) {
  subfam_ranges <- make_subfamily_ranges(tip_dat)
} else {
  # 没有亚家族时创建空数据框
  subfam_ranges <- data.frame(
    subfamily = character(),
    ymin = numeric(),
    ymax = numeric(),
    ymid = numeric(),
    stringsAsFactors = FALSE
  )
}

## ===================== 12. 顶部两个表头的位置 =====================

tree_header_x  <- (x_A_left  + x_A_right)  / 2
motif_header_x <- (x_C_left  + x_C_right)  / 2
header_y       <- max_y + 1.2

## ===================== 13. 拼合：树 + 基因主干线 + motif + 竖线 + 表头 =====================

p_combo <- p_tree +
  ## 先画基因主干线（浅灰）
  geom_segment(
    data        = gene_tracks,
    inherit.aes = FALSE,
    aes(
      x    = x_gene_start,
      xend = x_gene_end,
      y    = y,
      yend = y
    ),
    colour   = "grey70",
    linewidth = 0.4
  ) +
  ## motif 用新的 fill scale（不影响 bootstrap 的 fill）
  ggnewscale::new_scale_fill() +
  geom_rect(
    data        = motif_rect,
    inherit.aes = FALSE,
    aes(
      xmin = xmin,
      xmax = xmax,
      ymin = ymin,
      ymax = ymax,
      fill = motif
    ),
    color = NA
  ) +
  scale_fill_manual(values = motif_cols, name = "Motif")

# 只有有亚家族时才添加亚家族竖线
if (nrow(subfam_ranges) > 0) {
  p_combo <- p_combo +
    geom_segment(
      data        = subfam_ranges,
      inherit.aes = FALSE,
      aes(
        x     = x_subfam,
        xend  = x_subfam,
        y     = ymin,
        yend  = ymax,
        colour = subfamily
      ),
      linewidth = 1
    ) +
    geom_text(
      data        = subfam_ranges,
      inherit.aes = FALSE,
      aes(
        x     = x_subfam + width_B * 0.05,
        y     = ymid,
        label = subfamily,
        colour = subfamily
      ),
      hjust = 0,
      size  = 3
    ) +
    scale_color_manual(values = subfam_cols, name = "Subfamily")
}

p_combo <- p_combo +
  ## 顶部两个表头（各在自己 panel 正上方）
  annotate(
    "text",
    x        = tree_header_x,
    y        = header_y,
    label    = "Phylogenetic tree",
    fontface = "bold",
    size     = 4
  ) +
  annotate(
    "text",
    x        = motif_header_x,
    y        = header_y,
    label    = "Motif",
    fontface = "bold",
    size     = 4
  ) +
  ## 画板 x 轴范围 = 从树最左到 motif panel 右边，再留一点边距
  coord_cartesian(
    xlim = c(x_A_left, x_C_right + tree_core_width * 0.1)
  ) +
  ## 给表头留出一点空间
  expand_limits(y = header_y + 0.5) +
  theme(
    legend.position   = "right",
    legend.background = element_rect(color = "#969696", fill = "#d9d9d9")
  )

## 输出 PDF 和 PNG
output_pdf <- paste0(output_prefix, ".tree_motif.pdf")
output_png <- paste0(output_prefix, ".tree_motif.png")

# 保存PDF
ggsave(output_pdf, p_combo, width = 14, height = 10)

# 保存PNG（使用png()函数避免conda R的API冲突）
png(filename = output_png, width = 4200, height = 3000, res = 300)
print(p_combo)
dev.off()

cat("[OK] PDF: ", output_pdf, "\n", sep = "")
cat("[OK] PNG: ", output_png, "\n", sep = "")
cat("已输出树 + motif 组合图\n")
