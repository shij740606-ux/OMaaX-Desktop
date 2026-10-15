#!/usr/bin/env Rscript

## =========================================================
## combo_tree_motif_structure_domain.R
## Panel A：Phylogenetic tree（含基因 ID）
## Panel B：Subfamily 竖线
## Panel C：Motif（按 XML 实际长度比例）
## Panel D：Gene structure（按 GFF3 实际长度比例，只画 CDS + UTR）
## Panel E：Domain（按蛋白长度 qlen 比例；来自 hmmscan domtblout）
##
## 底部：为 Panel C/D/E 各画一条“坐标尺”（motif 位置 / gene 坐标 / aa 位置）
##
## 用法:
##   Rscript combo_tree_motif_structure_domain.R \
##     <treefile> <subfamily_file> <meme_xml> <gff3_file> <domtblout_file> \
##     [evalue_cutoff] [coverage_percent] [output_pdf]
##
## 说明:
##   evalue_cutoff 默认 1e-5（按 i-Evalue 或 full E 过滤）
##   coverage_percent 为可选，只有你提供时才启用 coverage 过滤
##   （覆盖度 = (ali_to - ali_from + 1) / qlen）
## =========================================================

args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 5) {
  stop(
    "用法:\n",
    "  Rscript combo_tree_motif_structure_domain.R \\\n",
    "    <treefile> <subfamily_file> <meme_xml> <gff3_file> <domtblout_file> \\\n",
    "    [evalue_cutoff] [coverage_percent] [output_pdf]\n\n",
    call. = FALSE
  )
}

treefile       <- args[1]
subfamily_file <- args[2]
meme_xml       <- args[3]
gff3_file      <- args[4]
domtblout_file <- args[5]

## E-value 阈值（默认 1e-5）
if (length(args) >= 6 && nzchar(args[6])) {
  e_cutoff <- suppressWarnings(as.numeric(args[6]))
  if (is.na(e_cutoff)) e_cutoff <- 1e-5
} else {
  e_cutoff <- 1e-5
}

## 覆盖度阈值（可选）：命令行用百分数，如 50 = 50%；不提供则不启用 coverage 过滤
if (length(args) >= 7 && nzchar(args[7])) {
  tmp_cov <- suppressWarnings(as.numeric(args[7]))
  if (is.na(tmp_cov) || tmp_cov <= 0) {
    cov_cutoff <- NA_real_   # 非法或 <=0 表示不启用 coverage 过滤
  } else {
    cov_cutoff <- tmp_cov / 100
  }
} else {
  cov_cutoff <- NA_real_     # 默认不启用 coverage 过滤
}

## 输出 pdf 名称（可选）
if (length(args) >= 8 && nzchar(args[8])) {
  output_pdf <- args[8]
} else {
  prefix <- sub("\\.[^.]*$", "", treefile)
  output_pdf <- paste0(prefix, ".tree_motif_structure_domain.pdf")
}
parse_panels <- function(panel_arg) {
  allowed <- c("motif", "structure", "domain")
  if (!nzchar(panel_arg)) return(allowed)
  parts <- trimws(unlist(strsplit(panel_arg, "[,;]")))
  parts <- parts[nzchar(parts)]
  parts <- tolower(gsub(" ", "_", parts, fixed = TRUE))
  parts[parts %in% c("motifs")] <- "motif"
  parts[parts %in% c("gene_structure", "gene-structure", "genestructure")] <- "structure"
  parts[parts %in% c("domains")] <- "domain"
  if (any(parts == "all")) return(allowed)
  bad <- setdiff(parts, allowed)
  if (length(bad) > 0) {
    stop("不支持的 combo panel: ", paste(bad, collapse = ", "), "; 可选: ", paste(allowed, collapse = ", "), call. = FALSE)
  }
  selected <- unique(parts)
  if (length(selected) < 2) {
    stop("combo panel test 至少需要选择两个数据类型；单类型图请使用 motif/structure/domain 阶段已有结果。", call. = FALSE)
  }
  selected
}

selected_panels <- parse_panels(if (length(args) >= 9 && nzchar(args[9])) args[9] else "motif,structure,domain")
has_panel <- function(panel_name) panel_name %in% selected_panels
target_marker_species <- if (length(args) >= 10 && nzchar(args[10])) args[10] else ""
reference_marker_species <- if (length(args) >= 11 && nzchar(args[11])) args[11] else ""

classify_tip_role <- function(label) {
  label <- as.character(label)
  if (!nzchar(target_marker_species) && !nzchar(reference_marker_species)) return("gene")
  if (nzchar(reference_marker_species)) {
    # Support both normalized labels such as At|At1g01010 and native
    # reference labels such as At1g01010 used in IQ-TREE outputs.
    if (grepl(paste0("^", reference_marker_species, "\\|"), label) || grepl(paste0("^", reference_marker_species, "[0-9]"), label)) {
      return("reference")
    }
  }
  "target"
}

