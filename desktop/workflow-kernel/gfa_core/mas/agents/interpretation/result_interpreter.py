"""Structured Results-style interpretation for stage evidence bundles."""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional


TEMPLATE_STEPS = [
    "本阶段分析对象和规模",
    "关键数据特征",
    "组内/组间模式或例外",
    "与前后阶段的关系",
    "可支持的解释",
    "不能越界的部分",
]

STAGE_GROUPS = {
    "genome_prepare": "data_preparation",
    "identify": "member_identification",
    "phylogeny": "classification_structure_support",
    "reference_classification": "classification_structure_support",
    "subfamily_confirm": "classification_structure_support",
    "domain": "classification_structure_support",
    "motif": "classification_structure_support",
    "structure": "classification_structure_support",
    "karyotype": "genomic_distribution_duplication",
    "synteny": "genomic_distribution_duplication",
    "interspecies_synteny": "cross_species_evolution",
    "ortholog": "cross_species_evolution",
    "kaks": "cross_species_evolution",
    "promoter": "regulatory_expression",
    "tfbs": "regulatory_expression",
    "expression": "regulatory_expression",
    "rnaseq_qc": "rna_response_candidate_prioritization",
    "rnaseq_align": "rna_response_candidate_prioritization",
    "rnaseq_quant": "rna_response_candidate_prioritization",
    "rnaseq_sample_qc": "rna_response_candidate_prioritization",
    "rnaseq_de": "rna_response_candidate_prioritization",
    "annotation": "rna_response_candidate_prioritization",
    "annotation_go": "rna_response_candidate_prioritization",
    "annotation_kegg": "rna_response_candidate_prioritization",
    "rnaseq_go_enrich": "rna_response_candidate_prioritization",
    "rnaseq_kegg_enrich": "rna_response_candidate_prioritization",
    "rnaseq_fgsea": "rna_response_candidate_prioritization",
    "rnaseq_wgcna": "rna_response_candidate_prioritization",
    "rnaseq_candidate_family": "rna_response_candidate_prioritization",
}

GROUP_ORDER = [
    "data_preparation",
    "member_identification",
    "classification_structure_support",
    "genomic_distribution_duplication",
    "cross_species_evolution",
    "regulatory_expression",
    "rna_response_candidate_prioritization",
]

GROUP_STAGE_ORDER = {
    "data_preparation": ["genome_prepare"],
    "member_identification": ["identify"],
    "classification_structure_support": ["phylogeny", "reference_classification", "subfamily_confirm", "domain", "motif", "structure"],
    "genomic_distribution_duplication": ["karyotype", "synteny"],
    "cross_species_evolution": ["interspecies_synteny", "ortholog", "kaks"],
    "regulatory_expression": ["promoter", "tfbs", "expression"],
    "rna_response_candidate_prioritization": [
        "rnaseq_qc",
        "rnaseq_align",
        "rnaseq_quant",
        "rnaseq_sample_qc",
        "rnaseq_de",
        "annotation",
        "annotation_go",
        "annotation_kegg",
        "rnaseq_go_enrich",
        "rnaseq_kegg_enrich",
        "rnaseq_fgsea",
        "rnaseq_wgcna",
        "rnaseq_candidate_family",
    ],
}

READINESS_RANK = {
    "boundary_only": 0,
    "candidate_only": 1,
    "descriptive_only": 2,
    "publishable_result": 3,
}

CANDIDATE_ONLY_STAGES = {
    "promoter",
    "tfbs",
    "expression",
    "rnaseq_fgsea",
    "rnaseq_wgcna",
    "rnaseq_candidate_family",
}


