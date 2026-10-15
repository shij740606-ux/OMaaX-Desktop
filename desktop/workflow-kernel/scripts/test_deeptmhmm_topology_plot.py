#!/usr/bin/env python3
"""
Standalone test script: DeepTMHMM topology visualization for gene-family PEP.

Default test data:
  workspace/job_1780471268646_fc6c66/final/Target.Ls.SPL.final_members.pep.fa

Outputs (under --output-dir):
  - predicted_topologies.3line / deeptmhmm_results.gff3  (from DeepTMHMM)
  - topology_segments.tsv
  - tm_summary.tsv
  - deeptmhmm_topology_strip.pdf/png
  - deeptmhmm_tm_summary.pdf/png
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JOB = PROJECT_ROOT / "workspace/job_1780471268646_fc6c66"
DEFAULT_FASTA = DEFAULT_JOB / "final/Target.Ls.SPL.final_members.pep.fa"
DEFAULT_OUTPUT = DEFAULT_JOB / "pep/analysis/test_topology_plot"
BIOLIB_BIN = "/home/xia-lab/miniconda3/envs/test4py311/bin/biolib"
RSCRIPT_BIN = "/home/xia-lab/miniconda3/envs/test4py311/bin/Rscript"

TOPOLOGY_LABELS = {
    "I": "Inside",
    "O": "Outside",
    "M": "TM helix",
    "B": "Beta TM",
    "S": "Signal",
    "L": "Lumen",
}


@dataclass
class TopologyRecord:
    protein_id: str
    protein_type: str
    sequence: str
    topology: str
    tm_count: int


def parse_fasta(path: Path) -> Dict[str, str]:
    records: Dict[str, str] = {}
    current_id: Optional[str] = None
    chunks: List[str] = []
    with open(path, encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(">"):
                if current_id:
                    records[current_id] = "".join(chunks)
                current_id = line[1:].split()[0]
                chunks = []
            else:
                chunks.append(line)
        if current_id:
            records[current_id] = "".join(chunks)
    return records


def write_clean_fasta(fasta_file: Path, output_file: Path) -> Path:
    allowed = set("ACDEFGHIKLMNPQRSTVWY")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as handle:
        for seq_id, seq in parse_fasta(fasta_file).items():
            clean = "".join(aa for aa in seq.upper() if aa in allowed)
            handle.write(f">{seq_id}\n{clean}\n")
    return output_file


def run_deeptmhmm_dtu_web(fasta_file: Path, output_dir: Path, timeout_sec: int = 3600) -> Path:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    geckodriver = shutil.which("geckodriver")
    if not geckodriver:
        raise RuntimeError("geckodriver not found")

    output_dir.mkdir(parents=True, exist_ok=True)
    clean_fasta = write_clean_fasta(fasta_file, output_dir / f"{fasta_file.stem}.deeptmhmm.clean.fa")
    topologies = output_dir / "predicted_topologies.3line"
    gff_file = output_dir / "deeptmhmm_results.gff3"
    html_file = output_dir / "deeptmhmm_dtu.html"

    options = Options()
    options.add_argument("-headless")
    driver = webdriver.Firefox(service=Service(geckodriver), options=options)
    try:
        print("[INFO] Submitting FASTA to DTU DeepTMHMM web service...")
        driver.get("https://services.healthtech.dtu.dk/services/DeepTMHMM-1.0/")
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.NAME, "uploadfile")))
        driver.find_element(By.NAME, "uploadfile").send_keys(str(clean_fasta.resolve()))
        driver.find_element(By.CSS_SELECTOR, 'input[type="submit"]').click()
        start = time.time()
        while time.time() - start < timeout_sec:
            body = driver.find_element(By.TAG_NAME, "body").text
            if "Predicted Topologies" in body or "Predicted topologies" in body:
                html_file.write_text(driver.page_source, encoding="utf-8")
                lines = body.splitlines()
                start_idx = None
                for i, line in enumerate(lines):
                    if line.strip() == "Predicted Topologies":
                        start_idx = i + 1
                        break
                if start_idx is None:
                    raise RuntimeError("Predicted Topologies section not found")
                topo_lines: List[str] = []
                gff_lines: List[str] = []
                in_gff = False
                for line in lines[start_idx:]:
                    if line.startswith("##gff-version"):
                        in_gff = True
                    if line.startswith("DTU Health Tech") or line.startswith("ADDRESS & INFO"):
                        break
                    if in_gff:
                        if line.strip():
                            gff_lines.append(line)
                    elif line.strip():
                        topo_lines.append(line)
                topologies.write_text("\n".join(topo_lines).strip() + "\n", encoding="utf-8")
                gff_file.write_text("\n".join(gff_lines).strip() + "\n", encoding="utf-8")
                print(f"[INFO] DTU web output: {topologies}")
                return topologies
            if "failed" in body.lower() or "error" in body.lower():
                html_file.write_text(driver.page_source, encoding="utf-8")
                raise RuntimeError(body[:500])
            time.sleep(20)
        html_file.write_text(driver.page_source, encoding="utf-8")
        raise RuntimeError("DTU DeepTMHMM web job timed out")
    finally:
        driver.quit()


def run_deeptmhmm_biolib(fasta_file: Path, output_dir: Path, timeout_sec: int = 900) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    topologies = output_dir / "predicted_topologies.3line"
    if topologies.exists() and topologies.stat().st_size > 0:
        print(f"[INFO] Reusing existing topology file: {topologies}")
        return topologies

    biolib = Path(BIOLIB_BIN)
    if not biolib.exists():
        raise FileNotFoundError(f"BioLib not found: {biolib}")

    clean_fasta = write_clean_fasta(fasta_file, output_dir / f"{fasta_file.stem}.deeptmhmm.clean.fa")
    print(f"[INFO] Running DeepTMHMM via BioLib on {clean_fasta}")
    cmd = [str(biolib), "run", "DTU/DeepTMHMM", "--fasta", str(clean_fasta)]
    result = subprocess.run(cmd, cwd=str(output_dir), capture_output=True, text=True, timeout=timeout_sec)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "DeepTMHMM failed")
    if not topologies.exists() or topologies.stat().st_size == 0:
        raise RuntimeError("DeepTMHMM finished but predicted_topologies.3line is missing")
    print(f"[INFO] DeepTMHMM output: {topologies}")
    return topologies


def run_deeptmhmm(fasta_file: Path, output_dir: Path, timeout_sec: int = 7200) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    topologies = output_dir / "predicted_topologies.3line"
    if topologies.exists() and topologies.stat().st_size > 0:
        print(f"[INFO] Reusing existing topology file: {topologies}")
        return topologies

    try:
        return run_deeptmhmm_biolib(fasta_file, output_dir, timeout_sec=min(timeout_sec, 900))
    except Exception as exc:
        print(f"[WARN] BioLib DeepTMHMM failed: {exc}")
        print("[INFO] Falling back to DTU web service...")
        return run_deeptmhmm_dtu_web(fasta_file, output_dir, timeout_sec=timeout_sec)


def split_protein_header(header: str) -> Tuple[str, str]:
    header = header.lstrip(">").strip()
    protein_type = "UNK"
    if "|" in header:
        left, right = header.split("|", 1)
        protein_id = left.strip()
        protein_type = right.strip().split()[0]
        return protein_id, protein_type
    return header.split()[0], protein_type


def parse_topology_3line(path: Path) -> List[TopologyRecord]:
    records: List[TopologyRecord] = []
    pending_id: Optional[str] = None
    pending_type = "UNK"
    pending_seq: Optional[str] = None

    with open(path, encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(">"):
                if pending_id and pending_seq is not None:
                    raise ValueError(f"Incomplete 3line record before header: {line}")
                pending_id, pending_type = split_protein_header(line)
                pending_seq = None
                continue
            if pending_id is None:
                continue
            if pending_seq is None:
                pending_seq = line.upper()
                continue

            topology = line.upper()
            if len(topology) != len(pending_seq):
                raise ValueError(
                    f"Length mismatch for {pending_id}: seq={len(pending_seq)} topo={len(topology)}"
                )
            tm_count = len(re.findall(r"M+", topology))
            records.append(
                TopologyRecord(
                    protein_id=pending_id,
                    protein_type=pending_type,
                    sequence=pending_seq,
                    topology=topology,
                    tm_count=tm_count,
                )
            )
            pending_id = None
            pending_type = "UNK"
            pending_seq = None

    if not records:
        raise ValueError(f"No topology records parsed from {path}")
    return records


def parse_gff3_tm_counts(path: Path) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    if not path.exists():
        return counts
    pattern = re.compile(r"^#\s+(\S+)\s+Number of predicted TMRs:\s+(\d+)")
    with open(path, encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            match = pattern.match(line.strip())
            if match:
                counts[match.group(1)] = int(match.group(2))
    return counts


def topology_segments(records: Iterable[TopologyRecord]) -> List[Dict[str, object]]:
    segments: List[Dict[str, object]] = []
    for record in records:
        if not record.topology:
            continue
        state = record.topology[0]
        start = 1
        for idx in range(1, len(record.topology) + 1):
            at_end = idx == len(record.topology)
            if at_end or record.topology[idx] != state:
                segments.append(
                    {
                        "protein_id": record.protein_id,
                        "protein_type": record.protein_type,
                        "state": state,
                        "state_label": TOPOLOGY_LABELS.get(state, state),
                        "start": start,
                        "end": idx,
                        "length": idx - start + 1,
                    }
                )
                if not at_end:
                    state = record.topology[idx]
                    start = idx + 1
    return segments


def short_label(protein_id: str, max_len: int = 28) -> str:
    if len(protein_id) <= max_len:
        return protein_id
    return protein_id[: max_len - 3] + "..."


def write_topology_segments_tsv(segments: List[Dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("protein_id\tprotein_type\tstate\tstate_label\tstart\tend\tlength\n")
        for row in segments:
            handle.write(
                f"{row['protein_id']}\t{row['protein_type']}\t{row['state']}\t{row['state_label']}\t"
                f"{row['start']}\t{row['end']}\t{row['length']}\n"
            )


def write_tm_summary_tsv(records: List[TopologyRecord], gff3_counts: Dict[str, int], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("protein_id\tprotein_type\ttm_count\tglob_tm_label\tseq_length\n")
        for record in records:
            tm_count = gff3_counts.get(record.protein_id, record.tm_count)
            label = "TM" if tm_count > 0 else "GLOB"
            if record.protein_type in {"TM", "GLOB"}:
                label = record.protein_type
            handle.write(
                f"{record.protein_id}\t{record.protein_type}\t{tm_count}\t{label}\t{len(record.sequence)}\n"
            )


def build_r_script(segments_tsv: Path, summary_tsv: Path, strip_pdf: Path, strip_png: Path,
                   summary_pdf: Path, summary_png: Path) -> str:
    return f"""
