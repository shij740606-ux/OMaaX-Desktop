#!/usr/bin/env Rscript
# 启动子分析增强可视化脚本 V3
# 修订内容:
# 1. 七大分类顺序修正,Other放最后,分类内细分元件
# 2. 组合图:现有进化树PDF + category_stacked结构 + 红绿渐变热图
# 3. big7柱状图细分元件类别

rm(list = ls())

cat("=== 加载R包 ===\n")
required_packages <- c("tidyverse", "ggtree", "treeio", "ape", 
                       "patchwork", "aplot", "RColorBrewer", "ggnewscale")

for(pkg in required_packages) {
  if(!require(pkg, character.only = TRUE, quietly = TRUE)) {
    cat("错误: 包", pkg, "未安装\n")
    quit(save = "no", status = 1)
  }
}

cat("✓ 所有包加载完成\n\n")

####----命令行参数----####
args <- commandArgs(trailingOnly = TRUE)

tree_pdf_file <- args[1]
group_file <- args[2]
promoter_file <- args[3]
element_desc_file <- ifelse(length(args) >= 4, args[4], NA)
output_prefix <- ifelse(length(args) >= 5, args[5], "promoter_analysis")

tree_nwk_file <- sub("_promoter_phylogenetic_tree\\.pdf$", ".tree.nwk", tree_pdf_file)
if(!file.exists(tree_nwk_file)) {
  tree_nwk_file <- sub("\\.pdf$", ".nwk", tree_pdf_file)
}

cat("=== 启动子增强可视化 V3 ===\n")
cat("进化树PDF:", tree_pdf_file, "\n")
cat("进化树NWK:", tree_nwk_file, "\n\n")

####----读取数据----####
tree <- treeio::read.newick(tree_nwk_file)

group_df <- read.delim(group_file, header = FALSE, stringsAsFactors = FALSE)
if(ncol(group_df) >= 2) colnames(group_df) <- c("label", "Subgroup")
group_df$label <- group_df$label %>% str_trim()
group_df$label_base <- group_df$label %>%
  str_remove("\\.\\d+\\.T\\d+$") %>%
  str_remove("\\.T\\d+$") %>%
  str_remove("\\.\\d+$")

promoter <- read.delim(promoter_file, header = FALSE, stringsAsFactors = FALSE)
if(ncol(promoter) >= 8) {
  promoter <- promoter[, c(1, 2, 4, 5, 6, 8)]
  colnames(promoter) <- c("gene", "element", "position", "length", "strand", "description")
}
promoter$gene <- promoter$gene %>% str_trim()

# 定义7大分类和顺序(Other放最后)
big7_order <- c("Core promoter", "Light response", "Hormone signaling", 
                "Stress response", "TF binding", "Development", "Other")

# 元件分类函数
categorize_element <- function(x) {
  if(grepl("TATA|CAAT|Inr|Y-patch", x, ignore.case = TRUE)) return("Core promoter")
  if(grepl("ABRE|GA|JA|SA|Auxin|Ethylene|hormone", x, ignore.case = TRUE)) return("Hormone signaling")
  if(grepl("G-box|GT1|I-box|Sp1|Box.4|GATA|light", x, ignore.case = TRUE)) return("Light response")
  if(grepl("DRE|LTR|ARE|STRE|W.box|W-box|stress|drought|cold", x, ignore.case = TRUE)) return("Stress response")
  if(grepl("MYB|MYC|WRKY|TF|binding", x, ignore.case = TRUE)) return("TF binding")
  if(grepl("RY|AAGAA|CAT|GCN4|Development|seed|meristem", x, ignore.case = TRUE)) return("Development")
  return("Other")
}

if(!is.na(element_desc_file) && file.exists(element_desc_file)) {
  element_desc <- read.csv(element_desc_file, stringsAsFactors = FALSE)
  element_desc$big7 <- sapply(element_desc$element, categorize_element)
  element_desc$big7 <- factor(element_desc$big7, levels = big7_order)
  promoter <- promoter %>%
    left_join(element_desc %>% select(element, big7), by = "element")
} else {
  promoter$big7 <- sapply(promoter$element, categorize_element)
  promoter$big7 <- factor(promoter$big7, levels = big7_order)
}

tree_labels <- tree$tip.label
tree_labels_base <- tree_labels %>%
  str_remove("\\.\\d+\\.T\\d+$") %>%
  str_remove("\\.T\\d+$") %>%
  str_remove("\\.\\d+$")

group_df <- group_df %>% filter(label_base %in% tree_labels_base)
promoter <- promoter %>% filter(gene %in% tree_labels_base)

cat("数据读取完成: 树", length(tree_labels), "基因, 启动子", nrow(promoter), "记录\n\n")

