#!/usr/bin/env python3
"""
PPI (Protein-Protein Interaction) Analysis阶段
基于STRING数据库的蛋白互作网络分析

输入：
  - 基因家族ID列表
  - 物种代码

输出：
  - PPI互作网络图（PDF/PNG）
  - 互作数据表（TSV）
  - 网络统计报告
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import hashlib
import os
import shutil

# 导入STRING API
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


CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
if CONDA_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = CONDA_BIN + os.pathsep + os.environ.get("PATH", "")
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
    """获取所需工具版本信息"""
    versions = {}
    
    # Python requests
    try:
        import requests
        versions["requests"] = requests.__version__
    except Exception:
        versions["requests"] = "not_found"
    
    # R (用于可视化)
    try:
        result = subprocess.run(
            ["R", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        output = result.stdout.strip() or result.stderr.strip()
        versions["R"] = output.split('\n')[0] if output else "unknown"
    except Exception:
        versions["R"] = "not_found"
    
    return versions


def run_ppi_analysis(
    gene_ids: List[str],
    species: str,
    output_dir: Path,
    prefix: str,
    required_score: int = 200,
    network_type: str = "functional"
) -> Dict[str, Any]:
    """
    运行PPI分析
    
    Args:
        gene_ids: 基因ID列表
        species: 物种代码
        output_dir: 输出目录
        prefix: 输出前缀
        required_score: 互作置信度阈值（0-1000）
        network_type: 网络类型
    
    Returns:
        分析结果字典
    """
    result = {
        "success": False,
        "network_file": None,
        "plot_file": None,
        "stats": {},
        "error": None
    }
    
    # 初始化STRING API
    api = StringAPI(species=species)
    
    print(f"[INFO] Querying STRING database for {len(gene_ids)} genes...")
    print(f"[INFO] Species: {species} (ID: {api.species_id})")
    print(f"[INFO] Confidence score threshold: {required_score}")
    
    # 获取互作网络
    network_data = api.get_network(
        proteins=gene_ids,
        required_score=required_score,
        network_type=network_type
    )
    
    if "error" in network_data:
        result["error"] = network_data["error"]
        return result
    
    # 解析互作数据
    interactions = []
    proteins_in_network = set()
    
    for interaction in network_data:
        if isinstance(interaction, dict):
            p1 = interaction.get("preferredName_A", "")
            p2 = interaction.get("preferredName_B", "")
            score = interaction.get("score", 0)
            
            interactions.append({
                "protein1": p1,
                "protein2": p2,
                "score": score,
                "ncbi_tax_id": interaction.get("ncbiTaxonId", ""),
            })
            
            proteins_in_network.add(p1)
            proteins_in_network.add(p2)
    
    # 统计信息
    scores = [i["score"] for i in interactions]
    normalized_scores = [s / 1000 if s > 1 else s for s in scores]
    stats = {
        "input_genes": len(gene_ids),
        "genes_in_network": len(proteins_in_network),
        "total_interactions": len(interactions),
        "avg_score": sum(normalized_scores) / len(normalized_scores) if normalized_scores else 0,
        "high_confidence": sum(1 for score in normalized_scores if score >= 0.7),
        "medium_confidence": sum(1 for score in normalized_scores if 0.4 <= score < 0.7),
        "low_confidence": sum(1 for score in normalized_scores if score < 0.4),
    }
    
    result["stats"] = stats
    
    print(f"[INFO] Network statistics:")
    print(f"  - Genes in network: {stats['genes_in_network']}/{stats['input_genes']}")
    print(f"  - Total interactions: {stats['total_interactions']}")
    print(f"  - High confidence (≥0.7): {stats['high_confidence']}")
    print(f"  - Medium confidence (0.4-0.7): {stats['medium_confidence']}")
    print(f"  - Low confidence (<0.4): {stats['low_confidence']}")
    
    # 保存网络数据
    network_file = output_dir / f"{prefix}.ppi_network.tsv"
    
    import pandas as pd
    df = pd.DataFrame(interactions)
    df.to_csv(network_file, sep="\t", index=False)
    
    result["network_file"] = network_file
    print(f"[INFO] Network data saved: {network_file}")
    
    # 生成可视化
    plot_file = output_dir / f"{prefix}.ppi_network.pdf"
    
    if interactions:
        success = generate_network_plot(
            network_data=interactions,
            output_file=plot_file,
            prefix=prefix
        )
        if success:
            result["plot_file"] = plot_file
            png_file = output_dir / f"{prefix}.ppi_network.png"
            try:
                if shutil.which("convert"):
                    subprocess.run(
                        ["convert", "-density", "300", str(plot_file), str(png_file)],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=120,
                    )
                elif shutil.which("pdftoppm"):
                    subprocess.run(
                        ["pdftoppm", "-png", "-singlefile", "-r", "300", str(plot_file), str(png_file.with_suffix(""))],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=120,
                    )
                if png_file.exists() and png_file.stat().st_size > 0:
                    result["plot_png_file"] = png_file
            except Exception as exc:
                print(f"[WARNING] PDF to PNG conversion failed: {exc}")
            print(f"[INFO] Network plot saved: {plot_file}")
    else:
        print("[WARNING] No interactions found, skipping plot generation")
    
    result["success"] = True
    return result


def normalize_gene_ids_for_string(gene_ids: List[str], species: str) -> List[str]:
    """Convert local transcript IDs to identifiers recognized by STRING."""
    normalized = []
    seen = set()
    for gene_id in gene_ids:
        value = gene_id.strip()
        if species.lower() == "osa":
            match = re.match(r"^(Os\d{2})t(\d+)(?:-\d+)?$", value)
            if match:
                value = f"{match.group(1)}g{match.group(2)}"
        if value and value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized


def generate_network_plot(
    network_data: List[Dict],
    output_file: Path,
    prefix: str
) -> bool:
    """
    生成PPI网络图
    
    Args:
        network_data: 网络边列表
        output_file: 输出PDF文件
        prefix: 前缀
    
    Returns:
        是否成功
    """
    import pandas as pd
    import tempfile
    
    # 创建临时文件存储网络数据
    with tempfile.NamedTemporaryFile(mode='w', suffix='.tsv', delete=False) as f:
        temp_network = f.name
        df = pd.DataFrame(network_data)
        df.to_csv(f, sep="\t", index=False)
    
    # 查找R脚本
    script_dir = Path(__file__).parent.parent.parent
    r_script = script_dir / "scripts" / "draw_ppi_network.R"
    
    if not r_script.exists():
        print(f"[WARNING] R script not found: {r_script}")
        # 删除临时文件
        os.unlink(temp_network)
        return False
    
    # 构建R命令 - 使用完整路径避免conda run的全局锁
    rscript_path = str(shutil.which("Rscript") or "Rscript")
    cmd = [
        rscript_path, str(r_script),
        "--network", temp_network,
        "--output", str(output_file),
        "--prefix", prefix
    ]
    
    print(f"[INFO] Generating network plot...")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5分钟超时
        )
        
        # 删除临时文件
        os.unlink(temp_network)
        
        if result.returncode == 0:
            print(f"[INFO] Network plot generated successfully")
            return True
        else:
            print(f"[WARNING] Plot generation failed: {result.stderr[:200]}")
            return False
            
    except Exception as e:
        print(f"[WARNING] Exception during plot generation: {e}")
        # 删除临时文件
        if os.path.exists(temp_network):
            os.unlink(temp_network)
        return False


def generate_report(
    output_dir: Path,
    stats: Dict[str, Any],
    versions: Dict[str, str],
    start_time: datetime,
    end_time: datetime,
    prefix: str = ""
) -> Path:
    """生成PPI分析报告"""
    report_name = f"{prefix}.ppi_report.md" if prefix else "ppi_report.md"
    report_file = output_dir / report_name
    
    elapsed = (end_time - start_time).total_seconds()
    
    content = f"""# PPI Network Analysis Report

