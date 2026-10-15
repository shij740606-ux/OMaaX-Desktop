"""
快速意图识别 - 基于关键词匹配，无需LLM调用
"""
import re
from typing import Optional, Dict, Any, List
from gfa_core.mas.stage_catalog import complete_dependencies as catalog_complete_dependencies
from gfa_core.mas.utils.intent_parser import (
    extract_negated_target_stages,
    is_gwas_fmt_intent,
    parse_gene_family_scope_selection,
    parse_intent_from_query,
    resolve_gene_family_report_intent,
    resolve_gwas_intent,
    resolve_population_intent,
)

# 分析类型关键词映射（注意：key必须与orchestrator.py中的analysis_type匹配）
ANALYSIS_KEYWORDS = {
    "wgs_align": ["wgs align", "wgs_align", "wgs alignment", "wgs比对", "wgs序列比对", "bwa-mem2"],
    "gwas_fmt": ["gwas格式", "gwas format", "tassel转换", "tassel convert", "hapmap转换", "vcf转hapmap", "vcf to hapmap"],
    "rnaseq_qc": ["rnaseq质控", "rna-seq质控", "质控分析", "质控", "qc", "fastqc", "数据质控", "测序质量"],
    "rnaseq_align": ["rnaseq比对", "rna-seq比对", "比对分析", "mapping", "hisat2", "star", "比对到基因组"],
    "rnaseq_quant": ["rnaseq定量", "rna-seq定量", "表达定量", "定量分析", "featurecounts", "计数"],
    "rnaseq_sample_qc": ["样本质量", "样本检测", "sample qc", "sample quality", "pca", "样本相关性", "样本聚类"],
    "rnaseq_wgcna": ["wgcna", "共表达", "加权共表达", "模块性状", "module trait"],
    "rnaseq_de": ["差异表达", "deg", "差异基因", "deseq2", "edger", "差异分析"],
    "rnaseq_go_enrich": ["go富集", "go分析", "go enrichment"],
    "rnaseq_kegg_enrich": ["kegg富集", "kegg分析", "kegg", "pathway"],
    "rnaseq_fgsea": ["fgsea", "gsea", "排序富集", "基因集富集"],
    "rnaseq_candidate_family": ["候选家族", "候选基因家族", "家族排名", "tf注释", "tf family", "tffamily", "pfam注释", "deg注释", "差异基因注释"],
    "rnaseq_report": ["rnaseq结果报告", "rna-seq结果报告", "转录组结果报告", "rnaseq报告", "rna-seq报告"],
    "rnaseq_enrich": ["富集分析", "功能富集"],
    "identify": ["鉴定", "identify", "识别", "找出", "鉴定基因家族"],
    "identify_refine": ["修订成员", "修订鉴定成员", "修改成员", "删除基因", "删除", "去除基因", "去掉基因", "移除基因", "补充基因", "遗漏基因", "重建蛋白", "人工修订id", "curated members", "refine identify"],
    "subfamily_confirm": ["确认亚家族", "亚家族确认", "确认亚家族标签", "人工确认亚家族", "confirmed subfamily", "subfamily confirm", "subfamily_confirm"],
    "phylogeny": ["进化树", "系统发育", "phylogeny", "建树", "ml树", "nj树", "系统进化", "进化分析"],
    "motif": ["motif", "基序", "保守序列", "meme"],
    "domain": ["结构域", "domain", "pfam", "interpro"],
    "structure": ["基因结构", "structure", "外显子", "内含子"],
    "promoter": ["启动子", "promoter", "顺式作用", "plantcare"],
    "synteny": ["种内共线性", "种内synteny", "mcscanx", "segmental", "tandem", "intraspecies"],
    "karyotype": ["核型", "karyotype", "染色体"],
    "expression": ["表达", "expression", "热图", "heatmap"],
    "interspecies_synteny": ["种间共线性", "物种间共线性", "跨物种共线性", "interspecies"],
    "kaks": ["kaks", "ka/ks", "选择压力", "dnds"],
    "ppi": ["ppi", "蛋白互作", "protein-protein", "string"],
    "pep": ["蛋白理化", "pep", "分子量", "等电点"],
    "combo": ["combo", "组合图", "综合图", "tree motif structure domain", "tree+motif", "组合可视化"],
    "genome_prepare": ["数据准备", "基因组准备", "genome_prepare", "prepare genome"],
    "prepare_organize": ["整理文件", "组织文件", "整理数据", "标准化命名", "重命名文件", "config整理"],
    "prepare_normalize_gff": ["修订gff", "规范化gff", "gff id", "修订id", "规范化id", "gff整理"],
    "prepare_longest_transcript": ["最长转录本", "最长cds", "primary transcript", "longest transcript", "提取最长"],
    "prepare_extract_pep": ["提取pep", "提取蛋白", "提取蛋白序列", "gffread pep", "extract pep"],
    "prepare_extract_cds": ["提取cds", "提取cds序列", "gffread cds", "extract cds"],
    "sequence_extract_by_id": ["按id提取序列", "按id提取蛋白", "按id提取cds", "从pep中提取", "从fasta中提取", "extract sequence by id"],
    "prepare_filter_fasta": ["按id提取", "按id筛选", "提取fasta", "筛选fasta", "subseq"],
    "prepare_simplify_ncbi": ["ncbi id", "ncbi修订", "ncbi简化", "ncbi header", "简化ncbi"],
    "auto_continue": ["接下来的所有", "剩余", "继续执行", "接着做", "未完成", "剩下的"],
}

