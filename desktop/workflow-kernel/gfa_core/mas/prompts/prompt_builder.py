"""
Prompt Builder for ResultParserAgent and DiscoveryAgent
基于 stage_examples.py 动态组装 Prompt
"""

from typing import Dict, Optional
from .stage_examples import get_stage_example, get_stage_name


# 基础指令模板
RESULT_PARSER_BASE = """你是 ResultParserAgent，负责解读【{stage_name}】阶段的结果。

【任务目标】
对基因家族分析数据进行专业、准确的生物学解读，验证用户假设，并给出明确的下一步建议。

【输出格式要求】
必须严格按以下三部分输出：

1. 【解读】
   - 用1-2句话解释核心数据的生物学意义
   - 结合具体数字和比较对象（如近缘物种）
   - 指出数据反映的进化或功能特征

2. 【假设验证】
   - 使用 ✅ ❌ ⚠️ 标记对用户假设的支持程度
   - ✅ 支持：数据明确支持假设
   - ❌ 不支持：数据明确反驳假设
   - ⚠️ 部分支持/需要更多信息：数据部分支持或存在不确定性

3. 【建议】
   - 明确指出下一步该做哪个阶段（如"建议下一步进行染色体分布分析"）
   - 说明为什么建议这个阶段（简要理由）
   - 如果有多条建议，按优先级列出

【注意事项】
- 解读要具体，避免泛泛而谈
- 假设验证要基于数据，不要过度推断
- 建议要可操作，明确指出阶段名称
"""

DISCOVERY_BASE = """你是 DiscoveryAgent，负责从【{stage_name}】阶段的结果中发现异常、挖掘机会、构建故事线。

【任务目标】
像一位经验丰富的科学家一样，从数据中识别异常模式、发现潜在机会、提炼科学洞察，为构建完整的研究故事提供素材。

【输出格式要求】
必须严格按以下三部分输出：

1. 【异常/机会发现】
   - 识别数据中的"不对劲"之处（如与预期不符的模式）
   - 发现潜在的研究机会或值得深入探索的方向
   - 指出可能被忽视的细节或矛盾

2. 【故事线素材/整合】
   - 提炼可用于构建科学叙事的关键证据
   - 说明这个发现如何融入更大的研究故事
   - 指出与其他阶段结果的潜在联系

3. 【深度洞察】
   - 提供超越数据本身的科学思考
   - 连接分子机制与进化/生物学意义
   - 提出可能的研究假设或值得验证的猜想

【思考角度】
- 数据是否与文献报道的常见模式一致？
- 是否存在"边缘"或"异常"的个案值得单独审视？
- 这个结果如何支持或挑战现有的生物学认知？
- 从进化角度，这个结果意味着什么？
"""


def detect_pattern_type(stage_key: str, results: Dict) -> str:
    """
    根据阶段结果自动判断模式类型
    
    Args:
        stage_key: 阶段key
        results: 阶段分析结果
    
    Returns:
        pattern_type: "expansion" | "conservation" | "contraction"
    """
    # 阶段1: 基因家族成员鉴定 - 基于成员数量判断
    if stage_key == "gene_identification":
        count = results.get("member_count", 0)
        reference = results.get("reference_count", 10)
        if count > reference * 1.5:
            return "expansion"
        elif count < reference * 0.5:
            return "contraction"
        else:
            return "conservation"
    
    # 阶段2: 系统发育分析 - 基于分支数量判断
    elif stage_key == "phylogenetic_analysis":
        branches = results.get("branch_count", 0)
        members = results.get("member_count", 1)
        if branches > members * 0.3:
            return "expansion"
        elif branches < members * 0.1:
            return "conservation"
        else:
            return "conservation"
    
    # 阶段3: 基因结构分析 - 基于结构多样性判断
    elif stage_key == "gene_structure":
        diversity = results.get("intron_diversity", 0.5)
        if diversity > 0.7:
            return "expansion"
        elif diversity < 0.3:
            return "conservation"
        else:
            return "conservation"
    
    # 阶段4: 保守基序与结构域分析
    elif stage_key == "motif_domain":
        unique_motifs = results.get("unique_motif_count", 0)
        total_motifs = results.get("total_motif_count", 1)
        if unique_motifs / total_motifs > 0.3:
            return "expansion"
        else:
            return "conservation"
    
    # 阶段5: 染色体定位与分布
    elif stage_key == "chromosomal_localization":
        clusters = results.get("gene_cluster_count", 0)
        if clusters > 3:
            return "expansion"
        else:
            return "conservation"
    
    # 阶段6: 共线性与复制事件分析
    elif stage_key == "collinearity_duplication":
        duplication_events = results.get("duplication_event_count", 0)
        if duplication_events > 5:
            return "expansion"
        elif duplication_events == 0:
            return "conservation"
        else:
            return "conservation"
    
    # 阶段7: 基因表达谱分析
    elif stage_key == "expression_profile":
        specific_genes = results.get("tissue_specific_genes", 0)
        total = results.get("total_genes", 1)
        if specific_genes / total > 0.3:
            return "expansion"
        else:
            return "conservation"
    
    # 阶段8: 启动子顺式作用元件分析
    elif stage_key == "promoter_analysis":
        element_diversity = results.get("cis_element_diversity", 0.5)
        if element_diversity > 0.7:
            return "expansion"
        else:
            return "conservation"
    
    # 阶段9: 选择压力分析（Ka/Ks）
    elif stage_key == "kaks_analysis":
        positive_selection = results.get("positive_selection_count", 0)
        if positive_selection > 5:
            return "expansion"
        else:
            return "conservation"
    
    # 阶段10: 综合可视化
    elif stage_key == "comprehensive_visualization":
        complexity = results.get("visualization_complexity", 0.5)
        if complexity > 0.7:
            return "expansion"
        else:
            return "conservation"
    
    # 阶段11: 蛋白质互作网络预测
    elif stage_key == "ppi_prediction":
        network_nodes = results.get("network_node_count", 0)
        if network_nodes > 50:
            return "expansion"
        else:
            return "conservation"
    
    # 阶段12: GO/KEGG富集分析
    elif stage_key == "go_kegg_enrichment":
        pathway_diversity = results.get("enriched_pathway_count", 0)
        if pathway_diversity > 10:
            return "expansion"
        else:
            return "conservation"
    
    # 阶段13: 共表达网络分析
    elif stage_key == "coexpression_network":
        modules = results.get("wgcna_module_count", 0)
        if modules > 5:
            return "expansion"
        else:
            return "conservation"
    
    # 默认: expansion（最常见的情况）
    return "expansion"


