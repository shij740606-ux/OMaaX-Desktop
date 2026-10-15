#!/usr/bin/env Rscript

## =========================================================
## tree_domain.R
## Panel A：Phylogenetic tree（含基因 ID + Bootstrap）
## Panel B：Subfamily 竖线（可选，帮助视觉分组）
## Panel C：Domain（按蛋白长度 qlen 比例；来自 hmmscan domtblout）
## =========================================================

args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 3) {
  stop(
    "用法:\n",
    "  Rscript tree_domain.R \\\n",
    "    <treefile> <subfamily_file> <domtblout_file> \\\n",
    "    [evalue_cutoff] [coverage_percent] [output_prefix]\n\n",
    call. = FALSE
  )
}

treefile       <- args[1]
subfamily_file <- args[2]
domtblout_file <- args[3]

## E-value 阈值（默认 1e-5）
e_cutoff <- if (length(args) >= 4 && nzchar(args[4])) {
  tmp <- suppressWarnings(as.numeric(args[4]))
  if (is.na(tmp)) 1e-5 else tmp
} else 1e-5

## 覆盖度阈值（可选）
cov_cutoff <- if (length(args) >= 5 && nzchar(args[5])) {
  tmp <- suppressWarnings(as.numeric(args[5]))
  if (is.na(tmp) || tmp <= 0) NA_real_ else tmp / 100
} else NA_real_

## 输出前缀（用于生成 pdf 和 png）
output_prefix <- if (length(args) >= 6 && nzchar(args[6])) {
  args[6]
} else {
  prefix <- sub("\\.[^.]*$", "", treefile)
  paste0(prefix, ".tree_domain")
}

## 输出文件路径
output_pdf <- paste0(output_prefix, ".pdf")
output_png <- paste0(output_prefix, ".png")

cat("树文件:         ", treefile,       "\n")
cat("亚家族文件:     ", subfamily_file, "\n")
cat("domtblout 文件: ", domtblout_file, "\n")
cat("E-value 阈值:   ", e_cutoff,       "\n")
cat("覆盖度过滤:     ", if(is.na(cov_cutoff)) "未启用" else paste0(cov_cutoff*100, "%"), "\n")
cat("输出前缀:       ", output_prefix,  "\n")
cat("PDF 输出:       ", output_pdf,     "\n")
cat("PNG 输出:       ", output_png,     "\n\n")

## ===================== 加载 R 包 =====================


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

## ===================== 读亚家族文件 =====================

add_confidence_labels <- function(subfam_df, subfamily_file) {
  add_subfamily_confidence_labels(subfam_df, subfamily_file)
}


## 处理 "NONE" 情况（表示没有亚家族文件）
if (subfamily_file == "NONE" || !file.exists(subfamily_file)) {
  cat("[INFO] 未找到亚家族文件，使用默认着色\n")
  ## 从树文件读取基因名作为默认亚家族
  tree_tmp <- read.tree(treefile)
  subfam <- data.frame(
    label = tree_tmp$tip.label,
    subfamily = "Default",
    low_confidence = FALSE,
    display_label = tree_tmp$tip.label,
    stringsAsFactors = FALSE
  )
} else {
  subfam <- read.table(subfamily_file, header = FALSE, stringsAsFactors = FALSE)
  colnames(subfam) <- c("label", "subfamily")
  subfam <- add_confidence_labels(subfam, subfamily_file)
}

subfam_levels <- sort(unique(subfam$subfamily))
n_subfam      <- length(subfam_levels)
subfam_cols <- make_subfamily_cols(subfam_levels)

## ===================== 树绘图函数 =====================

plot_tree <- function(treefile, subfam_df, subfam_cols, label_offset = 0.4) {
  tree <- read.tree(treefile)
  tree_df <- as_tibble(tree)
  tree_df$isTip <- tree_df$node <= length(tree$tip.label)
  tree_df <- dplyr::left_join(tree_df, subfam_df, by = "label")
  tree_df$display_label <- ifelse(is.na(tree_df$display_label), tree_df$label, tree_df$display_label)
  
  tree_df$support <- ifelse(tree_df$isTip, NA_real_,
                          suppressWarnings(as.numeric(tree_df$label)))

  ## 修复：使用 plotmath 兼容的表达式
  p <- ggtree(tree, branch.length = "none") %<+% tree_df +
    geom_nodepoint(
      aes(fill = cut(support, c(0, 50, 75, 100))),
      shape = 21, size = 2, na.rm = TRUE
    ) +
    scale_fill_manual(
      values = c("black", "grey40", "grey80"),
      name   = "Bootstrap",
      breaks = c("(75,100]", "(50,75]", "(0,50]"),
      labels = c("BP > 75", "50 < BP <= 75", "BP <= 50")  ## 修复：使用字符串而非 expression
    ) +
    geom_text2(
      aes(label = ifelse(!isTip & !is.na(support), round(support), "")),
      hjust = -0.2, vjust = -0.4, size = 2.2,
      na.rm = TRUE, show.legend = FALSE
    ) +
    geom_tippoint(aes(color = subfamily), size = 2, na.rm = TRUE) +
    scale_color_manual(values = subfam_cols, name = "Subfamily") +
    geom_tiplab(aes(label = display_label), offset = label_offset, size = 3, hjust = 0) +
    theme_tree() +
    theme(legend.background = element_rect(color = "#969696", fill = "#d9d9d9"))

  attr(p, "label_offset") <- label_offset
  return(p)
}

