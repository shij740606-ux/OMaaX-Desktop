from __future__ import annotations
from pathlib import Path
from typing import Iterable, Set


def read_id_set(path: str) -> Set[str]:
    s = set()
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            x = line.strip()
            if x:
                s.add(x)
    return s


def extract_fasta_by_ids(in_fasta: str, ids: Set[str], out_fasta: str) -> int:
    """
    依据 fasta header 的第一个 token(>ID 后空格前) 提取序列
    返回写出的序列条数
    """
    in_path = Path(in_fasta)
    out_path = Path(out_fasta)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    keep = False
    n = 0

    with in_path.open("r", encoding="utf-8", errors="ignore") as fin, \
         out_path.open("w", encoding="utf-8") as fout:
        for line in fin:
            if line.startswith(">"):
                seq_id = line[1:].strip().split()[0]
                keep = seq_id in ids
                if keep:
                    fout.write(line)
                    n += 1
            else:
                if keep:
                    fout.write(line)
    return n
