#!/usr/bin/env Rscript
suppressPackageStartupMessages({
  library(ggplot2)
  library(pheatmap)
  library(grid)
})

args <- commandArgs(trailingOnly = TRUE)
kv <- list()
for (arg in args) {
  if (grepl('=', arg, fixed = TRUE)) {
    parts <- strsplit(arg, '=', fixed = TRUE)[[1]]
    kv[[parts[1]]] <- paste(parts[-1], collapse = '=')
  }
}
required <- c('fpkm', 'tpm', 'vst', 'sample_table', 'de_dir', 'prefix', 'out_dir')
missing <- required[!required %in% names(kv) | !nzchar(unlist(kv[required]))]
if (length(missing) > 0) {
  stop('Missing required arguments: ', paste(missing, collapse = ', '))
}

dir.create(kv$out_dir, recursive = TRUE, showWarnings = FALSE)

read_matrix <- function(path) {
  mat <- read.table(path, header = TRUE, row.names = 1, sep = '\t', check.names = FALSE,
                    stringsAsFactors = FALSE, comment.char = '', quote = '')
  mat <- as.matrix(mat)
  storage.mode(mat) <- 'numeric'
  mat
}

read_samples <- function(path, sample_names) {
  tab <- read.table(path, header = TRUE, sep = '\t', stringsAsFactors = FALSE,
                    check.names = FALSE, comment.char = '', quote = '')
  names(tab) <- tolower(names(tab))
  sample_col <- if ('sample' %in% names(tab)) 'sample' else names(tab)[1]
  condition_col <- if ('condition' %in% names(tab)) 'condition' else names(tab)[min(2, ncol(tab))]
  out <- data.frame(sample = tab[[sample_col]], condition = tab[[condition_col]], stringsAsFactors = FALSE)
  out <- out[out$sample %in% sample_names, , drop = FALSE]
  missing_samples <- setdiff(sample_names, out$sample)
  if (length(missing_samples) > 0) {
    out <- rbind(out, data.frame(sample = missing_samples, condition = 'unknown', stringsAsFactors = FALSE))
  }
  out <- out[match(sample_names, out$sample), , drop = FALSE]
  rownames(out) <- out$sample
  out$condition <- factor(out$condition)
  out
}

save_plot <- function(plot, base, width = 8, height = 5) {
  png_file <- paste0(base, '.png')
  pdf_file <- paste0(base, '.pdf')
  ggsave(png_file, plot, width = width, height = height, dpi = 300)
  ggsave(pdf_file, plot, width = width, height = height)
  c(png_file, pdf_file)
}

save_pheatmap <- function(ph, base, width = 8, height = 10) {
  png_file <- paste0(base, '.png')
  pdf_file <- paste0(base, '.pdf')
  png(png_file, width = width * 300, height = height * 300, res = 300)
  grid.newpage(); grid.draw(ph$gtable)
  dev.off()
  pdf(pdf_file, width = width, height = height)
  grid.newpage(); grid.draw(ph$gtable)
  dev.off()
  c(png_file, pdf_file)
}

long_expr <- function(mat, label) {
  data.frame(
    gene_id = rep(rownames(mat), times = ncol(mat)),
    sample = rep(colnames(mat), each = nrow(mat)),
    value = as.numeric(mat),
    matrix = label,
    stringsAsFactors = FALSE
  )
}

fpkm <- read_matrix(kv$fpkm)
tpm <- read_matrix(kv$tpm)
vst <- read_matrix(kv$vst)
common_samples <- Reduce(intersect, list(colnames(fpkm), colnames(tpm), colnames(vst)))
if (length(common_samples) < 2) stop('Fewer than 2 common samples across FPKM/TPM/VST matrices')
fpkm <- fpkm[, common_samples, drop = FALSE]
tpm <- tpm[, common_samples, drop = FALSE]
vst <- vst[, common_samples, drop = FALSE]
samples <- read_samples(kv$sample_table, common_samples)
annotation_col <- data.frame(condition = samples$condition)
rownames(annotation_col) <- samples$sample

artifacts <- data.frame(artifact = character(), path = character(), stringsAsFactors = FALSE)
add_artifacts <- function(names, paths) {
  data.frame(artifact = names, path = paths, stringsAsFactors = FALSE)
}