## ===================== 解析 domtblout =====================

parse_domtblout <- function(domtbl_file, e_cutoff = 1e-5,
                            cov_cutoff = NA_real_, tree_genes = NULL) {
  
  dom <- read.table(domtbl_file, header = FALSE, comment.char = "#",
                    stringsAsFactors = FALSE, fill = TRUE)
  
  if (ncol(dom) < 22) {
    stop("domtblout 文件列数少于 22 列，请检查 hmmscan 输出格式。", call. = FALSE)
  }

  colnames(dom)[1:22] <- c(
    "target_name", "t_accession", "tlen", "query_name", "q_accession", "qlen",
    "E_full", "score_full", "bias_full", "dom_index", "dom_of",
    "c_Evalue", "i_Evalue", "dom_score", "dom_bias",
    "hmm_from", "hmm_to", "ali_from", "ali_to", "env_from", "env_to", "acc"
  )

  num_cols <- c("tlen", "qlen", "E_full", "score_full", "i_Evalue",
                "ali_from", "ali_to", "dom_score")
  for (cn in intersect(num_cols, colnames(dom))) {
    dom[[cn]] <- suppressWarnings(as.numeric(dom[[cn]]))
  }

  if (!is.null(tree_genes)) {
    dom <- dom[dom$query_name %in% tree_genes, , drop = FALSE]
  }

  dom <- dom %>%
    mutate(
      dom_len = ali_to - ali_from + 1,
      cov = ifelse(is.na(qlen) | qlen <= 0, NA_real_, dom_len / qlen),
      evalue = if("i_Evalue" %in% colnames(dom)) i_Evalue else E_full
    ) %>%
    filter(!is.na(evalue), evalue <= e_cutoff)

  if (!is.na(cov_cutoff)) {
    dom <- dom %>% filter(is.na(cov) | cov >= cov_cutoff)
  }

  return(dom)
}

## ===================== 画树 & 获取坐标 =====================

label_offset <- 0.4
p_tree <- plot_tree(treefile, subfam, subfam_cols, label_offset = label_offset)

tree_dat <- p_tree$data
tip_dat  <- tree_dat %>%
  filter(isTip) %>%
  select(label, x, y, subfamily)

if (nrow(tip_dat) == 0) stop("树中没有 tip 节点。", call. = FALSE)

x_tree_min <- min(tree_dat$x, na.rm = TRUE)
x_tip_max  <- max(tip_dat$x, na.rm = TRUE)
max_y      <- max(tree_dat$y, na.rm = TRUE)

## 计算 Panel A 宽度
tree_core_width <- x_tip_max - x_tree_min
label_panel_width <- max(1.5, tree_core_width * 0.6)
x_A_left  <- x_tree_min
x_A_right <- x_tip_max + label_offset + label_panel_width

## ===================== 解析 Domain 数据 =====================

domain_hits <- parse_domtblout(domtblout_file, e_cutoff = e_cutoff,
                               cov_cutoff = cov_cutoff, tree_genes = tip_dat$label)

domain_hits <- domain_hits %>%
  left_join(tip_dat, by = c("query_name" = "label")) %>%
  filter(!is.na(y))

if (nrow(domain_hits) == 0) {
  stop("筛选后没有任何 domain 命中。", call. = FALSE)
}

cat("蛋白长度范围: ", min(domain_hits$qlen), " ~ ", max(domain_hits$qlen), "\n")

## ===================== 布局计算 =====================

gap_AB <- max(0.3, tree_core_width * 0.15)
gap_BC <- max(0.3, tree_core_width * 0.15)

r_B <- 0.6   # Subfamily 宽度系数
r_C <- 2.5   # Domain 宽度系数（比原来更宽，突出显示）

## Panel B (Subfamily)
x_B_left  <- x_A_right + gap_AB
width_B   <- tree_core_width * r_B
x_B_right <- x_B_left + width_B
x_subfam  <- (x_B_left + x_B_right) / 2

## Panel C (Domain)
x_C_left  <- x_B_right + gap_BC
width_C   <- tree_core_width * r_C
x_C_right <- x_C_left + width_C

## ===================== Domain 坐标计算 =====================

