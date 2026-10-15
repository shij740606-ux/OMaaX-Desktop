#!/usr/bin/env Rscript
suppressPackageStartupMessages({
  library(argparser, quietly = TRUE)
  library(ggplot2)
  library(ggseqlogo)
})
parser <- arg_parser("draw motif seqlogo for meme result.")
parser <- add_argument(parser, "input", help = "input file, protein motif sequence table", type = "character")
parser <- add_argument(parser, "outpre", help = "output figure prefix", type = "character")
argv <- parse_args(parser)
data <- read.table(file = argv$input, header = FALSE, stringsAsFactors = FALSE)
motif_names <- unique(data[, 5])
all_motif_list <- list()
for (motif in motif_names) {
  seqs <- as.character(data[data[, 5] == motif, 4])
  seq_lengths <- nchar(seqs)
  if (length(unique(seq_lengths)) > 1) {
    min_len <- min(seq_lengths)
    message("Notice: Motif ", motif, " has different sequence lengths; trimming to ", min_len)
    seqs <- substr(seqs, 1, min_len)
  }
  all_motif_list[[motif]] <- seqs
  pdf(paste(argv$outpre, motif, "seqlogo.pdf", sep = "."), width = 15, height = 5)
  print(ggseqlogo(seqs) + theme_logo())
  dev.off()
  message("Finished motif: ", motif)
}
if (length(all_motif_list) > 0) {
  plot_height <- length(all_motif_list) * 1.8
  pdf(paste(argv$outpre, "allmotif.seqlogo.pdf", sep = "."), width = 15, height = max(4, plot_height))
  print(ggseqlogo(all_motif_list, ncol = 1) + theme_logo())
  dev.off()
} else {
  message("Warning: no motif sequences found; skipped combined plot")
}
