#!/usr/bin/env python3
"""
PPI (Protein-Protein Interaction) Analysis v2
支持非模式植物：通过同源基因映射查询模式植物STRING数据库

方案2+3：输出TSV数据 + 生成STRING网页链接
"""

import os
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
import hashlib
import urllib.parse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from string_api import StringAPI
from gfa_core.mas.effective_members import (
    MemberResolutionError,
    candidate_member_prefixes,
    resolve_effective_members,
)


# 镜像标准形态: 当前解释器即环境解释器(PATH 由容器提供)
CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
def parse_ortholog_mapping(mapping_file: Path) -> Dict[str, str]:
    """解析同源基因映射文件"""
    mapping = {}
    with open(mapping_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) >= 2:
                source_id = parts[0].strip()
                target_id = parts[1].strip()
                target_id_base = target_id.split('.')[0]
                mapping[source_id] = target_id_base
    return mapping


def generate_string_url(proteins: List[str], species: str = "3702") -> str:
    """生成STRING网页链接"""
    base_url = "https://string-db.org/cgi/network"
    params = {
        "identifiers": "%0d".join(proteins),
        "species": species,
        "required_score": "400",
        "network_type": "functional"
    }
    return f"{base_url}?{urllib.parse.urlencode(params)}"


def run_ppi_analysis_v2(
    gene_ids: List[str],
    ortholog_mapping: Optional[Dict[str, str]],
    species: str,
    output_dir: Path,
    prefix: str,
    required_score: int = 200
) -> Dict[str, Any]:
    """运行PPI分析"""
    result = {
        "success": False,
        "network_file": None,
        "string_url": None,
        "stats": {},
        "error": None
    }
    
    try:
        # 映射基因ID
        if ortholog_mapping:
            mapped_ids = []
            for gene_id in gene_ids:
                if gene_id in ortholog_mapping:
                    mapped_ids.append(ortholog_mapping[gene_id])
            print(f"[INFO] Mapped {len(mapped_ids)}/{len(gene_ids)} genes")
            if not mapped_ids:
                result["error"] = "No genes could be mapped"
                return result
            query_ids = mapped_ids
        else:
            query_ids = [g.split('.')[0] for g in gene_ids]
        
        print(f"[INFO] Querying STRING for {len(query_ids)} genes...")
        
        api = StringAPI(species=species)
        network_data = api.get_network(
            proteins=query_ids,
            required_score=required_score
        )
        
        if isinstance(network_data, dict) and "error" in network_data:
            result["error"] = network_data["error"]
            return result
        
        if not network_data:
            result["error"] = f"No PPI interactions found"
            return result
        
        # 生成STRING网页链接
        string_url = generate_string_url(query_ids, api.species_id)
        result["string_url"] = string_url
        print(f"[INFO] STRING URL: {string_url}")
        
        # 保存TSV数据
        network_file = output_dir / f"{prefix}.ppi_network.tsv"
        with open(network_file, 'w') as f:
            f.write("protein1\tprotein2\tcombined_score\n")
            for interaction in network_data:
                node1 = interaction.get('preferredName_A', '')
                node2 = interaction.get('preferredName_B', '')
                score = int(interaction.get('score', 0) * 1000)
                f.write(f"{node1}\t{node2}\t{score}\n")
        
        result["network_file"] = network_file
        
        stats = {
            "total_nodes": len(set([n for i in network_data for n in [i.get('preferredName_A'), i.get('preferredName_B')]])),
            "total_edges": len(network_data)
        }
        result["stats"] = stats
        
        print(f"[INFO] Network: {stats['total_nodes']} nodes, {stats['total_edges']} edges")
        result["success"] = True
        
    except Exception as e:
        result["error"] = str(e)
        print(f"[ERROR] {e}", file=sys.stderr)
    
    return result


def main():
    parser = argparse.ArgumentParser(description="PPI Network Analysis v2")
    parser.add_argument("-i", "--input", required=True)
    parser.add_argument("-s", "--species", default="ath")
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("-p", "--prefix", default="ppi")
    parser.add_argument("--score", type=int, default=200)
    parser.add_argument("--mapping", help="Ortholog mapping file")
    
    args = parser.parse_args()
    
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    final_dir = output_dir.parent / "final"
    final_dir.mkdir(exist_ok=True)
    
    # 读取基因ID
    gene_id_file = None
    last_resolution_error = ""
    for member_prefix in candidate_member_prefixes(final_dir, input_dir, args.prefix):
        try:
            resolved = resolve_effective_members(
                workspace=output_dir.parent,
                final_dir=final_dir,
                input_dir=input_dir,
                prefix=member_prefix,
                phase="auto",
                require="id",
            )
            if resolved.path_id:
                gene_id_file = resolved.path_id
                if resolved.warnings:
                    print(f"[WARN] {'; '.join(resolved.warnings)}")
                print(f"[INFO] Member source: {resolved.source_kind} ({resolved.member_count} IDs)")
                break
        except MemberResolutionError as exc:
            last_resolution_error = str(exc)
    if not gene_id_file:
        print(f"Error: Gene ID file not found via effective member resolver. {last_resolution_error}", file=sys.stderr)
        sys.exit(1)

    with open(gene_id_file, 'r') as f:
        gene_ids = [line.strip() for line in f if line.strip()]
    
    print(f"[INFO] Loaded {len(gene_ids)} gene IDs")
    
    # 读取同源映射
    ortholog_mapping = None
    if args.mapping:
        mapping_file = Path(args.mapping)
        if mapping_file.exists():
            ortholog_mapping = parse_ortholog_mapping(mapping_file)
            print(f"[INFO] Loaded {len(ortholog_mapping)} mappings")
    
    # 运行分析
    result = run_ppi_analysis_v2(
        gene_ids=gene_ids,
        ortholog_mapping=ortholog_mapping,
        species=args.species,
        output_dir=final_dir,
        prefix=args.prefix,
        required_score=args.score
    )
    
    if result["success"]:
        # 保存 STRING 链接到文件
        if result["string_url"]:
            url_file = final_dir / f"{args.prefix}.ppi_url.txt"
            with open(url_file, 'w') as f:
                f.write(f"STRING Protein Interaction Network\n")
                f.write(f"{'='*50}\n\n")
                f.write(f"View online: {result['string_url']}\n\n")
                f.write(f"Network Statistics:\n")
                f.write(f"  - Nodes: {result['stats']['total_nodes']}\n")
                f.write(f"  - Edges: {result['stats']['total_edges']}\n")
            print(f"[INFO] URL saved: {url_file}")
            print(f"[INFO] View online: {result['string_url']}")
        
        print(f"[INFO] Results saved to {final_dir}")
    else:
        print(f"Error: {result.get('error', 'Unknown')}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
