"""DeepTMHMM run/parse helpers for PEP v2."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.pep_v2.common import external_tool_timeout, parse_fasta, write_clean_fasta

BIOLIB_BIN = shutil.which("biolib") or "biolib"

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


def run_deeptmhmm_dtu_web(fasta_file: Path, output_dir: Path, timeout_sec: int) -> Dict[str, object]:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.firefox.service import Service
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    geckodriver = shutil.which("geckodriver")
    if not geckodriver:
        return {"success": False, "stderr": "geckodriver not found", "method": "DTU DeepTMHMM web"}

    output_dir.mkdir(parents=True, exist_ok=True)
    clean_fasta = write_clean_fasta(fasta_file, output_dir / f"{fasta_file.stem}.deeptmhmm.clean.fa")
    upload_dir = PROJECT_ROOT / ".runtime" / "deeptmhmm_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_fasta = upload_dir / f"{int(time.time() * 1000)}_{clean_fasta.name}"
    shutil.copy2(clean_fasta, upload_fasta)
    topologies = output_dir / "predicted_topologies.3line"
    gff_file = output_dir / "deeptmhmm_results.gff3"
    html_file = output_dir / "deeptmhmm_dtu.html"

    options = Options()
    options.add_argument("-headless")
    driver = webdriver.Firefox(service=Service(geckodriver), options=options)
    try:
        driver.get("https://services.healthtech.dtu.dk/services/DeepTMHMM-1.0/")
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.NAME, "uploadfile")))
        driver.find_element(By.NAME, "uploadfile").send_keys(str(upload_fasta))
        driver.find_element(By.CSS_SELECTOR, 'input[type="submit"]').click()
        start = time.time()
        while time.time() - start < timeout_sec:
            body = driver.find_element(By.TAG_NAME, "body").text
            if "Predicted Topologies" in body or "Predicted topologies" in body:
                html_file.write_text(driver.page_source, encoding="utf-8")
                lines = body.splitlines()
                start_idx = next(i for i, line in enumerate(lines) if line.strip() == "Predicted Topologies") + 1
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
                return {
                    "success": topologies.exists() and topologies.stat().st_size > 0,
                    "topologies": topologies,
                    "gff": gff_file,
                    "method": "DTU DeepTMHMM web",
                }
            if "failed" in body.lower() or "error" in body.lower():
                html_file.write_text(driver.page_source, encoding="utf-8")
                return {"success": False, "stderr": body[:500], "method": "DTU DeepTMHMM web"}
            time.sleep(20)
        html_file.write_text(driver.page_source, encoding="utf-8")
        return {"success": False, "stderr": "DTU DeepTMHMM web job timed out", "method": "DTU DeepTMHMM web"}
    finally:
        driver.quit()


def run_deeptmhmm(fasta_file: Path, output_dir: Path) -> Dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    topologies = output_dir / "predicted_topologies.3line"
    timeout_sec = external_tool_timeout(fasta_file, seconds_per_sequence=120, minimum=900, maximum=10800)

    web_result = run_deeptmhmm_dtu_web(fasta_file, output_dir, timeout_sec)
    if web_result.get("success"):
        return web_result

    try:
        cmd = [BIOLIB_BIN, "run", "DTU/DeepTMHMM", "--fasta", str(fasta_file)]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(output_dir), timeout=min(timeout_sec, 900))
        if result.returncode == 0 and topologies.exists() and topologies.stat().st_size > 0:
            return {"success": True, "topologies": topologies, "method": "BioLib DeepTMHMM"}
        return web_result if web_result.get("success") else {
            "success": False,
            "stderr": (result.stderr or result.stdout or web_result.get("stderr") or "DeepTMHMM failed")[:500],
            "method": "BioLib/DTU DeepTMHMM",
        }
    except Exception as exc:
        return {"success": False, "stderr": str(exc), "method": "BioLib/DTU DeepTMHMM"}


def split_protein_header(header: str) -> Tuple[str, str]:
    header = header.lstrip(">").strip()
    if "|" in header:
        left, right = header.split("|", 1)
        return left.strip(), right.strip().split()[0]
    return header.split()[0], "UNK"


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
                pending_id, pending_type = split_protein_header(line)
                pending_seq = None
                continue
            if pending_id is None:
                continue
            if pending_seq is None:
                pending_seq = line.upper()
                continue
            topology = line.upper()
            tm_count = len(re.findall(r"M+", topology))
            records.append(
                TopologyRecord(pending_id, pending_type, pending_seq, topology, tm_count)
            )
            pending_id = None
            pending_seq = None
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


def topology_segments(records: List[TopologyRecord]) -> List[Dict[str, object]]:
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
                    }
                )
                if not at_end:
                    state = record.topology[idx]
                    start = idx + 1
    return segments


def write_topology_tables(records: List[TopologyRecord], gff3_counts: Dict[str, int], output_dir: Path) -> Tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    segments = topology_segments(records)
    segments_tsv = output_dir / "topology_segments.tsv"
    summary_tsv = output_dir / "tm_summary.tsv"

    with open(segments_tsv, "w", encoding="utf-8") as handle:
        handle.write("protein_id\tprotein_type\tstate\tstate_label\tstart\tend\n")
        for row in segments:
            handle.write(
                f"{row['protein_id']}\t{row['protein_type']}\t{row['state']}\t{row['state_label']}\t"
                f"{row['start']}\t{row['end']}\n"
            )

    with open(summary_tsv, "w", encoding="utf-8") as handle:
        handle.write("protein_id\tprotein_type\ttm_count\tglob_tm_label\tseq_length\n")
        for record in records:
            tm_count = gff3_counts.get(record.protein_id, record.tm_count)
            label = record.protein_type if record.protein_type in {"TM", "GLOB"} else ("TM" if tm_count > 0 else "GLOB")
            handle.write(
                f"{record.protein_id}\t{record.protein_type}\t{tm_count}\t{label}\t{len(record.sequence)}\n"
            )
    return segments_tsv, summary_tsv
