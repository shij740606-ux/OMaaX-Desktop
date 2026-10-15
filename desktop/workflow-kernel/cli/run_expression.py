#!/usr/bin/env python3
"""
Expression阶段：基因表达量分析与可视化

输入：
  - expression_file: 表达量矩阵文件 (如 TPM/FPKM 矩阵)
  - gene_id_file: 基因ID列表文件
  - sample_file: 样本信息文件

输出：
  - final/{prefix}.expression.manifest.json (机器清单)
  - final/{prefix}.expression_report.md (分析报告)
  - final/{prefix}*.pdf (表达量图 PDF)
  - final/{prefix}*.png (表达量图 PNG)

依赖：
  - R环境 (conda env: rnaR)
  - pdftoppm (用于PDF转PNG)
  - 上游阶段: Identify (需要 final_members.id)

示例：
  python run_expression.py --prefix Ft.SPL \\
    --workspace /path/to/workspace \\
    --expression-file expression_matrix.txt \\
    --gene-id-file gene_ids.txt \\
    --sample-file sample_info.txt
"""

import os
import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

PROJECT_ROOT = Path(__file__).absolute().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.gene_family_scope import read_scope, sanitize_token
from gfa_core.gene_family_stage_context import (
    apply_stage_context,
    build_stage_context,
    infer_analysis_route,
    member_set_id_for,
)
from gfa_core.rnaseq_context import apply_rnaseq_context, build_rnaseq_context
from cli.rnaseq_env import prepend_path, rnaseq_rscript, rnar_bin, rnar_rscript

CONDA_BIN = str(rnar_bin())
prepend_path(CONDA_BIN)


def scope_parameters(
    scope_file: Optional[Path],
    scope_data: Optional[Dict[str, Any]],
    analysis_route: str = "",
    member_set_id: str = "",
) -> Dict[str, Any]:
    if not scope_file or not scope_data:
        return {}
    params = {
        "scope_file": str(scope_file),
        "scope_id": scope_data.get("scope_id", ""),
        "scope_mode": scope_data.get("scope_mode", ""),
        "target_species": scope_data.get("target_species", []),
        "reference_species": scope_data.get("reference_species", []),
        "comparative_species": scope_data.get("comparative_species", []),
        "selected_species": scope_data.get("selected_species", []),
        "family": scope_data.get("family", ""),
    }
    if analysis_route:
        params["analysis_route"] = analysis_route
    if member_set_id:
        params["member_set_id"] = member_set_id
    return params

