"""
Stage Examples for ResultParserAgent and DiscoveryAgent
基于 /home/xia-lab/wutx/15projectXiuding/24_result_discovery_3state.md 转换

每个阶段包含3种模式：
- expansion（扩张）
- conservation（保守）
- contraction（收缩）

每个模式包含2个Agent的示例：
- result_parser
- discovery
"""

from typing import Dict, List

# 导入所有阶段
from .stage_01_gene_identification import STAGE_GENE_IDENTIFICATION
from .stage_02_phylogenetic import STAGE_02_PHYLOGENETIC as STAGE_PHYLOGENETIC_ANALYSIS
from .stage_03_gene_structure import STAGE_03_GENE_STRUCTURE as STAGE_GENE_STRUCTURE
from .stage_04_motif_domain import STAGE_04_MOTIF_DOMAIN as STAGE_MOTIF_DOMAIN
from .stage_05_chromosomal import STAGE_05_CHROMOSOMAL as STAGE_CHROMOSOMAL
from .stage_06_collinearity import STAGE_06_COLLINEARITY as STAGE_COLLINEARITY
from .stage_07_expression import STAGE_07_EXPRESSION as STAGE_EXPRESSION
from .stage_08_promoter import STAGE_08_PROMOTER as STAGE_PROMOTER
from .stage_09_kaks import STAGE_09_KAKS as STAGE_KAKS
from .stage_10_visualization import STAGE_10_VISUALIZATION as STAGE_VISUALIZATION
from .stage_11_ppi import STAGE_11_PPI as STAGE_PPI
from .stage_12_enrichment import STAGE_12_ENRICHMENT as STAGE_ENRICHMENT
from .stage_13_coexpression import STAGE_13_COEXPRESSION as STAGE_COEXPRESSION

# 阶段映射表
STAGE_EXAMPLES = {
    "gene_identification": STAGE_GENE_IDENTIFICATION,
    "phylogenetic_analysis": STAGE_PHYLOGENETIC_ANALYSIS,
    "gene_structure": STAGE_GENE_STRUCTURE,
    "motif_domain": STAGE_MOTIF_DOMAIN,
    "chromosomal_localization": STAGE_CHROMOSOMAL,
    "collinearity_duplication": STAGE_COLLINEARITY,
    "expression_profile": STAGE_EXPRESSION,
    "promoter_analysis": STAGE_PROMOTER,
    "kaks_analysis": STAGE_KAKS,
    "comprehensive_visualization": STAGE_VISUALIZATION,
    "ppi_prediction": STAGE_PPI,
    "go_kegg_enrichment": STAGE_ENRICHMENT,
    "coexpression_network": STAGE_COEXPRESSION,
}

# 阶段列表
STAGE_KEYS = list(STAGE_EXAMPLES.keys())

# 模式类型
PATTERN_TYPES = ["expansion", "conservation", "contraction"]

# Agent类型
AGENT_TYPES = ["result_parser", "discovery"]


def get_stage_example(stage_key: str, pattern_type: str, agent_type: str) -> dict:
    """
    获取指定阶段、模式、Agent类型的示例
    
    Args:
        stage_key: 阶段key，如 "gene_identification"
        pattern_type: 模式类型，如 "expansion", "conservation", "contraction"
        agent_type: Agent类型，如 "result_parser", "discovery"
    
    Returns:
        示例字典
    """
    if stage_key not in STAGE_EXAMPLES:
        raise ValueError(f"Unknown stage: {stage_key}")
    
    stage = STAGE_EXAMPLES[stage_key]
    
    if pattern_type not in stage["patterns"]:
        pattern_type = "expansion"  # 默认使用expansion
    
    pattern = stage["patterns"][pattern_type]
    
    if agent_type not in pattern:
        raise ValueError(f"Unknown agent_type: {agent_type} for stage {stage_key}")
    
    return pattern[agent_type]


def get_stage_name(stage_key: str) -> str:
    """获取阶段中文名称"""
    if stage_key not in STAGE_EXAMPLES:
        return stage_key
    return STAGE_EXAMPLES[stage_key]["stage_name"]


def list_all_stages() -> List[Dict]:
    """列出所有可用阶段"""
    return [
        {"key": key, "name": STAGE_EXAMPLES[key]["stage_name"]}
        for key in STAGE_KEYS
    ]
