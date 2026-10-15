from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Set, Tuple
from .fasta_utils import read_id_set


@dataclass
class MergeOutputs:
    merged_id: str
    evidence_tsv: str
    hmm_count: int
    blast_count: int
    merged_count: int
    both_count: int


def merge_hmm_blast_ids(hmm_id_file: str, blast_id_file: str, out_dir: str, prefix: str) -> MergeOutputs:
    outp = Path(out_dir).resolve()
    outp.mkdir(parents=True, exist_ok=True)

    hmm_ids: Set[str] = read_id_set(hmm_id_file)
    blast_ids: Set[str] = read_id_set(blast_id_file)
    merged = sorted(hmm_ids | blast_ids)
    both = hmm_ids & blast_ids

    merged_id = (outp / f"{prefix}.candidates.merged.id").resolve()
    evidence_tsv = (outp / f"{prefix}.candidates.evidence.tsv").resolve()

    with merged_id.open("w", encoding="utf-8") as w:
        for i in merged:
            w.write(i + "\n")

    with evidence_tsv.open("w", encoding="utf-8") as w:
        w.write("id\tin_hmm\tin_blast\tevidence\n")
        for i in merged:
            ih = "1" if i in hmm_ids else "0"
            ib = "1" if i in blast_ids else "0"
            ev = "BOTH" if (i in both) else ("HMM" if i in hmm_ids else "BLAST")
            w.write(f"{i}\t{ih}\t{ib}\t{ev}\n")

    return MergeOutputs(
        merged_id=str(merged_id),
        evidence_tsv=str(evidence_tsv),
        hmm_count=len(hmm_ids),
        blast_count=len(blast_ids),
        merged_count=len(merged),
        both_count=len(both),
    )
