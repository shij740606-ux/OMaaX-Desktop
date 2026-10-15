#!/usr/bin/env python3
"""
从NCBI GEO下载表达量数据

功能：
1. 根据GEO编号(GSE/GSM)下载表达量矩阵
2. 解析样本元数据，生成sample.txt
3. 支持FPKM/TPM格式

用法：
    python run_fetch_expression.py --geo-id GSE111937 --output-dir ./output
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import ssl
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# 添加conda环境bin目录到PATH
CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
if CONDA_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = CONDA_BIN + os.pathsep + os.environ.get("PATH", "")


def geo_ssl_context() -> ssl.SSLContext:
    """Use a CA bundle that works with the local network's TLS chain."""
    ca_candidates = [
        Path("/etc/ssl/certs/ca-certificates.crt"),
    ]
    try:
        import certifi

        ca_candidates.append(Path(certifi.where()))
    except Exception:
        pass
    for ca_file in ca_candidates:
        if ca_file.exists() and ca_file.stat().st_size > 0:
            return ssl.create_default_context(cafile=str(ca_file))
    return ssl.create_default_context()


def get_geo_metadata(geo_id: str) -> Dict:
    """
    通过NCBI E-utilities获取GEO元数据
    """
    pass


def download_geo_expression(geo_id: str, output_dir: Path) -> Tuple[Optional[Path], Optional[Path], str]:
    """
    下载GEO表达量数据
    """
    print(f"\n开始下载GEO数据: {geo_id}")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. 直接访问FTP目录查找supplementary files
    # GEO系列编号的目录格式: GSE111nnn/GSE111937
    prefix = geo_id[:-3] + "nnn"  # GSE111937 -> GSE111nnn
    ftp_url = f"https://ftp.ncbi.nlm.nih.gov/geo/series/{prefix}/{geo_id}/suppl/"
    
    try:
        print(f"  访问FTP目录: {ftp_url}")
        req = urllib.request.Request(ftp_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=60, context=geo_ssl_context()) as response:
            html_content = response.read().decode('utf-8')
    except Exception as e:
        return None, None, f"❌ 无法访问FTP目录: {e}"
    
    # 2. 查找supplementary file下载链接 (txt.gz文件)
    supp_files = []
    pattern = r'href="([^"]+\.txt\.gz)"'
    matches = re.findall(pattern, html_content)
    
    for match in matches:
        url = ftp_url + match
        if 'fpkm' in url.lower() or 'tpm' in url.lower():
            supp_files.append(url)
            print(f"  找到表达量文件: {url}")
    
    # 如果没找到fpkm/tpm，尝试找其他txt文件
    if not supp_files:
        for match in matches:
            url = ftp_url + match
            if url not in supp_files:
                supp_files.append(url)
                print(f"  找到文件: {url}")
    
    if not supp_files:
        return None, None, "❌ 未找到Supplementary文件，可能需要手动下载"
    
    # 3. 下载表达量矩阵文件
    expression_file = None
    for url in supp_files:
        filename = url.split('/')[-1]
        local_path = output_dir / filename
        
        if local_path.exists():
            print(f"  文件已存在，跳过下载: {local_path.name}")
        else:
            print(f"  下载中: {filename}")
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=300, context=geo_ssl_context()) as response:
                    with open(local_path, "wb") as out_handle:
                        shutil.copyfileobj(response, out_handle)
                
                if local_path.exists() and local_path.stat().st_size > 0:
                    print(f"  下载成功: {local_path.name} ({local_path.stat().st_size} bytes)")
                else:
                    continue
            except Exception as e:
                print(f"  下载异常: {e}")
                continue
        
        # 解压如果需要
        if local_path.suffix == '.gz':
            unzip_path = local_path.with_suffix('')
            if unzip_path.exists():
                print(f"  解压文件已存在: {unzip_path.name}")
            else:
                print(f"  解压中: {local_path.name}")
                try:
                    subprocess.run(['gzip', '-d', '-f', str(local_path)], check=True)
                except Exception as e:
                    return None, None, f"❌ 解压失败: {e}"
            expression_file = unzip_path
        else:
            expression_file = local_path
        
        if expression_file:
            break
    
    if not expression_file or not expression_file.exists():
        return None, None, "❌ 下载表达量文件失败"
    
    # 4. 从表达量文件生成sample.txt - 使用列名前缀作为分组
    print("  生成样本信息文件...")
    sample_file = output_dir / "sample.txt"
    try:
        with open(expression_file, 'r') as f:
            header = f.readline().strip().split('\t')
        sample_names = header[1:]  # 排除第一列(geneID)
        
        with open(sample_file, 'w') as f:
            for sn in sample_names:
                group = sn.split('_')[0]  # X2_1 -> X2
                f.write(f"{sn}\t{group}\n")
        
        print(f"  生成样本信息文件: {sample_file}")
    except Exception as e:
        print(f"  生成样本信息失败: {e}")
        return expression_file, None, "⚠️ 生成sample.txt失败"
    
    return expression_file, sample_file, "✅ GEO数据下载成功"


def main():
    parser = argparse.ArgumentParser(description="GEO表达量数据下载工具")
    parser.add_argument("--geo-id", required=True, help="GEO编号 (如 GSE111937)")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("GEO Expression Data Downloader")
    print("=" * 60)
    print(f"GEO ID: {args.geo_id}")
    print(f"Output: {args.output_dir}")
    
    expression_file, sample_file, message = download_geo_expression(
        args.geo_id,
        Path(args.output_dir)
    )
    
    print("\n" + "=" * 60)
    print(message)
    print("=" * 60)
    
    if expression_file:
        print("\n生成文件:")
        print(f"  - 表达量矩阵: {expression_file.name}")
        if sample_file:
            print(f"  - 样本信息: {sample_file.name}")
    
    return 0 if expression_file else 1


if __name__ == "__main__":
    sys.exit(main())
