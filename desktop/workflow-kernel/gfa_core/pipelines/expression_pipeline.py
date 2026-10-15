#!/usr/bin/env python3
"""
表达分析Pipeline
整合差异表达分析 + 功能注释
自动适配小/大家族
"""

import os
import shutil
import subprocess
import pandas as pd
import numpy as np
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from pathlib import Path


@dataclass
class ExpressionConfig:
    """表达分析配置"""
    fpkm_file: str = ""
    sample_file: str = ""
    gene_list: str = ""
    output_prefix: str = "expression"
    r_script: str = "scripts/draw_expression.R"
    
    # 差异表达参数
    min_log2fc: float = 1.0  # 最小log2 fold change
    min_expression: float = 1.0  # 最小表达量
    
    # 功能注释
    gene2go_file: str = ""


@dataclass
class ExpressionResult:
    """表达分析结果"""
    exp_file: str  # 表达量矩阵
    diff_genes_x3: List[str] = field(default_factory=list)  # X3差异基因
    diff_genes_x4: List[str] = field(default_factory=list)  # X4差异基因
    all_diff_genes: List[str] = field(default_factory=list)  # 所有差异基因
    gene2go_file: str = ""
    plots: List[str] = field(default_factory=list)
    
    # 统计
    total_genes: int = 0
    diff_count_x3: int = 0
    diff_count_x4: int = 0


def run_r_expression(config: ExpressionConfig) -> ExpressionResult:
    """运行R脚本进行差异表达分析"""
    # 准备基因列表（去.T01后缀）
    if config.gene_list:
        gene_list_new = config.gene_list + "_new"
        with open(config.gene_list) as f:
            genes = [g.strip() for g in f if g.strip()]
        genes_new = [g.replace('.T01', '') for g in genes]
        with open(gene_list_new, 'w') as f:
            f.write('\n'.join(genes_new))
        config.gene_list = gene_list_new
    
    # 运行R脚本
    cmd = [
        "Rscript", config.r_script,
        config.fpkm_file,
        config.gene_list,
        config.sample_file,
        config.output_prefix
    ]
    
    # 确保使用正确的R
    r_exe = shutil.which("Rscript")
    if r_exe:
        cmd[0] = r_exe
    
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=os.path.dirname(config.fpkm_file) or '.')
    
    if result.returncode != 0:
        print(f"R脚本警告: {result.stderr}")
    
    # 读取结果文件
    exp_file = config.output_prefix + ".gene-exp.txt"
    if not os.path.exists(exp_file):
        exp_file = os.path.join(os.path.dirname(config.fpkm_file), config.output_prefix + ".gene-exp.txt")
    
    return exp_file


def find_differential_genes(exp_file: str, config: ExpressionConfig) -> ExpressionResult:
    """筛选差异表达基因"""
    # 读取表达量
    if not os.path.exists(exp_file):
        raise FileNotFoundError(f"表达量文件不存在: {exp_file}")
    
    exp = pd.read_csv(exp_file, sep='\t', index_col=0)
    
    # 计算各组平均表达量
    sample_info = {}
    with open(config.sample_file) as f:
        for line in f:
            parts = line.strip().split('\t')
            if len(parts) >= 2:
                sample_info[parts[0]] = parts[1]
    
    # 识别对照组和处理组
    groups = set(sample_info.values())
    if len(groups) < 2:
        raise ValueError("样本分组不足")
    
    control = min(groups)  # 假设字母序最小的是对照
    treatments = [g for g in groups if g != control]
    
    # 计算平均表达量
    for group in groups:
        cols = [c for c in exp.columns if sample_info.get(c, '').startswith(group)]
        if cols:
            exp[f'{group}_mean'] = exp[cols].mean(axis=1)
    
    # 计算log2FC
    for treat in treatments:
        if f'{treat}_mean' in exp.columns:
            exp[f'log2FC_{treat}vs{control}'] = np.log2((exp[f'{treat}_mean'] + 0.1) / (exp[f'{control}_mean'] + 0.1))
    
    # 筛选差异基因
    diff_x3 = set()
    diff_x4 = set()
    
    if 'log2FC_X3vsX2' in exp.columns:
        diff_x3 = set(exp[abs(exp['log2FC_X3vsX2']) >= config.min_log2fc].index)
    if 'log2FC_X4vsX2' in exp.columns:
        diff_x4 = set(exp[abs(exp['log2FC_X4vsX2']) >= config.min_log2fc].index)
    
    all_diff = diff_x3 | diff_x4
    
    # 过滤低表达基因
    exp = exp[exp[[c for c in exp.columns if '_mean' in c]].max(axis=1) >= config.min_expression]
    
    result = ExpressionResult(
        exp_file=exp_file,
        diff_genes_x3=sorted(diff_x3),
        diff_genes_x4=sorted(diff_x4),
        all_diff_genes=sorted(all_diff),
        total_genes=len(exp),
        diff_count_x3=len(diff_x3),
        diff_count_x4=len(diff_x4)
    )
    
    # 查找生成的图片
    out_dir = os.path.dirname(exp_file) or '.'
    result.plots = [
        f for f in os.listdir(out_dir) 
        if f.startswith(config.output_prefix) and f.endswith('.pdf')
    ]
    
    return result