# Full-matrix expression distribution plots. All genes are read and plotted as log2(x + 1).
for (entry in list(list(name = 'fpkm', mat = fpkm), list(name = 'tpm', mat = tpm))) {
  label <- toupper(entry$name)
  df <- long_expr(entry$mat, label)
  df$log_value <- log2(df$value + 1)
  df <- merge(df, data.frame(sample = samples$sample, condition = samples$condition), by = 'sample', all.x = TRUE)

  p_box <- ggplot(df, aes(x = sample, y = log_value, fill = condition)) +
    geom_boxplot(outlier.size = 0.15, linewidth = 0.25) +
    labs(x = NULL, y = paste0('log2(', label, ' + 1)'), title = paste0(label, ' distribution per sample')) +
    theme_bw(base_size = 10) +
    theme(axis.text.x = element_text(angle = 45, hjust = 1), legend.position = 'right')
  paths <- save_plot(p_box, file.path(kv$out_dir, paste0(kv$prefix, '.rnaseq_expression_plot.', entry$name, '_boxplot')), 8.5, 5)
  artifacts <- rbind(artifacts, add_artifacts(paste0(entry$name, '_boxplot_', c('png', 'pdf')), paths))

  p_density <- ggplot(df, aes(x = log_value, color = sample, group = sample)) +
    geom_density(linewidth = 0.5) +
    labs(x = paste0('log2(', label, ' + 1)'), y = 'Density', title = paste0(label, ' density per sample')) +
    theme_bw(base_size = 10) +
    theme(legend.position = 'right')
  paths <- save_plot(p_density, file.path(kv$out_dir, paste0(kv$prefix, '.rnaseq_expression_plot.', entry$name, '_density')), 8.5, 5)
  artifacts <- rbind(artifacts, add_artifacts(paste0(entry$name, '_density_', c('png', 'pdf')), paths))
}

# Top variable genes heatmap from VST matrix.
max_top <- as.integer(ifelse('top_n' %in% names(kv), kv$top_n, 100))
vars <- apply(vst, 1, var, na.rm = TRUE)
vars <- sort(vars[is.finite(vars)], decreasing = TRUE)
top_var_genes <- names(vars)[seq_len(min(max_top, length(vars)))]
top_var_mat <- vst[top_var_genes, , drop = FALSE]
write.table(data.frame(gene_id = top_var_genes, variance = vars[top_var_genes]),
            file = file.path(kv$out_dir, paste0(kv$prefix, '.rnaseq_expression_plot.top_variable_genes.tsv')),
            sep = '\t', quote = FALSE, row.names = FALSE)
ph <- pheatmap(top_var_mat, scale = 'row', annotation_col = annotation_col,
               show_rownames = length(top_var_genes) <= 100,
               main = paste0('Top ', length(top_var_genes), ' variable genes (VST)'),
               silent = TRUE)
paths <- save_pheatmap(ph, file.path(kv$out_dir, paste0(kv$prefix, '.rnaseq_expression_plot.top_variable_vst_heatmap')), 8, 10)
artifacts <- rbind(artifacts, add_artifacts(paste0('top_variable_vst_heatmap_', c('png', 'pdf')), paths))
artifacts <- rbind(artifacts, add_artifacts('top_variable_gene_table', file.path(kv$out_dir, paste0(kv$prefix, '.rnaseq_expression_plot.top_variable_genes.tsv'))))

