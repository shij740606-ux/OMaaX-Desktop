#!/usr/bin/env python3
"""
eggNOG自动化爬虫 - 生成gene2go.txt和.emapper.annotations
1. 提交蛋白序列到eggNOG获取nogname
2. 用nogname请求GO terms、KEGG terms、COG分类
3. 生成gene2go.txt和.emapper.annotations
"""

import requests
import time
import os
import argparse
from collections import defaultdict

EGGNOG_SCAN_URL = "http://eggnogapi5.embl.de/fast_webscan"
EGGNOG_GO_URL = "http://eggnogapi5.embl.de/nog_data/json/go_terms"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json"
}


def read_fasta(fasta_file):
    sequences = []
    with open(fasta_file) as f:
        name, seq = None, []
        for line in f:
            line = line.strip()
            if line.startswith('>'):
                if name:
                    sequences.append((name, ''.join(seq)))
                name = line[1:].split()[0].replace('.T01', '')
                seq = []
            else:
                seq.append(line)
        if name:
            sequences.append((name, ''.join(seq)))
    return sequences


def submit_to_eggnog(session, fasta_seq, db="euk"):
    """提交序列到eggNOG，返回nogname (ENOG ID)"""
    payload = {"fasta": fasta_seq, "nqueries": 1, "db": db}
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


def get_go_terms(session, nogname):
    """获取GO terms"""
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


def get_kegg_terms(session, nogname):
    """获取KEGG terms"""
    url = f"http://eggnogapi5.embl.de/nog_data/json/kegg_terms/{nogname}"
    try:
        resp = session.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            kegg_ids = set()
            for pathway, terms in data.get('kegg_terms', {}).items():
                for term in terms:
                    kegg_id = term[0] if isinstance(term, list) else term
                    if kegg_id.startswith('K'):
                        kegg_ids.add(f"ko:{kegg_id}")
            return kegg_ids
    except Exception as e:
        print(f"    KEGG获取错误: {e}")
    return set()


def get_cog_category(session, nogname):
    """获取COG分类"""
    url = f"http://eggnogapi5.embl.de/nog_data/json/cog_categories/{nogname}"
    try:
        resp = session.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            categories = set()
            for cat in data.get('cog_categories', []):
                if isinstance(cat, str):
                    categories.add(cat)
            return categories
    except Exception as e:
        print(f"    COG获取错误: {e}")
    return set()


def save_emapper_annotations(filepath, data):
    """保存.emapper.annotations格式文件"""
    # 注意：R的read.csv会把#开头的行当作注释，所以header不加#
    header = "query\tseed_ortholog\tevalue\tscore\teggnog_OGs\tmax_annot_lvl\tCOG_category\tDescription\tPreferred_name\tGOs\tEC\tKEGG_ko\tKEGG_Pathway\tKEGG_Module\tKEGG_Reaction\tKEGG_rclass\tBRITE\tKEGG_TC\tCAZy\tBiGG_Reaction\tPFAMs\n"
    
    with open(filepath, 'w') as f:
        f.write(header)
        for gene_id in sorted(data.keys()):
            d = data[gene_id]
            line = f"{d['query']}\t{d['seed_ortholog']}\t{d['evalue']}\t{d['score']}\t{d['eggnog_OGs']}\t{d['max_annot_lvl']}\t{d['COG_category']}\t{d['Description']}\t{d['Preferred_name']}\t{d['GOs']}\t{d['EC']}\t{d['KEGG_ko']}\t{d['KEGG_Pathway']}\t{d['KEGG_Module']}\t{d['KEGG_Reaction']}\t{d['KEGG_rclass']}\t{d['BRITE']}\t{d['KEGG_TC']}\t{d['CAZy']}\t{d['BiGG_Reaction']}\t{d['PFAMs']}\n"
            f.write(line)