cat("树文件:         ", treefile,       "\n")
cat("亚家族文件:     ", subfamily_file, "\n")
cat("MEME XML 文件:  ", meme_xml,       "\n")
cat("GFF3 文件:      ", gff3_file,      "\n")
cat("domtblout 文件: ", domtblout_file, "\n")
cat("E-value 阈值:   ", e_cutoff,       "\n")
if (is.na(cov_cutoff)) {
  cat("覆盖度过滤:     未启用（仅按 E-value 过滤）\n")
} else {
  cat("覆盖度阈值:     ", cov_cutoff * 100, "%\n", sep = "")
}
cat("输出文件:       ", output_pdf,     "\n")
cat("展示 panel:     ", paste(selected_panels, collapse = ", "), "\n\n")

## ===================== 2. 加载 R 包 =====================


script_args_all <- commandArgs(trailingOnly = FALSE)
script_file_arg <- grep("^--file=", script_args_all, value = TRUE)
script_dir <- if (length(script_file_arg) > 0) dirname(normalizePath(sub("^--file=", "", script_file_arg[1]))) else getwd()
source(file.path(script_dir, "subfamily_palette.R"))

suppressPackageStartupMessages({
  library(ape)
  library(ggtree)
  library(tidytree)
  library(dplyr)
  library(RColorBrewer)
  library(ggplot2)
  library(xml2)
  library(ggnewscale)
})

normalize_gene_id <- function(x) {
  x <- as.character(x)
  x <- sub("^[^|]+\\|", "", x)
  repeat {
    y <- sub("^(gene|transcript|CDS|mRNA)[:_]", "", x, ignore.case = TRUE)
    if (identical(y, x)) break
    x <- y
  }
  x <- sub("_[PT][0-9]+$", "", x, ignore.case = TRUE)
  x <- sub("\\.[0-9]+$", "", x)
  tolower(x)
}

make_distinct_cols <- function(n) {
  if (n <= 0) return(character(0))
  base <- c(
    "#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00",
    "#56B4E9", "#F0E442", "#000000", "#882255", "#44AA99",
    "#AA4499", "#117733", "#332288", "#DDCC77", "#88CCEE"
  )
  if (n <= length(base)) return(base[seq_len(n)])
  if (exists("hcl.colors", where = asNamespace("grDevices"), inherits = FALSE)) {
    return(grDevices::hcl.colors(n, palette = "Dark 3"))
  }
  grDevices::colorRampPalette(base)(n)
}

## ===================== 3. 读亚家族文件 + 深色配色 =====================

add_confidence_labels <- function(subfam_df, subfamily_file) {
  add_subfamily_confidence_labels(subfam_df, subfamily_file)
}


# 检查亚家族文件是否为 "NONE" 或不存在
has_subfamily <- subfamily_file != "NONE" && file.exists(subfamily_file)

if (has_subfamily) {
  subfam <- read_subfamily_plot_table(subfamily_file)
  subfam <- add_confidence_labels(subfam, subfamily_file)
  
  subfam_levels <- sort(unique(subfam$subfamily))
  n_subfam      <- length(subfam_levels)
  
  if (n_subfam == 0) {
    warning("在亚家族文件中没有检测到任何 subfamily，将不显示亚家族")
    has_subfamily <- FALSE
  }
} else {
  cat("[INFO] 未提供亚家族文件，将不显示亚家族\n")
  # 创建空的 subfam 数据框
  subfam <- data.frame(label = character(), subfamily = character(), low_confidence = logical(), display_label = character(), stringsAsFactors = FALSE)
  subfam_levels <- character()
  n_subfam <- 0
}
## 跨图一致的亚家族配色
if (n_subfam > 0) {
  subfam_cols <- make_subfamily_cols(subfam_levels)
} else {
  subfam_cols <- character()
}

## ===================== 4. 树绘图函数 =====================

plot_iqtree_by_subfamily <- function(treefile, subfam_df, subfam_cols, label_offset = 0.4) {
  tree <- read.tree(treefile)
  tree_df <- as_tibble(tree)
  tree_df$isTip <- tree_df$node <= length(tree$tip.label)

  tree_df <- dplyr::left_join(tree_df, subfam_df, by = "label")
  tree_df$display_label <- ifelse(is.na(tree_df$display_label), tree_df$label, tree_df$display_label)
  tree_df$tip_role <- ifelse(tree_df$isTip, vapply(tree_df$label, classify_tip_role, character(1)), NA_character_)

  tree_df$support <- ifelse(
    tree_df$isTip,
    NA_real_,
    suppressWarnings(as.numeric(tree_df$label))
  )

  p <- ggtree(tree, branch.length = "none") %<+% tree_df +
    ## bootstrap
    geom_nodepoint(
      aes(fill = cut(support, c(0, 50, 75, 100))),
      shape = 21,
      size  = 2,
      na.rm = TRUE
    ) +
    scale_fill_manual(
      values = c("black", "grey40", "grey80"),
      guide  = "legend",
      name   = "Bootstrap Percentage (BP)",
      breaks = c("(75,100]", "(50,75]", "(0,50]"),
      labels = expression(BP > 75,
                          50 < BP * "<=75",
                          BP <= 50)
    ) +
    geom_text2(
      aes(label = ifelse(!isTip & !is.na(support), round(support), "")),
      hjust = -0.2, vjust = -0.4, size = 2.2,
      na.rm = TRUE, show.legend = FALSE
    ) +
    ## tip 颜色 = subfamily
    geom_tippoint(
      aes(color = subfamily, shape = tip_role),
      size  = 2,
      na.rm = TRUE
    ) +
    scale_color_manual(
      values = subfam_cols,
      name   = "Subfamily"
    ) +
    scale_shape_manual(
      values = c(target = 17, reference = 16, gene = 16),
      breaks = c("target", "reference"),
      labels = c("Target", "Reference"),
      name = "Species role",
      na.translate = FALSE
    ) +
    ## 基因 ID 标签
    geom_tiplab(aes(label = display_label), offset = label_offset, size = 3, hjust = 0) +
    ggtitle(NULL) +
    theme_tree() +
    theme(
      legend.background = element_rect(color = "#969696",
                                       fill  = "#d9d9d9")
    )

  attr(p, "label_offset") <- label_offset
  return(p)
}

