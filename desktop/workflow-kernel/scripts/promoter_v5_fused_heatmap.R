#!/usr/bin/env Rscript
# V5 - 融合版热图：采用与motif/structure相同的布局和绘图方式
# 左：进化树（ggtree） | 中：亚家族竖线 | 右：启动子元件热图 | 最右：图例

rm(list = ls())
library(tidyverse)
library(ggtree)
library(treeio)
library(ape)
library(RColorBrewer)
library(ggnewscale)

cat("=== 启动子可视化 V5 - 融合版热图（仿Motif/Structure布局）===\n\n")

args <- commandArgs(trailingOnly = TRUE)

treefile <- args[1]
group_file <- args[2]
promoter_file <- args[3]
output_prefix <- args[5]

cat("树文件:", treefile, "\n")
cat("分组文件:", group_file, "\n")
cat("启动子文件:", promoter_file, "\n")
cat("输出前缀:", output_prefix, "\n\n")

# ===================== 1. 读取数据 =====================

tree <- read.tree(treefile)

group_df <- read.delim(group_file, header = FALSE, stringsAsFactors = FALSE)
colnames(group_df) <- c("label", "subfamily")

normalize_gene <- function(x) {
  sub("\\.\\d+\\.T\\d+$", "", x) %>% 
  sub("\\.T\\d+$", "", .) %>%
  sub("\\.\\d+$", "", .)
}

group_df$gene_key <- normalize_gene(group_df$label)

promoter <- read.delim(promoter_file, header = FALSE, stringsAsFactors = FALSE)
promoter <- promoter[, c(1, 2)]
colnames(promoter) <- c("gene", "element")
promoter$gene <- normalize_gene(promoter$gene)

tree_labels <- tree$tip.label
tree_labels_base <- normalize_gene(tree_labels)
promoter <- promoter %>% filter(gene %in% tree_labels_base)

cat("数据: 树", length(tree_labels), "基因, 启动子", nrow(promoter), "记录\n\n")

# ===================== 2. 亚家族颜色 =====================
subfam_levels <- sort(unique(group_df$subfamily))
n_subfam <- length(subfam_levels)
base_cols_sf <- brewer.pal(8, "Dark2")
subfam_cols_vec <- rep_len(base_cols_sf, n_subfam)
subfam_cols <- setNames(subfam_cols_vec, subfam_levels)

# ===================== 3. 绘制进化树（Panel A） =====================
label_offset <- 0.4

tree_df <- as_tibble(tree)
tree_df$isTip <- tree_df$node <= length(tree$tip.label)
tree_df$gene_key <- ifelse(tree_df$isTip, normalize_gene(tree_df$label), NA_character_)
tree_df <- left_join(tree_df, group_df[, c("gene_key", "subfamily")], by = "gene_key")
tree_df$support <- ifelse(tree_df$isTip, NA_real_, suppressWarnings(as.numeric(tree_df$label)))

p_tree <- ggtree(tree, branch.length = "none") %<+% tree_df +
  geom_nodepoint(aes(fill = cut(support, c(0, 50, 75, 100))), shape = 21, size = 2, na.rm = TRUE) +
  scale_fill_manual(values = c("black", "grey40", "grey80"), guide = "legend",
                    name = "Bootstrap Percentage (BP)",
                    breaks = c("(75,100]", "(50,75]", "(0,50]"),
                    labels = expression(BP > 75, 50 < BP * "<=75", BP <= 50)) +
  geom_tippoint(aes(color = subfamily), size = 2, na.rm = TRUE) +
  scale_color_manual(values = subfam_cols, name = "Subfamily", na.value = "grey70") +
  geom_tiplab(offset = label_offset, size = 3, hjust = 0) +
  theme_tree() +
  theme(legend.position = "right", legend.background = element_rect(color = "#969696", fill = "#d9d9d9"))

# ===================== 4. 获取树的坐标信息 =====================
tree_dat <- p_tree$data
tip_dat <- tree_dat %>% filter(isTip) %>% select(label, x, y, subfamily)

x_tree_min <- min(tree_dat$x, na.rm = TRUE)
x_tip_max <- max(tip_dat$x, na.rm = TRUE)
max_y <- max(tree_dat$y, na.rm = TRUE)

tip_labels <- tip_dat$label
max_label_len <- max(nchar(tip_labels), na.rm = TRUE)
char_width <- 0.12
label_extra_width <- max_label_len * char_width

# ===================== 5. 准备热图数据 =====================
element_counts <- promoter %>% count(element) %>% arrange(desc(n))
cat("元件统计（前10）:\n")
print(head(element_counts, 10))

retain_elements <- element_counts %>% filter(n >= 3) %>% pull(element)
cat(sprintf("保留出现次数>=3的元件: %d个\n", length(retain_elements)))

promoter_filtered <- promoter %>% filter(element %in% retain_elements) %>% select(gene, element)

