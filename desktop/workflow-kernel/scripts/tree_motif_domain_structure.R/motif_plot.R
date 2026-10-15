#!/usr/bin/env Rscript

## ===================== 1. 解析命令行参数 =====================

args <- commandArgs(trailingOnly = TRUE)

if (length(args) < 2) {
  stop("用法: Rscript motif_plot.R <treefile> <meme_xml> [output_pdf]\n",
       call. = FALSE)
}

treefile <- args[1]
meme_xml <- args[2]

## 第三个参数：输出 pdf 名称（可选）
if (length(args) >= 3) {
  output_pdf <- args[3]
} else {
  ## 默认用 MEME XML 文件名去掉后缀 + .motif.pdf
  output_pdf <- sub("\\.[^.]*$", "", meme_xml)
  output_pdf <- paste0(output_pdf, ".motif.pdf")
}

cat("树文件:      ", treefile,   "\n")
cat("MEME XML:    ", meme_xml,   "\n")
cat("输出文件:    ", output_pdf, "\n")

## ===================== 2. 加载所需 R 包 =====================

suppressPackageStartupMessages({
  library(ape)         # 读树，获取 tip 顺序
  library(xml2)        # 解析 MEME XML
  library(dplyr)       # 数据整理
  library(ggplot2)     # 画图
  library(gggenes)     # 这里只用 theme_genes
  library(RColorBrewer)# 自动配色
})

## ===================== 3. 解析 MEME XML 的函数 =====================

## 思路：
##  1. 解析 <sequence id="sequence_0" name="FtPinG0009145900.01.T01" length="989">
##     得到：内部 ID -> 基因名 -> 序列长度 的映射表
##  2. 再解析 motif 位点（contributing_site / site / scanned_site），
##     用 sequence_id 映射到真实基因名（name）

