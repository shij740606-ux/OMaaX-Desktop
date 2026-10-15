#!/usr/bin/env python3
"""
PPI (Protein-Protein Interaction) Analysis v2 - 带可复现性支持
支持非模式植物：通过同源基因映射查询模式植物STRING数据库

方案2+3：输出TSV数据 + 生成STRING网页链接
"""

import os
import argparse
import json
import sys
import hashlib
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import urllib.parse

sys.path.insert(0, str(Path(__file__).parent))
from string_api import StringAPI


# 添加conda环境bin目录到PATHCONDA_BIN = "/home/xia-lab/miniconda3/envs/test4py311/bin"if CONDA_BIN not in os.environ.get("PATH", ""):    os.environ["PATH"] = CONDA_BIN + ":" + os.environ.get("PATH", "")
def compute_file_hash(filepath: Path) -> str:
    """计算文件的SHA256哈希值"""
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception:
        return ""


def get_tool_versions() -> Dict[str, str]:
    """获取工具版本信息"""
    versions = {}
    
    # Python
    versions["python"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    
    # 检查 requests 版本（StringAPI 使用）
    try:
        import requests
        versions["requests"] = requests.__version__
    except ImportError:
        versions["requests"] = "not_found"
    
    return versions


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
    required_score: int = 700,
    cmd_history: List[str] = None
) -> Dict[str, Any]:
    """运行PPI分析"""
    start_time = datetime.now()
    
    result = {
        "success": False,
        "network_file": None,
        "string_url": None,
        "stats": {},
        "error": None,
        "cmd_history": cmd_history or []
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
            "total_edges": len(network_data),
            "query_genes": len(query_ids),
            "mapped_genes": len([g for g in gene_ids if ortholog_mapping and g in ortholog_mapping]) if ortholog_mapping else 0
        }
        result["stats"] = stats
        
        print(f"[INFO] Network: {stats['total_nodes']} nodes, {stats['total_edges']} edges")
        result["success"] = True
        
    except Exception as e:
        result["error"] = str(e)
        print(f"[ERROR] {e}", file=sys.stderr)
    
    result["elapsed_sec"] = (datetime.now() - start_time).total_seconds()
    return result


def write_manifest(
    output_dir: Path,
    prefix: str,
    result: Dict[str, Any],
    tool_versions: Dict[str, str],
    parameters: Dict[str, Any]
) -> Path:
    """写入符合schema的manifest文件"""
    
    manifest_data = {
        "manifest_version": "1.0",
        "stage": "ppi",
        "prefix": prefix,
        "timestamp": datetime.now().isoformat(),
        "status": "success" if result["success"] else "failed",
        "elapsed_sec": round(result.get("elapsed_sec", 0), 2),
        "tool_versions": tool_versions,
        "parameters": parameters,
        "cmd": result.get("cmd_history", []),
        "artifacts": {},
        "metrics": result.get("stats", {})
    }
    
    # 添加输出文件到 artifacts
    if result["network_file"] and Path(result["network_file"]).exists():
        net_file = Path(result["network_file"])
        manifest_data["artifacts"]["ppi_network"] = {
            "artifact": "ppi_network",
            "path": str(net_file.name),
            "full_path": str(net_file.resolve()),
            "type": "tsv",
            "size": net_file.stat().st_size,
            "sha256": compute_file_hash(net_file)
        }
    
    # 添加 URL 文件
    url_file = output_dir / f"{prefix}.ppi_url.txt"
    if url_file.exists():
        manifest_data["artifacts"]["ppi_url"] = {
            "artifact": "ppi_url",
            "path": str(url_file.name),
            "full_path": str(url_file.resolve()),
            "type": "txt",
            "size": url_file.stat().st_size,
            "sha256": compute_file_hash(url_file)
        }
    
    # 写入 manifest
    manifest_file = output_dir / f"{prefix}.ppi.manifest.json"
    with open(manifest_file, 'w') as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)
    
    return manifest_file


