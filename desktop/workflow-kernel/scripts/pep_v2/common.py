"""Shared FASTA/timeout helpers for PEP modules."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List


def parse_fasta(fasta_file: Path) -> Dict[str, str]:
    sequences: Dict[str, str] = {}
    current_id = None
    current_seq: List[str] = []
    with open(fasta_file, "r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if line.startswith(">"):
                if current_id:
                    sequences[current_id] = "".join(current_seq)
                current_id = line[1:].split()[0]
                current_seq = []
            else:
                current_seq.append(line)
        if current_id:
            sequences[current_id] = "".join(current_seq)
    return sequences


def external_tool_timeout(
    fasta_file: Path,
    seconds_per_sequence: int = 90,
    minimum: int = 300,
    maximum: int = 7200,
) -> int:
    try:
        count = sum(1 for line in open(fasta_file, "r", encoding="utf-8", errors="ignore") if line.startswith(">"))
    except Exception:
        count = 1
    return max(minimum, min(maximum, count * seconds_per_sequence))


def write_clean_fasta(fasta_file: Path, output_file: Path) -> Path:
    allowed = set("ACDEFGHIKLMNPQRSTVWY")
    sequences = parse_fasta(fasta_file)
    with open(output_file, "w", encoding="utf-8") as handle:
        for seq_id, seq in sequences.items():
            clean = "".join(aa for aa in seq.upper() if aa in allowed)
            handle.write(f">{seq_id}\n{clean}\n")
    return output_file
