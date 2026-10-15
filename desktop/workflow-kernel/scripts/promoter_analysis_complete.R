#!/usr/bin/env Rscript
# 启动子元件与进化树组合绘图 - 修复short_desc列问题

# 清理环境
rm(list = ls())

####----加载R包----####
required_packages <- c("tidyverse", "readxl", "writexl", "ggh4x", 
                       "ggtree", "treeio", "ape", "patchwork", "aplot", "RColorBrewer", "ggnewscale")
missing_packages <- required_packages[!required_packages %in% installed.packages()[,"Package"]]
if(length(missing_packages) > 0) {
  cat("安装缺少的包:", paste(missing_packages, collapse = ", "), "\n")
  install.packages(missing_packages, repos = "https://cloud.r-project.org")
}

library(tidyverse)
library(readxl)
library(writexl)
library(ggh4x)
library(ggtree)
library(treeio)
library(ape)
library(patchwork)
library(aplot)
library(RColorBrewer)
library(ggnewscale)

####----函数定义：处理元件描述----####
create_short_descriptions <- function(cir_element.desc) {
  cir_element.desc %>%
    mutate(
      description = str_trim(description),
      description = str_replace_all(description, '""', '"'),
      
      short_desc = case_when(
        element == "Myb-binding site" ~ "Myb-site (RNR1b, cell cycle)",
        element == "MYB recognition site" ~ "MYB-site (ProDH, dehydration)",
        element == "CCGTCC motif" ~ "CCGTCC motif (H3, S-phase)",
        
        str_detect(description, "^cis-acting element involved in the abscisic acid") ~ "ABA responsive",
        str_detect(description, "^cis-acting regulatory element involved in light") ~ "Light responsive",
        str_detect(description, "^cis-acting element involved in low-temperature") ~ "Cold responsive",
        str_detect(description, "^cis-acting element involved in defense and stress") ~ "Stress responsive",
        str_detect(description, "^cis-acting regulatory element involved in seed-specific") ~ "Seed specific",
        str_detect(description, "^cis-acting element involved in gibberellin") ~ "GA responsive",
        str_detect(description, "^cis-acting element involved in salicylic acid") ~ "SA responsive",
        str_detect(description, "^cis-acting regulatory element involved in the MeJA") ~ "JA responsive",
        str_detect(description, "^cis-acting element involved in auxin") ~ "Auxin responsive",
        
        str_detect(description, "MYB binding site involved") ~ {
          extracted <- str_extract(description, "MYB binding site involved in .+? regulation")
          ifelse(is.na(extracted), description, str_replace(extracted, "MYB binding site involved in ", ""))
        },
        
        TRUE ~ {
          sapply(description, function(desc) {
            first_sent <- str_extract(desc, "^[^.]+\\.?")
            
            if (!is.na(first_sent) && nchar(first_sent) > 50) {
              words <- str_split(first_sent, " ")[[1]]
              if (length(words) >= 6) {
                paste(paste(words[1:6], collapse = " "), "...")
              } else {
                substr(first_sent, 1, 50)
              }
            } else if (is.na(first_sent)) {
              if (nchar(desc) > 40) {
                paste0(substr(desc, 1, 40), "...")
              } else {
                desc
              }
            } else {
              first_sent
            }
          })
        }
      ),
      
      short_desc = ifelse(nchar(short_desc) > 50, 
                         paste0(substr(short_desc, 1, 50), "..."), 
                         short_desc),
      
      # 基于元件名称的分类（优先）+ 基于description的分类（补充）
      category = case_when(
        # 1. 核心启动子元件 (最高优先级)
        str_detect(element, "(?i)TATA|CAAT|Inr|Y-patch") ~ "Core promoter",
        
        # 2. 激素响应元件
        str_detect(element, "(?i)ABRE") ~ "ABA signaling",
        str_detect(element, "(?i)P-box|Py-box|GARE") ~ "GA signaling",
        str_detect(element, "(?i)CGTCA|TGACG") ~ "JA signaling",
        str_detect(element, "(?i)TCA|as-1") ~ "SA signaling",
        str_detect(element, "(?i)TGA|AuxRE") ~ "Auxin response",
        str_detect(element, "(?i)ERE") ~ "Ethylene response",
        
        # 3. 胁迫响应元件
        str_detect(element, "(?i)DRE|CRT|LTR|ARE|STRE|WRE|W.box|W-box") ~ "Stress response",
        str_detect(element, "(?i)MBS") & str_detect(description, "(?i)drought|stress") ~ "Stress response",
        
        # 4. 转录因子结合位点
        str_detect(element, "(?i)^MYB$|^Myb$|MYB-like|MRE") ~ "MYB-related",
        str_detect(element, "(?i)^MYC$|E-box|CANNTG") ~ "MYC-related",
        str_detect(element, "(?i)WRKY") ~ "WRKY-related",
        
        # 5. 光响应元件
        str_detect(element, "(?i)G-box|G-Box|GT1|I-box|Sp1|Box.4|GATA") ~ "Light response",
        
        # 6. 发育调控元件
        str_detect(element, "(?i)RY|AAGAA|CAT|GCN4|Skn-1") ~ "Development",
        
        # 7. 基于description的补充分类
        str_detect(description, "(?i)abscisic|ABA") ~ "ABA signaling",
        str_detect(description, "(?i)gibberellin|GA") ~ "GA signaling",
        str_detect(description, "(?i)jasmonate|MeJA|JA") ~ "JA signaling",
        str_detect(description, "(?i)salicylic|SA") ~ "SA signaling",
        str_detect(description, "(?i)auxin|Aux") ~ "Auxin response",
        str_detect(description, "(?i)ethylene") ~ "Ethylene response",
        str_detect(description, "(?i)light|Light") ~ "Light response",
        str_detect(description, "(?i)drought|dehydration|low.temperature|stress|anaerobic|cold") ~ "Stress response",
        str_detect(description, "(?i)seed.specific|endosperm|seed") ~ "Development",
        str_detect(description, "(?i)meristem") ~ "Development",
        str_detect(description, "(?i)wound|pathogen|defense") ~ "Stress response",
        str_detect(description, "(?i)cell.cycle|DNA.synthesis|replication|cell.division") ~ "Cell cycle",
        
        # 8. 其他
        TRUE ~ "Other"
      )
    )
}

