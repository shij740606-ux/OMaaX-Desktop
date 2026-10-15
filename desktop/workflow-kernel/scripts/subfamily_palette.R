## Shared subfamily palette for tree/motif/domain/structure/combo plots.
## The mapping is deterministic by sorted subfamily label, so the same
## subfamily receives the same color in every downstream figure.

subfamily_palette_base <- c(
  "#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00",
  "#56B4E9", "#882255", "#44AA99", "#AA4499", "#117733",
  "#332288", "#DDCC77", "#88CCEE", "#999933", "#661100",
  "#6699CC", "#888888", "#AA4466", "#4477AA", "#228833"
)

make_subfamily_cols <- function(subfamily_levels) {
  lev <- sort(unique(as.character(subfamily_levels)))
  lev <- lev[!is.na(lev) & nzchar(lev)]
  n <- length(lev)
  if (n <= 0) return(setNames(character(), character()))
  if (n <= length(subfamily_palette_base)) {
    cols <- subfamily_palette_base[seq_len(n)]
  } else if (exists("hcl.colors", where = asNamespace("grDevices"), inherits = FALSE)) {
    cols <- grDevices::hcl.colors(n, palette = "Dark 3")
  } else {
    cols <- grDevices::colorRampPalette(subfamily_palette_base)(n)
  }
  setNames(cols, lev)
}

source_subfamily_palette <- function() invisible(TRUE)


read_subfamily_delimited <- function(path, header = NA) {
  lines <- readLines(path, warn = FALSE)
  lines <- lines[nzchar(trimws(lines)) & !grepl("^\\s*#", lines)]
  if (length(lines) == 0) return(data.frame(stringsAsFactors = FALSE))
  sep <- if (grepl("\t", lines[[1]], fixed = TRUE)) "\t" else ","
  first <- trimws(strsplit(lines[[1]], sep, fixed = TRUE)[[1]])
  header_tokens <- tolower(first)
  has_header <- any(header_tokens %in% c(
    "gene_id", "gene", "id", "target_gene", "label",
    "subfamily", "confirmed_subfamily", "predicted_subfamily",
    "assigned_subfamily", "confidence"
  ))
  if (is.na(header)) header <- has_header
  utils::read.table(
    path,
    header = header,
    sep = sep,
    quote = "",
    comment.char = "",
    stringsAsFactors = FALSE,
    check.names = FALSE,
    fill = TRUE
  )
}

read_subfamily_plot_table <- function(path) {
  df <- read_subfamily_delimited(path, header = NA)
  if (nrow(df) == 0 || ncol(df) < 2) {
    stop("subfamily 文件应至少包含 geneID 和 subfamily 两列", call. = FALSE)
  }
  names_lower <- tolower(colnames(df))
  label_col <- match(TRUE, names_lower %in% c("gene_id", "gene", "id", "target_gene", "label"))
  subfamily_col <- match(TRUE, names_lower %in% c("confirmed_subfamily", "subfamily", "assigned_subfamily", "predicted_subfamily", "prediction"))
  if (is.na(label_col)) label_col <- 1
  if (is.na(subfamily_col)) subfamily_col <- 2
  out <- data.frame(
    label = as.character(df[[label_col]]),
    subfamily = as.character(df[[subfamily_col]]),
    stringsAsFactors = FALSE
  )
  out <- out[nzchar(trimws(out$label)) & nzchar(trimws(out$subfamily)), , drop = FALSE]
  out
}

subfamily_confidence_candidates <- function(subfamily_file) {
  unique(c(
    sub("\\.subfamily\\.tsv$", ".assignment.tsv", subfamily_file),
    sub("\\.subfamily\\.tsv$", ".confidence.tsv", subfamily_file),
    sub("\\.confirmed_subfamily\\.tsv$", ".confirmed_subfamily.review.tsv", subfamily_file),
    sub("\\.merged_subfamily\\.tsv$", ".merged_subfamily.review.tsv", subfamily_file),
    sub("\\.geneID\\.subfamily$", ".reference_classification.confidence.tsv", subfamily_file),
    sub("\\.geneID\\.subfamily$", ".reference_classification.assignment.tsv", subfamily_file)
  ))
}

