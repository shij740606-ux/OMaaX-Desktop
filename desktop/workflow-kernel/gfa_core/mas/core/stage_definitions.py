"""
统一的阶段定义模块
所有组件（IntentAgentV2, PlanningAgentV2, FastIntent, 前端）都应该引用这里的定义
"""

# 所有分析阶段的标准依赖定义
# 这是唯一正确的依赖定义源，所有组件都必须引用这里
STAGE_DEPENDENCIES = {
    # 基准阶段（无依赖）
    "identify": [],
    "identify_refine": [],
    "genome_prepare": [],
    "prepare_organize": [],
    "prepare_normalize_gff": [],
    "prepare_longest_transcript": [],
    "prepare_extract_pep": [],
    "prepare_extract_cds": [],
    "prepare_filter_fasta": [],
    "prepare_simplify_ncbi": [],
    "rnaseq_qc": [],
    
    # 第二层（依赖 identify）
    "phylogeny": ["identify"],
    "synteny": ["identify"],
    "pep": ["identify"],
    "msa": ["identify"],
    
    # 第三层（依赖 identify + phylogeny）
    "motif": ["identify", "phylogeny"],
    "domain": ["identify", "phylogeny"],
    "structure": ["identify", "phylogeny"],
    "promoter": ["identify", "phylogeny"],
    "expression": ["identify", "phylogeny"],
    "enrichment": ["identify", "phylogeny"],
    "ortholog": ["identify", "phylogeny"],
    "interspecies_synteny": ["identify"],
    
    # 第四层（依赖 identify + synteny）
    "karyotype": ["identify", "synteny"],
    
    # 第五层（依赖 identify）
    "ppi": ["identify", "interspecies_synteny"],
    
    # 第六层（依赖 identify + phylogeny + interspecies_synteny）
    "kaks": ["identify", "interspecies_synteny"],
    
    # 组合图（依赖 identify + phylogeny + motif + structure + domain）
    "combo": ["identify", "phylogeny", "motif", "structure", "domain"],
    
    # RNA-seq 流程（严格串行）
    "rnaseq_align": ["rnaseq_qc"],
    "rnaseq_quant": ["rnaseq_align"],
    "rnaseq_sample_qc": ["rnaseq_quant"],
    "rnaseq_de": ["rnaseq_sample_qc"],
    "rnaseq_wgcna": ["rnaseq_sample_qc"],
    "annotation": [],
    "rnaseq_go_enrich": ["rnaseq_de"],
    "rnaseq_kegg_enrich": ["rnaseq_de"],
    "rnaseq_enrich": ["rnaseq_de"],
    "rnaseq_fgsea": ["rnaseq_de"],
    "rnaseq_candidate_family": ["rnaseq_de", "rnaseq_go_enrich", "rnaseq_kegg_enrich", "rnaseq_fgsea"],
}

# 可并行执行的阶段组
# 同一组内的阶段在依赖满足后可以并行执行
PARALLEL_GROUPS = [
    ["phylogeny", "pep", "synteny", "interspecies_synteny"],
    ["motif", "domain", "structure", "promoter", "expression", "ortholog"],
    ["karyotype"],
    ["kaks", "ppi"],
]
# 阶段执行顺序（用于排序）
STAGE_ORDER = [
    "identify",
    "identify_refine",
    "genome_prepare",
    "prepare_organize",
    "prepare_normalize_gff",
    "prepare_longest_transcript",
    "prepare_extract_pep",
    "prepare_extract_cds",
    "prepare_filter_fasta",
    "prepare_simplify_ncbi",
    "rnaseq_qc",
    "phylogeny",
    "synteny",
    "pep",
    "interspecies_synteny",
    "msa",
    "motif",
    "domain",
    "structure",
    "promoter",
    "expression",
    "enrichment",
    "ortholog",
    "karyotype",
    "ppi",
    "kaks",
    "combo",
    "rnaseq_align",
    "rnaseq_quant",
    "rnaseq_sample_qc",
    "rnaseq_wgcna",
    "rnaseq_de",
    "annotation",
    "rnaseq_go_enrich",
    "rnaseq_kegg_enrich",
    "rnaseq_fgsea",
    "rnaseq_candidate_family",
    "rnaseq_enrich",
]