## Summary

- **Analysis Date**: {end_time.strftime("%Y-%m-%d %H:%M:%S")}
- **Total Runtime**: {elapsed:.1f} seconds
- **Data Source**: STRING Database (https://string-db.org)

## Tool Versions

"""
    for tool, version in versions.items():
        content += f"- **{tool}**: {version}\n"
    
    content += f"""
## Network Statistics

| Metric | Value |
|--------|-------|
| Input Genes | {stats.get('input_genes', 'N/A')} |
| Genes in Network | {stats.get('genes_in_network', 'N/A')} |
| Total Interactions | {stats.get('total_interactions', 'N/A')} |
| Average Score | {stats.get('avg_score', 'N/A'):.3f} |
| High Confidence (≥0.7) | {stats.get('high_confidence', 'N/A')} |
| Medium Confidence (0.4-0.7) | {stats.get('medium_confidence', 'N/A')} |
| Low Confidence (<0.4) | {stats.get('low_confidence', 'N/A')} |

## Output Files

- Network data: `{prefix}.ppi_network.tsv`
- Network plot: `{prefix}.ppi_network.pdf`

## Methods

Protein-protein interaction data was retrieved from the STRING database 
(v12.0) using the API. Interactions with confidence scores above the 
threshold were included in the network analysis.

"""
    
    with open(report_file, 'w') as f:
        f.write(content)
    
    return report_file


def generate_manifest(
    output_dir: Path,
    stats: Dict[str, Any],
    versions: Dict[str, str],
    start_time: datetime,
    end_time: datetime,
    cmd: List[str],
    input_dir: Path,
    workspace_dir: Path,
    job_id: str,
    run_id: str,
    prefix: str,
    required_score: int,
    network_type: str
) -> Path:
    """生成manifest文件"""
    manifest_name = f"{prefix}.ppi.manifest.json" if prefix else "ppi.manifest.json"
    manifest_file = output_dir / manifest_name
    
    manifest = {
        "manifest_version": "1.0",
        "stage": "ppi",
        "status": "success",
        "job_id": job_id,
        "run_id": run_id,
        "timestamp": end_time.isoformat(),
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "elapsed_sec": (end_time - start_time).total_seconds(),
        "cmd": cmd,
        "workspace_root": str(output_dir.parent.parent),
        "workspace": str(output_dir),
        "input_dir": str(input_dir),
        "metrics": stats,
        "parameters": {
            "required_score": required_score,
            "network_type": network_type
        },
        "artifacts": {},
        "tools": versions
    }
    
    # 添加artifacts
    def add_artifact(name: str, filepath: Path, artifact_type: str, **extra):
        if filepath and filepath.exists():
            manifest["artifacts"][name] = {
                "artifact": name,
                "path": f"final/{filepath.name}",
                "full_path": str(filepath.absolute()),
                "type": artifact_type,
                "size": filepath.stat().st_size,
                "sha256": compute_file_hash(filepath),
                **extra
            }
    
    network_file = output_dir / f"{prefix}.ppi_network.tsv"
    plot_file = output_dir / f"{prefix}.ppi_network.pdf"
    plot_png_file = output_dir / f"{prefix}.ppi_network.png"
    report_file = output_dir / f"{prefix}.ppi_report.md"
    
    add_artifact("network", network_file, "tsv", format="tsv")
    add_artifact("plot", plot_file, "pdf", format="pdf")
    add_artifact("plot_png", plot_png_file, "png", format="png")
    add_artifact("report", report_file, "markdown", format="md")
    
    # manifest自身
    manifest["artifacts"]["manifest"] = {
        "artifact": "manifest",
        "path": f"final/{manifest_name}",
        "type": "json",
        "format": "json"
    }
    
    with open(manifest_file, 'w') as f:
        json.dump(manifest, f, indent=2)
    
    return manifest_file


def main():
    parser = argparse.ArgumentParser(description="PPI Network Analysis")
    parser.add_argument("-i", "--input", required=True, help="Input directory with gene ID file")
    parser.add_argument("-s", "--species", default="ath", help="Species code (ath, osa, zma, etc.)")
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    parser.add_argument("-p", "--prefix", default="ppi", help="Output prefix")
    parser.add_argument("-j", "--job-id", default="unknown", help="Job ID")
    parser.add_argument("-r", "--run-id", default="unknown", help="Run ID")
    parser.add_argument("--score", type=int, default=200, help="Confidence score threshold (0-1000)")
    parser.add_argument("--network-type", default="functional", choices=["functional", "physical"], 
                        help="Network type")
    
    args = parser.parse_args()
    
    start_time = datetime.now()
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    
    # 创建输出目录
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir = output_dir.parent
    final_dir = workspace_dir / "final"
    final_dir.mkdir(exist_ok=True)
    
    # 查找基因ID文件
    gene_id_file = None
    last_resolution_error = ""
    for member_prefix in candidate_member_prefixes(final_dir, input_dir, args.prefix):
        try:
            resolved = resolve_effective_members(
                workspace=workspace_dir,
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

    print(f"[INFO] Using gene IDs from: {gene_id_file}")
    
    # 读取基因ID
    gene_ids = []
    with open(gene_id_file, 'r') as f:
        for line in f:
            gene_id = line.strip()
            if gene_id:
                gene_ids.append(gene_id)
    
    print(f"[INFO] Loaded {len(gene_ids)} gene IDs")
    string_gene_ids = normalize_gene_ids_for_string(gene_ids, args.species)
    if string_gene_ids != gene_ids:
        print(f"[INFO] Converted IDs for STRING lookup: {len(gene_ids)} input IDs -> {len(string_gene_ids)} query IDs")
    
    # 获取工具版本
    versions = get_tool_versions()
    print(f"Tool versions: {versions}")
    
    # 运行PPI分析
    result = run_ppi_analysis(
        gene_ids=string_gene_ids,
        species=args.species,
        output_dir=final_dir,
        prefix=args.prefix,
        required_score=args.score,
        network_type=args.network_type
    )
    
    if not result["success"]:
        print(f"Error: {result.get('error', 'Unknown error')}", file=sys.stderr)
        sys.exit(1)
    
    # 生成报告
    end_time = datetime.now()
    generate_report(final_dir, result["stats"], versions, start_time, end_time, args.prefix)
    
    # 生成manifest
    cmd = ["python", __file__, "-i", str(input_dir), "-s", args.species,
           "-o", str(output_dir), "-p", args.prefix, "-j", args.job_id, "-r", args.run_id]
    generate_manifest(
        final_dir, result["stats"], versions, start_time, end_time,
        cmd, input_dir, workspace_dir, args.job_id, args.run_id, args.prefix,
        args.score, args.network_type
    )
    
    print(f"PPI analysis completed. Results in {final_dir}")


if __name__ == "__main__":
    main()