def build_result_parser_prompt(
    stage_key: str,
    results: Dict,
    user_hypothesis: Optional[str] = None,
    custom_pattern: Optional[str] = None
) -> str:
    """
    构建 ResultParserAgent 的 Prompt
    
    Args:
        stage_key: 阶段key，如 "gene_identification"
        results: 阶段分析结果数据
        user_hypothesis: 用户的假设（可选）
        custom_pattern: 强制指定模式类型（可选）
    
    Returns:
        组装好的 Prompt 字符串
    """
    # 获取阶段名称
    stage_name = get_stage_name(stage_key)
    
    # 判断模式类型
    pattern_type = custom_pattern or detect_pattern_type(stage_key, results)
    
    # 获取示例
    try:
        example = get_stage_example(stage_key, pattern_type, "result_parser")
    except ValueError:
        # 如果指定模式不存在，使用 expansion
        pattern_type = "expansion"
        example = get_stage_example(stage_key, pattern_type, "result_parser")
    
    # 组装基础指令
    base = RESULT_PARSER_BASE.format(stage_name=stage_name)
    
    # 组装示例部分
    example_section = f"""
【参考示例】（{pattern_type} 模式）
输入：{example.get('input', '请提供具体数据')}
输出：
【解读】{example['interpretation']}
【假设验证】{example['hypothesis_validation']}
【建议】{example['recommendation']}
"""
    
    # 组装当前任务
    user_input = f"输入数据：{results}"
    if user_hypothesis:
        user_input += f"\n用户假设：{user_hypothesis}"
    
    task_section = f"""
【当前任务】
{user_input}

请按上述格式输出：
"""
    
    return base + example_section + task_section


def build_discovery_prompt(
    stage_key: str,
    results: Dict,
    context: Optional[Dict] = None,
    custom_pattern: Optional[str] = None
) -> str:
    """
    构建 DiscoveryAgent 的 Prompt
    
    Args:
        stage_key: 阶段key
        results: 阶段分析结果数据
        context: 上下文信息（如其他阶段结果）
        custom_pattern: 强制指定模式类型
    
    Returns:
        组装好的 Prompt 字符串
    """
    # 获取阶段名称
    stage_name = get_stage_name(stage_key)
    
    # 判断模式类型
    pattern_type = custom_pattern or detect_pattern_type(stage_key, results)
    
    # 获取示例
    try:
        example = get_stage_example(stage_key, pattern_type, "discovery")
    except ValueError:
        pattern_type = "expansion"
        example = get_stage_example(stage_key, pattern_type, "discovery")
    
    # 组装基础指令
    base = DISCOVERY_BASE.format(stage_name=stage_name)
    
    # 组装示例部分
    example_section = f"""
【参考示例】（{pattern_type} 模式）
输入：{example.get('input', '请提供具体数据')}
输出：
【异常/机会发现】{example.get('anomaly', '未发现明显异常')}
【故事线素材/整合】{example.get('storyline', '待补充')}
【深度洞察】{example.get('insight', '待补充')}
"""
    
    # 组装当前任务
    user_input = f"输入数据：{results}"
    if context:
        user_input += f"\n上下文信息：{context}"
    
    task_section = f"""
【当前任务】
{user_input}

请按上述格式输出：
"""
    
    return base + example_section + task_section


# 便捷函数
def build_agent_prompt(
    agent_type: str,
    stage_key: str,
    results: Dict,
    **kwargs
) -> str:
    """
    通用 Prompt 构建函数
    
    Args:
        agent_type: "result_parser" 或 "discovery"
        stage_key: 阶段key
        results: 分析结果
        **kwargs: 其他参数
    
    Returns:
        Prompt 字符串
    """
    if agent_type == "result_parser":
        return build_result_parser_prompt(stage_key, results, **kwargs)
    elif agent_type == "discovery":
        return build_discovery_prompt(stage_key, results, **kwargs)
    else:
        raise ValueError(f"Unknown agent_type: {agent_type}")


# 测试函数
if __name__ == "__main__":
    # 测试 ResultParserAgent
    test_results = {"member_count": 50, "reference_count": 10}
    prompt = build_result_parser_prompt(
        "gene_identification",
        test_results,
        user_hypothesis="SPL家族在大豆中发生扩张"
    )
    print("=" * 50)
    print("ResultParserAgent Prompt (前1000字符):")
    print(prompt[:1000])
    print("=" * 50)
    print(f"\n总长度: {len(prompt)} 字符")
    print(f"预估 Token 数: ~{len(prompt) // 4} tokens")