# 阶段基本信息
STAGE_INFO = {
    "identify": {"name": "基因家族鉴定", "estimated_time": 5, "cpu": 4},
    "identify_refine": {"name": "修订鉴定成员", "estimated_time": 5, "cpu": 4},
    "phylogeny": {"name": "系统发育分析", "estimated_time": 30, "cpu": 6},
    "motif": {"name": "Motif分析", "estimated_time": 20, "cpu": 4},
    "domain": {"name": "结构域分析", "estimated_time": 15, "cpu": 4},
    "structure": {"name": "基因结构分析", "estimated_time": 10, "cpu": 4},
    "promoter": {"name": "启动子分析", "estimated_time": 20, "cpu": 4},
    "synteny": {"name": "种内共线性分析", "estimated_time": 30, "cpu": 6},
    "karyotype": {"name": "核型分析", "estimated_time": 10, "cpu": 4},
    "interspecies_synteny": {"name": "物种间共线性", "estimated_time": 40, "cpu": 6},
    "expression": {"name": "表达分析", "estimated_time": 15, "cpu": 4},
    "kaks": {"name": "Ka/Ks分析", "estimated_time": 20, "cpu": 4},
    "ppi": {"name": "PPI分析", "estimated_time": 10, "cpu": 4},
    "pep": {"name": "蛋白特征分析", "estimated_time": 10, "cpu": 4},
    "ortholog": {"name": "直系同源基因鉴定", "estimated_time": 15, "cpu": 4},
    "msa": {"name": "多序列比对", "estimated_time": 10, "cpu": 4},
    "enrichment": {"name": "富集分析", "estimated_time": 15, "cpu": 4},
    "combo": {"name": "组合图", "estimated_time": 10, "cpu": 4},
    "genome_prepare": {"name": "基因组准备", "estimated_time": 10, "cpu": 4},
    "prepare_organize": {"name": "数据文件整理", "estimated_time": 5, "cpu": 4},
    "prepare_normalize_gff": {"name": "GFF ID规范化", "estimated_time": 5, "cpu": 4},
    "prepare_longest_transcript": {"name": "提取最长转录本", "estimated_time": 10, "cpu": 4},
    "prepare_extract_pep": {"name": "从GFF提取PEP", "estimated_time": 10, "cpu": 4},
    "prepare_extract_cds": {"name": "从GFF提取CDS", "estimated_time": 10, "cpu": 4},
    "prepare_filter_fasta": {"name": "按ID提取FASTA", "estimated_time": 5, "cpu": 4},
    "prepare_simplify_ncbi": {"name": "NCBI ID简化", "estimated_time": 5, "cpu": 4},
    "rnaseq_qc": {"name": "RNA-seq质控", "estimated_time": 30, "cpu": 8},
    "rnaseq_align": {"name": "RNA-seq比对", "estimated_time": 60, "cpu": 8},
    "rnaseq_quant": {"name": "RNA-seq定量", "estimated_time": 30, "cpu": 6},
    "rnaseq_sample_qc": {"name": "RNA-seq样本质量评估", "estimated_time": 10, "cpu": 4},
    "rnaseq_de": {"name": "差异表达分析（自动生成FPKM表达图）", "estimated_time": 20, "cpu": 4},
    "rnaseq_wgcna": {"name": "RNA-seq WGCNA分析", "estimated_time": 30, "cpu": 4},
    "annotation": {"name": "背景注释", "estimated_time": 180, "cpu": 8},
    "rnaseq_go_enrich": {"name": "RNA-seq GO富集分析", "estimated_time": 15, "cpu": 4},
    "rnaseq_kegg_enrich": {"name": "RNA-seq KEGG富集分析", "estimated_time": 15, "cpu": 4},
    "rnaseq_enrich": {"name": "富集分析", "estimated_time": 15, "cpu": 4},
    "rnaseq_fgsea": {"name": "RNA-seq fgsea分析", "estimated_time": 15, "cpu": 4},
    "rnaseq_candidate_family": {"name": "RNA-seq候选家族筛选", "estimated_time": 20, "cpu": 4},
}


def get_stage_dependencies(stage: str) -> list:
    """获取阶段的依赖列表"""
    return STAGE_DEPENDENCIES.get(stage, [])


def get_all_dependencies(stage: str, visited=None) -> list:
    """递归获取阶段的所有依赖（包括间接依赖）"""
    if visited is None:
        visited = set()
    
    if stage in visited:
        return []
    
    visited.add(stage)
    deps = STAGE_DEPENDENCIES.get(stage, [])
    all_deps = []
    
    for dep in deps:
        all_deps.extend(get_all_dependencies(dep, visited))
        if dep not in all_deps:
            all_deps.append(dep)
    
    return all_deps


def complete_dependencies(stages: list) -> list:
    """
    根据依赖关系补充缺失的阶段，并按依赖顺序排序
    
    Args:
        stages: 用户请求的阶段列表
        
    Returns:
        完整的阶段列表（包含所有依赖）
    """
    required = set(stages)
    
    # 递归添加依赖
    changed = True
    while changed:
        changed = False
        for stage in list(required):
            deps = STAGE_DEPENDENCIES.get(stage, [])
            for dep in deps:
                if dep not in required:
                    required.add(dep)
                    changed = True
    
    # 按依赖顺序排序
    ordered = []
    remaining = required.copy()
    
    order_index = {stage: idx for idx, stage in enumerate(STAGE_ORDER)}

    while remaining:
        # 找没有未满足依赖的阶段
        ready = {s for s in remaining if all(d in ordered for d in STAGE_DEPENDENCIES.get(s, []))}
        if not ready:
            # 有循环依赖，直接添加剩余
            ordered.extend(sorted(remaining, key=lambda s: order_index.get(s, len(STAGE_ORDER))))
            break
        
        # 按原始顺序添加
        for s in stages:
            if s in ready:
                ordered.append(s)
                remaining.remove(s)
        
        # 添加不在原始列表中的依赖，按全局阶段顺序保持稳定
        for s in sorted(ready, key=lambda item: order_index.get(item, len(STAGE_ORDER))):
            if s not in stages:
                ordered.append(s)
                remaining.remove(s)
    
    return ordered


# Compatibility re-export.
# The dependency-free authoritative catalog lives outside gfa_core.mas.core to
# avoid circular imports through core/__init__.py.
from gfa_core.mas.stage_catalog import (  # noqa: E402,F401
    PARALLEL_GROUPS,
    STAGE_DEPENDENCIES,
    STAGE_INFO,
    STAGE_ORDER,
    complete_dependencies,
    get_all_dependencies,
    get_stage_dependencies,
)
