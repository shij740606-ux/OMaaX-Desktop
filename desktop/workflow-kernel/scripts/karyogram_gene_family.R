#!/usr/bin/env Rscript

# karyogram_gene_family_ggplot.R
#
# 用法：
#   Rscript karyogram_gene_family_ggplot.R \
#     Ft.gff Ft.SPL.geneID genome.len Ft.tandem Ft.SPL.karyogram.ggplot.pdf [label_gap_mb]
#
# 参数说明（全部“无表头”）：
#   1: gff        : chr  gene_id  start  end      (制表符分隔，无表头)
#   2: family_ids : 一行一个 gene_id             (无表头)
#   3: genome_len : chr  length(bp)              (制表符分隔，无表头)
#   4: tandem     : 每行  gene1,gene2            (逗号分隔，无表头)
#   5: output.pdf : 输出图
#   6: label_gap_mb（可选）: 同一染色体上相邻两个标签之间的最小 y 间距（单位 Mb，默认 2）
#
# 图的特点：
#   - 染色体竖直，顶部齐平；白色“填充”+ 黑色边框，顶端/底端圆角
#   - 左侧：基因物理位置标签（xx.x Mb），有连线，横排，垂直于染色体
#   - 右侧：基因 ID 标签，有连线，横排，垂直于染色体
#   - 左右两边标签在同一水平线上，整齐对称

args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 5) {
  cat("Usage:\n",
      "  Rscript karyogram_gene_family_ggplot.R <gff> <family_ids> <genome_len> <tandem_pairs> <output.pdf> [label_gap_mb]\n\n",
      "Example:\n",
      "  Rscript karyogram_gene_family_ggplot.R Ft.gff Ft.SPL.geneID genome.len Ft.tandem Ft.SPL.karyogram.ggplot.pdf 2\n",
      file = stderr())
  quit(save = "no", status = 1)
}

gff_file    <- args[1]
family_file <- args[2]
genome_file <- args[3]
tandem_file <- args[4]
output_file <- args[5]

# 标签之间最小间距（单位 Mb）
label_gap_mb <- 2
if (length(args) >= 6) {
  lg <- suppressWarnings(as.numeric(args[6]))
  if (!is.na(lg) && lg > 0) label_gap_mb <- lg
}
label_gap_bp <- label_gap_mb * 1e6

# ----------------- 加载依赖包 --------------------

if (!requireNamespace("ggplot2", quietly = TRUE)) {
  install.packages("ggplot2", repos = "https://cloud.r-project.org")
}
suppressPackageStartupMessages(library(ggplot2))

# ----------------- 读入文件（无表头） ------------

# 1) Ft.gff: chr  gene  start  end
genes_all <- read.table(
  gff_file,
  header = FALSE,
  sep = "\t",
  stringsAsFactors = FALSE,
  col.names = c("chr", "gene", "start", "end")
)

# 2) 家族 ID 列表
family_ids <- read.table(
  family_file,
  header = FALSE,
  stringsAsFactors = FALSE
)[, 1]
family_ids <- trimws(family_ids)

# 3) 染色体长度：chr length(bp)
genome_len <- read.table(
  genome_file,
  header = FALSE,
  sep = "\t",
  stringsAsFactors = FALSE,
  col.names = c("chr", "length")
)

# 4) 串联重复：gene1,gene2
tandem_genes <- character(0)
tandem_pairs <- NULL
if (file.exists(tandem_file) && file.info(tandem_file)$size > 0) {
  tandem_pairs <- read.csv(
    tandem_file,
    header = FALSE,
    stringsAsFactors = FALSE
  )
  tandem_pairs <- tandem_pairs[, 1:2]
  tandem_genes <- unique(as.character(unlist(tandem_pairs)))
}

# ----------------- 过滤家族基因 ------------------

genes_family <- genes_all[genes_all$gene %in% family_ids, ]

if (nrow(genes_family) == 0) {
  cat("ERROR: No genes from", family_file, "were found in", gff_file, "\n",
      file = stderr())
  quit(save = "no", status = 2)
}

# 保持 genome_len 中的染色体顺序
genome_len$chr <- as.character(genome_len$chr)
chrom_order    <- genome_len$chr

genes_family$chr <- factor(genes_family$chr, levels = chrom_order)
genes_family     <- genes_family[order(genes_family$chr, genes_family$start), ]

# 标记串联重复
genes_family$is_tandem <- genes_family$gene %in% tandem_genes
genes_family$type      <- ifelse(genes_family$is_tandem, "Tandem", "Family")
genes_family$type      <- factor(genes_family$type, levels = c("Family", "Tandem"))

# ----------------- 构建染色体坐标 ----------------

chrom_df <- genome_len
chrom_df$chr <- factor(chrom_df$chr, levels = chrom_order)
chrom_df     <- chrom_df[order(chrom_df$chr), ]

# 原版视觉规格：固定染色体粗细、点/字大小和基础横向间距，只在必要时
# 根据 gene ID 与染色体 ID 的文本宽度增加相邻染色体间距。
chrom_spacing_base <- 20
label_dx_right <- 2
label_char_unit <- 0.62
chr_char_unit <- 0.50
text_pad <- 2.0
max_single_row_span <- 170

