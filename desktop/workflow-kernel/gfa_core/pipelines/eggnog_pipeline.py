#!/usr/bin/env python3
"""
eggNOG GO注释Pipeline
从蛋白序列自动获取GO注释
"""

import os
import requests
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Optional

EGGNOG_SCAN_URL = "http://eggnogapi5.embl.de/fast_webscan"
EGGNOG_GO_URL = "http://eggnogapi5.embl.de/nog_data/json/go_terms"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json"
}


@dataclass
class EggNOGOutput:
    gene2go_file: str
    gene_count: int
    go_term_count: int
    failed_genes: List[str] = field(default_factory=list)


def read_fasta(fasta_file: str) -> List[tuple]:
    """读取FASTA文件，返回[(gene_id, sequence), ...]"""
    sequences = []
    with open(fasta_file) as f:
        name, seq = None, []
        for line in f:
            line = line.strip()
            if line.startswith('>'):
                if name:
                    sequences.append((name, ''.join(seq)))
                name = line[1:].split()[0]  # 去掉.T01等后缀
                seq = []
            else:
                seq.append(line)
        if name:
            sequences.append((name, ''.join(seq)))
    return sequences


def submit_to_eggnog(session: requests.Session, fasta_seq: str) -> Optional[str]:
    """提交序列到eggNOG，返回nogname"""
    payload = {"fasta": fasta_seq, "nqueries": 1, "db": "euk"}
    try:
        resp = session.post(EGGNOG_SCAN_URL, json=payload, headers=HEADERS, timeout=60)
        if resp.status_code == 200:
            data = resp.json()
            if 'seq_matches' in data and data['seq_matches']:
                result = data['seq_matches'][0]
                if 'hit' in result:
                    hit = result['hit']
                    if 'matches' in hit and hit['matches']:
                        return hit['matches'][0].get('nogname')
    except Exception as e:
        print(f"    提交错误: {e}")
    return None


def get_go_terms(session: requests.Session, nogname: str) -> Set[str]:
    """获取GO terms"""
    if not nogname:
        return set()
    url = f"{EGGNOG_GO_URL}/{nogname}"
    try:
        resp = session.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            go_ids = set()
            for category, terms in data.get('go_terms', {}).items():
                for term in terms:
                    go_id = term[0]
                    if go_id.startswith('GO:'):
                        go_ids.add(go_id)
            return go_ids
    except Exception as e:
        print(f"    GO获取错误: {e}")
    return set()


def run_eggnog_pipeline(
    pep_file: str,
    output_file: str,
    delay: float = 1.0
) -> EggNOGOutput:
    """
    运行eggNOG GO注释流程
    
    Args:
        pep_file: 蛋白FASTA文件路径
        output_file: 输出gene2go.txt路径
        delay: 请求间隔(秒)
    
    Returns:
        EggNOGOutput结果对象
    """
    if not os.path.exists(pep_file):
        raise FileNotFoundError(f"蛋白文件不存在: {pep_file}")
    
    # 读取序列
    sequences = read_fasta(pep_file)
    print(f"[eggNOG] 共 {len(sequences)} 条序列")
    
    session = requests.Session()
    gene2go = defaultdict(set)
    failed_genes = []
    
    for i, (gene_id, seq) in enumerate(sequences):
        print(f"[eggNOG] [{i+1}/{len(sequences)}] {gene_id}")
        
        fasta = f">{gene_id}\n{seq}"
        
        # 1. 获取nogname
        nogname = submit_to_eggnog(session, fasta)
        if not nogname:
            print(f"    获取nogname失败")
            failed_genes.append(gene_id)
            time.sleep(delay)
            continue
        
        # 2. 获取GO terms
        go_ids = get_go_terms(session, nogname)
        gene2go[gene_id] = go_ids
        time.sleep(delay)
    
    session.close()
    
    # 保存结果
    os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else '.', exist_ok=True)
    with open(output_file, 'w') as f:
        for gene_id in sorted(gene2go.keys()):
            for go_id in sorted(gene2go[gene_id]):
                f.write(f"{gene_id}\t{go_id}\n")
    
    # 统计
    all_go_terms = set()
    for gos in gene2go.values():
        all_go_terms.update(gos)
    
    return EggNOGOutput(
        gene2go_file=output_file,
        gene_count=len(gene2go),
        go_term_count=len(all_go_terms),
        failed_genes=failed_genes
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='eggNOG GO注释Pipeline')
    parser.add_argument('-i', '--input', required=True, help='蛋白FASTA文件')
    parser.add_argument('-o', '--output', required=True, help='输出gene2go.txt')
    parser.add_argument('--delay', type=float, default=1.0, help='请求间隔(秒)')
    args = parser.parse_args()
    
    result = run_eggnog_pipeline(args.input, args.output, args.delay)
    print(f"\n完成! {result.gene_count} 个基因, {result.go_term_count} 个GO terms")
    if result.failed_genes:
        print(f"失败: {len(result.failed_genes)} 个基因")