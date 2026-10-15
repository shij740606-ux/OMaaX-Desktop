

#!/usr/bin/env python3
"""
Domain stage: 蛋白结构域分析 (Pfam HMM 扫描 + 可视化)
符合 workspace_contract_v1.md 和 manifest_v1.schema.json 契约

执行路径:
1. 单独触发: 独立执行，自动从上游 manifest 查找输入
2. Run All: 顺序执行，依赖 Identify 和 Phylogeny 阶段产物

目录结构适配:
- 前端上传: input/{job_id}/     (如 input/20260212_143523_2480772_ec5c6d/)
- 工作目录: workspace/{job_id}/ (如 workspace/20260212_143523_2480772_ec5c6d/)
"""

import argparse
import json
import subprocess
import sys
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import hashlib

PROJECT_ROOT = Path(__file__).absolute().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.gene_family_scope import read_scope, sanitize_token
from gfa_core.gene_family_stage_context import apply_stage_context, build_stage_context, user_output_prefix_for, write_legacy_manifest_copy
from gfa_core.mas.effective_members import MemberResolutionError, resolve_effective_members


# ===================== 配置常量 =====================

# Pfam 数据库项目内路径（可配置）
DEFAULT_PFAM_BASE = str(PROJECT_ROOT / "data" / "hmm")
DEFAULT_PFAM_HMM = "Pfam-A.hmm"

# E-value 默认阈值
DEFAULT_EVALUE = 1e-5

# R 脚本路径（相对于项目根目录）
DEFAULT_RSCRIPT = "scripts/tree_domain.R"

CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
if CONDA_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = CONDA_BIN + os.pathsep + os.environ.get("PATH", "")


# ===================== 工具函数 =====================

def display_path(path: Path | str) -> str:
    """Return the user-facing symlink path instead of resolving data-root links."""
    return str(Path(path).absolute())


def current_input_dir(workspace: Path) -> Path:
    return workspace.parent.parent / "input" / workspace.name


def workspace_root_dir(workspace: Path) -> Path:
    return workspace.parent


def inside_current_job(workspace: Path, path: Path) -> bool:
    input_dir = current_input_dir(workspace)
    try:
        resolved = path.resolve()
        return resolved.is_relative_to(workspace.resolve()) or resolved.is_relative_to(input_dir.resolve())
    except OSError:
        return False


def rebase_current_job_path(workspace: Path, path: Path) -> Optional[Path]:
    """Map copied legacy workspace/input paths back onto the current job roots."""
    parts = path.parts
    for root_name, current_root in (("workspace", workspace), ("input", current_input_dir(workspace))):
        for idx, part in enumerate(parts):
            if part != root_name:
                continue
            if idx + 1 >= len(parts) or parts[idx + 1] != workspace.name:
                continue
            suffix = Path(*parts[idx + 2:]) if idx + 2 < len(parts) else Path()
            candidate = current_root / suffix
            if candidate.exists():
                return candidate
    return None