all_combinations <- expand.grid(gene = unique(c(promoter_filtered$gene, tree_labels_base)),
                                element = unique(promoter_filtered$element), stringsAsFactors = FALSE)

promoter_counts <- promoter_filtered %>% group_by(gene, element) %>% summarise(count = n(), .groups = 'drop')

heatmap_data <- all_combinations %>%
  left_join(promoter_counts, by = c("gene", "element")) %>%
  mutate(count = ifelse(is.na(count), 0, count),
         count_cat = cut(count, breaks = c(0, 1, 2, 3, 5, 10, 20, 50, 100, 200), include.lowest = TRUE, right = FALSE))

gene_y_map <- setNames(tip_dat$y, normalize_gene(tip_dat$label))
heatmap_data$y <- gene_y_map[heatmap_data$gene]

# ===================== 6. 计算Panel布局 =====================
x_A_left <- x_tree_min
x_A_right <- x_tip_max + label_offset + label_extra_width + 0.5

gap_AB <- 2
width_B <- 3
x_B_left <- x_A_right + gap_AB
x_B_right <- x_B_left + width_B
x_subfam <- (x_B_left + x_B_right) / 2

gap_BC <- 0.8
n_elements <- length(retain_elements)
element_width <- 0.5
width_C <- max(5, n_elements * element_width)

x_C_left <- x_B_right + gap_BC
x_C_right <- x_C_left + width_C

element_x_map <- setNames(seq(x_C_left, x_C_right - element_width, length.out = n_elements), retain_elements)
heatmap_data$xmin <- element_x_map[heatmap_data$element]
heatmap_data$xmax <- heatmap_data$xmin + element_width * 0.9

gene_height <- 0.4
heatmap_data <- heatmap_data %>% mutate(ymin = y - gene_height, ymax = y + gene_height)

# ===================== 7. 准备亚家族竖线数据 =====================
subfam_ranges <- tip_dat %>% filter(!is.na(subfamily)) %>% group_by(subfamily) %>%
  summarise(ymin = min(y), ymax = max(y), ymid = (ymin + ymax) / 2, .groups = "drop")

# ===================== 8. 热图颜色定义 =====================
count_colors <- c("[0,1)" = "#f7fbff", "[1,2)" = "#c6dbef", "[2,3)" = "#9ecae1",
                  "[3,5)" = "#6baed6", "[5,10)" = "#4292c6", "[10,20)" = "#2171b5",
                  "[20,50)" = "#08519c", "[50,100)" = "#08306b", "[100,200]" = "#041d3a")

# ===================== 9. 组合绘图 =====================
tree_header_x <- (x_A_left + x_A_right) / 2
subf_header_x <- (x_B_left + x_B_right) / 2
heatmap_header_x <- (x_C_left + x_C_right) / 2
header_y <- max_y + 1.5

p_combo <- p_tree +
  geom_segment(data = subfam_ranges, inherit.aes = FALSE,
               aes(x = x_subfam, xend = x_subfam, y = ymin, yend = ymax, colour = subfamily),
               linewidth = 2, show.legend = FALSE) +
  geom_text(data = subfam_ranges, inherit.aes = FALSE,
            aes(x = x_subfam + 0.15, y = ymid, label = subfamily, colour = subfamily),
            hjust = 0, size = 3.5, show.legend = FALSE) +
  ggnewscale::new_scale_fill() +
  geom_rect(data = heatmap_data %>% filter(count > 0), inherit.aes = FALSE,
            aes(xmin = xmin, xmax = xmax, ymin = ymin, ymax = ymax, fill = count_cat), color = NA) +
  geom_text(data = heatmap_data %>% filter(count > 0), inherit.aes = FALSE,
            aes(x = (xmin + xmax) / 2, y = (ymin + ymax) / 2, label = count),
            size = 2, color = "white") +
  scale_fill_manual(values = count_colors, name = "Element Count", drop = FALSE) +
  annotate("text", x = tree_header_x, y = header_y, label = "Phylogenetic tree", fontface = "bold", size = 4) +
  annotate("text", x = subf_header_x, y = header_y, label = "Subfamily", fontface = "bold", size = 4) +
  annotate("text", x = heatmap_header_x, y = header_y, label = "Promoter Elements", fontface = "bold", size = 4) +
  coord_cartesian(xlim = c(x_A_left - 0.5, x_C_right + 1)) +
  expand_limits(y = header_y + 0.5) +
  theme(legend.position = "right", legend.background = element_rect(color = "#969696", fill = "#d9d9d9"))

# ===================== 10. 保存输出 =====================
output_pdf <- paste0(output_prefix, "_tree_promoter_heatmap.pdf")
output_png <- paste0(output_prefix, "_tree_promoter_heatmap.png")

ggsave(output_pdf, p_combo, width = 14, height = 10)

png(filename = output_png, width = 4200, height = 3000, res = 300)
print(p_combo)
dev.off()

cat("[OK] PDF: ", output_pdf, "\n")
cat("[OK] PNG: ", output_png, "\n")
cat("===== 全部完成 =====\n")