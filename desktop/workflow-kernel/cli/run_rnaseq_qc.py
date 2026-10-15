#!/usr/bin/env python3
"""
RNA-Seq QC阶段：对原始测序数据进行质量控制

输入：
  - 原始FASTQ文件（双端）
  - 样本配置文件（group_sample.yaml）

输出：
  - FastQC报告
  - Cutadapt处理后的clean reads
  - 质控报告和manifest
"""

import argparse
import json
import subprocess
import sys
import time
import yaml
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
import hashlib
import os

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gfa_core.rnaseq_context import apply_rnaseq_context, build_rnaseq_context, resolve_rnaseq_project_context
from cli.rnaseq_env import prepend_path, rnaseq_bin


RNASEQ_BIN = str(rnaseq_bin())
prepend_path(RNASEQ_BIN)

def validate_input_files(input_dir: Path, config: Dict[str, Any]) -> Dict[str, Dict[str, Path]]:
    """
    严格验证输入文件：检查R1+R2配对、文件存在、文件大小>0
    
    Returns:
        验证通过的样本字典 {sample_name: {"r1": Path, "r2": Path}}
    
    Raises:
        SystemExit: 验证失败时退出并返回错误信息
    """
    samples = config.get("samples", {})
    if not samples:
        print("❌ ERROR: No samples defined in configuration file", file=sys.stderr)
        sys.exit(1)
    
    validated_samples = {}
    errors = []
    
    for sample_name, paths in samples.items():
        # 解析路径
        if isinstance(paths, list):
            if len(paths) < 2:
                errors.append(f"Sample '{sample_name}': insufficient paths (need 2, got {len(paths)})")
                continue
            r1_path, r2_path = paths[0], paths[1]
        elif isinstance(paths, dict):
            r1_path = paths.get("r1") or paths.get("read1")
            r2_path = paths.get("r2") or paths.get("read2")
            if not r1_path or not r2_path:
                errors.append(f"Sample '{sample_name}': missing r1/r2 paths")
                continue
        else:
            errors.append(f"Sample '{sample_name}': unknown format {type(paths)}")
            continue
        
        # 解析完整路径
        r1 = Path(r1_path) if Path(r1_path).is_absolute() else input_dir / r1_path
        r2 = Path(r2_path) if Path(r2_path).is_absolute() else input_dir / r2_path
        
        # 验证文件存在
        if not r1.exists():
            errors.append(f"Sample '{sample_name}': R1 file not found: {r1}")
            continue
        if not r2.exists():
            errors.append(f"Sample '{sample_name}': R2 file not found: {r2}")
            continue
        
        # 验证文件大小>0
        r1_size = r1.stat().st_size
        r2_size = r2.stat().st_size
        if r1_size == 0:
            errors.append(f"Sample '{sample_name}': R1 file is empty: {r1}")
            continue
        if r2_size == 0:
            errors.append(f"Sample '{sample_name}': R2 file is empty: {r2}")
            continue
        
        # 验证R1和R2大小合理（差距不超过10倍）
        if r1_size > 0 and r2_size > 0:
            ratio = max(r1_size, r2_size) / min(r1_size, r2_size)
            if ratio > 10:
                errors.append(f"Sample '{sample_name}': R1/R2 file size mismatch (ratio: {ratio:.1f})")
                continue
        
        validated_samples[sample_name] = {"r1": r1, "r2": r2, "r1_size": r1_size, "r2_size": r2_size}
    
    # 报告错误并退出
    if errors:
        print(f"\n❌ ERROR: Input validation failed for {len(errors)} sample(s):\n", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        print(f"\nTotal samples in config: {len(samples)}", file=sys.stderr)
        print(f"Valid samples: {len(validated_samples)}", file=sys.stderr)
        sys.exit(1)
    
    print(f"✅ Input validation passed: {len(validated_samples)} sample(s) ready")
    for name, info in validated_samples.items():
        print(f"   - {name}: R1={info['r1_size']/1024/1024:.1f}MB, R2={info['r2_size']/1024/1024:.1f}MB")
    
    return validated_samples


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


def fastp_version(fastp_bin: Optional[Path] = None) -> str:
    if not fastp_bin:
        return "not_configured"
    try:
        result = subprocess.run(
            [str(fastp_bin), "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        output = result.stdout.strip() or result.stderr.strip()
        return output.split("\n")[0] if output else "unknown"
    except Exception:
        return "not_found"


def resolve_fastp_bin(fastp_bin_arg: str = "") -> Optional[Path]:
    candidates = []
    if fastp_bin_arg:
        candidates.append(Path(fastp_bin_arg))
    env_fastp = os.environ.get("FASTP_BIN", "").strip()
    if env_fastp:
        candidates.append(Path(env_fastp))
    path_fastp = shutil.which("fastp")
    if path_fastp:
        candidates.append(Path(path_fastp))
    for candidate in candidates:
        if candidate.exists() and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def get_tool_versions(fastp_bin: Optional[Path] = None) -> Dict[str, str]:
    """获取所需工具版本信息"""
    versions = {}
    
    # fastqc
    try:
        result = subprocess.run(
            [f"{RNASEQ_BIN}/fastqc", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        output = result.stdout.strip() or result.stderr.strip()
        versions["fastqc"] = output.split('\n')[0] if output else "unknown"
    except Exception:
        versions["fastqc"] = "not_found"
    
    # cutadapt
    try:
        result = subprocess.run(
            [f"{RNASEQ_BIN}/cutadapt", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5
        )
        output = result.stdout.strip() or result.stderr.strip()
        versions["cutadapt"] = output.split('\n')[0] if output else "unknown"
    except Exception:
        versions["cutadapt"] = "not_found"

    # MultiQC
    try:
        result = subprocess.run(
            [f"{RNASEQ_BIN}/multiqc", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10
        )
        output = result.stdout.strip() or result.stderr.strip()
        versions["multiqc"] = output.split('\n')[0] if output else "unknown"
    except Exception:
        versions["multiqc"] = "not_found"

    versions["fastp"] = fastp_version(fastp_bin)
    
    return versions


def load_samples_config(config_file: Path) -> Dict[str, Any]:
    """加载样本配置文件（支持合并格式：samples + groups + compare）"""
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)
    
    # 确保有 samples 字段
    if "samples" not in config:
        raise ValueError(f"Config file {config_file} must contain 'samples' field")
    
    return config


RNASEQ_BIN = str(rnaseq_bin())
prepend_path(RNASEQ_BIN)


def resolve_thread_plan(cpu: int, sample_parallel_jobs: int = 1, threads_per_sample: Optional[int] = None) -> Dict[str, int]:
    """Resolve conservative RNA-seq sample/thread settings from CLI args."""
    total_cpu = max(1, int(cpu or 1))
    jobs = max(1, int(sample_parallel_jobs or 1))
    if threads_per_sample is None or int(threads_per_sample) < 1:
        threads = max(1, total_cpu // jobs)
    else:
        threads = max(1, int(threads_per_sample))
    if jobs * threads > total_cpu:
        threads = max(1, total_cpu // jobs)
    return {"cpu": total_cpu, "sample_parallel_jobs": jobs, "threads_per_sample": threads}



def fastqc_output_base(fastq_path: Path) -> str:
    """Return FastQC output basename without _fastqc suffix for .fastq/.fq(.gz)."""
    name = fastq_path.name
    for suffix in (".fastq.gz", ".fq.gz", ".fastq", ".fq"):
        if name.lower().endswith(suffix):
            return name[:-len(suffix)]
    return fastq_path.stem


def sample_qc_outputs_complete(sample_name: str, file_info: Dict[str, Path], analysis_dir: Path, qc_tool: str = "fastp") -> bool:
    r1 = file_info["r1"]
    r2 = file_info["r2"]
    if qc_tool == "fastp":
        required = [
            analysis_dir / f"{sample_name}_1.clean.fastq.gz",
            analysis_dir / f"{sample_name}_2.clean.fastq.gz",
            analysis_dir / f"{sample_name}_fastp.html",
            analysis_dir / f"{sample_name}_fastp.json",
            analysis_dir / f"{sample_name}_fastp.log",
        ]
    else:
        required = [
            analysis_dir / f"{fastqc_output_base(r1)}_fastqc.html",
            analysis_dir / f"{fastqc_output_base(r1)}_fastqc.zip",
            analysis_dir / f"{fastqc_output_base(r2)}_fastqc.html",
            analysis_dir / f"{fastqc_output_base(r2)}_fastqc.zip",
            analysis_dir / f"{sample_name}_1.clean.fastq.gz",
            analysis_dir / f"{sample_name}_2.clean.fastq.gz",
            analysis_dir / f"{sample_name}_cutadapt.log",
        ]
    return all(path.exists() and path.stat().st_size > 0 for path in required)

def run_fastqc(fastq_files: List[Path], output_dir: Path, cpu: int = 4) -> Dict[str, Any]:
    """
    运行FastQC质控
    
    Returns:
        Dict with success status and timing info
    """
    results = {"success": True, "files": {}, "total_time_sec": 0}
    fastqc_path = f"{RNASEQ_BIN}/fastqc"
    
    for fq in fastq_files:
        cmd = [fastqc_path, "-o", str(output_dir), "-t", str(cpu), str(fq)]
        file_size_mb = fq.stat().st_size / 1024 / 1024
        
        print(f"   [FastQC] Starting: {fq.name} ({file_size_mb:.1f} MB)...", flush=True)
        start_ts = time.time()
        
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            elapsed = time.time() - start_ts
            results["files"][fq.name] = {"success": True, "time_sec": elapsed}
            results["total_time_sec"] += elapsed
            print(f"   [FastQC] Completed: {fq.name} in {elapsed:.1f}s", flush=True)
        except subprocess.CalledProcessError as e:
            elapsed = time.time() - start_ts
            results["files"][fq.name] = {"success": False, "time_sec": elapsed, "error": str(e)}
            results["success"] = False
            print(f"   [FastQC] FAILED: {fq.name} after {elapsed:.1f}s: {e.stderr}", file=sys.stderr)
            raise RuntimeError(f"FastQC failed for {fq.name}: {e.stderr}")
    
    return results


def run_cutadapt(
    r1: Path, 
    r2: Path, 
    out_r1: Path, 
    out_r2: Path, 
    log_file: Path,
    adapter_r1: str = "AGATCGGAAGAGC",
    adapter_r2: str = "AGATCGGAAGAGC",
    quality_cutoff: int = 20,
    min_length: int = 50,
    cpu: int = 1
) -> Dict[str, Any]:
    """
    运行Cutadapt去除接头
    
    Returns:
        Dict with success status, timing info, and stats
    """
    cmd = [
        f"{RNASEQ_BIN}/cutadapt",
        "-a", adapter_r1,
        "-A", adapter_r2,
        "-o", str(out_r1),
        "-p", str(out_r2),
        "-q", str(quality_cutoff),
        "-m", str(min_length),
        "-j", str(max(1, int(cpu or 1))),
        str(r1),
        str(r2)
    ]
    
    r1_size_mb = r1.stat().st_size / 1024 / 1024
    r2_size_mb = r2.stat().st_size / 1024 / 1024
    
    print(f"   [Cutadapt] Starting: {r1.name} + {r2.name} ({r1_size_mb+r2_size_mb:.1f} MB total)...", flush=True)
    start_ts = time.time()
    
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        elapsed = time.time() - start_ts
        
        with open(log_file, 'w') as f:
            f.write(result.stdout)
        
        # 验证输出文件
        if not out_r1.exists() or out_r1.stat().st_size == 0:
            raise RuntimeError(f"Cutadapt output R1 is empty or missing: {out_r1}")
        if not out_r2.exists() or out_r2.stat().st_size == 0:
            raise RuntimeError(f"Cutadapt output R2 is empty or missing: {out_r2}")
        
        out_size_mb = (out_r1.stat().st_size + out_r2.stat().st_size) / 1024 / 1024
        print(f"   [Cutadapt] Completed in {elapsed:.1f}s (output: {out_size_mb:.1f} MB)", flush=True)
        
        return {"success": True, "time_sec": elapsed, "log": result.stdout}
        
    except subprocess.CalledProcessError as e:
        elapsed = time.time() - start_ts
        with open(log_file, 'w') as f:
            f.write(e.stdout + "\n" + e.stderr)
        print(f"   [Cutadapt] FAILED after {elapsed:.1f}s: {e.stderr}", file=sys.stderr)
        raise RuntimeError(f"Cutadapt failed: {e.stderr}")
    
    except Exception as e:
        elapsed = time.time() - start_ts
        print(f"   [Cutadapt] FAILED after {elapsed:.1f}s: {str(e)}", file=sys.stderr)
        raise


def run_fastp(
    fastp_bin: Path,
    r1: Path,
    r2: Path,
    out_r1: Path,
    out_r2: Path,
    html_report: Path,
    json_report: Path,
    log_file: Path,
    cpu: int = 1,
    detect_adapter_for_pe: bool = True,
    cut_front: bool = True,
    cut_tail: bool = True,
    cut_window_size: int = 4,
    cut_mean_quality: int = 20,
    qualified_quality_phred: int = 20,
    unqualified_percent_limit: int = 30,
    n_base_limit: int = 5,
    length_required: int = 30,
) -> Dict[str, Any]:
    cmd = [
        str(fastp_bin),
        "-i", str(r1),
        "-I", str(r2),
        "-o", str(out_r1),
        "-O", str(out_r2),
        "--html", str(html_report),
        "--json", str(json_report),
        "--thread", str(max(1, int(cpu or 1))),
        "--cut_window_size", str(cut_window_size),
        "--cut_mean_quality", str(cut_mean_quality),
        "--qualified_quality_phred", str(qualified_quality_phred),
        "--unqualified_percent_limit", str(unqualified_percent_limit),
        "--n_base_limit", str(n_base_limit),
        "--length_required", str(length_required),
    ]
    if detect_adapter_for_pe:
        cmd.append("--detect_adapter_for_pe")
    if cut_front:
        cmd.append("--cut_front")
    if cut_tail:
        cmd.append("--cut_tail")

    print(f"   [fastp] Starting: {r1.name} + {r2.name}...", flush=True)
    start_ts = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    elapsed = time.time() - start_ts
    log_file.write_text(
        "Command: " + " ".join(cmd) + "\n\n"
        + "--- STDOUT ---\n"
        + result.stdout
        + "\n--- STDERR ---\n"
        + result.stderr,
        encoding="utf-8",
    )
    if result.returncode != 0:
        print(f"   [fastp] FAILED after {elapsed:.1f}s: {result.stderr}", file=sys.stderr)
        raise RuntimeError(f"fastp failed with exit code {result.returncode}: {result.stderr}")
    for path in (out_r1, out_r2, html_report, json_report):
        if not path.exists() or path.stat().st_size == 0:
            raise RuntimeError(f"fastp output is missing or empty: {path}")
    print(f"   [fastp] Completed in {elapsed:.1f}s", flush=True)
    return {"success": True, "time_sec": elapsed, "cmd": cmd, "log": str(log_file)}


def run_multiqc(search_paths: Sequence[Path], output_dir: Path, report_name: str) -> Optional[Path]:
    """Run MultiQC over selected QC outputs and return the report path."""
    multiqc = Path(f"{RNASEQ_BIN}/multiqc")
    existing = [str(path) for path in search_paths if path.exists()]
    if not multiqc.exists() or not existing:
        return None
    cmd = [str(multiqc), *existing, "-o", str(output_dir), "-n", report_name, "-f"]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        report = output_dir / report_name
        return report if report.exists() else None
    except subprocess.CalledProcessError as e:
        print(f"MultiQC failed: {e.stderr}", file=sys.stderr)
        return None


def get_sample_stats(sample_dir: Path, sample_name: str) -> Dict[str, Any]:
    """获取样本统计信息"""
    import re

    def first_count_after_colon(text: str) -> Optional[int]:
        value = text.split(":", 1)[1] if ":" in text else text
        match = re.search(r'([\d,]+)', value)
        if match:
            return int(match.group(1).replace(',', ''))
        return None

    stats = {
        "raw_reads": 0,
        "clean_reads": 0,
        "adapter_removed": 0,
        "quality_filtered": 0
    }
    fastp_json = sample_dir / f"{sample_name}_fastp.json"
    if fastp_json.exists():
        try:
            payload = json.loads(fastp_json.read_text(encoding="utf-8"))
            before = payload.get("summary", {}).get("before_filtering", {})
            after = payload.get("summary", {}).get("after_filtering", {})
            stats["raw_reads"] = int(before.get("total_reads", 0))
            stats["clean_reads"] = int(after.get("total_reads", 0))
            stats["fastp_q20_rate_before"] = before.get("q20_rate")
            stats["fastp_q30_rate_before"] = before.get("q30_rate")
            stats["fastp_q20_rate_after"] = after.get("q20_rate")
            stats["fastp_q30_rate_after"] = after.get("q30_rate")
            stats["read1_mean_length"] = after.get("read1_mean_length")
            stats["read2_mean_length"] = after.get("read2_mean_length")
            stats["gc_content_after"] = after.get("gc_content")
        except Exception as e:
            print(f"Warning: Could not read fastp JSON for {sample_name}: {e}")
    
    # 读取FastQC数据（从zip文件中提取）
    fastqc_zip = sample_dir / f"{sample_name}_1_fastqc.zip"
    if fastqc_zip.exists():
        try:
            import zipfile
            with zipfile.ZipFile(fastqc_zip, 'r') as zf:
                # 找到 fastqc_data.txt 文件
                data_file = None
                for name in zf.namelist():
                    if name.endswith('fastqc_data.txt'):
                        data_file = name
                        break
                if data_file:
                    with zf.open(data_file) as f:
                        for line in f:
                            line = line.decode('utf-8')
                            if line.startswith("Total Sequences"):
                                stats["raw_reads"] = int(line.split("\t")[1])
                            elif line.startswith("Sequence length"):
                                stats["read_length"] = line.split("\t")[1].strip()
        except Exception as e:
            print(f"Warning: Could not read FastQC data for {sample_name}: {e}")
    
    # 读取Cutadapt日志
    log_file = sample_dir / f"{sample_name}_cutadapt.log"
    if log_file.exists():
        with open(log_file, 'r') as f:
            for line in f:
                if "Total reads processed:" in line or "Total read pairs processed:" in line:
                    count = first_count_after_colon(line)
                    if count is not None:
                        stats["raw_reads"] = count
                elif "Reads with adapters:" in line or "Read 1 with adapter:" in line or "Read 2 with adapter:" in line:
                    count = first_count_after_colon(line)
                    if count is not None:
                        stats["adapter_removed"] += count
                elif "Pairs that were too short:" in line or "Reads that were too short:" in line:
                    count = first_count_after_colon(line)
                    if count is not None:
                        stats["quality_filtered"] += count
                elif "Reads written (passing filters):" in line or "Pairs written (passing filters):" in line:
                    count = first_count_after_colon(line)
                    if count is not None:
                        stats["clean_reads"] = count
    
    return stats


def generate_report(
    output_dir: Path,
    sample_stats: Dict[str, Dict[str, Any]],
    versions: Dict[str, str],
    start_time: datetime,
    end_time: datetime,
    prefix: str = "",
    qc_tool: str = "fastp",
) -> Path:
    """生成质控报告"""
    report_file = output_dir / f"{prefix}.rnaseq_qc_report.md" if prefix else output_dir / "rnaseq_qc_report.md"
    
    elapsed = (end_time - start_time).total_seconds()
    
    content = f"""# RNA-Seq Quality Control Report

## Summary

- **Analysis Date**: {end_time.strftime("%Y-%m-%d %H:%M:%S")}
- **Total Samples**: {len(sample_stats)}
- **Total Runtime**: {elapsed:.1f} seconds
- **QC Tool**: {qc_tool}

## Tool Versions

"""
    for tool, version in versions.items():
        content += f"- **{tool}**: {version}\n"
    
    content += "\n## Sample Statistics\n\n"
    content += "| Sample | Raw Reads | Clean Reads | Adapter Removed |\n"
    content += "|--------|-----------|-------------|-----------------|\n"
    
    for sample, stats in sample_stats.items():
        content += f"| {sample} | {stats.get('raw_reads', 'N/A')} | {stats.get('clean_reads', 'N/A')} | {stats.get('adapter_removed', 'N/A')} |\n"
    
    with open(report_file, 'w') as f:
        f.write(content)
    
    return report_file


def generate_manifest(
    output_dir: Path,
    sample_stats: Dict[str, Dict[str, Any]],
    versions: Dict[str, str],
    start_time: datetime,
    end_time: datetime,
    cmd: List[str],
    input_dir: Path,
    workspace_dir: Path,
    job_id: str,
    run_id: str,
    prefix: str = "",
    analysis_dir: Path = None,  # analysis 目录路径
    cpu: int = 4,
    sample_parallel_jobs: int = 1,
    threads_per_sample: int = 4,
    sample_config: Optional[Path] = None,
    qc_tool: str = "fastp",
    fastp_bin: str = "",
    fastp_params: Optional[Dict[str, Any]] = None,
) -> Path:
    """生成manifest文件"""
    manifest_file = output_dir / f"{prefix}.rnaseq_qc.manifest.json" if prefix else output_dir / "rnaseq_qc.manifest.json"
    
    total_raw = sum(s.get('raw_reads', 0) for s in sample_stats.values())
    total_clean = sum(s.get('clean_reads', 0) for s in sample_stats.values())
    
    manifest = {
        "manifest_version": "1.0",
        "stage": "rnaseq_qc",
        "status": "success",
        "prefix": prefix,
        "job_id": job_id,
        "run_id": run_id,
        "timestamp": end_time.isoformat(),
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "elapsed_sec": (end_time - start_time).total_seconds(),
        "cmd": cmd,
        "workspace_root": str(output_dir.parent.parent),
        "workspace": str(workspace_dir),
        "analysis_dir": str(analysis_dir) if analysis_dir else str(workspace_dir / "analysis"),
        "input_dir": str(input_dir),
        "metrics": {
            "total_samples": len(sample_stats),
            "total_raw_reads": total_raw,
            "total_clean_reads": total_clean,
            "samples": sample_stats
        },
        "parameters": {
            "cpu": cpu,
            "sample_parallel_jobs": sample_parallel_jobs,
            "threads_per_sample": threads_per_sample,
            "qc_tool": qc_tool,
            "fastqc_params": f"-t {threads_per_sample}",
            "cutadapt_params": f"-q 20 -m 50 -j {threads_per_sample}" if qc_tool == "cutadapt" else "",
            "fastp_bin": fastp_bin,
            "fastp_params": fastp_params or {},
        },
        "artifacts": {},
        "tools": versions
    }
    context = build_rnaseq_context(
        prefix=prefix,
        sample_config=sample_config,
        foreground_mode="global",
        stage="rnaseq_qc",
    )
    apply_rnaseq_context(manifest, context)
    
    # 添加artifacts（文件生成在 final/ 目录，使用 prefix 命名）
    # 参考 synteny 契约，包含 full_path、size、sha256 等字段
    report_name = f"{prefix}.rnaseq_qc_report.md" if prefix else "rnaseq_qc_report.md"
    manifest_name = f"{prefix}.rnaseq_qc.manifest.json" if prefix else "rnaseq_qc.manifest.json"
    
    def add_artifact(name: str, filepath: Path, artifact_type: str, **extra):
        """添加 artifact 到 manifest"""
        if filepath.exists():
            manifest["artifacts"][name] = {
                "artifact": name,
                "path": f"final/{filepath.name}",
                "full_path": str(filepath.absolute()),
                "type": artifact_type,
                "size": filepath.stat().st_size,
                "sha256": compute_file_hash(filepath),
                **extra
            }
    
    # analysis_dir 用于引用质控报告文件（保留在 analysis 目录）
    analysis_path = analysis_dir if analysis_dir else workspace_dir / "analysis"
    
    # final_dir 是 output_dir 的父目录下的 final 目录
    final_dir = output_dir.parent / "final"
    
    artifact_prefix = prefix if prefix else "rnaseq"
    for sample in sample_stats.keys():
        # FastQC HTML 报告（复制到 final，前端展示）
        html_r1 = final_dir / f"{artifact_prefix}.rnaseq_qc.{sample}_1_fastqc.html"
        html_r2 = final_dir / f"{artifact_prefix}.rnaseq_qc.{sample}_2_fastqc.html"
        add_artifact(f"qc_report_{sample}_r1", html_r1, "html", format="html")
        add_artifact(f"qc_report_{sample}_r2", html_r2, "html", format="html")
        
        # FastQC 数据（zip，保留在 analysis 目录）
        zip_r1 = analysis_path / f"{sample}_1_fastqc.zip"
        zip_r2 = analysis_path / f"{sample}_2_fastqc.zip"
        if zip_r1.exists():
            manifest["artifacts"][f"qc_data_{sample}_r1"] = {
                "artifact": f"qc_data_{sample}_r1",
                "path": f"rnaseq_qc/analysis/{zip_r1.name}",
                "full_path": str(zip_r1.absolute()),
                "type": "zip",
                "size": zip_r1.stat().st_size,
                "sha256": compute_file_hash(zip_r1),
                "format": "zip"
            }
        if zip_r2.exists():
            manifest["artifacts"][f"qc_data_{sample}_r2"] = {
                "artifact": f"qc_data_{sample}_r2",
                "path": f"rnaseq_qc/analysis/{zip_r2.name}",
                "full_path": str(zip_r2.absolute()),
                "type": "zip",
                "size": zip_r2.stat().st_size,
                "sha256": compute_file_hash(zip_r2),
                "format": "zip"
            }
        
        # Clean reads（FASTQ）- 大文件，复制到 final 后使用标准前缀命名
        clean_r1 = output_dir / f"{artifact_prefix}.rnaseq_qc.{sample}_1.clean.fastq.gz"
        clean_r2 = output_dir / f"{artifact_prefix}.rnaseq_qc.{sample}_2.clean.fastq.gz"
        add_artifact(f"clean_reads_r1_{sample}", clean_r1, "fastq", format="fastq.gz")
        add_artifact(f"clean_reads_r2_{sample}", clean_r2, "fastq", format="fastq.gz")
        
        # Cutadapt 日志
        log_file = output_dir / f"{artifact_prefix}.rnaseq_qc.{sample}_cutadapt.log"
        add_artifact(f"cutadapt_log_{sample}", log_file, "log", format="txt")

        fastp_html = output_dir / f"{artifact_prefix}.rnaseq_qc.{sample}_fastp.html"
        fastp_json = output_dir / f"{artifact_prefix}.rnaseq_qc.{sample}_fastp.json"
        fastp_log = output_dir / f"{artifact_prefix}.rnaseq_qc.{sample}_fastp.log"
        add_artifact(f"fastp_report_{sample}", fastp_html, "html", format="html")
        add_artifact(f"fastp_json_{sample}", fastp_json, "json", format="json")
        add_artifact(f"fastp_log_{sample}", fastp_log, "log", format="txt")
    
    # 报告、MultiQC 和 manifest
    report_file = output_dir / report_name
    add_artifact("report", report_file, "markdown", format="md")
    multiqc_name = f"{prefix}.rnaseq_qc_multiqc_report.html" if prefix else "rnaseq_qc_multiqc_report.html"
    multiqc_file = output_dir / multiqc_name
    add_artifact("multiqc_report", multiqc_file, "html", format="html")
    multiqc_data = output_dir / multiqc_name.replace(".html", "_data")
    if multiqc_data.exists():
        manifest["artifacts"]["multiqc_data"] = {
            "artifact": "multiqc_data",
            "path": f"final/{multiqc_data.name}",
            "full_path": str(multiqc_data.absolute()),
            "type": "directory",
            "format": "multiqc_data"
        }
    
    # manifest 自身（在生成后添加）
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
    parser = argparse.ArgumentParser(description="RNA-Seq Quality Control")
    parser.add_argument("-i", "--input", required=True, help="Input directory with FASTQ files")
    parser.add_argument("-c", "--config", required=True, help="Sample configuration file (YAML)")
    parser.add_argument("-o", "--output", required=True, help="Output directory")
    parser.add_argument("-j", "--job-id", default="unknown", help="Job ID")
    parser.add_argument("-r", "--run-id", default="unknown", help="Run ID")
    parser.add_argument("-t", "--cpu", type=int, default=4, help="Total CPU threads for this stage")
    parser.add_argument("--sample-parallel-jobs", type=int, default=1, help="Number of samples to process concurrently")
    parser.add_argument("--threads-per-sample", type=int, help="Threads used by each per-sample tool invocation")
    parser.add_argument("--resume-existing", action="store_true", help="Skip samples whose FastQC, clean reads, and cutadapt outputs already exist in the output directory")
    parser.add_argument("-p", "--prefix", default="", help="Output file prefix")
    parser.add_argument("--adapter-r1", default="AGATCGGAAGAGC", help="Adapter sequence for read1")
    parser.add_argument("--adapter-r2", default="AGATCGGAAGAGC", help="Adapter sequence for read2")
    parser.add_argument("--qc-tool", choices=["cutadapt", "fastp"], default="fastp", help="QC/cleaning backend. Default uses fastp; cutadapt remains available by explicit selection.")
    parser.add_argument("--fastp-bin", default="tools/fastp", help="Path to fastp executable. If omitted, FASTP_BIN or PATH is used.")
    parser.add_argument("--detect-adapter-for-pe", action=argparse.BooleanOptionalAction, default=True, help="Enable fastp paired-end adapter auto-detection.")
    parser.add_argument("--cut-front", action=argparse.BooleanOptionalAction, default=True, help="Enable fastp front quality cutting.")
    parser.add_argument("--cut-tail", action=argparse.BooleanOptionalAction, default=True, help="Enable fastp tail quality cutting.")
    parser.add_argument("--cut-window-size", type=int, default=4, help="fastp quality cutting window size.")
    parser.add_argument("--cut-mean-quality", type=int, default=20, help="fastp mean quality threshold for cutting.")
    parser.add_argument("--qualified-quality-phred", type=int, default=20, help="fastp qualified quality phred threshold.")
    parser.add_argument("--unqualified-percent-limit", type=int, default=30, help="fastp maximum unqualified base percent.")
    parser.add_argument("--n-base-limit", type=int, default=5, help="fastp maximum N bases per read.")
    parser.add_argument("--length-required", type=int, default=30, help="fastp minimum output read length.")
    
    args = parser.parse_args()
    thread_plan = resolve_thread_plan(args.cpu, args.sample_parallel_jobs, args.threads_per_sample)
    threads_per_sample = thread_plan["threads_per_sample"]
    
    start_time = datetime.now()
    input_dir = Path(args.input)
    output_dir = Path(args.output)  # rnaseq_qc 工作目录
    config_file = Path(args.config)
    context_for_prefix = resolve_rnaseq_project_context(
        input_dir=input_dir,
        requested_prefix=args.prefix,
        sample_config=config_file,
    )
    resolved_prefix = context_for_prefix.get("output_prefix")
    if resolved_prefix and resolved_prefix != args.prefix:
        print(
            f"[INFO] Using RNA-seq output prefix {resolved_prefix} "
            f"instead of requested {args.prefix or '<empty>'} "
            f"(source={context_for_prefix.get('source', '')})"
        )
        args.prefix = resolved_prefix
    
    # 创建工作目录
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 最终结果目录: workspace/<job_id>/final/
    # 根据契约，最终产物必须位于 workspace/<job_id>/final/
    workspace_dir = output_dir.parent  # job_id 目录
    final_dir = workspace_dir / "final"
    final_dir.mkdir(exist_ok=True)
    
    # 中间文件目录: rnaseq_qc/analysis/
    analysis_dir = output_dir / "analysis"
    analysis_dir.mkdir(exist_ok=True)
    
    # 加载样本配置并严格验证输入文件
    config = load_samples_config(config_file)
    
    # 严格验证输入文件（失败时直接退出）
    validated_samples = validate_input_files(input_dir, config)
    
    print(f"\nProcessing {len(validated_samples)} samples...")
    
    fastp_bin = resolve_fastp_bin(args.fastp_bin) if args.qc_tool == "fastp" else None
    if args.qc_tool == "fastp" and not fastp_bin:
        print("❌ ERROR: --qc-tool fastp requires an executable --fastp-bin, FASTP_BIN, or fastp on PATH", file=sys.stderr)
        sys.exit(1)

    fastp_params = {
        "detect_adapter_for_pe": bool(args.detect_adapter_for_pe),
        "cut_front": bool(args.cut_front),
        "cut_tail": bool(args.cut_tail),
        "cut_window_size": args.cut_window_size,
        "cut_mean_quality": args.cut_mean_quality,
        "qualified_quality_phred": args.qualified_quality_phred,
        "unqualified_percent_limit": args.unqualified_percent_limit,
        "n_base_limit": args.n_base_limit,
        "length_required": args.length_required,
    }

    # 获取工具版本
    versions = get_tool_versions(fastp_bin)
    print(f"Tool versions: {versions}")
    
    sample_stats = {}
    total_samples = len(validated_samples)

    def qc_one_sample(sample_name: str, file_info: Dict[str, Path]) -> tuple[str, Optional[Dict[str, Any]], Optional[str]]:
        r1 = file_info["r1"]
        r2 = file_info["r2"]
        print(f"\n[PROGRESS] Processing: {sample_name}", flush=True)
        print(f"   Input: {r1.name} + {r2.name}", flush=True)
        if args.resume_existing and sample_qc_outputs_complete(sample_name, file_info, analysis_dir, args.qc_tool):
            stats = get_sample_stats(analysis_dir, sample_name)
            stats["resumed_from_existing"] = True
            stats["fastqc_time_sec"] = 0
            stats["cutadapt_time_sec"] = 0
            stats["fastp_time_sec"] = 0
            print(f"   [RESUME] Skipping completed sample {sample_name}; existing QC outputs are complete", flush=True)
            return sample_name, stats, None

        out_r1 = analysis_dir / f"{sample_name}_1.clean.fastq.gz"
        out_r2 = analysis_dir / f"{sample_name}_2.clean.fastq.gz"
        if args.qc_tool == "fastp":
            try:
                fastp_result = run_fastp(
                    fastp_bin=fastp_bin,
                    r1=r1,
                    r2=r2,
                    out_r1=out_r1,
                    out_r2=out_r2,
                    html_report=analysis_dir / f"{sample_name}_fastp.html",
                    json_report=analysis_dir / f"{sample_name}_fastp.json",
                    log_file=analysis_dir / f"{sample_name}_fastp.log",
                    cpu=threads_per_sample,
                    **fastp_params,
                )
            except RuntimeError as e:
                return sample_name, None, f"fastp failed for {sample_name}: {e}"
            stats = get_sample_stats(analysis_dir, sample_name)
            stats["fastqc_time_sec"] = 0
            stats["cutadapt_time_sec"] = 0
            stats["fastp_time_sec"] = fastp_result.get("time_sec", 0)
        else:
            try:
                fastqc_result = run_fastqc([r1, r2], analysis_dir, threads_per_sample)
            except RuntimeError as e:
                return sample_name, None, f"FastQC failed for {sample_name}: {e}"
            log_file = analysis_dir / f"{sample_name}_cutadapt.log"
            try:
                cutadapt_result = run_cutadapt(
                    r1, r2, out_r1, out_r2, log_file,
                    args.adapter_r1, args.adapter_r2,
                    cpu=threads_per_sample,
                )
            except RuntimeError as e:
                return sample_name, None, f"Cutadapt failed for {sample_name}: {e}"
            stats = get_sample_stats(analysis_dir, sample_name)
            stats["fastqc_time_sec"] = fastqc_result.get("total_time_sec", 0)
            stats["cutadapt_time_sec"] = cutadapt_result.get("time_sec", 0)
            stats["fastp_time_sec"] = 0

        print(f"   ✓ Sample {sample_name} completed", flush=True)
        return sample_name, stats, None

    print(
        f"Thread plan: sample_parallel_jobs={thread_plan['sample_parallel_jobs']}, "
        f"threads_per_sample={threads_per_sample}, total_cpu={thread_plan['cpu']}"
    )
    with ThreadPoolExecutor(max_workers=max(1, int(thread_plan["sample_parallel_jobs"]))) as executor:
        future_map = {
            executor.submit(qc_one_sample, sample_name, file_info): sample_name
            for sample_name, file_info in validated_samples.items()
        }
        for future in as_completed(future_map):
            sample_name = future_map[future]
            try:
                completed_name, stats, error = future.result()
            except Exception as exc:
                print(f"\n❌ ERROR: QC failed for {sample_name}: {exc}", file=sys.stderr)
                sys.exit(1)
            if error:
                print(f"\n❌ ERROR: {error}", file=sys.stderr)
                sys.exit(1)
            if stats is not None:
                sample_stats[completed_name] = stats

    # 复制结果到 final 目录，使用标准命名格式
    safe_prefix = args.prefix if args.prefix else "rnaseq"
    
    print(f"\n[PROGRESS] Copying results to final directory...")
    copied_files = 0
    expected_files_per_sample = 5  # cutadapt: 2 clean + 2 FastQC HTML + 1 log; fastp: 2 clean + HTML + JSON + log
    
    for sample_name, file_info in validated_samples.items():
        sample_copied = 0
        r1_name = file_info["r1"].stem.replace('.fastq', '').replace('.fq', '')  # 原始R1文件名（不含扩展名）
        r2_name = file_info["r2"].stem.replace('.fastq', '').replace('.fq', '')  # 原始R2文件名（不含扩展名）
        
        # 复制 clean reads 到 final（下游分析需要）
        for suffix in ["_1.clean.fastq.gz", "_2.clean.fastq.gz"]:
            src = analysis_dir / f"{sample_name}{suffix}"
            dst = final_dir / f"{safe_prefix}.rnaseq_qc.{sample_name}{suffix}"
            if src.exists() and src.stat().st_size > 0:
                shutil.copy2(src, dst)
                copied_files += 1
                sample_copied += 1
            else:
                print(f"   ⚠ Missing/empty: {src.name}", file=sys.stderr)
        
        if args.qc_tool == "fastp":
            for src_name, dst_suffix in [
                (f"{sample_name}_fastp.html", f"{sample_name}_fastp.html"),
                (f"{sample_name}_fastp.json", f"{sample_name}_fastp.json"),
                (f"{sample_name}_fastp.log", f"{sample_name}_fastp.log"),
            ]:
                src = analysis_dir / src_name
                dst = final_dir / f"{safe_prefix}.rnaseq_qc.{dst_suffix}"
                if src.exists() and src.stat().st_size > 0:
                    shutil.copy2(src, dst)
                    copied_files += 1
                    sample_copied += 1
                else:
                    print(f"   ⚠ Missing/empty fastp output: {src_name}", file=sys.stderr)
        else:
            # 复制 FastQC HTML 报告到 final（前端展示需要）
            # FastQC使用原始FASTQ文件名生成报告
            fastqc_patterns = [
                (f"{r1_name}_fastqc.html", f"{sample_name}_1_fastqc.html"),
                (f"{r2_name}_fastqc.html", f"{sample_name}_2_fastqc.html")
            ]
            for src_pattern, dst_suffix in fastqc_patterns:
                src = analysis_dir / src_pattern
                dst = final_dir / f"{safe_prefix}.rnaseq_qc.{dst_suffix}"
                if src.exists() and src.stat().st_size > 0:
                    shutil.copy2(src, dst)
                    copied_files += 1
                    sample_copied += 1
                else:
                    print(f"   ⚠ Missing/empty FastQC HTML: {src_pattern}", file=sys.stderr)
            
            # 复制 cutadapt 日志到 final（小文件，包含统计信息）
            src = analysis_dir / f"{sample_name}_cutadapt.log"
            dst = final_dir / f"{safe_prefix}.rnaseq_qc.{sample_name}_cutadapt.log"
            if src.exists() and src.stat().st_size > 0:
                shutil.copy2(src, dst)
                copied_files += 1
                sample_copied += 1
            else:
                print(f"   ⚠ Missing/empty: {src.name}", file=sys.stderr)
        
        print(f"   {sample_name}: {sample_copied}/{expected_files_per_sample} files copied")
    
    # 验证输出完整性
    total_expected = len(validated_samples) * expected_files_per_sample
    if copied_files < total_expected:
        print(f"\n❌ ERROR: Output validation failed", file=sys.stderr)
        print(f"   Expected: {total_expected} files", file=sys.stderr)
        print(f"   Copied: {copied_files} files", file=sys.stderr)
        sys.exit(1)
    
    print(f"\n✅ All {copied_files} output files validated and copied")
    
    # 生成 MultiQC 报告
    multiqc_name = f"{args.prefix}.rnaseq_qc_multiqc_report.html" if args.prefix else "rnaseq_qc_multiqc_report.html"
    multiqc_report = run_multiqc([analysis_dir], final_dir, multiqc_name)
    if multiqc_report:
        print(f"MultiQC report: {multiqc_report}")

    # 生成报告
    end_time = datetime.now()
    generate_report(final_dir, sample_stats, versions, start_time, end_time, args.prefix, qc_tool=args.qc_tool)
    
    # 生成manifest
    cmd = ["python", __file__, "-i", str(input_dir), "-c", str(config_file),
           "-o", str(output_dir), "-j", args.job_id, "-r", args.run_id, "-p", args.prefix,
           "-t", str(thread_plan["cpu"]),
           "--sample-parallel-jobs", str(thread_plan["sample_parallel_jobs"]),
           "--threads-per-sample", str(thread_plan["threads_per_sample"]),
           "--qc-tool", args.qc_tool]
    if args.qc_tool == "fastp":
        cmd.extend(["--fastp-bin", str(fastp_bin)])
        for flag, value in {
            "--cut-window-size": args.cut_window_size,
            "--cut-mean-quality": args.cut_mean_quality,
            "--qualified-quality-phred": args.qualified_quality_phred,
            "--unqualified-percent-limit": args.unqualified_percent_limit,
            "--n-base-limit": args.n_base_limit,
            "--length-required": args.length_required,
        }.items():
            cmd.extend([flag, str(value)])
        if args.detect_adapter_for_pe:
            cmd.append("--detect-adapter-for-pe")
        else:
            cmd.append("--no-detect-adapter-for-pe")
        if args.cut_front:
            cmd.append("--cut-front")
        else:
            cmd.append("--no-cut-front")
        if args.cut_tail:
            cmd.append("--cut-tail")
        else:
            cmd.append("--no-cut-tail")
    generate_manifest(
        final_dir, sample_stats, versions, start_time, end_time,
        cmd, input_dir, output_dir, args.job_id, args.run_id, args.prefix,
        analysis_dir=analysis_dir,  # 传入 analysis 目录
        cpu=args.cpu,
        sample_parallel_jobs=thread_plan["sample_parallel_jobs"],
        threads_per_sample=threads_per_sample,
        sample_config=config_file,
        qc_tool=args.qc_tool,
        fastp_bin=str(fastp_bin) if fastp_bin else "",
        fastp_params=fastp_params if args.qc_tool == "fastp" else {},
    )
    
    total_elapsed = (end_time - start_time).total_seconds()
    print(f"\n{'='*60}")
    print(f"✅ RNA-seq QC completed successfully")
    print(f"   Samples processed: {len(validated_samples)}")
    print(f"   Total runtime: {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")
    print(f"   Results: {output_dir}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
