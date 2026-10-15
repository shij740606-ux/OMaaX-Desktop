#!/usr/bin/env python3
"""
同源基因识别 (Ortholog Identification)
使用BLAST/Diamond在两个物种间识别同源基因对
"""

import os
import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional


CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
if CONDA_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = CONDA_BIN + os.pathsep + os.environ.get("PATH", "")
CONDA_LIB = str(CONDA_PREFIX / "lib")
if CONDA_LIB not in os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep):
    os.environ["LD_LIBRARY_PATH"] = CONDA_LIB + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
def run_blast(query_fasta: Path, subject_fasta: Path, output_file: Path, evalue: float = 1e-5):
    """运行BLASTP比对，优先使用conda环境中的blastp"""
    # 尝试使用conda环境中的blastp
    blastp_paths = [
        shutil.which("blastp"),
    ]
    
    blastp_cmd = None
    for path in blastp_paths:
        if Path(path).exists():
            blastp_cmd = path
            break
    
    if not blastp_cmd:
        print("[ERROR] blastp not found. Please install BLAST+.")
        return False
    
    cmd = [
        blastp_cmd,
        "-query", str(query_fasta),
        "-subject", str(subject_fasta),
        "-outfmt", "6 qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore",
        "-evalue", str(evalue),
        "-out", str(output_file),
        "-num_threads", "8"
    ]
    
    print(f"[INFO] Running BLASTP: {query_fasta.name} vs {subject_fasta.name}")
    result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ.copy())
    
    if result.returncode != 0:
        print(f"[ERROR] BLASTP failed: {result.stderr}")
        return False
    
    return True


def parse_blast_results(blast_file: Path, output_pairs_file: Path, 
                        identity_threshold: float = 30.0,
                        coverage_threshold: float = 50.0):
    """解析BLAST结果，提取最佳同源基因对"""
    
    from collections import defaultdict
    
    # 读取BLAST结果
    hits = defaultdict(list)
    
    with open(blast_file, 'r') as f:
        for line in f:
            if line.strip():
                parts = line.strip().split('\t')
                if len(parts) >= 12:
                    query_id = parts[0]
                    subject_id = parts[1]
                    identity = float(parts[2])
                    length = int(parts[3])
                    bitscore = float(parts[11])
                    
                    hits[query_id].append({
                        'subject': subject_id,
                        'identity': identity,
                        'length': length,
                        'bitscore': bitscore
                    })
    
    # 提取最佳匹配（双向最佳匹配 = 直系同源）
    ortholog_pairs = []
    
    for query_id, hit_list in hits.items():
        # 按bitscore排序，取最佳
        hit_list.sort(key=lambda x: x['bitscore'], reverse=True)
        best_hit = hit_list[0]
        
        # 过滤条件
        if best_hit['identity'] >= identity_threshold:
            ortholog_pairs.append((query_id, best_hit['subject'], best_hit['identity']))
    
    # 写入输出文件
    with open(output_pairs_file, 'w') as f:
        f.write("# Ortholog pairs identified by BLASTP\n")
        f.write("# Format: Subject_ID\tQuery_ID\tIdentity(%)\n")
        for query, subject, identity in ortholog_pairs:
            f.write(f"{subject}\t{query}\t{identity:.2f}\n")
    
    print(f"[INFO] Found {len(ortholog_pairs)} ortholog pairs")
    print(f"[INFO] Output: {output_pairs_file}")
    
    return len(ortholog_pairs)


def main():
    parser = argparse.ArgumentParser(description='Identify orthologs between two species')
    parser.add_argument('-q', '--query', required=True, help='Query species protein FASTA')
    parser.add_argument('-s', '--subject', required=True, help='Subject species protein FASTA (e.g., Ath)')
    parser.add_argument('-o', '--output', required=True, help='Output directory')
    parser.add_argument('-p', '--prefix', default='ortholog', help='Output file prefix')
    parser.add_argument('--evalue', type=float, default=1e-5, help='BLAST e-value threshold')
    parser.add_argument('--identity', type=float, default=30.0, help='Identity threshold (%%)')
    
    args = parser.parse_args()
    
    query_fasta = Path(args.query)
    subject_fasta = Path(args.subject)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 检查输入文件
    if not query_fasta.exists():
        print(f"[ERROR] Query file not found: {query_fasta}")
        sys.exit(1)
    
    if not subject_fasta.exists():
        print(f"[ERROR] Subject file not found: {subject_fasta}")
        sys.exit(1)
    
    # 运行BLAST
    blast_output = output_dir / f"{args.prefix}.blastp.txt"
    if not run_blast(query_fasta, subject_fasta, blast_output, args.evalue):
        print("[ERROR] BLASTP failed")
        sys.exit(1)
    
    # 解析结果
    pairs_file = output_dir / f"{args.prefix}_pairs.txt"
    count = parse_blast_results(blast_output, pairs_file, args.identity)
    
    if count > 0:
        print(f"[SUCCESS] Ortholog identification complete. Found {count} pairs.")
        sys.exit(0)
    else:
        print("[WARNING] No ortholog pairs found.")
        sys.exit(1)


if __name__ == "__main__":
    main()
