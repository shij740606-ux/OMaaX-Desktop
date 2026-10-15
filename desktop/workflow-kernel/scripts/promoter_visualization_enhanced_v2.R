#!/usr/bin/env Rscript
# 启动子分析增强可视化脚本 V2
# 修订内容:
# 1. 所有图添加PNG格式
# 2. 修复category_stacked图的基因ID显示
# 3. 三合一组合图(树+结构+热图)
# 4. 使用已有进化树图

rm(list = ls())

####----加载R包----####
cat("=== 加载R包 ===\n")

required_packages <- c("tidyverse", "ggtree", "treeio", "ape", 
                       "patchwork", "aplot", "RColorBrewer", "ggnewscale")

for(pkg in required_packages) {
  if(!require(pkg, character.only = TRUE, quietly = TRUE)) {
    cat("错误: 包", pkg, "未安装\n")
    quit(save = "no", status = 1)
  }
}

optional_packages <- c("UpSetR", "circlize", "ComplexHeatmap")
for(pkg in optional_packages) {
  if(require(pkg, character.only = TRUE, quietly = TRUE)) {
    cat("✓ 已加载:", pkg, "\n")
  }
}

cat("✓ 所有必需包加载完成\n\n")

####----命令行参数----####
args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 3) {
  cat("
启动子分析增强可视化 V2

用法:
  Rscript promoter_visualization_enhanced_v2.R [已有进化树PDF] [分组文件] [启动子文件] [元件描述文件] [输出前缀]

可视化类型:
  1. 元件数量热图 (PNG+PDF)
  2. 三合一组合图: 进化树+结构图+热图 (PNG+PDF)
  3. 元件分布条形图 (PNG+PDF)
  4. 类别堆叠条形图 (PNG+PDF, 修复基因ID显示)
  5. 启动子结构示意图 (PNG+PDF)
  6. UpSet图 (PNG+PDF)
  7. 环形图 (PNG+PDF)

")
  quit(save = "no", status = 0)
}

tree_pdf_file <- args[1]  # 已有进化树PDF
group_file <- args[2]
promoter_file <- args[3]
element_desc_file <- ifelse(length(args) >= 4, args[4], NA)
output_prefix <- ifelse(length(args) >= 5, args[5], "promoter_analysis")

# 从PDF路径推断nwk文件路径
tree_nwk_file <- sub("_promoter_phylogenetic_tree\\.pdf$", ".tree.nwk", tree_pdf_file)
if(!file.exists(tree_nwk_file)) {
  tree_nwk_file <- sub("\\.pdf$", ".nwk", tree_pdf_file)
}

cat("=== 启动子增强可视化 V2 ===\n")
cat("进化树PDF:", tree_pdf_file, "\n")
cat("进化树NWK:", tree_nwk_file, "\n")
cat("分组文件:", group_file, "\n")
cat("启动子文件:", promoter_file, "\n")
cat("输出前缀:", output_prefix, "\n\n")

normalize_gene <- function(x) {
  x %>%
    stringr::str_trim() %>%
    stringr::str_remove("\\.\\d+\\.T\\d+$") %>%
    stringr::str_remove("\\.T\\d+$") %>%
    stringr::str_remove("\\.\\d+$")
}

####----读取数据----####
# 1. 读取树(用于数据匹配)
tree <- treeio::read.newick(tree_nwk_file)

# 2. 读取分组
group_df <- read.delim(group_file, header = FALSE, stringsAsFactors = FALSE)
if(ncol(group_df) >= 2) {
  colnames(group_df) <- c("label", "Subgroup")
}

# 清理基因ID
group_df$label <- group_df$label %>% str_trim()
group_df$label_base <- normalize_gene(group_df$label)

# 3. 读取启动子数据
promoter <- read.delim(promoter_file, header = FALSE, stringsAsFactors = FALSE)
if(ncol(promoter) >= 8) {
  promoter <- promoter[, c(1, 2, 4, 5, 6, 8)]
  colnames(promoter) <- c("gene", "element", "position", "length", "strand", "description")
}
promoter$gene_raw <- promoter$gene %>% str_trim()
promoter$gene <- normalize_gene(promoter$gene_raw)

# 4. 读取元件描述
if(!is.na(element_desc_file) && file.exists(element_desc_file)) {
  element_desc <- read.csv(element_desc_file, stringsAsFactors = FALSE)
  element_desc$category <- sapply(element_desc$element, function(x) {
    if(grepl("TATA|CAAT|Inr|Y-patch", x, ignore.case = TRUE)) return("Core promoter")
    if(grepl("ABRE", x, ignore.case = TRUE)) return("ABA signaling")
    if(grepl("P-box|GARE", x, ignore.case = TRUE)) return("GA signaling")
    if(grepl("CGTCA|TGACG", x, ignore.case = TRUE)) return("JA signaling")
    if(grepl("TCA|as-1", x, ignore.case = TRUE)) return("SA signaling")
    if(grepl("TGA|AuxRE", x, ignore.case = TRUE)) return("Auxin response")
    if(grepl("ERE", x, ignore.case = TRUE)) return("Ethylene response")
    if(grepl("DRE|LTR|ARE|STRE|W.box|W-box", x, ignore.case = TRUE)) return("Stress response")
    if(grepl("^MYB$|MYB-like|MRE", x, ignore.case = TRUE)) return("MYB-related")
    if(grepl("^MYC$|E-box", x, ignore.case = TRUE)) return("MYC-related")
    if(grepl("WRKY", x, ignore.case = TRUE)) return("WRKY-related")
    if(grepl("G-box|GT1|I-box|Sp1|Box.4|GATA", x, ignore.case = TRUE)) return("Light response")
    if(grepl("RY|AAGAA|CAT|GCN4", x, ignore.case = TRUE)) return("Development")
    return("Other")
  })
  promoter <- promoter %>%
    left_join(element_desc %>% select(element, category), by = "element")
} else {
  promoter$category <- "Other"
}

# 匹配基因
tree_labels <- tree$tip.label
tree_labels_base <- normalize_gene(tree_labels)

group_df <- group_df %>% filter(label_base %in% tree_labels_base)
promoter <- promoter %>% filter(gene %in% tree_labels_base)

cat("数据读取完成:\n")
cat("  树中基因数:", length(tree_labels), "\n")
cat("  分组基因数:", nrow(group_df), "\n")
cat("  启动子记录数:", nrow(promoter), "\n")
cat("  唯一元件数:", length(unique(promoter$element)), "\n\n")

if (nrow(promoter) == 0) {
  stop("No promoter records matched tree labels after ID normalization; skip legacy promoter plots.")
}

# 定义保存函数(同时输出PDF和PNG)
save_plot <- function(plot, filename_base, width, height) {
  pdf_file <- paste0(filename_base, ".pdf")
  png_file <- paste0(filename_base, ".png")
  
  # PDF
  ggsave(pdf_file, plot, width = width, height = height, device = cairo_pdf)
  cat("  PDF:", pdf_file, "\n")
  
  # PNG (使用agg设备避免API版本问题)
  tryCatch({
    png(filename = png_file, width = width * 100, height = height * 100, res = 100)
    print(plot)
    dev.off()
    cat("  PNG:", png_file, "\n")
  }, error = function(e) {
    cat("  PNG生成失败,仅PDF可用\n")
  })
}

####----可视化1: 元件数量热图----####
cat("[1/7] 绘制元件热图...\n")

heatmap_data <- promoter %>%
  group_by(gene, element) %>%
  summarise(count = n(), .groups = "drop") %>%
  complete(gene = tree_labels_base, element, fill = list(count = 0)) %>%
  mutate(gene = factor(gene, levels = rev(tree_labels_base)))

p_heatmap <- ggplot(heatmap_data, aes(x = element, y = gene, fill = count)) +
  geom_tile(color = "white", size = 0.1) +
  geom_text(aes(label = ifelse(count > 0, count, "")), size = 2, color = "black") +
  scale_fill_gradient(low = "white", high = "#08519c", name = "Count") +
  labs(x = "Cis-acting Element", y = "Gene") +
  theme_bw() +
  theme(
    axis.text.x = element_text(angle = 90, hjust = 1, vjust = 0.5, size = 6),
    axis.text.y = element_text(size = 8),
    panel.grid = element_blank()
  )

save_plot(p_heatmap, paste0(output_prefix, "_01_heatmap"), 16, 10)
cat("  ✓ 热图完成\n\n")

####----可视化2: 三合一组合图----####
cat("[2/7] 绘制三合一组合图(树+结构+热图)...\n")

# 定义7大分类颜色
big7_colors <- c(
  "Core promoter" = "#e41a1c",
  "Light response" = "#377eb8", 
  "Hormone signaling" = "#4daf4a",
  "Stress response" = "#984ea3",
  "TF binding" = "#ff7f00",
  "Development" = "#f781bf",
  "Other" = "#999999"
)

# 将细分类合并为7大分类
promoter$big7_category <- sapply(promoter$category, function(x) {
  if(x == "Core promoter") return("Core promoter")
  if(x == "Light response") return("Light response")
  if(x %in% c("ABA signaling", "GA signaling", "JA signaling", "SA signaling", 
              "Auxin response", "Ethylene response")) return("Hormone signaling")
  if(x == "Stress response") return("Stress response")
  if(x %in% c("MYB-related", "MYC-related", "WRKY-related")) return("TF binding")
  if(x %in% c("Development", "Cell cycle")) return("Development")
  return("Other")
})

# 2A. 读取已有进化树图(使用pdftools或magick)
# 如果没有这些包，使用ggtree重新绘制
tree_plot_ready <- FALSE
if(require("magick", quietly = TRUE)) {
  tryCatch({
    tree_img <- image_read(tree_pdf_file)
    tree_img <- image_convert(tree_img, "png")
    tree_plot_ready <- TRUE
    cat("  ✓ 已读取现有进化树图\n")
  }, error = function(e) {
    cat("  ⚠ 无法读取PDF,将重新绘制树\n")
  })
}

# 准备亚家族颜色
subfam_colors <- brewer.pal(n = max(3, length(unique(group_df$Subgroup))), name = "Dark2")
subfam_colors <- subfam_colors[1:length(unique(group_df$Subgroup))]
names(subfam_colors) <- unique(group_df$Subgroup)

# 重新绘制树(用于组合)
group_df_for_tree <- group_df %>% mutate(label = label_base)
p_tree <- ggtree(tree) %<+% group_df_for_tree +
  geom_tippoint(aes(color = Subgroup), size = 3) +
  scale_color_manual(values = subfam_colors, name = "Subfamily") +
  theme_tree2() +
  theme(legend.position = "none")

# 2B. 启动子结构图(所有基因)
structure_data_all <- promoter %>%
  mutate(
    start = -2000 + position - length,
    end = -2000 + position,
    gene = factor(gene, levels = rev(tree_labels_base))
  )

# 只显示有元件的位置(简化显示)
structure_summary <- structure_data_all %>%
  group_by(gene, big7_category) %>%
  summarise(count = n(), .groups = "drop") %>%
  complete(gene = tree_labels_base, big7_category, fill = list(count = 0)) %>%
  mutate(gene = factor(gene, levels = rev(tree_labels_base)))

p_structure_all <- ggplot(structure_summary, 
                          aes(x = big7_category, y = gene, fill = count)) +
  geom_tile(color = "white", size = 0.1) +
  scale_fill_gradient(low = "white", high = "#08519c", name = "Count") +
  labs(x = "Category", y = NULL) +
  theme_bw() +
  theme(
    axis.text.x = element_text(angle = 45, hjust = 1, size = 8),
    axis.text.y = element_text(size = 7),
    panel.grid = element_blank()
  )

# 2C. 简化热图(所有基因,只显示Top 20元件)
top20_elements <- promoter %>%
  group_by(element) %>%
  summarise(count = n(), .groups = "drop") %>%
  arrange(desc(count)) %>%
  head(20) %>%
  pull(element)

heatmap_top20 <- heatmap_data %>%
  filter(element %in% top20_elements) %>%
  mutate(gene = factor(gene, levels = rev(tree_labels_base)))

p_heatmap_top20 <- ggplot(heatmap_top20, aes(x = element, y = gene, fill = count)) +
  geom_tile(color = "white", size = 0.1) +
  geom_text(aes(label = ifelse(count > 0, count, "")), size = 2, color = "black") +
  scale_fill_gradient(low = "white", high = "#08519c", name = "Count") +
  labs(x = "Element", y = NULL) +
  theme_bw() +
  theme(
    axis.text.x = element_text(angle = 90, hjust = 1, vjust = 0.5, size = 6),
    axis.text.y = element_blank(),
    axis.ticks.y = element_blank(),
    panel.grid = element_blank()
  )

# 2D. 三合一组合(图例放最右边)
p_combined <- (p_tree + theme(legend.position = "none")) | 
              (p_structure_all + theme(legend.position = "none")) | 
              p_heatmap_top20 |
              guide_area()

p_combined <- p_combined + 
  plot_layout(widths = c(1.5, 1, 2.5, 0.5), guides = "collect")

save_plot(p_combined, paste0(output_prefix, "_02_combined"), 22, 12)
cat("  ✓ 三合一组合图完成\n\n")

####----可视化3: 元件分布条形图----####
cat("[3/7] 绘制元件分布条形图...\n")

element_stats <- promoter %>%
  group_by(element) %>%
  summarise(count = n(), .groups = "drop") %>%
  arrange(desc(count)) %>%
  head(30)

p_bar <- ggplot(element_stats, aes(x = reorder(element, count), y = count)) +
  geom_bar(stat = "identity", fill = "steelblue") +
  coord_flip() +
  labs(x = "Cis-acting Element", y = "Count", 
       title = "Top 30 Most Abundant Cis-acting Elements") +
  theme_bw() +
  theme(
    plot.title = element_text(size = 14, face = "bold"),
    axis.text = element_text(size = 9)
  )

save_plot(p_bar, paste0(output_prefix, "_03_element_barplot"), 10, 8)
cat("  ✓ 元件条形图完成\n\n")

####----可视化3b: 7大分类截断柱状图----####
cat("[3b/7] 绘制7大分类截断柱状图...\n")

# 统计7大分类
big7_stats <- promoter %>%
  filter(!is.na(big7_category), big7_category != "") %>%
  group_by(big7_category) %>%
  summarise(count = n(), .groups = "drop") %>%
  arrange(desc(count))

if (nrow(big7_stats) == 0) {
  cat("  ⚠ 7大分类柱状图跳过: 无可用 big7 分类数据\n\n")
} else {
plot_break_bar <- function(data, x_col, y_col, break_start = 500, break_end = 2000) {
  max_val <- max(data[[y_col]])
  
  p <- ggplot(data, aes_string(x = x_col, y = y_col, fill = x_col)) +
    geom_bar(stat = "identity", width = 0.7) +
    scale_fill_manual(values = big7_colors, name = "Category") +
    labs(x = "Category", y = "Count", 
         title = "Cis-acting Element Count by 7 Major Categories") +
    theme_bw() +
    theme(
      plot.title = element_text(size = 14, face = "bold"),
      axis.text.x = element_text(angle = 45, hjust = 1, size = 10),
      axis.text.y = element_text(size = 9),
      legend.position = "none"
    )
  
  # 如果需要截断(最大值超过break_end)
  if(max_val > break_end) {
    p <- p + 
      coord_cartesian(ylim = c(0, break_start + (max_val - break_end) + 100)) +
      scale_y_break(c(break_start, max_val - break_end), scales = 0.3)
  }
  
  return(p)
}

# 尝试使用ggbreak包，如果没有则使用普通图
if(require("ggbreak", quietly = TRUE)) {
  p_big7 <- plot_break_bar(big7_stats, "big7_category", "count", 500, 2000)
} else {
  # 普通柱状图+对数坐标
  p_big7 <- ggplot(big7_stats, aes(x = reorder(big7_category, count), y = count, fill = big7_category)) +
    geom_bar(stat = "identity", width = 0.7) +
    geom_text(aes(label = count), vjust = -0.5, size = 4) +
    scale_fill_manual(values = big7_colors, name = "Category") +
    scale_y_log10() +  # 对数坐标减小视觉差距
    labs(x = "Category", y = "Count (log scale)", 
         title = "Cis-acting Element Count by 7 Major Categories") +
    theme_bw() +
    theme(
      plot.title = element_text(size = 14, face = "bold"),
      axis.text.x = element_text(angle = 45, hjust = 1, size = 10),
      legend.position = "none"
    )
}

save_plot(p_big7, paste0(output_prefix, "_03b_big7_barplot"), 10, 8)
cat("  ✓ 7大分类柱状图完成\n\n")
}

####----可视化4: 类别堆叠条形图(修复基因ID)----####
cat("[4/7] 绘制类别堆叠条形图...\n")

# 定义15分类颜色
category_colors <- c(
  "Core promoter" = "#e41a1c", "Light response" = "#377eb8",
  "ABA signaling" = "#4daf4a", "GA signaling" = "#984ea3",
  "JA signaling" = "#ff7f00", "SA signaling" = "#ffff33",
  "Auxin response" = "#a65628", "Ethylene response" = "#f781bf",
  "Stress response" = "#999999", "MYB-related" = "#66c2a5",
  "MYC-related" = "#fc8d62", "WRKY-related" = "#8da0cb",
  "Development" = "#e78ac3", "Cell cycle" = "#a6d854", "Other" = "#ffd92f"
)

category_stats <- promoter %>%
  group_by(gene, category) %>%
  summarise(count = n(), .groups = "drop") %>%
  complete(gene = tree_labels_base, category, fill = list(count = 0))

# 计算比例
category_props <- category_stats %>%
  group_by(gene) %>%
  mutate(prop = ifelse(sum(count) > 0, count / sum(count), 0)) %>%
  ungroup() %>%
  mutate(gene = factor(gene, levels = rev(tree_labels_base)))

p_stacked <- ggplot(category_props, aes(x = gene, y = prop, fill = category)) +
  geom_bar(stat = "identity", position = "stack", width = 0.8) +
  scale_fill_manual(values = category_colors, name = "Category") +
  coord_flip() +
  labs(x = "Gene", y = "Proportion", 
       title = "Cis-acting Element Category Distribution by Gene") +
  theme_bw() +
  theme(
    plot.title = element_text(size = 14, face = "bold"),
    axis.text.y = element_text(size = 7),
    legend.position = "right"
  )

save_plot(p_stacked, paste0(output_prefix, "_04_category_stacked"), 12, 10)
cat("  ✓ 类别堆叠图完成\n\n")

####----可视化5: UpSet图----####
cat("[5/7] 绘制UpSet图...\n")

upset_data <- promoter %>%
  group_by(gene, element) %>%
  summarise(present = 1, .groups = "drop") %>%
  pivot_wider(names_from = element, values_from = present, values_fill = 0)

top_elements <- promoter %>%
  group_by(element) %>%
  summarise(count = n(), .groups = "drop") %>%
  arrange(desc(count)) %>%
  head(20) %>%
  pull(element)

upset_subset <- upset_data %>%
  select(gene, all_of(top_elements)) %>%
  as.data.frame()

write.csv(upset_subset, paste0(output_prefix, "_05_upset_data.csv"), row.names = FALSE)

if("UpSetR" %in% installed.packages()[,"Package"]) {
  tryCatch({
    p_upset <- upset(upset_subset, sets = top_elements, order.by = "freq",
                     nsets = 20, nintersects = 40,
                     main.bar.color = "steelblue",
                     sets.bar.color = "darkred",
                     matrix.color = "gray20")
    
    pdf(paste0(output_prefix, "_05_upset.pdf"), width = 14, height = 10)
    print(p_upset)
    dev.off()
    
    png(paste0(output_prefix, "_05_upset.png"), width = 1400, height = 1000)
    print(p_upset)
    dev.off()
    
    cat("  ✓ UpSet图完成\n")
  }, error = function(e) {
    cat("  UpSet图失败:", e$message, "\n")
  })
} else {
  cat("  UpSetR未安装,跳过\n")
}
cat("\n")

####----可视化6: 环形图----####
cat("[6/7] 绘制环形图...\n")

category_totals <- promoter %>%
  group_by(category) %>%
  summarise(count = n(), .groups = "drop") %>%
  arrange(desc(count))

p_donut <- ggplot(category_totals, aes(x = 2, y = count, fill = category)) +
  geom_bar(stat = "identity", width = 1, color = "white") +
  coord_polar("y", start = 0) +
  xlim(0.5, 2.5) +
  scale_fill_manual(values = category_colors, name = "Category") +
  labs(title = "Cis-acting Element Category Distribution") +
  theme_void() +
  theme(
    plot.title = element_text(size = 14, face = "bold", hjust = 0.5),
    legend.position = "right"
  )

save_plot(p_donut, paste0(output_prefix, "_06_category_donut"), 10, 8)
cat("  ✓ 环形图完成\n\n")

####----完成----####
cat("===== 所有可视化完成 =====\n")
cat("输出文件:\n")
for(i in 1:6) {
  cat(sprintf("%d. %s_%02d_*.pdf/png\n", i, output_prefix, i))
}