def add_functional_annotation(result: ExpressionResult, gene2go_file: str) -> ExpressionResult:
    """添加功能注释"""
    result.gene2go_file = gene2go_file
    
    if not os.path.exists(gene2go_file):
        return result
    
    # 读取GO注释
    gene2go = defaultdict(list)
    with open(gene2go_file) as f:
        for line in f:
            gene, go = line.strip().split('\t')
            gene2go[gene].append(go)
    
    result.gene2go = dict(gene2go)
    return result


def generate_summary_report(result: ExpressionResult, output_file: str):
    """生成分析报告"""
    with open(output_file, 'w') as f:
        f.write("=" * 60 + "\n")
        f.write("基因家族表达分析报告\n")
        f.write("=" * 60 + "\n\n")
        
        f.write(f"总基因数: {result.total_genes}\n")
        f.write(f"差异基因 (X3 vs X2): {result.diff_count_x3}\n")
        f.write(f"差异基因 (X4 vs X2): {result.diff_count_x4}\n\n")
        
        if result.diff_genes_x3:
            f.write("差异基因列表 (X3 vs X2):\n")
            for g in result.diff_genes_x3:
                f.write(f"  {g}\n")
            f.write("\n")
        
        if result.diff_genes_x4:
            f.write("差异基因列表 (X4 vs X2):\n")
            for g in result.diff_genes_x4:
                f.write(f"  {g}\n")
            f.write("\n")
        
        # 功能注释统计
        if hasattr(result, 'gene2go') and result.gene2go:
            f.write("功能注释统计:\n")
            diff_with_go = sum(1 for g in result.all_diff_genes if g in result.gene2go)
            f.write(f"  差异基因有GO注释: {diff_with_go}/{len(result.all_diff_genes)}\n")
        
        if result.plots:
            f.write("\n生成的图表:\n")
            for p in result.plots:
                f.write(f"  - {p}\n")
    
    print(f"报告已保存: {output_file}")


def run_expression_pipeline(
    fpkm_file: str,
    sample_file: str,
    gene_list: str,
    gene2go_file: str = "",
    output_dir: str = ".",
    output_prefix: str = "expression"
) -> ExpressionResult:
    """
    运行完整的表达分析Pipeline
    """
    config = ExpressionConfig(
        fpkm_file=fpkm_file,
        sample_file=sample_file,
        gene_list=gene_list,
        output_prefix=output_prefix
    )
    
    # 1. 运行R差异表达分析
    print("[表达] 运行差异表达分析...")
    exp_file = run_r_expression(config)
    
    # 2. 筛选差异基因
    print("[表达] 筛选差异基因...")
    result = find_differential_genes(exp_file, config)
    
    # 3. 添加功能注释
    if gene2go_file and os.path.exists(gene2go_file):
        print("[表达] 添加功能注释...")
        result = add_functional_annotation(result, gene2go_file)
    
    # 4. 生成报告
    report_file = os.path.join(output_dir, f"{output_prefix}_report.txt")
    generate_summary_report(result, report_file)
    
    print(f"\n=== 分析完成 ===")
    print(f"总基因: {result.total_genes}")
    print(f"差异基因 X3vsX2: {result.diff_count_x3}")
    print(f"差异基因 X4vsX2: {result.diff_count_x4}")
    
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='表达分析Pipeline')
    parser.add_argument('--fpkm', required=True, help='FPKM表达量文件')
    parser.add_argument('--sample', required=True, help='样本分组文件')
    parser.add_argument('--genes', required=True, help='基因ID列表')
    parser.add_argument('--gene2go', default='', help='GO注释文件')
    parser.add_argument('--output-dir', default='.', help='输出目录')
    parser.add_argument('--prefix', default='expression', help='输出前缀')
    args = parser.parse_args()
    
    run_expression_pipeline(
        args.fpkm, args.sample, args.genes, args.gene2go,
        args.output_dir, args.prefix
    )