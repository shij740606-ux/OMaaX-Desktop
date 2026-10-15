#!/usr/bin/env python3
"""KaKs Analysis CLI v2 - 使用蛋白序列比对 (with reproducibility support)"""
import os
import argparse
import json
import logging
import subprocess
import sys
import re
import shutil
import hashlib
import gzip
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple, Set

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
if CONDA_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = CONDA_BIN + os.pathsep + os.environ.get("PATH", "")
CONDA_LIB = str(CONDA_PREFIX / "lib")
if CONDA_LIB not in os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep):
    os.environ["LD_LIBRARY_PATH"] = CONDA_LIB + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
LIBCRYPT_REAL = Path(CONDA_LIB) / "libcrypt.so.2.0.0"


def conda_tool_env(preload_libcrypt: bool = False) -> Dict[str, str]:
    env = os.environ.copy()
    if CONDA_BIN not in env.get("PATH", ""):
        env["PATH"] = CONDA_BIN + ":" + env.get("PATH", "")
    if CONDA_LIB not in env.get("LD_LIBRARY_PATH", ""):
        env["LD_LIBRARY_PATH"] = CONDA_LIB + ":" + env.get("LD_LIBRARY_PATH", "")
    if preload_libcrypt and LIBCRYPT_REAL.exists():
        existing = env.get("LD_PRELOAD", "").strip()
        env["LD_PRELOAD"] = str(LIBCRYPT_REAL) + ((" " + existing) if existing else "")
    return env


