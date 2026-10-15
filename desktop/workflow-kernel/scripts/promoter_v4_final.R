#!/usr/bin/env Rscript
# V4 Final - 使用外部进化树图 + 红绿配色

rm(list = ls())
library(tidyverse)
library(ggtree)
library(treeio)
library(ape)
library(patchwork)
library(aplot)
library(RColorBrewer)
library(grid)
library(png)

cat("=== 启动子可视化 V4 Final ===\n\n")

args <- commandArgs(trailingOnly = TRUE)
tree_pdf <- args[1]
group_file <- args[2]
promoter_file <- args[3]
output_prefix <- args[5]

tree_nwk <- sub("_promoter_phylogenetic_tree\\.pdf$", ".tree.nwk", tree_pdf)
if(!file.exists(tree_nwk)) tree_nwk <- sub("\\.pdf$", ".nwk", tree_pdf)

cat("树PDF:", tree_pdf, "\n")
cat("树NWK:", tree_nwk, "\n\n")

# 读取数据
tree <- read.newick(tree_nwk)
group_df <- read.delim(group_file, header = FALSE)
colnames(group_df) <- c("label", "Subgroup")
group_df$label_base <- group_df$label %>% str_remove("\\.\\d+\\.T\\d+$") %>% str_remove("\\.\\d+$")

promoter <- read.delim(promoter_file, header = FALSE)
promoter <- promoter[, c(1, 2)]
colnames(promoter) <- c("gene", "element")

tree_labels <- tree$tip.label
tree_labels_base <- tree_labels %>% str_remove("\\.\\d+\\.T\\d+$") %>% str_remove("\\.\\d+$")

promoter$gene <- promoter$gene %>% str_trim()
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

big7_colors <- c("Core promoter"="#e41a1c", "Light response"="#377eb8",
                 "Hormone signaling"="#4daf4a", "Stress response"="#984ea3",
                 "TF binding"="#ff7f00", "Development"="#f781bf", "Other"="#999999")

# 保存函数
save_plot <- function(p, name, w, h) {
  ggsave(paste0(name, ".pdf"), p, width=w, height=h, device=cairo_pdf)
  cat("  PDF:", name, ".pdf\n")
  png(paste0(name, ".png"), width=w*100, height=h*100, res=100)
  print(p)
  dev.off()
  cat("  PNG:", name, ".png\n")
}

####----[1/4] 三合一组合图----####
cat("[1/4] 三合一组合图...\n")

# 读取外部树图
temp_png <- paste0(output_prefix, "_tree_temp.png")
system(paste("pdftoppm", tree_pdf, "-png -singlefile", temp_png))

tree_grob <- NULL
if(file.exists(paste0(temp_png, ".png"))) {
  tree_img <- readPNG(paste0(temp_png, ".png"))
  tree_grob <- rasterGrob(tree_img, interpolate=TRUE)
  file.remove(paste0(temp_png, ".png"))
}

p_tree <- if(!is.null(tree_grob)) {
  ggplot() + annotation_custom(tree_grob) + theme_void()
} else {
  ggtree(tree) + theme_tree2()
}

# 结构图
structure_data <- promoter %>%
  group_by(gene, big7) %>% summarise(count=n(), .groups="drop") %>%
  complete(gene=tree_labels_base, big7, fill=list(count=0)) %>%
  group_by(gene) %>% mutate(prop=count/sum(count)) %>% ungroup() %>%
  mutate(gene=factor(gene, levels=rev(tree_labels_base)))

p_structure <- ggplot(structure_data, aes(x=prop, y=gene, fill=big7)) +
  geom_bar(stat="identity", position="stack", width=0.8) +
  scale_fill_manual(values=big7_colors, name="Category") +
  labs(x="Proportion", y=NULL) +
  theme_bw() + theme(axis.text.y=element_blank(), axis.ticks.y=element_blank(), panel.grid=element_blank())

# 热图 - 红绿配色
top_elem <- promoter %>% group_by(element) %>% summarise(n=n(), .groups="drop") %>%
  arrange(desc(n)) %>% head(25) %>% pull(element)

