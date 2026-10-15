"""Publication-oriented plotting for PEP v2."""

from __future__ import annotations

import subprocess
from pathlib import Path

RSCRIPT_BIN = "Rscript"


def run_r_script(r_script_path: Path, timeout_sec: int = 180) -> None:
    result = subprocess.run(
        [RSCRIPT_BIN, str(r_script_path)],
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "R plotting failed")


def plot_protparam(data_tsv: Path, output_pdf: Path, output_png: Path, r_dir: Path) -> None:
    r_script = r_dir / "plot_protparam_v2.R"
    r_script.write_text(
        f"""
library(ggplot2)
library(patchwork)

data <- read.delim("{data_tsv}", sep="\\t", header=TRUE, check.names=FALSE)

p1 <- ggplot(data, aes(x = mw / 1000)) +
  geom_histogram(bins = min(15, max(5, nrow(data))), fill = "#4C72B0", color = "white", linewidth = 0.2) +
  labs(title = "Molecular Weight", x = "MW (kDa)", y = "Count") +
  theme_minimal(base_size = 10)

p2 <- ggplot(data, aes(x = pI)) +
  geom_histogram(bins = min(15, max(5, nrow(data))), fill = "#DD8452", color = "white", linewidth = 0.2) +
  labs(title = "Isoelectric Point", x = "pI", y = "Count") +
  theme_minimal(base_size = 10)

p3 <- ggplot(data, aes(x = length)) +
  geom_histogram(bins = min(15, max(5, nrow(data))), fill = "#55A868", color = "white", linewidth = 0.2) +
  labs(title = "Sequence Length", x = "Length (aa)", y = "Count") +
  theme_minimal(base_size = 10)

p4 <- ggplot(data, aes(x = gravy)) +
  geom_histogram(bins = min(15, max(5, nrow(data))), fill = "#8172B3", color = "white", linewidth = 0.2) +
  labs(title = "GRAVY", x = "GRAVY", y = "Count") +
  theme_minimal(base_size = 10)

combined <- (p1 + p2) / (p3 + p4) +
  plot_annotation(
    title = "Physicochemical Properties (ExPASy ProtParam)",
    caption = "Source: https://web.expasy.org/protparam/ ; values retrieved per sequence via official ProtParam endpoint."
  )

ggsave("{output_pdf}", combined, width = 12, height = 9)
png("{output_png}", width = 1200, height = 900, res = 150)
print(combined)
dev.off()
""",
        encoding="utf-8",
    )
    run_r_script(r_script)


def plot_deeptmhmm(
    segments_tsv: Path,
    summary_tsv: Path,
    strip_pdf: Path,
    strip_png: Path,
    summary_pdf: Path,
    summary_png: Path,
    r_dir: Path,
) -> None:
    r_script = r_dir / "plot_deeptmhmm_v2.R"
    r_script.write_text(
        f"""
library(ggplot2)
library(patchwork)

segments <- read.delim("{segments_tsv}", sep="\\t", header=TRUE, stringsAsFactors=FALSE)
summary <- read.delim("{summary_tsv}", sep="\\t", header=TRUE, stringsAsFactors=FALSE)

short_id <- function(x) ifelse(nchar(x) > 28, paste0(substr(x, 1, 25), "..."), x)

summary$protein_label <- short_id(summary$protein_id)
label_levels <- rev(unique(summary$protein_label[order(summary$tm_count, summary$protein_id)]))
segments$protein_label <- factor(short_id(segments$protein_id), levels = label_levels)
segments$mid <- (segments$start + segments$end) / 2
segments$width <- segments$end - segments$start + 1
segments$state_label <- factor(
  segments$state_label,
  levels = c("Inside", "Outside", "TM helix", "Beta TM", "Signal", "Lumen")
)

state_colors <- c(
  "Inside" = "#9ecae1", "Outside" = "#a1d99b", "TM helix" = "#ef6548",
  "Beta TM" = "#bcbddc", "Signal" = "#fdd0a2", "Lumen" = "#fdae6b"
)

strip_h <- max(5, 0.24 * length(label_levels) + 1.8)

p_strip <- ggplot(segments, aes(x = mid, y = protein_label, width = width, fill = state_label)) +
  geom_tile(height = 0.82, color = NA) +
  scale_fill_manual(values = state_colors, name = "Topology") +
  scale_x_continuous(expand = c(0.01, 0)) +
  labs(
    title = "DeepTMHMM Predicted Topology",
    subtitle = "Topology parsed from DeepTMHMM predicted_topologies.3line (I=inside, O=outside, M=TM helix)",
    caption = "Batch web submission to DTU DeepTMHMM; this is not the single-sequence official plot.png output.",
    x = "Residue position",
    y = NULL
  ) +
  theme_minimal(base_size = 10) +
  theme(
    legend.position = "bottom",
    panel.grid = element_blank(),
    plot.caption = element_text(size = 8, hjust = 0, color = "#555555")
  )

type_counts <- as.data.frame(table(summary$glob_tm_label))
colnames(type_counts) <- c("protein_type", "count")
type_counts$protein_type <- factor(type_counts$protein_type, levels = c("GLOB", "TM"))
max_count <- max(type_counts$count, na.rm = TRUE)

p_type <- ggplot(type_counts, aes(x = protein_type, y = count, fill = protein_type)) +
  geom_col(width = 0.42, color = "white", linewidth = 0.2) +
  geom_text(aes(label = count), vjust = -0.4, size = 3) +
  scale_fill_manual(values = c("GLOB" = "#6baed6", "TM" = "#ef6548")) +
  scale_y_continuous(expand = expansion(mult = c(0, 0.12)), limits = c(0, max_count * 1.15)) +
  labs(title = "Protein Class", x = NULL, y = "Count") +
  theme_minimal(base_size = 9) +
  theme(legend.position = "none", panel.grid.major.x = element_blank())

summary$tm_factor <- factor(summary$tm_count, levels = sort(unique(summary$tm_count)))
tm_counts <- as.data.frame(table(summary$tm_factor))
colnames(tm_counts) <- c("tm_count", "count")
tm_counts$tm_count <- factor(tm_counts$tm_count, levels = levels(summary$tm_factor))
max_tm <- max(tm_counts$count, na.rm = TRUE)

p_tm <- ggplot(tm_counts, aes(x = tm_count, y = count)) +
  geom_col(width = 0.42, fill = "#08519c", color = "white", linewidth = 0.2) +
  geom_text(aes(label = count), vjust = -0.4, size = 3) +
  scale_y_continuous(expand = expansion(mult = c(0, 0.12)), limits = c(0, max_tm * 1.15)) +
  labs(
    title = "TM Helix Count",
    subtitle = "Counts from GFF3 when available, else contiguous M segments in topology",
    x = "TM helices",
    y = "Count"
  ) +
  theme_minimal(base_size = 9) +
  theme(panel.grid.major.x = element_blank())

p_summary <- p_type + p_tm + plot_layout(widths = c(1, 1.2))

ggsave("{strip_pdf}", p_strip, width = 11, height = strip_h)
ggsave("{summary_pdf}", p_summary, width = 8, height = 3.2)

png("{strip_png}", width = 1100, height = round(strip_h * 100), res = 150)
print(p_strip)
dev.off()

png("{summary_png}", width = 800, height = 320, res = 150)
print(p_summary)
dev.off()
""",
        encoding="utf-8",
    )
    run_r_script(r_script)


