#!/usr/bin/env Rscript

## =========================================================
## plot_structure.R
## Panel A: Phylogenetic tree (gene ID + bootstrap) - 左侧
## Panel B: Subfamily vertical bars - 中间
## Panel C: Gene structure (CDS + UTR) - 右侧
## =========================================================

args <- commandArgs(trailingOnly = TRUE)

get_arg <- function(flag, default = NA_character_) {
  i <- match(flag, args)
  if (!is.na(i) && i < length(args)) return(args[i + 1])
  default
}

## ===================== 1) Parse args =====================
treefile      <- get_arg("--tree")
structure_tsv <- get_arg("--structure")
out_raw       <- get_arg("--output")

subfamily_cli <- get_arg("--subfamily", NA_character_)
root_cli      <- get_arg("--root", NA_character_)

width_out  <- suppressWarnings(as.numeric(get_arg("--width",  "16")))
height_out <- suppressWarnings(as.numeric(get_arg("--height", "10")))
dpi_out    <- suppressWarnings(as.numeric(get_arg("--dpi",    "300")))

if (is.na(treefile) || !nzchar(treefile) ||
    is.na(structure_tsv) || !nzchar(structure_tsv) ||
    is.na(out_raw) || !nzchar(out_raw)) {
  stop("用法:\n  Rscript plot_structure.R --tree <tree.nwk> --structure <structure.tsv> --output <out>\n", call. = FALSE)
}

cat("[INFO] tree:      ", treefile, "\n")
cat("[INFO] structure: ", structure_tsv, "\n")
cat("[INFO] output:    ", out_raw, "\n\n")

## ===================== 2) Load packages =====================

script_args_all <- commandArgs(trailingOnly = FALSE)
script_file_arg <- grep("^--file=", script_args_all, value = TRUE)
script_dir <- if (length(script_file_arg) > 0) dirname(normalizePath(sub("^--file=", "", script_file_arg[1]))) else getwd()
source(file.path(script_dir, "subfamily_palette.R"))

suppressPackageStartupMessages({
  library(ape)
  library(ggtree)
  library(tidytree)
  library(dplyr)
  library(ggplot2)
  library(ggnewscale)
})

## ===================== 3) Small utilities =====================

fix_output_ext <- function(x, default_ext = "pdf") {
  ext <- tolower(tools::file_ext(x))
  ok  <- ext %in% c("pdf", "png", "svg", "tif", "tiff", "jpg", "jpeg")
  if (!ok) x <- paste0(x, ".", default_ext)
  x
}

out_pdf <- fix_output_ext(out_raw, default_ext = "pdf")
out_png <- paste0(tools::file_path_sans_ext(out_pdf), ".png")

normalize_gene <- function(x) {
  sub("\\.[Tt][0-9]+.*$", "", x)
}

infer_job_id <- function(p) {
  if (is.na(p) || !nzchar(p)) return(NA_character_)
  # 支持两种格式：旧格式 20260420_093710_12345678_abcdef12 和新格式 job_1776593224607
  m <- regexpr("/(workspace|input)/((?:[0-9]{8}_[0-9]{6}_[0-9]+_[a-f0-9]+|job_[0-9]+))", p, perl = TRUE)
  if (m[1] == -1) return(NA_character_)
  sub(".*/(workspace|input)/((?:[0-9]{8}_[0-9]{6}_[0-9]+_[a-f0-9]+|job_[0-9]+)).*$", "\\2", p)
}

infer_root <- function(p) {
  if (is.na(p) || !nzchar(p)) return(NA_character_)
  if (grepl("/workspace/", p)) {
    return(sub("^(.*)/workspace/.*$", "\\1", p))
  }
  if (grepl("/input/", p)) {
    return(sub("^(.*)/input/.*$",  "\\1", p))
  }
  NA_character_
}

clean_path <- function(p) {
  if (length(p) == 0) return(NA_character_)
  vapply(p, function(x) {
    if (is.na(x) || !nzchar(x)) return(NA_character_)
    x <- sub("/+$", "", x)
    x <- gsub("//+", "/", x)
    x
  }, character(1))
}


## ===================== 4) Find subfamily file =====================

tree_base  <- basename(treefile)
prefix     <- sub("\\.[^.]*$", "", tree_base)
candidate1 <- paste0(prefix, ".geneID.subfamily")
candidate2 <- if (grepl("\\.tree$", prefix)) {
  paste0(sub("\\.tree$", "", prefix), ".geneID.subfamily")
} else {
  NULL
}

job_id <- infer_job_id(treefile)
if (is.na(job_id)) job_id <- infer_job_id(structure_tsv)
if (is.na(job_id)) job_id <- infer_job_id(out_pdf)

