#!/usr/bin/env python3
"""
顺式作用元件预测 - HTTP Agent 版本
无需浏览器，纯 HTTP 提交
"""

import os
import argparse
import json
import shutil
import sys
import tarfile
import time
from datetime import datetime
from pathlib import Path

# 导入 HTTP 提交器
from utils.plantcare_submitter_http import PlantCAREHTTPSubmitter
from utils.email_fetcher import PlantCAREEmailFetcher
from utils.cis_element_parser import PlantCAREParser


# 添加conda环境bin目录到PATHCONDA_BIN = "/home/xia-lab/miniconda3/envs/test4py311/bin"if CONDA_BIN not in os.environ.get("PATH", ""):    os.environ["PATH"] = CONDA_BIN + ":" + os.environ.get("PATH", "")
def main():
    parser = argparse.ArgumentParser(
        description="Cis-Element Analysis via PlantCARE (HTTP Agent)"
    )
    parser.add_argument("--upstream_fasta", required=True, help="上游序列FASTA文件")
    parser.add_argument("--job_name", required=True, help="任务标识(用于邮件匹配)")
    parser.add_argument("--prefix", required=True, help="输出文件前缀")
    parser.add_argument("--workspace", required=True, help="工作目录")
    parser.add_argument("--email", default="gfmagent@foxmail.com", help="接收结果的邮箱")
    parser.add_argument("--email_password", required=True, help="邮箱IMAP授权码")
    parser.add_argument("--timeout", type=int, default=3600, 
                       help="等待邮件超时时间(秒)，默认1小时")
    
    args = parser.parse_args()
    
    # 准备工作目录
    ws = Path(args.workspace)
    analysis_dir = ws / "cis_element" / "analysis"
    final_dir = ws / "final" / "cis_element"
    final_dir.mkdir(parents=True, exist_ok=True)
    
    # 验证输入文件
    fasta_path = Path(args.upstream_fasta)
    if not fasta_path.exists():
        print(f"错误: FASTA文件不存在: {fasta_path}", file=sys.stderr)
        return 1
    
    # 检查文件大小 (PlantCARE 限制 100KB)
    file_size = fasta_path.stat().st_size
    if file_size > 100 * 1024:
        print(f"错误: 文件过大 ({file_size/1024:.1f}KB > 100KB 限制)", file=sys.stderr)
        return 1
    
    start_time = datetime.now()
    
    try:
        # Stage 1: HTTP 提交到 PlantCARE
        print(f"[1/4] HTTP 提交任务到 PlantCARE: {args.job_name}")
        submitter = PlantCAREHTTPSubmitter(args.email)
        
        try:
            job_info = submitter.submit(fasta_path, args.job_name)
        except Exception as e:
            print(f"      提交失败: {e}", file=sys.stderr)
            # 保存错误状态
            _save_error_manifest(final_dir, args, start_time, str(e))
            return 1
        
        print(f"      ✓ Job ID: {job_info['plantcare_job_id']}")
        print(f"      ✓ 内部ID: {job_info['internal_id']}")
        
        # 保存中间状态 (支持断点续传)
        state_file = analysis_dir / f"{args.prefix}.state.json"
        state_file.write_text(json.dumps({
            "stage": "waiting_for_email",
            "job_info": job_info,
            "timestamp": start_time.isoformat()
        }, indent=2))
        
        # Stage 2: 等待邮件结果
        print(f"[2/4] 等待邮件结果 (最长 {args.timeout//3600} 小时)...")
        email_start = time.time()
        
        fetcher = PlantCAREEmailFetcher(args.email, args.email_password)
        
        try:
            tar_path = fetcher.wait_for_result(
                job_info['plantcare_job_id'],
                args.job_name,
                timeout_seconds=args.timeout
            )
            email_wait_time = time.time() - email_start
            print(f"      ✓ 收到附件: {tar_path.name} (等待 {email_wait_time:.1f} 秒)")
            
        except TimeoutError as e:
            print(f"      ✗ 等待超时: {e}", file=sys.stderr)
            _save_error_manifest(final_dir, args, start_time, str(e), job_info)
            return 1
        except Exception as e:
            print(f"      ✗ 邮箱错误: {e}", file=sys.stderr)
            _save_error_manifest(final_dir, args, start_time, str(e), job_info)
            return 1
        
        # Stage 3: 解析结果
        print(f"[3/4] 解析 PlantCARE 结果...")
        parser = PlantCAREParser()
        
        # 保存原始附件
        raw_tar = final_dir / f"{args.prefix}.plantcare_raw.tar.gz"
        shutil.copy(tar_path, raw_tar)
        
        # 解压并解析
        parsed = parser.parse_tarball(tar_path, analysis_dir)
        print(f"      ✓ 发现 {parsed['total_elements']} 个顺式作用元件")
        
        # Stage 4: 生成标准输出
        print(f"[4/4] 生成标准契约输出...")
        
        # 复制结果文件
        result_files = list(analysis_dir.glob("*.html")) + list(analysis_dir.glob("*.txt"))
        if result_files:
            final_html = final_dir / f"{args.prefix}.cis_element_results.html"
            shutil.copy(result_files[0], final_html)
        
        # 生成摘要 JSON
        summary = {
            "job_info": job_info,
            "parsing_result": parsed,
            "performance": {
                "email_wait_time_sec": round(email_wait_time, 2),
                "total_time_sec": round((datetime.now() - start_time).total_seconds(), 2)
            }
        }
        summary_file = final_dir / f"{args.prefix}.cis_element_summary.json"
        summary_file.write_text(json.dumps(summary, indent=2))
        
        # 生成 Markdown 报告
        report = _generate_report(args.prefix, parsed, job_info)
        report_file = final_dir / f"{args.prefix}.cis_element_report.md"
        report_file.write_text(report)
        
        # 生成 Manifest
        manifest = _generate_manifest(args, job_info, final_dir, summary, start_time)
        manifest_file = final_dir / f"{args.prefix}.cis_element.manifest.json"
        manifest_file.write_text(json.dumps(manifest, indent=2))
        
        print(f"\n✓ 完成! 结果保存在: {final_dir}")
        return 0
        
    except Exception as e:
        print(f"\n✗ 未处理错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


def _generate_report(prefix: str, parsed: dict, job_info: dict) -> str:
    """生成 Markdown 报告"""
    lines = [
        f"# {prefix} 顺式作用元件预测报告",
        "",
        "## 任务信息",
        f"- **PlantCARE Job ID**: `{job_info['plantcare_job_id']}`",
        f"- **参考名称**: {job_info['ref']}",
        f"- **接收邮箱**: {job_info['email']}",
        "",
        f"## 分析结果",
        f"- **发现顺式作用元件总数**: **{parsed['total_elements']}**",
        "",
        "## 详细结果 (前50个)",
        "| 位置 | 序列 | 元件名称 | 功能 |",
        "|------|------|----------|------|"
    ]
    
    elements = parsed.get('elements', [])
    for elem in elements[:50]:
        lines.append(
            f"| {elem.get('position', '-')} | {elem.get('sequence', '-')} | "
            f"{elem.get('element_name', '-')} | {elem.get('function', '-')} |"
        )
    
    if len(elements) > 50:
        lines.append(f"\n> 共 {len(elements)} 个元件，完整结果见 JSON 文件")
    
    lines.extend([
        "",
        "## 数据来源",
        f"- 原始结果: `{prefix}.plantcare_raw.tar.gz`",
        f"- 结构化数据: `{prefix}.cis_element_summary.json`"
    ])
    
    return "\n".join(lines)


def _generate_manifest(args, job_info, final_dir, summary, start_time):
    """生成符合契约的 Manifest"""
    end_time = datetime.now()
    elapsed = (end_time - start_time).total_seconds()
    
    def get_size(filename):
        path = final_dir / filename
        return path.stat().st_size if path.exists() else 0
    
    prefix = args.prefix
    
    return {
        "manifest_version": "1.0",
        "job_id": Path(args.workspace).name,
        "stage": "cis_element",
        "run_id": f"ce_http_{end_time.strftime('%Y%m%d_%H%M%S')}",
        "prefix": prefix,
        "timestamp": end_time.isoformat(),
        "status": "success",
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "elapsed_sec": round(elapsed, 2),
        "cmd": sys.argv,
        "workspace_root": args.workspace,
        "workspace": str(Path(args.workspace) / "cis_element"),
        "input_dir": str(Path(args.workspace) / "input"),
        "artifacts": {
            "raw_tarball": {
                "artifact": "plantcare_raw.tar.gz",
                "full_path": str(final_dir / f"{prefix}.plantcare_raw.tar.gz"),
                "type": "application/gzip",
                "size": get_size(f"{prefix}.plantcare_raw.tar.gz")
            },
            "results_html": {
                "artifact": "cis_element_results.html",
                "full_path": str(final_dir / f"{prefix}.cis_element_results.html"),
                "type": "text/html",
                "size": get_size(f"{prefix}.cis_element_results.html")
            },
            "summary_json": {
                "artifact": "cis_element_summary.json",
                "full_path": str(final_dir / f"{prefix}.cis_element_summary.json"),
                "type": "application/json",
                "size": get_size(f"{prefix}.cis_element_summary.json")
            },
            "report": {
                "artifact": "cis_element_report.md",
                "full_path": str(final_dir / f"{prefix}.cis_element_report.md"),
                "type": "text/markdown",
                "size": get_size(f"{prefix}.cis_element_report.md")
            },
            "manifest": {
                "artifact": "cis_element.manifest.json",
                "full_path": str(final_dir / f"{prefix}.cis_element.manifest.json"),
                "type": "application/json",
                "size": 0  # 自引用，最后计算
            }
        },
        "parameters": {
            "upstream_fasta": args.upstream_fasta,
            "job_name": args.job_name,
            "email": args.email,
            "submission_method": "http_agent",
            "plantcare_job_id": job_info['plantcare_job_id']
        },
        "metrics": {
            "sequences_count": -1,  # 可从 FASTA 统计
            "elements_found_count": summary['parsing_result']['total_elements'],
            "email_wait_time_sec": summary['performance']['email_wait_time_sec'],
            "total_time_sec": summary['performance']['total_time_sec']
        }
    }


def _save_error_manifest(final_dir, args, start_time, error_msg, job_info=None):
    """保存失败状态的 Manifest"""
    final_dir.mkdir(parents=True, exist_ok=True)
    
    manifest = {
        "manifest_version": "1.0",
        "job_id": Path(args.workspace).name if args.workspace else "unknown",
        "stage": "cis_element",
        "timestamp": datetime.now().isoformat(),
        "status": "failed",
        "start_time": start_time.isoformat(),
        "end_time": datetime.now().isoformat(),
        "error": error_msg,
        "parameters": {
            "job_name": args.job_name,
            "email": args.email
        }
    }
    
    if job_info:
        manifest["job_info"] = job_info
    
    manifest_file = final_dir / f"{args.prefix}.cis_element.manifest.json"
    manifest_file.write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    sys.exit(main())