def write_report(
    output_dir: Path,
    prefix: str,
    result: Dict[str, Any],
    parameters: Dict[str, Any]
) -> Path:
    """写入分析报告"""
    
    report_path = output_dir / f"{prefix}.ppi_report.md"
    
    with open(report_path, 'w') as f:
        f.write(f"# PPI Network Analysis Report - {prefix}\n\n")
        f.write(f"**Generated**: {datetime.now().isoformat()}\n\n")
        
        f.write("## Analysis Overview\n")
        f.write(f"- **Status**: {'✅ Success' if result['success'] else '❌ Failed'}\n")
        f.write(f"- **Species**: {parameters.get('species', 'unknown')}\n")
        f.write(f"- **Score Threshold**: {parameters.get('score', 700)}\n")
        f.write(f"- **Query Genes**: {result.get('stats', {}).get('query_genes', 'N/A')}\n")
        f.write(f"- **Network Nodes**: {result.get('stats', {}).get('total_nodes', 'N/A')}\n")
        f.write(f"- **Network Edges**: {result.get('stats', {}).get('total_edges', 'N/A')}\n")
        f.write(f"- **Elapsed Time**: {result.get('elapsed_sec', 0):.2f} seconds\n\n")
        
        if result.get('string_url'):
            f.write("## STRING Network\n")
            f.write(f"🔗 **[View Online]({result['string_url']})**\n\n")
        
        f.write("## Output Files\n")
        if result.get('network_file'):
            f.write(f"- **Network TSV**: `{Path(result['network_file']).name}`\n")
        f.write(f"- **URL File**: `{prefix}.ppi_url.txt`\n")
        f.write(f"- **Manifest**: `{prefix}.ppi.manifest.json`\n\n")
        
        if result.get('error'):
            f.write("## Error\n")
            f.write(f"```\n{result['error']}\n```\n")
    
    return report_path


def main():
    parser = argparse.ArgumentParser(description="PPI Network Analysis v2 (with reproducibility)")
    parser.add_argument("-i", "--input", required=True, help="Input directory with gene IDs")
    parser.add_argument("-s", "--species", default="ath", help="Species code (default: ath)")
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    parser.add_argument("-p", "--prefix", default="ppi", help="Output prefix")
    parser.add_argument("--score", type=int, default=700, help="Interaction score threshold")
    parser.add_argument("--mapping", help="Ortholog mapping file")
    
    args = parser.parse_args()
    
    # 记录开始时间
    start_time = datetime.now()
    
    # 记录命令
    cmd_history = [f"python {' '.join(sys.argv)}"]
    
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    final_dir = output_dir.parent / "final"
    final_dir.mkdir(exist_ok=True)
    
    # 获取工具版本
    tool_versions = get_tool_versions()
    
    # 记录参数
    parameters = {
        "species": args.species,
        "score": args.score,
        "prefix": args.prefix,
        "has_mapping": bool(args.mapping)
    }
    
    # 读取基因ID
    gene_id_files = list(input_dir.glob("*.final_members.id")) + list(final_dir.glob("*.final_members.id"))
    
    if not gene_id_files:
        print("Error: Gene ID file not found", file=sys.stderr)
        sys.exit(1)
    
    with open(gene_id_files[0], 'r') as f:
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
        required_score=args.score,
        cmd_history=cmd_history
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
                f.write(f"  - Query Genes: {result['stats']['query_genes']}\n")
                f.write(f"  - Network Nodes: {result['stats']['total_nodes']}\n")
                f.write(f"  - Network Edges: {result['stats']['total_edges']}\n")
                if result['stats'].get('mapped_genes'):
                    f.write(f"  - Mapped Genes: {result['stats']['mapped_genes']}\n")
            print(f"[INFO] URL saved: {url_file}")
        
        # 生成 manifest
        manifest_file = write_manifest(final_dir, args.prefix, result, tool_versions, parameters)
        print(f"[INFO] Manifest saved: {manifest_file}")
        
        # 生成报告
        report_file = write_report(final_dir, args.prefix, result, parameters)
        print(f"[INFO] Report saved: {report_file}")
        
        print(f"[INFO] Results saved to {final_dir}")
    else:
        # 即使失败也生成 manifest
        write_manifest(final_dir, args.prefix, result, tool_versions, parameters)
        print(f"Error: {result.get('error', 'Unknown')}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()