root_dir <- if (!is.na(root_cli) && nzchar(root_cli)) root_cli else infer_root(treefile)
if (is.na(root_dir)) root_dir <- infer_root(structure_tsv)

pick_newest <- function(paths) {
  paths <- paths[!is.na(paths) & nzchar(paths) & file.exists(paths)]
  if (length(paths) == 0) return(NA_character_)
  info <- file.info(paths)
  paths[which.max(info$mtime)]
}

subfamily_file <- NA_character_

if (!is.na(subfamily_cli) && nzchar(subfamily_cli)) {
  if (!file.exists(subfamily_cli)) stop("你指定的 --subfamily 不存在：", subfamily_cli, call. = FALSE)
  subfamily_file <- clean_path(subfamily_cli)
} else {
  all_candidates <- character()
  if (!is.na(root_dir) && !is.na(job_id)) {
    root_dir <- clean_path(root_dir)

    all_candidates <- c(all_candidates,
      clean_path(file.path(root_dir, "workspace", job_id, "final", candidate1)),
      clean_path(file.path(root_dir, "input", job_id, candidate1))
    )

    if (!is.null(candidate2)) {
      all_candidates <- c(all_candidates,
        clean_path(file.path(root_dir, "workspace", job_id, "final", candidate2)),
        clean_path(file.path(root_dir, "input", job_id, candidate2))
      )
    }

    matches <- character()

    for (cand in all_candidates) {
      if (file.exists(cand)) matches <- c(matches, cand)
    }

    if (length(matches) == 0) {
      input_dir <- clean_path(file.path(root_dir, "input", job_id))
      if (dir.exists(input_dir)) {
        all_files <- list.files(input_dir, recursive = TRUE, full.names = TRUE)
        all_files <- clean_path(all_files)
        possible_names <- c(candidate1, candidate2)
        possible_names <- possible_names[!is.null(possible_names)]
        for (fname in possible_names) {
          found <- all_files[basename(all_files) == fname]
          if (length(found) > 0) matches <- c(matches, found)
        }
      }
    }

    subfamily_file <- pick_newest(unique(matches))
  }
}

cat("[INFO] job_id:    ", job_id, "\n")
cat("[INFO] root_dir:  ", root_dir, "\n")
cat("[INFO] subfamily: ", subfamily_file, "\n\n")

## ===================== 5) Read structure TSV =====================

read_structure <- function(f) {
  df <- tryCatch(
    read.delim(f, sep = "\t", header = TRUE,
               quote = "", comment.char = "",
               stringsAsFactors = FALSE, check.names = FALSE),
    error = function(e) NULL
  )
  if (is.null(df) || ncol(df) < 4) {
    stop("structure.tsv 读入失败或列数不足（应为TAB分隔且至少4列）。", call. = FALSE)
  }
  df
}

st <- read_structure(structure_tsv)

need_cols <- c("gene_id", "feature", "start", "end")
miss <- setdiff(need_cols, colnames(st))
if (length(miss) > 0) stop("structure.tsv 缺少必要列：", paste(miss, collapse = ", "), call. = FALSE)

st$start <- suppressWarnings(as.numeric(st$start))
st$end   <- suppressWarnings(as.numeric(st$end))
if (any(is.na(st$start)) || any(is.na(st$end))) stop("structure.tsv 的 start/end 有非数字。", call. = FALSE)

st$gene_key <- normalize_gene(st$gene_id)
st$Type <- dplyr::case_when(
  st$feature == "CDS" ~ "CDS",
  st$feature %in% c("UTR","five_prime_UTR","three_prime_UTR","5UTR","3UTR") ~ "UTR",
  TRUE ~ NA_character_
)

## ===================== 6) Read subfamily file + colors =====================

read_subfamily <- function(f) {
  df <- read_subfamily_plot_table(f)
  df$gene_key <- normalize_gene(df$label)
  df <- add_subfamily_confidence_labels(df, f)

  dup <- df$gene_key[duplicated(df$gene_key)]
  if (length(dup) > 0) {
    warning("subfamily 文件存在重复 gene（归一化后），将保留第一条：", paste(unique(head(dup, 10)), collapse = ", "))
    df <- df[!duplicated(df$gene_key), ]
  }
  subfam_levels <- sort(unique(df$subfamily))
  colmap <- make_subfamily_cols(subfam_levels)
  list(df = df, colmap = colmap)
}