def _safe_int(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        number = int(float(value))
        return number if number > 0 else 0
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _value(value: Any, default: str = "N/A") -> Any:
    if value is None or value == "":
        return default
    return value


def _count_text(value: Any, unit: str = "") -> str:
    if value is None or value == "":
        return "N/A"
    return f"{value}{unit}"


def _join(items: Iterable[Any], limit: int = 5, sep: str = "、") -> str:
    values = [str(item) for item in items if str(item).strip()]
    return sep.join(values[:limit]) if values else "未解析"


def _top_domain_label(item: Any) -> str:
    if isinstance(item, dict):
        name = item.get("domain") or item.get("name") or item.get("id") or item.get("accession")
        count = item.get("count") or item.get("proteins") or item.get("hits")
    elif isinstance(item, (list, tuple)) and item:
        name = item[0]
        count = item[1] if len(item) > 1 else None
    else:
        return ""
    if not name:
        return ""
    return f"{name}({count})" if count not in {None, ""} else str(name)


def _evidence_basis(bundle: Dict[str, Any]) -> List[str]:
    basis: List[str] = []
    for item in bundle.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        if claim and evidence:
            basis.append(f"{claim}: {evidence}")
        elif evidence:
            basis.append(evidence)
    if not basis:
        basis.append("未找到可追溯本地证据；仅能报告阶段状态，不能形成结果解读。")
    return basis[:5]


def _limitations(bundle: Dict[str, Any], fallback: str) -> List[str]:
    risks = [
        str(item.get("message") or "").strip()
        for item in (bundle.get("risks") or [])
        if isinstance(item, dict) and str(item.get("message") or "").strip()
    ]
    if risks:
        return risks[:4]
    status = str(bundle.get("status") or "").strip()
    if status and status not in {"success", "ok", "completed"}:
        return [f"阶段状态为 {status}，结果解读应标注为受限。", fallback]
    return [fallback]


def _stage_group(stage: str) -> str:
    return STAGE_GROUPS.get(stage, "other")


def _simple_value(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _local_facts(bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    facts: List[Dict[str, Any]] = []
    for source_name in ("parsed", "metrics"):
        payload = bundle.get(source_name) if isinstance(bundle.get(source_name), dict) else {}
        for key in sorted(payload):
            value = payload.get(key)
            if _simple_value(value):
                facts.append({"source": source_name, "field": str(key), "value": value})
            elif isinstance(value, dict):
                simple = {str(k): v for k, v in value.items() if _simple_value(v)}
                if simple:
                    facts.append({"source": source_name, "field": str(key), "value": simple})
            elif isinstance(value, list):
                simple_items = [item for item in value if _simple_value(item)]
                if simple_items:
                    facts.append({"source": source_name, "field": str(key), "value": simple_items[:8]})
            if len(facts) >= 10:
                return facts
    return facts


def _evidence_refs(bundle: Dict[str, Any]) -> List[str]:
    refs: List[str] = []
    for item in bundle.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        ref = item.get("id") or item.get("local_id") or item.get("source_file") or item.get("claim")
        if ref and str(ref) not in refs:
            refs.append(str(ref))
        if len(refs) >= 8:
            break
    return refs


def _missing_slots(bundle: Dict[str, Any]) -> List[str]:
    stage = str(bundle.get("stage") or "")
    status = str(bundle.get("status") or "").strip()
    parsed = bundle.get("parsed") if isinstance(bundle.get("parsed"), dict) else {}
    slots: List[str] = []
    if status and status not in {"success", "ok", "completed"}:
        slots.append(f"{status} 阶段缺少完整子任务产物，不能按完全成功阶段写作。")
    if stage == "phylogeny":
        if parsed.get("tree_tip_estimate") in {None, 0, "N/A"}:
            slots.append("缺少可核验的 Newick tip 计数或分支支持信息。")
    if stage == "motif":
        if not parsed.get("motif_site_distribution"):
            slots.append("缺少每个 motif 的位点分布表，不能写成员级 motif 排列。")
        if status == "partial_success":
            slots.append("motif 阶段存在部分成功，seqlogo 或辅助图件等子任务应作为缺口处理。")
    if stage == "domain" and not parsed.get("top_domains"):
        slots.append("缺少可解析的主要结构域分布。")
    if stage == "structure" and not parsed.get("raw_mrna_gene_count"):
        slots.append("缺少结构原始表基因级计数，组内 exon/intron 模式应降级。")
    return slots


def _readiness(stage: str, bundle: Dict[str, Any], generic: bool = False) -> tuple[str, List[str]]:
    status = str(bundle.get("status") or "").strip()
    parsed = bundle.get("parsed") if isinstance(bundle.get("parsed"), dict) else {}
    risks = [risk for risk in (bundle.get("risks") or []) if isinstance(risk, dict)]
    reasons: List[str] = []
    readiness = "publishable_result"

    if stage in CANDIDATE_ONLY_STAGES:
        readiness = "candidate_only"
    if generic:
        readiness = "descriptive_only"
        reasons.append("该阶段使用通用模板，不能替代领域特异 Results 解读。")
    if not bundle.get("evidence"):
        readiness = "descriptive_only"
        reasons.append("缺少可追溯 evidence 条目，只能描述 manifest/指标层结果。")
    if status and status not in {"success", "ok", "completed"}:
        readiness = "descriptive_only"
        reasons.append(f"阶段状态为 {status}，需要降级解释。")
    if any(risk.get("severity") == "high" for risk in risks):
        readiness = "boundary_only"
        reasons.append("存在 high risk 证据冲突，不能写成可发表结果。")
    elif risks and readiness == "publishable_result":
        readiness = "descriptive_only"
        reasons.append("存在风险提示，解释需保留边界。")
    if stage == "phylogeny":
        gap = _safe_float(parsed.get("gap_percentage"))
        if gap is not None and gap >= 50:
            readiness = "descriptive_only" if readiness != "boundary_only" else readiness
            reasons.append(f"MSA gap比例较高({gap:.2f}%)，不能生成强拓扑结论。")
    if stage == "tfbs" and status == "missing":
        readiness = "boundary_only"
    return readiness, reasons


def _with_schema(entry: Dict[str, Any], bundle: Dict[str, Any], *, generic: bool = False) -> Dict[str, Any]:
    stage = str(entry.get("stage") or bundle.get("stage") or "")
    readiness, downgrade_reasons = _readiness(stage, bundle, generic=generic)
    boundary = entry.get("reviewer_risk") or "仅能基于本地证据解释，不能由相邻阶段或文献背景补写缺失结果。"
    entry.update({
        "schema": "structured_result_interpretation_v1",
        "stage_group": _stage_group(stage),
        "interpretation_level": readiness,
        "local_facts": _local_facts(bundle),
        "evidence_refs": _evidence_refs(bundle),
        "missing_slots": _missing_slots(bundle),
        "downgrade_reasons": downgrade_reasons,
        "boundary": boundary,
    })
    return entry


def _entry(
    stage: str,
    bundle: Dict[str, Any],
    stage_name: str,
    interpretation: str,
    article_gap: str,
    reviewer_risk: str,
    *,
    limitations: Optional[List[str]] = None,
    generic: bool = False,
) -> Dict[str, Any]:
    return _with_schema({
        "stage": stage,
        "stage_name": stage_name,
        "status": bundle.get("status", "unknown"),
        "template_steps": list(TEMPLATE_STEPS),
        "result_interpretation": interpretation,
        "evidence_basis": _evidence_basis(bundle),
        "limitations": limitations if limitations is not None else _limitations(bundle, reviewer_risk),
        "article_style_gap": article_gap,
        "reviewer_risk": reviewer_risk,
        "interpretation_depth": "article_results_style_stage_interpretation",
    }, bundle, generic=generic)


def _missing_entry(stage: str, stage_name: str) -> Dict[str, Any]:
    if stage == "tfbs":
        interpretation = (
            "本轮深度解读没有检测到可用的 TFBS 阶段 manifest 或位点表，因此不能报告转录因子 motif、结合位点坐标、"
            "富集 TF 家族或启动子区间分布。该部分只能写作缺失结果和后续补做建议，不能由 promoter 或 RAG 文献替代。"
        )
        gap = "论文 Results 若讨论启动子结合位点，通常会列出预测工具、motif库、位点数量、代表TF家族和基因级分布；当前这些本地证据均缺失。"
        risk = "不能把启动子顺式元件、文献中的TF背景或RAG检索结果写成本次 TFBS 预测结论。"
    else:
        interpretation = f"本轮深度解读没有检测到 {stage_name} 的可用产物，无法形成该阶段的文章式结果解读。"
        gap = "论文 Results 通常需要先给出本阶段对象规模和关键统计；当前缺少本地结果。"
        risk = "缺失阶段不能被相邻阶段或文献背景补写成已完成结果。"
    return _with_schema({
        "stage": stage,
        "stage_name": stage_name,
        "status": "missing",
        "template_steps": list(TEMPLATE_STEPS),
        "result_interpretation": interpretation,
        "evidence_basis": ["未检测到该阶段 manifest 或可追溯产物。"],
        "limitations": [risk],
        "article_style_gap": gap,
        "reviewer_risk": risk,
        "interpretation_depth": "missing_stage_boundary",
    }, {"stage": stage, "status": "missing", "parsed": {}, "metrics": {}, "evidence": [], "risks": []})


def _identify(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    final_members = _value(parsed.get("final_members"))
    total_proteins = _value(parsed.get("total_proteins"))
    hmm_hits = _value(parsed.get("hmm_hits"))
    blast_hits = _value(parsed.get("blast_hits"))
    id_count = _value(parsed.get("id_file_count"))
    fasta_count = _value(parsed.get("fasta_sequence_count"))
    interpretation = (
        f"本阶段以目标物种蛋白集为搜索背景，在 {total_proteins} 条蛋白中形成 {final_members} 个 {prefix} 成员集合。"
        f"HMMER 与 BLAST 记录分别为 {hmm_hits}/{blast_hits}，成员 ID 文件和蛋白 FASTA 分别读取到 {id_count}/{fasta_count} 条记录，"
        "因此 identify 阶段可以承担后续系统发育、结构域、motif、基因结构和染色体分析的基准成员集。"
        "结果解读应强调筛选规模、最终集合和文件一致性，而不是直接推断功能分化。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "文章通常会继续报告重命名规则、蛋白长度/分子量/pI 等属性范围；若这些来自 PEP 或其他阶段，应分开引用，不能写入 identify 本身。",
        "若 ID 计数、FASTA 计数和 manifest final_members 不一致，后续所有阶段的对象集合都会受到影响。",
    )


def _phylogeny(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    gap = _safe_float(parsed.get("gap_percentage"))
    caution = "；由于 gap 比例较高，深层拓扑或分支关系需结合支持率、裁剪策略和结构证据审慎解释" if gap is not None and gap >= 50 else ""
    interpretation = (
        f"系统发育阶段使用 { _count_text(parsed.get('sequence_count'), '条序列') } 构建 MSA，"
        f"比对长度为 {_value(parsed.get('msa_length'))}，gap 比例为 {_value(parsed.get('gap_percentage'))}%，"
        f"树模型记录为 {_value(parsed.get('tree_model'))}，Newick tip 估算为 {_value(parsed.get('tree_tip_estimate'))}。"
        f"这些结果可以作为后续按分支比较 motif、domain 和 gene structure 的组织框架{caution}。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "论文 Results 通常会给出亚家族/分组数量、每组成员数和参考物种锚定；当前若未解析分组，只能解释树的输入规模和质量。",
        "没有分支支持率、参考序列或稳定分组时，不应把树拓扑直接写成确定亚家族功能分化。",
    )


def _domain(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    top_domains = parsed.get("top_domains") or []
    top_text = _join([_top_domain_label(d) for d in top_domains], limit=5)
    scope_text = str(prefix or "").strip()
    prefix_parts = [part.strip() for part in scope_text.split(".") if part.strip()]
    if len(prefix_parts) >= 2:
        family = prefix_parts[1]
    elif "家族" in scope_text:
        family = scope_text.split("家族", 1)[0].split("；")[-1].strip() or "目标"
    else:
        family = "目标"
    domain_names = {
        str((item or {}).get("domain") or (item or {}).get("name") or "").upper()
        for item in top_domains
        if isinstance(item, dict)
    }
    sbp_count = parsed.get("sbp_protein_count")
    sbp_text = (
        f"，SBP 相关显著覆盖为 {_value(sbp_count)}"
        if family.upper() == "SPL" or "SBP" in domain_names
        else ""
    )
    interpretation = (
        f"结构域阶段覆盖 {_value(parsed.get('proteins_total'))} 个蛋白，其中 {_value(parsed.get('proteins_with_domains'))} 个有结构域记录，"
        f"显著 domain hits 为 {_value(parsed.get('significant_domain_hits'))}{sbp_text}。"
        f"主要结构域包括 {top_text}。该结果可用于判断 identify 得到的成员是否具有一致的 {family} 家族相关结构域组成，"
        "并可与 motif 阶段共同描述蛋白保守性。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "文章通常会把结构域种类、家族相关核心结构域和附加结构域与系统发育分组并列展示；当前若缺少分组映射，不能讨论组特异结构域。",
        "结构域结论应以原始 domtblout 显著命中为准；manifest 汇总与原始解析冲突时必须降级。",
    )


def _motif(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    conserved = parsed.get("most_conserved_motif") or {}
    distribution = parsed.get("motif_site_distribution") or []
    dist_text = _join([f"{m.get('id') or m.get('name')}:{m.get('sites')} sites" for m in distribution if isinstance(m, dict)], limit=6)
    width_range = parsed.get("motif_width_range") or {}
    width_text = (
        f"{width_range.get('min')}-{width_range.get('max')} aa"
        if width_range.get("min") is not None and width_range.get("max") is not None
        else "未解析"
    )
    interpretation = (
        f"Motif 阶段在 {_value(parsed.get('sequence_count'))} 条蛋白中识别 {_value(parsed.get('motif_count'))} 个 motif，"
        f"总位点数为 {_value(parsed.get('total_sites'))}，宽度范围为 {width_text}。"
        f"最保守 motif 为 {conserved.get('id') or conserved.get('name') or '未解析'}，覆盖 {conserved.get('sites', 'N/A')} 个 sites；"
        f"不同 motif 的出现频率并不均一，前列分布为 {dist_text}。"
        "结果层面可以写出 motif 数量、覆盖差异和保守核心候选；若没有每个基因的 motif 位置表，则不能进一步写成具体成员或亚家族的排列模式。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "文章 Results 通常会结合系统发育树说明同一分支 motif 组成相似、个别成员缺失或新增 motif；当前若位置表为空，应停留在总体分布和保守程度。",
        "MEME motif 是序列模式证据，不等同于 Pfam 结构域或实验证明的功能位点。",
    )


def _structure(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    max_cds = parsed.get("max_cds_gene")
    max_text = f"{max_cds[0]}({max_cds[1]}个CDS)" if isinstance(max_cds, (list, tuple)) and len(max_cds) >= 2 else "未解析"
    interpretation = (
        f"基因结构阶段记录 {_value(parsed.get('genes_total'))} 个成员，其中 {_value(parsed.get('genes_with_structure'))} 个有结构信息，"
        f"structure.tsv 中 mRNA 记录覆盖 {_value(parsed.get('raw_mrna_gene_count'))} 个基因，平均外显子数为 {_value(parsed.get('avg_exon_count_per_gene'))}。"
        f"总 CDS/UTR feature 分别为 {_value(parsed.get('total_cds_features'))}/{_value(parsed.get('total_utr_features'))}，CDS 数最多的成员为 {max_text}。"
        "这类结果适合与 motif 和 phylogeny 交叉，观察结构复杂度是否与分支或保守 motif 一致。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "文章通常会描述 exon/intron 数量范围、相似分支结构一致性和异常成员；当前已有总体统计，但若没有分组映射，应避免组内模式结论。",
        "转录本级、feature级和基因级计数必须分开；结构覆盖率异常时不能直接发表。",
    )


def _promoter(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    tops = parsed.get("top_elements") or []
    top_text = _join([f"{i.get('name')}({i.get('count')})" for i in tops if isinstance(i, dict)], limit=5)
    interpretation = (
        f"启动子阶段识别总元件 {_value(parsed.get('total_elements'))} 个、唯一元件 {_value(parsed.get('unique_elements'))} 类，"
        f"覆盖 {_value(parsed.get('genes_with_elements'))} 个家族成员；过滤后元件为 {_value(parsed.get('filtered_elements'))}，"
        f"富集/共现对分别为 {_value(parsed.get('enriched_pairs'))}/{_value(parsed.get('cooccurrence_pairs'))}。高频元件包括 {top_text}。"
        "这可以支持启动子元件组成和候选调控线索描述，但不等同于 TF 实际结合。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "文章若讨论启动子调控，通常会进一步按激素、胁迫、光响应等类别归纳，并与表达模式相互印证；当前应先区分基础启动子元件和功能响应元件。",
        "TATA/CAAT 等高频基础元件不能单独支撑胁迫或激素响应结论。",
    )


def _tfbs(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    top_motifs = parsed.get("top_tf_motifs") or []
    enriched = parsed.get("enriched_tf_motifs") or []
    top_text = _join([f"{row.get('motif_id')}({row.get('family_sites')} sites/{row.get('family_genes_with_site')} genes)" for row in top_motifs if isinstance(row, dict)], limit=5)
    enriched_text = _join([f"{row.get('motif_id')}(q={row.get('q_value')})" for row in enriched if isinstance(row, dict)], limit=5)
    interpretation = (
        f"TFBS 阶段以 {_value(parsed.get('family_promoter_count'))} 条家族启动子和 {_value(parsed.get('background_promoter_count'))} 条背景启动子为对象，"
        f"预测到家族位点 {_value(parsed.get('family_site_count'))} 个、TF motif {_value(parsed.get('family_motif_count'))} 个，"
        f"motif 库为 {_value(parsed.get('motif_database'))}，选择策略为 {_value(parsed.get('motif_selection'))}。"
        f"高频 motif 包括 {top_text}；显著富集候选包括 {enriched_text}。"
        "该结果可形成可检验的调控候选，但必须表述为计算预测。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "文章式写法应报告位点数量、代表 motif/TF 家族、富集显著性和基因级坐标示例；若缺少 q 值或坐标，解释深度应下降。",
        "FIMO/TFBS 预测不能证明体内真实结合；需要表达、TF 注释、ChIP/EMSA/报告基因等后续验证。",
    )


def _synteny(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    pairs = parsed.get("example_family_pairs") or []
    pair_items: List[str] = []
    for item in pairs:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            pair_items.append(f"{item[0]}-{item[1]}")
        elif isinstance(item, dict):
            left = item.get("query") or item.get("gene1") or item.get("source")
            right = item.get("subject") or item.get("gene2") or item.get("target")
            if left and right:
                pair_items.append(f"{left}-{right}")
        elif isinstance(item, str):
            pair_items.append(item)
    pair_text = _join(pair_items, limit=4)
    interpretation = (
        f"种内共线性阶段的全基因组背景包含 {_value(parsed.get('synteny_blocks'))} 个 block、{_value(parsed.get('gene_pairs'))} 对基因，"
        f"其中家族成员级共线性候选为 {_value(parsed.get('family_pair_count'))} 对，涉及 {_value(parsed.get('family_block_count'))} 个 block。"
        f"示例配对为 {pair_text}。结果解读必须区分全基因组背景规模和 {prefix} 家族内配对规模，"
        "后者才可用于讨论片段复制或保守锚定候选。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "文章通常会明确报告片段复制/串联复制数量并列出家族复制基因对；当前若只有 collinearity 解析，应避免把全基因组 block 数当作家族扩张证据。",
        "家族内共线性对数量与原文或图件不一致时，应作为复核点，而不是强行解释。",
    )


def _karyotype(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    chrom_text = _join(parsed.get("chromosomes") or [], limit=8)
    interpretation = (
        f"核型阶段将 {_value(parsed.get('gene_count'))} 个家族成员定位到 {_value(parsed.get('chromosome_count'))} 条染色体，"
        f"涉及染色体 {chrom_text}，串联重复计数为 {_value(parsed.get('tandem_count'))}。"
        "这可以描述染色体层面的分布范围和是否存在明显串联复制线索；若缺少每条染色体成员数，不能进一步断言分布热点。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "论文 Results 常写染色体不均匀分布、具体富集染色体和复制类型；当前若没有每条染色体计数，应只写覆盖染色体数和 tandem 统计。",
        "染色体定位不能替代共线性分析；串联复制、片段复制和普通定位需要分开。",
    )


def _interspecies_synteny(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    interpretation = (
        f"种间共线性阶段解析到全基因组 block {_value(parsed.get('alignment_blocks'))} 个、基因对 {_value(parsed.get('gene_pairs'))} 对，"
        f"目标家族成员参与 {_value(parsed.get('family_pair_count'))} 对，参考家族成员参与 {_value(parsed.get('ref_family_pair_count'))} 对，"
        f"目标-参考家族直接同对为 {_value(parsed.get('family_ref_pair_count'))} 对。"
        "只有目标和参考家族成员在同一共线性基因对中直接配对时，才适合写成跨物种保守共线性证据。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "文章通常会比较多个物种间保守连线数量并推断共同祖先或保守性等级；当前必须先区分全基因组背景和家族直接配对。",
        "若 family_ref_pair_count 为 0，不能写成目标家族与参考家族存在直接保守共线性。",
    )


def _pep(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    task_status = parsed.get("task_status") or {}
    successes = [name for name, info in task_status.items() if isinstance(info, dict) and info.get("success") is True]
    failures = [f"{name}: {info.get('error')}" for name, info in task_status.items() if isinstance(info, dict) and info.get("success") is False and info.get("error")]
    interpretation = (
        f"PEP 阶段以 {_value(parsed.get('protein_count'))} 个蛋白为对象，子任务成功数为 {_value(parsed.get('tasks_success'))}/{_value(parsed.get('tasks_total'))}。"
        f"成功部分包括 {_join(successes)}；失败或未产出部分包括 {_join(failures, limit=4)}。"
        "因此本阶段不能整体判为不可解，DeepTMHMM 等成功子任务可继续作为蛋白拓扑预测线索；"
        "但 ProtParam/WoLF PSORT 失败时，理化性质和亚细胞定位不能写成结果。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "文章通常会报告长度、分子量、pI、GRAVY、跨膜结构和定位分布；当前只应解释成功子任务，失败子任务留作缺口。",
        "部分成功阶段不能全盘否定，也不能把失败子任务对应指标补写成已完成结果。",
    )


def _expression(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    tables = parsed.get("tables") or {}
    tops = parsed.get("top_expression_genes") or []
    top_text = _join([f"{row.get('gene')}({row.get('max_expression')})" for row in tops if isinstance(row, dict)], limit=4)
    interpretation = (
        f"表达阶段 manifest 记录基因数为 {_value(parsed.get('gene_count'))}；表达矩阵维度为 "
        f"{_value((tables.get('gene_exp') or {}).get('rows'))} 行 x {_value((tables.get('gene_exp') or {}).get('columns'))} 列。"
        f"当前解析到的高表达候选包括 {top_text}。该结果适合与 promoter、TFBS、phylogeny 或 subfamily 结果交叉，"
        "提出候选表达模式和候选基因，而不是单独证明调控机制。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "文章 Results 通常会按组织/时期/处理分组描述表达模式、例外基因和同源基因表达差异；当前若缺少分组设计，应只报告矩阵和候选高值。",
        "表达相关性或高表达不能单独证明功能，需要实验验证或多证据一致性。",
    )


def _rnaseq_qc(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    raw_reads = _safe_int(parsed.get("total_raw_reads"))
    clean_reads = _safe_int(parsed.get("total_clean_reads"))
    retention = f"{clean_reads / raw_reads:.1%}" if raw_reads and clean_reads else "未解析"
    interpretation = (
        f"RNA-seq 质控阶段覆盖 {_value(parsed.get('total_samples'))} 个样本，"
        f"manifest 汇总 raw reads={_value(parsed.get('total_raw_reads'))}、clean reads={_value(parsed.get('total_clean_reads'))}，保留比例为 {retention}。"
        "这一阶段的结果用于判断测序 reads 是否经过接头和低质量序列过滤，并决定数据是否适合进入比对。"
        "如果 reads 计数字段为 0 或未解析，应引用 FastQC/Cutadapt/fastp 原始产物判断质量，而不能把 0 写成真实测序量。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "论文 Results 通常会报告每个样本过滤前后 reads、Q20/Q30、GC 含量和过滤比例；当前若只有汇总字段，应避免过度评价样本质量。",
        "QC 只能证明测序数据质量和过滤状态，不能证明处理造成表达变化。",
    )


def _rnaseq_align(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    interpretation = (
        f"RNA-seq 比对阶段覆盖 {_value(parsed.get('total_samples'))} 个样本，"
        f"平均比对率记录为 {_value(parsed.get('average_alignment_rate'))}。"
        "该结果用于评估 clean reads 与参考基因组/注释体系的匹配程度，是后续表达定量可靠性的前提。"
        "若 manifest 中 total_reads/aligned_reads 解析为 0，应回查 HISAT2/STAR/比对日志，而不是把 0 作为真实比对失败结论。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "论文 Results 通常会列出总体比对率、唯一比对率、多比对率和低比对样本；当前若仅有平均比对率，只能做总体质量判断。",
        "比对率说明数据和参考的匹配质量，不能直接说明基因差异表达或通路变化。",
    )


def _rnaseq_quant(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    interpretation = (
        f"RNA-seq 定量阶段生成 {_value(parsed.get('total_genes'))} 个基因、"
        f"{_value(parsed.get('samples_processed'))} 个样本的表达矩阵。"
        "结果层面应区分原始 count 矩阵和 TPM/FPKM 等展示型矩阵：count 适合进入 DESeq2/edgeR 统计模型，"
        "TPM/FPKM 更适合表达热图、趋势图或样本间展示。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "论文 Results 通常会说明保留基因数、样本数、表达过滤阈值和矩阵类型；当前若缺少过滤规则，应避免评价低表达基因处理是否充分。",
        "不能把 TPM/FPKM 的表观差异直接写成统计显著差异。",
    )


def _rnaseq_sample_qc(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    interpretation = (
        f"RNA-seq 样本质量评估使用 {_value(parsed.get('samples'))} 个样本、{_value(parsed.get('conditions'))} 个条件，"
        f"输入/表达基因数为 {_value(parsed.get('input_genes'))}/{_value(parsed.get('expressed_genes'))}，"
        f"PC1/PC2 解释率为 {_value(parsed.get('pc1_percent'))}%/{_value(parsed.get('pc2_percent'))}%。"
        "该阶段用于检查重复一致性、分组分离和潜在离群样本，应位于差异表达之前。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "论文 Results 通常会结合 PCA、样本相关性热图和聚类树共同描述样本质量；当前若只解析 PCA，应把结论限定为主成分层面的质控线索。",
        "PCA 分离或聚类趋势不能直接证明处理机制，需要结合 DE、富集和实验设计解释。",
    )


def _rnaseq_de(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    largest = parsed.get("largest_comparison") or {}
    largest_text = (
        f"其中 {largest.get('name')} 的 DEG 数最多，为 {largest.get('total')} 个"
        if largest.get("name")
        else "当前未解析到 DEG 数最多的具体比较"
    )
    interpretation = (
        f"差异表达阶段完成 {_value(parsed.get('total_comparisons'))} 个比较，"
        f"共筛选 {_value(parsed.get('de_genes_total'))} 个差异基因，其中上调 {_value(parsed.get('de_genes_up'))}、下调 {_value(parsed.get('de_genes_down'))}；"
        f"{largest_text}。该结果能说明哪些比较产生更强转录响应，并为 GO/KEGG/fgsea/WGCNA 提供前景或排序输入。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "论文 Results 通常会按比较组列出 DEG 数、上下调比例、阈值和火山图/热图；当前若缺少分比较表，应只解释总量和方向。",
        "DEG 数量不能单独证明通路激活、调控因果或候选基因功能。",
    )


def _annotation(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    interpretation = (
        f"背景注释阶段使用 {_value(parsed.get('method_used'))}，输入蛋白 {_value(parsed.get('input_proteins'))} 个，"
        f"得到 GO 注释基因 {_value(parsed.get('genes_with_go'))} 个、KO 注释基因 {_value(parsed.get('genes_with_ko'))} 个，"
        f"GO/KO term 数分别为 {_value(parsed.get('go_term_count'))}/{_value(parsed.get('ko_term_count'))}。"
        "这些 gene2go/gene2ko 背景决定 GO/KEGG 富集的统计边界和可解释范围。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "论文 Results 通常会报告注释数据库、版本、ID 标准化和背景覆盖率；当前若缺少数据库版本，应在方法和限制中补充。",
        "未注释不等于无功能；低注释覆盖会降低富集解释强度。",
    )


def _rnaseq_go_enrich(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    top_terms = parsed.get("top_go_terms") or []
    top_text = _join(
        [
            f"{row.get('term') or row.get('Description') or row.get('ID')}(q={row.get('qvalue') or row.get('padj') or row.get('p.adjust')})"
            for row in top_terms
            if isinstance(row, dict)
        ],
        limit=4,
    )
    interpretation = (
        f"GO 富集阶段以前景差异基因 {_value(parsed.get('de_genes_input'))} 个为输入，"
        f"在 {_value(parsed.get('annotated_background_genes'))} 个 GO 背景基因中检出 {_value(parsed.get('go_enriched_terms'))} 个富集 term，"
        f"foreground 模式为 {_value(parsed.get('foreground_mode'))}。代表性 term 包括 {top_text}。"
        "该结果用于描述 DEG 集合在 BP/MF/CC 层面的功能偏向。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "论文 Results 应报告具体 GO term、类别、显著性和 GeneRatio；只报告 term 数量不足以形成生物学解释。",
        "GO 富集说明统计偏向，不能单独证明对应过程被真实激活或由目标家族调控。",
    )


def _rnaseq_kegg_enrich(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    top_paths = parsed.get("top_kegg_pathways") or []
    top_text = _join(
        [
            f"{row.get('pathway') or row.get('Description') or row.get('ID')}(q={row.get('qvalue') or row.get('padj') or row.get('p.adjust')})"
            for row in top_paths
            if isinstance(row, dict)
        ],
        limit=4,
    )
    interpretation = (
        f"KEGG 富集阶段以前景差异基因 {_value(parsed.get('de_genes_input'))} 个为输入，"
        f"KO 背景基因为 {_value(parsed.get('annotated_background_genes'))} 个，过滤后通路 {_value(parsed.get('kegg_filtered_pathways'))} 条，"
        f"显著展示通路 {_value(parsed.get('kegg_enriched_pathways'))} 条；kegg_organism={_value(parsed.get('kegg_organism'))}，"
        f"filter_scope={_value(parsed.get('kegg_filter_scope'))}。代表性通路包括 {top_text}。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "论文 Results 应区分 raw pathway 和植物/物种过滤后的 pathway，并报告显著性、GeneRatio 或 KO 命中；当前解释应优先采用过滤后结果。",
        "KEGG 富集不能证明通路真实激活；非植物或疾病类通路不能作为植物项目主结论。",
    )


def _rnaseq_fgsea(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    modes = parsed.get("kegg_gene_set_modes")
    mode_text = ",".join(modes) if isinstance(modes, list) else _value(modes)
    top = parsed.get("top_fgsea_pathways") or []
    top_text = _join(
        [
            f"{row.get('Description') or row.get('pathway') or row.get('ID')}(NES={row.get('NES')}, padj={row.get('padj')})"
            for row in top
            if isinstance(row, dict)
        ],
        limit=4,
    )
    interpretation = (
        f"fgsea 阶段基于 {_value(parsed.get('ranked_genes'))} 个排序基因，"
        f"其中 {_value(parsed.get('genes_with_pathway_in_ranks'))} 个可映射到通路，测试 {_value(parsed.get('pathways_tested'))} 条通路，"
        f"padj<=0.05 的通路为 {_value(parsed.get('significant_pathways_padj_0_05'))} 条。"
        f"gene set 模式为 {mode_text}，代表性排序富集通路包括 {top_text}。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "论文 Results 应报告 NES、padj、leading-edge 基因和正/负向趋势；fgsea 的价值是补充阈值型 DEG 富集未覆盖的整体协同变化。",
        "fgsea 是排序富集趋势，不能替代 DE 统计，也不能单独证明调控因果。",
    )


def _rnaseq_wgcna(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    top_module = parsed.get("top_module_trait") or {}
    top_text = (
        f"{top_module.get('module')} 与 {top_module.get('trait')} 相关 r={top_module.get('correlation')}"
        if top_module
        else "未解析到最高模块-性状相关"
    )
    interpretation = (
        f"WGCNA 阶段使用 {_value(parsed.get('genes_used'))} 个表达基因、{_value(parsed.get('samples'))} 个样本构建共表达网络，"
        f"softPower={_value(parsed.get('soft_power'))}，识别 {_value(parsed.get('modules'))} 个模块，矩阵类型为 {_value(parsed.get('matrix_type'))}；"
        f"主要模块-性状信号为 {top_text}。该结果适合筛选候选模块和候选 hub 基因。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "论文 Results 应报告模块颜色、模块大小、module-trait correlation、p 值和候选 hub；当前若缺少 p 值或 hub 表，应停留在候选模块层面。",
        "小样本 WGCNA 属于探索性网络线索，不能写成强因果调控网络。",
    )


def _rnaseq_candidate_family(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    recs = parsed.get("top_recommendations") or parsed.get("top_candidate_families") or []
    rec_text = _join(
        [
            row.get("candidate_family") or row.get("family") or row.get("name")
            for row in recs
            if isinstance(row, dict)
        ],
        limit=5,
    )
    interpretation = (
        f"候选家族筛选阶段在 {_value(parsed.get('annotated_deg_genes'))} 个已注释 DEG 中识别 "
        f"{_value(parsed.get('candidate_family_count'))} 个候选家族/结构域，推荐模式为 {_value(parsed.get('recommendation_mode'))}，"
        f"实验设计为 {_value(parsed.get('experiment_design'))}，处理信息为 {_value(parsed.get('treatment'))}。"
        f"当前优先候选包括 {rec_text}；RAG 文献状态为 {_value(parsed.get('rag_reference_status'))}。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "论文 Results 可把该阶段作为候选优先级排序和下游家族分析入口；若要写功能机制，必须再结合正式 identify、结构、表达和实验验证。",
        "候选家族筛选不是最终家族鉴定，也不能证明候选家族功能；RAG 只能提供外部背景。",
    )


def _generic(stage: str, bundle: Dict[str, Any], prefix: str, stage_name: str) -> Dict[str, Any]:
    metrics = bundle.get("metrics") or {}
    metric_text = _join([f"{key}={value}" for key, value in list(metrics.items())[:6]], limit=6, sep="，")
    interpretation = (
        f"{stage_name} 阶段已进入深度解读证据束，当前可追溯指标包括 {metric_text}。"
        "该阶段暂使用通用文章式模板：先描述对象规模和关键统计，再说明可支持的解释边界。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "该阶段还没有专门的文章 Results 写法模板，后续可根据论文复现样例补充分支/模式/例外项。",
        "通用模板只能保证证据边界，不能替代领域特异结果解读。",
        generic=True,
    )


def _genome_prepare(
    stage: str,
    bundle: Dict[str, Any],
    prefix: str,
    stage_name: str,
) -> Dict[str, Any]:
    parsed = bundle.get("parsed") or {}
    species_rows = parsed.get("prepared_species") or []
    species_text = _join(
        [
            (
                f"{row.get('species')}({row.get('role')}, "
                f"gene_count={row.get('gene_count')}, "
                f"{'成功' if row.get('success') else '失败'})"
            )
            for row in species_rows
            if isinstance(row, dict)
        ],
        limit=10,
    )
    interpretation = (
        f"数据准备共处理 {_value(parsed.get('total_species'))} 个物种，其中 "
        f"{_value(parsed.get('success_species'))} 个成功、"
        f"{_value(parsed.get('failed_species'))} 个失败。"
        f"物种级结果为：{species_text}。"
        f"config.xlsx 预检状态为 {'通过' if parsed.get('config_preflight_ok') else '未通过或未记录'}；"
        "该阶段建立后续成员鉴定和 RNA-seq 所使用的标准化基因组、注释、CDS、蛋白及 ID 映射输入。"
    )
    return _entry(
        stage,
        bundle,
        stage_name,
        interpretation,
        "如需用于正式报告，应继续记录各物种输入版本、标准化文件校验和 ID 映射覆盖率。",
        "数据准备成功只说明标准化输入已经生成，不证明下游成员鉴定或表达分析结果正确。",
    )
_INTERPRETERS: Dict[str, Callable[[str, Dict[str, Any], str, str], Dict[str, Any]]] = {
    "genome_prepare": _genome_prepare,
    "identify": _identify,
    "phylogeny": _phylogeny,
    "domain": _domain,
    "motif": _motif,
    "structure": _structure,
    "promoter": _promoter,
    "tfbs": _tfbs,
    "synteny": _synteny,
    "interspecies_synteny": _interspecies_synteny,
    "karyotype": _karyotype,
    "pep": _pep,
    "expression": _expression,
    "rnaseq_qc": _rnaseq_qc,
    "rnaseq_align": _rnaseq_align,
    "rnaseq_quant": _rnaseq_quant,
    "rnaseq_sample_qc": _rnaseq_sample_qc,
    "rnaseq_de": _rnaseq_de,
    "annotation": _annotation,
    "annotation_go": _annotation,
    "annotation_kegg": _annotation,
    "rnaseq_go_enrich": _rnaseq_go_enrich,
    "rnaseq_kegg_enrich": _rnaseq_kegg_enrich,
    "rnaseq_fgsea": _rnaseq_fgsea,
    "rnaseq_wgcna": _rnaseq_wgcna,
    "rnaseq_candidate_family": _rnaseq_candidate_family,
}


def build_structured_result_interpretations(
    stages: List[str],
    bundles: Dict[str, Dict[str, Any]],
    *,
    prefix: str,
    stage_names: Dict[str, str],
    expected_stages: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Build article Results-style stage interpretations from local evidence."""
    ordered: List[str] = []
    for stage in expected_stages or []:
        if stage not in ordered:
            ordered.append(stage)
    for stage in stages:
        if stage not in ordered:
            ordered.append(stage)

    output: Dict[str, Dict[str, Any]] = {}
    for stage in ordered:
        stage_name = stage_names.get(stage, stage)
        bundle = bundles.get(stage)
        if not bundle:
            output[stage] = _missing_entry(stage, stage_name)
            continue
        interpreter = _INTERPRETERS.get(stage, _generic)
        output[stage] = interpreter(stage, bundle, prefix, stage_name)
    return output


def _unique_text(items: Iterable[Any], limit: int = 12) -> List[str]:
    values: List[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in values:
            values.append(text)
        if len(values) >= limit:
            break
    return values


def _group_readiness(entries: List[Dict[str, Any]]) -> str:
    if not entries:
        return "boundary_only"
    return min(
        (str(entry.get("interpretation_level") or "boundary_only") for entry in entries),
        key=lambda level: READINESS_RANK.get(level, 0),
    )


def _group_title(group: str) -> str:
    titles = {
        "data_preparation": "数据准备与标准化输入",
        "member_identification": "成员集合与分析对象边界",
        "classification_structure_support": "分类、结构域、motif 与基因结构联合证据",
        "genomic_distribution_duplication": "染色体分布与复制事件证据",
        "cross_species_evolution": "跨物种保守性与选择压力证据",
        "regulatory_expression": "调控元件与表达证据",
        "rna_response_candidate_prioritization": "RNA-seq 响应与候选优先级证据",
        "other": "其他阶段证据",
    }
    return titles.get(group, group)


def _stage_payload(stage: str, bundles: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    bundle = bundles.get(stage) or {}
    parsed = bundle.get("parsed") if isinstance(bundle.get("parsed"), dict) else {}
    metrics = bundle.get("metrics") if isinstance(bundle.get("metrics"), dict) else {}
    payload: Dict[str, Any] = {}
    payload.update(metrics)
    payload.update(parsed)
    return payload


def _group_result_interpretation(group: str, group_stages: List[str], bundles: Dict[str, Dict[str, Any]], prefix: str) -> str:
    if group == "member_identification":
        identify = _stage_payload("identify", bundles)
        return (
            f"{prefix} 的成员集合以 identify 阶段为对象边界，最终成员数为 "
            f"{_value(identify.get('final_members') or identify.get('fasta_sequence_count'))}，"
            f"搜索背景蛋白数为 {_value(identify.get('total_proteins'))}。"
            "该组结果适合定义后续所有结构、进化和表达分析的基准集合；不应直接写成功能分化结论。"
        )
    if group == "classification_structure_support":
        phylogeny = _stage_payload("phylogeny", bundles)
        domain = _stage_payload("domain", bundles)
        motif = _stage_payload("motif", bundles)
        structure = _stage_payload("structure", bundles)
        domains = _join([_top_domain_label(item) for item in (domain.get("top_domains") or [])], limit=5)
        sequence_text = f"{_value(phylogeny.get('sequence_count'))} 条序列"
        return (
            f"分类与结构证据组以 {sequence_text} 的系统发育/MSA结果作为组织框架，"
            f"比对长度为 {_value(phylogeny.get('msa_length'))}，gap 比例为 {_value(phylogeny.get('gap_percentage'))}%。"
            f"结构域结果覆盖 {_value(domain.get('proteins_total'))} 个蛋白，主要结构域包括 {domains}；"
            f"motif 阶段识别 {_value(motif.get('motif_count'))} 个 motif、{_value(motif.get('total_sites'))} 个位点；"
            f"基因结构阶段覆盖 {_value(structure.get('genes_total'))} 个成员。"
            "这组证据可以写成“成员保守结构和分支组织框架”，但只有在分支支持、成员级 motif/domain 映射和结构表完整时，"
            "才适合进一步写亚家族特异结构差异。"
        )
    if group == "genomic_distribution_duplication":
        karyotype = _stage_payload("karyotype", bundles)
        synteny = _stage_payload("synteny", bundles)
        return (
            f"染色体与复制证据组记录的定位基因数为 {_value(karyotype.get('mapped_genes') or karyotype.get('genes_mapped'))}，"
            f"共线性/复制对数量为 {_value(synteny.get('duplicate_pairs') or synteny.get('syntenic_pairs'))}。"
            "该组结果用于描述家族在基因组上的分布和潜在扩张来源；缺少复制类型、block 质量或坐标核验时不应扩展到选择机制。"
        )
    if group == "cross_species_evolution":
        inter = _stage_payload("interspecies_synteny", bundles)
        ortholog = _stage_payload("ortholog", bundles)
        kaks = _stage_payload("kaks", bundles)
        return (
            f"跨物种证据组整合种间共线性、同源关系和 Ka/Ks。当前种间共线性对为 "
            f"{_value(inter.get('orthologous_pairs') or inter.get('syntenic_pairs'))}，"
            f"ortholog 记录为 {_value(ortholog.get('ortholog_pairs') or ortholog.get('pairs'))}，"
            f"Ka/Ks 可用对数为 {_value(kaks.get('pair_count') or kaks.get('valid_pairs'))}。"
            "该组结果可支持保守性和选择压力的候选描述；不应在缺少物种树、同源校验和统计分布时写成确定进化历史。"
        )
    if group == "regulatory_expression":
        promoter = _stage_payload("promoter", bundles)
        tfbs = _stage_payload("tfbs", bundles)
        expression = _stage_payload("expression", bundles)
        return (
            f"调控与表达证据组显示 promoter 元件总数为 {_value(promoter.get('total_elements'))}，"
            f"TFBS 位点数为 {_value(tfbs.get('family_site_count') or tfbs.get('site_count'))}，"
            f"表达矩阵/表格覆盖 {_value(expression.get('gene_count') or expression.get('genes') or expression.get('tables'))}。"
            "该组适合提出候选调控线索和表达响应模式；顺式元件、TFBS 和表达相关性均不能单独证明真实调控因果。"
        )
    if group == "rna_response_candidate_prioritization":
        de = _stage_payload("rnaseq_de", bundles)
        go = _stage_payload("rnaseq_go_enrich", bundles)
        kegg = _stage_payload("rnaseq_kegg_enrich", bundles)
        wgcna = _stage_payload("rnaseq_wgcna", bundles)
        candidate = _stage_payload("rnaseq_candidate_family", bundles)
        return (
            f"RNA-seq 证据组以差异表达、富集、排序富集、共表达和候选家族筛选组成响应证据链。"
            f"DEG 总数为 {_value(de.get('de_genes_total'))}，GO/KEGG 显著条目分别为 "
            f"{_value(go.get('go_enriched_terms'))}/{_value(kegg.get('kegg_enriched_pathways'))}，"
            f"WGCNA 模块数为 {_value(wgcna.get('modules'))}，候选家族数为 {_value(candidate.get('candidate_family_count'))}。"
            "该组可用于排序候选家族和候选基因；若缺少样本 QC、比较设计或独立验证，应收束为候选优先级而非机制结论。"
        )
    stage_list = "、".join(group_stages)
    return f"{_group_title(group)} 包含 {stage_list} 阶段，当前仅能基于各阶段 manifest 和本地证据做汇总性解读。"


def _group_boundary(group: str, readiness: str, downgrade_reasons: List[str], missing_slots: List[str]) -> str:
    base = {
        "classification_structure_support": "高 gap、缺少分支支持或成员级 motif/domain/structure 映射时，只能把系统发育树作为组织框架，不能生成强拓扑结论或亚家族功能分化结论。",
        "rna_response_candidate_prioritization": "RNA-seq 组应收束为响应模式和候选优先级；缺少实验验证时不能写成因果调控机制。",
        "regulatory_expression": "调控组只能支持候选元件、候选 TFBS 和表达相关线索；不能替代 EMSA、ChIP 或转录调控实验证据。",
        "cross_species_evolution": "跨物种组应区分共线性、同源和选择压力证据；缺少物种树或正交校验时不能写成完整进化历史。",
        "genomic_distribution_duplication": "分布和复制组可描述位置与重复线索；缺少复制分类和显著性检验时不能推断扩张机制。",
        "member_identification": "成员鉴定组定义分析对象；不能从成员数本身推出功能分化。",
    }.get(group, "该组只能基于本地证据解释，不能由文献背景补写缺失结果。")
    if readiness == "publishable_result":
        return base
    reason_text = "；".join(_unique_text([*downgrade_reasons, *missing_slots], limit=4))
    return f"{base} 当前 readiness={readiness}；{reason_text}" if reason_text else f"{base} 当前 readiness={readiness}。"


def build_grouped_result_interpretations(
    stages: List[str],
    bundles: Dict[str, Dict[str, Any]],
    structured_result_interpretations: Dict[str, Dict[str, Any]],
    *,
    prefix: str,
) -> Dict[str, Dict[str, Any]]:
    """Build bounded cross-stage Results-style narratives from local evidence."""
    grouped: Dict[str, Dict[str, Any]] = {}
    stage_set = set(stages) | set(structured_result_interpretations)
    for group in GROUP_ORDER + ["other"]:
        if group == "other":
            ordered_stages = [stage for stage in stages if _stage_group(stage) == "other"]
        else:
            preferred = GROUP_STAGE_ORDER.get(group, [])
            ordered_stages = [stage for stage in preferred if stage in stage_set]
            ordered_stages.extend([stage for stage in stages if _stage_group(stage) == group and stage not in ordered_stages])
        entries = [
            structured_result_interpretations[stage]
            for stage in ordered_stages
            if isinstance(structured_result_interpretations.get(stage), dict)
        ]
        present_stages = [stage for stage in ordered_stages if stage in bundles]
        if not entries or not present_stages:
            continue
        readiness = _group_readiness(entries)
        evidence_refs = _unique_text(ref for entry in entries for ref in (entry.get("evidence_refs") or []))
        missing_slots = _unique_text(slot for entry in entries for slot in (entry.get("missing_slots") or []))
        downgrade_reasons = _unique_text(reason for entry in entries for reason in (entry.get("downgrade_reasons") or []))
        grouped[group] = {
            "schema": "grouped_result_interpretation_v1",
            "group": group,
            "title": _group_title(group),
            "stages": present_stages,
            "all_group_stages": ordered_stages,
            "readiness": readiness,
            "result_interpretation": _group_result_interpretation(group, present_stages, bundles, prefix),
            "evidence_refs": evidence_refs,
            "missing_slots": missing_slots,
            "downgrade_reasons": downgrade_reasons,
            "boundary": _group_boundary(group, readiness, downgrade_reasons, missing_slots),
        }
    return grouped