add_subfamily_confidence_labels <- function(subfam_df, subfamily_file) {
  subfam_df$low_confidence <- FALSE
  subfam_df$uncertain_confidence <- FALSE
  subfam_df$display_label <- subfam_df$label

  if (grepl("confirmed_subfamily", basename(subfamily_file))) {
    cat("确认亚家族文件: 不添加不确定性 * 标注\n")
    return(subfam_df)
  }

  low_targets <- character()
  for (confidence_file in subfamily_confidence_candidates(subfamily_file)) {
    if (identical(confidence_file, subfamily_file) || !file.exists(confidence_file)) next
    conf_df <- tryCatch(read_subfamily_delimited(confidence_file, header = NA), error = function(e) NULL)
    if (is.null(conf_df) || nrow(conf_df) == 0) next
    names_lower <- tolower(colnames(conf_df))
    gene_col <- match(TRUE, names_lower %in% c("gene_id", "target_gene", "gene", "id", "label"))
    confidence_col <- match(TRUE, names_lower %in% c("confidence", "score"))
    if (is.na(gene_col) || is.na(confidence_col)) next
    confidence <- tolower(trimws(as.character(conf_df[[confidence_col]])))
    genes <- as.character(conf_df[[gene_col]])
    low_targets <- c(low_targets, genes[confidence %in% c("medium", "low", "uncertain")])
  }

  low_targets <- unique(low_targets[nzchar(low_targets)])
  if (length(low_targets) > 0) {
    subfam_df$low_confidence <- subfam_df$label %in% low_targets
    subfam_df$uncertain_confidence <- subfam_df$low_confidence
    subfam_df$display_label <- ifelse(subfam_df$low_confidence, paste0(subfam_df$label, "*"), subfam_df$label)
  }
  cat("不确定亚家族标记: ", sum(subfam_df$low_confidence), " 个 tip 使用 * 标注\n", sep = "")
  subfam_df
}


make_subfamily_ranges <- function(tip_dat) {
  if (is.null(tip_dat) || nrow(tip_dat) == 0 || !("subfamily" %in% colnames(tip_dat))) {
    return(data.frame(subfamily = character(), ymin = numeric(), ymax = numeric(), ymid = numeric(), stringsAsFactors = FALSE))
  }
  df <- tip_dat[!is.na(tip_dat$subfamily) & nzchar(as.character(tip_dat$subfamily)), , drop = FALSE]
  if (nrow(df) == 0) {
    return(data.frame(subfamily = character(), ymin = numeric(), ymax = numeric(), ymid = numeric(), stringsAsFactors = FALSE))
  }
  df <- df[order(df$y), , drop = FALSE]
  subfam <- as.character(df$subfamily)
  run_id <- cumsum(c(TRUE, subfam[-1] != subfam[-length(subfam)]))
  df$subfamily_run_id <- run_id
  out <- dplyr::summarise(
    dplyr::group_by(df, subfamily_run_id, subfamily),
    ymin = min(y),
    ymax = max(y),
    ymid = (ymin + ymax) / 2,
    tip_count = dplyr::n(),
    .groups = "drop"
  )
  out$subfamily_run_label <- out$subfamily
  out
}

make_circular_subfamily_arc_paths <- function(tip_dat, n_tips, arc_radius, label_radius = arc_radius + 0.45, points_per_tip = 4) {
  ranges <- make_subfamily_ranges(tip_dat)
  if (nrow(ranges) == 0 || !is.finite(arc_radius) || arc_radius <= 0) {
    empty_paths <- data.frame(subfamily = character(), subfamily_run_id = integer(), x = numeric(), y = numeric(), stringsAsFactors = FALSE)
    empty_labels <- data.frame(subfamily = character(), subfamily_run_id = integer(), x = numeric(), y = numeric(), angle = numeric(), hjust = numeric(), stringsAsFactors = FALSE)
    return(list(paths = empty_paths, labels = empty_labels, ranges = ranges))
  }

  path_list <- vector("list", nrow(ranges))
  label_list <- vector("list", nrow(ranges))
  for (i in seq_len(nrow(ranges))) {
    ymin <- max(0.5, ranges$ymin[i] - 0.45)
    ymax <- min(n_tips + 0.5, ranges$ymax[i] + 0.45)
    tip_count <- max(1, ranges$tip_count[i])
    n_points <- max(12, ceiling(tip_count * points_per_tip))
    y_seq <- seq(ymin, ymax, length.out = n_points)
    run_id <- if ("subfamily_run_id" %in% names(ranges)) ranges$subfamily_run_id[i] else i

    path_list[[i]] <- data.frame(
      subfamily = ranges$subfamily[i],
      subfamily_run_id = run_id,
      x = arc_radius,
      y = y_seq,
      stringsAsFactors = FALSE
    )

    y_mid <- ranges$ymid[i]
    theta <- 360 * y_mid / n_tips
    tangent_angle <- theta - 90
    if (theta > 90 && theta < 270) tangent_angle <- tangent_angle + 180
    label_list[[i]] <- data.frame(
      subfamily = ranges$subfamily[i],
      subfamily_run_id = run_id,
      x = label_radius,
      y = y_mid,
      angle = tangent_angle,
      hjust = 0.5,
      stringsAsFactors = FALSE
    )
  }

  list(
    paths = do.call(rbind, path_list),
    labels = do.call(rbind, label_list),
    ranges = ranges
  )
}