# 保存函数
save_plot <- function(plot, filename_base, width, height) {
  pdf_file <- paste0(filename_base, ".pdf")
  png_file <- paste0(filename_base, ".png")
  ggsave(pdf_file, plot, width = width, height = height, device = cairo_pdf)
  cat("  PDF:", pdf_file, "\n")
  tryCatch({
    png(filename = png_file, width = width * 100, height = height * 100, res = 100)
    print(plot)
    dev.off()
    cat("  PNG:", png_file, "\n")
  }, error = function(e) {
    cat("  PNG生成失败\n")
  })
}

####----可视化1: 三合一组合图----####
cat("[1/4] 绘制三合一组合图...\n")

# 1A. 尝试读取已有进化树图(如果magick可用)
p_tree_external <- NULL
if(require("magick", quietly = TRUE)) {
  tryCatch({
    tree_img <- image_read(tree_pdf_file)
    tree_grob <- rasterGrob(tree_img, interpolate = TRUE)
    p_tree_external <- ggplot() + 
      annotation_custom(tree_grob, xmin = -Inf, xmax = Inf, ymin = -Inf, ymax = Inf) +
      theme_void()
    cat("  ✓ 已读取外部进化树图\n")
  }, error = function(e) {
    cat("  ⚠ 读取外部树图失败,将使用ggtree绘制\n")
  })
} else {
  cat("  ℹ magick包未安装,将使用ggtree绘制树\n")
}

# 如果外部图读取失败,使用ggtree
if(is.null(p_tree_external)) {
  group_df_for_tree <- group_df %>% mutate(label = label_base)
  subfam_colors <- brewer.pal(max(3, length(unique(group_df$Subgroup))), "Dark2")
  subfam_colors <- subfam_colors[1:length(unique(group_df$Subgroup))]
  names(subfam_colors) <- unique(group_df$Subgroup)
  
  p_tree_external <- ggtree(tree) %<+% group_df_for_tree +
    geom_tippoint(aes(color = Subgroup), size = 3) +
    scale_color_manual(values = subfam_colors, name = "Subfamily") +
    theme_tree2() +
    theme(legend.position = "none")
}

# 1B. 结构图: category_stacked样式(显示7大分类)
structure_data <- promoter %>%
  group_by(gene, big7) %>%
  summarise(count = n(), .groups = "drop") %>%
  complete(gene = tree_labels_base, big7, fill = list(count = 0)) %>%
  group_by(gene) %>%
  mutate(prop = count / sum(count)) %>%
  ungroup() %>%
  mutate(gene = factor(gene, levels = rev(tree_labels_base)))

big7_colors <- c(
  "Core promoter" = "#e41a1c",
  "Light response" = "#377eb8",
  "Hormone signaling" = "#4daf4a",
  "Stress response" = "#984ea3",
  "TF binding" = "#ff7f00",
  "Development" = "#f781bf",
  "Other" = "#999999"
)

p_structure <- ggplot(structure_data, aes(x = prop, y = gene, fill = big7)) +
  geom_bar(stat = "identity", position = "stack", width = 0.8) +
  scale_fill_manual(values = big7_colors, name = "Category") +
  labs(x = "Proportion", y = NULL) +
  theme_bw() +
  theme(
    axis.text.y = element_blank(),
    axis.ticks.y = element_blank(),
    panel.grid = element_blank()
  )

# 1C. 热图: 红绿渐变,小方格
top_elements <- promoter %>%
  group_by(element) %>%
  summarise(count = n(), .groups = "drop") %>%
  arrange(desc(count)) %>%
  head(25) %>%
  pull(element)

heatmap_subset <- promoter %>%
  filter(element %in% top_elements) %>%
  group_by(gene, element) %>%
  summarise(count = n(), .groups = "drop") %>%
  complete(gene = tree_labels_base, element, fill = list(count = 0)) %>%
  mutate(gene = factor(gene, levels = rev(tree_labels_base)))

p_heatmap_rg <- ggplot(heatmap_subset, aes(x = element, y = gene, fill = count)) +
  geom_tile(color = "white", size = 0.2) +
  scale_fill_gradient2(low = "green", mid = "black", high = "red", 
                       midpoint = max(heatmap_subset$count)/2,
                       name = "Count") +
  labs(x = "Element", y = NULL) +
  theme_bw() +
  theme(
    axis.text.x = element_text(angle = 90, hjust = 1, vjust = 0.5, size = 7),
    axis.text.y = element_blank(),
    axis.ticks.y = element_blank(),
    panel.grid = element_blank()
  )