if (is.na(subfamily_file)) {
  tree_for_default <- ape::read.tree(treefile)
  subfam_df <- data.frame(
    label = tree_for_default$tip.label,
    subfamily = "NAC",
    low_confidence = FALSE,
    display_label = tree_for_default$tip.label,
    stringsAsFactors = FALSE
  )
  subfam_df$gene_key <- normalize_gene(subfam_df$label)
  subfam_cols <- c(NAC = "#4D4D4D")
  cat("[INFO] subfamily:  not provided; using one default group for all tips\n\n")
} else {
  sf <- read_subfamily(subfamily_file)
  subfam_df   <- sf$df
  subfam_cols <- sf$colmap
}

## ===================== 7) Plot tree (Panel A) =====================

plot_iqtree_by_subfamily <- function(treefile, subfam_df, subfam_cols, label_offset = 0.25) {
  tree <- ape::read.tree(treefile)
  tree_df <- tidytree::as_tibble(tree)
  tree_df$isTip <- tree_df$node <= length(tree$tip.label)
  tree_df$gene_key <- ifelse(tree_df$isTip, normalize_gene(tree_df$label), NA_character_)
  tree_df <- dplyr::left_join(tree_df, subfam_df[, c("gene_key","subfamily","display_label")], by = "gene_key")
  tree_df$display_label <- ifelse(is.na(tree_df$display_label), tree_df$label, tree_df$display_label)
  tree_df$support <- ifelse(
    tree_df$isTip,
    NA_real_,
    suppressWarnings(as.numeric(tree_df$label))
  )

  p <- ggtree(tree, branch.length = "none") %<+% tree_df +
    geom_nodepoint(
      aes(fill = cut(support, c(0, 50, 75, 100))),
      shape = 21, size = 2, na.rm = TRUE
    ) +
    scale_fill_manual(
      values = c("black", "grey40", "grey80"),
      guide  = "legend",
      name   = "Bootstrap Percentage (BP)",
      breaks = c("(75,100]", "(50,75]", "(0,50]"),
      labels = expression(BP > 75, 50 < BP * "<=75", BP <= 50)
    ) +
    geom_text2(
      aes(label = ifelse(!isTip & !is.na(support), round(support), "")),
      hjust = -0.2, vjust = -0.4, size = 2.2,
      na.rm = TRUE, show.legend = FALSE
    ) +
    geom_tippoint(aes(color = subfamily), size = 2, na.rm = TRUE) +
    scale_color_manual(values = subfam_cols, name = "Subfamily", na.value = "grey70") +
    geom_tiplab(aes(label = display_label), offset = label_offset, size = 3, hjust = 0) +
    theme_tree() +
    theme(
      legend.position   = "right",
      legend.background = element_rect(color = "#969696", fill = "#d9d9d9")
    )

  attr(p, "label_offset") <- label_offset
  p
}

label_offset <- 0.25
p_tree <- plot_iqtree_by_subfamily(treefile, subfam_df, subfam_cols, label_offset = label_offset)

tree_dat <- p_tree$data
tip_dat  <- tree_dat %>%
  dplyr::filter(isTip) %>%
  dplyr::select(label, x, y, subfamily)

if (nrow(tip_dat) == 0) stop("树中没有 tip 节点。", call. = FALSE)

## ===================== 8) Build subfamily vertical bars (Panel B) =====================

subfam_ranges <- make_subfamily_ranges(tip_dat)

## ===================== 9) Align gene structure to tree tips =====================

tip_dat$gene_key <- normalize_gene(tip_dat$label)

st2 <- st %>%
  dplyr::filter(!is.na(Type)) %>%
  dplyr::inner_join(tip_dat %>% dplyr::select(gene_key, y), by = "gene_key")

if (nrow(st2) == 0) stop("structure.tsv（CDS/UTR）与树 tip（归一化后）无交集。", call. = FALSE)

gene_ranges <- st2 %>%
  dplyr::group_by(gene_key, y) %>%
  dplyr::summarise(
    gene_start = min(pmin(start, end), na.rm = TRUE),
    gene_end   = max(pmax(start, end), na.rm = TRUE),
    gene_len   = gene_end - gene_start + 1,
    .groups = "drop"
  )

feat_df <- st2 %>%
  dplyr::left_join(gene_ranges %>% dplyr::select(gene_key, gene_start), by = "gene_key") %>%
  dplyr::mutate(
    rel_start = pmin(start, end) - gene_start,
    rel_end   = pmax(start, end) - gene_start
  )

## ===================== 10) Panel layout (A + B + C) =====================

## 获取 Panel A 的实际范围
x_tree_min <- min(tree_dat$x, na.rm = TRUE)
x_tree_max <- max(tree_dat$x, na.rm = TRUE)
x_tip_max  <- max(tip_dat$x,   na.rm = TRUE)
max_y      <- max(tree_dat$y,  na.rm = TRUE)