chr_gene_chars <- tapply(nchar(as.character(genes_family$gene)), genes_family$chr, max, na.rm = TRUE)
chr_gene_chars[!is.finite(chr_gene_chars)] <- 0
chrom_df$gene_label_width <- as.numeric(chr_gene_chars[as.character(chrom_df$chr)]) * label_char_unit
chrom_df$gene_label_width[is.na(chrom_df$gene_label_width)] <- 0
chrom_df$chr_label_half_width <- pmax(2.0, nchar(as.character(chrom_df$chr)) * chr_char_unit / 2)
chrom_df$right_need <- label_dx_right + chrom_df$gene_label_width + text_pad
chrom_df$left_need <- chrom_df$chr_label_half_width + text_pad
chrom_df$chr_x <- 0
chrom_df$row <- 1

# 先按动态间距试排一行；如果超出原版可读宽度，则拆成两行。
place_row <- function(df, start_x = 0) {
  if (nrow(df) == 0) return(df)
  df$chr_x <- numeric(nrow(df))
  df$chr_x[1] <- start_x
  if (nrow(df) > 1) {
    for (i in 2:nrow(df)) {
      needed_gap <- df$right_need[i - 1] + df$left_need[i] + 2.5
      df$chr_x[i] <- df$chr_x[i - 1] + max(chrom_spacing_base, needed_gap)
    }
  }
  df
}

candidate <- place_row(chrom_df)
single_span <- max(candidate$chr_x) - min(candidate$chr_x)
if (single_span <= max_single_row_span || nrow(chrom_df) <= 4) {
  chrom_df <- candidate
  chrom_df$row <- 1
} else {
  # 两行尽量均分，避免第一行过密、第二行过空。
  split_at <- ceiling(nrow(chrom_df) / 2)
  row1 <- place_row(chrom_df[seq_len(split_at), , drop = FALSE])
  row2 <- place_row(chrom_df[(split_at + 1):nrow(chrom_df), , drop = FALSE])
  row1$row <- 1
  row2$row <- 2
  chrom_df <- rbind(row1, row2)
}

# 每行保持同一物理 y 比例；多行时只增加行间距，不改变染色体/字体比例。
y_top <- max(chrom_df$length)
row_gap <- 0.32 * y_top
row_offsets <- setNames((max(chrom_df$row) - chrom_df$row) * (y_top + row_gap), chrom_df$chr)
chrom_df$row_offset <- as.numeric(row_offsets[as.character(chrom_df$chr)])
chrom_df$y_top    <- chrom_df$row_offset + y_top
chrom_df$y_bottom <- chrom_df$row_offset + y_top - chrom_df$length

# 基因在竖直方向的位置（从每行顶部往下量）
genes_family$chr_x <- chrom_df$chr_x[match(genes_family$chr, chrom_df$chr)]
genes_family$row_offset <- chrom_df$row_offset[match(genes_family$chr, chrom_df$chr)]
genes_family$y_gene <- genes_family$row_offset + y_top - genes_family$start

top_offset    <- 0.05 * y_top
bottom_offset <- 0.05 * y_top

# ----------------- 计算整齐的标签 Y 位置 ----------

# 同一染色体上，gene ID 标签使用最小 y 间距避让。间距不能只按 Mb，
# 还要满足 14x8 图上的视觉可读高度，否则相邻 ID 仍会重叠。
genes_family$y_label <- NA_real_
font_size_pt <- 2.0 * 2.845
panel_height_in <- ifelse(max(chrom_df$row) == 1, 8, 12) - 1.1
auto_visual_gap_bp <- y_top * (font_size_pt / 72) / max(1, panel_height_in) * 1.55
effective_label_gap_bp <- max(label_gap_bp, auto_visual_gap_bp)

for (chr_i in levels(genes_family$chr)) {
  idx <- which(genes_family$chr == chr_i)
  if (length(idx) == 0) next
  ord <- order(genes_family$y_gene[idx])
  ys  <- genes_family$y_gene[idx][ord]
  lab_ys <- numeric(length(ys))
  prev_y <- -Inf
  for (j in seq_along(ys)) {
    y <- ys[j]
    if (y - prev_y < effective_label_gap_bp) y <- prev_y + effective_label_gap_bp
    lab_ys[j] <- y
    prev_y <- y
  }

  chrom_row <- chrom_df[match(chr_i, chrom_df$chr), ]
  lower_bound <- chrom_row$y_bottom + bottom_offset * 0.35
  upper_bound <- chrom_row$y_top - top_offset * 0.35
  overflow <- max(lab_ys, na.rm = TRUE) - upper_bound
  if (is.finite(overflow) && overflow > 0) lab_ys <- lab_ys - overflow
  underflow <- lower_bound - min(lab_ys, na.rm = TRUE)
  if (is.finite(underflow) && underflow > 0) lab_ys <- lab_ys + underflow

  genes_family$y_label[idx][ord] <- lab_ys
}