## ===================== 5. 解析 MEME XML =====================

parse_meme_xml <- function(meme_file) {

  doc <- xml2::read_xml(meme_file)

  ## 所有序列的 id/name/length
  seq_nodes <- xml2::xml_find_all(doc, ".//sequence")
  seq_ids   <- xml2::xml_attr(seq_nodes, "id")
  seq_names <- xml2::xml_attr(seq_nodes, "name")

  ## 兼容 length / Length 大小写
  seq_lengths <- xml2::xml_attr(seq_nodes, "length")
  if (any(is.na(seq_lengths))) {
    seq_lengths2 <- xml2::xml_attr(seq_nodes, "Length")
    idx <- which(is.na(seq_lengths) & !is.na(seq_lengths2))
    seq_lengths[idx] <- seq_lengths2[idx]
  }
  seq_lengths <- suppressWarnings(as.numeric(seq_lengths))

  name_map   <- setNames(seq_names,   seq_ids)   # id -> gene
  length_map <- setNames(seq_lengths, seq_names) # gene -> seqlen

  motif_nodes <- xml2::xml_find_all(doc, ".//motif")
  site_list   <- list()
  motif_index <- 1L

  for (m in motif_nodes) {
    motif_id <- xml2::xml_attr(m, "id")
    if (is.na(motif_id) || motif_id == "") {
      motif_id <- xml2::xml_attr(m, "name")
    }
    if (is.na(motif_id) || motif_id == "") {
      motif_id <- paste0("Motif_", motif_index)
    }

    motif_width <- xml2::xml_attr(m, "width")
    motif_width <- suppressWarnings(as.numeric(motif_width))
    if (is.na(motif_width)) motif_width <- 10

    sites <- xml2::xml_find_all(m, ".//contributing_site")
    if (length(sites) == 0) next

    seq_id <- xml2::xml_attr(sites, "sequence_id")
    start  <- xml2::xml_attr(sites, "position")
    start  <- suppressWarnings(as.numeric(start))

    width  <- rep(motif_width, length(sites))

    df <- data.frame(
      seq_id = seq_id,
      start  = start,
      width  = width,
      motif  = motif_id,
      stringsAsFactors = FALSE
    )

    site_list[[length(site_list) + 1L]] <- df
    motif_index <- motif_index + 1L
  }

  if (length(site_list) == 0) {
    stop("在 MEME XML 中没有找到任何 motif site，请检查文件或 XML 结构。", call. = FALSE)
  }

  sites <- dplyr::bind_rows(site_list)

  ## 序列名 & 序列长度来自 XML 中的 sequence 区
  sites$gene   <- unname(name_map[sites$seq_id])
  sites$seqlen <- length_map[sites$gene]

  ## motif 终点坐标
  sites$end    <- sites$start + sites$width - 1

  sites
}

## ===================== 6. 解析 GFF3（基因结构） =====================