# Top DE genes heatmaps from all full DESeq2 result tables.
de_files <- list.files(kv$de_dir, pattern = paste0('^', kv$prefix, '\\.?rnaseq_de\\..*\\.DE_results\\.txt$'), full.names = TRUE)
if (length(de_files) == 0) {
  de_files <- list.files(kv$de_dir, pattern = '\\.DE_results\\.txt$', full.names = TRUE)
}
summary_rows <- list()
for (de_file in sort(de_files)) {
  de <- read.table(de_file, header = TRUE, sep = '\t', stringsAsFactors = FALSE, check.names = FALSE,
                   comment.char = '', quote = '')
  required_cols <- c('gene_id', 'padj', 'log2FoldChange')
  if (!all(required_cols %in% colnames(de))) next
  de <- de[!is.na(de$padj) & !is.na(de$log2FoldChange), , drop = FALSE]
  de$abs_lfc <- abs(de$log2FoldChange)
  sig <- de[de$padj < 0.05 & de$abs_lfc >= 1, , drop = FALSE]
  sig <- sig[order(sig$padj, -sig$abs_lfc), , drop = FALSE]
  genes <- sig$gene_id[sig$gene_id %in% rownames(vst)]
  genes <- unique(genes)[seq_len(min(max_top, length(unique(genes))))]
  comparison <- sub(paste0('^', kv$prefix, '\\.?rnaseq_de\\.'), '', basename(de_file))
  comparison <- sub('\\.DE_results\\.txt$', '', comparison)
  comparison <- gsub('[^A-Za-z0-9_]+', '_', comparison)
  summary_rows[[comparison]] <- data.frame(comparison = comparison, significant_genes = nrow(sig), plotted_genes = length(genes))
  if (length(genes) == 0) next
  out_table <- file.path(kv$out_dir, paste0(kv$prefix, '.rnaseq_expression_plot.', comparison, '.top_de_genes.tsv'))
  out_cols <- intersect(c('gene_id', 'baseMean', 'log2FoldChange', 'padj', 'regulation'), colnames(sig))
  write.table(sig[sig$gene_id %in% genes, out_cols, drop = FALSE],
              file = out_table, sep = '\t', quote = FALSE, row.names = FALSE)
  ph <- pheatmap(vst[genes, , drop = FALSE], scale = 'row', annotation_col = annotation_col,
                 show_rownames = length(genes) <= 100,
                 main = paste0(comparison, ': top ', length(genes), ' DE genes (VST)'),
                 silent = TRUE)
  paths <- save_pheatmap(ph, file.path(kv$out_dir, paste0(kv$prefix, '.rnaseq_expression_plot.', comparison, '.top_de_vst_heatmap')), 8, 10)
  artifacts <- rbind(artifacts, add_artifacts(paste0(comparison, '_top_de_vst_heatmap_', c('png', 'pdf')), paths))
  artifacts <- rbind(artifacts, add_artifacts(paste0(comparison, '_top_de_gene_table'), out_table))
}
if (length(summary_rows) > 0) {
  summary_df <- do.call(rbind, summary_rows)
  write.table(summary_df, file = file.path(kv$out_dir, paste0(kv$prefix, '.rnaseq_expression_plot.de_heatmap_summary.tsv')),
              sep = '\t', quote = FALSE, row.names = FALSE)
  artifacts <- rbind(artifacts, add_artifacts('de_heatmap_summary', file.path(kv$out_dir, paste0(kv$prefix, '.rnaseq_expression_plot.de_heatmap_summary.tsv'))))
}