# 1D. 组合(图例放最右)
p_combined <- (p_tree_external + theme(legend.position = "none")) | 
              (p_structure + theme(legend.position = "none")) | 
              (p_heatmap_rg + theme(legend.position = "none")) |
              guide_area()

p_combined <- p_combined + plot_layout(widths = c(2, 1.5, 2.5, 0.5), guides = "collect")

save_plot(p_combined, paste0(output_prefix, "_01_combined"), 24, 14)
cat("  ✓ 三合一组合图完成\n\n")

####----可视化2: 7大分类细分柱状图----####
cat("[2/4] 绘制7大分类细分柱状图...\n")

# 统计每个大分类内的元件
big7_element_stats <- promoter %>%
  group_by(big7, element) %>%
  summarise(count = n(), .groups = "drop") %>%
  group_by(big7) %>%
  arrange(desc(count)) %>%
  mutate(element_rank = row_number()) %>%
  ungroup()

# 每个大分类取Top 10元件
top_elements_per_big7 <- big7_element_stats %>%
  group_by(big7) %>%
  slice_max(order_by = count, n = 10) %>%
  ungroup()

# 绘制细分柱状图(每个大分类单独排序)
p_big7_detailed <- ggplot(top_elements_per_big7, 
                          aes(x = reorder(element, count), y = count, fill = big7)) +
  geom_bar(stat = "identity", width = 0.7) +
  scale_fill_manual(values = big7_colors, name = "Category") +
  facet_wrap(~big7, scales = "free", ncol = 3) +
  coord_flip() +
  labs(x = "Element", y = "Count", title = "Top 10 Elements in Each of 7 Major Categories") +
  theme_bw() +
  theme(
    plot.title = element_text(size = 14, face = "bold"),
    axis.text.y = element_text(size = 7),
    strip.background = element_rect(fill = "lightblue"),
    strip.text = element_text(face = "bold")
  )

save_plot(p_big7_detailed, paste0(output_prefix, "_02_big7_detailed"), 16, 12)
cat("  ✓ 7大分类细分柱状图完成\n\n")

####----可视化3: 元件热图(红绿渐变)----####
cat("[3/4] 绘制红绿渐变热图...\n")

heatmap_full <- promoter %>%
  group_by(gene, element) %>%
  summarise(count = n(), .groups = "drop") %>%
  complete(gene = tree_labels_base, element, fill = list(count = 0)) %>%
  mutate(gene = factor(gene, levels = rev(tree_labels_base)))

p_heatmap_full <- ggplot(heatmap_full, aes(x = element, y = gene, fill = count)) +
  geom_tile(color = "white", size = 0.1) +
  geom_text(aes(label = ifelse(count > 0, count, "")), size = 2, color = "white") +
  scale_fill_gradient2(low = "darkgreen", mid = "black", high = "red",
                       midpoint = median(heatmap_full$count[heatmap_full$count > 0]),
                       name = "Count") +
  labs(x = "Cis-acting Element", y = "Gene") +
  theme_bw() +
  theme(
    axis.text.x = element_text(angle = 90, hjust = 1, vjust = 0.5, size = 6),
    axis.text.y = element_text(size = 8),
    panel.grid = element_blank()
  )

save_plot(p_heatmap_full, paste0(output_prefix, "_03_heatmap_rg"), 18, 12)
cat("  ✓ 红绿渐变热图完成\n\n")

####----可视化4: 类别堆叠图----####
cat("[4/4] 绘制类别堆叠图...\n")

category_props <- promoter %>%
  group_by(gene, big7) %>%
  summarise(count = n(), .groups = "drop") %>%
  complete(gene = tree_labels_base, big7, fill = list(count = 0)) %>%
  group_by(gene) %>%
  mutate(prop = count / sum(count)) %>%
  ungroup() %>%
  mutate(gene = factor(gene, levels = rev(tree_labels_base)))

p_stacked <- ggplot(category_props, aes(x = gene, y = prop, fill = big7)) +
  geom_bar(stat = "identity", position = "stack", width = 0.8) +
  scale_fill_manual(values = big7_colors, name = "Category") +
  coord_flip() +
  labs(x = "Gene", y = "Proportion", title = "7 Major Categories Distribution by Gene") +
  theme_bw() +
  theme(
    plot.title = element_text(size = 14, face = "bold"),
    axis.text.y = element_text(size = 7),
    legend.position = "right"
  )

save_plot(p_stacked, paste0(output_prefix, "_04_stacked"), 12, 10)
cat("  ✓ 类别堆叠图完成\n\n")

####----完成----####
cat("===== 所有可视化完成 =====\n")
cat("输出文件:\n")
for(i in 1:4) {
  cat(sprintf("%d. %s_0%d_*.pdf/png\n", i, output_prefix, i))
}