def open_text(path):
    path = Path(path)
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="ignore")
    return open(path, "r", encoding="utf-8", errors="ignore")


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
    
    # MUSCLE
    try:
        result = subprocess.run(['muscle', '-version'], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            versions["muscle"] = result.stdout.strip().split('\n')[0]
        else:
            versions["muscle"] = "unknown"
    except:
        versions["muscle"] = "not_found"
    
    # yn00 (PAML)
    try:
        result = subprocess.run(['yn00'], capture_output=True, text=True, check=False)
        if result.returncode == 0 or "YN00" in result.stderr:
            versions["yn00"] = "PAML available"
        else:
            versions["yn00"] = "unknown"
    except:
        versions["yn00"] = "not_found"
    
    # R
    try:
        result = subprocess.run(['Rscript', '--version'], capture_output=True, text=True, check=False)
        versions["Rscript"] = (result.stdout.strip() or result.stderr.strip()).split('\n')[0]
    except:
        versions["Rscript"] = "not_found"
    
    return versions

def parse_fasta(fasta_path):
    """解析FASTA文件，返回ID到序列的映射"""
    sequences = {}
    with open_text(fasta_path) as f:
        current_id = None
        current_seq = []
        for line in f:
            line = line.strip()
            if line.startswith('>'):
                if current_id:
                    sequences[current_id] = ''.join(current_seq)
                current_id = line[1:].split()[0]
                current_seq = []
            else:
                current_seq.append(line)
        if current_id:
            sequences[current_id] = ''.join(current_seq)
    return sequences

def find_matching_id(query_id, id_dict):
    """在字典中查找匹配的ID，支持版本号匹配"""
    # 精确匹配
    if query_id in id_dict:
        return query_id

    query_lower = query_id.lower()
    for key in id_dict:
        if query_lower == key.lower():
            return key
    
    # 去除版本号后匹配（如 AT1G01010.1 匹配 AT1G01010）
    query_base = query_id.split('.')[0].lower()
    for key in id_dict:
        key_base = key.split('.')[0].lower()
        if query_base == key_base:
            return key
    
    # 不再使用宽松的部分匹配，避免错误匹配不同基因
    # 例如：AT1G01010 不应该匹配 AT1G010100
    
    return None

def parse_pairs(pairs_file):
    """解析同源基因对"""
    pairs = []
    with open(pairs_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) >= 2:
                pairs.append((parts[0].strip(), parts[1].strip()))
    return pairs

def run_kaks_pair(gene1, gene2, cds1_dict, cds2_dict, pep1_dict, pep2_dict, work_dir, neutral_rate=7.08e-9):
    """运行单个基因对的Ka/Ks分析
    
    参数:
    - gene1: Ft基因 (用cds1/pep1)
    - gene2: Ath基因 (用cds2/pep2)
    """
    pair_dir = work_dir / f"{gene1}_{gene2}"
    pair_dir.mkdir(exist_ok=True)
    
    # 查找匹配的ID
    # gene1是Ft基因，用cds1/pep1
    cds1_id = find_matching_id(gene1, cds1_dict)  # Ft CDS
    cds2_id = find_matching_id(gene2, cds2_dict)  # Ath CDS
    pep1_id = find_matching_id(gene1, pep1_dict)  # Ft PEP
    pep2_id = find_matching_id(gene2, pep2_dict)  # Ath PEP
    
    if not cds1_id or not cds2_id:
        logger.warning(f"CDS not found: {gene1}({cds1_id}) or {gene2}({cds2_id})")
        return None
    if not pep1_id or not pep2_id:
        logger.warning(f"PEP not found: {gene1}({pep1_id}) or {gene2}({pep2_id})")
        return None
    
    # 写入蛋白序列
    # pep1_id 是 Ft 基因，从 pep1_dict (Ft PEP) 获取
    # pep2_id 是 Ath 基因，从 pep2_dict (Ath PEP) 获取
    pep_file = pair_dir / "pep.fa"
    with open(pep_file, 'w') as f:
        f.write(f">{gene1}\n{pep1_dict[pep1_id]}\n")  # Ft PEP
        f.write(f">{gene2}\n{pep2_dict[pep2_id]}\n")  # Ath PEP
    
    # 写入CDS序列
    # cds1_id 是 Ft 基因，从 cds1_dict (Ft CDS) 获取
    # cds2_id 是 Ath 基因，从 cds2_dict (Ath CDS) 获取
    cds_file = pair_dir / "cds.fa"
    with open(cds_file, 'w') as f:
        f.write(f">{gene1}\n{cds1_dict[cds1_id]}\n")  # Ft CDS
        f.write(f">{gene2}\n{cds2_dict[cds2_id]}\n")  # Ath CDS
    
    # MUSCLE比对蛋白序列 - 使用完整路径避免conda run的全局锁
    pep_aln = pair_dir / "pep_aln.fa"
    try:
        muscle_path = str(shutil.which("muscle") or "muscle")
        subprocess.run(
            [muscle_path, '-in', str(pep_file), '-out', str(pep_aln)],
            check=True, capture_output=True, text=True
        )
    except Exception as e:
        logger.warning(f"MUSCLE failed: {e}")
        return None
    
    # pal2nal转换 - 使用完整路径
    codon_aln = pair_dir / "codon_aln.fa"
    try:
        pal2nal_path = str(shutil.which("pal2nal.pl") or "pal2nal.pl")
        result = subprocess.run(
            [pal2nal_path, str(pep_aln), str(cds_file), '-output', 'fasta', '-nogap'],
            capture_output=True, text=True, env=conda_tool_env(preload_libcrypt=True)
        )
        if result.returncode == 0 and result.stdout.strip() and not result.stdout.lstrip().startswith("#---  ERROR"):
            with open(codon_aln, 'w') as f:
                f.write(result.stdout)
        else:
            logger.warning(f"pal2nal failed: {(result.stderr or result.stdout)[:200]}")
            return None
    except Exception as e:
        logger.warning(f"pal2nal error: {e}")
        return None
    
    # yn00计算Ka/Ks - 使用完整路径
    ctl_file = pair_dir / "yn00.ctl"
    yn00_out = pair_dir / "yn00.out"
    # 使用绝对路径
    codon_aln_abs = codon_aln.resolve()
    yn00_out_abs = yn00_out.resolve()
    with open(ctl_file, 'w') as f:
        f.write(f"seqfile = {codon_aln_abs}\noutfile = {yn00_out_abs}\nnoisy = 9\nverbose = 1\nicode = 0\nweighting = 0\ncommonf3x4 = 0\n")
    
    try:
        yn00_path = str(shutil.which("yn00") or "yn00")
        subprocess.run(
            [yn00_path, 'yn00.ctl'],
            cwd=pair_dir, check=True, capture_output=True
        )
    except Exception as e:
        logger.warning(f"yn00 failed: {e}")
        return None
    
    # 解析结果
    yn00_out = pair_dir / 'yn00.out'
    if not yn00_out.exists():
        return None
    
    with open(yn00_out) as f:
        content = f.read()
    
    ka = ks = kaks = None
    # YN00 table columns:
    # seq. seq. S N t kappa omega dN +- SE dS +- SE
    yn_pattern = (
        r'^\s*\d+\s+\d+\s+'
        r'(?P<S>\d+(?:\.\d+)?)\s+'
        r'(?P<N>\d+(?:\.\d+)?)\s+'
        r'(?P<t>\d+(?:\.\d+)?)\s+'
        r'(?P<kappa>\d+(?:\.\d+)?)\s+'
        r'(?P<omega>\d+(?:\.\d+)?)\s+'
        r'(?P<dN>\d+(?:\.\d+)?)\s+\+-\s+\d+(?:\.\d+)?\s+'
        r'(?P<dS>\d+(?:\.\d+)?)\s+\+-\s+\d+(?:\.\d+)?'
    )
    matches = [m for m in re.finditer(yn_pattern, content, flags=re.MULTILINE)]
    if matches:
        last = matches[-1]
        ka = float(last.group('dN'))
        ks = float(last.group('dS'))
        kaks = float(last.group('omega'))
    
    if ka is None or ks is None:
        return None
    
    divergence_time = ks / (2 * neutral_rate) / 1e6 if ks > 0 else None
    
    return {'gene1': gene1, 'gene2': gene2, 'Ka': ka, 'Ks': ks, 'Ka/Ks': kaks, 'divergence_time_mya': divergence_time}

def load_gene_list(gene_list_file: str) -> Set[str]:
    """加载基因列表，返回基因ID集合（去除版本号）"""
    gene_set = set()
    with open(gene_list_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            # 支持制表符分隔的文件（取第一列）
            gene_id = line.split('\t')[0].strip()
            # 存储原始ID和去除版本号的ID
            gene_set.add(gene_id)
            gene_set.add(gene_id.split('.')[0])
    return gene_set


def filter_pairs_by_gene_list(pairs: List[Tuple[str, str]], gene_list1: Set[str], gene_list2: Set[str]) -> List[Tuple[str, str]]:
    """过滤基因对，只保留两个基因都在对应列表中的对
    
    pairs文件格式: Ft基因 <tab> Ath基因 <tab> score (如 .anchors 文件)
    gene_list1: Ft基因列表 (对应 --cds1/--pep1)
    gene_list2: Ath基因列表 (对应 --cds2/--pep2)
    """
    filtered = []
    for gene1, gene2 in pairs:
        # gene1是Ft基因（第一列），用gene_list1(Ft列表)检查
        gene1_match = find_matching_id(gene1, {g: g for g in gene_list1})
        # gene2是Ath基因（第二列），用gene_list2(Ath列表)检查
        gene2_match = find_matching_id(gene2, {g: g for g in gene_list2})
        
        if gene1_match and gene2_match:
            filtered.append((gene1, gene2))
    return filtered


def write_kaks_failure_manifest(
    args,
    output_dir: Path,
    start_time: datetime,
    tool_versions: Dict[str, str],
    all_pairs_count: int,
    sequence_compatible_pairs: int,
    filtered_out: List[Tuple[str, str, str]],
    message: str,
) -> Path:
    final_dir = output_dir.parent / "final"
    final_dir.mkdir(exist_ok=True)
    elapsed = (datetime.now() - start_time).total_seconds()
    preview = [
        {"gene1": gene1, "gene2": gene2, "reason": reason}
        for gene1, gene2, reason in filtered_out[:20]
    ]
    manifest_data = {
        "manifest_version": "1.0",
        "stage": "kaks",
        "prefix": args.prefix_name,
        "timestamp": datetime.now().isoformat(),
        "status": "failed",
        "elapsed_sec": elapsed,
        "tool_versions": tool_versions,
        "parameters": {
            "prefix": args.prefix_name,
            "cds1": str(args.cds1),
            "cds2": str(args.cds2),
            "pep1": str(args.pep1),
            "pep2": str(args.pep2),
            "pairs": str(args.pairs),
            "neutral_rate": args.neutral_rate,
            "total_pairs_after_gene_list": all_pairs_count,
            "sequence_compatible_pairs": sequence_compatible_pairs,
        },
        "cmd": ["python " + " ".join(sys.argv)],
        "artifacts": {},
        "metrics": {
            "total_pairs_after_gene_list": all_pairs_count,
            "sequence_compatible_pairs": sequence_compatible_pairs,
            "successful_pairs": 0,
            "filtered_out_count": len(filtered_out),
        },
        "error": {
            "message": message,
            "filtered_out_preview": preview,
        },
    }
    manifest_file = final_dir / f"{args.prefix_name}.kaks.manifest.json"
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)
    report_file = final_dir / f"{args.prefix_name}.kaks_report.md"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(f"# Ka/Ks Analysis Report - {args.prefix_name}\n\n")
        f.write(f"**Generated**: {datetime.now().isoformat()}\n\n")
        f.write("## Analysis Overview\n")
        f.write("- **Status**: Failed\n")
        f.write(f"- **Reason**: {message}\n")
        f.write(f"- **Total Pairs After Gene List Filter**: {all_pairs_count}\n")
        f.write(f"- **Sequence-Compatible Pairs**: {sequence_compatible_pairs}\n\n")
        if preview:
            f.write("## Filtered Pair Preview\n")
            for item in preview[:10]:
                f.write("- {gene1} vs {gene2}: {reason}\n".format(**item))
    logger.info(f"Failure manifest saved: {manifest_file}")
    return manifest_file


def main():
    parser = argparse.ArgumentParser(description='Ka/Ks Analysis v2')
    parser.add_argument('-o', '--output', required=True)
    parser.add_argument('--prefix-name', required=True, help='Output file prefix (e.g., Ft.SPL)')
    parser.add_argument('--cds1', required=True)
    parser.add_argument('--cds2', required=True)
    parser.add_argument('--pep1', required=True)
    parser.add_argument('--pep2', required=True)
    parser.add_argument('--pairs', required=True, help='Ortholog pairs file (all pairs)')
    parser.add_argument('--gene-list1', help='Gene list for species 1 (e.g., SPL family genes)')
    parser.add_argument('--gene-list2', help='Gene list for species 2 (e.g., SPL family genes)')
    parser.add_argument('--neutral-rate', type=float, default=7.08e-9)
    
    args = parser.parse_args()
    
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / 'analysis'
    work_dir.mkdir(exist_ok=True)
    
    logger.info("Parsing input files...")
    cds1_dict = parse_fasta(args.cds1)
    cds2_dict = parse_fasta(args.cds2)
    pep1_dict = parse_fasta(args.pep1)
    pep2_dict = parse_fasta(args.pep2)
    all_pairs = parse_pairs(args.pairs)
    
    # 加载基因家族列表（如果提供）
    gene_list1 = None
    gene_list2 = None
    if args.gene_list1:
        gene_list1 = load_gene_list(args.gene_list1)
        logger.info(f"Loaded gene list 1: {len(gene_list1)} genes")
    if args.gene_list2:
        gene_list2 = load_gene_list(args.gene_list2)
        logger.info(f"Loaded gene list 2: {len(gene_list2)} genes")
    
    # 第一步：如果提供了基因列表，先按基因列表过滤pairs。
    # 允许只提供目标物种家族列表；参考物种未整理 subfamily 时仍可对目标家族
    # 在共线性锚定到的参考基因上计算 Ka/Ks。
    if gene_list1 or gene_list2:
        logger.info(f"Filtering pairs by gene lists...")
        logger.info(f"  Gene list 1: {len(gene_list1 or [])} genes")
        logger.info(f"  Gene list 2: {len(gene_list2 or [])} genes")
        pairs_before = len(all_pairs)
        if gene_list1 and gene_list2:
            all_pairs = filter_pairs_by_gene_list(all_pairs, gene_list1, gene_list2)
        else:
            filtered = []
            gene_list1_lookup = {g: g for g in (gene_list1 or [])}
            gene_list2_lookup = {g: g for g in (gene_list2 or [])}
            for gene1, gene2 in all_pairs:
                if gene_list1 and not find_matching_id(gene1, gene_list1_lookup):
                    continue
                if gene_list2 and not find_matching_id(gene2, gene_list2_lookup):
                    continue
                filtered.append((gene1, gene2))
            all_pairs = filtered
        logger.info(f"  Pairs after filtering: {len(all_pairs)} / {pairs_before}")
    
    # 第二步：过滤基因对：只保留 CDS 和蛋白序列中都存在的基因对
    # 注意：.anchors文件格式是 Ft基因 <tab> Ath基因 <tab> score
    # --cds1/pep1 是 Ft 的序列，--cds2/pep2 是 Ath 的序列
    pairs = []
    filtered_out = []
    for gene1, gene2 in all_pairs:
        # gene1 是 Ft 基因（来自.anchors第一列），用 cds1/pep1 (Ft序列) 检查
        gene1_cds_match = find_matching_id(gene1, cds1_dict)
        gene1_pep_match = find_matching_id(gene1, pep1_dict)
        # gene2 是 Ath 基因（来自.anchors第二列），用 cds2/pep2 (Ath序列) 检查
        gene2_cds_match = find_matching_id(gene2, cds2_dict)
        gene2_pep_match = find_matching_id(gene2, pep2_dict)
        
        if gene1_cds_match and gene1_pep_match and gene2_cds_match and gene2_pep_match:
            pairs.append((gene1, gene2))
        else:
            # 记录被过滤的基因对及原因
            reason = []
            if not gene1_cds_match: reason.append(f"{gene1} not in CDS1 (Ft)")
            if not gene1_pep_match: reason.append(f"{gene1} not in PEP1 (Ft)")
            if not gene2_cds_match: reason.append(f"{gene2} not in CDS2 (Ath)")
            if not gene2_pep_match: reason.append(f"{gene2} not in PEP2 (Ath)")
            filtered_out.append((gene1, gene2, "; ".join(reason)))
    
    if len(pairs) < len(all_pairs):
        logger.info(f"Filtered pairs: {len(all_pairs)} -> {len(pairs)} (based on available sequences)")
        if filtered_out:
            logger.info(f"First 5 filtered pairs:")
            for gene1, gene2, reason in filtered_out[:5]:
                logger.info(f"  - {gene1} vs {gene2}: {reason}")
    elif len(pairs) == 0 and len(all_pairs) > 0:
        logger.warning(f"No pairs passed filtering! Check if gene IDs in pairs file match sequence files.")
        logger.warning(f"Example pairs file IDs: {all_pairs[:3]}")
        cds1_ids = set(cds1_dict.keys())
        cds2_ids = set(cds2_dict.keys())
        logger.warning(f"Example CDS1 IDs: {list(cds1_ids)[:3]}")
        logger.warning(f"Example CDS2 IDs: {list(cds2_ids)[:3]}")
    
    # 记录开始时间
    start_time = datetime.now()
    
    # 获取工具版本
    tool_versions = get_tool_versions()
    logger.info(f"Tool versions: {tool_versions}")
    
    logger.info(f"CDS1: {len(cds1_dict)}, PEP1: {len(pep1_dict)}")
    logger.info(f"CDS2: {len(cds2_dict)}, PEP2: {len(pep2_dict)}")
    logger.info(f"Pairs: {len(pairs)}")
    
    results = []
    for i, (gene1, gene2) in enumerate(pairs, 1):
        logger.info(f"Processing {i}/{len(pairs)}: {gene1} vs {gene2}")
        result = run_kaks_pair(gene1, gene2, cds1_dict, cds2_dict, pep1_dict, pep2_dict, work_dir, args.neutral_rate)
        if result:
            results.append(result)
            logger.info(f"  Ka={result['Ka']:.4f}, Ks={result['Ks']:.4f}, Ka/Ks={result['Ka/Ks']:.4f}")
    
    logger.info(f"Successfully analyzed {len(results)}/{len(pairs)} pairs")
    
    if results:
        import pandas as pd
        df = pd.DataFrame(results)
        results_csv = output_dir / f"{args.prefix_name}.kaks_results.csv"
        df.to_csv(results_csv, index=False)
        
        # 分歧时间
        time_txt = output_dir / f"{args.prefix_name}.divergence_time.txt"
        with open(time_txt, 'w') as f:
            f.write(f"# Divergence Time (Neutral rate: {args.neutral_rate})\n\n")
            f.write("Gene1\tGene2\tKs\tDivergence_Mya\n")
            for r in results:
                if r['divergence_time_mya']:
                    f.write(f"{r['gene1']}\t{r['gene2']}\t{r['Ks']:.6f}\t{r['divergence_time_mya']:.2f}\n")
        
        # 生成绘图
        logger.info("Generating plots...")
        plot_script = Path("skills/kaks/scripts/plot_kaks.R")
        if plot_script.exists():
            try:
                rscript_path = str(shutil.which("Rscript") or "Rscript")
                plot_cmd = [rscript_path, str(plot_script), str(results_csv), str(output_dir / args.prefix_name)]
                subprocess.run(plot_cmd, check=True, capture_output=True)
                logger.info(f"Plots saved to {output_dir / args.prefix_name}.kaks_plots.pdf")
            except Exception as e:
                logger.warning(f"Plot generation failed: {e}")
        else:
            logger.warning(f"Plot script not found: {plot_script}")
        
        logger.info(f"Mean Ka/Ks: {df['Ka/Ks'].mean():.4f}")
        logger.info(f"Mean Ks: {df['Ks'].mean():.4f}")
        logger.info(f"Results saved to {output_dir}")
        
        # 复制结果到 final 目录
        final_dir = output_dir.parent / "final"
        final_dir.mkdir(exist_ok=True)
        
        # 复制所有生成的文件到 final
        import shutil
        artifacts = {}
        for ext in ['.csv', '.txt', '.pdf', '.png']:
            for file in output_dir.glob(f"{args.prefix_name}*{ext}"):
                dest = final_dir / file.name
                shutil.copy2(file, dest)
                logger.info(f"Copied to final: {file.name}")
                # 记录 artifact
                artifact_type = ext.lstrip('.')
                if 'kaks_results' in file.name:
                    artifact_type = "kaks_results"
                elif 'kaks_gene_pairs' in file.name:
                    artifact_type = "kaks_gene_pairs"
                elif file.name.endswith('divergence_time.txt'):
                    artifact_type = "divergence_time"
                elif 'kaks_plots' in file.name:
                    artifact_type = "kaks_plots"
                elif 'ka_ks_comparison' in file.name:
                    artifact_type = "ka_ks_comparison"
                elif 'kaks_barplot' in file.name:
                    artifact_type = "kaks_barplot"
                elif 'kaks_distribution' in file.name:
                    artifact_type = "kaks_distribution"
                elif 'kaks_scatter' in file.name:
                    artifact_type = "kaks_scatter"
                elif 'ks_distribution' in file.name:
                    artifact_type = "ks_distribution"
                elif 'divergence_distribution' in file.name:
                    artifact_type = "divergence_distribution"
                elif 'divergence_barplot' in file.name:
                    artifact_type = "divergence_barplot"
                
                artifacts[artifact_type] = {
                    "artifact": artifact_type,
                    "path": str(file.name),
                    "full_path": str(dest.resolve()),
                    "type": ext.lstrip('.'),
                    "size": dest.stat().st_size,
                    "sha256": compute_file_hash(dest)
                }
        
        # 生成 manifest
        logger.info("Generating manifest...")
        
        # === 统计选择压力分类 ===
        positive_selection_count = len(df[df['Ka/Ks'] > 1])
        purifying_selection_count = len(df[df['Ka/Ks'] < 1])
        neutral_selection_count = len(df[df['Ka/Ks'] == 1]) if len(df[df['Ka/Ks'] == 1]) > 0 else 0
        
        # === 构建method_summary ===
        method_summary = {
            "alignment": {
                "tool": "MUSCLE",
                "version": tool_versions.get("muscle", "unknown"),
                "alignment_type": "protein-guided codon alignment"
            },
            "kaks_calculation": {
                "tool": "KaKs_Calculator (YN model)",
                "version": tool_versions.get("yn00", "PAML"),
                "model": "YN (Yang-Nielson)",
                "neutral_rate": args.neutral_rate,
                "total_pairs": len(pairs),
                "successful_pairs": len(results)
            }
        }
        
        # === 构建comparative_summary ===
        mean_kaks = df['Ka/Ks'].mean()
        
        # 提取物种信息
        target_species = args.prefix_name.split('.')[0] if '.' in args.prefix_name else args.prefix_name.split('_')[0]
        
        comparative_summary = {
            "total_pairs": len(pairs),
            "successful_pairs": len(results),
            "positive_selection_count": positive_selection_count,
            "purifying_selection_count": purifying_selection_count,
            "neutral_selection_count": neutral_selection_count,
            "mean_ka_ks": round(mean_kaks, 4),
            "mean_ks": round(df['Ks'].mean(), 4),
            "target_species": target_species,
            "selection_pressure": "Positive" if mean_kaks > 1 else "Purifying" if mean_kaks < 1 else "Neutral",
            "key_sites": [],  # 可从正选择分析中提取
            "observation": f"Found {positive_selection_count} genes under positive selection (Ka/Ks > 1)" if positive_selection_count > 0 else f"Most genes ({purifying_selection_count}) under purifying selection"
        }
        
        manifest_data = {
            "manifest_version": "1.0",
            "stage": "kaks",
            "prefix": args.prefix_name,
            "timestamp": datetime.now().isoformat(),
            "status": "success",
            "elapsed_sec": (datetime.now() - start_time).total_seconds(),
            "tool_versions": tool_versions,
            "parameters": {
                "prefix": args.prefix_name,
                "cds1": str(args.cds1),
                "cds2": str(args.cds2),
                "pep1": str(args.pep1),
                "pep2": str(args.pep2),
                "pairs": str(args.pairs),
                "neutral_rate": args.neutral_rate,
                "total_pairs": len(pairs),
                "successful_pairs": len(results)
            },
            "cmd": [f"python {' '.join(sys.argv)}"],
            "artifacts": artifacts,
            "metrics": {
                "mean_ka_ks": df['Ka/Ks'].mean(),
                "mean_ks": df['Ks'].mean(),
                "min_ka_ks": df['Ka/Ks'].min(),
                "max_ka_ks": df['Ka/Ks'].max(),
                "total_pairs": len(pairs),
                "successful_pairs": len(results),
                "positive_selection_count": positive_selection_count,
                "purifying_selection_count": purifying_selection_count
            },
            
            # ========== 新增：method_summary（模板标准）==========
            "method_summary": method_summary,
            
            # ========== 新增：comparative_summary（模板标准）==========
            "comparative_summary": comparative_summary,
            
            # ========== 新增：biological_notes（预留Agent填充）==========
            "biological_notes": {
                "result_parser": "",  # Agent填充：生物学意义解读
                "discovery": ""       # Agent填充：异常/机会/故事线
            }
        }
        
        manifest_file = final_dir / f"{args.prefix_name}.kaks.manifest.json"
        with open(manifest_file, 'w') as f:
            json.dump(manifest_data, f, indent=2, ensure_ascii=False)
        logger.info(f"Manifest saved: {manifest_file}")
        
        # 生成报告
        logger.info("Generating report...")
        report_file = final_dir / f"{args.prefix_name}.kaks_report.md"
        with open(report_file, 'w') as f:
            f.write(f"# Ka/Ks Analysis Report - {args.prefix_name}\n\n")
            f.write(f"**Generated**: {datetime.now().isoformat()}\n\n")
            f.write("## Analysis Overview\n")
            f.write(f"- **Status**: ✅ Success\n")
            f.write(f"- **Total Pairs**: {len(pairs)}\n")
            f.write(f"- **Successful Pairs**: {len(results)}\n")
            f.write(f"- **Mean Ka/Ks**: {df['Ka/Ks'].mean():.4f}\n")
            f.write(f"- **Mean Ks**: {df['Ks'].mean():.4f}\n")
            f.write(f"- **Neutral Rate**: {args.neutral_rate}\n\n")
            f.write("## Tool Versions\n")
            for tool, version in tool_versions.items():
                f.write(f"- **{tool}**: {version}\n")
            f.write("\n## Output Files\n")
            for artifact_name, artifact_info in artifacts.items():
                f.write(f"- **{artifact_name}**: `{artifact_info['path']}` ({artifact_info['size']} bytes)\n")
            f.write("\n## Results Summary\n")
            f.write("| Gene1 | Gene2 | Ka | Ks | Ka/Ks | Divergence (Mya) |\n")
            f.write("|-------|-------|------|------|--------|------------------|\n")
            for _, row in df.head(10).iterrows():
                f.write(f"| {row['gene1']} | {row['gene2']} | {row['Ka']:.2f} | {row['Ks']:.2f} | {row['Ka/Ks']:.4f} | {row.get('divergence_time_mya', 'N/A')} |\n")
        logger.info(f"Report saved: {report_file}")
        
        logger.info(f"All results copied to {final_dir}")
    else:
        message = "No successful results"
        logger.error(message)
        write_kaks_failure_manifest(
            args=args,
            output_dir=output_dir,
            start_time=start_time,
            tool_versions=tool_versions,
            all_pairs_count=len(all_pairs),
            sequence_compatible_pairs=len(pairs),
            filtered_out=filtered_out,
            message=message,
        )
        sys.exit(1)

if __name__ == '__main__':
    main()
