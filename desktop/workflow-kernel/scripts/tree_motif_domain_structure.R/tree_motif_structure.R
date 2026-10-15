#!/usr/bin/env Rscript

## =========================================================
## combo_motif_structure.R
## 左：系统发育树（含基因 ID）
## 中：亚家族竖线（不压 gene ID）
## 右一：MEME motif（按 XML 实际长度比例）
## 右二：GFF3 基因结构（按 GFF3 实际长度比例；只画 CDS + UTR）
## 顶部：三个表头：Phylogenetic tree / Motif / Gene structure
##
## 用法:
##   Rscript combo_motif_structure.R \
##     <treefile> <subfamily_file> <meme_xml> <gff3_file> [output_pdf]
## =========================================================

args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 4) {
  stop(
    "用法:\n",
    "  Rscript combo_motif_structure.R ",
    "<treefile> <subfamily_file> <meme_xml> <gff3_file> [output_pdf]\n\n",
    call. = FALSE
  )
}

treefile       <- args[1]
subfamily_file <- args[2]
meme_xml       <- args[3]
gff3_file      <- args[4]

## 第五个参数：输出 pdf 名称
if (length(args) >= 5 && nzchar(args[5])) {
  output_pdf <- args[5]
} else {
  prefix <- sub("\\.[^.]*$", "", treefile)
  output_pdf <- paste0(prefix, ".tree_motif_structure.pdf")
}

cat("树文件:         ", treefile,       "\n")
cat("亚家族文件:     ", subfamily_file, "\n")
cat("MEME XML 文件:  ", meme_xml,       "\n")
cat("GFF3 文件:      ", gff3_file,      "\n")
cat("输出文件:       ", output_pdf,     "\n\n")

## ===================== 2. 加载 R 包 =====================

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

## ===================== 3. 读亚家族文件 + 深色配色 =====================

subfam <- read.table(subfamily_file,
                     header = FALSE,
                     stringsAsFactors = FALSE)
colnames(subfam) <- c("label", "subfamily")

subfam_levels <- sort(unique(subfam$subfamily))
n_subfam      <- length(subfam_levels)
if (n_subfam == 0) {
  stop("在亚家族文件中没有检测到任何 subfamily，请检查文件格式。", call. = FALSE)
}

## 深色调：Dark2，若类别多就循环使用
base_cols_sf <- RColorBrewer::brewer.pal(8, "Dark2")
subfam_cols_vec <- rep_len(base_cols_sf, n_subfam)
subfam_cols  <- setNames(subfam_cols_vec, subfam_levels)

## ===================== 4. 树绘图函数（只画树 + tip/subfamily，不画竖线） =====================

plot_iqtree_by_subfamily <- function(treefile, subfam_df, subfam_cols, label_offset = 0.4) {
  tree <- read.tree(treefile)
  tree_df <- as_tibble(tree)
  tree_df$isTip <- tree_df$node <= length(tree$tip.label)

  tree_df <- dplyr::left_join(tree_df, subfam_df, by = "label")

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
    ## tip 颜色 = subfamily
    geom_tippoint(
      aes(color = subfamily),
      size  = 2,
      na.rm = TRUE
    ) +
    scale_color_manual(
      values = subfam_cols,
      name   = "Subfamily"
    ) +
    ## 基因 ID 标签，靠树一点
    geom_tiplab(offset = label_offset, size = 3, hjust = 0) +
    ggtitle(NULL) +
    theme_tree() +
    theme(
      legend.background = element_rect(color = "#969696",
                                       fill  = "#d9d9d9")
    )

  attr(p, "label_offset") <- label_offset
  return(p)
}

## ===================== 5. 解析 MEME XML，得到 motif 位点 + 序列长度 =====================

parse_meme_xml <- function(meme_file) {

  doc <- xml2::read_xml(meme_file)

  ## 所有序列的 id/name/length
  seq_nodes <- xml2::xml_find_all(doc, ".//sequence")
  seq_ids   <- xml2::xml_attr(seq_nodes, "id")
  seq_names <- xml2::xml_attr(seq_nodes, "name")

  ## 兼容 length / Length 大小写
  seq_lengths <- xml2::xml_attr(seq_nodes, "length")
  if (any(is.na(seq_lengths))) {
    seq_lengths2 <- xml2::xml_attr(seq_nodes, "Length")
    idx <- which(is.na(seq_lengths) & !is.na(seq_lengths2))
    seq_lengths[idx] <- seq_lengths2[idx]
  }
  seq_lengths <- suppressWarnings(as.numeric(seq_lengths))

  name_map   <- setNames(seq_names,   seq_ids)   # id -> gene
  length_map <- setNames(seq_lengths, seq_names) # gene -> seqlen

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

  ## motif 终点坐标
  sites$end    <- sites$start + sites$width - 1

  sites
}