parse_gff3_gene_structure <- function(gff3_file, tree_genes) {
  tree_lookup <- setNames(tree_genes, normalize_gene_id(tree_genes))

  gff <- read.table(gff3_file,
                    sep = "\t",
                    comment.char = "#",
                    quote = "",
                    stringsAsFactors = FALSE,
                    fill = TRUE)

  if (ncol(gff) < 9) {
    stop("GFF3 文件列数不足 9 列，请检查格式。", call. = FALSE)
  }

  colnames(gff)[1:9] <- c("seqid", "source", "type", "start",
                          "end", "score", "strand", "phase",
                          "attributes")

  gff$start <- as.numeric(gff$start)
  gff$end   <- as.numeric(gff$end)

  ## 只保留关心的 feature 类型
  feat_types <- c("exon", "CDS", "UTR",
                  "five_prime_UTR", "three_prime_UTR",
                  "5UTR", "3UTR")
  feat_df <- gff[gff$type %in% feat_types, ]

  if (nrow(feat_df) == 0) {
    stop("在 GFF3 中没有找到 exon/CDS/UTR 等特征。", call. = FALSE)
  }

  ## 解析 attributes 里的 Parent=...
  get_attr <- function(attr_vec, key) {
    sapply(strsplit(attr_vec, ";"), function(fields) {
      kv <- strsplit(fields, "=")
      res <- NA_character_
      for (p in kv) {
        if (length(p) == 2 && p[1] == key) {
          res <- p[2]
          break
        }
      }
      res
    })
  }

  ## NCBI/Gnomon GFF3 often stores mRNA/CDS IDs as XP_* protein accessions,
  ## while the tree tips are XM_* transcript accessions in Name/transcript_id.
  ## Build an ID -> tree-tip alias map from mRNA rows before matching features.
  mrna_df <- gff[tolower(gff$type) %in% c("mrna", "transcript"), ]
  transcript_alias <- character(0)
  if (nrow(mrna_df) > 0) {
    mrna_id <- get_attr(mrna_df$attributes, "ID")
    mrna_name <- get_attr(mrna_df$attributes, "Name")
    mrna_tx <- get_attr(mrna_df$attributes, "transcript_id")
    mrna_gene <- get_attr(mrna_df$attributes, "gene")
    for (i in seq_len(nrow(mrna_df))) {
      candidates <- c(mrna_name[i], mrna_tx[i], mrna_id[i], mrna_gene[i])
      candidates <- candidates[!is.na(candidates) & nzchar(candidates)]
      target <- NA_character_
      for (cand in candidates) {
        key <- normalize_gene_id(cand)
        if (key %in% names(tree_lookup)) {
          target <- unname(tree_lookup[key])
          break
        }
      }
      if (!is.na(target) && !is.na(mrna_id[i]) && nzchar(mrna_id[i])) {
        transcript_alias[mrna_id[i]] <- target
      }
    }
  }

  feat_df$Parent <- get_attr(feat_df$attributes, "Parent")
  feat_df$Parent_resolved <- ifelse(!is.na(feat_df$Parent) & feat_df$Parent %in% names(transcript_alias),
                                    unname(transcript_alias[feat_df$Parent]),
                                    feat_df$Parent)
  feat_df$Parent_key <- normalize_gene_id(feat_df$Parent_resolved)

  ## 只保留 Parent/别名 出现在树 tip.label 的转录本
  feat_df <- feat_df[!is.na(feat_df$Parent_resolved) &
                       feat_df$Parent_key %in% names(tree_lookup), ]

  if (nrow(feat_df) == 0) {
    stop("GFF3 中的 Parent 与树 tip.label 无交集，请检查 ID 是否一致。", call. = FALSE)
  }

  feat_df$gene <- unname(tree_lookup[feat_df$Parent_key])

  ## 每个 gene 的基因区域：真实 genomic 坐标范围
  gene_ranges <- feat_df %>%
    dplyr::group_by(gene) %>%
    dplyr::summarise(
      gene_start = min(start, end, na.rm = TRUE),
      gene_end   = max(start, end, na.rm = TRUE),
      gene_len   = gene_end - gene_start + 1,
      .groups    = "drop"
    )

  ## 特征相对该基因起点的坐标
  feat_df <- feat_df %>%
    dplyr::left_join(gene_ranges, by = "gene") %>%
    dplyr::mutate(
      rel_start = pmin(start, end) - gene_start,
      rel_end   = pmax(start, end) - gene_start
    ) %>%
    dplyr::mutate(
      Type = dplyr::case_when(
        type %in% c("five_prime_UTR", "three_prime_UTR",
                    "5UTR", "3UTR", "UTR") ~ "UTR",
        TRUE ~ type
      )
    )

  list(features = feat_df, ranges = gene_ranges)
}

## ===================== 7. 解析 domtblout（domain） =====================

