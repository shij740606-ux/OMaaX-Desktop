"""WoLF PSORT parse helpers for PEP v2."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.pep_v2.common import external_tool_timeout

LOCATION_LABELS = {
    "nucl": "Nucleus", "cyto": "Cytoplasm", "chlo": "Chloroplast", "mito": "Mitochondrion",
    "plas": "Plasma membrane", "vacu": "Vacuole", "golg": "Golgi apparatus", "extr": "Extracellular",
    "pero": "Peroxisome", "e.r.": "Endoplasmic reticulum", "er": "Endoplasmic reticulum",
    "cysk": "Cytoskeleton", "cyto_nucl": "Cytoplasm / Nucleus",
}
LOCATION_ORDER = [
    "Nucleus", "Cytoplasm", "Cytoplasm / Nucleus", "Chloroplast", "Mitochondrion",
    "Plasma membrane", "Endoplasmic reticulum", "Golgi apparatus", "Vacuole",
    "Extracellular", "Peroxisome", "Cytoskeleton",
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
    return clean[:1].upper() + clean[1:]


def run_wolfpsort(fasta_file: Path, output_file: Path) -> Dict[str, object]:
    script = PROJECT_ROOT / "scripts/wolfpsort_batch.py"
    if not script.exists():
        return {"success": False, "stderr": f"WoLF PSORT script not found: {script}"}
    if output_file.exists():
        output_file.unlink()
    started_at = __import__("time").time()
    timeout_sec = external_tool_timeout(fasta_file, seconds_per_sequence=90)
    result = subprocess.run(
        [sys.executable, str(script), "--input", str(fasta_file), "--output", str(output_file)],
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    if result.returncode != 0:
        return {"success": False, "stderr": result.stderr or result.stdout or "WoLF PSORT failed"}
    if not output_file.exists() or output_file.stat().st_size == 0 or output_file.stat().st_mtime < started_at:
        return {"success": False, "stderr": "WoLF PSORT did not generate output"}
    return {"success": True, "output_file": output_file, "method": "WoLF PSORT web (Plant)"}


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
            loc_scores: Dict[str, float] = {}
            for site, value in pattern.findall(parts[1]):
                label = normalize_location_label(site.strip())
                if label:
                    loc_scores[label] = loc_scores.get(label, 0.0) + float(value)
            if loc_scores:
                scores[protein_id] = loc_scores
                primary[protein_id] = max(loc_scores.items(), key=lambda item: item[1])[0]
    return scores, primary


def short_label(protein_id: str, max_len: int = 28) -> str:
    return protein_id if len(protein_id) <= max_len else protein_id[: max_len - 3] + "..."


def write_subcellular_tables(
    scores: Dict[str, Dict[str, float]],
    primary: Dict[str, str],
    analysis_dir: Path,
    final_dir: Path,
) -> Path:
    analysis_dir.mkdir(parents=True, exist_ok=True)
    locations = sorted(
        {loc for loc_scores in scores.values() for loc in loc_scores},
        key=lambda loc: (LOCATION_ORDER.index(loc) if loc in LOCATION_ORDER else len(LOCATION_ORDER), loc),
    )
    protein_order = sorted(
        scores.keys(),
        key=lambda pid: (
            LOCATION_ORDER.index(primary[pid]) if primary.get(pid) in LOCATION_ORDER else len(LOCATION_ORDER),
            primary.get(pid, ""),
            pid,
        ),
    )
    scores_tsv = analysis_dir / "subcellular_scores.tsv"
    with open(scores_tsv, "w", encoding="utf-8") as handle:
        handle.write("protein_id\tprotein_label\tprimary_location\t" + "\t".join(locations) + "\n")
        for protein_id in protein_order:
            loc_scores = scores[protein_id]
            row = [protein_id, short_label(protein_id), primary.get(protein_id, "")]
            row.extend(str(loc_scores.get(loc, 0.0)) for loc in locations)
            handle.write("\t".join(row) + "\n")

    counts: Dict[str, int] = {}
    for loc in primary.values():
        counts[loc] = counts.get(loc, 0) + 1
    summary_tsv = final_dir / "subcellular_data.tsv"
    with open(summary_tsv, "w", encoding="utf-8") as handle:
        handle.write("location\tcount\tsource\n")
        for loc, count in sorted(counts.items(), key=lambda item: item[1], reverse=True):
            handle.write(f"{loc}\t{count}\tWoLF PSORT\n")
    return scores_tsv