def main():
    parser = argparse.ArgumentParser(description='eggNOG自动化爬虫 - 生成gene2go.txt和.emapper.annotations')
    parser.add_argument('-i', '--input', required=True, help='蛋白FASTA文件')
    parser.add_argument('-o', '--output', required=True, help='输出gene2go.txt文件')
    parser.add_argument('--emapper-output', help='输出.emapper.annotations文件路径（可选）')
    parser.add_argument('--start', type=int, default=0, help='从第几个开始（从0开始）')
    parser.add_argument('--save-every', type=int, default=5, help='每处理多少条保存一次')
    
    args = parser.parse_args()
    
    pep_file = args.input
    output_file = args.output
    emapper_output = args.emapper_output
    
    if not emapper_output:
        emapper_output = output_file.replace('.gene2go.txt', '.emapper.annotations')
    
    if not os.path.exists(pep_file):
        print(f"错误: 文件不存在 {pep_file}")
        return
    
    sequences = read_fasta(pep_file)
    total = len(sequences)
    print(f"共 {total} 条序列")
    
    sequences = sequences[args.start:]
    print(f"待处理: {len(sequences)} 条 (start={args.start})")
    
    session = requests.Session()
    gene2go = defaultdict(set)
    emapper_data = {}
    
    # 加载已有结果（断点续传）
    if os.path.exists(output_file):
        with open(output_file) as f:
            for line in f:
                parts = line.strip().split('\t')
                if len(parts) >= 2:
                    gene2go[parts[0]].add(parts[1])
        print(f"已加载 {len(gene2go)} 个已有gene2go结果")
    
    # 同时加载emapper数据（如果存在）
    if os.path.exists(emapper_output):
        with open(emapper_output) as f:
            header = f.readline()  # 跳过header
            for line in f:
                parts = line.strip().split('\t')
                if len(parts) >= 21:
                    gene_id = parts[0]
                    emapper_data[gene_id] = {
                        'query': parts[0],
                        'seed_ortholog': parts[1],
                        'evalue': parts[2],
                        'score': parts[3],
                        'eggnog_OGs': parts[4],
                        'max_annot_lvl': parts[5],
                        'COG_category': parts[6],
                        'Description': parts[7],
                        'Preferred_name': parts[8],
                        'GOs': parts[9],
                        'EC': parts[10],
                        'KEGG_ko': parts[11],
                        'KEGG_Pathway': parts[12],
                        'KEGG_Module': parts[13],
                        'KEGG_Reaction': parts[14],
                        'KEGG_rclass': parts[15],
                        'BRITE': parts[16],
                        'KEGG_TC': parts[17],
                        'CAZy': parts[18],
                        'BiGG_Reaction': parts[19],
                        'PFAMs': parts[20]
                    }
        print(f"已加载 {len(emapper_data)} 个已有emapper结果")
    
    processed = 0
    for i, (gene_id, seq) in enumerate(sequences):
        actual_idx = args.start + i
        
        if gene_id in gene2go and gene2go[gene_id]:
            print(f"\n[{actual_idx+1}/{total}] {gene_id} (已存在，跳过)")
            continue
        
        print(f"\n[{actual_idx+1}/{total}] {gene_id}")
        
        fasta = f">{gene_id}\n{seq}"
        
        nogname = submit_to_eggnog(session, fasta)
        if not nogname:
            print(f"    获取nogname失败")
            time.sleep(2)
            continue
        
        print(f"    NOG: {nogname}")
        
        go_ids = get_go_terms(session, nogname)
        kegg_ids = get_kegg_terms(session, nogname)
        cog_cats = get_cog_category(session, nogname)
        
        print(f"    GO: {len(go_ids)}, KEGG: {len(kegg_ids)}, COG: {len(cog_cats)}")
        
        if go_ids:
            gene2go[gene_id] = go_ids
        
        emapper_data[gene_id] = {
            'query': gene_id,
            'seed_ortholog': nogname,
            'evalue': '1e-50',
            'score': '200',
            'eggnog_OGs': f"{nogname}@1|root",
            'max_annot_lvl': 'Eukaryota',
            'COG_category': ','.join(cog_cats) if cog_cats else '-',
            'Description': '-',
            'Preferred_name': gene_id,
            'GOs': ','.join(sorted(go_ids)) if go_ids else '-',
            'EC': '-',
            'KEGG_ko': ','.join(sorted(kegg_ids)) if kegg_ids else '-',
            'KEGG_Pathway': '-',
            'KEGG_Module': '-',
            'KEGG_Reaction': '-',
            'KEGG_rclass': '-',
            'BRITE': '-',
            'KEGG_TC': '-',
            'CAZy': '-',
            'BiGG_Reaction': '-',
            'PFAMs': '-'
        }
        
        
        processed += 1
        time.sleep(1)
        
        if processed % args.save_every == 0:
            with open(output_file, 'w') as f:
                for gid in sorted(gene2go.keys()):
                    for go_id in sorted(gene2go[gid]):
                        f.write(f"{gid}\t{go_id}\n")
            save_emapper_annotations(emapper_output, emapper_data)
            print(f"    [进度已保存] {processed} 个新基因")
    
    with open(output_file, 'w') as f:
        for gene_id in sorted(gene2go.keys()):
            for go_id in sorted(gene2go[gene_id]):
                f.write(f"{gene_id}\t{go_id}\n")
    
    save_emapper_annotations(emapper_output, emapper_data)
    
    print(f"\n完成!")
    print(f"  - gene2go: {len(gene2go)} 个基因 -> {output_file}")
    print(f"  - emapper: {len(emapper_data)} 个基因 -> {emapper_output}")
    session.close()


if __name__ == "__main__":
    main()
