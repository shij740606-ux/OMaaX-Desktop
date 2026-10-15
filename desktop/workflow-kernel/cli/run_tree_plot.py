#!/usr/bin/env python3
"""
绘制进化树图的Python包装器
"""

import os
import argparse
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime

# 镜像标准形态: 当前解释器即环境解释器(PATH 由容器提供)
CONDA_PREFIX = Path(sys.executable).resolve().parent.parent
CONDA_BIN = str(CONDA_PREFIX / "bin")
def _now_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def main():
    parser = argparse.ArgumentParser(description="绘制进化树图")
    parser.add_argument("--manifest", type=Path, help="manifest文件路径")
    parser.add_argument("--prefix", type=str, help="分析前缀")
    parser.add_argument("--workspace", type=Path, help="工作空间目录")
    parser.add_argument("--log-file", type=Path, help="日志文件路径")
    
    args = parser.parse_args()
    
    # 创建日志文件
    log_file = args.log_file or Path(f"tree_plot_{_now_tag()}.log")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(log_file, 'w', encoding='utf-8') as f_log:
        f_log.write(f"命令: {' '.join(sys.argv)}\n")
        f_log.write(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f_log.write("=" * 80 + "\n\n")
    
    print(f"🔧 开始绘制进化树图")
    print(f"   日志文件: {log_file}")
    
    # 确定输入文件
    nwk_file = None
    msa_file = None
    
    if args.manifest and args.manifest.exists():
        # 从manifest读取
        try:
            with open(args.manifest, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 查找tree.nwk文件
            artifacts = data.get('artifacts', {})
            if isinstance(artifacts, dict):
                tree_info = artifacts.get('tree', {})
                if tree_info:
                    tree_path = tree_info.get('full_path') or tree_info.get('path')
                    if tree_path:
                        nwk_file = Path(tree_path)
                        
                msa_info = artifacts.get('msa', {})
                if msa_info:
                    msa_path = msa_info.get('full_path') or msa_info.get('path')
                    if msa_path:
                        msa_file = Path(msa_path)
        except Exception as e:
            print(f"⚠️  读取manifest失败: {e}")
    
    # 如果没有从manifest找到，使用workspace和prefix
    if not nwk_file and args.workspace and args.prefix:
        nwk_file = args.workspace / "final" / f"{args.prefix}.tree.nwk"
    
    if not msa_file and args.workspace and args.prefix:
        msa_file = args.workspace / "final" / f"{args.prefix}.pep.msa.fa"
    
    # 检查文件是否存在
    if not nwk_file or not nwk_file.exists():
        print(f"❌ 树文件不存在: {nwk_file}")
        with open(log_file, 'a', encoding='utf-8') as f_log:
            f_log.write(f"错误: 树文件不存在: {nwk_file}\n")
        return 1
    
    if not msa_file or not msa_file.exists():
        print(f"⚠️  MSA文件不存在: {msa_file}")
        with open(log_file, 'a', encoding='utf-8') as f_log:
            f_log.write(f"警告: MSA文件不存在: {msa_file}\n")
    
    # ========== 绘制进化树 ==========
    print(f"📊 绘制进化树: {nwk_file}")
    r_script_dir = Path(__file__).parent.parent.parent / "scripts"
    plot_tree_r = r_script_dir / "plot_tree.R"
    
    if not plot_tree_r.exists():
        # 如果脚本不存在，使用当前目录的脚本
        plot_tree_r = Path("plot_tree.R")
    
    if not plot_tree_r.exists():
        print(f"❌ R脚本不存在: {plot_tree_r}")
        with open(log_file, 'a', encoding='utf-8') as f_log:
            f_log.write(f"错误: R脚本不存在: {plot_tree_r}\n")
        return 1
    
    # 运行进化树绘制
    try:
        cmd = ["Rscript", str(plot_tree_r), str(nwk_file)]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=nwk_file.parent,
            timeout=300
        )
        
        with open(log_file, 'a', encoding='utf-8') as f_log:
            f_log.write(f"R脚本输出:\n{result.stdout}\n")
            if result.stderr:
                f_log.write(f"R脚本错误:\n{result.stderr}\n")
        
        print(f"   返回码: {result.returncode}")
        if result.returncode != 0:
            print(f"⚠️  进化树绘制可能有问题，检查日志")
    except Exception as e:
        print(f"❌ 进化树绘制失败: {e}")
        with open(log_file, 'a', encoding='utf-8') as f_log:
            f_log.write(f"异常: {e}\n")
        return 1
    
    # ========== 绘制MSA图 ==========
    if msa_file and msa_file.exists():
        print(f"📊 绘制MSA图: {msa_file}")
        
        # 查找MSA绘制R脚本
        msa_r_script = r_script_dir / "draw_msa_from_file.R"
        if not msa_r_script.exists():
            msa_r_script = Path("draw_msa_from_file.R")
        
        if msa_r_script.exists():
            try:
                cmd = ["Rscript", str(msa_r_script), str(msa_file)]
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    cwd=msa_file.parent,
                    timeout=300
                )
                
                with open(log_file, 'a', encoding='utf-8') as f_log:
                    f_log.write(f"MSA绘制输出:\n{result.stdout}\n")
                    if result.stderr:
                        f_log.write(f"MSA绘制错误:\n{result.stderr}\n")
                
                print(f"   返回码: {result.returncode}")
            except Exception as e:
                print(f"⚠️  MSA绘制失败: {e}")
        else:
            print(f"⚠️  MSA绘制R脚本未找到: {msa_r_script}")
    
    # ========== 检查输出文件 ==========
    print(f"📁 检查输出文件...")
    
    # 进化树输出目录
    tree_output_dir = nwk_file.parent / f"{nwk_file.stem}_output"
    
    # MSA输出目录
    msa_output_dir = None
    if msa_file and msa_file.exists():
        msa_output_dir = msa_file.parent / f"{msa_file.stem}_msa_output"
    
    output_files = []
    
    if tree_output_dir.exists():
        tree_files = list(tree_output_dir.glob("*"))
        output_files.extend(tree_files)
        print(f"   进化树输出: {len(tree_files)}个文件")
    
    if msa_output_dir and msa_output_dir.exists():
        msa_files = list(msa_output_dir.glob("*"))
        output_files.extend(msa_files)
        print(f"   MSA输出: {len(msa_files)}个文件")
    
    # 生成简单的manifest
    output_manifest = {
        "stage": "tree_plot",
        "timestamp": datetime.now().isoformat(),
        "status": "success" if output_files else "partial",
        "input_files": {
            "tree": str(nwk_file),
            "msa": str(msa_file) if msa_file and msa_file.exists() else None
        },
        "output_dirs": {
            "tree_output": str(tree_output_dir) if tree_output_dir.exists() else None,
            "msa_output": str(msa_output_dir) if msa_output_dir and msa_output_dir.exists() else None
        },
        "artifacts": {
            "tree_plot_pdf": [],
            "tree_plot_png": [],
            "msa_plot_png": [],
            "msa_interactive_html": []
        }
    }
    
    # 分类记录文件
    for file_path in output_files:
        if "rectangular_tree.pdf" in file_path.name:
            output_manifest["artifacts"]["tree_plot_pdf"].append(str(file_path))
        elif "circular_tree.pdf" in file_path.name:
            output_manifest["artifacts"]["tree_plot_pdf"].append(str(file_path))
        elif "_rectangular_tree.png" in file_path.name:
            output_manifest["artifacts"]["tree_plot_png"].append(str(file_path))
        elif "_circular_tree.png" in file_path.name:
            output_manifest["artifacts"]["tree_plot_png"].append(str(file_path))
        elif "_ggmsa_plot.png" in file_path.name:
            output_manifest["artifacts"]["msa_plot_png"].append(str(file_path))
        elif "_interactive.html" in file_path.name:
            output_manifest["artifacts"]["msa_interactive_html"].append(str(file_path))
    
    # 保存manifest
    manifest_path = nwk_file.parent / f"{nwk_file.stem}_plot.manifest.json"
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(output_manifest, f, indent=2, ensure_ascii=False)
    
    print(f"✅ 绘图完成")
    print(f"   输出文件: {len(output_files)}个")
    print(f"   Manifest: {manifest_path}")
    
    with open(log_file, 'a', encoding='utf-8') as f_log:
        f_log.write("\n" + "=" * 80 + "\n")
        f_log.write(f"生成文件: {len(output_files)}个\n")
        f_log.write(f"完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