library(ggplot2)
library(patchwork)

segments <- read.delim("{segments_tsv}", sep="\\t", header=TRUE, stringsAsFactors=FALSE)
summary <- read.delim("{summary_tsv}", sep="\\t", header=TRUE, stringsAsFactors=FALSE)

short_id <- function(x) {{
  ifelse(nchar(x) > 28, paste0(substr(x, 1, 25), "..."), x)
}}

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
  "Inside" = "#9ecae1",
  "Outside" = "#a1d99b",
  "TM helix" = "#ef6548",
  "Beta TM" = "#bcbddc",
  "Signal" = "#fdd0a2",
  "Lumen" = "#fdae6b"
)

p_strip <- ggplot(segments, aes(x = mid, y = protein_label, width = width, fill = state_label)) +
  geom_tile(height = 0.85, color = NA) +
  scale_fill_manual(values = state_colors, name = "Topology") +
  scale_x_continuous(expand = c(0, 0)) +
  labs(
    title = "DeepTMHMM Predicted Topology",
    subtitle = "Each row = one protein; colored blocks = inside / outside / transmembrane helix",
    x = "Residue position",
    y = NULL
  ) +
  theme_minimal(base_size = 11) +
  theme(
    legend.position = "bottom",
    panel.grid.major.y = element_blank(),
    panel.grid.minor = element_blank()
  )