parse_meme_xml <- function(meme_xml_file) {
  doc <- xml2::read_xml(meme_xml_file)

  ## ---------- 3.1 解析所有 sequence 映射表 ----------
  seq_nodes <- xml2::xml_find_all(doc, ".//sequence")

  if (length(seq_nodes) == 0) {
    warning("在 MEME XML 中没有找到 <sequence> 节点，将只使用位点自身携带的 sequence_id/sequence_name。")
    seq_map <- data.frame(
      seq_xml_id = character(0),
      seq_name   = character(0),
      seq_length = integer(0),
      stringsAsFactors = FALSE
    )
  } else {
    seq_ids     <- xml2::xml_attr(seq_nodes, "id")
    seq_name    <- xml2::xml_attr(seq_nodes, "name")
    seq_length  <- xml2::xml_attr(seq_nodes, "length")

    empty_name <- is.na(seq_name) | seq_name == ""
    seq_name[empty_name] <- seq_ids[empty_name]

    seq_length <- suppressWarnings(as.integer(seq_length))

    seq_map <- data.frame(
      seq_xml_id = seq_ids,
      seq_name   = seq_name,
      seq_length = seq_length,
      stringsAsFactors = FALSE
    )

    cat("在 MEME XML 中找到序列数: ", nrow(seq_map), "\n")
  }

  ## ---------- 3.2 查找所有 motif 位点节点 ----------
  site_nodes <- xml2::xml_find_all(
    doc,
    ".//contributing_site | .//site | .//scanned_site"
  )

  if (length(site_nodes) == 0) {
    stop("没有在 MEME XML 中解析出任何 motif 位点，请检查 meme.xml 结构。",
         call. = FALSE)
  }

  cat("在 MEME XML 中找到位点节点数: ", length(site_nodes), "\n")

  rows <- vector("list", length(site_nodes))
  idx  <- 1L

  for (s in site_nodes) {

    ## -------- 3.2.1 motif_id --------
    motif_id <- xml2::xml_attr(s, "motif_id")
    if (is.na(motif_id) || motif_id == "") {
      motif_id <- xml2::xml_attr(s, "motif")
    }
    if (is.na(motif_id) || motif_id == "") {
      motif_id <- xml2::xml_attr(s, "id")
    }

    if (is.na(motif_id) || motif_id == "") {
      motif_node <- xml2::xml_find_first(s, "ancestor::motif")
      if (!inherits(motif_node, "xml_missing")) {
        motif_id <- xml2::xml_attr(motif_node, "id")
        if (is.na(motif_id) || motif_id == "") {
          motif_id <- xml2::xml_attr(motif_node, "name")
        }
      }
    }

    if (is.na(motif_id) || motif_id == "") {
      motif_id <- "motif_unknown"
    }

    ## -------- 3.2.2 sequence_id / name 映射 --------
    seq_key <- xml2::xml_attr(s, "sequence_id")
    if (is.na(seq_key) || seq_key == "") {
      seq_key <- xml2::xml_attr(s, "seq")
    }

    seq_name_direct <- xml2::xml_attr(s, "sequence_name")
    if (is.na(seq_name_direct) || seq_name_direct == "") {
      seq_name_direct <- xml2::xml_attr(s, "name")
    }

    seq_id <- NA_character_

    if (!is.na(seq_name_direct) && seq_name_direct != "") {
      seq_id <- seq_name_direct
    } else {
      if (!is.na(seq_key) && seq_key != "" && nrow(seq_map) > 0) {
        hit <- which(seq_map$seq_xml_id == seq_key)
        if (length(hit) > 0) {
          seq_id <- seq_map$seq_name[hit[1]]
        }
      }

      if (is.na(seq_id) || seq_id == "") {
        seq_node <- xml2::xml_find_first(s, "ancestor::sequence")
        if (!inherits(seq_node, "xml_missing")) {
          seq_id <- xml2::xml_attr(seq_node, "name")
          if (is.na(seq_id) || seq_id == "") {
            seq_id <- xml2::xml_attr(seq_node, "id")
          }
        }
      }
    }

    if (is.na(seq_id) || seq_id == "") {
      next
    }

    ## -------- 3.2.3 起始位置 start --------
    start <- xml2::xml_attr(s, "position")
    if (is.na(start) || start == "") {
      start <- xml2::xml_attr(s, "start")
    }
    start <- suppressWarnings(as.integer(start))

    ## -------- 3.2.4 宽度 width / length --------
    width <- xml2::xml_attr(s, "length")
    if (is.na(width) || width == "") {
      width <- xml2::xml_attr(s, "width")
    }
    if (is.na(width) || width == "") {
      motif_node2 <- xml2::xml_find_first(s, "ancestor::motif")
      if (!inherits(motif_node2, "xml_missing")) {
        width <- xml2::xml_attr(motif_node2, "width")
      }
    }
    width <- suppressWarnings(as.integer(width))

    if (is.na(start) || is.na(width)) {
      next
    }

    end_pos <- start + width - 1L

    rows[[idx]] <- data.frame(
      sequence_id = seq_id,
      motif_id    = motif_id,
      start       = start,
      end         = end_pos,
      stringsAsFactors = FALSE
    )
    idx <- idx + 1L
  }

  rows <- rows[!vapply(rows, is.null, logical(1))]
  if (length(rows) == 0) {
    stop("在 MEME XML 中找到了位点标签，但关键属性 (sequence/name/position/length) 不完整，无法解析。",
         call. = FALSE)
  }

  motif_df <- dplyr::bind_rows(rows)

  ## 挂上 seq_map，后面画主干线和缩放要用
  attr(motif_df, "seq_map") <- seq_map

  return(motif_df)
}

## ===================== 4. 主绘图函数 =====================

