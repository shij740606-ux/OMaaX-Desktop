#!/usr/bin/env python3
"""
Standalone test script: WoLF PSORT subcellular localization heatmap.

Default test data:
  workspace/job_1780459186454_21f4cf/pep/analysis/Ls.SPL_wolfpsort.txt

Outputs (under --output-dir):
  - subcellular_scores.tsv
  - subcellular_primary.tsv
  - subcellular_heatmap.pdf/png
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WOLFPSORT = (
    PROJECT_ROOT
    / "workspace/job_1780459186454_21f4cf/pep/analysis/Ls.SPL_wolfpsort.txt"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "workspace/job_1780471268646_fc6c66/pep/analysis/test_subcellular_heatmap"
)
RSCRIPT_BIN = "/home/xia-lab/miniconda3/envs/test4py311/bin/Rscript"

LOCATION_LABELS = {
    "nucl": "Nucleus",
    "nucleus": "Nucleus",
    "cyto": "Cytoplasm",
    "cytoplasm": "Cytoplasm",
    "chlo": "Chloroplast",
    "chloroplast": "Chloroplast",
    "mito": "Mitochondrion",
    "mitochondrion": "Mitochondrion",
    "plas": "Plasma membrane",
    "plasma membrane": "Plasma membrane",
    "vacu": "Vacuole",
    "vacuole": "Vacuole",
    "golg": "Golgi apparatus",
    "golgi": "Golgi apparatus",
    "extr": "Extracellular",
    "extracellular": "Extracellular",
    "pero": "Peroxisome",
    "peroxisome": "Peroxisome",
    "e.r.": "Endoplasmic reticulum",
    "er": "Endoplasmic reticulum",
    "cysk": "Cytoskeleton",
    "cytoskeleton": "Cytoskeleton",
    "cyto_nucl": "Cytoplasm / Nucleus",
}

LOCATION_ORDER = [
    "Nucleus",
    "Cytoplasm",
    "Cytoplasm / Nucleus",
    "Chloroplast",
    "Mitochondrion",
    "Plasma membrane",
    "Endoplasmic reticulum",
    "Golgi apparatus",
    "Vacuole",
    "Extracellular",
    "Peroxisome",
    "Cytoskeleton",
]


def normalize_location_label(label: str) -> str:
    clean = re.sub(r"\s+", " ", str(label or "").strip())
    if not clean:
        return ""
    key = clean.lower().replace("_", " ")
    if key in LOCATION_LABELS:
        return LOCATION_LABELS[key]
    if clean.lower() in LOCATION_LABELS:
        return LOCATION_LABELS[clean.lower()]
    if " " not in clean and "_" in clean:
        parts = [normalize_location_label(part) for part in clean.split("_")]
        parts = [part for part in parts if part]
        if parts:
            return " / ".join(parts)
    return clean[:1].upper() + clean[1:]


def parse_wolfpsort_scores(path: Path) -> Tuple[Dict[str, Dict[str, float]], Dict[str, str]]:
    scores: Dict[str, Dict[str, float]] = {}
    primary: Dict[str, str] = {}
    pattern = re.compile(r"([^:,]+):\s*([\d.]+)")

    with open(path, encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.lower().startswith("protein_id"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            protein_id = parts[0].strip()
            prediction = parts[1].strip()
            loc_scores: Dict[str, float] = {}
            for site, value in pattern.findall(prediction):
                label = normalize_location_label(site.strip())
                if not label:
                    continue
                loc_scores[label] = loc_scores.get(label, 0.0) + float(value)
            if not loc_scores:
                continue
            scores[protein_id] = loc_scores
            primary[protein_id] = max(loc_scores.items(), key=lambda item: item[1])[0]
    return scores, primary


def short_label(protein_id: str, max_len: int = 28) -> str:
    if len(protein_id) <= max_len:
        return protein_id
    return protein_id[: max_len - 3] + "..."


def write_scores_tsv(scores: Dict[str, Dict[str, float]], primary: Dict[str, str], path: Path) -> List[str]:
    locations = sorted(
        {loc for loc_scores in scores.values() for loc in loc_scores},
        key=lambda loc: (
            LOCATION_ORDER.index(loc) if loc in LOCATION_ORDER else len(LOCATION_ORDER),
            loc,
        ),
    )
    protein_order = sorted(
        scores.keys(),
        key=lambda pid: (
            LOCATION_ORDER.index(primary[pid]) if primary.get(pid) in LOCATION_ORDER else len(LOCATION_ORDER),
            primary.get(pid, ""),
            pid,
        ),
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("protein_id\tprotein_label\tprimary_location\t" + "\t".join(locations) + "\n")
        for protein_id in protein_order:
            loc_scores = scores[protein_id]
            row = [
                protein_id,
                short_label(protein_id),
                primary.get(protein_id, ""),
            ]
            row.extend(str(loc_scores.get(loc, 0.0)) for loc in locations)
            handle.write("\t".join(row) + "\n")
    return locations


def write_primary_tsv(primary: Dict[str, str], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("protein_id\tprimary_location\n")
        for protein_id in sorted(primary):
            handle.write(f"{protein_id}\t{primary[protein_id]}\n")


def build_r_script(scores_tsv: Path, heatmap_pdf: Path, heatmap_png: Path) -> str:
    return f"""
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