summary$tm_factor <- factor(summary$tm_count, levels = sort(unique(summary$tm_count)))
type_counts <- as.data.frame(table(summary$glob_tm_label))
colnames(type_counts) <- c("protein_type", "count")

p_type <- ggplot(type_counts, aes(x = protein_type, y = count, fill = protein_type)) +
  geom_col(width = 0.55, color = "white") +
  geom_text(aes(label = count), vjust = -0.3, size = 4) +
  scale_fill_manual(values = c("GLOB" = "#6baed6", "TM" = "#ef6548")) +
  labs(title = "Protein Type Summary", x = "DeepTMHMM class", y = "Count") +
  theme_minimal() +
  theme(legend.position = "none")

p_tm <- ggplot(summary, aes(x = tm_factor)) +
  geom_bar(fill = "#08519c", color = "white") +
  labs(
    title = "Transmembrane Helix Count",
    subtitle = "Parsed from topology line (GFF3-confirmed when available)",
    x = "Number of TM helices",
    y = "Protein count"
  ) +
  theme_minimal()

combined <- p_strip / (p_type + p_tm) + plot_layout(heights = c(2.2, 1))

strip_h <- max(6, 0.28 * length(label_levels) + 2)
summary_h <- max(8, 0.28 * length(label_levels) + 4)

