#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 5) stop("usage: coords coarse_bp fine_bp coarse_png fine_png", call. = FALSE)
suppressPackageStartupMessages(library(CMplot))
coords <- read.table(args[1], header = TRUE, sep = "\t", stringsAsFactors = FALSE, check.names = FALSE)
plot_data <- data.frame(SNP = coords$SNP, Chromosome = coords$PlotChromosome, Position = coords$Position)

png(args[4], width = 1800, height = 900, res = 180)
CMplot(plot_data, plot.type = "d", bin.size = as.integer(args[2]), LOG10 = FALSE, file.output = FALSE, verbose = FALSE)
dev.off()

png(args[5], width = 1800, height = 900, res = 180)
CMplot(plot_data, plot.type = "d", bin.size = as.integer(args[3]), LOG10 = FALSE, file.output = FALSE, verbose = FALSE)
dev.off()
