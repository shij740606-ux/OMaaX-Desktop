#!/usr/bin/env Rscript
# 绘制种间共线性图 (使用ggplot2)
# 输入：collinearity文件, bed文件
# 输出：PDF和PNG
# 布局：Ath染色体在上排，Ft染色体在下排，连线连接对应位置

suppressPackageStartupMessages({
  library(ggplot2)
  library(dplyr)
  library(stringr)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 4) {
  stop("用法: Rscript plot_interspecies_synteny.R <collinearity> <ref_bed> <target_bed> <output_prefix>", call. = FALSE)
}

collinearity_file <- args[1]
ref_bed <- args[2]
target_bed <- args[3]
output_prefix <- args[4]

# 读取BED文件
read_bed <- function(bed_file) {
  bed <- read.table(bed_file, sep="\t", stringsAsFactors=FALSE,
                    col.names=c("chr", "start", "end", "gene"))
  bed$mid <- (bed$start + bed$end) / 2
  return(bed)
}

# 读取collinearity文件
read_collinearity <- function(col_file) {
  lines <- readLines(col_file)
  blocks <- list()
  current_block <- NULL
  
  for (line in lines) {
    if (grepl("^## Alignment", line)) {
      m <- str_match(line, "Alignment\\s+(\\d+):.*N=(\\d+)\\s+(\\w+)&(\\w+)")
      if (!is.na(m[1])) {
        if (!is.null(current_block) && nrow(current_block$genes) > 0) {
          blocks[[length(blocks) + 1]] <- current_block
        }
        current_block <- list(
          id = as.integer(m[2]),
          n = as.integer(m[3]),
          ref_chr = m[4],
          target_chr = m[5],
          genes = data.frame(ref_gene=character(), target_gene=character(), stringsAsFactors=FALSE)
        )
      }
    } else if (grepl("^\\s*\\d+-\\s+\\d+:", line) && !is.null(current_block)) {
      parts <- str_split(line, "\\t")[[1]]
      if (length(parts) >= 3) {
        ref_gene <- str_trim(parts[2])
        target_gene <- str_trim(parts[3])
        current_block$genes <- rbind(current_block$genes, 
                                     data.frame(ref_gene=ref_gene, target_gene=target_gene, 
                                                stringsAsFactors=FALSE))
      }
    }
  }
  
  if (!is.null(current_block) && nrow(current_block$genes) > 0) {
    blocks[[length(blocks) + 1]] <- current_block
  }
  
  return(blocks)
}

# 主程序
cat("读取BED文件...\n")
ref_data <- read_bed(ref_bed)
target_data <- read_bed(target_bed)

cat("  Ath基因:", nrow(ref_data), "\n")
cat("  Ft基因:", nrow(target_data), "\n")

cat("读取collinearity文件...\n")
blocks <- read_collinearity(collinearity_file)
cat(sprintf("  共%d个共线性块\n", length(blocks)))

if (length(blocks) == 0) {
  cat("警告：没有共线性块，无法绘图\n")
  quit(save="no", status=1)
}

# 准备染色体数据 - 按物种分组，同一物种的染色体水平排列
ref_chr <- ref_data %>%
  group_by(chr) %>%
  summarise(len = max(end), .groups = "drop") %>%
  arrange(chr) %>%
  mutate(chr_num = row_number())

target_chr <- target_data %>%
  group_by(chr) %>%
  summarise(len = max(end), .groups = "drop") %>%
  arrange(chr) %>%
  mutate(chr_num = row_number())

# 计算统一的缩放比例
max_chr_len <- max(c(ref_chr$len, target_chr$len))

# 计算染色体在图中的位置
# Ath在上排 (y=2), Ft在下排 (y=1)
chr_width_pct <- 80 / max(nrow(ref_chr), nrow(target_chr))  # 每条染色体占总宽度的百分比
chr_spacing <- 2  # 染色体之间的间距

# Ath染色体位置 (上排, y=2)
ref_chr <- ref_chr %>%
  mutate(
    x_start = (chr_num - 1) * (100 / nrow(ref_chr)) + 5,
    x_end = x_start + (len / max_chr_len) * (80 / nrow(ref_chr)),
    y = 2  # 上排
  )

# Ft染色体位置 (下排, y=1)  
target_chr <- target_chr %>%
  mutate(
    x_start = (chr_num - 1) * (100 / nrow(target_chr)) + 5,
    x_end = x_start + (len / max_chr_len) * (80 / nrow(target_chr)),
    y = 1  # 下排
  )

# 准备连线数据
links <- data.frame()
for (block in blocks) {
  block_links <- block$genes %>%
    left_join(ref_data %>% select(gene, ref_chr=chr, ref_pos=mid), by=c("ref_gene"="gene")) %>%
    left_join(target_data %>% select(gene, target_chr=chr, target_pos=mid), by=c("target_gene"="gene")) %>%
    left_join(ref_chr %>% select(chr, ref_x_start=x_start, ref_x_end=x_end, ref_len=len), by=c("ref_chr"="chr")) %>%
    left_join(target_chr %>% select(chr, target_x_start=x_start, target_x_end=x_end, target_len=len), by=c("target_chr"="chr")) %>%
    filter(!is.na(ref_pos), !is.na(target_pos))
  
  if (nrow(block_links) > 0) {
    # 计算在图中的实际x坐标
    block_links <- block_links %>%
      mutate(
        x1 = ref_x_start + (ref_pos / ref_len) * (ref_x_end - ref_x_start),
        x2 = target_x_start + (target_pos / target_len) * (target_x_end - target_x_start)
      )
    links <- rbind(links, block_links)
  }
}

cat(sprintf("  共%d条连线\n", nrow(links)))

if (nrow(links) == 0) {
  cat("警告：没有有效连线，无法绘图\n")
  quit(save="no", status=1)
}

# 创建染色体矩形数据
chr_rects <- rbind(
  ref_chr %>% select(chr, x_start, x_end, y) %>% mutate(species="Ath"),
  target_chr %>% select(chr, x_start, x_end, y) %>% mutate(species="Ft")
)

# 绘图
cat("绘制共线性图...\n")

p <- ggplot() +
  # 绘制染色体矩形
  geom_rect(data=chr_rects, 
            aes(xmin=x_start, xmax=x_end, ymin=y-0.15, ymax=y+0.15, fill=species),
            color="black", linewidth=0.3) +
  # 绘制连线 (连接上排和下排)
  geom_segment(data=links,
               aes(x=x1, y=2-0.15, xend=x2, yend=1+0.15),
               alpha=0.15, color="steelblue", linewidth=0.1) +
  # 染色体标签
  geom_text(data=chr_rects, aes(x=(x_start+x_end)/2, y=y, label=chr), 
            vjust=-1.5, size=2.5) +
  # 物种标签
  annotate("text", x=2, y=2, label="Ath", hjust=1, size=4, fontface="bold", color="#E69F00") +
  annotate("text", x=2, y=1, label="Ft", hjust=1, size=4, fontface="bold", color="#56B4E9") +
  scale_fill_manual(values=c("Ath"="#E69F00", "Ft"="#56B4E9")) +
  coord_cartesian(xlim=c(-5, 105), ylim=c(0.5, 2.5), clip="off") +
  labs(title="Interspecies Synteny: Ft vs Ath") +
  theme_minimal() +
  theme(
    panel.grid = element_blank(),
    axis.text = element_blank(),
    axis.title = element_blank(),
    plot.title = element_text(hjust=0.5, face="bold", size=14),
    legend.position = "none"
  )

# 保存
output_pdf <- paste0(output_prefix, ".pdf")
output_png <- paste0(output_prefix, ".png")

ggsave(output_pdf, p, width=14, height=8)
cat(sprintf("PDF: %s\n", output_pdf))

png(filename=output_png, width=4200, height=2400, res=300)
print(p)
dev.off()
cat(sprintf("PNG: %s\n", output_png))

cat("完成\n")
