#!/usr/bin/env python3
"""
功能注释自动适配器
根据基因家族规模和数据情况自动选择分析方法
"""

import os
import json
import re
import requests
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional, Tuple
from pathlib import Path

EGGNOG_SCAN_URL = "http://eggnogapi5.embl.de/fast_webscan"
EGGNOG_GO_URL = "http://eggnogapi5.embl.de/nog_data/json/go_terms"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json"
}


@dataclass
class AnnotationConfig:
    """功能注释配置"""
    pep_file: str = ""
    output_dir: str = ""
    use_api: bool = True  # 是否使用API
    use_local_har: bool = True  # 是否使用本地har文件
    use_local_json: bool = True  # 是否使用本地json文件
    min_genes_for_enrichment: int = 30  # 小于此数量不做GO富集
    delay: float = 1.0  # API请求间隔
    save_every: int = 10  # API注释时每处理多少条保存一次
    progress_file: str = ""  # 可选进度JSON文件，用于前端轮询


@dataclass
class AnnotationResult:
    """功能注释结果"""
    gene2go_file: str
    gene_count: int
    go_term_count: int
    method_used: str  # 'api', 'local_json', 'local_har', 'shared_nog'
    enrichment_recommended: bool
    notes: str = ""


def _write_gene2go(path: str, gene2go: Dict[str, Set[str]]) -> None:
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else '.', exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, 'w') as f:
        for gene_id in sorted(gene2go.keys()):
            for go_id in sorted(gene2go[gene_id]):
                f.write(f"{gene_id}\t{go_id}\n")
    os.replace(tmp_path, path)


def _load_gene2go(path: str) -> Dict[str, Set[str]]:
    gene2go: Dict[str, Set[str]] = defaultdict(set)
    if not os.path.exists(path):
        return gene2go
    with open(path) as f:
        for raw in f:
            parts = raw.strip().split('\t')
            if len(parts) >= 2 and parts[0] and parts[1].startswith("GO:"):
                gene2go[parts[0]].add(parts[1])
    return gene2go