####----函数：智能读取分组文件----####
read_group_file_smart <- function(file_path) {
  cat("尝试读取分组文件:", file_path, "\n")
  
  # 读取第一行判断格式
  first_line <- readLines(file_path, n = 1)
  
  # 尝试不同的读取方式
  tryCatch({
    # 方式1: 制表符分隔（这是最可能的）
    df <- read.delim(file_path, header = TRUE, sep = "\t", stringsAsFactors = FALSE, 
                     fileEncoding = "UTF-8", quote = "")
    if (ncol(df) >= 2) {
      # 检查是否有需要的列
      if ("label" %in% colnames(df) && "Subgroup" %in% colnames(df)) {
        cat("成功以制表符读取，找到label和Subgroup列\n")
        return(df)
      } else {
        # 尝试自动重命名
        if (length(colnames(df)) >= 2) {
          colnames(df)[1:2] <- c("label", "Subgroup")
          cat("自动重命名列为: label, Subgroup\n")
          return(df)
        }
      }
    }
  }, error = function(e) {
    cat("制表符读取失败:", e$message, "\n")
  })
  
  tryCatch({
    # 方式2: 空格分隔
    df <- read.table(file_path, header = TRUE, stringsAsFactors = FALSE, 
                     fileEncoding = "UTF-8")
    if (ncol(df) >= 2) {
      # 检查是否有需要的列
      if ("label" %in% colnames(df) && "Subgroup" %in% colnames(df)) {
        cat("成功以空格读取，找到label和Subgroup列\n")
        return(df)
      } else {
        # 尝试自动重命名
        if (length(colnames(df)) >= 2) {
          colnames(df)[1:2] <- c("label", "Subgroup")
          cat("自动重命名列为: label, Subgroup\n")
          return(df)
        }
      }
    }
  }, error = function(e) {
    cat("空格读取失败:", e$message, "\n")
  })
  
  # 如果所有方法都失败，尝试读取原始内容并解析
  cat("尝试方式3: 读取原始内容并解析\n")
  lines <- readLines(file_path, encoding = "UTF-8")
  
  # 删除空行
  lines <- lines[lines != ""]
  
  # 解析每一行
  parsed_data <- lapply(lines, function(line) {
    # 尝试分割：先按制表符，再按多个空格
    parts <- str_split(line, "\t")[[1]]
    if (length(parts) < 2) {
      parts <- str_split(line, "\\s+")[[1]]
    }
    return(parts)
  })
  
  # 提取列名和数据
  col_names <- parsed_data[[1]]
  data_rows <- parsed_data[-1]
  
  # 创建数据框
  df <- as.data.frame(do.call(rbind, data_rows), stringsAsFactors = FALSE)
  colnames(df) <- col_names
  
  # 确保有两列
  if (ncol(df) >= 2) {
    # 重命名为标准列名
    if (ncol(df) == 2) {
      colnames(df) <- c("label", "Subgroup")
    } else {
      # 只取前两列
      df <- df[, 1:2]
      colnames(df) <- c("label", "Subgroup")
      cat("只取前两列作为label和Subgroup\n")
    }
    cat("成功解析文件\n")
    return(df)
  }
  
  stop("无法解析分组文件，请检查文件格式")
}

