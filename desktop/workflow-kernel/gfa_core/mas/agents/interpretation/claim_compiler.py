"""Compile matched patterns into bounded local claims."""
from __future__ import annotations

from typing import Any, Dict, List

from .claim_critic import critique_claim
from .claim_schema import evidence_for_stages, make_claim, risks_for_stages


CLAIM_TITLES = {
    "family_membership_confidence": "成员集合与核心结构域可信度",
    "protein_architecture": "蛋白结构域与motif架构解释",
    "evolutionary_duplication": "成员级复制候选线索",
    "evolutionary_conservation": "跨物种保守性候选证据",
    "selection_pressure": "Ka/Ks选择压力候选线索",
    "subfamily_prediction_reference": "亚家族分类预测前置参考",
    "subfamily_assignment": "参考辅助亚家族归属框架",
    "protein_property_profile": "蛋白理化性质画像",
    "interaction_network": "候选蛋白互作网络",
    "candidate_response_gene": "候选处理响应成员",
    "functional_theme": "表达与富集支持的功能主题",
}


CLAIM_INTERPRETATIONS = {
    "family_membership_confidence": "该结论说明当前成员集合在鉴定阈值和结构域证据下具有可追溯性，适合作为后续系统发育、结构和进化分析的输入基础。",
    "protein_architecture": "该结论用于判断成员是否共享核心蛋白架构，并为后续筛选结构分化候选提供依据；它描述的是结构一致性或候选差异，而不是直接功能证明。",
    "evolutionary_duplication": "该结论把成员级共线性作为复制事件候选线索，需要结合染色体位置、复制类型和Ks分布进一步确认。",
    "evolutionary_conservation": "该结论说明目标与参考成员之间存在保守性线索，可支持跨物种比较解释，但不能直接推出功能完全等价。",
    "selection_pressure": "该结论只把Ka/Ks结果作为选择压力筛查线索；在CDS比对、Ks饱和和逐对结果复核前，不应写成正选择或适应性进化已被证明。",
    "subfamily_prediction_reference": "该结论用于说明亚家族分类预测这个前置步骤目前提供了哪些参考信息、以及为何尚不能形成确认分类；它适合作为用户后续补数据和复跑的诊断依据。",
    "subfamily_assignment": "该结论提供目标成员的亚家族归属框架，可用于组织后续功能讨论；低置信或混合分支成员需要单独标注。",
    "protein_property_profile": "该结论描述成员集合的长度、pI和疏水性等性质分布，可辅助筛选异常成员，但不能单独推断功能或定位。",
    "interaction_network": "该结论提供候选互作网络线索，适合用于优先级排序；预测互作仍需物种映射审计和实验验证。",
    "candidate_response_gene": "该结论把启动子、表达或RNA-seq证据合并为候选响应线索，适合进入后续表达验证和功能验证。",
    "functional_theme": "该结论说明表达变化和富集结果形成可解释主题，但富集偏倚不能单独证明调控因果关系。",
}


CLAIM_CAVEATS = {
    "family_membership_confidence": "边界成员、截短蛋白和阈值敏感成员仍需复核；成员数不应直接解释为扩张或收缩。",
    "protein_architecture": "缺少成员级motif排列矩阵或分支支持时，不能断言亚群功能分化。",
    "evolutionary_duplication": "全基因组block数量不能直接代表家族扩张；需要成员级配对和复制类型证据。",
    "evolutionary_conservation": "同源或共线性支持保守性候选，不等同于功能完全一致。",
    "selection_pressure": "Ka/Ks对比对质量、Ks饱和和CDS注释敏感，当前只能作为候选筛查。",
    "subfamily_prediction_reference": "当前只能作为低置信前置参考；若参考亚家族表、参考序列ID映射或联合FASTA不完整，不能确认目标成员亚家族归属。",
    "subfamily_assignment": "参考辅助分类依赖参考物种标签和树拓扑，低置信成员不能强行归类。",
    "protein_property_profile": "理化性质是描述性统计，不能单独证明蛋白功能。",
    "interaction_network": "PPI预测不等同于已验证物理互作。",
    "candidate_response_gene": "候选响应不等同于调控因果，需要qPCR、遗传材料或扰动实验验证。",
    "functional_theme": "富集结果说明集合偏倚或排序趋势，不直接证明具体通路被该家族调控。",
}