## 计算基因标签的实际宽度
tip_labels <- tip_dat$label
max_label_len <- max(nchar(tip_labels), na.rm = TRUE)
char_width <- 0.12
label_extra_width <- max_label_len * char_width

## Panel A 范围（树 + 标签）
x_A_left  <- x_tree_min
x_A_right <- x_tip_max + label_offset + label_extra_width + 0.5

## Panel B 范围（subfamily bars）
gap_AB <- 2
width_B <- 3
x_B_left  <- x_A_right + gap_AB
x_B_right <- x_B_left + width_B
x_subfam  <- (x_B_left + x_B_right) / 2  # subfamily 条柱放在 Panel B 中间

## Panel C 范围（gene structure）
gap_BC <- 0.8
width_C <- max_gene_len <- max(gene_ranges$gene_len, na.rm = TRUE)
## 设置基因结构宽度比例（每1000bp对应1个单位宽度）
bp_per_unit <- 500
width_C <- max(5, max_gene_len / bp_per_unit)

x_C_left  <- x_B_right + gap_BC
x_C_right <- x_C_left + width_C

scale_gene <- width_C / max_gene_len

## 构建基因结构数据
gene_tracks <- gene_ranges %>%
  dplyr::mutate(
    x_gene_start = x_C_left,
    x_gene_end   = x_C_left + gene_len * scale_gene
  )

feat_rect <- feat_df %>%
  dplyr::mutate(
    xmin = x_C_left + rel_start * scale_gene,
    xmax = x_C_left + rel_end   * scale_gene,
    ymin = y - 0.25,
    ymax = y + 0.25
  )

gene_palette <- c(CDS = "#1b9e77", UTR = "#d95f02")

## 标题位置
tree_header_x <- (x_A_left + x_A_right) / 2
subf_header_x <- (x_B_left + x_B_right) / 2
gene_header_x <- (x_C_left + x_C_right) / 2
header_y <- max_y + 1.5

## ===================== 11) Combine plot =====================

p_combo <- p_tree +
  ## Panel B: subfamily bars + labels
  geom_segment(
    data = subfam_ranges,
    inherit.aes = FALSE,
    aes(x = x_subfam, xend = x_subfam, y = ymin, yend = ymax, colour = subfamily),
    linewidth = 2,
    show.legend = FALSE
  ) +
  geom_text(
    data = subfam_ranges,
    inherit.aes = FALSE,
    aes(x = x_subfam + 0.15, y = ymid, label = subfamily, colour = subfamily),
    hjust = 0,
    size = 3.5,
    show.legend = FALSE
  ) +
  
  ## Panel C: gene tracks + CDS/UTR
  ggnewscale::new_scale_fill() +
  geom_segment(
    data = gene_tracks,
    inherit.aes = FALSE,
    aes(x = x_gene_start, xend = x_gene_end, y = y, yend = y),
    colour = "grey70",
    linewidth = 0.4
  ) +
  geom_rect(
    data = feat_rect,
    inherit.aes = FALSE,
    aes(xmin = xmin, xmax = xmax, ymin = ymin, ymax = ymax, fill = Type),
    color = NA
  ) +
  scale_fill_manual(values = gene_palette, name = "Gene Structure") +

  ## 标题
  annotate("text", x = tree_header_x, y = header_y, label = "Phylogenetic tree",
           fontface = "bold", size = 4) +
  annotate("text", x = subf_header_x, y = header_y, label = "Subfamily",
           fontface = "bold", size = 4) +
  annotate("text", x = gene_header_x, y = header_y, label = "Gene structure",
           fontface = "bold", size = 4) +

  ## 设置坐标范围
  coord_cartesian(xlim = c(x_A_left - 0.5, x_C_right + 1)) +
  expand_limits(y = header_y + 0.5) +
  theme(
    legend.position   = "right",
    legend.background = element_rect(color = "#969696", fill = "#d9d9d9")
  )

## ===================== 12) Save outputs =====================

# 保存PDF
ggsave(out_pdf, p_combo, width = width_out, height = height_out)

# 保存PNG（使用png()函数避免conda R的API冲突）
png(filename = out_png, width = width_out * dpi_out, height = height_out * dpi_out, res = dpi_out)
print(p_combo)
dev.off()

cat("[OK] PDF: ", out_pdf, "\n", sep = "")
cat("[OK] PNG: ", out_png, "\n", sep = "")
cat("[OK] Subfamily used: ", subfamily_file, "\n", sep = "")