# Time-course normalized count trajectories. This follows the DESeq2 plotCounts
# idea: estimate size factors from raw counts, then plot normalized counts for
# genes selected from the time LRT result. It is only enabled when count_matrix
# is provided and sample conditions contain numeric time labels such as T0/T12/T24.
if ('count_matrix' %in% names(kv) && file.exists(kv$count_matrix)) {
  time_values <- suppressWarnings(as.numeric(gsub('[^0-9.+-]', '', as.character(samples$condition))))
  has_time <- all(is.finite(time_values)) && length(unique(time_values)) >= 3
  time_lrt_files <- list.files(kv$de_dir, pattern = paste0('^', kv$prefix, '\\.?rnaseq_de\\.time_lrt\\.DE_results\\.txt$'), full.names = TRUE)
  if (length(time_lrt_files) == 0) {
    time_lrt_files <- list.files(kv$de_dir, pattern = 'time_lrt\\.DE_results\\.txt$', full.names = TRUE)
  }
  if (has_time && length(time_lrt_files) > 0) {
    suppressPackageStartupMessages(library(DESeq2))
    count_mat <- read_matrix(kv$count_matrix)
    count_mat <- round(count_mat[, common_samples, drop = FALSE])
    count_mat[count_mat < 0 | is.na(count_mat)] <- 0
    coldata <- data.frame(row.names = common_samples, condition = samples$condition, time = time_values)
    dds_counts <- DESeqDataSetFromMatrix(countData = count_mat, colData = coldata, design = ~ condition)
    dds_counts <- estimateSizeFactors(dds_counts)
    norm_counts <- counts(dds_counts, normalized = TRUE)

    time_lrt <- read.table(time_lrt_files[1], header = TRUE, sep = '\t', stringsAsFactors = FALSE,
                           check.names = FALSE, comment.char = '', quote = '')
    if (all(c('gene_id', 'padj') %in% colnames(time_lrt))) {
      time_lrt <- time_lrt[!is.na(time_lrt$padj), , drop = FALSE]
      time_lrt <- time_lrt[order(time_lrt$padj, -time_lrt$baseMean), , drop = FALSE]
      max_time_genes <- as.integer(ifelse('time_top_n' %in% names(kv), kv$time_top_n, 12))
      time_genes <- unique(time_lrt$gene_id[time_lrt$gene_id %in% rownames(norm_counts)])
      time_genes <- time_genes[seq_len(min(max_time_genes, length(time_genes)))]
      if (length(time_genes) > 0) {
        norm_sub <- norm_counts[time_genes, , drop = FALSE]
        norm_long <- data.frame(
          gene_id = rep(rownames(norm_sub), times = ncol(norm_sub)),
          sample = rep(colnames(norm_sub), each = nrow(norm_sub)),
          normalized_count = as.numeric(norm_sub),
          stringsAsFactors = FALSE
        )
        norm_long <- merge(norm_long, data.frame(sample = samples$sample, condition = samples$condition, time = time_values),
                           by = 'sample', all.x = TRUE)
        norm_long$condition <- factor(norm_long$condition, levels = unique(samples$condition[order(time_values)]))
        time_gene_table <- time_lrt[match(time_genes, time_lrt$gene_id), , drop = FALSE]
        write.table(time_gene_table,
                    file = file.path(kv$out_dir, paste0(kv$prefix, '.rnaseq_expression_plot.time_lrt.top_timecourse_genes.tsv')),
                    sep = '\t', quote = FALSE, row.names = FALSE)
        write.table(norm_long,
                    file = file.path(kv$out_dir, paste0(kv$prefix, '.rnaseq_expression_plot.time_lrt.normalized_counts_long.tsv')),
                    sep = '\t', quote = FALSE, row.names = FALSE)
        p_time <- ggplot(norm_long, aes(x = time, y = normalized_count, color = condition)) +
          geom_point(size = 1.8, alpha = 0.85) +
          stat_summary(aes(group = gene_id), fun = mean, geom = 'line', color = 'grey25', linewidth = 0.45) +
          scale_y_log10() +
          facet_wrap(~ gene_id, scales = 'free_y', ncol = 4) +
          labs(x = 'Time', y = 'Normalized counts (DESeq2 size-factor scaled, log10 axis)',
               title = paste0('Top ', length(time_genes), ' time-LRT genes across time')) +
          theme_bw(base_size = 10) +
          theme(legend.position = 'none', strip.text = element_text(size = 7))
        paths <- save_plot(p_time, file.path(kv$out_dir, paste0(kv$prefix, '.rnaseq_expression_plot.time_lrt.normalized_count_trajectories')), 10, 8)
        artifacts <- rbind(artifacts, add_artifacts(paste0('time_lrt_normalized_count_trajectories_', c('png', 'pdf')), paths))
        artifacts <- rbind(artifacts, add_artifacts('time_lrt_top_timecourse_gene_table', file.path(kv$out_dir, paste0(kv$prefix, '.rnaseq_expression_plot.time_lrt.top_timecourse_genes.tsv'))))
        artifacts <- rbind(artifacts, add_artifacts('time_lrt_normalized_counts_long', file.path(kv$out_dir, paste0(kv$prefix, '.rnaseq_expression_plot.time_lrt.normalized_counts_long.tsv'))))
      }
    }
  }
}

manifest <- data.frame(
  key = c('fpkm_matrix', 'tpm_matrix', 'vst_matrix', 'sample_table', 'de_results_count', 'samples', 'genes_fpkm', 'genes_tpm', 'genes_vst', 'top_n'),
  value = c(kv$fpkm, kv$tpm, kv$vst, kv$sample_table, length(de_files), length(common_samples), nrow(fpkm), nrow(tpm), nrow(vst), max_top),
  stringsAsFactors = FALSE
)
write.table(manifest, file = file.path(kv$out_dir, paste0(kv$prefix, '.rnaseq_expression_plot.prototype_manifest.tsv')),
            sep = '\t', quote = FALSE, row.names = FALSE)
write.table(artifacts, file = file.path(kv$out_dir, paste0(kv$prefix, '.rnaseq_expression_plot.artifacts.tsv')),
            sep = '\t', quote = FALSE, row.names = FALSE)

cat('rnaseq_expression_plot_prototype complete\n')
cat('Output directory:', kv$out_dir, '\n')
cat('Artifacts:', nrow(artifacts), '\n')