ggsave("{strip_pdf}", p_strip, width = 12, height = strip_h)
ggsave("{summary_pdf}", combined, width = 12, height = summary_h)

png("{strip_png}", width = 1200, height = round(strip_h * 100), res = 150)
print(p_strip)
dev.off()

png("{summary_png}", width = 1200, height = round(summary_h * 100), res = 150)
print(combined)
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
    parser = argparse.ArgumentParser(description="Test DeepTMHMM topology plotting")
    parser.add_argument("--fasta", type=Path, default=DEFAULT_FASTA, help="Input protein FASTA")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT, help="Output directory")
    parser.add_argument("--topology-file", type=Path, help="Existing predicted_topologies.3line (skip DeepTMHMM run)")
    parser.add_argument("--skip-run", action="store_true", help="Skip DeepTMHMM if topology file already exists")
    parser.add_argument("--run-method", choices=("auto", "biolib", "dtu"), default="auto",
                        help="DeepTMHMM runner: auto tries BioLib then DTU web")
    parser.add_argument("--timeout-sec", type=int, default=7200)
    args = parser.parse_args()

    fasta = args.fasta.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not fasta.exists():
        print(f"[ERROR] FASTA not found: {fasta}", file=sys.stderr)
        return 1

    print("=" * 60)
    print("DeepTMHMM topology plot test")
    print("=" * 60)
    print(f"FASTA: {fasta}")
    print(f"Output: {output_dir}")

    if args.topology_file:
        topology_file = args.topology_file.resolve()
    elif args.skip_run and (output_dir / "predicted_topologies.3line").exists():
        topology_file = output_dir / "predicted_topologies.3line"
    else:
        try:
            if args.run_method == "biolib":
                topology_file = run_deeptmhmm_biolib(fasta, output_dir, timeout_sec=args.timeout_sec)
            elif args.run_method == "dtu":
                topology_file = run_deeptmhmm_dtu_web(fasta, output_dir, timeout_sec=args.timeout_sec)
            else:
                topology_file = run_deeptmhmm(fasta, output_dir, timeout_sec=args.timeout_sec)
        except Exception as exc:
            print(f"[ERROR] DeepTMHMM failed: {exc}", file=sys.stderr)
            return 1

    gff3_file = topology_file.parent / "deeptmhmm_results.gff3"
    if not gff3_file.exists():
        gff3_file = topology_file.parent / "TMRs.gff3"

    records = parse_topology_3line(topology_file)
    gff3_counts = parse_gff3_tm_counts(gff3_file)
    segments = topology_segments(records)

    segments_tsv = output_dir / "topology_segments.tsv"
    summary_tsv = output_dir / "tm_summary.tsv"
    write_topology_segments_tsv(segments, segments_tsv)
    write_tm_summary_tsv(records, gff3_counts, summary_tsv)

    strip_pdf = output_dir / "deeptmhmm_topology_strip.pdf"
    strip_png = output_dir / "deeptmhmm_topology_strip.png"
    summary_pdf = output_dir / "deeptmhmm_tm_summary.pdf"
    summary_png = output_dir / "deeptmhmm_tm_summary.png"

    r_script_path = output_dir / "plot_deeptmhmm_topology.R"
    r_script_path.write_text(
        build_r_script(segments_tsv, summary_tsv, strip_pdf, strip_png, summary_pdf, summary_png),
        encoding="utf-8",
    )

    try:
        run_r_plot(r_script_path)
    except Exception as exc:
        print(f"[ERROR] Plotting failed: {exc}", file=sys.stderr)
        return 1

    tm_proteins = sum(1 for r in records if gff3_counts.get(r.protein_id, r.tm_count) > 0)
    glob_proteins = len(records) - tm_proteins
    print("\nSummary")
    print(f"  Proteins parsed: {len(records)}")
    print(f"  GLOB: {glob_proteins}")
    print(f"  TM:   {tm_proteins}")
    print("\nArtifacts")
    for path in [topology_file, segments_tsv, summary_tsv, strip_pdf, strip_png, summary_pdf, summary_png]:
        print(f"  - {path}")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
