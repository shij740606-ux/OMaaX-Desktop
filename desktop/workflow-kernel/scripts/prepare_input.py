#!/usr/bin/env python3
import argparse
import gzip
import re
import shutil
from pathlib import Path
from typing import Dict, Tuple, Optional, List


FASTA_SUFFIX = {".fa", ".fasta", ".faa", ".fna", ".pep"}
GFF_SUFFIX = {".gff", ".gff3"}


def is_gz(p: Path) -> bool:
    return p.name.lower().endswith(".gz")


def strip_gz(p: Path) -> Path:
    return Path(p.name[:-3]) if is_gz(p) else Path(p.name)


def gunzip_to(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(src, "rb") as f_in, dst.open("wb") as f_out:
        shutil.copyfileobj(f_in, f_out)


def read_fasta(p: Path) -> Dict[str, str]:
    seqs = {}
    name = None
    chunks = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith(">"):
                if name is not None:
                    seqs[name] = "".join(chunks)
                name = line[1:].split()[0]
                chunks = []
            else:
                chunks.append(line)
        if name is not None:
            seqs[name] = "".join(chunks)
    return seqs


def write_fasta(p: Path, seqs: Dict[str, str]):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as w:
        for k, v in seqs.items():
            w.write(f">{k}\n")
            for i in range(0, len(v), 60):
                w.write(v[i:i+60] + "\n")


def gene_key(seq_id: str) -> str:
    # 常见转录本：Gene.T01 / Gene.t1 / Gene.1 / Gene.P01 等
    for pat in [r"^(.*)\.[Tt]\d+$", r"^(.*)\.[Pp]\d+$", r"^(.*)\.\d+$"]:
        m = re.match(pat, seq_id)
        if m:
            return m.group(1)
    return seq_id


def pick_longest_isoform(seqs: Dict[str, str]) -> Dict[str, str]:
    best = {}
    best_len = {}
    for sid, s in seqs.items():
        g = gene_key(sid)
        L = len(s.replace("-", "").replace(".", ""))
        if g not in best_len or L > best_len[g]:
            best_len[g] = L
            best[g] = s
    # 输出的 header 用 gene_key（更干净）
    return {g: best[g] for g in best}


def detect_roles(files: List[Path]) -> Dict[str, List[Path]]:
    roles = {"genome": [], "gff": [], "pep": [], "cds": [], "other": []}
    for p in files:
        name = strip_gz(p).name.lower()
        suf = Path(name).suffix.lower()

        if suf in GFF_SUFFIX:
            roles["gff"].append(p)
        elif suf in FASTA_SUFFIX:
            # 用文件名关键词粗分 pep/cds/genome
            if "cds" in name:
                roles["cds"].append(p)
            elif any(k in name for k in ["pep", "prot", "pros", "protein"]):
                roles["pep"].append(p)
            else:
                roles["genome"].append(p)
        else:
            roles["other"].append(p)
    return roles


def choose_best(paths: List[Path]) -> Optional[Path]:
    if not paths:
        return None
    return sorted(paths, key=lambda x: x.stat().st_size, reverse=True)[0]


def main():
    ap = argparse.ArgumentParser(description="Prepare inputs from input/<job>/ into workspace/<job>/prepared/")
    ap.add_argument("--input-dir", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    in_dir = Path(args.input_dir).resolve()
    ws = Path(args.workspace).resolve()
    prepared = (ws / "prepared").resolve()
    prepared.mkdir(parents=True, exist_ok=True)

    # 收集文件
    files = [p for p in in_dir.iterdir() if p.is_file()]
    roles = detect_roles(files)

    # 选择“最像 genome”的 fasta：体积最大的那个
    genome_src = choose_best(roles["genome"])
    gff_src = choose_best(roles["gff"])
    pep_src = choose_best(roles["pep"])
    cds_src = choose_best(roles["cds"])

    # 解压/拷贝到 prepared
    def materialize(src: Optional[Path], dst_name: str) -> Optional[Path]:
        if not src:
            return None
        dst = prepared / dst_name
        if dst.exists() and (not args.force):
            return dst
        if is_gz(src):
            gunzip_to(src, dst)
        else:
            shutil.copy(src, dst)
        return dst

    genome = materialize(genome_src, f"{args.prefix}.genome.fasta") if genome_src else None
    gff = materialize(gff_src, f"{args.prefix}.gff3") if gff_src else None

    pep = materialize(pep_src, f"{args.prefix}.pep.raw.fasta") if pep_src else None
    cds = materialize(cds_src, f"{args.prefix}.cds.raw.fasta") if cds_src else None

    # 如果没 pep/cds，但有 genome+gff：尝试用 gffread 生成
    if (pep is None or cds is None) and genome and gff:
        gffread = shutil.which("gffread")
        if gffread:
            pep_out = prepared / f"{args.prefix}.pep.raw.fasta"
            cds_out = prepared / f"{args.prefix}.cds.raw.fasta"
            cmd = [gffread, str(gff), "-g", str(genome), "-y", str(pep_out), "-x", str(cds_out)]
            print("CMD:", " ".join(cmd))
            import subprocess
            subprocess.run(cmd, check=True)
            pep = pep_out if pep_out.exists() else pep
            cds = cds_out if cds_out.exists() else cds

    # 最长转录本
    final_pep = prepared / f"{args.prefix}.pep.fasta"
    final_cds = prepared / f"{args.prefix}.cds.fasta"

    if pep and (args.force or not final_pep.exists()):
        seqs = read_fasta(pep)
        write_fasta(final_pep, pick_longest_isoform(seqs))

    if cds and (args.force or not final_cds.exists()):
        seqs = read_fasta(cds)
        write_fasta(final_cds, pick_longest_isoform(seqs))

    # 输出 manifest
    out_manifest = prepared / "input.manifest.json"
    manifest = {
        "prefix": args.prefix,
        "input_dir": str(in_dir),
        "prepared_dir": str(prepared),
        "paths": {
            "genome": str(genome) if genome else None,
            "gff3": str(gff) if gff else None,
            "pep": str(final_pep) if final_pep.exists() else None,
            "cds": str(final_cds) if final_cds.exists() else None,
        },
        "note": "pep/cds are longest-isoform fasta if available; requires gffread to derive pep/cds from genome+gff.",
    }
    out_manifest.write_text(__import__("json").dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Prepared manifest:", out_manifest)
    print("pep:", manifest["paths"]["pep"])
    print("cds:", manifest["paths"]["cds"])
    print("gff3:", manifest["paths"]["gff3"])
    print("genome:", manifest["paths"]["genome"])


if __name__ == "__main__":
    main()