## ===================== 6. 解析 GFF3，提取基因结构 =====================

parse_gff3_gene_structure <- function(gff3_file, tree_genes) {

  gff <- read.table(gff3_file,
                    sep = "\t",
                    comment.char = "#",
                    quote = "",
                    stringsAsFactors = FALSE,
                    fill = TRUE)

  if (ncol(gff) < 9) {
    stop("GFF3 文件列数不足 9 列，请检查格式。", call. = FALSE)
  }

  colnames(gff)[1:9] <- c("seqid", "source", "type", "start",
                          "end", "score", "strand", "phase",
                          "attributes")

  gff$start <- as.numeric(gff$start)
  gff$end   <- as.numeric(gff$end)

  ## 只保留关心的 feature 类型
  feat_types <- c("exon", "CDS", "UTR",
                  "five_prime_UTR", "three_prime_UTR",
                  "5UTR", "3UTR")
  feat_df <- gff[gff$type %in% feat_types, ]

  if (nrow(feat_df) == 0) {
    stop("在 GFF3 中没有找到 exon/CDS/UTR 等特征。", call. = FALSE)
  }

  ## 解析 attributes 里的 Parent=...
  get_attr <- function(attr_vec, key) {
    sapply(strsplit(attr_vec, ";"), function(fields) {
      kv <- strsplit(fields, "=")
      res <- NA_character_
      for (p in kv) {
        if (length(p) == 2 && p[1] == key) {
          res <- p[2]
          break
        }
      }
      res
    })
  }

  feat_df$Parent <- get_attr(feat_df$attributes, "Parent")

  ## 只保留 Parent 出现在树 tip.label 的转录本
  feat_df <- feat_df[!is.na(feat_df$Parent) &
                       feat_df$Parent %in% tree_genes, ]

  if (nrow(feat_df) == 0) {
    stop("GFF3 中的 Parent 与树 tip.label 无交集，请检查 ID 是否一致。", call. = FALSE)
  }

  feat_df$gene <- feat_df$Parent

  ## 每个 gene 的基因区域：真实 genomic 坐标范围
  gene_ranges <- feat_df %>%
    dplyr::group_by(gene) %>%
    dplyr::summarise(
      gene_start = min(start, end, na.rm = TRUE),
      gene_end   = max(start, end, na.rm = TRUE),
      gene_len   = gene_end - gene_start + 1,
      .groups    = "drop"
    )

  ## 特征相对该基因起点的坐标
  feat_df <- feat_df %>%
    dplyr::left_join(gene_ranges, by = "gene") %>%
    dplyr::mutate(
      rel_start = pmin(start, end) - gene_start,
      rel_end   = pmax(start, end) - gene_start
    ) %>%
    dplyr::mutate(
      Type = dplyr::case_when(
        type %in% c("five_prime_UTR", "three_prime_UTR",
                    "5UTR", "3UTR", "UTR") ~ "UTR",
        TRUE ~ type
      )
    )

  list(features = feat_df, ranges = gene_ranges)
}

## ===================== 7. 画树 & 基本坐标信息 =====================

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

## 树的“主干宽度”（不算标签）
tree_core_width <- x_tip_max - x_tree_min
if (!is.finite(tree_core_width) || tree_core_width <= 0) {
  stop("树的宽度计算异常，请检查 treefile。", call. = FALSE)
}

## gene ID 左边界（文本起点）
x_label_left <- x_tip_max + label_offset

## Panel A（树 + gene ID）
label_panel_width <- max(1.5, tree_core_width * 0.5)
x_A_left  <- x_tree_min
x_A_right <- x_label_left + label_panel_width

## ===================== 8. 解析 MEME XML，并与树对齐（Y 对齐） =====================

motif_sites <- parse_meme_xml(meme_xml)