p <- ggplot(long, aes(x = location, y = protein_label, fill = score)) +
  geom_tile(color = "white", linewidth = 0.3) +
  geom_text(
    aes(label = score_label, color = text_color),
    size = 2.0,
    show.legend = FALSE
  ) +
  scale_color_identity() +
  scale_fill_gradient(low = "#f7fbff", high = "#08519c", name = "WoLF score") +
  labs(
    title = "Subcellular Localization Heatmap (WoLF PSORT)",
    subtitle = "格内数字 = WoLF PSORT 得分；颜色越深表示该区室得分越高",
    caption = "注：格内数字为 WoLF PSORT 得分；得分 ≥10 通常较可靠；3–5 且多列接近 → 不确定。",
    x = "Predicted compartment",
    y = NULL
  ) +
  theme_minimal(base_size = 11) +
  theme(
    axis.text.x = element_text(angle = 45, hjust = 1),
    panel.grid = element_blank(),
    plot.caption = element_text(hjust = 0, size = 9, color = "#444444", margin = margin(t = 8))
  )

plot_h <- max(6, 0.28 * length(unique(long$protein_label)) + 2.5)
plot_w <- max(11, 0.55 * length(locations) + 4)
ggsave("{heatmap_pdf}", p, width = plot_w, height = plot_h)
png("{heatmap_png}", width = round(plot_w * 100), height = round(plot_h * 100), res = 150)
print(p)
dev.off()
"""


def run_r_plot(r_script_path: Path) -> None:
    rscript = Path(RSCRIPT_BIN)
    if not rscript.exists():
        raise FileNotFoundError(f"Rscript not found: {rscript}")
    result = subprocess.run([str(rscript), str(r_script_path)], capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "R plotting failed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Test WoLF PSORT subcellular heatmap plotting")
    parser.add_argument("--input", type=Path, default=DEFAULT_WOLFPSORT, help="WoLF PSORT result TSV")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT, help="Output directory")
    args = parser.parse_args()

    input_file = args.input.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_file.exists():
        print(f"[ERROR] Input not found: {input_file}", file=sys.stderr)
        return 1

    print("=" * 60)
    print("Subcellular localization heatmap test")
    print("=" * 60)
    print(f"Input:  {input_file}")
    print(f"Output: {output_dir}")

    scores, primary = parse_wolfpsort_scores(input_file)
    if not scores:
        print("[ERROR] No WoLF PSORT scores parsed", file=sys.stderr)
        return 1

    scores_tsv = output_dir / "subcellular_scores.tsv"
    primary_tsv = output_dir / "subcellular_primary.tsv"
    locations = write_scores_tsv(scores, primary, scores_tsv)
    write_primary_tsv(primary, primary_tsv)

    heatmap_pdf = output_dir / "subcellular_heatmap.pdf"
    heatmap_png = output_dir / "subcellular_heatmap.png"
    r_script_path = output_dir / "plot_subcellular_heatmap.R"
    r_script_path.write_text(build_r_script(scores_tsv, heatmap_pdf, heatmap_png), encoding="utf-8")

    try:
        run_r_plot(r_script_path)
    except Exception as exc:
        print(f"[ERROR] Plotting failed: {exc}", file=sys.stderr)
        return 1

    from collections import Counter

    counts = Counter(primary.values())
    print("\nSummary")
    print(f"  Proteins: {len(scores)}")
    print(f"  Compartments in matrix: {len(locations)}")
    for loc, count in counts.most_common():
        print(f"  Primary {loc}: {count}")

    print("\nArtifacts")
    for path in [scores_tsv, primary_tsv, heatmap_pdf, heatmap_png]:
        print(f"  - {path}")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