RNASEQ_MARKERS = ["rnaseq", "rna-seq", "rna seq", "转录组", "fastq"]
RNASEQ_STAGES = {
    "rnaseq_qc",
    "rnaseq_align",
    "rnaseq_quant",
    "rnaseq_sample_qc",
    "rnaseq_wgcna",
    "rnaseq_de",
    "rnaseq_go_enrich",
    "rnaseq_kegg_enrich",
    "rnaseq_fgsea",
    "rnaseq_candidate_family",
    "rnaseq_report",
    "rnaseq_enrich",
}

# 基因家族正则模式
GENE_FAMILY_PATTERN = re.compile(r'\b([A-Z]{2,8})\b')

# 物种关键词映射
SPECIES_KEYWORDS = {
    "Ft": ["ft", "苦荞", "tataricum", "buckwheat"],
    "At": ["at", "拟南芥", "thaliana", "arabidopsis"],
    "Os": ["os", "水稻", "sativa", "rice"],
    "Zm": ["zm", "玉米", "maize", "mays"],
    "Sl": ["sl", "番茄", "tomato", "lycopersicum"],
    "Lc": ["lc", "荔枝", "litchi", "chinensis"],
    "Dl": ["dl", "龙眼", "longan"],
    "Py": ["py"],
    "Cp": ["cp", "papaya"],
    "Lg": ["lg"],
}


def _complete_dependencies(stages: List[str]) -> List[str]:
    """根据依赖关系补充缺失的阶段"""
    return catalog_complete_dependencies(stages)