plot_motif_from_meme <- function(treefile, meme_xml_file) {

  ## ---------- 4.1 读树并获取 tip 顺序 ----------
  tree <- ape::read.tree(treefile)
  tip_order <- tree$tip.label

  ## ---------- 4.2 解析 MEME XML 得到 motif 位点 + 序列映射 ----------
  motif_raw <- parse_meme_xml(meme_xml_file)
  seq_map   <- attr(motif_raw, "seq_map")

  cat("解析到 motif 位点总数: ", nrow(motif_raw), "\n")
  cat("解析到的序列 ID 示例: \n")
  print(head(unique(motif_raw$sequence_id), 5))

  ## ---------- 4.3 基因主干线数据（所有出现于树 + MEME 的基因） ----------
  if (!is.null(seq_map) && nrow(seq_map) > 0) {
    gene_df <- seq_map %>%
      filter(seq_name %in% tip_order)

    if (nrow(gene_df) == 0) {
      stop("MEME XML 中的 <sequence name> 与树的 tip.label 没有交集，无法画基因主干线。",
           call. = FALSE)
    }

    ## 统一一个“绘图用的最大长度”，用于等长显示
    if (all(is.na(gene_df$seq_length))) {
      global_len <- max(motif_raw$end, na.rm = TRUE)
    } else {
      global_len <- max(gene_df$seq_length, na.rm = TRUE)
    }

    gene_df$sequence_id <- factor(
      gene_df$seq_name,
      levels = rev(tip_order)
    )
    gene_df <- gene_df %>%
      mutate(
        y_num      = as.numeric(sequence_id),
        gene_start = 0,
        gene_end   = global_len,
        seq_length = ifelse(is.na(seq_length), global_len, seq_length)
      )
  } else {
    stop("没有可用的 sequence 映射信息，无法绘制基因主干线。", call. = FALSE)
  }

  ## ---------- 4.4 motif 位点：只保留在树上的，并做等比例缩放 ----------
  motif_df <- motif_raw %>%
    filter(sequence_id %in% gene_df$seq_name)

  if (nrow(motif_df) == 0) {
    warning("没有任何 motif 位点的 sequence_id 能和树的 tip.label 对上，只画基因主干线。")
    motif_df <- NULL
  } else {
    motif_df <- motif_df %>%
      left_join(
        gene_df %>% select(seq_name = seq_name, seq_length),
        by = c("sequence_id" = "seq_name")
      )

    motif_df <- motif_df %>%
      mutate(
        seq_length = ifelse(is.na(seq_length), global_len, seq_length),
        start_scaled = start / seq_length * global_len,
        end_scaled   = end   / seq_length * global_len
      )

    motif_df$sequence_id <- factor(
      motif_df$sequence_id,
      levels = levels(gene_df$sequence_id)  # 与 gene_df 一致
    )
    motif_df <- motif_df %>%
      mutate(y_num = as.numeric(sequence_id))
  }

  ## ---------- 4.5 自动为每个 motif 配色 ----------
  if (!is.null(motif_df)) {
    motif_levels <- sort(unique(motif_df$motif_id))
    n_motifs     <- length(motif_levels)

    if (n_motifs == 0) {
      motif_cols <- NULL
    } else if (n_motifs <= 12) {
      base_cols  <- RColorBrewer::brewer.pal(max(3, n_motifs), "Set3")
      motif_cols <- setNames(base_cols[seq_len(n_motifs)], motif_levels)
    } else {
      base_cols  <- RColorBrewer::brewer.pal(12, "Set3")
      motif_cols <- colorRampPalette(base_cols)(n_motifs)
      names(motif_cols) <- motif_levels
    }
  } else {
    motif_cols <- NULL
  }

  ## ---------- 4.6 画图：等长基因主干线 + motif 矩形色块 ----------
  p <- ggplot() +
    ## 基因主干线（灰色小矩形，所有长度统一）
    geom_rect(
      data = gene_df,
      aes(
        xmin = gene_start,
        xmax = gene_end,
        ymin = y_num - 0.15,
        ymax = y_num + 0.15
      ),
      fill  = "grey80",
      color = NA
    )

  if (!is.null(motif_df) && !is.null(motif_cols)) {
    p <- p +
      geom_rect(
        data = motif_df,
        aes(
          xmin = start_scaled,
          xmax = end_scaled,
          ymin = y_num - 0.4,
          ymax = y_num + 0.4,
          fill = motif_id
        )
      ) +
      scale_fill_manual(
        values = motif_cols,
        name   = "Motif"
      )
  }

  p <- p +
    labs(x = "", y = "") +
    ggtitle("Motif Analysis") +
    gggenes::theme_genes() +
    theme(
      axis.text.y        = element_blank(),
      axis.ticks.y       = element_blank(),
      panel.grid         = element_blank(),   # 去掉多余水平线和网格线
      legend.background  = element_rect(color = "#969696", fill = "#d9d9d9"),
      plot.title         = element_text(hjust = 0.5)
    )

  return(p)
}

## ===================== 5. 调用函数并保存图像 =====================

motif_p <- plot_motif_from_meme(treefile, meme_xml)

ggsave(output_pdf, motif_p, width = 8, height = 10)

cat("已输出 motif 图像到: ", output_pdf, "\n")