parse_domtblout <- function(domtbl_file, e_cutoff = 1e-5,
                            cov_cutoff = NA_real_, tree_genes = NULL) {

  dom <- read.table(domtbl_file,
                    header = FALSE,
                    comment.char = "#",
                    stringsAsFactors = FALSE,
                    fill = TRUE)

  if (ncol(dom) < 22) {
    stop("domtblout 文件列数少于 22 列，请检查 hmmscan 输出是否为 --domtblout 格式。", call. = FALSE)
  }

  ## HMMER3 domtblout 固定前 22 列
  colnames(dom)[1:22] <- c(
    "target_name", "t_accession", "tlen",
    "query_name",  "q_accession", "qlen",
    "E_full", "score_full", "bias_full",
    "dom_index", "dom_of",
    "c_Evalue", "i_Evalue", "dom_score", "dom_bias",
    "hmm_from", "hmm_to",
    "ali_from", "ali_to",
    "env_from", "env_to",
    "acc"
  )

  ## 余下列合并为 description
  if (ncol(dom) > 22) {
    desc_cols <- dom[, 23:ncol(dom), drop = FALSE]
    dom$description <- apply(desc_cols, 1, function(x) paste(x, collapse = " "))
  } else {
    dom$description <- NA_character_
  }

  ## 转成数值
  num_cols <- c(
    "tlen", "qlen",
    "E_full", "score_full", "bias_full",
    "dom_index", "dom_of",
    "c_Evalue", "i_Evalue", "dom_score", "dom_bias",
    "hmm_from", "hmm_to",
    "ali_from", "ali_to",
    "env_from", "env_to",
    "acc"
  )
  for (cn in intersect(num_cols, colnames(dom))) {
    dom[[cn]] <- suppressWarnings(as.numeric(dom[[cn]]))
  }

  ## 只保留树上的基因（若指定）
  if (!is.null(tree_genes)) {
    tree_lookup <- setNames(tree_genes, normalize_gene_id(tree_genes))
    dom$query_key <- normalize_gene_id(dom$query_name)
    dom <- dom[dom$query_key %in% names(tree_lookup), , drop = FALSE]
    if (nrow(dom) > 0) {
      dom$query_name <- unname(tree_lookup[dom$query_key])
    }
  }

  if (nrow(dom) == 0) {
    warning("domtblout 中没有与树上的基因匹配的记录。")
    return(dom)
  }

  ## 计算 domain 覆盖度（按蛋白长度 qlen）
  dom <- dom %>%
    dplyr::mutate(
      dom_len = ali_to - ali_from + 1,
      cov     = ifelse(is.na(qlen) | qlen <= 0, NA_real_, dom_len / qlen)
    )

  ## 使用 i_Evalue 作为 domain 显著性（若不存在则退回 full E）
  dom_e <- if ("i_Evalue" %in% colnames(dom)) dom$i_Evalue else dom$E_full

  dom_filt <- dom %>%
    dplyr::mutate(evalue = dom_e) %>%
    dplyr::filter(!is.na(evalue), evalue <= e_cutoff)

  ## 只有在 cov_cutoff 非 NA 时才启用覆盖度过滤
  if (!is.na(cov_cutoff)) {
    dom_filt <- dom_filt %>%
      dplyr::filter(is.na(cov) | cov >= cov_cutoff)
  }

  if (nrow(dom_filt) == 0) {
    warning("在 domtblout 中按 E-value/coverage 筛选后没有 domain 命中。")
  }

  dom_filt
}

## ===================== 8. 底部坐标轴辅助函数 =====================

make_axis_df <- function(max_len, scale_val, x_left, axis_y, n_breaks = 5) {
  empty_line   <- data.frame(x = numeric(0), xend = numeric(0),
                             y = numeric(0), yend = numeric(0))
  empty_labels <- data.frame(x = numeric(0), y = numeric(0),
                             label = character(0))
  if (!is.finite(max_len) || max_len <= 0 ||
      !is.finite(scale_val) || scale_val <= 0) {
    return(list(line = empty_line, ticks = empty_line, labels = empty_labels))
  }
  brks <- pretty(c(0, max_len), n = n_breaks)
  brks <- brks[brks >= 0 & brks <= max_len]

  line_df <- data.frame(
    x    = x_left,
    xend = x_left + max_len * scale_val,
    y    = axis_y,
    yend = axis_y
  )
  ticks_df <- data.frame(
    x    = x_left + brks * scale_val,
    xend = x_left + brks * scale_val,
    y    = axis_y,
    yend = axis_y + 0.2
  )
  labels_df <- data.frame(
    x     = x_left + brks * scale_val,
    y     = axis_y - 0.3,
    label = as.integer(brks)
  )
  list(line = line_df, ticks = ticks_df, labels = labels_df)
}

## ===================== 9. 画树 & 基本坐标信息 =====================

label_offset <- 0.4
p_tree <- plot_iqtree_by_subfamily(treefile, subfam, subfam_cols,
                                   label_offset = label_offset)

tree_dat <- p_tree$data
tip_dat  <- tree_dat %>%
  dplyr::filter(isTip) %>%
  dplyr::select(label, x, y, subfamily)

if (nrow(tip_dat) == 0) {
  stop("树中没有 tip 节点，请检查 treefile。", call. = FALSE)
}

x_tree_min <- min(tree_dat$x, na.rm = TRUE)
x_tip_max  <- max(tip_dat$x,   na.rm = TRUE)
max_y      <- max(tree_dat$y,  na.rm = TRUE)
min_y      <- min(tree_dat$y,  na.rm = TRUE)
axis_y     <- min_y - 1.5

tree_core_width <- x_tip_max - x_tree_min
if (!is.finite(tree_core_width) || tree_core_width <= 0) {
  stop("树的宽度计算异常，请检查 treefile。", call. = FALSE)
}

x_label_left <- x_tip_max + label_offset
label_panel_width <- max(1.5, tree_core_width * 0.6)
x_A_left  <- x_tree_min
x_A_right <- x_label_left + label_panel_width