max_qlen     <- max(domain_hits$qlen, na.rm = TRUE)
scale_domain <- width_C / max_qlen

cat("Domain scale: ", scale_domain, "\n")

## 蛋白主干线
domain_tracks <- domain_hits %>%
  distinct(query_name, y, qlen) %>%
  mutate(
    x_start = x_C_left,
    x_end   = x_C_left + qlen * scale_domain
  )

## Domain 矩形
domain_rect <- domain_hits %>%
  mutate(
    xmin = x_C_left + (ali_from - 1) * scale_domain,
    xmax = x_C_left + ali_to * scale_domain,
    ymin = y - 0.25,
    ymax = y + 0.25
  )

## Domain 配色
domain_levels <- sort(unique(domain_rect$target_name))
domain_cols   <- setNames(make_distinct_cols(length(domain_levels)), domain_levels)

## ===================== 亚家族范围 =====================

subfam_ranges <- make_subfamily_ranges(tip_dat)

## ===================== 底部坐标轴 =====================

make_axis <- function(max_len, scale_val, x_left, axis_y, n_breaks = 5) {
  if (!is.finite(max_len) || max_len <= 0) return(NULL)
  
  brks <- pretty(c(0, max_len), n = n_breaks)
  brks <- brks[brks >= 0 & brks <= max_len]
  
  list(
    line = data.frame(x = x_left, xend = x_left + max_len * scale_val, 
                      y = axis_y, yend = axis_y),
    ticks = data.frame(x = x_left + brks * scale_val, 
                       xend = x_left + brks * scale_val,
                       y = axis_y, yend = axis_y + 0.2),
    labels = data.frame(x = x_left + brks * scale_val, 
                        y = axis_y - 0.3, label = as.integer(brks))
  )
}

axis_y <- min(tree_dat$y) - 1.5
domain_axis <- make_axis(max_qlen, scale_domain, x_C_left, axis_y)

## ===================== 组合绘图 =====================

header_y <- max_y + 1.2

p_combo <- p_tree +
  ## Panel B: Subfamily 竖线
  geom_segment(
    data = subfam_ranges, inherit.aes = FALSE,
    aes(x = x_subfam, xend = x_subfam, y = ymin, yend = ymax, color = subfamily),
    linewidth = 1.5
  ) +
  geom_text(
    data = subfam_ranges, inherit.aes = FALSE,
    aes(x = x_subfam + width_B * 0.08, y = ymid, label = subfamily, color = subfamily),
    hjust = 0, size = 3.5
  ) +
  
  ## Panel C: Domain
  geom_segment(
    data = domain_tracks, inherit.aes = FALSE,
    aes(x = x_start, xend = x_end, y = y, yend = y),
    colour = "grey70", linewidth = 0.4
  ) +
  ggnewscale::new_scale_fill() +
  geom_rect(
    data = domain_rect, inherit.aes = FALSE,
    aes(xmin = xmin, xmax = xmax, ymin = ymin, ymax = ymax, fill = target_name),
    color = NA
  ) +
  scale_fill_manual(values = domain_cols, name = "Domain") +
  
  ## 底部坐标轴
  geom_segment(data = domain_axis$line, inherit.aes = FALSE,
               aes(x = x, xend = xend, y = y, yend = yend), linewidth = 0.4) +
  geom_segment(data = domain_axis$ticks, inherit.aes = FALSE,
               aes(x = x, xend = xend, y = y, yend = yend), linewidth = 0.3) +
  geom_text(data = domain_axis$labels, inherit.aes = FALSE,
            aes(x = x, y = y, label = label), size = 2.5) +
  
  ## 顶部标题
  annotate("text", x = (x_A_left + x_A_right)/2, y = header_y, 
           label = "Phylogenetic tree", fontface = "bold", size = 4) +
  annotate("text", x = x_subfam, y = header_y, 
           label = "Subfamily", fontface = "bold", size = 4) +
  annotate("text", x = (x_C_left + x_C_right)/2, y = header_y, 
           label = "Protein Domain", fontface = "bold", size = 4) +
  
  coord_cartesian(xlim = c(x_A_left - 0.5, x_C_right + 1)) +
  expand_limits(y = header_y + 0.5) +
  theme(legend.position = "right",
        legend.background = element_rect(color = "#969696", fill = "#d9d9d9"))

## ===================== 保存输出（PDF + PNG）=====================

# 保存 PDF（矢量图）
ggsave(output_pdf, p_combo, width = 14, height = 10)
cat("[OK] PDF: ", output_pdf, "\n", sep = "")

# 保存 PNG（使用png()函数避免conda R的API冲突）
png(filename = output_png, width = 4200, height = 3000, res = 300)
print(p_combo)
dev.off()
cat("[OK] PNG: ", output_png, "\n", sep = "")

cat("[OK] Subfamily used: ", subfamily_file, "\n", sep = "")