def plot_subcellular_heatmap(scores_tsv: Path, output_pdf: Path, output_png: Path, r_dir: Path) -> None:
    r_script = r_dir / "plot_subcellular_v2.R"
    r_script.write_text(
        f"""
library(ggplot2)
library(tidyr)
library(dplyr)

raw <- read.delim("{scores_tsv}", sep="\\t", header=TRUE, check.names=FALSE, stringsAsFactors=FALSE)
meta_cols <- c("protein_id", "protein_label", "primary_location")
locations <- setdiff(names(raw), meta_cols)

long <- raw %>%
  pivot_longer(cols = all_of(locations), names_to = "location", values_to = "score") %>%
  mutate(
    protein_label = factor(protein_label, levels = rev(unique(raw$protein_label))),
    location = factor(location, levels = locations),
    score = as.numeric(score),
    score_label = ifelse(score > 0, sub("\\\\.0$", "", sprintf("%.1f", score)), ""),
    text_color = ifelse(score >= 7, "white", "#222222")
  )

plot_h <- max(5.5, 0.24 * length(unique(long$protein_label)) + 2.2)
plot_w <- max(10, 0.5 * length(locations) + 3.5)

p <- ggplot(long, aes(x = location, y = protein_label, fill = score)) +
  geom_tile(color = "white", linewidth = 0.25) +
  geom_text(aes(label = score_label, color = text_color), size = 1.8, show.legend = FALSE) +
  scale_color_identity() +
  scale_fill_gradient(low = "#f7fbff", high = "#08519c", name = "WoLF score") +
  labs(
    title = "Subcellular Localization (WoLF PSORT, Plant)",
    subtitle = "Cell values = WoLF PSORT scores from wolfpsort.hgc.jp",
    caption = "Note: scores are WoLF PSORT k-NN vote strengths, not experimental localization. score >= 10 is usually reliable; score 3-5 with similar values across columns suggests uncertainty.",
    x = "Predicted compartment",
    y = NULL
  ) +
  theme_minimal(base_size = 10) +
  theme(
    axis.text.x = element_text(angle = 45, hjust = 1),
    panel.grid = element_blank(),
    plot.caption = element_text(hjust = 0, size = 8, color = "#555555", margin = margin(t = 6))
  )

ggsave("{output_pdf}", p, width = plot_w, height = plot_h)
png("{output_png}", width = round(plot_w * 100), height = round(plot_h * 100), res = 150)
print(p)
dev.off()
""",
        encoding="utf-8",
    )
    run_r_script(r_script)