tip_lookup <- setNames(tip_dat$label, normalize_gene_id(tip_dat$label))
empty_axis <- make_axis_df(NA_real_, NA_real_, NA_real_, axis_y)

## ===================== 10. 按需解析 panel 数据 =====================

if (has_panel("motif")) {
  if (meme_xml == "NONE" || !file.exists(meme_xml)) {
    stop("已选择 motif panel，但 MEME XML 不存在。", call. = FALSE)
  }
  motif_sites <- parse_meme_xml(meme_xml)
  motif_sites <- motif_sites %>%
    dplyr::filter(!is.na(gene)) %>%
    dplyr::mutate(gene_key = normalize_gene_id(gene)) %>%
    dplyr::filter(gene_key %in% names(tip_lookup)) %>%
    dplyr::mutate(gene = unname(tip_lookup[gene_key])) %>%
    dplyr::left_join(tip_dat, by = c("gene" = "label")) %>%
    dplyr::filter(!is.na(seqlen), !is.na(y))

  if (nrow(motif_sites) == 0) {
    stop("在 MEME 结果中没有找到与树 tip.label 匹配的基因。", call. = FALSE)
  }

  cat("motif 序列长度范围 (XML): ",
      min(motif_sites$seqlen, na.rm = TRUE), " ~ ",
      max(motif_sites$seqlen, na.rm = TRUE), "\n")

  motif_levels <- sort(unique(motif_sites$motif))
  motif_cols <- setNames(make_distinct_cols(length(motif_levels)), motif_levels)
  motif_sites$motif <- factor(motif_sites$motif, levels = motif_levels)
} else {
  motif_sites <- data.frame()
  motif_cols <- character()
}

if (has_panel("structure")) {
  if (gff3_file == "NONE" || !file.exists(gff3_file)) {
    stop("已选择 structure panel，但 GFF3 文件不存在。", call. = FALSE)
  }
  gs <- parse_gff3_gene_structure(gff3_file, tip_dat$label)
  gene_feat   <- gs$features
  gene_ranges <- gs$ranges

  gene_ranges <- gene_ranges %>%
    dplyr::left_join(tip_dat, by = c("gene" = "label")) %>%
    dplyr::filter(!is.na(y))
  gene_feat <- gene_feat %>%
    dplyr::left_join(tip_dat, by = c("gene" = "label")) %>%
    dplyr::filter(!is.na(y))

  if (nrow(gene_feat) == 0) {
    stop("GFF3 中匹配到树 tip.label 的基因结构为空。", call. = FALSE)
  }

  cat("基因长度范围 (GFF3): ",
      min(gene_ranges$gene_len, na.rm = TRUE), " ~ ",
      max(gene_ranges$gene_len, na.rm = TRUE), "\n")
} else {
  gene_feat <- data.frame()
  gene_ranges <- data.frame()
}

if (has_panel("domain")) {
  if (domtblout_file == "NONE" || !file.exists(domtblout_file)) {
    stop("已选择 domain panel，但 domtblout 文件不存在。", call. = FALSE)
  }
  domain_hits <- parse_domtblout(domtblout_file,
                                 e_cutoff   = e_cutoff,
                                 cov_cutoff = cov_cutoff,
                                 tree_genes = tip_dat$label)
  domain_hits <- domain_hits %>%
    dplyr::left_join(tip_dat, by = c("query_name" = "label")) %>%
    dplyr::filter(!is.na(y))

  if (nrow(domain_hits) > 0) {
    cat("蛋白长度范围 (domtblout qlen): ",
        min(domain_hits$qlen, na.rm = TRUE), " ~ ",
        max(domain_hits$qlen, na.rm = TRUE), "\n")
  } else {
    cat("注意：筛选后没有任何 domain 命中，将不会绘制 domain 命中矩形。\n")
  }
} else {
  domain_hits <- data.frame()
}

## ===================== 11. 动态 X 布局 =====================

gap <- max(0.3, tree_core_width * 0.10)
r_B <- 0.6
panel_ratio <- c(motif = 2.0, structure = 2.8, domain = 2.0)
panel_label <- c(motif = "Motif", structure = "Gene structure", domain = "Domain")

x_B_left   <- x_A_right + gap
width_B    <- tree_core_width * r_B
x_B_right  <- x_B_left + width_B
x_subfam   <- (x_B_left + x_B_right) / 2

panel_layout <- data.frame(
  panel = character(), x_left = numeric(), x_right = numeric(), width = numeric(), label = character(),
  stringsAsFactors = FALSE
)
cursor <- x_B_right
for (panel_name in selected_panels) {
  width <- tree_core_width * panel_ratio[[panel_name]]
  x_left <- cursor + gap
  x_right <- x_left + width
  panel_layout <- rbind(panel_layout, data.frame(
    panel = panel_name,
    x_left = x_left,
    x_right = x_right,
    width = width,
    label = panel_label[[panel_name]],
    stringsAsFactors = FALSE
  ))
  cursor <- x_right
}

