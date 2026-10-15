#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(ape)
  library(ggtree)
  library(tidytree)
  library(ggplot2)
})

args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 1) {
  stop("用法: Rscript plot_tree.R <treefile> [output_dir]", call. = FALSE)
}

treefile <- args[1]
output_dir <- if (length(args) >= 2 && args[2] != "") args[2] else dirname(treefile)
dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)

tree <- read.tree(treefile)
n_tips <- length(tree$tip.label)
max_id_len <- max(nchar(tree$tip.label))

cat("基因数:", n_tips, " 最长ID:", max_id_len, "字符\n")

# 提取bootstrap
tree_df <- as_tibble(tree)
tree_df$isTip <- tree_df$node <= n_tips
tree_df$support <- ifelse(
  tree_df$isTip,
  NA_real_,
  suppressWarnings(as.numeric(tree_df$label))
)

# 基础图
p_base <- ggtree(tree, layout = "rectangular", branch.length = "none") %<+% tree_df +
  geom_nodepoint(
    aes(fill = cut(support, c(0, 50, 75, 100))),
    shape = 21, size = 2, na.rm = TRUE
  ) +
  scale_fill_manual(
    values = c("black", "grey", "white"),
    name = "Bootstrap",
    breaks = c("(75,100]", "(50,75]", "(0,50]")
  ) +
  geom_tiplab(size = 2.8, offset = 0.02, hjust = 0) +
  theme_tree2()

# 获取范围
pb <- ggplot_build(p_base)
panel_ranges <- pb$layout$panel_params[[1]]

# 计算x轴范围
id_width <- max_id_len * 0.12
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

# 输出
min_height <- max(6, n_tips * 0.3)
output_base <- file.path(output_dir, tools::file_path_sans_ext(basename(treefile)))

# 保存PDF
ggsave(paste0(output_base, ".pdf"), p_final, height = min_height, limitsize = FALSE)

# 保存PNG（使用png()函数避免conda R的API冲突）
png(filename = paste0(output_base, ".png"), width = min_height * 300, height = min_height * 300, res = 300)
print(p_final)
dev.off()

cat("完成\n")