heatmap_data <- promoter %>% filter(element %in% top_elem) %>%
  group_by(gene, element) %>% summarise(count=n(), .groups="drop") %>%
  complete(gene=tree_labels_base, element, fill=list(count=0)) %>%
  mutate(gene=factor(gene, levels=rev(tree_labels_base)))

# 舒服的红绿配色: RdYlGn
p_heatmap <- ggplot(heatmap_data, aes(x=element, y=gene, fill=count)) +
  geom_tile(color="white", size=0.2) +
  scale_fill_gradientn(colors=c("#d73027","#fc8d59","#fee08b","#d9ef8b","#91cf60","#1a9850"), name="Count") +
  labs(x="Element", y=NULL) +
  theme_bw() + 
  theme(axis.text.x=element_text(angle=90, hjust=1, size=7), axis.text.y=element_blank(),
        axis.ticks.y=element_blank(), panel.grid=element_blank())

# 组合
p_combined <- (p_tree + theme(legend.position="none")) | 
              (p_structure + theme(legend.position="none")) | 
              p_heatmap +
              plot_layout(widths=c(2, 1.5, 3))

save_plot(p_combined, paste0(output_prefix, "_01_combined"), 24, 14)
cat("  ✓ 完成\n\n")

####----[2/4] 7大分类细分----####
cat("[2/4] 7大分类细分...\n")

big7_stats <- promoter %>% group_by(big7, element) %>% summarise(count=n(), .groups="drop") %>%
  group_by(big7) %>% arrange(desc(count)) %>% slice_head(n=10) %>% ungroup()

p_big7 <- ggplot(big7_stats, aes(x=reorder(element, count), y=count, fill=big7)) +
  geom_bar(stat="identity", width=0.7) +
  scale_fill_manual(values=big7_colors) +
  facet_wrap(~big7, scales="free", ncol=3) + coord_flip() +
  labs(x="Element", y="Count", title="Top 10 Elements by Category") +
  theme_bw() + theme(axis.text.y=element_text(size=7), strip.background=element_rect(fill="lightblue"))

save_plot(p_big7, paste0(output_prefix, "_02_big7_detailed"), 16, 12)
cat("  ✓ 完成\n\n")

####----[3/4] 全热图----####
cat("[3/4] 全热图...\n")

heatmap_full <- promoter %>% group_by(gene, element) %>% summarise(count=n(), .groups="drop") %>%
  complete(gene=tree_labels_base, element, fill=list(count=0)) %>%
  mutate(gene=factor(gene, levels=rev(tree_labels_base)))

p_heatmap_full <- ggplot(heatmap_full, aes(x=element, y=gene, fill=count)) +
  geom_tile(color="white", size=0.1) +
  geom_text(aes(label=ifelse(count>0, count, "")), size=2) +
  scale_fill_gradientn(colors=c("#d73027","#fc8d59","#fee08b","#d9ef8b","#91cf60","#1a9850"), name="Count") +
  labs(x="Element", y="Gene") +
  theme_bw() + theme(axis.text.x=element_text(angle=90, hjust=1, size=6), axis.text.y=element_text(size=8))

save_plot(p_heatmap_full, paste0(output_prefix, "_03_heatmap_full"), 18, 12)
cat("  ✓ 完成\n\n")

####----[4/4] 堆叠图----####
cat("[4/4] 堆叠图...\n")

cat_props <- promoter %>% group_by(gene, big7) %>% summarise(count=n(), .groups="drop") %>%
  complete(gene=tree_labels_base, big7, fill=list(count=0)) %>%
  group_by(gene) %>% mutate(prop=count/sum(count)) %>% ungroup() %>%
  mutate(gene=factor(gene, levels=rev(tree_labels_base)))

p_stacked <- ggplot(cat_props, aes(x=gene, y=prop, fill=big7)) +
  geom_bar(stat="identity", position="stack", width=0.8) +
  scale_fill_manual(values=big7_colors, name="Category") +
  coord_flip() +
  labs(x="Gene", y="Proportion") +
  theme_bw() + theme(axis.text.y=element_text(size=7))

save_plot(p_stacked, paste0(output_prefix, "_04_stacked"), 12, 10)
cat("  ✓ 完成\n\n")

cat("===== 全部完成 =====\n")
