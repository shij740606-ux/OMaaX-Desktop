#!/usr/bin/env Rscript

## ===================== 1. 解析命令行参数 =====================

args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 2) {
  stop("用法: Rscript tree_plot.R <treefile> <subfamily_file> [output_pdf] [x_max]\n",
       call. = FALSE)
}

treefile       <- args[1]
subfamily_file <- args[2]

## 第三个参数：输出 pdf 名称（可选）
if (length(args) >= 3) {
  output_pdf <- args[3]
} else {
  ## 如果没给，就用树文件名去掉后缀自动生成
  output_pdf <- sub("\\.[^.]*$", "", treefile)
  output_pdf <- paste0(output_pdf, ".pdf")
}

## 第四个参数：x 轴最大值（可选）
if (length(args) >= 4) {
  x_max <- as.numeric(args[4])
} else {
  x_max <- 18
}

cat("树文件:      ", treefile,       "\n")
cat("亚家族文件:  ", subfamily_file, "\n")
cat("输出文件:    ", output_pdf,     "\n")
cat("x_max:       ", x_max,          "\n")

## ===================== 2. 加载所需 R 包 =====================

suppressPackageStartupMessages({
  library(ape)
  library(ggtree)
  library(tidytree)
  library(dplyr)
  library(RColorBrewer)
  library(ggplot2)
})

## ===================== 3. 封装绘图函数 =====================

plot_iqtree_by_subfamily <- function(treefile, subfamily_file, x_max = 18) {
  ## ---------- 3.1 读树 ----------
  tree <- read.tree(treefile)

  ## 转成 tibble（tidytree::as_tibble）
  tree_df <- as_tibble(tree)

  ## 手动添加 isTip 列：phylo 对象中，1:Ntip 是叶节点
  tree_df$isTip <- tree_df$node <= length(tree$tip.label)

  ## ---------- 3.2 读亚家族文件 ----------
  ## 格式: 两列，无表头：geneID subfamily
  subfam <- read.table(subfamily_file,
                       header = FALSE,
                       stringsAsFactors = FALSE)
  colnames(subfam) <- c("label", "subfamily")  # label 要与树的 tip.label 对应

  ## ---------- 3.3 生成 support 列 ----------
  ## 只给内部节点算 bootstrap，tip 为 NA
  tree_df$support <- ifelse(
    tree_df$isTip,
    NA_real_,
    suppressWarnings(as.numeric(tree_df$label))
  )

  ## ---------- 3.4 合并 subfamily 信息到 tree_df ----------
  tree_df <- dplyr::left_join(tree_df, subfam, by = "label")

  ## ---------- 3.5 自动配色（按 subfamily） ----------
  subfam_levels <- sort(unique(subfam$subfamily))
  n_subfam <- length(subfam_levels)

  if (n_subfam == 0) {
    stop("在亚家族文件中没有检测到任何 subfamily，请检查文件格式。", call. = FALSE)
  }

  ## Set1 最多 9 个颜色；至少 3 个颜色
  n_col <- max(3, min(n_subfam, 9))
  base_cols <- RColorBrewer::brewer.pal(n_col, "Set1")
  subfam_cols <- setNames(base_cols[seq_len(n_subfam)], subfam_levels)

  ## ---------- 3.6 计算每个 subfamily 的 clade 根节点 node ----------
  ## 使用 getMRCA，若某 subfamily 只有 1 条基因，则用它自己的 tip 节点
  node_subfam <- subfam %>%
    dplyr::group_by(subfamily) %>%
    dplyr::summarise(
      node = {
        tips <- label
        if (length(tips) == 1) {
          which(tree$tip.label == tips)
        } else {
          getMRCA(tree, tips)
        }
      },
      .groups = "drop"
    )

  ## ---------- 3.7 构建 ggtree 图 ----------
  p <- ggtree(tree, branch.length = "none") %<+% tree_df +
    ## 节点 bootstrap 支持度
    geom_nodepoint(
      aes(fill = cut(support, c(0, 50, 75, 100))),
      shape = 21,
      size  = 2,
      na.rm = TRUE
    ) +
    scale_fill_manual(
      values = c("black", "grey", "white"),
      guide  = "legend",
      name   = "Bootstrap Percentage (BP)",
      breaks = c("(75,100]", "(50,75]", "(0,50]"),
      labels = expression(BP > 75,
                          50 < BP * "<=75",
                          BP <= 50)
    ) +
    ## tip 按亚家族上色
    geom_tippoint(
      aes(color = subfamily),
      size  = 2,
      na.rm = TRUE
    ) +
    scale_color_manual(
      values = subfam_cols,
      name   = "Subfamily"
    ) +
    ## 叶子标签（基因 ID）
    geom_tiplab(offset = 0.5, size = 3) +
    ## 标题 & x 轴范围
    ggtitle("Phylogenetic Tree") +
    xlim(NA, x_max) +
    theme_tree() +
    theme(
      plot.title       = element_text(hjust = 0.5),
      legend.background = element_rect(color = "#969696",
                                       fill  = "#d9d9d9")
    )

  ## ---------- 3.8 给每个 subfamily 添加 clade 标签 ----------
  for (i in seq_len(nrow(node_subfam))) {
    this_sub  <- node_subfam$subfamily[i]
    this_node <- node_subfam$node[i]
    this_col  <- subfam_cols[[this_sub]]

    p <- p +
      geom_cladelab(
        node        = this_node,
        label       = this_sub,
        align       = TRUE,
        angle       = 270,
        offset      = 6.5,
        offset.text = 1,
        textcolor   = this_col,
        barcolor    = this_col,
        barsize     = 1.5,
        extend      = 0.7
      )
  }

  return(p)
}

## ===================== 4. 调用函数并保存图像 =====================

p <- plot_iqtree_by_subfamily(treefile, subfamily_file, x_max = x_max)

ggsave(output_pdf, p, width = 8, height = 10)

cat("已输出图像到: ", output_pdf, "\n")