panel_left <- function(panel_name) panel_layout$x_left[match(panel_name, panel_layout$panel)]
panel_width <- function(panel_name) panel_layout$width[match(panel_name, panel_layout$panel)]
panel_right <- function(panel_name) panel_layout$x_right[match(panel_name, panel_layout$panel)]
x_plot_right <- if (nrow(panel_layout) > 0) max(panel_layout$x_right) else x_B_right

## ===================== 12. panel 坐标计算 =====================

if (has_panel("motif")) {
  x_C_left <- panel_left("motif")
  width_C <- panel_width("motif")
  max_len_motif <- max(motif_sites$seqlen, na.rm = TRUE)
  scale_motif <- width_C / max_len_motif
  cat("最长 motif 序列长度: ", max_len_motif,
      "；motif 面板宽度: ", width_C,
      "；scale_motif: ", scale_motif, "\n")

  motif_gene_tracks <- motif_sites %>%
    dplyr::distinct(gene, y, seqlen) %>%
    dplyr::mutate(
      x_gene_start = x_C_left,
      x_gene_end   = x_C_left + seqlen * scale_motif
    )
  motif_rect <- motif_sites %>%
    dplyr::mutate(
      xmin = x_C_left + (start - 1) * scale_motif,
      xmax = x_C_left + end        * scale_motif,
      ymin = y - 0.25,
      ymax = y + 0.25
    )
  motif_axis <- make_axis_df(max_len_motif, scale_motif, x_C_left, axis_y)
} else {
  motif_gene_tracks <- data.frame()
  motif_rect <- data.frame()
  motif_axis <- empty_axis
}

if (has_panel("structure")) {
  x_D_left <- panel_left("structure")
  width_D <- panel_width("structure")
  max_gene_len <- max(gene_ranges$gene_len, na.rm = TRUE)
  scale_gene <- width_D / max_gene_len
  cat("最长基因长度: ", max_gene_len,
      "；gene 面板宽度: ", width_D,
      "；scale_gene: ", scale_gene, "\n")

  gene_tracks <- gene_ranges %>%
    dplyr::mutate(
      x_gene_start = x_D_left,
      x_gene_end   = x_D_left + gene_len * scale_gene
    )
  gene_feat_rect <- gene_feat %>%
    dplyr::mutate(
      xmin = x_D_left + rel_start * scale_gene,
      xmax = x_D_left + rel_end   * scale_gene,
      ymin = y - 0.25,
      ymax = y + 0.25
    ) %>%
    dplyr::filter(Type %in% c("CDS", "UTR"))
  gene_palette <- c(CDS = "#1b9e77", UTR = "#d95f02")
  gene_types <- sort(unique(gene_feat_rect$Type))
  gene_cols_vec <- gene_palette[gene_types]
  names(gene_cols_vec) <- gene_types
  gene_axis <- make_axis_df(max_gene_len, scale_gene, x_D_left, axis_y)
} else {
  gene_tracks <- data.frame()
  gene_feat_rect <- data.frame()
  gene_cols_vec <- character()
  gene_axis <- empty_axis
}

if (has_panel("domain")) {
  x_E_left <- panel_left("domain")
  width_E <- panel_width("domain")
  max_qlen <- NA_real_
  scale_domain <- NA_real_
  if (nrow(domain_hits) > 0) {
    max_qlen <- max(domain_hits$qlen, na.rm = TRUE)
    scale_domain <- width_E / max_qlen
    cat("最长蛋白长度: ", max_qlen,
        "；domain 面板宽度: ", width_E,
        "；scale_domain: ", scale_domain, "\n")

    domain_gene_tracks <- domain_hits %>%
      dplyr::distinct(query_name, y, qlen) %>%
      dplyr::mutate(
        x_gene_start = x_E_left,
        x_gene_end   = x_E_left + qlen * scale_domain
      )
    domain_rect <- domain_hits %>%
      dplyr::mutate(
        xmin = x_E_left + (ali_from - 1) * scale_domain,
        xmax = x_E_left + ali_to        * scale_domain,
        ymin = y - 0.25,
        ymax = y + 0.25
      )
    domain_levels <- sort(unique(domain_rect$target_name))
    domain_cols <- setNames(make_distinct_cols(length(domain_levels)), domain_levels)
    domain_rect$target_name <- factor(domain_rect$target_name, levels = domain_levels)
  } else {
    domain_gene_tracks <- data.frame()
    domain_rect <- data.frame()
    domain_cols <- character()
  }
  domain_axis <- make_axis_df(max_qlen, scale_domain, x_E_left, axis_y)
} else {
  domain_gene_tracks <- data.frame()
  domain_rect <- data.frame()
  domain_cols <- character()
  domain_axis <- empty_axis
}

subfam_ranges <- make_subfamily_ranges(tip_dat)
tree_header_x <- (x_A_left + x_A_right) / 2
header_y <- max_y + 1.2

## ===================== 13. 拼合整图 =====================

p_combo <- p_tree

