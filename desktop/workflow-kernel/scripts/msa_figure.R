#!/usr/bin/env Rscript
Sys.setenv(PATH = paste("/home/userlab/bin", Sys.getenv("PATH"), sep = ":"))
suppressPackageStartupMessages({
  library(argparser, quietly = TRUE)
  library(msa)
})
parser <- arg_parser("draw Multiple sequence alignment for proteins")
parser <- add_argument(parser, "fasta", help = "input: protein sequences in fasta format", type = "character")
parser <- add_argument(parser, "output", help = "output prefix", type = "character")
argv <- parse_args(parser)
pdf.options(useDingbats = FALSE, family = "Helvetica")
sequences <- readAAStringSet(argv$fasta)
alignment <- msaMuscle(sequences, order = "input")
msaPrettyPrint(alignment, output = "pdf", file = paste(argv$output, "msa_seqlogo.pdf", sep = "."), alFile = paste(argv$output, "msa_seqlogo.fasta", sep = "."), showNames = "left", showLogo = "top", logoColors = "chemical", shadingMode = "similar", showLegend = FALSE, askForOverwrite = FALSE)