def _write_progress(
    progress_file: str,
    *,
    status: str,
    total: int,
    processed: int,
    annotated: int,
    failed: int,
    current_gene: str = "",
    message: str = "",
) -> None:
    if not progress_file:
        return
    payload = {
        "stage": "annotation",
        "status": status,
        "total": total,
        "processed": processed,
        "annotated": annotated,
        "failed": failed,
        "current_gene": current_gene,
        "progress": int(processed * 100 / total) if total else 0,
        "message": message,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    path = Path(progress_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_fasta(fasta_file: str) -> List[Tuple[str, str]]:
    """读取FASTA文件"""
    sequences = []
    with open(fasta_file) as f:
        name, seq = None, []
        for line in f:
            line = line.strip()
            if line.startswith('>'):
                if name:
                    sequences.append((name, ''.join(seq)))
                name = line[1:].split()[0]
                seq = []
            else:
                seq.append(line)
        if name:
            sequences.append((name, ''.join(seq)))
    return sequences


def try_local_json(pep_file: str, output_file: str) -> Optional[AnnotationResult]:
    """尝试从本地json文件获取GO注释"""
    # 查找同目录下的eggNOG_*.json文件
    pep_dir = os.path.dirname(pep_file) or '.'
    
    # 查找json文件
    json_files = list(Path(pep_dir).glob("eggNOG_*.json")) + \
                 list(Path(pep_dir).parent.glob("gongnengzhushi/eggNOG_*.json"))
    
    for json_file in json_files:
        try:
            with open(json_file) as f:
                data = json.load(f)
            
            go_terms = data.get('go_terms', {})
            all_go_ids = set()
            for category, terms in go_terms.items():
                for term in terms:
                    go_id = term[0]
                    if go_id.startswith('GO:'):
                        all_go_ids.add(go_id)
            
            # 读取基因列表
            sequences = read_fasta(pep_file)
            
            # 生成gene2go（所有基因共享相同的GO terms）
            with open(output_file, 'w') as f:
                for gene_id, _ in sequences:
                    for go_id in sorted(all_go_ids):
                        f.write(f"{gene_id}\t{go_id}\n")
            
            return AnnotationResult(
                gene2go_file=output_file,
                gene_count=len(sequences),
                go_term_count=len(all_go_ids),
                method_used='local_json',
                enrichment_recommended=len(sequences) >= 30,
                notes=f"使用本地文件: {json_file}"
            )
        except Exception as e:
            continue
    
    return None


def try_local_har(pep_file: str, output_file: str) -> Optional[AnnotationResult]:
    """尝试从本地har文件解析GO注释"""
    pep_dir = os.path.dirname(pep_file) or '.'
    
    har_files = list(Path(pep_dir).glob("*.har*")) + \
                list(Path(pep_dir).parent.glob("gongnengzhushi/*.har*"))
    
    for har_file in har_files:
        try:
            with open(har_file) as f:
                content = f.read()
            
            # 查找go_terms数据
            match = re.search(r'"text":"(\{"go_terms":\{"[^}]+\}\})', content)
            if not match:
                continue
            
            json_str = match.group(1).replace('\\"', '"').replace('\\n', '')
            data = json.loads(json_str)
            
            go_terms = data.get('go_terms', {})
            all_go_ids = set()
            for category, terms in go_terms.items():
                for term in terms:
                    go_id = term[0]
                    if go_id.startswith('GO:'):
                        all_go_ids.add(go_id)
            
            sequences = read_fasta(pep_file)
            
            with open(output_file, 'w') as f:
                for gene_id, _ in sequences:
                    for go_id in sorted(all_go_ids):
                        f.write(f"{gene_id}\t{go_id}\n")
            
            return AnnotationResult(
                gene2go_file=output_file,
                gene_count=len(sequences),
                go_term_count=len(all_go_ids),
                method_used='local_har',
                enrichment_recommended=len(sequences) >= 30,
                notes=f"使用HAR文件: {har_file}"
            )
        except Exception as e:
            continue
    
    return None


def try_api(pep_file: str, output_file: str, delay: float = 1.0, save_every: int = 10, progress_file: str = "") -> AnnotationResult:
    """使用eggNOG API获取GO注释"""
    sequences = read_fasta(pep_file)
    session = requests.Session()
    gene2go = _load_gene2go(output_file)
    failed_genes = []
    processed = 0
    save_every = max(int(save_every or 10), 1)
    total = len(sequences)
    print(f"[注释] 已加载断点结果: {len(gene2go)} 个已有gene2go基因")
    _write_progress(
        progress_file,
        status="running",
        total=total,
        processed=0,
        annotated=len(gene2go),
        failed=0,
        message="annotation started",
    )
    
    for i, (gene_id, seq) in enumerate(sequences):
        print(f"  [{i+1}/{len(sequences)}] {gene_id}")
        if gene_id in gene2go and gene2go[gene_id]:
            processed += 1
            _write_progress(
                progress_file,
                status="running",
                total=total,
                processed=processed,
                annotated=len(gene2go),
                failed=len(failed_genes),
                current_gene=gene_id,
                message="cached gene skipped",
            )
            continue
        
        fasta = f">{gene_id}\n{seq}"
        
        # 获取nogname
        nogname = None
        try:
            resp = session.post(EGGNOG_SCAN_URL, json={"fasta": fasta, "nqueries": 1, "db": "euk"}, 
                              headers=HEADERS, timeout=60)
            if resp.status_code == 200:
                data = resp.json()
                if 'seq_matches' in data and data['seq_matches']:
                    result = data['seq_matches'][0]
                    if 'hit' in result and 'matches' in result['hit']:
                        nogname = result['hit']['matches'][0].get('nogname')
        except Exception as e:
            pass
        
        if not nogname:
            failed_genes.append(gene_id)
            processed += 1
            if processed % save_every == 0:
                _write_gene2go(output_file, gene2go)
            _write_progress(
                progress_file,
                status="running",
                total=total,
                processed=processed,
                annotated=len(gene2go),
                failed=len(failed_genes),
                current_gene=gene_id,
                message="nogname not found",
            )
            continue
        
        # 获取GO terms
        try:
            resp = session.get(f"{EGGNOG_GO_URL}/{nogname}", headers=HEADERS, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                for category, terms in data.get('go_terms', {}).items():
                    for term in terms:
                        go_id = term[0]
                        if go_id.startswith('GO:'):
                            gene2go[gene_id].add(go_id)
        except Exception as e:
            pass
        
        processed += 1
        if processed % save_every == 0:
            _write_gene2go(output_file, gene2go)
            print(f"[注释] 进度已保存: {processed}/{total}, annotated={len(gene2go)}")
        _write_progress(
            progress_file,
            status="running",
            total=total,
            processed=processed,
            annotated=len(gene2go),
            failed=len(failed_genes),
            current_gene=gene_id,
            message=f"processed {processed}/{total}",
        )
        time.sleep(delay)
    
    session.close()
    
    _write_gene2go(output_file, gene2go)
    
    all_go_terms = set()
    for gos in gene2go.values():
        all_go_terms.update(gos)
    _write_progress(
        progress_file,
        status="success",
        total=total,
        processed=processed,
        annotated=len(gene2go),
        failed=len(failed_genes),
        message="annotation completed",
    )
    
    return AnnotationResult(
        gene2go_file=output_file,
        gene_count=len(gene2go),
        go_term_count=len(all_go_terms),
        method_used='api',
        enrichment_recommended=len(gene2go) >= 30,
        notes=f"失败基因: {len(failed_genes)}" if failed_genes else ""
    )


def run_annotation(pep_file: str, output_dir: str = ".", config: AnnotationConfig = None) -> AnnotationResult:
    """
    自动运行功能注释
    优先级: 本地json > 本地har > API
    """
    if config is None:
        config = AnnotationConfig()
    
    # 设置输出文件
    output_file = os.path.join(output_dir, "gene2go.txt")
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. 尝试本地json
    if config.use_local_json:
        print("[注释] 尝试本地json文件...")
        result = try_local_json(pep_file, output_file)
        if result:
            print(f"[注释] 成功使用local_json: {result.gene_count}基因, {result.go_term_count}GO terms")
            return result
    
    # 2. 尝试本地har
    if config.use_local_har:
        print("[注释] 尝试本地har文件...")
        result = try_local_har(pep_file, output_file)
        if result:
            print(f"[注释] 成功使用local_har: {result.gene_count}基因, {result.go_term_count}GO terms")
            return result
    
    # 3. 使用API
    if config.use_api:
        print("[注释] 使用eggNOG API...")
        result = try_api(pep_file, output_file, config.delay, config.save_every, config.progress_file)
        print(f"[注释] 成功使用api: {result.gene_count}基因, {result.go_term_count}GO terms")
        return result
    
    raise RuntimeError("所有注释方法都失败")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='功能注释自动适配器')
    parser.add_argument('-i', '--input', required=True, help='蛋白FASTA文件')
    parser.add_argument('-o', '--output', default='gene2go.txt', help='输出文件')
    parser.add_argument('--no-api', action='store_true', help='不使用API')
    parser.add_argument('--delay', type=float, default=1.0, help='API请求间隔')
    args = parser.parse_args()
    
    config = AnnotationConfig(use_api=not args.no_api, delay=args.delay)
    result = run_annotation(args.input, os.path.dirname(args.output) or '.', config)
    
    print(f"\n=== 结果 ===")
    print(f"方法: {result.method_used}")
    print(f"基因数: {result.gene_count}")
    print(f"GO terms: {result.go_term_count}")
    print(f"建议富集分析: {'是' if result.enrichment_recommended else '否'}")
    if result.notes:
        print(f"备注: {result.notes}")