def fast_intent_recognition(query: str) -> Optional[Dict[str, Any]]:
    """
    快速意图识别 - 基于关键词匹配
    
    Returns:
        dict: {"stage": str, "stages": List[str], "gene_family": str, "species": str, "confidence": float}
        None: 无法识别
    """
    query_lower = query.lower()
    report_intent = resolve_gene_family_report_intent(query)
    if report_intent:
        return {
            "stage": "gene_family_report",
            **report_intent,
        }
    population = resolve_population_intent(query)
    if population:
        return {
            "stage": population["target"], "stages": population["stages"],
            "gene_family": "", "species": "", "prefix": population["prefix"],
            "params": population["params"],
            "confidence": 0.98, "method": "population_deterministic",
        }
    gwas = resolve_gwas_intent(query)
    if gwas:
        return {
            "stage": gwas["target"],
            "stages": gwas["stages"],
            "gene_family": "",
            "species": "",
            "prefix": "cohort",
            "params": {},
            "confidence": 0.98,
            "method": "gwas_deterministic",
        }
    try:
        from gfa_core.mas.utils.intent_parser import (
            parse_msa_region_plot_request,
            parse_ncbi_fetch_request,
            parse_sequence_extract_request,
        )

        ncbi_params = parse_ncbi_fetch_request(query)
        if ncbi_params:
            return {
                "stage": "ncbi_fetch_sequences",
                "stages": ["ncbi_fetch_sequences"],
                "gene_family": "NCBI.fetch",
                "species": ncbi_params.get("ncbiSpecies") or ncbi_params.get("ncbiTaxid") or "",
                "prefix": ncbi_params.get("prefix", ""),
                "task_kind": "utility",
                "taskType": "utility",
                "utility_tool": "ncbi_fetch_sequences",
                "params": ncbi_params,
                "confidence": 0.95,
                "method": "utility_parser",
            }

        sequence_extract_params = parse_sequence_extract_request(query)
        if sequence_extract_params:
            return {
                "stage": "sequence_extract_by_id",
                "stages": ["sequence_extract_by_id"],
                "gene_family": "Sequence.extract",
                "species": "",
                "prefix": sequence_extract_params.get("prefix", ""),
                "task_kind": "utility",
                "taskType": "utility",
                "utility_tool": "fasta_header_clean" if sequence_extract_params.get("sequenceExtractCleanHeaders") else "sequence_extract_by_id",
                "params": sequence_extract_params,
                "confidence": 0.95,
                "method": "utility_parser",
            }

        msa_region_params = parse_msa_region_plot_request(query)
        if msa_region_params:
            return {
                "stage": "msa_region_plot",
                "stages": ["msa_region_plot"],
                "gene_family": "MSA.region",
                "species": "",
                "prefix": msa_region_params.get("prefix", ""),
                "task_kind": "utility",
                "taskType": "utility",
                "utility_tool": "msa_region_plot",
                "params": msa_region_params,
                "confidence": 0.95,
                "method": "utility_parser",
            }
    except Exception:
        pass
    excluded_stages = extract_negated_target_stages(query)
    if excluded_stages:
        parsed = parse_intent_from_query(query)
        target_stages = list(parsed.get("stages") or [])
        if not target_stages:
            return None
        all_stages = _complete_dependencies(target_stages)
        return {
            "stage": parsed.get("primary_stage") or target_stages[0],
            "stages": all_stages,
            "gene_family": parsed.get("gene_family", ""),
            "species": parsed.get("species", ""),
            "prefix": parsed.get("prefix", ""),
            "target_species": parsed.get("target_species", []),
            "reference_species": parsed.get("reference_species", []),
            "comparative_species": parsed.get("comparative_species", []),
            "scopeMode": parsed.get("scopeMode", "target_only"),
            "params": parsed.get("params", {}),
            "confidence": parsed.get("confidence", 0.9),
            "task_kind": parsed.get("task_kind", "pipeline"),
            "method": "keyword_negation",
        }
    has_rnaseq_marker = any(marker in query_lower for marker in RNASEQ_MARKERS)
    if has_rnaseq_marker and any(
        keyword in query_lower
        for keyword in ["生成结果报告", "打开结果报告", "结果报告", "生成报告", "打开报告"]
    ):
        action = "open" if "打开" in query_lower else "generate"
        return {
            "stage": "rnaseq_report",
            "stages": ["rnaseq_report"],
            "gene_family": "",
            "species": "",
            "prefix": "",
            "params": {"rnaseqReportAction": action},
            "confidence": 0.99,
            "method": "rnaseq_report_deterministic",
        }
    header_clean_context = any(kw in query_lower for kw in [
        "id处理", "处理id", "修订id", "简化id", "清理id", "id简化",
        "去掉注释", "去除注释", "注释信息", "长注释",
        "clean header", "simple header", "simplify header", "fasta header", "序列头",
    ])
    if header_clean_context and any(kw in query_lower for kw in ["蛋白", "protein", "pep", "序列", "fasta", "家族"]):
        species = ""
        for sp, keywords in SPECIES_KEYWORDS.items():
            if any(kw.lower() in query_lower for kw in keywords):
                species = sp
                break
        family = ""
        for item in ["SPL", "NAC", "WRKY", "MYB", "BHLH", "WD40", "AP2", "ERF"]:
            if item.lower() in query_lower:
                family = item
                break
        gf_match = GENE_FAMILY_PATTERN.search(query)
        if not family and gf_match and gf_match.group(1).upper() not in {"RNA", "SEQ", "FASTQ"}:
            family = gf_match.group(1)
        return {
            "stage": "sequence_extract_by_id",
            "stages": ["sequence_extract_by_id"],
            "gene_family": "Sequence.extract",
            "species": species,
            "prefix": f"{species}.{family}" if species and family else "sequence_clean",
            "task_kind": "utility",
            "taskType": "utility",
            "utility_tool": "fasta_header_clean",
            "params": {
                "prefix": f"{species}.{family}" if species and family else "sequence_clean",
                "sequenceExtractSeqType": "pep" if any(kw in query_lower for kw in ["蛋白", "protein", "pep"]) else "auto",
                "sequenceExtractRenameMode": "simple",
                "sequenceExtractCleanHeaders": True,
            },
            "confidence": 0.95,
            "method": "keyword",
        }
    extraction_context = any(kw in query_lower for kw in ["提取", "extract", "gffread", "筛选", "subseq"])
    explicit_gene_family_context = any(kw in query_lower for kw in ["基因家族", "家族表达", "表达量", "热图", "heatmap"])
    curated_member_context = (
        any(kw in query_lower for kw in ["修订后的", "修订后", "人工修订", "curated", "edited"])
        and any(kw in query_lower for kw in ["家族id", "成员id", "基因家族id", "member id", "members"])
    )
    prepare_extract_stages = []
    explicit_gff_extract_context = extraction_context and any(
        kw in query_lower for kw in ["gff", "gff3", "gtf", "gffread", "genome", "基因组"]
    )
    if explicit_gff_extract_context and not curated_member_context:
        if "pep" in query_lower or "蛋白" in query_lower:
            prepare_extract_stages.append("prepare_extract_pep")
        if "cds" in query_lower:
            prepare_extract_stages.append("prepare_extract_cds")
    if any(kw in query_lower for kw in ANALYSIS_KEYWORDS["wgs_align"]):
        detected_stage = "wgs_align"
        max_score = 5
    elif is_gwas_fmt_intent(query):
        detected_stage = "gwas_fmt"
        max_score = 5
    elif curated_member_context or any(kw in query_lower for kw in ANALYSIS_KEYWORDS["identify_refine"]):
        detected_stage = "identify_refine"
        max_score = 5
    else:
        detected_stage = None
        max_score = 0
    
    # 1. 识别分析类型
    if not detected_stage and any(kw in query_lower for kw in ["候选家族", "候选基因家族", "家族排名", "tf注释", "tf family", "tffamily", "pfam注释", "deg注释", "差异基因注释"]):
        detected_stage = "rnaseq_candidate_family"
        max_score = 5
    if not detected_stage and any(kw in query_lower for kw in ["fgsea", "gsea", "排序富集", "基因集富集"]):
        detected_stage = "rnaseq_fgsea"
        max_score = 5
    if not detected_stage and any(kw in query_lower for kw in ["wgcna", "共表达", "加权共表达", "模块性状"]):
        detected_stage = "rnaseq_wgcna"
        max_score = 5
    if not detected_stage and any(kw in query_lower for kw in ["样本质量", "样本质量评估", "样本检测", "sample qc", "sample quality", "pca", "样本相关"]):
        detected_stage = "rnaseq_sample_qc"
        max_score = 5
    if not detected_stage and any(kw in query_lower for kw in ["差异表达", "差异分析", "差异基因", "deg", "deseq2", "edger"]) and not explicit_gene_family_context:
        detected_stage = "rnaseq_de"
        max_score = 5
    if not detected_stage and any(kw in query_lower for kw in ["质控", "qc", "fastqc", "测序质量"]):
        detected_stage = "rnaseq_qc"
        max_score = 5
    if not detected_stage and any(kw in query_lower for kw in ["定量", "quant", "featurecounts", "count矩阵", "计数矩阵"]):
        detected_stage = "rnaseq_quant"
        max_score = 5
    if not detected_stage and any(kw in query_lower for kw in ["比对", "align", "mapping", "hisat2", "star"]) and not any(kw in query_lower for kw in ["多序列", "msa", "multiple alignment"]):
        detected_stage = "rnaseq_align"
        max_score = 5
    if not detected_stage and any(kw in query_lower for kw in ["富集", "enrich", "go富集", "go分析", "kegg", "通路", "pathway"]):
        if "kegg" in query_lower or "通路" in query_lower or "pathway" in query_lower:
            detected_stage = "rnaseq_kegg_enrich"
        elif re.search(r"(^|[^a-z])go([^a-z]|$)", query_lower) or "go富集" in query_lower or "go分析" in query_lower:
            detected_stage = "rnaseq_go_enrich"
        else:
            detected_stage = "rnaseq_go_enrich"
        max_score = 5

    if not detected_stage and has_rnaseq_marker:
        if any(kw in query_lower for kw in ["候选家族", "候选基因家族", "家族排名", "tf注释", "tf family", "tffamily", "pfam注释", "deg注释", "差异基因注释"]):
            detected_stage = "rnaseq_candidate_family"
            max_score = 5
        elif any(kw in query_lower for kw in ["fgsea", "gsea", "排序富集", "基因集富集"]):
            detected_stage = "rnaseq_fgsea"
            max_score = 5
        elif any(kw in query_lower for kw in ["wgcna", "共表达", "加权共表达", "模块性状"]):
            detected_stage = "rnaseq_wgcna"
            max_score = 5
        elif any(kw in query_lower for kw in ["样本质量", "样本检测", "sample qc", "sample quality", "pca", "样本相关"]):
            detected_stage = "rnaseq_sample_qc"
            max_score = 5
        elif any(kw in query_lower for kw in ["kegg", "pathway", "通路"]):
            detected_stage = "rnaseq_kegg_enrich"
            max_score = 5
        elif any(kw in query_lower for kw in ["go"]):
            detected_stage = "rnaseq_go_enrich"
            max_score = 5
        elif any(kw in query_lower for kw in ["富集", "enrich", "功能富集"]):
            detected_stage = "rnaseq_go_enrich"
            max_score = 5
        elif any(kw in query_lower for kw in ["差异", "deg", "deseq2", "edger"]):
            detected_stage = "rnaseq_de"
            max_score = 5
        elif any(kw in query_lower for kw in ["定量", "quant", "count", "featurecounts"]):
            detected_stage = "rnaseq_quant"
            max_score = 5
        elif any(kw in query_lower for kw in ["比对", "align", "mapping", "hisat2", "star"]):
            detected_stage = "rnaseq_align"
            max_score = 5
        elif any(kw in query_lower for kw in ["质控", "qc", "fastqc", "测序质量"]):
            detected_stage = "rnaseq_qc"
            max_score = 5
        elif any(kw in query_lower for kw in ["分析", "执行", "运行", "开始", "流程", "pipeline"]):
            detected_stage = "rnaseq_de"
            max_score = 5
    
    if not detected_stage:
        for stage, keywords in ANALYSIS_KEYWORDS.items():
            if stage in {
                "sequence_extract_by_id",
                "prepare_organize",
                "prepare_normalize_gff",
                "prepare_longest_transcript",
                "prepare_extract_pep",
                "prepare_extract_cds",
                "prepare_filter_fasta",
                "prepare_simplify_ncbi",
            }:
                continue
            if stage in RNASEQ_STAGES and not has_rnaseq_marker:
                continue
            if stage == "expression" and has_rnaseq_marker:
                continue
            score = 0
            for kw in keywords:
                if kw.lower() in query_lower:
                    score += 1
                    # 完全匹配加分
                    if kw.lower() == query_lower.strip():
                        score += 2
            if score > max_score:
                max_score = score
                detected_stage = stage
    
    if prepare_extract_stages:
        detected_stage = prepare_extract_stages[0]
        max_score = 5
    elif not detected_stage or max_score == 0:
        return None
    
    # 2. 识别基因家族（大写2-8字母）
    gene_family = ""
    gf_match = GENE_FAMILY_PATTERN.search(query)
    if gf_match and gf_match.group(1).upper() not in {"RNA", "SEQ", "FASTQ"}:
        gene_family = gf_match.group(1).upper()
    if not gene_family:
        for item in ["SPL", "NAC", "WRKY", "MYB", "BHLH", "WD40", "AP2", "ERF"]:
            if item.lower() in query_lower:
                gene_family = item
                break
    
    # 3. 识别物种
    species = ""
    species_list: List[str] = []
    code_mentions = re.findall(r"(?<![A-Za-z0-9])(?:Ft|At|Os|Zm|Sl|Lc|Dl|Py|Cp|Lg)(?![A-Za-z0-9])", query, flags=re.IGNORECASE)
    if code_mentions:
        canonical = {code.lower(): code for code in SPECIES_KEYWORDS}
        for item in code_mentions:
            sp = canonical.get(item.lower())
            if sp and sp not in species_list:
                species_list.append(sp)
        if len(species_list) == 1:
            species = species_list[0]
        elif len(species_list) > 1:
            species = "Multi"
    species_detected = False
    if species_list:
        species_detected = True
    else:
        for sp, keywords in SPECIES_KEYWORDS.items():
            for kw in keywords:
                if kw.lower() in query_lower:
                    species = sp
                    species_list = [sp]
                    species_detected = True
                    break
            if species_detected:
                break
    if detected_stage == "identify" and not species_detected and any(kw in query_lower for kw in ["基因家族成员鉴定", "基因家族鉴定", "identify"]):
        species = "Multi"
    
    # 4. 计算置信度
    confidence = min(0.95, 0.7 + max_score * 0.05)
    
    # 5. 展开完整依赖链
    generic_enrich = (
        detected_stage == "rnaseq_go_enrich"
        and any(kw in query_lower for kw in ["富集", "enrich", "功能富集"])
        and not any(kw in query_lower for kw in ["go", "kegg", "pathway", "通路"])
    )
    generic_rnaseq = (
        detected_stage == "rnaseq_de"
        and has_rnaseq_marker
        and any(kw in query_lower for kw in ["分析", "执行", "运行", "开始", "流程", "pipeline"])
        and not any(kw in query_lower for kw in ["差异", "deg", "deseq2", "edger"])
    )
    if prepare_extract_stages:
        all_stages = _complete_dependencies(prepare_extract_stages)
    elif generic_rnaseq:
        all_stages = _complete_dependencies(["rnaseq_de", "rnaseq_go_enrich", "rnaseq_kegg_enrich", "rnaseq_fgsea", "rnaseq_wgcna", "rnaseq_candidate_family"])
    elif generic_enrich:
        all_stages = _complete_dependencies(["rnaseq_go_enrich", "rnaseq_kegg_enrich"])
    else:
        all_stages = _complete_dependencies([detected_stage])
    
    scope_selection = parse_gene_family_scope_selection(query)
    if scope_selection.get("targetSpecies"):
        species_list = list(scope_selection.get("targetSpecies") or [])
        species = species_list[0] if len(species_list) == 1 else "Multi"
    params = {}
    if any(scope_selection.get(key) for key in ("targetSpecies", "referenceSpecies", "comparativeSpecies")):
        params.update(scope_selection)
        if gene_family:
            params["geneFamily"] = gene_family

    return {
        "stage": detected_stage,
        "stages": all_stages,  # 完整的分析流程
        "gene_family": gene_family,
        "species": species,
        "prefix": f"{species}.{gene_family}" if species and gene_family else (gene_family or ""),
        "target_species": species_list,
        "reference_species": scope_selection.get("referenceSpecies", []),
        "comparative_species": scope_selection.get("comparativeSpecies", []),
        "scopeMode": scope_selection.get("scopeMode", "target_only"),
        "params": params,
        "confidence": confidence,
        "task_kind": (
            "utility" if detected_stage in {
                "prepare_organize",
                "prepare_normalize_gff",
                "prepare_longest_transcript",
                "prepare_extract_pep",
                "prepare_extract_cds",
                "sequence_extract_by_id",
                "prepare_filter_fasta",
                "prepare_simplify_ncbi",
            } else "repair" if detected_stage in {"identify_refine", "subfamily_confirm"} else "pipeline"
        ),
        "method": "keyword"  # 标记为关键词匹配
    }


def should_use_fast_intent(query: str) -> bool:
    """
    判断是否可以使用快速意图识别
    """
    # 简单查询使用快速识别
    if len(query) < 100:
        return True
    
    # 包含明确关键词的使用快速识别
    all_keywords = []
    for kws in ANALYSIS_KEYWORDS.values():
        all_keywords.extend(kws)
    
    query_lower = query.lower()
    for kw in all_keywords:
        if kw.lower() in query_lower:
            return True
    
    return False