if (has_panel("motif")) {
  p_combo <- p_combo +
    geom_segment(
      data = motif_gene_tracks, inherit.aes = FALSE,
      aes(x = x_gene_start, xend = x_gene_end, y = y, yend = y),
      colour = "grey70", linewidth = 0.4
    ) +
    ggnewscale::new_scale_fill() +
    geom_rect(
      data = motif_rect, inherit.aes = FALSE,
      aes(xmin = xmin, xmax = xmax, ymin = ymin, ymax = ymax, fill = motif),
      color = NA
    ) +
    scale_fill_manual(values = motif_cols, name = "Motif") +
    geom_segment(data = motif_axis$line, inherit.aes = FALSE, aes(x = x, xend = xend, y = y, yend = yend), colour = "black", linewidth = 0.4) +
    geom_segment(data = motif_axis$ticks, inherit.aes = FALSE, aes(x = x, xend = xend, y = y, yend = yend), colour = "black", linewidth = 0.3) +
    geom_text(data = motif_axis$labels, inherit.aes = FALSE, aes(x = x, y = y, label = label), size = 2.5)
}

if (has_panel("structure")) {
  p_combo <- p_combo +
    geom_segment(
      data = gene_tracks, inherit.aes = FALSE,
      aes(x = x_gene_start, xend = x_gene_end, y = y, yend = y),
      colour = "grey70", linewidth = 0.4
    ) +
    ggnewscale::new_scale_fill() +
    geom_rect(
      data = gene_feat_rect, inherit.aes = FALSE,
      aes(xmin = xmin, xmax = xmax, ymin = ymin, ymax = ymax, fill = Type),
      color = NA
    ) +
    scale_fill_manual(values = gene_cols_vec, name = "Gene structure") +
    geom_segment(data = gene_axis$line, inherit.aes = FALSE, aes(x = x, xend = xend, y = y, yend = yend), colour = "black", linewidth = 0.4) +
    geom_segment(data = gene_axis$ticks, inherit.aes = FALSE, aes(x = x, xend = xend, y = y, yend = yend), colour = "black", linewidth = 0.3) +
    geom_text(data = gene_axis$labels, inherit.aes = FALSE, aes(x = x, y = y, label = label), size = 2.5)
}

if (has_panel("domain") && nrow(domain_rect) > 0) {
  p_combo <- p_combo +
    geom_segment(
      data = domain_gene_tracks, inherit.aes = FALSE,
      aes(x = x_gene_start, xend = x_gene_end, y = y, yend = y),
      colour = "grey70", linewidth = 0.4
    ) +
    ggnewscale::new_scale_fill() +
    geom_rect(
      data = domain_rect, inherit.aes = FALSE,
      aes(xmin = xmin, xmax = xmax, ymin = ymin, ymax = ymax, fill = target_name),
      color = NA
    ) +
    scale_fill_manual(values = domain_cols, name = "Domain") +
    geom_segment(data = domain_axis$line, inherit.aes = FALSE, aes(x = x, xend = xend, y = y, yend = yend), colour = "black", linewidth = 0.4) +
    geom_segment(data = domain_axis$ticks, inherit.aes = FALSE, aes(x = x, xend = xend, y = y, yend = yend), colour = "black", linewidth = 0.3) +
    geom_text(data = domain_axis$labels, inherit.aes = FALSE, aes(x = x, y = y, label = label), size = 2.5)
}

p_combo <- p_combo +
  geom_segment(
    data = subfam_ranges, inherit.aes = FALSE,
    aes(x = x_subfam, xend = x_subfam, y = ymin, yend = ymax, colour = subfamily),
    linewidth = 1
  ) +
  geom_text(
    data = subfam_ranges, inherit.aes = FALSE,
    aes(x = x_subfam + width_B * 0.05, y = ymid, label = subfamily, colour = subfamily),
    hjust = 0, size = 3
  ) +
  annotate("text", x = tree_header_x, y = header_y, label = "Phylogenetic tree", fontface = "bold", size = 4)

for (i in seq_len(nrow(panel_layout))) {
  p_combo <- p_combo + annotate(
    "text",
    x = (panel_layout$x_left[i] + panel_layout$x_right[i]) / 2,
    y = header_y,
    label = panel_layout$label[i],
    fontface = "bold",
    size = 4
  )
}

p_combo <- p_combo +
  coord_cartesian(xlim = c(x_A_left, x_plot_right + tree_core_width * 0.1)) +
  expand_limits(y = header_y + 0.5) +
  theme(
    legend.position = "right",
    legend.background = element_rect(color = "#969696", fill = "#d9d9d9")
  )

plot_width <- c(`1` = 12, `2` = 16, `3` = 20)[as.character(length(selected_panels))]
if (is.na(plot_width)) plot_width <- 20

ggsave(output_pdf, p_combo, width = plot_width, height = 10)

output_png <- sub("\\.pdf$", ".png", output_pdf)
ggsave(output_png, p_combo, width = plot_width, height = 10, dpi = 150, bg = "white")

cat("已输出 combo panel test 图到: ", output_pdf, "\n")
cat("[OK] PNG: ", output_png, "\n", sep = "")