motif_sites <- motif_sites %>%
  dplyr::filter(!is.na(gene)) %>%
  dplyr::filter(gene %in% tip_dat$label)

if (nrow(motif_sites) == 0) {
  stop("在 MEME 结果中没有找到与树 tip.label 匹配的基因。", call. = FALSE)
}

motif_sites <- motif_sites %>%
  dplyr::left_join(tip_dat, by = c("gene" = "label")) %>%
  dplyr::filter(!is.na(seqlen))

cat("motif 序列长度范围 (XML): ",
    min(motif_sites$seqlen, na.rm = TRUE), " ~ ",
    max(motif_sites$seqlen, na.rm = TRUE), "\n")

## motif 配色
motif_levels <- sort(unique(motif_sites$motif))
n_motif      <- length(motif_levels)
base_cols_m  <- RColorBrewer::brewer.pal(8, "Dark2")
motif_cols_vec <- rep_len(base_cols_m, n_motif)
motif_cols   <- setNames(motif_cols_vec, motif_levels)
motif_sites$motif <- factor(motif_sites$motif, levels = motif_levels)

## ===================== 9. 解析 GFF3（gene structure），并与树对齐 =====================

gs <- parse_gff3_gene_structure(gff3_file, tip_dat$label)
gene_feat   <- gs$features
gene_ranges <- gs$ranges

gene_ranges <- gene_ranges %>%
  dplyr::left_join(tip_dat, by = c("gene" = "label"))
gene_feat   <- gene_feat %>%
  dplyr::left_join(tip_dat, by = c("gene" = "label"))

gene_ranges <- gene_ranges %>% dplyr::filter(!is.na(y))
gene_feat   <- gene_feat   %>% dplyr::filter(!is.na(y))

if (nrow(gene_feat) == 0) {
  stop("GFF3 中匹配到树 tip.label 的基因结构为空。", call. = FALSE)
}

cat("基因长度范围 (GFF3): ",
    min(gene_ranges$gene_len, na.rm = TRUE), " ~ ",
    max(gene_ranges$gene_len, na.rm = TRUE), "\n")

## ===================== 10. 四个 Panel 的 X 布局 =====================

## 间隔和比例（可以微调）：
gap1 <- max(0.3, tree_core_width * 0.10)   # A-B 间隔
gap2 <- max(0.3, tree_core_width * 0.10)   # B-C 间隔
gap3 <- max(0.3, tree_core_width * 0.10)   # C-D 间隔

r_B  <- 0.4                                # Panel B 宽度 = 树宽 * 0.4
r_C  <- 1.6                                # Panel C 宽度 = 树宽 * 1.6
r_D  <- 2.2                                # Panel D 宽度 = 树宽 * 2.2（结构面板更大）

## Panel B（亚家族竖线）
x_B_left   <- x_A_right + gap1
width_B    <- tree_core_width * r_B
x_B_right  <- x_B_left + width_B
x_subfam   <- (x_B_left + x_B_right) / 2

## Panel C（motif）
x_C_left   <- x_B_right + gap2
width_C    <- tree_core_width * r_C
x_C_right  <- x_C_left + width_C

## Panel D（gene structure）
x_D_left   <- x_C_right + gap3
width_D    <- tree_core_width * r_D
x_D_right  <- x_D_left + width_D

## ===================== 11. motif 坐标（按 XML 实际长度） =====================

max_len_motif <- max(motif_sites$seqlen, na.rm = TRUE)
scale_motif   <- width_C / max_len_motif

cat("最长 motif 序列长度: ", max_len_motif,
    "；motif 面板宽度: ", width_C,
    "；scale_motif: ", scale_motif, "\n")

## motif 基因主干线
motif_gene_tracks <- motif_sites %>%
  dplyr::distinct(gene, y, seqlen) %>%
  dplyr::mutate(
    x_gene_start = x_C_left,
    x_gene_end   = x_C_left + seqlen * scale_motif
  )

## motif 矩形
motif_rect <- motif_sites %>%
  dplyr::mutate(
    xmin = x_C_left + (start - 1) * scale_motif,
    xmax = x_C_left + end        * scale_motif,
    ymin = y - 0.25,
    ymax = y + 0.25
  )

## ===================== 12. gene structure 坐标（按 GFF3 实际长度；只画 CDS + UTR） =====================