####----命令行参数处理----####
args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 1 || args[1] %in% c("-h", "--help")) {
  cat("
启动子元件与进化树组合绘图

用法:
  Rscript promoter_analysis.R [树文件] [分组文件] [启动子文件] [元件描述文件] [输出前缀]

示例:
  Rscript promoter_analysis.R tree.nwk groups.txt promoter.tab elements.csv my_analysis
  Rscript promoter_analysis.R tree.nwk groups.txt NA elements.csv my_analysis  # 无启动子数据

")
  quit(save = "no", status = 0)
}

# 解析命令行参数
cat("=== 解析命令行参数 ===\n")
cat("接收到的参数数量:", length(args), "\n")

# 设置默认值
tree_file <- NULL
group_file <- NULL
promoter_file <- NULL
element_desc_file <- NULL
output_prefix <- "output"

# 根据参数数量分配
if (length(args) >= 1) tree_file <- args[1]
if (length(args) >= 2) group_file <- args[2]
if (length(args) >= 3) promoter_file <- ifelse(args[3] == "NA", NULL, args[3])
if (length(args) >= 4) element_desc_file <- args[4]
if (length(args) >= 5) output_prefix <- args[5]

cat("参数设置:\n")
cat("  树文件:", ifelse(is.null(tree_file), "未指定", tree_file), "\n")
cat("  分组文件:", ifelse(is.null(group_file), "未指定", group_file), "\n")
cat("  启动子文件:", ifelse(is.null(promoter_file), "未指定", promoter_file), "\n")
cat("  元件描述文件:", ifelse(is.null(element_desc_file), "未指定", element_desc_file), "\n")
cat("  输出前缀:", output_prefix, "\n\n")

####----检查必需文件----####
# 检查树文件
if (is.null(tree_file) || !file.exists(tree_file)) {
  stop("错误: 树文件不存在或未指定。请提供有效的树文件路径。")
}

# 检查分组文件
if (is.null(group_file) || !file.exists(group_file)) {
  stop("错误: 分组文件不存在或未指定。请提供有效的分组文件路径。")
}

####----加载数据----####

# 1. 读取进化树文件
cat("=== 步骤1: 读取进化树文件 ===\n")
cat("读取文件:", tree_file, "\n")
tree <- treeio::read.newick(file = tree_file, node.label = "support")

# 清理基因ID
tree_clean <- as_tibble(tree) %>%
  dplyr::mutate(
    label = str_trim(label),
    label = str_remove(label, pattern = "\\.\\d+\\.T\\d+$"),
    label = str_remove(label, pattern = "\\.T\\d+$"),
    label = str_remove(label, pattern = "\\.\\d+$")
  ) %>%
  as.treedata()

# 2. 读取分组文件（使用智能读取函数）
cat("\n=== 步骤2: 读取分组文件 ===\n")
group_df <- read_group_file_smart(group_file)

# 清理分组文件中的基因ID
group_df <- group_df %>%
  mutate(
    label = str_trim(label),
    label = str_remove(label, pattern = "\\.\\d+\\.T\\d+$"),
    label = str_remove(label, pattern = "\\.T\\d+$"),
    label = str_remove(label, pattern = "\\.\\d+$"),
    Subgroup = str_trim(Subgroup)
  )

cat("分组统计:\n")
print(table(group_df$Subgroup))

# 3. 检查基因ID匹配
cat("\n=== 检查基因ID匹配 ===\n")
tree_labels <- tree_clean@phylo$tip.label
group_labels <- group_df$label

common_genes <- intersect(tree_labels, group_labels)
cat("树中基因数量:", length(tree_labels), "\n")
cat("分组文件中基因数量:", length(group_labels), "\n")
cat("匹配的基因数量:", length(common_genes), "\n")

if (length(common_genes) == 0) {
  stop("错误: 树和分组文件没有共同的基因ID！")
}

# 只保留匹配的基因
group_df <- group_df %>%
  filter(label %in% common_genes)

# 3. 读取启动子元件数据（如果有）
promoter_data_exists <- FALSE
if (!is.null(promoter_file) && file.exists(promoter_file)) {
  cat("\n=== 步骤3: 读取启动子元件数据 ===\n")
  cat("读取文件:", promoter_file, "\n")
  
  promoter <- tryCatch({
    data.table::fread(
      file = promoter_file,
      header = FALSE,
      sep = "\t",
      stringsAsFactors = FALSE
    )
  }, error = function(e) {
    cat("读取启动子文件时出错:", e$message, "\n")
    return(NULL)
  })
  
  if (!is.null(promoter) && ncol(promoter) >= 8) {
    promoter <- promoter %>%
      dplyr::select(1, 2, 8) %>%
      purrr::set_names("X1", "X2", "X3") %>%
      dplyr::filter(!str_detect(X2, pattern = "Unname")) %>%
      dplyr::filter(!is.na(X2)) %>%
      dplyr::filter(!str_detect(X3, "short_function")) %>%
      dplyr::mutate(
        X1 = str_trim(X1),
        X1 = str_remove(string = X1, pattern = "\\.\\d+\\.T\\d+$"),
        X1 = str_remove(string = X1, pattern = "\\.T\\d+$"),
        X1 = str_remove(string = X1, pattern = "\\.\\d+$")
      ) %>%
      filter(X1 %in% tree_labels)
    
    cat("启动子数据行数:", nrow(promoter), "\n")
    
    if (nrow(promoter) > 0) {
      promoter_data_exists <- TRUE
      cat("启动子数据可用\n")
    } else {
      cat("警告: 启动子数据中没有与树匹配的基因\n")
      promoter <- NULL
    }
  } else {
    cat("警告: 启动子文件列数不足或读取失败，跳过启动子分析\n")
    promoter <- NULL
  }
} else {
  cat("\n=== 步骤3: 无启动子文件，跳过启动子分析 ===\n")
  promoter <- NULL
}

# 4. 读取或生成元件描述文件
if (promoter_data_exists) {
  if (!is.null(element_desc_file) && file.exists(element_desc_file)) {
    cat("\n=== 步骤4: 读取元件描述文件 ===\n")
    cat("读取文件:", element_desc_file, "\n")
    
    # 根据文件扩展名选择读取方式
    file_ext <- tools::file_ext(element_desc_file)
    
    if (file_ext == "csv") {
      cir_element.desc <- read.csv(element_desc_file, stringsAsFactors = FALSE)
    } else if (file_ext %in% c("xls", "xlsx")) {
      cir_element.desc <- read_xlsx(element_desc_file)
    } else {
      cir_element.desc <- read.delim(element_desc_file, stringsAsFactors = FALSE)
    }
    
    # 检查列名
    if (!"element" %in% names(cir_element.desc)) {
      if (any(grepl("element|name|motif", names(cir_element.desc), ignore_case = TRUE))) {
        element_col <- names(cir_element.desc)[grepl("element|name|motif", names(cir_element.desc), ignore_case = TRUE)][1]
        names(cir_element.desc)[names(cir_element.desc) == element_col] <- "element"
      }
    }
    
    if (!"description" %in% names(cir_element.desc)) {
      if (any(grepl("description|desc|function", names(cir_element.desc), ignore_case = TRUE))) {
        desc_col <- names(cir_element.desc)[grepl("description|desc|function", names(cir_element.desc), ignore_case = TRUE)][1]
        names(cir_element.desc)[names(cir_element.desc) == desc_col] <- "description"
      }
    }
    
    # 处理描述，创建简短标签和分类
    cir_element.desc <- create_short_descriptions(cir_element.desc)
    
    # 保存处理后的元件描述
    write_xlsx(cir_element.desc, paste0(output_prefix, "_element_descriptions.xlsx"))
    cat("已保存处理后的元件描述到:", paste0(output_prefix, "_element_descriptions.xlsx"), "\n")
    
    # 合并描述信息到promoter数据
    promoter <- promoter %>%
      left_join(cir_element.desc, by = c("X2" = "element"))
    
    # 移除没有描述的行
    promoter <- promoter[!is.na(promoter$description), ]
    
    if (nrow(promoter) == 0) {
      cat("警告: 合并后没有数据，跳过启动子分析\n")
      promoter_data_exists <- FALSE
    } else {
      # 保存合并后的promoter数据用于调试
      write.csv(promoter, paste0(output_prefix, "_promoter_merged.csv"), row.names = FALSE)
      cat("已保存合并后的启动子数据用于调试\n")
    }
  } else {
    # 没有描述文件，创建简化版本
    cat("\n=== 步骤4: 无元件描述文件，使用简化模式 ===\n")
    cat("提示: 如需完整功能描述，请使用 scripts/tiqu_miaoshuwenjian.py 从PlantCARE数据库查询\n")
    
    # 从promoter数据中提取唯一元件列表
    unique_elements <- unique(promoter$X2)
    cat("发现", length(unique_elements), "个唯一元件\n")
    
    # 创建简化描述表
    cir_element.desc <- data.frame(
      element = unique_elements,
      description = unique_elements,
      short_desc = unique_elements,
      category = "Other",
      stringsAsFactors = FALSE
    )
    
    # 合并到promoter数据
    promoter <- promoter %>%
      left_join(cir_element.desc, by = c("X2" = "element"))
    
    # 保存简化描述
    write_xlsx(cir_element.desc, paste0(output_prefix, "_element_descriptions_simple.xlsx"))
    cat("已保存简化元件描述到:", paste0(output_prefix, "_element_descriptions_simple.xlsx"), "\n")
  }
} else if (promoter_data_exists) {
  cat("\n警告: 有启动子数据但无元件描述文件，无法进行功能分类\n")
  promoter_data_exists <- FALSE
}

####----绘制进化树----####
cat("\n=== 绘制进化树 ===\n")

# 确定亚家族数量和颜色
subgroups <- unique(group_df$Subgroup)

# 定义颜色方案
subgroup_colors <- c(
  "I" = "#66c2a5", "II" = "#fc8d62", "III" = "#8da0cb", "IV" = "#e78ac3",
  "V" = "#a6d854", "VI" = "#ffd92f", "VII" = "#e5c494", "VIII" = "#b3b3b3"
)

# 为存在的亚家族选择颜色
available_colors <- c()
for (sg in subgroups) {
  if (sg %in% names(subgroup_colors)) {
    available_colors[sg] <- subgroup_colors[sg]
  } else {
    available_colors[sg] <- "#999999"
  }
}

# 绘制进化树
p.tree <- ggtree(tree_clean, branch.length = "none") %<+% group_df +
  geom_tree(aes(color = Subgroup), size = 1.2) +
  scale_color_manual(values = available_colors, name = "Subfamily") +
  ggnewscale::new_scale_fill() +
  geom_nodepoint(
    aes(fill = cut(support, c(0, 50, 75, 100))),
    shape = 21, size = 3
  ) +
  scale_fill_manual(
    values = c("black", "grey", "white"),
    guide = "legend",
    name = "Bootstrap Percentage",
    breaks = c("(75,100]", "(50,75]", "(0,50]"),
    labels = c("BP > 75", "50 < BP ≤ 75", "BP ≤ 50")
  ) +
  geom_tiplab(offset = 0.3, size = 3) +
  geom_tippoint(aes(x = x + 0.2), shape = 21, fill = "#3690c0", size = 2) +
  theme_tree() +
  theme(
    legend.position = "right",
    legend.background = element_rect(color = "#808080", linetype = 1),
    legend.box = "vertical",
    legend.margin = margin(6, 6, 6, 6)
  ) +
  xlim(NA, 16)

# 保存单独的树图 (PDF + PNG)
tree_output_pdf <- paste0(output_prefix, "_phylogenetic_tree.pdf")
tree_output_png <- paste0(output_prefix, "_phylogenetic_tree.png")
ggsave(filename = tree_output_pdf, plot = p.tree, height = 10, width = 8)
# 使用png()函数直接生成PNG（ggsave在conda环境中有API版本冲突问题）
png(filename = tree_output_png, width = 1200, height = 1500, res = 150)
print(p.tree)
dev.off()
cat("已保存进化树:", tree_output_pdf, "\n")
cat("已保存进化树PNG:", tree_output_png, "\n")

####----绘制启动子热图（如果数据存在）----####
if (promoter_data_exists && nrow(promoter) > 0) {
  cat("\n=== 绘制启动子热图 ===\n")
  
  # 获取树中的基因顺序
  id_vector <- get_taxa_name(p.tree)
  
  # 筛选元件：移除出现次数太少的元件
  element_counts <- promoter %>%
    pull(X2) %>%
    table() %>%
    as_tibble() %>%
    purrr::set_names("element", "count") %>%
    dplyr::arrange(desc(count))
  
  cat("元件出现次数统计（前20）:\n")
  print(head(element_counts, 20))
  
  # 保留出现次数>=3的元件
  retain_elements <- element_counts %>%
    dplyr::filter(count >= 3) %>%
    pull(element)
  
  cat(sprintf("保留出现次数>=3的元件: %d个\n", length(retain_elements)))
  
  if (length(retain_elements) == 0) {
    cat("警告: 没有元件出现次数>=3，跳过热图绘制\n")
  } else {
    # 在每个功能类别中，保留出现次数最多的前5个元件
    promoter_summary <- promoter %>%
      dplyr::filter(X2 %in% retain_elements) %>%
      group_by(category, X2) %>%
      summarise(count = n(), .groups = 'drop')
    
    promoter_retained <- promoter_summary %>%
      group_by(category) %>%
      top_n(5, count) %>%
      pull(X2) %>%
      unique()
    
    cat(sprintf("最终保留元件: %d个\n", length(promoter_retained)))
    
    if (length(promoter_retained) > 0) {
      # 准备热图数据 - 关键修复部分
      cat("准备热图数据...\n")
      
      # 首先，从promoter数据中提取需要的信息
      promoter_filtered <- promoter %>%
        dplyr::filter(X2 %in% promoter_retained) %>%
        dplyr::select(X1, X2, category, short_desc) %>%
        mutate(
          X2 = factor(X2, levels = promoter_retained, ordered = TRUE)
        )
      
      # 创建所有基因-元件的组合
      all_combinations <- expand.grid(
        X1 = unique(c(promoter_filtered$X1, id_vector)),
        X2 = unique(promoter_filtered$X2),
        stringsAsFactors = FALSE
      ) %>%
        mutate(X2 = factor(X2, levels = promoter_retained, ordered = TRUE))
      
      # 汇总每个基因-元件的数量
      promoter_counts <- promoter_filtered %>%
        group_by(X1, X2, category, short_desc) %>%
        summarise(count = n(), .groups = 'drop')
      
      # 合并数据
      heatmap_data <- all_combinations %>%
        left_join(promoter_counts, by = c("X1", "X2")) %>%
        # 从cir_element.desc获取category和short_desc，避免列名冲突
        left_join(
          cir_element.desc %>% 
            select(element, category_desc = category, short_desc_element = short_desc),
          by = c("X2" = "element")
        ) %>%
        mutate(
          count = ifelse(is.na(count), 0, count),
          X1 = factor(X1, levels = rev(id_vector), ordered = TRUE),
          # 使用promoter数据中的category和short_desc，如果缺失则使用cir_element.desc中的
          category = ifelse(is.na(category), category_desc, category),
          short_desc = ifelse(is.na(short_desc), short_desc_element, short_desc),
          count_cat = cut(
            count,
            breaks = c(0, 1, 2, 3, 5, 10, 20, 50, 100, 200),
            include.lowest = TRUE,
            right = FALSE
          )
        ) %>%
        select(-category_desc, -short_desc_element)  # 删除临时列
      
      # 检查heatmap_data的列
      cat("heatmap_data列名:", colnames(heatmap_data), "\n")
      cat("short_desc列是否存在:", "short_desc" %in% colnames(heatmap_data), "\n")
      cat("category列是否存在:", "category" %in% colnames(heatmap_data), "\n")
      cat("heatmap_data行数:", nrow(heatmap_data), "\n")
      
      # 绘制热图
      cat("绘制热图...\n")
      
      p.promoter <- ggplot(heatmap_data, aes(x = interaction(X2, short_desc), y = X1)) +
        geom_tile(fill = "white", color = "grey80", size = 0.3) +
        geom_point(
          aes(fill = count_cat),
          shape = 22, size = 8
        ) +
        geom_text(aes(label = ifelse(count > 0, count, "")), size = 2.5) +
        scale_fill_manual(
          values = c(
            "[0,1)" = "#f7fbff",
            "[1,2)" = "#c6dbef",
            "[2,3)" = "#9ecae1",
            "[3,5)" = "#6baed6",
            "[5,10)" = "#4292c6",
            "[10,20)" = "#2171b5",
            "[20,50)" = "#08519c",
            "[50,100)" = "#08306b",
            "[100,200]" = "#041d3a"
          ),
          name = "元件数量",
          drop = FALSE
        ) +
        guides(x = "axis_nested") +
        labs(x = "", y = "") +
        theme_bw() +
        theme(
          axis.text.x = element_text(
            color = "black",
            angle = 90,
            size = 8,
            hjust = 1,
            vjust = 0.5
          ),
          axis.text.y = element_text(color = "black", size = 9),
          legend.position = "right",
          legend.background = element_rect(color = "#808080", linetype = 1),
          axis.ticks.length.x.top = unit(0.2, "cm"),
          axis.ticks.y = element_blank(),
          panel.border = element_blank(),
          panel.grid = element_blank(),
          plot.margin = unit(c(0.3, 0.3, 0.3, 0.3), "cm")
        ) +
        scale_x_discrete(expand = c(0, 0), position = "top") +
        guides(fill = guide_legend(ncol = 1))
      
      # 保存热图 (PDF + PNG) - 单独热图保留
      heatmap_output_pdf <- paste0(output_prefix, "_promoter_heatmap.pdf")
      heatmap_output_png <- paste0(output_prefix, "_promoter_heatmap.png")
      ggsave(filename = heatmap_output_pdf, plot = p.promoter, height = 12, width = 14)
      png(filename = heatmap_output_png, width = 2100, height = 1800, res = 150)
      print(p.promoter)
      dev.off()
      cat("已保存启动子热图:", heatmap_output_pdf, "\n")
      cat("已保存启动子热图PNG:", heatmap_output_png, "\n")
      
      ####----新版组合图：仿Motif风格（三面板布局）----####
      cat("\n=== 绘制新版组合图（仿Motif风格）===\n")
      
      # 准备亚家族颜色（与Motif一致使用Dark2）
      subfam_levels <- sort(unique(group_df$Subgroup))
      n_subfam <- length(subfam_levels)
      base_cols_sf <- brewer.pal(8, "Dark2")
      subfam_cols_vec <- rep_len(base_cols_sf, n_subfam)
      subfam_cols <- setNames(subfam_cols_vec, subfam_levels)
      
      # 从p.tree对象中提取树的坐标数据
      tree_dat <- p.tree$data
      tip_dat <- tree_dat %>%
        filter(isTip) %>%
        select(label, x, y) %>%
        left_join(group_df %>% select(label, Subgroup), by = "label")
      
      # 计算亚家族范围（Y轴范围）
      subfam_ranges <- tip_dat %>%
        filter(!is.na(Subgroup)) %>%
        group_by(Subgroup) %>%
        summarise(
          ymin = min(y),
          ymax = max(y),
          ymid = (ymin + ymax) / 2,
          .groups = 'drop'
        )
      
      # 获取树的基本坐标
      x_tree_min <- min(tree_dat$x, na.rm = TRUE)
      x_tip_max <- max(tip_dat$x, na.rm = TRUE)
      max_y <- max(tree_dat$y, na.rm = TRUE)
      tree_core_width <- x_tip_max - x_tree_min
      
      # 计算三个面板的X坐标（仿Motif风格）
      label_offset <- 0.4
      x_label_left <- x_tip_max + label_offset
      label_panel_width <- max(1.5, tree_core_width * 0.5)
      
      # Panel A: 树 + gene ID
      x_A_left <- x_tree_min
      x_A_right <- x_label_left + label_panel_width
      
      # Panel B: 亚家族竖线（中间）
      gap1 <- max(0.3, tree_core_width * 0.1)
      r_B <- 0.4
      x_B_left <- x_A_right + gap1
      width_B <- tree_core_width * r_B
      x_B_right <- x_B_left + width_B
      x_subfam <- (x_B_left + x_B_right) / 2
      
      # Panel C: 启动子热图区域（右侧）
      gap2 <- max(0.3, tree_core_width * 0.1)
      r_C <- 1.6
      x_C_left <- x_B_right + gap2
      width_C <- tree_core_width * r_C
      x_C_right <- x_C_left + width_C
      
      # 准备热图数据 - 将热图转换为矩形数据
      # 为每个基因-元件组合创建矩形
      heatmap_rect <- heatmap_data %>%
        mutate(
          # Y坐标保持与树一致
          ymin = as.numeric(X1) - 0.4,
          ymax = as.numeric(X1) + 0.4,
          # X坐标：将元件位置映射到Panel C
          # 计算每个元件在热图中的顺序位置
          element_index = as.numeric(factor(interaction(X2, short_desc))),
          n_elements = n_distinct(interaction(X2, short_desc)),
          # 每个元件占据的宽度
          elem_width = width_C / n_elements,
          xmin = x_C_left + (element_index - 1) * elem_width,
          xmax = x_C_left + element_index * elem_width
        )
      
      # 为不同数量级别设置颜色
      count_colors <- c(
        "[0,1)" = "#f7fbff",
        "[1,2)" = "#c6dbef",
        "[2,3)" = "#9ecae1",
        "[3,5)" = "#6baed6",
        "[5,10)" = "#4292c6",
        "[10,20)" = "#2171b5",
        "[20,50)" = "#08519c",
        "[50,100)" = "#08306b",
        "[100,200]" = "#041d3a"
      )
      
      # 构建组合图（仿Motif风格）
      p_combo <- p.tree +
        # Panel B: 亚家族竖线
        geom_segment(
          data = subfam_ranges,
          inherit.aes = FALSE,
          aes(x = x_subfam, xend = x_subfam, y = ymin, yend = ymax, colour = Subgroup),
          linewidth = 1.5
        ) +
        geom_text(
          data = subfam_ranges,
          inherit.aes = FALSE,
          aes(x = x_subfam + width_B * 0.08, y = ymid, label = Subgroup, colour = Subgroup),
          hjust = 0, size = 3.5
        ) +
        scale_color_manual(values = subfam_cols, name = "Subfamily") +
        
        # Panel C: 启动子元件矩形（使用new_scale_fill）
        ggnewscale::new_scale_fill() +
        geom_rect(
          data = heatmap_rect %>% filter(count > 0),
          inherit.aes = FALSE,
          aes(xmin = xmin, xmax = xmax, ymin = ymin, ymax = ymax, fill = count_cat),
          color = "grey80", size = 0.2
        ) +
        geom_text(
          data = heatmap_rect %>% filter(count > 0),
          inherit.aes = FALSE,
          aes(x = (xmin + xmax) / 2, y = (ymin + ymax) / 2, label = count),
          size = 2, color = "white"
        ) +
        scale_fill_manual(values = count_colors, name = "Element Count", drop = FALSE) +
        
        # 设置坐标范围
        coord_cartesian(xlim = c(x_A_left, x_C_right + tree_core_width * 0.1)) +
        expand_limits(y = max_y + 1.5) +
        
        # 添加表头
        annotate("text", x = (x_A_left + x_A_right) / 2, y = max_y + 1.2, 
                 label = "Phylogenetic tree", fontface = "bold", size = 4) +
        annotate("text", x = x_subfam, y = max_y + 1.2, 
                 label = "Subfamily", fontface = "bold", size = 4) +
        annotate("text", x = (x_C_left + x_C_right) / 2, y = max_y + 1.2, 
                 label = "Promoter Elements", fontface = "bold", size = 4) +
        
        theme(
          legend.position = "right",
          legend.background = element_rect(color = "#969696", fill = "#d9d9d9")
        )
      
      # 保存新版组合图
      combined_new_pdf <- paste0(output_prefix, "_tree_promoter_combo_new.pdf")
      combined_new_png <- paste0(output_prefix, "_tree_promoter_combo_new.png")
      
      ggsave(filename = combined_new_pdf, plot = p_combo, width = 16, height = 14)
      png(filename = combined_new_png, width = 4800, height = 4200, res = 300)
      print(p_combo)
      dev.off()
      
      cat("已保存新版组合图（仿Motif风格）:", combined_new_pdf, "\n")
      cat("已保存新版组合图PNG:", combined_new_png, "\n")
      
      # 保留旧版组合图生成代码（兼容）
      ####----旧版组合图（可选）----####
      cat("\n=== 同时生成旧版组合图 ===\n")
      p.promoter2 <- p.promoter + theme(axis.text.y = element_blank())
      combined_plot <- p.promoter2 %>% insert_left(p.tree, width = 0.7)
      
      combined_output_pdf <- paste0(output_prefix, "_tree_promoter_combined.pdf")
      combined_output_png <- paste0(output_prefix, "_tree_promoter_combined.png")
      ggsave(filename = combined_output_pdf, plot = combined_plot, height = 14, width = 16)
      png(filename = combined_output_png, width = 2400, height = 2100, res = 150)
      print(combined_plot)
      dev.off()
      cat("已保存旧版组合图:", combined_output_pdf, "\n")
      
    } else {
      cat("\n=== 跳过启动子热图绘制（保留元件不足） ===\n")
    }
  }
} else {
  cat("\n=== 跳过启动子相关绘图（无数据或数据不足） ===\n")
}

####----输出统计信息----####
cat("\n===== 分析完成 =====\n")
cat("生成的文件:\n")
cat("1.", tree_output_pdf, "\n")
cat("   ", tree_output_png, "\n")

if (exists("heatmap_output_pdf") && file.exists(heatmap_output_pdf)) {
  cat("2.", heatmap_output_pdf, "\n")
  cat("   ", heatmap_output_png, "\n")
}
if (exists("combined_output_pdf") && file.exists(combined_output_pdf)) {
  cat("3.", combined_output_pdf, "\n")
  cat("   ", combined_output_png, "\n")
}
if (exists("combined_new_pdf") && file.exists(combined_new_pdf)) {
  cat("3a. 新版组合图（仿Motif风格）:", combined_new_pdf, "\n")
  cat("    ", combined_new_png, "\n")
}
if (exists("final_output_pdf") && file.exists(final_output_pdf)) {
  cat("4.", final_output_pdf, "\n")
  cat("   ", final_output_png, "\n")
}

# 保存分组统计
group_summary <- group_df %>%
  group_by(Subgroup) %>%
  summarise(Genes = n(), .groups = 'drop')

group_summary_file <- paste0(output_prefix, "_subgroup_summary.xlsx")
write_xlsx(group_summary, group_summary_file)
cat("5.", group_summary_file, "\n")

# 保存参数记录
params <- data.frame(
  参数 = c("树文件", "分组文件", "启动子文件", "元件描述文件", "输出前缀"),
  值 = c(tree_file, group_file, 
         ifelse(is.null(promoter_file), "未提供", promoter_file), 
         ifelse(is.null(element_desc_file), "未提供", element_desc_file), 
         output_prefix)
)

params_file <- paste0(output_prefix, "_analysis_parameters.csv")
write.csv(params, params_file, row.names = FALSE)
cat("6.", params_file, "\n")

cat("\n===== 分析完成 =====\n")