def _stage_data(bundles: Dict[str, Dict[str, Any]], stage: str) -> Dict[str, Any]:
    bundle = bundles.get(stage) or {}
    parsed = bundle.get("parsed") if isinstance(bundle.get("parsed"), dict) else {}
    metrics = bundle.get("metrics") if isinstance(bundle.get("metrics"), dict) else {}
    data = dict(metrics)
    data.update(parsed)
    return data


def _fmt(value: Any, default: str = "N/A") -> str:
    if value in (None, ""):
        return default
    if isinstance(value, float):
        return f"{value:.3g}"
    return str(value)


def _claim_text(match: Dict[str, Any], prefix: str, bundles: Dict[str, Dict[str, Any]]) -> str:
    pattern_id = match.get("pattern_id")
    identify = _stage_data(bundles, "identify")
    domain = _stage_data(bundles, "domain")
    motif = _stage_data(bundles, "motif")
    synteny = _stage_data(bundles, "synteny")
    inter = _stage_data(bundles, "interspecies_synteny")
    ortholog = _stage_data(bundles, "ortholog")
    kaks = _stage_data(bundles, "kaks")
    ref_class = _stage_data(bundles, "reference_classification")
    subfamily = _stage_data(bundles, "subfamily_confirm")
    pep = _stage_data(bundles, "pep")
    ppi = _stage_data(bundles, "ppi")
    if pattern_id == "family_member_confidence":
        return f"{prefix} 鉴定得到 {_fmt(identify.get('final_members'))} 个成员，HMM/BLAST与成员文件提供可追溯证据；成员规模解释仍应局限在当前输入和阈值范围内。"
    if pattern_id == "core_domain_support":
        return f"{prefix} 中 {_fmt(domain.get('proteins_with_domains') or domain.get('sbp_protein_count'))}/{_fmt(domain.get('proteins_total'))} 个蛋白具有结构域证据，显著domain hits为 {_fmt(domain.get('significant_domain_hits'))}，支持成员集合的核心结构域可信度。"
    if pattern_id == "conserved_architecture":
        conserved = motif.get("most_conserved_motif") if isinstance(motif.get("most_conserved_motif"), dict) else {}
        return f"{prefix} 同时具有 {_fmt(domain.get('significant_domain_hits'))} 个显著结构域hits和 {_fmt(motif.get('motif_count'))} 个motif；其中 {_fmt(conserved.get('id') or conserved.get('name'), '核心motif')} 覆盖 {_fmt(conserved.get('sites'))} 条序列，支持蛋白架构保守性候选。"
    if pattern_id == "architecture_divergence_candidate":
        phylogeny = _stage_data(bundles, "phylogeny")
        return f"{prefix} 的系统发育输入包含 {_fmt(phylogeny.get('sequence_count') or phylogeny.get('msa_sequence_count') or phylogeny.get('tree_tip_estimate'))} 条序列，并有结构域/motif证据支撑；可据此筛选候选结构分化成员，但不能直接断言亚群功能分化。"
    if pattern_id == "stress_response_candidate_gene":
        return f"{prefix} 中部分成员具有候选处理响应特征，适合进入后续表达和功能验证。"
    if pattern_id == "enrichment_supported_response_theme":
        return f"{prefix} 相关数据中的差异表达与富集结果共同支持功能层面的响应主题。"
    if pattern_id == "intraspecies_synteny_duplication_candidate":
        return f"{prefix} 的种内共线性在全基因组 {_fmt(synteny.get('synteny_blocks'))} 个block背景下，检测到 {_fmt(synteny.get('family_pair_count'))} 对家族成员级共线性候选，可作为复制事件线索而非家族扩张证明。"
    if pattern_id == "cross_species_conservation_candidate":
        return f"{prefix} 在目标-参考比较中检测到 {_fmt(inter.get('family_ref_pair_count') or inter.get('pairwise_family_highlight_links'))} 个成员级种间共线性/高亮线索，并有 {_fmt(ortholog.get('ortholog_pairs'))} 对直系同源配对作为补充，支持跨物种保守性候选。"
    if pattern_id == "orthology_supported_conservation":
        return f"{prefix} 检测到 {_fmt(ortholog.get('ortholog_pairs'))} 对直系同源配对，可作为跨物种保守性解释的成员级依据，但不能等同于功能完全一致。"
    if pattern_id == "kaks_selection_pressure_candidate":
        return f"{prefix} 的Ka/Ks分析成功计算 {_fmt(kaks.get('successful_pairs') or kaks.get('total_pairs'))} 对基因，平均Ka/Ks为 {_fmt(kaks.get('mean_ka_ks'))}、候选正选择计数为 {_fmt(kaks.get('positive_selection_count'))}；该结果应作为选择压力候选线索并先通过CDS/Ks质量复核。"
    if pattern_id == "subfamily_prediction_attempt":
        ref_bundle = bundles.get("reference_classification") or {}
        errors = ref_class.get("errors") or ref_bundle.get("errors") or []
        if not isinstance(errors, list):
            errors = [errors]
        error_text = "；主要问题: " + "；".join(str(item) for item in errors[:2] if item) if errors else ""
        status = ref_bundle.get("status") or ref_class.get("status") or "unknown"
        target_count = _fmt(ref_class.get("target_gene_count"))
        reference_count = _fmt(ref_class.get("reference_gene_count"))
        if status == "success" and target_count != "N/A":
            return (
                f"{prefix} 的参考辅助亚家族分类预测已执行，当前状态为 success；"
                f"覆盖目标成员 {target_count} 个、参考成员 {reference_count} 个。"
                f"该结果可作为基因家族分析前的参考分类建议，用于组织后续系统发育、结构域和motif解读；"
                f"在人工确认或分支支持复核前，不应表述为最终亚家族归属。"
            )
        return (
            f"{prefix} 的参考辅助亚家族分类预测已执行，当前状态为 {status}；"
            f"classifiable_species_count={_fmt(ref_class.get('classifiable_species_count'))}，"
            f"per_species_count={_fmt(ref_class.get('per_species_count'))}，"
            f"failed_species_count={_fmt(ref_class.get('failed_species_count'))}。"
            f"该结果只能作为基因家族分析前的低置信参考和输入诊断，不能作为目标成员亚家族归属确认。"
            f"{error_text}"
        )
    if pattern_id == "reference_subfamily_assignment":
        counts = subfamily.get("subfamily_counts") or ref_class.get("assigned_subfamily_counts") or {}
        if isinstance(counts, dict) and counts:
            count_text = "，".join(f"{k}:{v}" for k, v in list(counts.items())[:8])
        else:
            count_text = "未记录"
        confidence = ref_class.get("confidence_counts")
        confidence_text = "；置信度分布 " + ", ".join(f"{k}:{v}" for k, v in confidence.items()) if isinstance(confidence, dict) else ""
        return f"{prefix} 的参考辅助亚家族分析覆盖 {_fmt(subfamily.get('confirmed_genes') or ref_class.get('target_gene_count'))} 个目标成员，亚家族分布为 {count_text}{confidence_text}。"
    if pattern_id == "protein_property_profile":
        return f"{prefix} 的蛋白性质画像覆盖 {_fmt(pep.get('protein_count'))} 个蛋白，平均长度 {_fmt(pep.get('avg_length'))} aa，平均pI {_fmt(pep.get('avg_pi'))}，平均GRAVY {_fmt(pep.get('avg_gravy'))}；这些指标用于描述性质分布，不单独证明功能。"
    if pattern_id == "ppi_interaction_candidate":
        return f"{prefix} 的PPI结果包含 {_fmt(ppi.get('genes_in_network'))} 个网络基因和 {_fmt(ppi.get('total_interactions'))} 条互作，可作为候选网络线索，仍需物种映射审计和实验验证。"
    return str(match.get("allowed_claim") or "")