max_gene_len  <- max(gene_ranges$gene_len, na.rm = TRUE)
scale_gene    <- width_D / max_gene_len

cat("最长基因长度: ", max_gene_len,
    "；gene 面板宽度: ", width_D,
    "；scale_gene: ", scale_gene, "\n")

## 基因主干线（Panel D）
gene_tracks <- gene_ranges %>%
  dplyr::mutate(
    x_gene_start = x_D_left,
    x_gene_end   = x_D_left + gene_len * scale_gene
  )

## 各 feature 矩形（先算坐标）
gene_feat_rect <- gene_feat %>%
  dplyr::mutate(
    xmin = x_D_left + rel_start * scale_gene,
    xmax = x_D_left + rel_end   * scale_gene,
    ymin = y - 0.25,
    ymax = y + 0.25
  )

## 只保留 CDS 和 UTR，不画 exon 等其它类型
gene_feat_rect <- gene_feat_rect %>%
  dplyr::filter(Type %in% c("CDS", "UTR"))

## gene structure 深色配色（只针对 CDS 和 UTR）
gene_palette <- c(
  CDS = "#1b9e77",  # 深蓝绿
  UTR = "#d95f02"   # 深橙
)

gene_types    <- sort(unique(gene_feat_rect$Type))
gene_cols_vec <- gene_palette[gene_types]
names(gene_cols_vec) <- gene_types

## ===================== 13. 亚家族竖线的 Y 范围 =====================

subfam_ranges <- tip_dat %>%
  dplyr::filter(!is.na(subfamily)) %>%
  dplyr::group_by(subfamily) %>%
  dplyr::summarise(
    ymin = min(y),
    ymax = max(y),
    ymid = (ymin + ymax) / 2,
    .groups = "drop"
  )

## ===================== 14. 顶部三个表头的位置 =====================

tree_header_x  <- (x_A_left  + x_A_right)  / 2
motif_header_x <- (x_C_left  + x_C_right)  / 2
gene_header_x  <- (x_D_left  + x_D_right)  / 2
header_y       <- max_y + 1.2

## ===================== 15. 拼合：树 + motif + gene + 竖线 + 表头 =====================

p_combo <- p_tree +
  ## Panel C：motif 基因主干线（浅灰）
  geom_segment(
    data        = motif_gene_tracks,
    inherit.aes = FALSE,
    aes(
      x    = x_gene_start,
      xend = x_gene_end,
      y    = y,
      yend = y
    ),
    colour    = "grey70",
    linewidth = 0.4
  ) +
  ## motif 用新的 fill scale
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
  scale_fill_manual(values = motif_cols, name = "Motif") +
  ## Panel D：gene 基因主干线
  geom_segment(
    data        = gene_tracks,
    inherit.aes = FALSE,
    aes(
      x    = x_gene_start,
      xend = x_gene_end,
      y    = y,
      yend = y
    ),
    colour    = "grey70",
    linewidth = 0.4
  ) +
  ## gene structure 用新的 fill scale（CDS / UTR 深色）
  ggnewscale::new_scale_fill() +
  geom_rect(
    data        = gene_feat_rect,
    inherit.aes = FALSE,
    aes(
      xmin = xmin,
      xmax = xmax,
      ymin = ymin,
      ymax = ymax,
      fill = Type
    ),
    color = NA
  ) +
  scale_fill_manual(
    values = gene_cols_vec,
    name   = "Gene Structure"
  ) +
  ## Panel B：亚家族竖线 + 标签
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
  ## 顶部三个表头
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
  annotate(
    "text",
    x        = gene_header_x,
    y        = header_y,
    label    = "Gene structure",
    fontface = "bold",
    size     = 4
  ) +
  ## X 轴范围从树最左到 gene panel 最右
  coord_cartesian(
    xlim = c(x_A_left, x_D_right + tree_core_width * 0.1)
  ) +
  expand_limits(y = header_y + 0.5) +
  theme(
    legend.position   = "right",
    legend.background = element_rect(color = "#969696", fill = "#d9d9d9")
  )

## 整体宽一点，容得下四个 panel
ggsave(output_pdf, p_combo, width = 18, height = 10)

cat("已输出 树 + motif + 基因结构 组合图到: ", output_pdf, "\n")
