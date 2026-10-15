# pipelines/blast_pipeline.py
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple


@dataclass
class BlastOutputs:
    out_dir: str
    db_fasta: str
    blastout: str
    filtered_tsv: str
    id_list: str
    hit_count: int
    unique_id_count: int
    params: Dict[str, Any]


def _ensure_exe(name: str) -> str:
    exe = shutil.which(name)
    if not exe:
        raise RuntimeError(f"找不到可执行程序: {name} (请确认已安装并在 PATH 中)")
    return exe


def _parse_blast_line(line: str) -> Optional[Dict[str, Any]]:
    parts = line.rstrip("\n").split("\t")
    if len(parts) < 14:
        return None
    try:
        qseqid = parts[0]
        sseqid = parts[1]
        pident = float(parts[2])
        length = int(parts[3])
        evalue = float(parts[10])
        bitscore = float(parts[11])
        qlen = int(parts[12])
        slen = int(parts[13])
        qcov = (length / qlen * 100.0) if qlen else 0.0
        scov = (length / slen * 100.0) if slen else 0.0
        return {
            "qseqid": qseqid,
            "sseqid": sseqid,
            "pident": pident,
            "length": length,
            "evalue": evalue,
            "bitscore": bitscore,
            "qlen": qlen,
            "slen": slen,
            "qcov": qcov,
            "scov": scov,
            "raw": parts,
        }
    except Exception:
        return None


def run_blastp_pipeline(
    reference_fasta: str,
    query_fasta: str,
    out_dir: str,
    prefix: str,
    evalue_max: float = 1e-5,
    pident_min: float = 30.0,
    qcov_min: float = 40.0,
    scov_min: float = 40.0,
    threads: int = 8,
    max_target_seqs: int = 50,
) -> BlastOutputs:
    """
    makeblastdb + blastp + 过滤 + 输出 id_list
    输出文件：
      {prefix}.blastout
      {prefix}.blastout.filter.tsv   (带 qcov/scov)
      {prefix}.blastout.filter.id
      {prefix}.summary.json
    """
    _ensure_exe("makeblastdb")
    _ensure_exe("blastp")

    outp = Path(out_dir).resolve()
    outp.mkdir(parents=True, exist_ok=True)

    ref = Path(reference_fasta).resolve()
    qry = Path(query_fasta).resolve()

    blastout = (outp / f"{prefix}.blastout").resolve()
    filtered_tsv = (outp / f"{prefix}.blastout.filter.tsv").resolve()
    id_list = (outp / f"{prefix}.blastout.filter.id").resolve()
    summary_json = (outp / f"{prefix}.summary.json").resolve()

         # 1) 建库：强制把数据库生成在 out_dir，并使用固定前缀 db_prefix
    db_prefix = (outp / Path(ref).stem).resolve()  # 例如 .../blast/SPL_Ath
    pin = Path(str(db_prefix) + ".pin")
    psq = Path(str(db_prefix) + ".psq")
    phr = Path(str(db_prefix) + ".phr")

    if not (pin.exists() and psq.exists() and phr.exists()):
        cmd_db = [
            "makeblastdb",
            "-in", str(ref),
            "-dbtype", "prot",
            "-out", str(db_prefix),
        ]
        proc_db = subprocess.run(cmd_db, cwd=str(outp), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc_db.returncode != 0:
            raise RuntimeError(f"makeblastdb 失败\nCMD: {' '.join(cmd_db)}\nSTDERR:\n{proc_db.stderr[:4000]}")       
    # blastp 的 -db 使用 reference fasta 名称即可（在 out_dir 里）
    db_for_blast = str(db_prefix)

    # 2) blastp
    outfmt = "6 qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore qlen slen"
    cmd_blast = [
        "blastp",
        "-query", str(qry),
        "-db", db_for_blast,
        "-evalue", str(evalue_max),
        "-outfmt", outfmt,
        "-max_target_seqs", str(max_target_seqs),
        "-num_threads", str(threads),
        "-out", str(blastout),
    ]
    proc = subprocess.run(cmd_blast, cwd=str(outp), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"blastp 失败\nCMD: {' '.join(cmd_blast)}\nSTDERR:\n{proc.stderr[:4000]}")

    # 3) 过滤 + 每个 qseqid 只保留 best hit
    best: Dict[str, Dict[str, Any]] = {}
    kept_rows: List[Dict[str, Any]] = []

    with blastout.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not line.strip():
                continue
            rec = _parse_blast_line(line)
            if not rec:
                continue

            if rec["pident"] < pident_min:
                continue
            if rec["qcov"] < qcov_min:
                continue
            if rec["scov"] < scov_min:
                continue

            q = rec["qseqid"]
            cur = best.get(q)
            if cur is None:
                best[q] = rec
            else:
                # bitscore 越大越好；若相同，evalue 越小越好
                if (rec["bitscore"] > cur["bitscore"]) or (
                    rec["bitscore"] == cur["bitscore"] and rec["evalue"] < cur["evalue"]
                ):
                    best[q] = rec

    kept_rows = list(best.values())
    kept_rows.sort(key=lambda x: (-x["bitscore"], x["evalue"]))

    # 写 filter.tsv
    with filtered_tsv.open("w", encoding="utf-8") as w:
        w.write("\t".join(["qseqid", "sseqid", "pident", "evalue", "bitscore", "qcov", "scov", "aln_len", "qlen", "slen"]) + "\n")
        for r in kept_rows:
            w.write(
                "\t".join([
                    r["qseqid"], r["sseqid"],
                    f"{r['pident']:.3f}",
                    f"{r['evalue']:.3g}",
                    f"{r['bitscore']:.1f}",
                    f"{r['qcov']:.1f}",
                    f"{r['scov']:.1f}",
                    str(r["length"]),
                    str(r["qlen"]),
                    str(r["slen"]),
                ]) + "\n"
            )

    # 写 id list
    ids = sorted({r["qseqid"] for r in kept_rows})
    with id_list.open("w", encoding="utf-8") as w:
        for i in ids:
            w.write(i + "\n")

    outputs = BlastOutputs(
        out_dir=str(outp),
        db_fasta=str(ref),
        blastout=str(blastout),
        filtered_tsv=str(filtered_tsv),
        id_list=str(id_list),
        hit_count=len(kept_rows),
        unique_id_count=len(ids),
        params={
            "reference_fasta": str(ref),
            "query_fasta": str(qry),
            "prefix": prefix,
            "evalue_max": evalue_max,
            "pident_min": pident_min,
            "qcov_min": qcov_min,
            "scov_min": scov_min,
            "threads": threads,
            "max_target_seqs": max_target_seqs,
        },
    )

    with summary_json.open("w", encoding="utf-8") as w:
        json.dump(asdict(outputs), w, ensure_ascii=False, indent=2)

    return outputs