def resolve_job_path(workspace: Path, raw_path: str | Path, manifest_path: Optional[Path] = None) -> Optional[Path]:
    """Resolve a manifest path without trusting stale absolute full_path values."""
    raw_text = str(raw_path or "").strip()
    if not raw_text:
        return None
    path = Path(raw_text)
    if path.is_absolute():
        rebased = rebase_current_job_path(workspace, path)
        if rebased:
            return rebased.absolute()
        if inside_current_job(workspace, path):
            return path.absolute()
        return None

    candidates: List[Path] = [workspace / path]
    if manifest_path:
        candidates.extend([manifest_path.parent / path, manifest_path.parent / path.name])
    if str(path).startswith("final/") and manifest_path:
        candidates.append(manifest_path.parent / path.name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def manifest_artifact_path(workspace: Path, manifest_path: Path, artifact: Dict) -> Optional[Path]:
    if not isinstance(artifact, dict):
        return None
    for key in ("path", "relpath_or_hint", "file", "full_path"):
        resolved = resolve_job_path(workspace, artifact.get(key) or "", manifest_path)
        if resolved:
            return resolved
    return None

def get_tool_versions() -> Dict[str, str]:
    """获取工具版本信息"""
    versions = {}
    
    # HMMER 版本
    try:
        r = subprocess.run(["hmmscan", "-h"], capture_output=True, text=True, check=False)
        if r.returncode == 0 and r.stdout:
            first_line = r.stdout.strip().split('\\n')[0]
            versions["hmmer"] = first_line
    except:
        versions["hmmer"] = "not_found"
    
    # R 版本
    try:
        r = subprocess.run(["R", "--version"], capture_output=True, text=True, check=False)
        if r.returncode == 0 and r.stdout:
            versions["R"] = r.stdout.split("\\n")[0]
    except:
        versions["R"] = "not_found"
    
    # Biopython 版本
    try:
        import Bio
        versions["biopython"] = Bio.__version__
    except:
        versions["biopython"] = "not_found"
    
    return versions


def prepare_directories(workspace: Path) -> Dict[str, Path]:
    """创建Domain阶段的标准目录结构"""
    paths = {
        "analysis_dir": workspace / "domain" / "analysis",
        "final_dir": workspace / "final",
        "logs_dir": workspace / "logs",
    }
    
    print("创建目录结构:")
    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        print(f"  {name}: {path}")
    
    return paths


def compute_file_hash(filepath: Path) -> str:
    """计算文件 SHA256 哈希"""
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except:
        return ""


def find_latest_manifest(final_dir: Path, stage: str) -> Optional[Path]:
    """查找指定阶段的最新 manifest 文件"""
    pattern = f"*.{stage}.manifest.json"
    manifests = list(final_dir.glob(pattern))
    if not manifests:
        return None
    return max(manifests, key=lambda p: p.stat().st_mtime)


def read_manifest(manifest_file: Path) -> Dict:
    """读取 manifest 文件"""
    with open(manifest_file, 'r') as f:
        return json.load(f)


def resolve_scope_tree_file(ws: Path, prefix: str, scope_data: Optional[Dict], cli_tree: Optional[str] = None) -> Path:
    final_dir = ws / "final"
    if cli_tree:
        tree_path = resolve_job_path(ws, cli_tree) or Path(cli_tree)
        if tree_path.exists() and tree_path.stat().st_size > 0:
            return tree_path.absolute()
        raise FileNotFoundError(f"Specified tree file not found: {cli_tree}")

    manifest_candidates: List[Path] = []
    scope_id = str((scope_data or {}).get("scope_id") or "").strip()
    if scope_id:
        manifest_candidates.append(final_dir / f"{prefix}.{sanitize_token(scope_id)}.phylogeny.manifest.json")
    else:
        manifest_candidates.append(final_dir / f"{prefix}.phylogeny.manifest.json")

    for manifest_file in manifest_candidates:
        if not manifest_file.exists() or manifest_file.stat().st_size == 0:
            continue
        try:
            manifest_data = read_manifest(manifest_file)
        except Exception:
            continue
        artifact = (manifest_data.get("artifacts", {}) or {}).get("tree")
        tree_path = None
        if isinstance(artifact, dict):
            tree_path = manifest_artifact_path(ws, manifest_file, artifact)
        elif isinstance(artifact, str):
            tree_path = resolve_job_path(ws, artifact, manifest_file)
        if tree_path and tree_path.exists() and tree_path.stat().st_size > 0:
            return tree_path.absolute()

    fallback = final_dir / f"{prefix}.tree.nwk"
    if not scope_id and fallback.exists() and fallback.stat().st_size > 0:
        return fallback.absolute()
    raise FileNotFoundError(f"Tree file not found for prefix {prefix}. Please run phylogeny first or specify --tree.")


def count_fasta_records(fasta_file: Path) -> int:
    count = 0
    with open(fasta_file, encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if line.startswith(">"):
                count += 1
    return count


def ensure_protein_fasta_without_stop_markers(protein_fasta: Path) -> Path:
    """Use a job-local derived FASTA without '*' stop markers when needed."""
    has_stop = False
    with open(protein_fasta, encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not line.startswith(">") and "*" in line:
                has_stop = True
                break
    if not has_stop:
        return protein_fasta
    clean_name = re.sub(r"(\.fa(?:sta)?)$", r".clean\1", protein_fasta.name, flags=re.IGNORECASE)
    if clean_name == protein_fasta.name:
        clean_name = protein_fasta.name + ".clean.fa"
    clean_fasta = protein_fasta.with_name(clean_name)
    if clean_fasta.exists() and clean_fasta.stat().st_mtime >= protein_fasta.stat().st_mtime:
        return clean_fasta
    with open(protein_fasta, "r", encoding="utf-8", errors="ignore") as src, open(clean_fasta, "w", encoding="utf-8") as dst:
        for line in src:
            dst.write(line if line.startswith(">") else line.replace("*", ""))
    print(f"[INFO] Created stop-marker-clean FASTA for hmmscan: {clean_fasta}")
    return clean_fasta


def resolve_scope_protein_fasta(ws: Path, prefix: str, scope_data: Dict, cli_proteins: Optional[str] = None) -> Path:
    if cli_proteins:
        protein_path = resolve_job_path(ws, cli_proteins) or Path(cli_proteins)
        if protein_path.exists() and protein_path.stat().st_size > 0:
            return ensure_protein_fasta_without_stop_markers(protein_path)
        raise FileNotFoundError(f"Specified protein file not found: {protein_path}")

    final_dir = ws / "final"
    scope_id = str(scope_data.get("scope_id") or "")
    manifest_candidates = []
    if scope_id:
        manifest_candidates.append(final_dir / f"{prefix}.{sanitize_token(scope_id)}.phylogeny.manifest.json")
    else:
        manifest_candidates.append(final_dir / f"{prefix}.phylogeny.manifest.json")

    for manifest_file in manifest_candidates:
        if not manifest_file.exists():
            continue
        try:
            manifest_data = read_manifest(manifest_file)
        except Exception:
            continue
        fasta_text = manifest_data.get("parameters", {}).get("scope_input_fasta")
        if fasta_text:
            fasta_path = resolve_job_path(ws, fasta_text, manifest_file)
            if fasta_path and fasta_path.exists() and fasta_path.stat().st_size > 0:
                print(f"[INFO] Found scoped protein FASTA from phylogeny manifest: {fasta_path}")
                return ensure_protein_fasta_without_stop_markers(fasta_path)

    raise FileNotFoundError(
        "Cannot find scoped protein FASTA. Run phylogeny with the same scope first, "
        "or provide --proteins explicitly."
    )


def resolve_project_root(ws: Path) -> Path:
    """
    解析项目根目录
    从 workspace/{job_id}/ 向上回溯到项目根目录
    假设结构: .../bba/workspace/{job_id}/ -> .../bba/
    """
    # ws 是 workspace/{job_id}/，parent 是 workspace/，parent.parent 才是项目根目录
    return ws.parent.parent


def resolve_subfamily_file(ws: Path, prefix: str, job_id: str, cli_subfamily: Optional[str] = None, output_prefix: Optional[str] = None) -> Optional[Path]:
    """
    解析亚家族文件路径
    命名规则: {prefix}.confirmed_subfamily.tsv (如 Ft.SPL.confirmed_subfamily.tsv)
    优先级: final/ > input/{job_id}/ (项目根目录)
    
    实际目录结构：
    - workspace/{job_id}/final/     <- 阶段产物
    - input/{job_id}/               <- 前端上传（项目根目录下）
    """
    # 方式一：命令行指定
    if cli_subfamily:
        if str(cli_subfamily).strip().lower() in {"none", "skip", "disabled"}:
            print("[INFO] Subfamily coloring explicitly disabled")
            return None
        subfamily_path = resolve_job_path(ws, cli_subfamily) or Path(cli_subfamily)
        if subfamily_path.exists():
            print(f"[INFO] Using specified subfamily file: {subfamily_path}")
            return subfamily_path
        else:
            print(f"[WARNING] Specified subfamily file not found: {cli_subfamily}")
            return None
    
    # 自动查找只接受 confirmed_subfamily；prediction/geneID 必须由 --subfamily 显式传入。
    names = []
    for base in [output_prefix, prefix]:
        if not base or base in names:
            continue
        names.extend([
            f"{base}.confirmed_subfamily.tsv",
        ])
    subfamily_name = f"{prefix}.confirmed_subfamily.tsv"
    
    # 项目根目录（workspace 的父目录的父目录）
    project_root = resolve_project_root(ws)
    
    # 搜索路径优先级：
    search_paths = []
    
    # 1. final/ 目录（阶段产物，优先级最高）
    final_dir = ws / "final"
    if final_dir.exists():
        search_paths.extend(final_dir / name for name in names)
    
    # 2. 项目根目录的 input/{job_id}/ 目录（前端上传的实际位置）
    # 修正：input/ 直接在项目根目录下，不是 workspace/ 下
    search_paths.extend(
        project_root / "input" / job_id / name
        for name in names
       
    )
    
    # 3. workspace 下的 input/ 目录（兼容性检查，某些旧版本可能用这里）
    workspace_input = ws / "input"
    if workspace_input.exists():
        search_paths.extend(workspace_input / name for name in names)
    
    for candidate in search_paths:
        if candidate.exists():
            print(f"[INFO] Found subfamily file: {candidate}")
            return candidate
    
    print(f"[INFO] No subfamily file found ({subfamily_name})")
    print(f"[DEBUG] Searched paths:")
    for p in search_paths:
        print(f"  - {p} (exists: {p.exists()})")
    return None

def prepare_pfam_database(domain_dir: Path, pfam_base: str = DEFAULT_PFAM_BASE) -> Tuple[Path, bool, str]:
    """
    准备 Pfam 数据库（软连接方式）
    
    Returns:
        (pfam_hmm_path, success, error_message)
    """
    pfam_source = Path(pfam_base) / DEFAULT_PFAM_HMM
    
    # 检查源文件是否存在
    if not pfam_source.exists():
        return None, False, f"Pfam source not found: {pfam_source}"
    
    # 检查索引文件
    index_extensions = ['.h3f', '.h3i', '.h3m', '.h3p']
    missing_indices = []
    
    for ext in index_extensions:
        idx_file = Path(pfam_base) / f"{DEFAULT_PFAM_HMM}{ext}"
        if not idx_file.exists():
            missing_indices.append(str(idx_file))
    
    if missing_indices:
        return None, False, f"Missing Pfam index files: {', '.join(missing_indices)}. Run 'hmmpress {pfam_source}' first."
    
    # 创建软连接
    pfam_link = domain_dir / DEFAULT_PFAM_HMM
    
    try:
        if pfam_link.exists() or pfam_link.is_symlink():
            pfam_link.unlink()
        os.symlink(pfam_source, pfam_link)
        print(f"[INFO] Linked Pfam database: {pfam_source} -> {pfam_link}")
        
        # 链接索引文件
        for ext in index_extensions:
            src_idx = Path(pfam_base) / f"{DEFAULT_PFAM_HMM}{ext}"
            link_idx = domain_dir / f"{DEFAULT_PFAM_HMM}{ext}"
            if link_idx.exists() or link_idx.is_symlink():
                link_idx.unlink()
            os.symlink(src_idx, link_idx)
        
        print(f"[INFO] Linked {len(index_extensions)} index files")
        return pfam_link, True, ""
        
    except Exception as e:
        return None, False, f"Failed to create Pfam symlinks: {str(e)}"


def run_hmmscan(pfam_hmm: Path, protein_fasta: Path, domain_dir: Path,
                output_prefix: str = "", cpu: int = 8, evalue: float = DEFAULT_EVALUE) -> Tuple[bool, Dict]:
    """
    运行 hmmscan 进行结构域搜索
    
    Returns:
        (success, result_dict)
    """
    basename = sanitize_token(output_prefix) if output_prefix else "results"
    domtblout = domain_dir / f"{basename}.results.domtblout"
    tblout = domain_dir / f"{basename}.results.tblout"
    output_log = domain_dir / f"{basename}.results.out"
    
    cmd = [
        "hmmscan",
        "--cpu", str(cpu),
        "--domtblout", str(domtblout),
        "--tblout", str(tblout),
        "-o", str(output_log),
        "-E", str(evalue),  # 报告阈值
        "--domE", str(evalue),  # 结构域报告阈值
        str(pfam_hmm),
        str(protein_fasta)
    ]
    
    print(f"[CMD] {' '.join(cmd)}")
    
    start_time = datetime.now()
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        end_time = datetime.now()
        
        success = result.returncode == 0
        elapsed = (end_time - start_time).total_seconds()
        
        # 检查输出文件
        domtblout_exists = domtblout.exists() and domtblout.stat().st_size > 0
        
        if success and not domtblout_exists:
            success = False
            error_msg = "hmmscan returned 0 but domtblout is empty"
        elif not success:
            error_msg = result.stderr[-2000:] if result.stderr else "hmmscan failed"
        else:
            error_msg = ""
        
        return success, {
            "domtblout": domtblout if domtblout.exists() else None,
            "tblout": tblout if tblout.exists() else None,
            "output_log": output_log if output_log.exists() else None,
            "returncode": result.returncode,
            "stderr": result.stderr[-2000:] if result.stderr else "",
            "elapsed_sec": elapsed,
            "error": error_msg
        }
        
    except Exception as e:
        return False, {
            "domtblout": None,
            "tblout": None,
            "output_log": None,
            "returncode": -1,
            "stderr": str(e),
            "elapsed_sec": 0,
            "error": str(e)
        }


def parse_domtblout(domtblout: Path, evalue_cutoff: float = DEFAULT_EVALUE) -> Tuple[List[Dict], Dict]:
    """
    解析 hmmscan domtblout 文件
    
    Returns:
        (domains_list, stats_dict)
    """
    domains = []
    stats = {
        "total_hits": 0,
        "significant_hits": 0,
        "unique_proteins": set(),
        "unique_domains": set(),
        "protein_domain_counts": {}
    }
    
    if not domtblout.exists():
        return [], stats
    
    with open(domtblout, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            stats["total_hits"] += 1
            
            # domtblout 格式（空格分隔，22列）
            parts = line.split()
            if len(parts) < 22:
                continue
            
            try:
                # 解析关键字段
                target_name = parts[0]      # Domain name
                t_accession = parts[1]      # Domain accession
                tlen = int(parts[2])        # Domain length
                query_name = parts[3]       # Protein ID
                q_accession = parts[4]      # Protein accession
                qlen = int(parts[5])        # Protein length
                
                # E-values and scores
                E_full = float(parts[6])    # Full sequence E-value
                score_full = float(parts[7]) # Full sequence score
                
                # Domain coordinates
                dom_index = int(parts[9])   # Domain index
                dom_of = int(parts[10])     # Total domains

                c_evalue = float(parts[11]) # Conditional E-value
                i_evalue = float(parts[12]) # Independent E-value
                dom_score = float(parts[13]) # Domain score

                hmm_from = int(parts[15])   # HMM start
                hmm_to = int(parts[16])     # HMM end
                ali_from = int(parts[17])  # Alignment start (protein)
                ali_to = int(parts[18])    # Alignment end (protein)
                env_from = int(parts[19])   # Envelope start
                env_to = int(parts[20])    # Envelope end
                acc = float(parts[21])      # Average posterior probability
                
                # 应用 E-value 过滤（使用 i-Evalue，更严格）
                if i_evalue > evalue_cutoff:
                    continue
                
                stats["significant_hits"] += 1
                stats["unique_proteins"].add(query_name)
                stats["unique_domains"].add(target_name)
                
                if query_name not in stats["protein_domain_counts"]:
                    stats["protein_domain_counts"][query_name] = 0
                stats["protein_domain_counts"][query_name] += 1
                
                domains.append({
                    "target_name": target_name,
                    "target_accession": t_accession,
                    "query_name": query_name,
                    "query_length": qlen,
                    "i_evalue": i_evalue,
                    "domain_score": dom_score,
                    "hmm_from": hmm_from,
                    "hmm_to": hmm_to,
                    "ali_from": ali_from,
                    "ali_to": ali_to,
                    "env_from": env_from,
                    "env_to": env_to,
                    "accuracy": acc
                })
                
            except (ValueError, IndexError) as e:
                continue
    
    # 转换 set 为 count
    stats["unique_proteins"] = len(stats["unique_proteins"])
    stats["unique_domains"] = len(stats["unique_domains"])
    
    return domains, stats


def run_r_plotting(tree_file: Path, subfamily_file: Optional[Path], 
                   domtblout_file: Path, output_prefix: Path,
                   rscript_path: Optional[Path] = None,
                   evalue_cutoff: float = DEFAULT_EVALUE,
                   coverage_cutoff: Optional[float] = None) -> Dict:
    """
    运行 R 绘图脚本 tree_domain.R
    
    Args:
        tree_file: 系统发育树文件 (Newick)
        subfamily_file: 亚家族分类文件 (可选)
        domtblout_file: hmmscan domtblout 文件
        output_prefix: 输出文件前缀（不含扩展名）
        rscript_path: R 脚本路径
        evalue_cutoff: E-value 阈值
        coverage_cutoff: 覆盖度阈值 (百分比，如 50 表示 50%)
    
    Returns:
        Dict with success, output_files, etc.
    """
    if rscript_path is None:
        # 从项目根目录推断
        rscript_path = Path(__file__).parent.parent.parent / "scripts" / "tree_domain.R"
    
    if not rscript_path.exists():
        return {
            "success": False, 
            "error": f"R script not found: {rscript_path}"
        }
    
    # 构建命令 - 适配 tree_domain.R 的参数格式
    # 用法: Rscript tree_domain.R <treefile> <subfamily_file> <domtblout_file> [evalue] [coverage] [output_prefix]
    cmd = [
        "Rscript", str(rscript_path),
        str(tree_file),
        str(subfamily_file) if subfamily_file else "NONE",  # R 脚本需要处理 NONE
        str(domtblout_file),
        str(evalue_cutoff),
        str(coverage_cutoff) if coverage_cutoff else "",  # 空字符串表示不启用
        str(output_prefix)
    ]
    
    print(f"[CMD] {' '.join(cmd)}")
    
    start_time = datetime.now()
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=600)
        end_time = datetime.now()
        
        success = result.returncode == 0
        
        # 检查输出文件（R 脚本输出: {prefix}.pdf 和 {prefix}.png）
        output_files = []
        for ext in ['.pdf', '.png']:
            f = Path(f"{output_prefix}{ext}")
            if f.exists():
                output_files.append(str(f))
        
        return {
            "success": success and len(output_files) > 0,
            "returncode": result.returncode,
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-2000:],
            "output_files": output_files,
            "elapsed_sec": (end_time - start_time).total_seconds()
        }
        
    except subprocess.TimeoutExpired:
        return {
            "success": False, 
            "error": "R plotting timeout (>600s)", 
            "elapsed_sec": 600
        }
    except Exception as e:
        return {
            "success": False, 
            "error": str(e), 
            "elapsed_sec": 0
        }


def generate_report(prefix: str, metrics: Dict, parameters: Dict,
                   output_file: Path, artifacts: Dict, plot_success: bool) -> None:
    """生成 domain_report.md 报告"""
    
    report_lines = [
        f"# Domain Analysis Report: {prefix}",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Status:** {'Success' if plot_success else 'Partial Success (Plotting Failed)'}",
        "",
        "## Summary",
        "",
        f"- **Total Proteins:** {metrics['proteins_total']}",
        f"- **Proteins with Domains:** {metrics['proteins_with_domains']} ({metrics['domain_coverage_rate']*100:.1f}%)",
        f"- **Total Domain Hits:** {metrics['total_domain_hits']}",
        f"- **Significant Hits (E < {parameters.get('evalue_cutoff', DEFAULT_EVALUE)}):** {metrics['significant_domain_hits']}",
        f"- **Unique Domain Types:** {metrics['unique_domain_types']}",
        f"- **Average Domains per Protein:** {metrics['avg_domains_per_protein']:.2f}",
        "",
        "## Domain Statistics",
        "",
    ]
    
    # 添加 top domains 信息
    if "top_domains" in metrics and metrics["top_domains"]:
        report_lines.append("### Top Domains by Frequency")
        report_lines.append("")
        for domain, count in metrics["top_domains"]:
            report_lines.append(f"- **{domain}:** {count} hits")
        report_lines.append("")
    
    report_lines.extend([
        "## Parameters",
        "",
        f"- **E-value Cutoff:** {parameters.get('evalue_cutoff', DEFAULT_EVALUE)}",
        f"- **Coverage Cutoff:** {parameters.get('coverage_cutoff', 'Not applied')}",
        f"- **CPU Cores:** {parameters.get('cpu', 'N/A')}",
        f"- **Pfam Database:** {parameters.get('pfam_hmm', 'N/A')}",
        f"- **Protein Source:** {parameters.get('protein_source', 'N/A')}",
        f"- **Tree File:** {parameters.get('tree_file', 'N/A')}",
        f"- **Subfamily File:** {parameters.get('subfamily_file', 'Not provided')}",
        "",
        "## Output Files",
        ""
    ])
    
    for key, artifact in artifacts.items():
        if isinstance(artifact, dict):
            report_lines.append(f"- **{key}:** `{artifact.get('path', 'N/A')}` ({artifact.get('type', 'unknown')})")
    
    report_lines.extend([
        "",
        "## Visualization",
        ""
    ])
    
    if plot_success:
        report_lines.append("Protein domain architecture visualization was successfully generated with three panels:")
        report_lines.append("1. **Phylogenetic Tree** - Gene relationships with subfamily coloring")
        report_lines.append("2. **Subfamily Bars** - Visual connection between tree and domains")
        report_lines.append("3. **Domain Architecture** - Pfam domain positions along protein sequences")
        report_lines.append("")
        report_lines.append("Output formats:")
        report_lines.append("- **PDF**: Vector graphics for publication")
        report_lines.append("- **PNG**: 300 DPI raster image for presentation")
    else:
        report_lines.append("**Warning:** Domain visualization failed. See logs for details.")
        report_lines.append("")
        report_lines.append("Raw domain data is available in the domtblout file for manual analysis.")
    
    report_lines.extend([
        "",
        "---",
        "*Generated by Gene Family Analysis Pipeline - Domain Stage*"
    ])
    
    with open(output_file, 'w') as f:
        f.write('\\n'.join(report_lines))
    
    print(f"[INFO] Report generated: {output_file}")


def write_manifest(manifest: Dict, manifest_file: Path) -> None:
    """写入 manifest 文件"""
    with open(manifest_file, 'w') as f:
        json.dump(manifest, f, indent=2)
    print(f"[INFO] Manifest: {manifest_file}")


def write_failure_manifest(workspace: Path, final_dir: Path, prefix: str,
                          job_id: str, run_id: str, start_time: datetime,
                          error_msg: str, partial_artifacts: Dict = None) -> None:
    """写入失败状态的 manifest"""
    end_time = datetime.now()
    
    manifest = {
        "manifest_version": "1.0",
        "job_id": job_id,
        "stage": "domain",
        "run_id": run_id,
        "prefix": prefix,
        "timestamp": end_time.isoformat(),
        "status": "failed",
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "elapsed_sec": (end_time - start_time).total_seconds(),
        "cmd": [str(Path(__file__).name)],
        "workspace_root": display_path(workspace_root_dir(workspace)),
        "workspace": display_path(workspace),
        "input_dir": display_path(current_input_dir(workspace)),
        "artifacts": partial_artifacts or {},
        "parameters": {
            "stage": "domain",
            "error": error_msg
        },
        "metrics": {
            "error_type": "execution_failed"
        }
    }
    
    manifest_file = final_dir / f"{prefix}.domain.manifest.json"
    write_manifest(manifest, manifest_file)
    print(f"[ERROR] Failure manifest written: {manifest_file}")


def quarantine_stale_unscoped_failure(final_dir: Path, prefix: str, stage: str, canonical_manifest: Path) -> None:
    """Move stale unscoped failure manifests aside after a scoped success is available."""
    stale_manifest = final_dir / f"{prefix}.{stage}.manifest.json"
    if stale_manifest == canonical_manifest or not stale_manifest.exists():
        return
    try:
        with open(stale_manifest, "r", encoding="utf-8") as handle:
            stale_data = json.load(handle)
    except Exception:
        return
    if stale_data.get("status") != "failed":
        return
    if stale_data.get("scope_id") or stale_data.get("stage_context"):
        return
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archived = final_dir / f"{prefix}.{stage}.stale_failed.{timestamp}.json"
    stale_manifest.rename(archived)
    print(f"[INFO] Quarantined stale unscoped failure manifest: {archived}")


def calculate_domain_metrics(domains: List[Dict], total_proteins: int, 
                             hmmscan_stats: Dict) -> Dict:
    """计算结构域相关的 metrics"""
    
    # 统计每个 domain 的频率
    domain_freq = {}
    for d in domains:
        name = d["target_name"]
        domain_freq[name] = domain_freq.get(name, 0) + 1
    
    # Top 10 domains
    top_domains = sorted(domain_freq.items(), key=lambda x: x[1], reverse=True)[:10]
    
    # 计算有 domain 的蛋白数量
    proteins_with_domains = len(set(d["query_name"] for d in domains))
    
    metrics = {
        "proteins_total": total_proteins,
        "proteins_with_domains": proteins_with_domains,
        "domain_coverage_rate": round(proteins_with_domains / max(total_proteins, 1), 4),
        "total_domain_hits": hmmscan_stats.get("total_hits", 0),
        "significant_domain_hits": len(domains),
        "unique_domain_types": len(domain_freq),
        "avg_domains_per_protein": round(len(domains) / max(proteins_with_domains, 1), 2),
        "top_domains": top_domains
    }
    
    return metrics


# ===================== 主函数 =====================

def main():
    parser = argparse.ArgumentParser(
        description='Domain stage - Protein domain analysis (Pfam HMM scan + visualization)'
    )
    parser.add_argument('--prefix', required=True, 
                       help='Analysis prefix (e.g., Ft.SPL)')
    parser.add_argument('--workspace', required=True, 
                       help='Workspace directory (e.g., workspace/20260212_143523_2480772_ec5c6d)')
    parser.add_argument('--proteins', 
                       help='Protein FASTA file (auto-detect from Identify manifest if not specified)')
    parser.add_argument('--scope-file',
                       help='Gene-family scope JSON from resolve_scope.py')
    parser.add_argument('--analysis-route', default='', help='Explicit analysis route identity for manifest/status')
    parser.add_argument('--member-set-id', default='', help='Explicit member-set identity for manifest/status')
    parser.add_argument('--tree', 
                       help='Tree file (auto-detect from Phylogeny manifest if not specified)')
    parser.add_argument('--subfamily', 
                       help='Subfamily classification file (explicit path; auto-detect only uses confirmed_subfamily.tsv)')
    parser.add_argument('--pfam-base', 
                       default=DEFAULT_PFAM_BASE,
                       help=f'Pfam database base directory (default: {DEFAULT_PFAM_BASE})')
    parser.add_argument('--evalue', type=float, 
                       default=DEFAULT_EVALUE,
                       help=f'E-value cutoff (default: {DEFAULT_EVALUE})')
    parser.add_argument('--coverage', type=float, 
                       help='Coverage cutoff percentage (optional, e.g., 50 for 50%%)')
    parser.add_argument('--cpu', type=int, default=8, 
                       help='CPU cores for hmmscan (default: 8)')
    parser.add_argument('--rscript', 
                       help='Path to tree_domain.R script (auto-detect if not specified)')
    parser.add_argument('--log-file', 
                       help='Log file path')
    parser.add_argument('--run-id', 
                       help='Run ID (auto-generated if not provided)')
    
    args = parser.parse_args()
    
    run_id = args.run_id or str(uuid.uuid4())[:8]
    job_id = Path(args.workspace).name
    
    start_time = datetime.now()
    ws = Path(args.workspace)
    
    # 验证 workspace 路径
    if not ws.exists():
        print(f"[ERROR] Workspace directory not found: {ws}")
        sys.exit(1)
    
    # 1. 创建标准目录结构（契约要求）
    domain_dir = ws / "domain" / "analysis"
    domain_dir.mkdir(parents=True, exist_ok=True)
    final_dir = ws / "final"
    final_dir.mkdir(parents=True, exist_ok=True)

    scope_data: Optional[Dict] = None
    scope_file: Optional[Path] = None
    if args.scope_file:
        scope_file = Path(args.scope_file)
        if not scope_file.exists():
            error_msg = f"Scope file not found: {args.scope_file}"
            print(f"[ERROR] {error_msg}")
            write_failure_manifest(ws, final_dir, args.prefix, job_id, run_id, start_time, error_msg)
            sys.exit(1)
        scope_data = read_scope(scope_file)
    scope_id = str((scope_data or {}).get("scope_id") or "").strip()
    output_prefix = user_output_prefix_for(
        "domain",
        args.prefix,
        scope_data or {},
        analysis_route=args.analysis_route,
        member_set_id=args.member_set_id,
    ) if scope_id else args.prefix
    
    print(f"[INFO] Domain stage started for {args.prefix}")
    print(f"[INFO] Job ID: {job_id}, Run ID: {run_id}")
    print(f"[INFO] Workspace: {display_path(ws)}")
    
    # 2. 解析输入文件（支持命令行指定或自动查找）
    
    # 2.1 蛋白序列文件
    if scope_data:
        try:
            protein_file = resolve_scope_protein_fasta(ws, args.prefix, scope_data, args.proteins)
        except FileNotFoundError as e:
            error_msg = str(e)
            print(f"[ERROR] {error_msg}")
            write_failure_manifest(ws, final_dir, args.prefix, job_id, run_id, start_time, error_msg)
            sys.exit(1)
    elif args.proteins:
        protein_file = resolve_job_path(ws, args.proteins) or Path(args.proteins)
        if not protein_file.exists():
            error_msg = f"Specified protein file not found: {protein_file}"
            print(f"[ERROR] {error_msg}")
            write_failure_manifest(ws, final_dir, args.prefix, job_id, run_id, start_time, error_msg)
            sys.exit(1)
    else:
        phase = "subfamily_evidence_required" if args.analysis_route == "subfamily_evidence" else "post_confirm_required"
        try:
            resolved = resolve_effective_members(
                workspace=ws,
                final_dir=final_dir,
                input_dir=current_input_dir(ws),
                prefix=args.prefix,
                phase=phase,  # type: ignore[arg-type]
                require="pep",
                member_set_id=args.member_set_id,
            )
            if resolved.path_pep:
                protein_file = resolved.path_pep
                print(f"[INFO] Protein file from {resolved.source_kind}: {protein_file}")
            else:
                protein_file = Path("")
        except MemberResolutionError as e:
            if args.analysis_route == "subfamily_evidence":
                error_msg = f"Complete identify member review before Domain subfamily evidence: {e}"
            else:
                error_msg = f"Complete subfamily confirmation before regular Domain analysis: {e}"
            print(f"[ERROR] {error_msg}")
            write_failure_manifest(ws, final_dir, args.prefix, job_id, run_id, start_time, error_msg)
            sys.exit(1)
        if protein_file.exists() and protein_file.stat().st_size > 0:
            protein_file = ensure_protein_fasta_without_stop_markers(protein_file)
            print(f"[INFO] Protein file: {protein_file}")
            protein_record_count = count_fasta_records(protein_file)
        else:
            protein_file = Path("")

    if scope_data or args.proteins:
        protein_file = ensure_protein_fasta_without_stop_markers(protein_file)
        print(f"[INFO] Protein file: {protein_file}")
        protein_record_count = count_fasta_records(protein_file)

    if not scope_data and not args.proteins and not protein_file:
        candidates = [final_dir / f"{args.prefix}.final_members.pep.fa"]
        if "." in args.prefix:
            species, family = args.prefix.split(".", 1)
            if species not in {"Target", "Multi", "Reference", "Comparative"} and family:
                candidates.append(final_dir / f"Target.{species}.{family}.final_members.pep.fa")
        # 从 Identify manifest 查找
        identify_manifest = find_latest_manifest(final_dir, "identify")
        if identify_manifest:
            try:
                manifest_data = read_manifest(identify_manifest)
                artifacts = manifest_data.get("artifacts", {}) if isinstance(manifest_data.get("artifacts", {}), dict) else {}
                for key in ("final_members", "final_proteins", "proteins"):
                    item = artifacts.get(key)
                    protein_path = manifest_artifact_path(ws, identify_manifest, item) if isinstance(item, dict) else None
                    if protein_path:
                        candidates.append(protein_path)
            except Exception:
                pass
        protein_file = next((path for path in candidates if path.exists() and path.stat().st_size > 0), candidates[0])
        
        if not protein_file.exists():
            error_msg = "Protein file not found. Checked: " + ", ".join(str(path) for path in candidates) + ". Please run Identify stage first or specify --proteins."
            print(f"[ERROR] {error_msg}")
            write_failure_manifest(ws, final_dir, args.prefix, job_id, run_id, start_time, error_msg)
            sys.exit(1)
        protein_file = ensure_protein_fasta_without_stop_markers(protein_file)
        print(f"[INFO] Protein file: {protein_file}")
        protein_record_count = count_fasta_records(protein_file)
    
    # 2.2 树文件
    try:
        tree_file = resolve_scope_tree_file(ws, args.prefix, scope_data, args.tree)
    except FileNotFoundError as e:
        error_msg = str(e)
        print(f"[ERROR] {error_msg}")
        write_failure_manifest(ws, final_dir, args.prefix, job_id, run_id, start_time, error_msg)
        sys.exit(1)
    
    print(f"[INFO] Tree file: {tree_file}")
    
    # 2.3 亚家族文件（可选，但 R 脚本需要）
    # 自动查找只接受 confirmed_subfamily；prediction/geneID 必须显式传入
    subfamily_file = resolve_subfamily_file(ws, args.prefix, job_id, args.subfamily, output_prefix=output_prefix)
    
    # 3. 准备 Pfam 数据库
    print("[INFO] Preparing Pfam database...")
    pfam_hmm, pfam_success, pfam_error = prepare_pfam_database(domain_dir, args.pfam_base)
    
    if not pfam_success:
        print(f"[ERROR] {pfam_error}")
        write_failure_manifest(ws, final_dir, args.prefix, job_id, run_id, start_time, pfam_error)
        sys.exit(1)
    
    print(f"[INFO] Pfam ready: {pfam_hmm}")
    
    # 4. 运行 hmmscan
    print(f"[INFO] Running hmmscan (this may take a while)...")
    hmm_success, hmm_result = run_hmmscan(
        pfam_hmm, protein_file, domain_dir,
        output_prefix=output_prefix,
        cpu=args.cpu, evalue=args.evalue
    )
    
    if not hmm_success:
        error_msg = f"hmmscan failed: {hmm_result.get('error', 'unknown error')}"
        print(f"[ERROR] {error_msg}")
        print(f"[ERROR] stderr: {hmm_result.get('stderr', '')}")
        write_failure_manifest(ws, final_dir, args.prefix, job_id, run_id, start_time, error_msg)
        sys.exit(1)
    
    print(f"[INFO] hmmscan completed in {hmm_result['elapsed_sec']:.1f}s")
    print(f"[INFO] domtblout: {hmm_result['domtblout']}")
    
    # 5. 解析 domtblout
    print(f"[INFO] Parsing domain hits (E < {args.evalue})...")
    domains, hmm_stats = parse_domtblout(hmm_result['domtblout'], args.evalue)
    
    print(f"[INFO]  - Total hits: {hmm_stats['total_hits']}")
    print(f"[INFO]  - Significant hits: {hmm_stats['significant_hits']}")
    print(f"[INFO]  - Unique proteins: {hmm_stats['unique_proteins']}")
    print(f"[INFO]  - Unique domains: {hmm_stats['unique_domains']}")
    
    if not domains:
        print(f"[WARNING] No significant domain hits found with E-value < {args.evalue}")
        # 不退出，继续生成空报告
    
    # 6. 计算 metrics
    # 获取总蛋白数量（从蛋白文件）
    try:
        from Bio import SeqIO
        total_proteins = len(list(SeqIO.parse(protein_file, "fasta")))
    except:
        # 回退：从 domains 估算
        total_proteins = hmm_stats.get("unique_proteins", 0)
    
    domain_metrics = calculate_domain_metrics(domains, total_proteins, hmm_stats)
    
    print(f"[INFO] Metrics calculated:")
    for k, v in domain_metrics.items():
        if k != "top_domains":
            print(f"  - {k}: {v}")
    
    # 7. R 绘图（输出到 final/）
    # 使用标准命名：{prefix}.domain（不含扩展名，R脚本会自动添加 .pdf 和 .png）
    output_path_prefix = final_dir / f"{output_prefix}.domain"
    
    # 确定 R 脚本路径
    if args.rscript:
        rscript_path = Path(args.rscript)
    else:
        rscript_path = Path(__file__).parent.parent.parent / "scripts" / "tree_domain.R"
    
    plot_result = run_r_plotting(
        tree_file,
        subfamily_file,
        hmm_result['domtblout'],
        output_path_prefix,
        rscript_path=rscript_path,
        evalue_cutoff=args.evalue,
        coverage_cutoff=args.coverage
    )
    
    plot_success = plot_result.get("success", False)
    if not plot_success:
        print(f"[WARNING] R plotting failed: {plot_result.get('error', plot_result.get('stderr', 'unknown'))}")
    else:
        print(f"[INFO] Plot files generated: {plot_result.get('output_files', [])}")
    
    # 8. 准备 artifacts
    end_time = datetime.now()
    artifacts = {}
    
    # 中间文件（domtblout, tblout）
    if hmm_result['domtblout'] and hmm_result['domtblout'].exists():
        artifacts["domtblout"] = {
            "artifact": "domtblout",
            "path": str(hmm_result['domtblout'].relative_to(ws)),
            "full_path": display_path(hmm_result['domtblout']),
            "type": "txt",
            "size": hmm_result['domtblout'].stat().st_size,
            "sha256": compute_file_hash(hmm_result['domtblout']),
            "description": "hmmscan domain table output (raw)"
        }
    
    if hmm_result['tblout'] and hmm_result['tblout'].exists():
        artifacts["tblout"] = {
            "artifact": "tblout",
            "path": str(hmm_result['tblout'].relative_to(ws)),
            "full_path": display_path(hmm_result['tblout']),
            "type": "txt",
            "size": hmm_result['tblout'].stat().st_size,
            "description": "hmmscan per-sequence hits (raw)"
        }
    
    # 最终产物（图片）- 同时支持 PNG 和 PDF
    for img_file in plot_result.get("output_files", []):
        img_path = Path(img_file)
        if img_path.exists():
            key = f"domain_plot_{img_path.suffix[1:]}"  # domain_plot_png, domain_plot_pdf
            artifacts[key] = {
                "artifact": "domain_plot",
                "path": str(img_path.relative_to(ws)),
                "full_path": display_path(img_path),
                "type": "image",
                "format": img_path.suffix[1:],
                "size": img_path.stat().st_size,
                "sha256": compute_file_hash(img_path),
                "description": f"Domain architecture visualization ({img_path.suffix[1:].upper()})"
            }
    
    # 9. 生成报告
    report_file = final_dir / f"{output_prefix}.domain_report.md"
    
    parameters = {
        "cpu": args.cpu,
        "prefix": args.prefix,
        "output_prefix": output_prefix,
        "stage": "domain",
        "job_id": job_id,
        "run_id": run_id,
        "evalue_cutoff": args.evalue,
        "coverage_cutoff": args.coverage,
        "pfam_hmm": str(pfam_hmm),
        "pfam_base": args.pfam_base,
        "protein_source": str(protein_file),
        "protein_sha256": compute_file_hash(protein_file),
        "protein_record_count": protein_record_count,
        "tree_file": str(tree_file),
        "subfamily_file": str(subfamily_file) if subfamily_file else None,
        "hmmscan_elapsed_sec": hmm_result.get("elapsed_sec", 0),
        "Rscript_path": str(rscript_path),
        "tool_versions": get_tool_versions(),
        "timestamp_start": start_time.isoformat(),
        "timestamp_end": end_time.isoformat(),
        "workspace_root": display_path(workspace_root_dir(ws)),
        "workspace": display_path(ws),
        "input_dir": display_path(current_input_dir(ws)),
        "domain_analysis_dir": display_path(domain_dir),
        "final_dir": display_path(final_dir)
    }
    if scope_data:
        parameters["scope_file"] = str(scope_file)
        parameters["scope_id"] = scope_data.get("scope_id", "")
        parameters["scope_mode"] = scope_data.get("scope_mode", "")
        parameters["target_species"] = scope_data.get("target_species", [])
        parameters["reference_species"] = scope_data.get("reference_species", [])
        parameters["comparative_species"] = scope_data.get("comparative_species", [])
        parameters["selected_species"] = scope_data.get("selected_species", [])
    
    generate_report(output_prefix, domain_metrics, parameters, report_file, artifacts, plot_success)
    
    if report_file.exists():
        artifacts["domain_report"] = {
            "artifact": "domain_report",
            "path": str(report_file.relative_to(ws)),
            "full_path": display_path(report_file),
            "type": "report",
            "format": "markdown",
            "size": report_file.stat().st_size,
            "sha256": compute_file_hash(report_file),
            "description": "Human-readable analysis report"
        }
    
    # 10. 确定状态
    # if not domains:
    #     status = "partial_success"  # hmmscan 成功但无显著 hits
    # elif plot_success:
    #     status = "success"
    # else:
    #     status = "partial_success
        # 检查输出文件是否存在
    expected_png = final_dir / f"{output_prefix}.domain.png"
    expected_pdf = final_dir / f"{output_prefix}.domain.pdf"

    if plot_success and (expected_png.exists() or expected_pdf.exists()):
        status = "success"  # 只要绘图成功，就视为成功
    else:
        status = "partial_success"
    
    # 11. 构建完整 manifest
    manifest = {
        "manifest_version": "1.0",
        "job_id": job_id,
        "stage": "domain",
        "run_id": run_id,
        "prefix": args.prefix,
        "output_prefix": output_prefix,
        "timestamp": end_time.isoformat(),
        "status": status,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "elapsed_sec": (end_time - start_time).total_seconds(),
        "cmd": [
            str(Path(__file__).name),
            "--prefix", args.prefix,
            "--workspace", str(ws),
            "--proteins", str(protein_file),
            "--tree", str(tree_file),
            "--pfam-base", args.pfam_base,
            "--evalue", str(args.evalue),
            "--cpu", str(args.cpu),
            "--run-id", run_id
        ] + (["--scope-file", str(scope_file)] if scope_file else [])
          + (["--subfamily", str(subfamily_file)] if subfamily_file else [])
          + (["--coverage", str(args.coverage)] if args.coverage else [])
          + (["--rscript", str(rscript_path)] if args.rscript else []),
        "workspace_root": display_path(workspace_root_dir(ws)),
        "workspace": display_path(ws),
        "input_dir": display_path(current_input_dir(ws)),
        "artifacts": artifacts,
        "parameters": parameters,
        "metrics": domain_metrics
    }
    manifest["metrics"]["protein_record_count"] = protein_record_count
    if scope_data:
        manifest["scope_id"] = scope_data.get("scope_id", "")
        manifest["scope_mode"] = scope_data.get("scope_mode", "")
        manifest["scope_file"] = str(scope_file)
        manifest["target_species"] = scope_data.get("target_species", [])
        manifest["reference_species"] = scope_data.get("reference_species", [])
        manifest["comparative_species"] = scope_data.get("comparative_species", [])
        manifest["selected_species"] = scope_data.get("selected_species", [])
        manifest["scope"] = {
            "family": scope_data.get("family", ""),
            "prefix": scope_data.get("prefix", ""),
            "scope_id": scope_data.get("scope_id", ""),
            "scope_mode": scope_data.get("scope_mode", ""),
            "target_species": scope_data.get("target_species", []),
            "reference_species": scope_data.get("reference_species", []),
            "comparative_species": scope_data.get("comparative_species", []),
            "selected_species": scope_data.get("selected_species", []),
        }
    context = build_stage_context(
        stage="domain",
        prefix=args.prefix,
        scope_data=scope_data or {},
        scope_file=scope_file or "",
        output_prefix=output_prefix,
        subfamily_file=subfamily_file,
        analysis_route=args.analysis_route,
        member_set_id=args.member_set_id,
    )
    manifest["tree_source"] = str(tree_file)
    manifest["protein_fasta"] = str(protein_file)
    apply_stage_context(manifest, context)
    
    manifest_file = final_dir / f"{output_prefix}.domain.manifest.json"
    write_manifest(manifest, manifest_file)
    if scope_data and scope_data.get("scope_id"):
        write_legacy_manifest_copy(manifest_file, args.prefix, str(scope_data.get("scope_id") or ""), "domain")
        quarantine_stale_unscoped_failure(final_dir, args.prefix, "domain", manifest_file)
    
    # 12. 完成
    if status == "success":
        print(f"[INFO] Domain stage completed successfully")
    else:
        print(f"[WARNING] Domain stage completed with status: {status}")
    
    print("\\n=== OUTPUT_FILES ===")
    for k, v in artifacts.items():
        if isinstance(v, dict) and v.get("type") == "image":
            print(f"IMAGE: {v['full_path']}")
    
    sys.exit(0 if status == "success" else 1)


if __name__ == "__main__":
    main()
