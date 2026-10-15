#!/usr/bin/env python3
"""
使用 matplotlib + Bio.Phylo 绘制进化树 (无头模式)
"""
import sys
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'  # 无头模式
os.environ['MPLBACKEND'] = 'Agg'  # 使用Agg后端

from pathlib import Path
import matplotlib
matplotlib.use('Agg')  # 无头后端
import matplotlib.pyplot as plt
from Bio import Phylo

def plot_tree(treefile, output_dir):
    """绘制进化树并输出PDF和PNG"""
    treefile = Path(treefile)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 读取树 (Newick格式)
    tree = Phylo.read(str(treefile), "newick")
    
    # 输出文件名
    base_name = treefile.stem.replace('.iqtree', '').replace('.treefile', '')
    pdf_file = output_dir / f"{base_name}.pdf"
    png_file = output_dir / f"{base_name}.png"
    
    # 创建图形
    fig, ax = plt.subplots(figsize=(14, 10))
    
    # 绘制进化树
    Phylo.draw(tree, axes=ax, do_show=False, 
               branch_labels=lambda c: c.confidence if c.confidence else None)
    
    # 设置标题
    ax.set_title("Interspecies Phylogenetic Tree", fontsize=14, fontweight='bold')
    
    # 保存PDF
    plt.savefig(str(pdf_file), format='pdf', bbox_inches='tight', dpi=300)
    print(f"PDF: {pdf_file}")
    
    # 保存PNG
    plt.savefig(str(png_file), format='png', bbox_inches='tight', dpi=300)
    print(f"PNG: {png_file}")
    
    plt.close()
    print("完成")
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python plot_tree.py <treefile> [output_dir]")
        sys.exit(1)
    
    treefile = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) >= 3 else Path(treefile).parent
    
    plot_tree(treefile, output_dir)
