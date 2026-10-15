#!/usr/bin/env Rscript
# 启动子分析增强可视化脚本
# 包含8种可视化方式
# 适配testpy311环境

rm(list = ls())

####----加载R包----####
cat("=== 加载R包 ===\n")

# 检查并加载必需包
required_packages <- c("tidyverse", "ggtree", "treeio", "ape", 
                       "patchwork", "aplot", "RColorBrewer", "ggnewscale")

for(pkg in required_packages) {
  if(!require(pkg, character.only = TRUE, quietly = TRUE)) {
    cat("错误: 包", pkg, "未安装\n")
    cat("请在testpy311环境中安装: conda install r-", tolower(pkg), "\n", sep="")
    quit(save = "no", status = 1)
  }
}

# 加载可选包
optional_packages <- c("UpSetR", "circlize", "ComplexHeatmap")
for(pkg in optional_packages) {
  if(require(pkg, character.only = TRUE, quietly = TRUE)) {
    cat("✓ 已加载:", pkg, "\n")
  } else {
    cat("⚠ 可选包未安装:", pkg, "\n")
  }
}

cat("✓ 所有必需包加载完成\n\n")

####----命令行参数----####
args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 3) {
  cat("
启动子分析增强可视化

用法:
  Rscript promoter_visualization_enhanced.R [树文件] [分组文件] [启动子文件] [元件描述文件] [输出前缀]

可视化类型:
  1. 系统发育树 + 亚家族标注
  2. 元件数量热图
  3. 组合图（树+热图）
  4. 元件分布条形图
  5. 类别堆叠条形图
  6. 启动子结构示意图（基因箭头+元件位置）
  7. UpSet图（元件共现）
  8. 环形图（元件类别分布）

")
  quit(save = "no", status = 0)
}

tree_file <- args[1]
group_file <- args[2]
promoter_file <- args[3]
element_desc_file <- ifelse(length(args) >= 4, args[4], NA)
output_prefix <- ifelse(length(args) >= 5, args[5], "promoter_analysis")

cat("=== 启动子增强可视化 ===\n")
cat("树文件:", tree_file, "\n")
cat("分组文件:", group_file, "\n")
cat("启动子文件:", promoter_file, "\n")
cat("输出前缀:", output_prefix, "\n\n")

####----读取数据----####
# 1. 读取树
tree <- treeio::read.newick(tree_file)

# 2. 读取分组
group_df <- read.delim(group_file, header = FALSE, stringsAsFactors = FALSE)
if(ncol(group_df) >= 2) {
  colnames(group_df) <- c("label", "Subgroup")
}

# 清理基因ID（保留基础ID用于匹配）
group_df$label <- group_df$label %>%
  str_trim()

# 创建基础ID列用于匹配
group_df$label_base <- group_df$label %>%
  str_remove("\\.\\d+\\.T\\d+$") %>%
  str_remove("\\.T\\d+$") %>%
  str_remove("\\.\\d+$")

# 3. 读取启动子数据
promoter <- read.delim(promoter_file, header = FALSE, stringsAsFactors = FALSE)
if(ncol(promoter) >= 8) {
  promoter <- promoter[, c(1, 2, 4, 5, 6, 8)]
  colnames(promoter) <- c("gene", "element", "position", "length", "strand", "description")
}

# 启动子数据基因ID（已经是基础ID）
promoter$gene <- promoter$gene %>% str_trim()

# 4. 读取元件描述（如果有）
if(!is.na(element_desc_file) && file.exists(element_desc_file)) {
  element_desc <- read.csv(element_desc_file, stringsAsFactors = FALSE)
  # 添加分类
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
  
  # 合并分类到promoter数据
  promoter <- promoter %>%
    left_join(element_desc %>% select(element, category), by = "element")
} else {
  promoter$category <- "Other"
}

# 只保留树中存在的基因
tree_labels <- tree$tip.label

# 创建树标签的基础ID
tree_labels_base <- tree_labels %>%
  str_remove("\\.\\d+\\.T\\d+$") %>%
  str_remove("\\.T\\d+$") %>%
  str_remove("\\.\\d+$")

# 使用基础ID进行匹配
group_df <- group_df %>% filter(label_base %in% tree_labels_base)
promoter <- promoter %>% filter(gene %in% tree_labels_base)

# 更新树标签为匹配格式
names(tree_labels_base) <- tree_labels

cat("数据读取完成:\n")
cat("  树中基因数:", length(tree_labels), "\n")
cat("  分组基因数:", nrow(group_df), "\n")
cat("  启动子记录数:", nrow(promoter), "\n")
cat("  唯一元件数:", length(unique(promoter$element)), "\n\n")

####----可视化1: 系统发育树 + 亚家族----####
cat("[1/8] 绘制系统发育树...\n")

# 准备亚家族颜色
subfam_colors <- brewer.pal(n = length(unique(group_df$Subgroup)), name = "Dark2")
names(subfam_colors) <- unique(group_df$Subgroup)

# 绘制树
# 创建分组数据框，使用原始树标签
group_df_for_tree <- group_df %>%
  mutate(label = label_base) %>%
  select(-label_base)

p_tree <- ggtree(tree) %<+% group_df_for_tree +
  geom_tippoint(aes(color = Subgroup), size = 3) +
  geom_tiplab(aes(color = Subgroup), size = 2.5, offset = 0.005) +
  scale_color_manual(values = subfam_colors, name = "Subfamily") +
  theme_tree2() +
  theme(legend.position = "right")

# 保存
ggsave(paste0(output_prefix, "_01_tree.pdf"), p_tree, width = 10, height = 8, device = cairo_pdf)
tryCatch({
  ggsave(paste0(output_prefix, "_01_tree.png"), p_tree, width = 10, height = 8, dpi = 150, type = "cairo")
}, error = function(e) {
  cat("  警告: PNG保存失败，仅PDF可用\n")
})
cat("  已保存: _01_tree\n")

####----可视化2: 元件数量热图----####
cat("[2/8] 绘制元件热图...\n")

# 准备热图数据（使用基础ID）
heatmap_data <- promoter %>%
  group_by(gene, element) %>%
  summarise(count = n(), .groups = "drop") %>%
  complete(gene = tree_labels_base, element, fill = list(count = 0)) %>%
  mutate(gene = factor(gene, levels = rev(tree_labels_base)))

# 绘制热图
p_heatmap <- ggplot(heatmap_data, aes(x = element, y = gene, fill = count)) +
  geom_tile(color = "white", size = 0.1) +
  scale_fill_gradient(low = "white", high = "#08519c", name = "Count") +
  labs(x = "Cis-acting Element", y = "Gene") +
  theme_bw() +
  theme(
    axis.text.x = element_text(angle = 90, hjust = 1, vjust = 0.5, size = 6),
    axis.text.y = element_text(size = 7),
    panel.grid = element_blank()
  )

ggsave(paste0(output_prefix, "_02_heatmap.pdf"), p_heatmap, width = 16, height = 10, device = cairo_pdf)
cat("  已保存: _02_heatmap\n")

####----可视化3: 组合图（树+热图）----####
cat("[3/8] 绘制组合图...\n")

# 简化热图（无y轴标签）
p_heatmap_simple <- p_heatmap + 
  theme(axis.text.y = element_blank(), axis.title.y = element_blank())

# 组合
p_combined <- p_heatmap_simple %>% insert_left(p_tree, width = 0.4)

ggsave(paste0(output_prefix, "_03_combined.pdf"), p_combined, width = 18, height = 10, device = cairo_pdf)
cat("  已保存: _03_combined\n")

####----可视化4: 元件分布条形图----####
cat("[4/8] 绘制元件分布条形图...\n")

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

ggsave(paste0(output_prefix, "_04_element_barplot.pdf"), p_bar, width = 10, height = 8, device = cairo_pdf)
cat("  已保存: _04_element_barplot\n")

####----可视化5: 类别堆叠条形图----####
cat("[5/8] 绘制类别堆叠条形图...\n")

category_stats <- promoter %>%
  group_by(gene, category) %>%
  summarise(count = n(), .groups = "drop") %>%
  complete(gene = tree_labels, category, fill = list(count = 0))

# 计算每个基因的类别比例
category_props <- category_stats %>%
  group_by(gene) %>%
  mutate(prop = count / sum(count)) %>%
  ungroup() %>%
  mutate(gene = factor(gene, levels = rev(tree_labels)))

# 定义类别颜色
category_colors <- c(
  "Core promoter" = "#e41a1c",
  "Light response" = "#377eb8",
  "ABA signaling" = "#4daf4a",
  "GA signaling" = "#984ea3",
  "JA signaling" = "#ff7f00",
  "SA signaling" = "#ffff33",
  "Auxin response" = "#a65628",
  "Ethylene response" = "#f781bf",
  "Stress response" = "#999999",
  "MYB-related" = "#66c2a5",
  "MYC-related" = "#fc8d62",
  "WRKY-related" = "#8da0cb",
  "Development" = "#e78ac3",
  "Cell cycle" = "#a6d854",
  "Other" = "#ffd92f"
)

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

ggsave(paste0(output_prefix, "_05_category_stacked.pdf"), p_stacked, width = 12, height = 10, device = cairo_pdf)
cat("  已保存: _05_category_stacked\n")

####----可视化6: 启动子结构示意图----####
cat("[6/8] 绘制启动子结构示意图...\n")

# 选择Top 5最复杂的基因进行展示
top_genes <- promoter %>%
  group_by(gene) %>%
  summarise(n_elements = n(), .groups = "drop") %>%
  arrange(desc(n_elements)) %>%
  head(5) %>%
  pull(gene)

# 准备结构数据
structure_data <- promoter %>%
  filter(gene %in% top_genes) %>%
  mutate(
    # 将位置转换为相对于ATG的负坐标
    start = -2000 + position - length,
    end = -2000 + position
  ) %>%
  select(gene, element, start, end, category) %>%
  mutate(
    gene = factor(gene, levels = top_genes),
    category = factor(category, levels = names(category_colors))
  )

# 绘制结构图
p_structure <- ggplot(structure_data) +
  # 绘制启动子区域背景
  geom_rect(aes(xmin = -2000, xmax = 0, ymin = as.numeric(gene) - 0.3, 
                ymax = as.numeric(gene) + 0.3), 
            fill = "grey95", color = "grey70") +
  # 绘制元件
  geom_rect(aes(xmin = start, xmax = end, ymin = as.numeric(gene) - 0.25, 
                ymax = as.numeric(gene) + 0.25, fill = category),
            color = "black", size = 0.2) +
  scale_fill_manual(values = category_colors, name = "Category") +
  scale_y_continuous(breaks = 1:length(top_genes), labels = top_genes) +
  labs(x = "Position relative to ATG (bp)", y = "Gene",
       title = "Promoter Structure (Top 5 Most Complex)") +
  theme_bw() +
  theme(
    plot.title = element_text(size = 14, face = "bold"),
    panel.grid.minor = element_blank()
  )

ggsave(paste0(output_prefix, "_06_promoter_structure.pdf"), p_structure, width = 14, height = 6, device = cairo_pdf)
cat("  已保存: _06_promoter_structure\n")

####----可视化7: UpSet图（元件共现）----####
cat("[7/8] 绘制UpSet图...\n")

# 准备UpSet数据
upset_data <- promoter %>%
  group_by(gene, element) %>%
  summarise(present = 1, .groups = "drop") %>%
  pivot_wider(names_from = element, values_from = present, values_fill = 0)

# 选择Top 20元件进行UpSet分析
top_elements <- promoter %>%
  group_by(element) %>%
  summarise(count = n(), .groups = "drop") %>%
  arrange(desc(count)) %>%
  head(20) %>%
  pull(element)

upset_subset <- upset_data %>%
  select(gene, all_of(top_elements)) %>%
  as.data.frame()

# 保存UpSet数据
write.csv(upset_subset, paste0(output_prefix, "_07_upset_data.csv"), row.names = FALSE)

# 如果UpSetR可用，绘制UpSet图
if("UpSetR" %in% installed.packages()[,"Package"]) {
  tryCatch({
    p_upset <- upset(upset_subset, 
                     sets = top_elements,
                     order.by = "freq",
                     nsets = 20,
                     nintersects = 40,
                     main.bar.color = "steelblue",
                     sets.bar.color = "darkred",
                     matrix.color = "gray20")
    
    pdf(paste0(output_prefix, "_07_upset.pdf"), width = 14, height = 10)
    print(p_upset)
    dev.off()
    
    png(paste0(output_prefix, "_07_upset.png"), width = 1400, height = 1000, res = 100)
    print(p_upset)
    dev.off()
    
    cat("  已保存: _07_upset\n")
  }, error = function(e) {
    cat("  UpSet图绘制失败:", e$message, "\n")
  })
} else {
  cat("  UpSetR包未安装，跳过UpSet图\n")
}

####----可视化8: 环形图（类别分布）----####
cat("[8/8] 绘制环形图...\n")

# 计算类别总数
category_totals <- promoter %>%
  group_by(category) %>%
  summarise(count = n(), .groups = "drop") %>%
  arrange(desc(count))

# 绘制环形图
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

ggsave(paste0(output_prefix, "_08_category_donut.pdf"), p_donut, width = 10, height = 8, device = cairo_pdf)
cat("  已保存: _08_category_donut\n")

####----完成----####
cat("\n===== 所有可视化完成 =====\n")
cat("生成的文件:\n")
for(i in 1:8) {
  pdf_file <- paste0(output_prefix, "_0", i, "_*.pdf")
  cat(sprintf("%d. %s\n", i, pdf_file))
}