def get_tool_versions() -> Dict[str, str]:
    """获取工具版本信息"""
    versions = {}
    
    # R版本
    try:
        result = subprocess.run(
            ["R", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        if result.returncode == 0:
            lines = result.stdout.split('\n')
            for line in lines:
                if "R version" in line:
                    versions["R"] = line.strip()
                    break
            if "R" not in versions:
                versions["R"] = lines[0] if lines else "unknown"
    except Exception as e:
        versions["R"] = f"error: {str(e)[:50]}"
    
    # pdftoppm版本
    try:
        result = subprocess.run(
            ["pdftoppm", "-v"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        output = result.stdout.strip() or result.stderr.strip()
        if output:
            versions["pdftoppm"] = output.split('\n')[0]
        else:
            versions["pdftoppm"] = "available"
    except Exception as e:
        versions["pdftoppm"] = f"error: {str(e)[:50]}"
    
    # Python版本
    versions["python"] = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    
    return versions


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


def abs_text(path: Optional[Path]) -> str:
    return str(Path(path).absolute()) if path else ""


def parse_metric_tsv(path: Path) -> Dict[str, str]:
    metrics: Dict[str, str] = {}
    if not path.exists():
        return metrics
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        header = handle.readline()
        for line in handle:
            if not line.strip() or "\t" not in line:
                continue
            key, value = line.rstrip("\n").split("\t", 1)
            metrics[key] = value
    return metrics


def truthy_metric(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "pass"}


def resolve_rnaseq_expression_matrices(
    final_dir: Path,
    prefix: str,
    fpkm_file: Optional[Path] = None,
    tpm_file: Optional[Path] = None,
    vst_file: Optional[Path] = None,
) -> Dict[str, Any]:
    """Resolve RNA-seq expression matrices from quant bridge QC.

    FPKM remains featureCounts-derived. TPM is switched to Salmon/Kallisto
    tximport only when rnaseq_quant matrix_bridge_qc explicitly passes.
    """
    fpkm = fpkm_file or final_dir / f"{prefix}.fpkm_matrix.txt"
    featurecounts_tpm = tpm_file or final_dir / f"{prefix}.tpm_matrix.txt"
    vst = vst_file or final_dir / f"{prefix}.rnaseq_sample_qc.vst_matrix.tsv"
    qc_file = final_dir / f"{prefix}.rnaseq_quant.matrix_bridge_qc.tsv"
    qc = parse_metric_tsv(qc_file)
    status_pass = qc.get("status") == "pass"
    source = qc.get("recommended_expression_tpm_source", "")
    engine = source.split("_", 1)[0] if source.endswith("_tximport_tpm") else ""
    candidate = final_dir / f"{prefix}.{engine}.tpm_matrix.txt" if engine else featurecounts_tpm

    if status_pass and engine in {"salmon", "kallisto", "rsem"} and candidate.exists():
        chosen_tpm = candidate
        tpm_source = source
        reason = f"bridge_qc_passed:{qc_file.name}"
    else:
        chosen_tpm = featurecounts_tpm
        tpm_source = "featurecounts_derived_tpm"
        if not qc:
            reason = "matrix_bridge_qc_missing"
        elif not status_pass:
            reason = "bridge_qc_not_pass:" + (qc.get("failure_reasons") or qc.get("status", "unknown"))
        elif not candidate.exists():
            reason = f"bridge_qc_candidate_missing:{candidate.name}"
        else:
            reason = "featurecounts_tpm_requested"

    return {
        "fpkm_file": fpkm,
        "tpm_file": chosen_tpm,
        "vst_file": vst,
        "tpm_source": tpm_source,
        "tpm_source_reason": reason,
        "matrix_bridge_qc_file": qc_file if qc_file.exists() else None,
        "matrix_bridge_qc": qc,
        "display_evidence_policy": "DE heatmaps use VST from featureCounts/DESeq2; abundance distributions and family TPM plots use tximport TPM only after bridge QC passes.",
    }


def validate_previous_stage(workspace: Path, prefix: str, gene_id_override: Optional[Path] = None) -> Dict[str, Any]:
    """
    验证Identify阶段的输出文件并获取必要信息
    
    Args:
        workspace: 工作空间目录（workspace/<job_id>）
        prefix: 分析前缀，如 Ft.SPL
    
    Returns:
        包含输入文件路径和Identify manifest数据的字典
    
    Raises:
        FileNotFoundError: Identify输出不存在
        ValueError: 文件无效或格式错误
    """
    print(f"正在验证Identify阶段输出: {workspace}")
    
    # 1. 查找Identify的manifest
    identify_manifest_path = workspace / "final" / f"{prefix}.identify.manifest.json"
    if not identify_manifest_path.exists():
        raise FileNotFoundError(f"Identify manifest未找到: {identify_manifest_path}")
    
    print(f"  找到Identify manifest: {identify_manifest_path}")
    
    # 2. 读取Identify manifest
    with open(identify_manifest_path, 'r') as f:
        identify_manifest = json.load(f)
    
    # 3. 验证Identify状态
    if identify_manifest.get("status") != "success":
        raise ValueError(f"Identify阶段未成功完成，状态: {identify_manifest.get('status')}")
    
    # 4. 查找基因ID文件
    final_members_id = gene_id_override if gene_id_override else workspace / "final" / f"{prefix}.final_members.id"
    if not final_members_id.exists():
        raise FileNotFoundError(f"基因ID文件未找到: {final_members_id}")
    
    print(f"  找到基因ID文件: {final_members_id}")
    
    # 读取基因数量
    with open(final_members_id, 'r') as f:
        gene_count = len([line for line in f if line.strip()])
    
    print(f"  基因数量: {gene_count}")
    
    return {
        "identify_manifest": identify_manifest,
        "final_members_id": final_members_id,
        "gene_count": gene_count,
        "identify_inputs": identify_manifest.get("inputs", [])
    }


def prepare_directories(workspace: Path) -> Dict[str, Path]:
    """创建Expression阶段的标准目录结构"""
    paths = {
        "analysis_dir": workspace / "expression" / "analysis",
        "final_dir": workspace / "final",
        "logs_dir": workspace / "logs",
    }
    
    print("创建目录结构:")
    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        print(f"  {name}: {path}")
    
    return paths



def run_rnaseq_auto_expression(
    workspace: Path,
    prefix: str,
    job_id: str,
    run_id: str,
    fpkm_file: Path,
    tpm_file: Path,
    vst_file: Path,
    sample_table: Path,
    de_dir: Path,
    count_matrix: Optional[Path] = None,
    family_members: Optional[Path] = None,
    family_label: str = "",
    top_n: int = 100,
    time_top_n: int = 12,
    triggered_by: str = "rnaseq_de",
) -> Path:
    """Run RNA-seq-derived expression plots without requiring Identify.

    This mode is the automatic post-processing path after rnaseq_de.  The
    legacy expression mode below remains available for user-supplied FPKM/TPM
    matrices and explicit gene lists.
    """
    final_dir = workspace / "final"
    logs_dir = workspace / "logs"
    final_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    script = PROJECT_ROOT / "scripts" / "draw_rnaseq_expression.R"
    if not script.exists():
        raise FileNotFoundError(f"RNA-seq expression plotting script not found: {script}")
    resolved_matrices = resolve_rnaseq_expression_matrices(
        final_dir=final_dir,
        prefix=prefix,
        fpkm_file=fpkm_file,
        tpm_file=tpm_file,
        vst_file=vst_file,
    )
    fpkm_file = Path(resolved_matrices["fpkm_file"])
    tpm_file = Path(resolved_matrices["tpm_file"])
    vst_file = Path(resolved_matrices["vst_file"])
    tpm_source = str(resolved_matrices.get("tpm_source", "unknown"))
    tpm_source_reason = str(resolved_matrices.get("tpm_source_reason", ""))
    matrix_bridge_qc_file = resolved_matrices.get("matrix_bridge_qc_file")
    required_paths = {
        "fpkm_file": fpkm_file,
        "tpm_file": tpm_file,
        "vst_file": vst_file,
        "sample_table": sample_table,
        "de_dir": de_dir,
    }
    missing = [name for name, path in required_paths.items() if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing RNA-seq expression inputs: " + ", ".join(missing))

    cmd = [
        rnaseq_rscript(),
        str(script),
        f"fpkm={fpkm_file}",
        f"tpm={tpm_file}",
        f"vst={vst_file}",
        f"sample_table={sample_table}",
        f"de_dir={de_dir}",
        f"prefix={prefix}",
        f"out_dir={final_dir}",
        f"top_n={top_n}",
        f"time_top_n={time_top_n}",
        f"tpm_source={tpm_source}",
    ]
    if matrix_bridge_qc_file:
        cmd.append(f"matrix_bridge_qc={matrix_bridge_qc_file}")
    if count_matrix and count_matrix.exists():
        cmd.append(f"count_matrix={count_matrix}")
    if family_members and family_members.exists():
        cmd.append(f"gene_family_members={family_members}")
        cmd.append(f"family_label={family_label or family_members.stem}")

    start_time = datetime.now()
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=1800)
    end_time = datetime.now()
    status = "success" if result.returncode == 0 else "failed"

    artifacts: Dict[str, Dict[str, Any]] = {}
    for file in sorted(final_dir.glob(f"{prefix}.rnaseq_expression_plot.*")):
        if not file.is_file():
            continue
        suffix = file.suffix.lower().lstrip(".")
        if suffix in {"png"}:
            artifact_type = "image"
        elif suffix in {"pdf"}:
            artifact_type = "plot"
        elif suffix in {"tsv", "txt"}:
            artifact_type = "table"
        else:
            artifact_type = "file"
        key = file.name.replace(f"{prefix}.", "").replace(".", "_")
        artifacts[key] = {
            "artifact": key,
            "path": f"final/{file.name}",
            "full_path": abs_text(file),
            "type": artifact_type,
            "format": suffix,
            "size": file.stat().st_size,
            "sha256": compute_file_hash(file),
        }
    de_tpm_consistency = final_dir / f"{prefix}.rnaseq_expression_plot.de_tpm_consistency.tsv"
    missing_de_genes_in_tpm: Optional[int] = None
    if de_tpm_consistency.exists():
        try:
            with open(de_tpm_consistency, "r", encoding="utf-8", errors="ignore") as handle:
                header = handle.readline().rstrip("\n").split("\t")
                missing_idx = header.index("missing_de_genes_in_tpm") if "missing_de_genes_in_tpm" in header else -1
                if missing_idx >= 0:
                    total_missing = 0
                    for line in handle:
                        parts = line.rstrip("\n").split("\t")
                        if missing_idx < len(parts) and parts[missing_idx]:
                            total_missing += int(float(parts[missing_idx]))
                    missing_de_genes_in_tpm = total_missing
        except Exception:
            missing_de_genes_in_tpm = None

    manifest_path = final_dir / f"{prefix}.expression.manifest.json"
    report_path = final_dir / f"{prefix}.expression_report.md"
    if manifest_path.exists():
        try:
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            existing_manifest = {}
        existing_params = existing_manifest.get("parameters") if isinstance(existing_manifest.get("parameters"), dict) else {}
        existing_is_gene_family = bool(
            existing_manifest.get("scope_id")
            or existing_params.get("scope_id")
            or (existing_manifest.get("stage_context") or {}).get("scope_id")
        ) and not bool(existing_manifest.get("rnaseq_scope_id") or existing_params.get("rnaseq_scope_id"))
        if existing_is_gene_family:
            manifest_path = final_dir / f"{prefix}.rnaseq_expression.manifest.json"
            report_path = final_dir / f"{prefix}.rnaseq_expression_report.md"
    report_path.write_text(
        "\n".join([
            f"# RNA-seq Expression 自动绘图报告 - {prefix}",
            "",
            f"- 状态: {status}",
            f"- 触发阶段: {triggered_by}",
            f"- FPKM: {fpkm_file}",
            f"- TPM: {tpm_file}",
            f"- TPM source: {tpm_source}",
            f"- TPM source reason: {tpm_source_reason}",
            f"- Matrix bridge QC: {matrix_bridge_qc_file if matrix_bridge_qc_file else 'not found'}",
            f"- Missing significant DE genes in TPM: {missing_de_genes_in_tpm if missing_de_genes_in_tpm is not None else 'not available'}",
            f"- VST: {vst_file}",
            f"- DE目录: {de_dir}",
            f"- Family members: {family_members if family_members else 'not provided'}",
            f"- Artifacts: {len(artifacts)}",
            "",
            "```bash",
            " ".join(str(x) for x in cmd),
            "```",
            "",
            "## stderr tail",
            "```",
            (result.stderr or "")[-2000:],
            "```",
        ]) + "\n",
        encoding="utf-8",
    )
    artifacts["report"] = {
        "artifact": "report",
        "path": f"final/{report_path.name}",
        "full_path": abs_text(report_path),
        "type": "markdown",
        "format": "md",
        "size": report_path.stat().st_size,
        "sha256": compute_file_hash(report_path),
    }

    rnaseq_context = build_rnaseq_context(
        prefix=prefix,
        count_matrix=count_matrix,
        sample_table=sample_table,
        foreground_mode="family" if family_members and family_members.exists() else "global",
        family_members_file=family_members if family_members and family_members.exists() else None,
        family_label=family_label,
        stage="expression",
    )
    manifest = {
        "manifest_version": "1.0",
        "job_id": job_id,
        "stage": "expression",
        "prefix": prefix,
        "run_id": run_id,
        "timestamp": end_time.isoformat(),
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "elapsed_sec": round((end_time - start_time).total_seconds(), 2),
        "status": status,
        "triggered_by": triggered_by,
        "mode": "rnaseq_auto",
        "cmd": [" ".join(str(x) for x in cmd)],
        "workspace_root": abs_text(workspace.parent),
        "workspace": abs_text(workspace),
        "input_dir": abs_text(workspace.parent.parent / "input" / job_id),
        "parameters": {
            "mode": "rnaseq_auto",
            "triggered_by": triggered_by,
            "fpkm_file": abs_text(fpkm_file),
            "tpm_file": abs_text(tpm_file),
            "tpm_source": tpm_source,
            "tpm_source_reason": tpm_source_reason,
            "matrix_bridge_qc": abs_text(matrix_bridge_qc_file) if matrix_bridge_qc_file else "",
            "display_evidence_policy": resolved_matrices.get("display_evidence_policy", ""),
            "vst_file": abs_text(vst_file),
            "sample_table": abs_text(sample_table),
            "de_dir": abs_text(de_dir),
            "count_matrix": abs_text(count_matrix) if count_matrix else "",
            "family_members": abs_text(family_members) if family_members else "",
            "family_label": family_label,
            "top_n": top_n,
            "time_top_n": time_top_n,
        },
        "metrics": {
            "total_artifacts": len(artifacts),
            "returncode": result.returncode,
            "family_branch": bool(family_members and family_members.exists()),
            "matrix_bridge_qc": resolved_matrices.get("matrix_bridge_qc", {}),
            "missing_de_genes_in_tpm": missing_de_genes_in_tpm,
            "display_evidence_policy": resolved_matrices.get("display_evidence_policy", ""),
        },
        "artifacts": artifacts,
        "provenance": {
            "derived_from": [
                f"{prefix}.rnaseq_quant.manifest.json",
                f"{prefix}.rnaseq_sample_qc.manifest.json",
                f"{prefix}.rnaseq_de.manifest.json",
            ],
            "stage_sequence": ["rnaseq_quant", "rnaseq_sample_qc", "rnaseq_de", "expression"],
        },
        "stdout_tail": (result.stdout or "")[-2000:],
        "stderr_tail": (result.stderr or "")[-2000:],
    }
    apply_rnaseq_context(manifest, rnaseq_context)
    artifacts["manifest"] = {
        "artifact": "manifest",
        "path": f"final/{manifest_path.name}",
        "full_path": abs_text(manifest_path),
        "type": "json",
        "format": "json",
    }
    manifest["artifacts"] = artifacts
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"RNA-seq expression plotting failed; see {manifest_path}")
    return manifest_path


def run_expression_analysis(
    expression_file: Path,
    gene_id_file: Path,
    sample_file: Path,
    output_prefix: str,
    output_dir: Path,
    id_transform: str = "none"
) -> Dict[str, Any]:
    """运行表达量分析并生成PDF和PNG"""
    
    # 处理基因ID转换
    processed_gene_file = output_dir / f"{output_prefix}.geneID.processed"
    
    if id_transform == "remove_T01":
        # 去除.T01后缀
        with open(gene_id_file, 'r') as f:
            lines = f.readlines()
        with open(processed_gene_file, 'w') as f:
            for line in lines:
                parts = line.strip().split('\t')
                if parts:
                    gene_id = parts[0].replace('.T01', '')
                    f.write(gene_id + '\t' + '\t'.join(parts[1:]) + '\n')
        print(f"  已转换基因ID (去除.T01后缀): {processed_gene_file}")
    elif id_transform == "add_T01":
        # 添加.T01后缀
        with open(gene_id_file, 'r') as f:
            lines = f.readlines()
        with open(processed_gene_file, 'w') as f:
            for line in lines:
                parts = line.strip().split('\t')
                if parts:
                    gene_id = parts[0] + '.T01'
                    f.write(gene_id + '\t' + '\t'.join(parts[1:]) + '\n')
        print(f"  已转换基因ID (添加.T01后缀): {processed_gene_file}")
    else:
        # 不转换
        processed_gene_file = gene_id_file
    
    # 构建命令 - 使用完整路径避免conda run的全局锁
    rscript_path = rnar_rscript()
    cmd = [
        rscript_path, str(Path("scripts/draw_expression.R")),
        str(expression_file),
        str(processed_gene_file),
        str(sample_file),
        output_prefix
    ]
    
    print(f"  命令: {' '.join(cmd)}")
    
    # 执行
    start_time = time.time()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(Path(".")),  # 从项目根目录运行
        timeout=600
    )
    
    elapsed = time.time() - start_time
    
    # 收集输出文件
    output_files = []
    for ext in ['.pdf', '.txt']:
        # 也搜索项目根目录（R脚本可能输出到那里）
        files = list(output_dir.glob(f"{output_prefix}*{ext}"))
        root_files = list(Path(".").glob(f"{output_prefix}*{ext}"))
        for f in root_files:
            if str(f) not in files:
                files.append(f)
        output_files.extend([str(f) for f in files])
    
    # 转换PDF为PNG
    print(f"  转换PDF为PNG...")
    # 收集所有PDF文件（从output_dir和根目录）
    pdf_files = list(output_dir.glob(f"{output_prefix}*.pdf"))
    root_pdf_files = list(Path(".").glob(f"{output_prefix}*.pdf"))
    for f in root_pdf_files:
        if str(f) not in [str(p) for p in pdf_files]:
            pdf_files.append(f)
    
    # 如果PDF在根目录，复制到output_dir
    for pdf_file in pdf_files:
        if pdf_file.parent != output_dir:
            import shutil
            dest = output_dir / pdf_file.name
            shutil.copy(pdf_file, dest)
            print(f"  复制文件到output_dir: {pdf_file.name}")
            pdf_files = [dest if str(f) == str(pdf_file) else f for f in pdf_files]
    
    for pdf_file in pdf_files:
        png_file = pdf_file.with_suffix('.png')
        try:
            convert_cmd = ["pdftoppm", "-png", "-singlefile", "-r", "150", str(pdf_file), str(png_file.with_suffix(''))]
            conv_result = subprocess.run(convert_cmd, capture_output=True, text=True, timeout=60)
            if conv_result.returncode == 0 and png_file.exists():
                output_files.append(str(png_file))
                print(f"    转换成功: {png_file.name}")
            else:
                print(f"    转换失败: {pdf_file.name}")
        except Exception as e:
            print(f"    转换异常: {e}")
    
    print(f"  生成 {len(output_files)} 个输出文件")
    for f in output_files[:10]:
        print(f"    - {Path(f).name}")
    
    return {
        "success": result.returncode == 0,
        "command": " ".join(cmd),
        "returncode": result.returncode,
        "stdout": result.stdout[-500:] if result.stdout else "",
        "stderr": result.stderr[-500:] if result.stderr else "",
        "output_files": output_files,
        "elapsed_sec": elapsed
    }


def write_report(
    workspace: Path,
    prefix: str,
    metrics: Dict[str, Any],
    parameters: Dict[str, Any],
    tool_versions: Dict[str, str],
    analysis_result: Dict[str, Any],
    artifacts: Dict[str, Dict]
) -> Path:
    """写入人可读的分析报告"""
    report_path = workspace / "final" / f"{prefix}.expression_report.md"
    
    print(f"生成分析报告: {report_path}")
    
    with open(report_path, "w") as f:
        f.write(f"# Expression分析报告 - {prefix}\n\n")
        f.write(f"**生成时间**: {datetime.now().isoformat()}\n\n")
        
        f.write("## 1. 分析概述\n")
        f.write(f"- **分析阶段**: Expression\n")
        f.write(f"- **分析状态**: {'成功' if analysis_result.get('success', False) else '失败'}\n")
        f.write(f"- **基因数量**: {metrics.get('gene_count', 'N/A')}\n")
        f.write(f"- **输出文件数**: {metrics.get('total_output_files', 'N/A')}\n")
        f.write(f"- **分析耗时**: {metrics.get('elapsed_sec', 0):.1f} 秒\n\n")
        
        f.write("## 2. 工具版本\n")
        for tool, version in tool_versions.items():
            f.write(f"- **{tool}**: {version}\n")
        f.write("\n")
        
        f.write("## 3. 执行参数\n")
        for param, value in parameters.items():
            f.write(f"- **{param}**: {value}\n")
        f.write("\n")
        
        f.write("## 4. 执行命令\n")
        f.write(f"```bash\n{analysis_result.get('command', 'N/A')}\n```\n\n")
        
        if analysis_result.get('returncode', -1) != 0:
            f.write(f"**返回码**: {analysis_result.get('returncode', 'N/A')}\n")
            if analysis_result.get('stderr'):
                f.write(f"**错误信息**:\n```\n{analysis_result['stderr'][:500]}\n```\n\n")
        
        f.write("## 5. 产出文件\n")
        for artifact_name, artifact_info in artifacts.items():
            if isinstance(artifact_info, dict):
                f.write(f"- **{artifact_name}**: {artifact_info.get('path', 'N/A')}\n")
                if artifact_info.get('size'):
                    f.write(f"  - 大小: {artifact_info['size']} 字节\n")
                if artifact_info.get('sha256'):
                    f.write(f"  - SHA256: {artifact_info['sha256'][:16]}...\n")
        f.write("\n")
        
        f.write("## 6. 详细统计\n")
        f.write("```json\n")
        f.write(json.dumps(metrics, indent=2, ensure_ascii=False))
        f.write("\n```\n")
    
    return report_path


def write_manifest(
    workspace: Path,
    job_id: str,
    run_id: str,
    prefix: str,
    start_time: datetime,
    end_time: datetime,
    artifacts: Dict[str, Dict],
    metrics: Dict[str, Any],
    parameters: Dict[str, Any],
    tool_versions: Dict[str, str],
    cmd_history: List[str],
    identify_inputs: List[Dict],
    analysis_result: Dict[str, Any] = None,
    status: str = "success",
    stage_context: Any = None,
) -> Path:
    """
    写入符合schema v1的manifest文件
    
    注意：artifacts必须是字典格式，不是列表
    新增：method_summary, comparative_summary, biological_notes（模板标准）
    """
    elapsed_sec = (end_time - start_time).total_seconds()
    
    # === 构建method_summary ===
    method_summary = {
        "data_type": "FPKM/TPM expression matrix",
        "visualization": {
            "heatmap": True,  # R脚本会生成
            "correlation": True,
            "grouped_barplot": True,
            "tissue_specificity": True  # 新增的组织特异性分析
        },
        "statistics": {
            "correlation_method": "Pearson",
            "tissue_specificity_index": "Tau index",
            "clustering": "hierarchical"
        },
        "tool": "R (pheatmap/ggplot2/tidyverse)"
    }
    
    # === 构建comparative_summary ===
    gene_count = metrics.get("gene_count", 0)
    
    # 尝试读取tissue_specificity.txt获取组织特异性统计
    tissue_spec_summary = {}
    final_dir = workspace / "final"
    tissue_spec_file = final_dir / f"{prefix}.tissue_specificity.txt"
    
    if tissue_spec_file.exists():
        try:
            import csv
            specificity_categories = {}
            max_groups = {}
            with open(tissue_spec_file, 'r') as f:
                reader = csv.DictReader(f, delimiter='\t')
                for row in reader:
                    category = row.get('specificity_category', 'Unknown')
                    specificity_categories[category] = specificity_categories.get(category, 0) + 1
                    max_group = row.get('max_group', 'Unknown')
                    max_groups[max_group] = max_groups.get(max_group, 0) + 1
            
            tissue_spec_summary = {
                "specificity_distribution": specificity_categories,
                "max_group_distribution": max_groups,
                "specific_genes": specificity_categories.get('Specific', 0),
                "broad_genes": specificity_categories.get('Broad', 0)
            }
        except Exception as e:
            print(f"Warning: Could not parse tissue_specificity.txt: {e}")
    
    # 提取物种信息
    scoped_targets = parameters.get("target_species") if isinstance(parameters, dict) else None
    if isinstance(scoped_targets, list) and len(scoped_targets) == 1:
        target_species = str(scoped_targets[0])
    else:
        target_species = prefix.split('.')[0] if '.' in prefix else prefix.split('_')[0]
    
    comparative_summary = {
        "total_genes": gene_count,
        "target_species": target_species,
        "data_source": parameters.get("expression_file", "unknown"),
        "groups": "multiple" if artifacts.get("gene_exp_hist_png") else "single",
        "tissue_specificity": tissue_spec_summary,
        "observation": f"Expression analysis for {gene_count} gene family members" if gene_count else "Expression analysis completed"
    }
    
    manifest_data = {
        # 基础标识字段（必需）
        "manifest_version": "1.0",
        "job_id": job_id,
        "stage": "expression",
        "run_id": run_id,
        "prefix": prefix,
        
        # 状态和时间字段（必需）
        "timestamp": end_time.isoformat(),
        "status": status,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "elapsed_sec": round(elapsed_sec, 2),
        
        # 执行信息（必需）
        "cmd": cmd_history,
        "tool_versions": tool_versions,
        
        # 目录路径（必需）
        "workspace_root": abs_text(workspace.parent),
        "workspace": abs_text(workspace),
        "input_dir": abs_text(workspace.parent.parent / "input" / job_id),
        
        # 输入文件（必需）
        "inputs": identify_inputs,
        
        # 输出产物（必需）- 必须是字典格式
        "artifacts": artifacts,
        
        # 参数和指标（必需）
        "parameters": parameters,
        "metrics": metrics,
        
        # 溯源信息（可选但推荐）
        "provenance": {
            "derived_from": f"{prefix}.identify.manifest.json",
            "stage_sequence": ["identify", "expression"]
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

    for key in (
        "scope_id",
        "scope_mode",
        "scope_file",
        "target_species",
        "reference_species",
        "comparative_species",
        "selected_species",
        "family",
        "analysis_route",
        "member_set_id",
    ):
        if key in parameters:
            manifest_data[key] = parameters[key]
    if stage_context is not None:
        manifest_data = apply_stage_context(manifest_data, stage_context)
    
    manifest_path = workspace / "final" / f"{prefix}.expression.manifest.json"
    
    print(f"生成Manifest文件: {manifest_path}")
    
    # 确保JSON严格性：无注释，确保ASCII
    with open(manifest_path, "w", encoding='utf-8') as f:
        json.dump(manifest_data, f, indent=2, ensure_ascii=False)

    if parameters.get("scope_id"):
        scoped_manifest = workspace / "final" / f"{prefix}.{sanitize_token(str(parameters['scope_id']))}.expression.manifest.json"
        manifest_data["scope_manifest"] = abs_text(scoped_manifest)
        with open(scoped_manifest, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2, ensure_ascii=False)
    
    return manifest_path


def main():
    parser = argparse.ArgumentParser(
        description="Expression阶段：基因表达量分析与可视化",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python run_expression.py --prefix Ft.SPL --workspace /path/to/workspace \\
    --expression-file expression_matrix.txt --gene-id-file gene_ids.txt \\
    --sample-file sample_info.txt

输入要求：
  - Identify阶段必须已成功运行
  - final/{prefix}.final_members.id 必须存在

输出产物：
  - final/{prefix}.expression.manifest.json (机器清单)
  - final/{prefix}.expression_report.md (分析报告)
  - final/{prefix}*.pdf (表达量图 PDF)
  - final/{prefix}*.png (表达量图 PNG)
        """
    )
    
    parser.add_argument("--prefix", required=True, 
                       help="分析前缀，格式：物种.家族（如 Ft.SPL）")
    parser.add_argument("--workspace", required=True,
                       help="工作空间目录（workspace/<job_id>/)")
    parser.add_argument("--mode", default="legacy", choices=["legacy", "rnaseq_auto"],
                       help="运行模式：legacy=用户提供表达矩阵+gene list；rnaseq_auto=RNA-seq DE后自动绘图")
    parser.add_argument("--expression-file", required=True,
                       help="表达量矩阵文件路径；rnaseq_auto模式下为FPKM矩阵")
    parser.add_argument("--gene-id-file", default="",
                       help="基因ID列表文件路径；rnaseq_auto模式下可选，用于family分支")
    parser.add_argument("--sample-file", required=True,
                       help="样本信息文件路径；rnaseq_auto模式下为sample_table.tsv")
    parser.add_argument("--tpm-file", default="", help="rnaseq_auto模式使用的TPM矩阵")
    parser.add_argument("--vst-file", default="", help="rnaseq_auto模式使用的VST矩阵")
    parser.add_argument("--count-matrix", default="", help="rnaseq_auto模式可选count矩阵，用于normalized count/time-course图")
    parser.add_argument("--de-dir", default="", help="rnaseq_auto模式使用的DE结果目录")
    parser.add_argument("--family-label", default="", help="rnaseq_auto family分支标签")
    parser.add_argument("--triggered-by", default="manual", help="自动触发来源阶段")
    parser.add_argument("--id-transform", default="none",
                       choices=["none", "remove_T01", "add_T01"],
                       help="ID转换方式（默认：none）")
    parser.add_argument("--scope-file",
                       help="target/reference/comparative scope JSON；Expression只允许单个target物种")
    parser.add_argument("--analysis-route", default="",
                       help="gene-family stage analysis route, e.g. single_species")
    parser.add_argument("--member-set-id", default="",
                       help="gene-family member set identity for scoped status validation")
    parser.add_argument("--log-file",
                       help="日志文件路径（默认：workspace/logs/{timestamp}_expression.log）")
    
    args = parser.parse_args()
    
    # 记录开始时间
    start_time = datetime.now()
    run_id = f"expression_{start_time.strftime('%Y%m%d_%H%M%S_%f')[:-3]}"
    
    # 设置工作空间
    workspace = Path(args.workspace)
    job_id = workspace.name  # workspace/<job_id>中的job_id
    
    print("=" * 60)
    print(f"🚀 启动Expression分析")
    print("=" * 60)
    print(f"分析前缀: {args.prefix}")
    print(f"工作空间: {workspace}")
    print(f"任务ID: {job_id}")
    print(f"运行ID: {run_id}")

    if args.mode == "rnaseq_auto":
        try:
            manifest_path = run_rnaseq_auto_expression(
                workspace=workspace,
                prefix=args.prefix,
                job_id=job_id,
                run_id=run_id,
                fpkm_file=Path(args.expression_file),
                tpm_file=Path(args.tpm_file),
                vst_file=Path(args.vst_file),
                sample_table=Path(args.sample_file),
                de_dir=Path(args.de_dir),
                count_matrix=Path(args.count_matrix) if args.count_matrix else None,
                family_members=Path(args.gene_id_file) if args.gene_id_file else None,
                family_label=args.family_label,
                triggered_by=args.triggered_by,
            )
            print(f"✅ RNA-seq Expression自动绘图完成: {manifest_path}")
            sys.exit(0)
        except Exception as exc:
            print(f"❌ RNA-seq Expression自动绘图失败: {exc}")
            import traceback
            traceback.print_exc()
            sys.exit(2)
    
    stage_context = None

    try:
        scope_file = Path(args.scope_file) if args.scope_file else None
        scope_data = None
        analysis_route = str(args.analysis_route or "").strip()
        member_set_id = str(args.member_set_id or "").strip()
        if scope_file:
            if not scope_file.exists():
                raise FileNotFoundError(f"Scope file not found: {scope_file}")
            scope_data = read_scope(scope_file)
            target_species = scope_data.get("target_species", [])
            if not analysis_route:
                analysis_route = infer_analysis_route("expression", scope_data)
            if not member_set_id and analysis_route and analysis_route != "unscoped" and scope_data.get("scope_id"):
                member_set_id = member_set_id_for(args.prefix, scope_data, analysis_route)
            stage_context = build_stage_context(
                stage="expression",
                prefix=args.prefix,
                scope_data=scope_data,
                scope_file=scope_file,
                analysis_route=analysis_route,
                member_set_id=member_set_id,
            )
            if len(target_species) != 1:
                raise ValueError(
                    f"Expression 阶段一次只能分析一个目标物种；当前 scope target_species={target_species}。"
                    "请在弹窗中只选择一个 target，或分别运行每个目标物种。"
                )

        if not args.gene_id_file:
            raise ValueError("legacy expression mode requires --gene-id-file")

        # 1. 准备目录结构
        print("\n📁 准备目录结构...")
        paths = prepare_directories(workspace)
        
        # 2. 获取工具版本
        print("\n🔧 检查工具版本...")
        tool_versions = get_tool_versions()
        print(f"   R: {tool_versions.get('R', 'unknown')}")
        print(f"   pdftoppm: {tool_versions.get('pdftoppm', 'unknown')}")
        print(f"   python: {tool_versions.get('python', 'unknown')}")
        
        # 3. 验证Identify输出
        print("\n🔍 验证Identify阶段输出...")
        identify_info = validate_previous_stage(
            workspace,
            args.prefix,
            gene_id_override=Path(args.gene_id_file) if scope_data else None,
        )
        identify_inputs = identify_info.get("identify_inputs", [])
        gene_count = identify_info.get("gene_count", 0)
        
        # 4. 运行表达量分析
        print("\n🔄 运行表达量分析...")
        result = run_expression_analysis(
            expression_file=Path(args.expression_file),
            gene_id_file=Path(args.gene_id_file),
            sample_file=Path(args.sample_file),
            output_prefix=f"{args.prefix}.expression",
            output_dir=paths["final_dir"],
            id_transform=args.id_transform
        )
        
        # 5. 确定分析状态
        status = "success" if result.get("success", False) else "failed"
        
        # 6. 准备artifacts信息（字典格式）
        artifacts = {}
        for f_path in result.get("output_files", []):
            f = Path(f_path)
            if f.exists():
                artifact_name = f.stem.replace(f"{args.prefix}.expression.", "").replace(".", "_")
                
                # 确定artifact类型
                if f.suffix == ".pdf":
                    artifact_type = "plot"
                    format_type = "pdf"
                elif f.suffix == ".png":
                    artifact_type = "image"
                    format_type = "png"
                elif f.suffix == ".txt":
                    artifact_type = "table"
                    format_type = "txt"
                else:
                    artifact_type = "file"
                    format_type = f.suffix.lstrip('.')
                
                artifacts[artifact_name] = {
                    "artifact": artifact_name,
                    "path": f"final/{f.name}",
                    "full_path": str(f.absolute()),
                    "type": artifact_type,
                    "format": format_type,
                    "size": f.stat().st_size,
                    "sha256": compute_file_hash(f)
                }
        
        # 7. 准备metrics
        metrics = {
            "gene_count": gene_count,
            "total_output_files": len(result.get("output_files", [])),
            "total_artifacts": len(artifacts),
            "elapsed_sec": result.get("elapsed_sec", 0)
        }
        
        # 8. 准备parameters
        parameters = {
            "prefix": args.prefix,
            "id_transform": args.id_transform,
            "expression_file": str(args.expression_file),
            "gene_id_file": str(args.gene_id_file),
            "sample_file": str(args.sample_file),
            "stage": "expression",
            "job_id": job_id,
            "run_id": run_id,
            "manifest_version": "1.0",
            "status": status
        }
        parameters.update(scope_parameters(scope_file, scope_data, analysis_route, member_set_id))
        
        # 9. 记录命令历史
        cmd_history = [result.get("command", "")]
        
        # 10. 写入报告
        print("\n📝 生成分析报告...")
        report_path = write_report(
            workspace=workspace,
            prefix=args.prefix,
            metrics=metrics,
            parameters=parameters,
            tool_versions=tool_versions,
            analysis_result=result,
            artifacts=artifacts
        )
        
        # 添加报告到artifacts
        if report_path.exists():
            artifacts["report"] = {
                "artifact": "report",
                "path": f"final/{args.prefix}.expression_report.md",
                "full_path": str(report_path.absolute()),
                "type": "markdown",
                "format": "md",
                "size": report_path.stat().st_size,
                "sha256": compute_file_hash(report_path)
            }
        
        # 11. 写入manifest
        print("\n📋 生成Manifest文件...")
        end_time = datetime.now()
        manifest_path = write_manifest(
            workspace=workspace,
            job_id=job_id,
            run_id=run_id,
            prefix=args.prefix,
            start_time=start_time,
            end_time=end_time,
            artifacts=artifacts,
            metrics=metrics,
            parameters=parameters,
            tool_versions=tool_versions,
            cmd_history=cmd_history,
            identify_inputs=identify_inputs,
            analysis_result=result,  # 新增：传递分析结果
            status=status,
            stage_context=stage_context,
        )
        
        # 添加manifest自身到artifacts
        if manifest_path.exists():
            artifacts["manifest"] = {
                "artifact": "manifest",
                "path": f"final/{args.prefix}.expression.manifest.json",
                "full_path": str(manifest_path.absolute()),
                "type": "json",
                "format": "json",
                "size": manifest_path.stat().st_size,
                "sha256": compute_file_hash(manifest_path)
            }
        
        # 12. 更新manifest包含完整的artifacts
        if manifest_path.exists():
            with open(manifest_path, 'r') as f:
                manifest_data = json.load(f)
            manifest_data["artifacts"] = artifacts
            with open(manifest_path, 'w') as f:
                json.dump(manifest_data, f, indent=2, ensure_ascii=False)
            if parameters.get("scope_id"):
                scoped_manifest = workspace / "final" / f"{args.prefix}.{sanitize_token(str(parameters['scope_id']))}.expression.manifest.json"
                manifest_data["scope_manifest"] = abs_text(scoped_manifest)
                with open(scoped_manifest, "w", encoding="utf-8") as f:
                    json.dump(manifest_data, f, indent=2, ensure_ascii=False)
        
        # 13. 输出成功信息
        elapsed = (end_time - start_time).total_seconds()
        print("\n" + "=" * 60)
        if status == "success":
            print(f"✅ Expression分析完成!")
        else:
            print(f"❌ Expression分析失败!")
        print(f"   总耗时: {elapsed:.1f} 秒")
        print(f"   状态: {status}")
        print(f"   Manifest: {manifest_path}")
        print(f"   报告: {report_path}")
        print(f"   输出文件数: {len(result.get('output_files', []))}")
        print("=" * 60)
        
        # 根据状态返回退出码
        if status == "success":
            sys.exit(0)
        else:
            sys.exit(1)
            
    except Exception as e:
        # 记录结束时间
        end_time = datetime.now()
        
        print(f"\n❌ Expression分析失败: {e}")
        import traceback
        traceback.print_exc()
        
        try:
            # 创建基本的artifacts（可能部分成功）
            artifacts = {}
            
            # 尝试写入失败manifest
            manifest_path = write_manifest(
                workspace=workspace,
                job_id=job_id,
                run_id=run_id,
                prefix=args.prefix,
                start_time=start_time,
                end_time=end_time,
                artifacts=artifacts,
                metrics={},
                parameters={
                    "prefix": args.prefix,
                    "error": str(e)[:200]
                },
                tool_versions=tool_versions if 'tool_versions' in locals() else {},
                cmd_history=[],
                identify_inputs=identify_inputs if 'identify_inputs' in locals() else [],
                analysis_result=None,  # 新增：失败时为None
                status="failed",
                stage_context=stage_context,
            )
            
            print(f"   失败Manifest已写入: {manifest_path}")
            
        except Exception as manifest_error:
            print(f"   写入失败Manifest时出错: {manifest_error}")
        
        sys.exit(2)


if __name__ == "__main__":
    main()
