#!/usr/bin/env Rscript
# V6 - 5面板组合图：A(树)+B(亚家族)+C(甜甜圈)+D(热图)+E(图例)

rm(list = ls())
library(tidyverse)
library(ggtree)
library(treeio)
library(ape)
library(RColorBrewer)
library(ggnewscale)

cat("=== 启动子可视化 V6 - 5面板组合图 ===\n\n")

args <- commandArgs(trailingOnly = TRUE)
treefile <- args[1]
group_file <- args[2]
promoter_file <- args[3]
output_prefix <- args[5]

cat("树文件:", treefile, "\n")
cat("分组文件:", group_file, "\n") 
cat("启动子文件:", promoter_file, "\n")
cat("输出前缀:", output_prefix, "\n\n")

# 读取数据
tree <- read.tree(treefile)
group_df <- read.delim(group_file, header = FALSE, stringsAsFactors = FALSE)
colnames(group_df) <- c("label", "subfamily")

normalize_gene <- function(x) {
  sub("\\.\\d+\\.T\\d+$", "", x) %>% sub("\\.T\\d+$", "", .) %>% sub("\\.\\d+$", "", .)
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

# 7大分类
big7_order <- c("Core promoter", "Light response", "Hormone signaling", 
                "Stress response", "TF binding", "Development", "Other")
promoter$big7 <- sapply(promoter$element, function(x) {
  if(grepl("TATA|CAAT", x, ignore.case = TRUE)) return("Core promoter")
  if(grepl("G-box|GT1|I-box|Sp1|Box.4|GATA", x, ignore.case = TRUE)) return("Light response")
  if(grepl("ABRE|GA|JA|SA|Auxin|Ethylene", x, ignore.case = TRUE)) return("Hormone signaling")
  if(grepl("DRE|LTR|ARE|STRE|W.box|W-box", x, ignore.case = TRUE)) return("Stress response")
  if(grepl("MYB|MYC|WRKY", x, ignore.case = TRUE)) return("TF binding")
  if(grepl("RY|AAGAA|CAT|GCN4", x, ignore.case = TRUE)) return("Development")
  return("Other")
})
promoter$big7 <- factor(promoter$big7, levels = big7_order)

# 颜色
subfam_levels <- sort(unique(group_df$subfamily))
subfam_cols <- setNames(brewer.pal(8, "Dark2")[1:length(subfam_levels)], subfam_levels)
big7_colors <- c("Core promoter"="#e41a1c", "Light response"="#377eb8",
                 "Hormone signaling"="#4daf4a", "Stress response"="#984ea3",
                 "TF binding"="#ff7f00", "Development"="#f781bf", "Other"="#999999")

# Panel A: 进化树
label_offset <- 0.4
tree_df <- as_tibble(tree)
tree_df$isTip <- tree_df$node <= length(tree$tip.label)
tree_df$gene_key <- ifelse(tree_df$isTip, normalize_gene(tree_df$label), NA_character_)
tree_df <- left_join(tree_df, group_df[, c("gene_key", "subfamily")], by = "gene_key")
tree_df$support <- ifelse(tree_df$isTip, NA_real_, suppressWarnings(as.numeric(tree_df$label)))

p_tree <- ggtree(tree, branch.length = "none") %<+% tree_df +
  geom_nodepoint(aes(fill = cut(support, c(0, 50, 75, 100))), shape = 21, size = 2, na.rm = TRUE) +
  scale_fill_manual(values = c("black", "grey40", "grey80"), guide = "legend",
                    name = "Bootstrap", breaks = c("(75,100]", "(50,75]", "(0,50]"),
                    labels = c(">75", "50-75", "<50")) +
  geom_tippoint(aes(color = subfamily), size = 2, na.rm = TRUE) +
  scale_color_manual(values = subfam_cols, name = "Subfamily", na.value = "grey70") +
  geom_tiplab(offset = label_offset, size = 2.5, hjust = 0) +
  theme_tree() + theme(legend.position = "none")

# 获取坐标
tree_dat <- p_tree$data
tip_dat <- tree_dat %>% filter(isTip) %>% select(label, x, y, subfamily)
x_tree_min <- min(tree_dat$x, na.rm = TRUE)
x_tip_max <- max(tip_dat$x, na.rm = TRUE)
max_y <- max(tree_dat$y, na.rm = TRUE)
max_label_len <- max(nchar(tip_dat$label), na.rm = TRUE)
label_extra_width <- max_label_len * 0.12

# Panel B: 亚家族竖线
subfam_ranges <- tip_dat %>% filter(!is.na(subfamily)) %>% group_by(subfamily) %>%
  summarise(ymin = min(y), ymax = max(y), ymid = (ymin + ymax) / 2, .groups = "drop")

# Panel D: 热图数据
element_counts <- promoter %>% count(element) %>% arrange(desc(n))
retain_elements <- element_counts %>% filter(n >= 3) %>% pull(element)
cat(sprintf("保留元件: %d个\n", length(retain_elements)))

promoter_filtered <- promoter %>% filter(element %in% retain_elements) %>% select(gene, element)
all_combinations <- expand.grid(gene = unique(c(promoter_filtered$gene, tree_labels_base)),
                                element = unique(promoter_filtered$element), stringsAsFactors = FALSE)
promoter_counts <- promoter_filtered %>% group_by(gene, element) %>% summarise(count = n(), .groups = 'drop')

heatmap_data <- all_combinations %>%
  left_join(promoter_counts, by = c("gene", "element")) %>%
  mutate(count = ifelse(is.na(count), 0, count),
         count_cat = cut(count, breaks = c(0, 1, 2, 3, 5, 10, 20, 50, 100, 200), 
                        include.lowest = TRUE, right = FALSE))

gene_y_map <- setNames(tip_dat$y, normalize_gene(tip_dat$label))
heatmap_data$y <- gene_y_map[heatmap_data$gene]

# 先定义基因高度
gene_height <- 0.35

# 计算5面板布局（根据内容自动调整）
x_A_left <- x_tree_min
x_A_right <- x_tip_max + label_offset + label_extra_width + 0.3

gap_AB <- 0.5
width_B <- 2.5
x_B_left <- x_A_right + gap_AB
x_B_right <- x_B_left + width_B
x_subfam <- (x_B_left + x_B_right) / 2

gap_BC <- 1.0
width_C <- 12  # 大幅增加堆叠条形图宽度
x_C_left <- x_B_right + gap_BC
x_C_right <- x_C_left + width_C

gap_CD <- 1.0
n_elements <- length(retain_elements)
# 热图方格为正方形
element_width <- gene_height * 2
width_D <- n_elements * element_width
x_D_left <- x_C_right + gap_CD
x_D_right <- x_D_left + width_D

element_x_map <- setNames(seq(x_D_left, x_D_right - element_width, length.out = n_elements), retain_elements)
heatmap_data$xmin <- element_x_map[heatmap_data$element]
heatmap_data$xmax <- heatmap_data$xmin + element_width * 0.9

heatmap_data <- heatmap_data %>% mutate(ymin = y - gene_height, ymax = y + gene_height)

# Panel C: 7大分类堆叠数据
category_data <- promoter %>%
  group_by(gene, big7) %>%
  summarise(count = n(), .groups = 'drop') %>%
  group_by(gene) %>%
  mutate(prop = count / sum(count)) %>%
  ungroup()

category_data$y <- gene_y_map[category_data$gene]

# 计算堆叠图的x坐标
x_cat_left <- x_C_left
x_cat_right <- x_C_right
category_data <- category_data %>%
  group_by(gene) %>%
  mutate(xmin = x_cat_left + cumsum(prop) * (x_cat_right - x_cat_left) - prop * (x_cat_right - x_cat_left),
         xmax = x_cat_left + cumsum(prop) * (x_cat_right - x_cat_left)) %>%
  ungroup()

gene_height_cat <- 0.35
category_data <- category_data %>%
  mutate(ymin = y - gene_height_cat, ymax = y + gene_height_cat)

# 颜色定义
count_colors <- c("[0,1)" = "#f7fbff", "[1,2)" = "#c6dbef", "[2,3)" = "#9ecae1",
                  "[3,5)" = "#6baed6", "[5,10)" = "#4292c6", "[10,20)" = "#2171b5",
                  "[20,50)" = "#08519c", "[50,100)" = "#08306b", "[100,200]" = "#041d3a")

# 构建组合图
header_y <- max_y + 1.2

p_combo <- p_tree +
  # Panel B: 亚家族竖线
  geom_segment(data = subfam_ranges, inherit.aes = FALSE,
               aes(x = x_subfam, xend = x_subfam, y = ymin, yend = ymax, colour = subfamily),
               linewidth = 2, show.legend = FALSE) +
  geom_text(data = subfam_ranges, inherit.aes = FALSE,
            aes(x = x_subfam + 0.1, y = ymid, label = subfamily, colour = subfamily),
            hjust = 0, size = 3, show.legend = FALSE) +
  
  # Panel C: 7大分类堆叠条形图
  ggnewscale::new_scale_fill() +
  geom_rect(data = category_data, inherit.aes = FALSE,
            aes(xmin = xmin, xmax = xmax, ymin = ymin, ymax = ymax, fill = big7), color = "white", size = 0.2) +
  scale_fill_manual(values = big7_colors, name = "Category", drop = FALSE) +
  
  # Panel D: 热图（正方形方格+实线边框）
  ggnewscale::new_scale_fill() +
  # 先画白色背景（0值）
  geom_rect(data = heatmap_data, inherit.aes = FALSE,
            aes(xmin = xmin, xmax = xmax, ymin = ymin, ymax = ymax), 
            fill = "white", color = "grey80", size = 0.3) +
  # 再画有值的方格
  geom_rect(data = heatmap_data %>% filter(count > 0), inherit.aes = FALSE,
            aes(xmin = xmin, xmax = xmax, ymin = ymin, ymax = ymax, fill = count_cat), 
            color = "grey60", size = 0.3) +
  geom_text(data = heatmap_data %>% filter(count > 0), inherit.aes = FALSE,
            aes(x = (xmin + xmax) / 2, y = (ymin + ymax) / 2, label = count),
            size = 2, color = "white") +
  scale_fill_manual(values = count_colors, name = "Count", drop = FALSE) +
  
  # 标题
  annotate("text", x = (x_A_left + x_A_right) / 2, y = header_y, label = "A", fontface = "bold", size = 5) +
  annotate("text", x = x_subfam, y = header_y, label = "B", fontface = "bold", size = 5) +
  annotate("text", x = (x_C_left + x_C_right) / 2, y = header_y, label = "C", fontface = "bold", size = 5) +
  annotate("text", x = (x_D_left + x_D_right) / 2, y = header_y, label = "D", fontface = "bold", size = 5) +
  
  coord_cartesian(xlim = c(x_A_left - 0.3, x_D_right + 0.5)) +
  expand_limits(y = header_y + 0.3) +
  theme(legend.position = "right", legend.background = element_rect(color = "#969696", fill = "#d9d9d9"))

# 保存（画布根据内容自动调整）
output_pdf <- paste0(output_prefix, "_v6_combined.pdf")
output_png <- paste0(output_prefix, "_v6_combined.png")

# 计算合适的画布尺寸
total_width <- (x_D_right - x_A_left) + 3  # 加上图例空间
total_height <- max_y + 2

# 限制最大尺寸
max_width <- 30
max_height <- 20
final_width <- min(total_width, max_width)
final_height <- min(total_height, max_height)

ggsave(output_pdf, p_combo, width = final_width, height = final_height, limitsize = FALSE)
png(filename = output_png, width = final_width * 300, height = final_height * 300, res = 300)
print(p_combo)
dev.off()

cat("[OK] PDF: ", output_pdf, "\n")
cat("[OK] PNG: ", output_png, "\n")
cat("===== 完成 =====\n")
