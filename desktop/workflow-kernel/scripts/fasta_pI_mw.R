#!/usr/bin/env Rscript
args <- commandArgs(TRUE)
if (length(args) != 2) {
  print("usage: <pep.fasta>   <outfile> ")
  q(status = 1)
}
suppressPackageStartupMessages({
  library(seqinr)
  library(Peptides)
})
fasta <- read.fasta(args[1], seqtype = "AA", as.string = TRUE)
fasta_out <- data.frame(ID = names(fasta), Length = sapply(fasta, lengthpep), MolWt = round(sapply(fasta, mw), 2), pI = round(sapply(fasta, pI, pKscale = "EMBOSS"), 2))
write.table(fasta_out, file = args[2], row.names = FALSE, sep = "	", quote = FALSE)