def _interpretation_level(claim_type: str) -> str:
    if claim_type == "statistical_finding":
        return "statistical_finding"
    if claim_type == "derived_stat":
        return "derived_stat"
    return "candidate_hypothesis"


def _evidence_chain(evidence: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    chain: List[Dict[str, str]] = []
    for item in evidence[:6]:
        if not isinstance(item, dict):
            continue
        chain.append({
            "stage": str(item.get("stage") or ""),
            "statement": str(item.get("statement") or item.get("evidence") or item.get("claim") or ""),
            "source_type": str(item.get("source_type") or ""),
            "source_file": str(item.get("source_file") or ""),
        })
    return [item for item in chain if item["statement"]]


def _evidence_summary(evidence: List[Dict[str, Any]]) -> str:
    parts = []
    for item in evidence[:3]:
        if not isinstance(item, dict):
            continue
        stage = str(item.get("stage") or "")
        statement = str(item.get("statement") or item.get("evidence") or "")
        if statement:
            parts.append(f"{stage}: {statement}" if stage else statement)
    return "；".join(parts)


def compile_claims(
    matches: List[Dict[str, Any]],
    *,
    bundles: Dict[str, Dict[str, Any]],
    evidence_cards: List[Dict[str, Any]],
    risk_flags: List[Dict[str, Any]],
    prefix: str,
) -> List[Dict[str, Any]]:
    claims: List[Dict[str, Any]] = []
    counters: Dict[str, int] = {}
    for match in matches:
        if match.get("status") != "matched":
            continue
        group = str(match.get("claim_group") or "local_claim")
        counters[group] = counters.get(group, 0) + 1
        stages = list(match.get("applicable_stages") or [])
        evidence = evidence_for_stages(evidence_cards, stages, limit=8)
        counter = risks_for_stages(risk_flags, stages)
        claim_type = str(match.get("claim_type") or "candidate_hypothesis")
        confidence = str(match.get("default_confidence") or "medium")
        if len(match.get("optional_satisfied") or []) >= 2 and confidence == "medium":
            confidence = "high"
        claim = make_claim(
            claim_id=f"{group}.{counters[group]:03d}",
            claim_group=group,
            claim_type=claim_type,
            claim=_claim_text(match, prefix, bundles),
            stages=stages,
            local_evidence=evidence,
            counter_evidence=counter,
            matched_patterns=[str(match.get("pattern_id") or "")],
            confidence=confidence,
            confidence_reason=(
                "required evidence satisfied: "
                + ", ".join(match.get("required_satisfied") or [])
                + ("; optional evidence: " + ", ".join(match.get("optional_satisfied") or []) if match.get("optional_satisfied") else "")
            ),
            limitations=[
                f"Forbidden overclaim: {match.get('forbidden_claim')}"
            ] if match.get("forbidden_claim") else [],
            next_validation=list(match.get("validation_needed") or []),
            interpretation_level=_interpretation_level(claim_type),
        )
        claim["title"] = str(match.get("name") or CLAIM_TITLES.get(group) or group)
        claim["evidence_chain"] = _evidence_chain(evidence)
        claim["evidence"] = _evidence_summary(evidence)
        claim["interpretation"] = CLAIM_INTERPRETATIONS.get(
            group,
            "该结论是由当前阶段证据组合得到的候选解释，应保持在本地证据允许的范围内。",
        )
        claim["caveat"] = CLAIM_CAVEATS.get(
            group,
            "当前证据不能支持超出本地结果范围的机制性或因果性结论。",
        )
        claims.append(critique_claim(claim))
    return claims
