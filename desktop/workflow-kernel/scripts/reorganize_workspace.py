#!/usr/bin/env python3
"""
Workspace 目录重构脚本
将 final/ 目录内容分类整理到 figures/, tables/, reports/ 子目录
"""

import os
import shutil
import json
from pathlib import Path
from datetime import datetime


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def reorganize_workspace(job_id: str, workspace_root: str = str(PROJECT_ROOT / "workspace")):
    """
    重构 workspace/{job_id}/final/ 目录结构
    
    新结构:
    final/
    ├── figures/          # PNG, PDF 图片
    ├── tables/           # CSV, TSV, FASTA 数据
    ├── reports/          # MD, JSON 报告
    └── manifest.json     # 汇总清单
    """
    
    final_dir = Path(workspace_root) / job_id / "final"
    
    if not final_dir.exists():
        print(f"❌ Directory not found: {final_dir}")
        return False
    
    # 创建子目录
    figures_dir = final_dir / "figures"
    tables_dir = final_dir / "tables"
    reports_dir = final_dir / "reports"
    
    figures_dir.mkdir(exist_ok=True)
    tables_dir.mkdir(exist_ok=True)
    reports_dir.mkdir(exist_ok=True)
    
    print(f"📁 Reorganizing: {final_dir}")
    
    # 文件分类
    figures = []
    tables = []
    reports = []
    
    for item in final_dir.iterdir():
        if item.is_dir():
            # 处理特殊目录
            if "motif_logo" in item.name.lower():
                # 移动到 tables 目录
                shutil.move(str(item), str(tables_dir / item.name))
                tables.append(item.name)
                print(f"  📋 Table Directory: {item.name}")
            continue  # 跳过已创建的子目录
            
        suffix = item.suffix.lower()
        name = item.name
        
        # 分类
        if suffix in ['.png', '.svg']:
            shutil.move(str(item), str(figures_dir / name))
            figures.append(name)
            print(f"  📊 Figure: {name}")
            
        elif suffix in ['.pdf']:
            # PDF 可能是图也可能是报告，根据文件名判断
            if any(keyword in name.lower() for keyword in ['report', 'manifest']):
                shutil.move(str(item), str(reports_dir / name))
                reports.append(name)
                print(f"  📄 Report: {name}")
            else:
                shutil.move(str(item), str(figures_dir / name))
                figures.append(name)
                print(f"  📊 Figure: {name}")
                
        elif suffix in ['.csv', '.tsv', '.fa', '.fasta', '.id', '.nwk', '.msa']:
            shutil.move(str(item), str(tables_dir / name))
            tables.append(name)
            print(f"  📋 Table: {name}")
            
        elif suffix in ['.md', '.json', '.txt']:
            shutil.move(str(item), str(reports_dir / name))
            reports.append(name)
            print(f"  📄 Report: {name}")
        else:
            # 其他文件保留在根目录
            print(f"  📦 Other: {name}")
    
    # 生成新的 manifest.json
    manifest = {
        "manifest_version": "2.0",
        "job_id": job_id,
        "reorganized_at": datetime.now().isoformat(),
        "structure": {
            "figures": figures,
            "tables": tables,
            "reports": reports
        },
        "directories": {
            "figures": str(figures_dir.relative_to(final_dir)),
            "tables": str(tables_dir.relative_to(final_dir)),
            "reports": str(reports_dir.relative_to(final_dir))
        }
    }
    
    manifest_path = final_dir / "manifest.json"
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ Reorganization complete!")
    print(f"   Figures: {len(figures)} files")
    print(f"   Tables: {len(tables)} files")
    print(f"   Reports: {len(reports)} files")
    print(f"   Manifest: {manifest_path}")
    
    return True


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python reorganize_workspace.py <job_id>")
        print("Example: python reorganize_workspace.py job_1773147030349")
        sys.exit(1)
    
    job_id = sys.argv[1]
    reorganize_workspace(job_id)