genes_family$x_label_right <- genes_family$chr_x + label_dx_right

# ----------------- 统一标尺与顶部/底部标注 ----------

chrom_df$length_Mb <- round(chrom_df$length / 1e6, 1)

chrom_df$y_name   <- chrom_df$y_top + top_offset
chrom_df$y_length <- chrom_df$y_bottom - bottom_offset

# 标尺按最长染色体全长绘制；标尺线高度等于最长染色体，标签显示最长染色体 Mb。
scale_len_bp <- max(chrom_df$length)
scale_mb <- scale_len_bp / 1e6
scale_tick <- 1.0

scale_df <- aggregate(row_offset ~ row, chrom_df, min)
scale_df$y_top <- scale_df$row_offset + y_top
scale_df$y_bottom <- scale_df$y_top - scale_len_bp
scale_df$x <- min(chrom_df$chr_x) - 9
scale_df$label <- paste0(round(scale_mb, 1), " Mb")

# ----------------- 画图 --------------------------

col_family <- "#377EB8"
col_tandem <- "#E41A1C"

x_min <- min(scale_df$x) - 5
x_max <- max(chrom_df$chr_x + chrom_df$right_need, genes_family$x_label_right + nchar(as.character(genes_family$gene)) * label_char_unit, na.rm = TRUE) + 6
y_min <- min(chrom_df$y_bottom, genes_family$y_label, scale_df$y_bottom, na.rm = TRUE) - bottom_offset * 1.5
y_max <- max(chrom_df$y_name, genes_family$y_label, na.rm = TRUE) + top_offset * 1.5

# 保持原版比例：一行 14x8；两行只增加高度，宽度不随 x-span 无限放大。
plot_width <- 14
plot_height <- ifelse(max(chrom_df$row) == 1, 8, 12)

pdf(output_file, width = plot_width, height = plot_height)

p <- ggplot() +
  geom_segment(
    data = chrom_df,
    aes(x = chr_x, xend = chr_x, y = y_bottom, yend = y_top),
    linewidth = 4,
    lineend = "round",
    colour  = "black"
  ) +
  geom_segment(
    data = chrom_df,
    aes(x = chr_x, xend = chr_x, y = y_bottom, yend = y_top),
    linewidth = 3,
    lineend = "round",
    colour  = "white"
  ) +
  geom_point(
    data = genes_family,
    aes(x = chr_x, y = y_gene, colour = type),
    size = 1.5
  ) +
  geom_segment(
    data = genes_family,
    aes(x = chr_x, y = y_gene,
        xend = x_label_right, yend = y_label,
        colour = type),
    linewidth = 0.3
  ) +
  geom_text(
    data = genes_family,
    aes(x = x_label_right, y = y_label, label = gene, colour = type),
    hjust = 0,
    size  = 2.0
  ) +
  geom_segment(
    data = scale_df,
    aes(x = x, xend = x, y = y_top, yend = y_bottom),
    linewidth = 0.45,
    colour = "black"
  ) +
  geom_segment(
    data = scale_df,
    aes(x = x - scale_tick, xend = x + scale_tick, y = y_top, yend = y_top),
    linewidth = 0.45,
    colour = "black"
  ) +
  geom_segment(
    data = scale_df,
    aes(x = x - scale_tick, xend = x + scale_tick, y = y_bottom, yend = y_bottom),
    linewidth = 0.45,
    colour = "black"
  ) +
  geom_text(
    data = scale_df,
    aes(x = x - 1.2, y = (y_top + y_bottom) / 2, label = label),
    angle = 90,
    hjust = 0.5,
    size = 2.7
  ) +
  geom_text(
    data = chrom_df,
    aes(x = chr_x, y = y_name, label = chr),
    fontface = "bold",
    size     = 3
  ) +
  geom_text(
    data = chrom_df,
    aes(x = chr_x, y = y_length, label = paste0(length_Mb, " Mb")),
    size = 2.5
  ) +
  scale_colour_manual(values = c(Family = col_family, Tandem = col_tandem)) +
  scale_x_continuous(limits = c(x_min, x_max), expand = c(0, 0)) +
  scale_y_continuous(limits = c(y_min, y_max), expand = c(0, 0)) +
  coord_cartesian(clip = "off") +
  theme_minimal(base_size = 10) +
  theme(
    plot.title      = element_blank(),
    axis.title      = element_blank(),
    panel.grid      = element_blank(),
    axis.ticks      = element_blank(),
    axis.text       = element_blank(),
    legend.position = "none",
    plot.margin     = margin(10, 90, 20, 90)
  )

print(p)
dev.off()

cat("Plot written to:", output_file, "\n",
    "Label gap used (Mb):", label_gap_mb, "\n",
    "Unified scale (Mb):", round(scale_mb, 1), "\n",
    "Effective label gap (Mb):", round(effective_label_gap_bp / 1e6, 2), "\n",
    "Rows:", max(chrom_df$row), "\n",
    "Canvas:", plot_width, "x", plot_height, "in\n",
    "X span:", round(x_min, 2), "to", round(x_max, 2), "\n")
