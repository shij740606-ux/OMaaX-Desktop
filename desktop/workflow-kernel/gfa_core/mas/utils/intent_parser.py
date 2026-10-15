"""
Intent Parser - 硬编码规则解析用户意图
从 orchestrator.py 提取并扩展
"""
import re
from typing import Dict, List, Optional

from gfa_core.mas.stage_catalog import complete_dependencies as catalog_complete_dependencies
from gfa_core.mas.stage_catalog import STAGE_ORDER

# 常见基因家族
COMMON_FAMILIES = ['SPL', 'NAC', 'WRKY', 'MYB', 'bHLH', 'WD40', 'AP2', 'ERF']

# 需要排除的中文请求词（Python 3 的 \w 匹配 Unicode 字母）
EXCLUDE_WORDS = {
    '帮我', '执行', '分析', '进行', '运行', '开始', '帮我执行', 
    '帮我运行', '请帮我', '请你', '想要', '希望', '需要',
    '鉴定分析', '进化分析', '表达分析', '结构分析', '启动子分析',
    '共线性分析', '核型分析', '蛋白分析', 'motif分析', 'domain分析',
    'ID', 'IDS'
}

# 核心分析阶段（完整流程用）- 必须与 manifest 中的 stage 字段一致。
# “基因家族分析/全流程”默认不包含 Expression；Expression 需要外部
# FPKM/TPM 矩阵和 sample.txt，只有用户明确点名表达分析时才加入。
# KaKs/PPI 需要 Ortholog，作为可执行上游阶段显式纳入全流程计划。
CORE_STAGES = [
    "identify", "phylogeny", "motif", "domain", "structure", "combo",
    "promoter", "tfbs", "synteny", "interspecies_synteny", "karyotype", "pep",
    "ortholog", "kaks", "ppi"
]

# 完整流程关键词
COMPLETE_WORKFLOW_KEYWORDS = [
    "基因家族分析", "完整分析", "全流程", "全部阶段", "完整流程"
]

# 分析阶段关键词映射（key 必须与 manifest 中的 stage 字段一致）
STAGE_KEYWORDS = {
    "gwas_fmt": ["gwas格式", "gwas format", "tassel转换", "tassel convert", "hapmap转换", "vcf转hapmap", "vcf to hapmap"],
    "identify": ["identify", "鉴定", "鉴定分析", "基因鉴定", "gene-identify"],
    "identify_refine": ["修订成员", "修订鉴定成员", "修改成员", "删除基因", "删除", "去除基因", "去掉基因", "移除基因", "补充基因", "遗漏基因", "重建蛋白", "人工修订id", "curated members", "refine identify"],
    "phylogeny": ["phylogeny", "进化", "进化树", "系统发育", "树"],
    "reference_classification": ["预测亚家族", "参考亚家族", "亚家族分类", "参考分类", "亚家族", "subfamily prediction", "subfamily", "reference classification", "reference_classification"],
    "subfamily_confirm": ["确认亚家族", "亚家族确认", "确认亚家族标签", "确认分类", "人工确认亚家族", "confirmed subfamily", "subfamily confirm", "subfamily_confirm"],
    "motif": ["motif", "基序", "motif分析", "motif-analysis"],
    "domain": ["domain", "结构域", "domain分析", "pfam", "domain-analysis"],
    "structure": ["structure", "基因结构", "结构分析", "蛋白结构"],
    "promoter_plot_revise": ["promoter_plot_revise", "启动子diy", "启动子 diy", "启动子自定义", "启动子自定义绘图", "启动子重绘", "启动子二次绘图", "plantcare重绘", "promoter custom", "promoter replot", "diy绘图"],
    "tfbs": [
        "tfbs", "fimo", "tf结合", "tf 结合", "转录因子结合", "转录因子结合位点",
        "启动子结合位点", "结合位点", "binding site", "binding-site", "binding_site",
        "tf motif", "tf motif scanning", "motif binding"
    ],
    "promoter": [
        "promoter", "启动子", "启动子分析", "顺式", "顺式元件", "plantcare",
        "cis-element", "cis element", "promoter-analysis"
    ],
    "synteny": ["synteny", "种内共线性", "种内共线性分析", "mcscanx", "intraspecies"],
    "karyotype": ["karyotype", "核型", "染色体分布", "核型分析", "染色体定位"],
    "interspecies_synteny": ["interspecies", "种间", "种间共线性", "跨物种"],
    "expression": ["expression", "表达", "表达分析", "表达量"],
    "kaks": ["kaks", "kaks分析", "进化速率", "选择压力", "ka/ks"],
    "ppi": ["ppi", "蛋白互作", "互作网络", "protein interaction"],
    "pep": ["pep", "蛋白特征", "蛋白性质", "等电点", "分子量", "亚细胞定位"],
    "combo": ["combo", "组合图", "综合图", "组合可视化", "tree motif structure domain", "tree+motif"],
    "annotation": ["背景注释", "功能注释", "基因组注释", "自动注释", "eggnog", "annotation"],
    "genome_prepare": ["数据准备", "基因组准备", "genome_prepare", "prepare genome"],
    "sequence_extract_by_id": ["按id提取序列", "按id提取蛋白", "按id提取cds", "从pep中提取", "从fasta中提取", "extract sequence by id"],
    "msa_region_plot": ["msa区间", "msa绘图", "msa二次绘图", "msa局部绘图", "多序列比对区间", "比对区间绘图", "保守区间绘图", "保守位置绘图", "alignment region", "msa region"],
    "prepare_organize": ["整理文件", "组织文件", "整理数据", "标准化命名", "重命名文件", "config整理"],
    "prepare_normalize_gff": ["修订gff", "规范化gff", "gff id", "修订id", "规范化id", "gff整理"],
    "prepare_longest_transcript": ["最长转录本", "最长cds", "primary transcript", "longest transcript", "提取最长"],
    "prepare_extract_pep": ["提取pep", "提取蛋白", "提取蛋白序列", "gffread pep", "extract pep"],
    "prepare_extract_cds": ["提取cds", "提取cds序列", "gffread cds", "extract cds"],
    "prepare_filter_fasta": ["按id提取", "按id筛选", "提取fasta", "筛选fasta", "subseq"],
    "prepare_simplify_ncbi": ["ncbi id", "ncbi修订", "ncbi简化", "ncbi header", "简化ncbi"],
    # RNA-seq 相关（独立流程）
    "rnaseq_qc": ["rnaseq质控", "质控", "qc"],
    "rnaseq_align": ["rnaseq比对", "比对", "align", "hisat2"],
    "rnaseq_quant": ["rnaseq定量", "定量", "quant", "featurecounts"],
    "rnaseq_sample_qc": ["样本质量", "样本检测", "sample qc", "sample quality", "pca", "样本相关性", "样本聚类"],
    "rnaseq_wgcna": ["wgcna", "共表达", "加权共表达", "模块性状", "module trait"],
    "rnaseq_de": ["差异表达", "de", "差异分析", "deseq2"],
    "rnaseq_fgsea": ["fgsea", "gsea", "排序富集", "基因集富集"],
    "rnaseq_go_enrich": ["rnaseq go富集", "rnaseq_go富集", "rna go富集", "go富集", "go分析", "富集分析", "enrich"],
    "rnaseq_kegg_enrich": ["rnaseq kegg", "rnaseq_kegg", "rna kegg", "kegg富集", "kegg分析", "kegg", "富集分析", "enrich"],
    "rnaseq_enrich": ["富集分析", "enrich", "go分析", "kegg", "clusterprofiler"],
    "rnaseq_candidate_family": ["候选家族", "候选基因家族", "家族排名", "tf注释", "tf family", "tffamily", "pfam注释", "deg注释", "差异基因注释", "差异基因家族", "candidate family"],
    "rnaseq_report": ["rnaseq结果报告", "rna-seq结果报告", "转录组结果报告", "rnaseq报告", "rna-seq报告"],
    "ncbi_fetch_sequences": ["ncbi下载", "ncbi序列", "下载序列", "下载蛋白", "获取蛋白", "下载protein", "下载cds", "protein和cds", "蛋白和cds", "基因list", "gene list", "gene_list", "id列表", ".ids"],
}


def is_gwas_fmt_intent(query: str) -> bool:
    """Return whether a command explicitly requests GWAS format conversion."""
    query_lower = str(query or "").lower()
    compact = re.sub(r"[\s_-]+", "", query_lower)
    explicit_conversion = any(
        keyword in compact
        for keyword in (
            "gwas格式",
            "gwasformat",
            "tassel转换",
            "tasselconvert",
            "hapmap转换",
            "vcf格式转换",
            "vcf转hapmap",
            "vcf转换为hapmap",
            "vcftohapmap",
        )
    )
    return explicit_conversion


def is_generic_gwas_intent(query: str) -> bool:
    """Return whether the request selects the complete GWAS workflow."""
    resolved = resolve_gwas_intent(query)
    return bool(resolved and resolved.get("full_chain"))


def parse_population_input_params(query: str) -> Dict[str, str]:
    """Extract explicitly named population VCF and sample-group paths."""
    text = re.sub(r"\s*/\s*", "/", str(query or "").strip())
    params = {"prefix": "cohort"}

    def anchored_path(anchor: str, suffix: str) -> Optional[str]:
        quoted = re.search(
            rf"(?:{anchor})\s*(?:文件)?\s*[=:：]?\s*[\"“']([^\"”']+?{suffix})[\"”']",
            text,
            flags=re.IGNORECASE,
        )
        if quoted:
            return quoted.group(1).strip()
        unquoted = re.search(
            rf"(?:{anchor})\s*(?:文件)?\s*[=:：]?\s*([^\s，。；,;\"“”']+?{suffix})(?=$|[\s，。；,;])",
            text,
            flags=re.IGNORECASE,
        )
        return unquoted.group(1).strip() if unquoted else None

    vcf = anchored_path(r"使用|use|vcf", r"\.vcf(?:\.gz)?")
    if vcf:
        params["populationVcf"] = vcf
    group = anchored_path(r"分组表|group\s+table|sample\s+group", r"\.(?:tsv|txt|csv)")
    if group:
        params["populationSampleGroup"] = group
    return params


def resolve_population_intent(query: str) -> Optional[Dict]:
    """Resolve population-genomics requests without stealing gene-family trees."""
    text = str(query or "").lower()
    compact = re.sub(r"[\s_-]+", "", text)
    if any(token in compact for token in ("基因家族", "genefamily")):
        return None
    target: Optional[str] = None
    full_chain = False
    population_context = any(token in compact for token in ("群体", "population", "群体遗传"))
    if not population_context:
        return None
    if any(token in compact for token in ("群体系统树", "群体进化树", "populationphylogeny", "populationtree")):
        target = "pop_phylogeny"
    elif any(token in compact for token in ("群体结构", "admixture")):
        target = "pop_structure"
    elif any(token in compact for token in ("ld衰减", "连锁不平衡衰减", "lddecay")):
        target = "pop_ld_decay"
    elif any(token in compact for token in ("snp密度", "变异密度", "snpdensity")):
        target = "pop_snp_density"
    elif "pca" in compact or "主成分" in compact:
        target = "pop_pca"
    elif any(token in compact for token in ("群体遗传分析", "群体基因组分析", "populationgenomics")):
        target = "pop_snp_density"
        full_chain = True
    if target is None:
        return None
    stages = ["pop_pca", "pop_phylogeny", "pop_structure", "pop_ld_decay", "pop_snp_density"] if full_chain else [target]
    return {
        "target": target,
        "stages": stages,
        "full_chain": full_chain,
        "prefix": "cohort",
        "params": parse_population_input_params(query),
    }


def resolve_gwas_intent(query: str) -> Optional[Dict]:
    """Resolve one deterministic GWAS target and its dependency closure."""
    text = str(query or "").lower()
    compact = re.sub(r"[\s_-]+", "", text)
    target: Optional[str] = None
    full_chain = False

    if is_gwas_fmt_intent(query):
        target = "gwas_fmt"
    elif "emmax" in compact and any(word in compact for word in ("绘图", "作图", "manhattan", "qq图", "plot")):
        target = "gwas_emplot"
    elif ("mlm" in compact or "cmlm" in compact) and any(
        word in compact for word in ("绘图", "作图", "manhattan", "qq图", "plot")
    ):
        target = "gwas_plot"
    elif any(word in compact for word in ("亲缘关系", "亲缘矩阵", "kinship")):
        target = "gwas_kinship"
    elif any(word in compact for word in ("表型准备", "表型数据", "性状准备", "traitprepare")):
        target = "gwas_trait"
    elif any(word in compact for word in ("协变量", "covariate", "q矩阵", "qmatrix")):
        target = "gwas_cov"
    elif "emmax" in compact:
        target = "gwas_emmax"
    elif "cmlm" in compact or "mlm" in compact or "混合线性模型" in compact:
        target = "gwas_mlm"
    elif "glm" in compact or "一般线性模型" in compact or "固定效应模型" in compact:
        target = "gwas_glm"
    elif "gwas" in compact and any(word in compact for word in ("绘图", "作图", "manhattan", "qq图", "plot")):
        target = "gwas_plot"
    elif "gwas" in compact:
        target = "gwas_glm"
        full_chain = True

    if target is None:
        return None
    targets = ["gwas_plot", "gwas_emplot", "gwas_glm"] if full_chain else [target]
    return {
        "target": target,
        "stages": catalog_complete_dependencies(targets),
        "full_chain": full_chain,
    }

# These are direct utility/repair operations, not normal pipeline-analysis
# stages. They are handled with stricter context below so broad words such as
# "提取蛋白" or "修订ID" cannot steal a gene-family/RNA-seq pipeline request.
UTILITY_KEYWORD_STAGES = {
    "sequence_extract_by_id",
    "msa_region_plot",
    "promoter_plot_revise",
    "prepare_organize",
    "prepare_normalize_gff",
    "prepare_longest_transcript",
    "prepare_extract_pep",
    "prepare_extract_cds",
    "prepare_filter_fasta",
    "prepare_simplify_ncbi",
    "ncbi_fetch_sequences",
}
REPAIR_KEYWORD_STAGES = {"identify_refine", "subfamily_confirm"}

RNASEQ_MARKERS = ["rnaseq", "rna-seq", "rna seq", "转录组", "fastq"]
RNASEQ_STAGES = {
    "rnaseq_qc",
    "rnaseq_align",
    "rnaseq_quant",
    "rnaseq_sample_qc",
    "rnaseq_wgcna",
    "rnaseq_de",
    "rnaseq_fgsea",
    "rnaseq_go_enrich",
    "rnaseq_kegg_enrich",
    "rnaseq_enrich",
    "rnaseq_candidate_family",
    "rnaseq_report",
}


def resolve_gene_family_report_intent(query: str) -> Optional[Dict]:
    """Resolve only explicit gene-family report generate/open requests."""
    text = str(query or "").lower()
    compact = re.sub(r"\s+", "", text)
    if any(marker.replace(" ", "") in compact for marker in RNASEQ_MARKERS + ["转录组"]):
        return None
    action = ""
    if any(phrase in compact for phrase in ("打开基因家族结果报告", "打开基因家族报告", "openresultreport")):
        action = "open"
    elif any(phrase in compact for phrase in ("生成基因家族结果报告", "生成基因家族报告", "生成结果报告", "generateresultreport")):
        action = "generate"
    if not action:
        return None
    return {
        "gene_family": "",
        "species": "",
        "stages": ["gene_family_report"],
        "primary_stage": "gene_family_report",
        "task_type": "report",
        "task_kind": "report",
        "params": {"geneFamilyReportAction": action, "reportAction": action},
        "confidence": 1.0,
        "need_clarify": False,
        "clarify_type": None,
        "message": None,
        "method": "gene_family_report_deterministic",
    }

# 物种关键词映射
SPECIES_KEYWORDS = {
    "Ft": ["ft", "tartary", "苦荞", "荞麦"],
    "At": ["at", "arabidopsis", "拟南芥"],
    "Os": ["os", "rice", "水稻", "稻"],
    "Ls": ["ls", "lettuce", "lactuca", "生菜", "莴苣"],
    "Zm": ["zm", "maize", "玉米"],
    "Sl": ["sl", "tomato", "番茄"],
    "Lc": ["lc", "litchi", "荔枝"],
    "Dl": ["dl", "longan", "龙眼"],
    "Ppy": ["ppy", "ppyrus", "pyrus"],
    "Py": ["py"],
    "Cp": ["cp", "papaya", "木瓜"],
    "Vv": ["vv", "grape", "葡萄"],
    "Lg": ["lg"],
    "Gm": ["gm"],
    "Br": ["br"],
    "Pt": ["pt"],
    "Sb": ["sb"],
}

_SPECIES_CODE_PATTERN = (
    r"(?<![A-Za-z0-9])(?:"
    + "|".join(
        sorted((re.escape(code) for code in SPECIES_KEYWORDS), key=len, reverse=True)
    )
    + r")(?![A-Za-z0-9])"
)
_ROLE_BOUND_SPECIES_CODE = r"[A-Z][A-Za-z0-9_-]{1,15}"

SPECIES_TO_LATIN = {
    "At": "Arabidopsis thaliana",
    "Os": "Oryza sativa",
    "Ls": "Lactuca sativa",
    "Zm": "Zea mays",
    "Sl": "Solanum lycopersicum",
    "Ft": "Fagopyrum tataricum",
    "Lc": "Litchi chinensis",
    "Dl": "Dimocarpus longan",
    "Ppy": "Pyrus pyrifolia",
    "Py": "",
    "Cp": "Carica papaya",
    "Vv": "Vitis vinifera",
}

SPECIES_TO_TAXID = {
    "At": "3702",
    "Os": "4530",
    "Zm": "4577",
    "Sl": "4081",
    "Ft": "62330",
    "Vv": "29760",
}


def _unique_keep_order(items: List[str]) -> List[str]:
    seen = set()
    ordered = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _species_mentions_with_roles(query: str) -> Dict[str, List[str]]:
    text = str(query or "")
    lower = text.lower()

    def collect_species(segment: str) -> List[str]:
        segment_lower = segment.lower()
        found: List[str] = []
        for match in re.finditer(
            _SPECIES_CODE_PATTERN,
            segment,
            flags=re.IGNORECASE,
        ):
            code = canonical.get(match.group(0).lower())
            if code:
                found.append(code)
        for code, keywords in SPECIES_KEYWORDS.items():
            for keyword in keywords:
                if len(keyword) <= 2 and keyword.lower() == code.lower():
                    continue
                if re.search(re.escape(keyword.lower()), segment_lower):
                    found.append(code)
        return _unique_keep_order(found)

    canonical = {code.lower(): code for code in SPECIES_KEYWORDS}
    role_markers: List[tuple[int, str]] = []
    marker_patterns = [
        ("targetSpecies", r"目标物种|目标|target|待鉴定|研究物种"),
        (
            "referenceSpecies",
            r"参考物种|参考|(?<![A-Za-z0-9_])reference(?![A-Za-z0-9_])|"
            r"(?<![A-Za-z0-9_])ref(?![A-Za-z0-9_])|seed_reference|已知家族",
        ),
        ("comparativeSpecies", r"比较物种|比较|对比|comparative|comparison|outgroup|外群"),
    ]
    role_bound_marker_patterns = {
        "targetSpecies": r"目标物种|目标基因组|target\s+(?:species|genome)",
        "referenceSpecies": r"参考物种|参考基因组|reference\s+(?:species|genome)",
        "comparativeSpecies": r"比较物种|比较基因组|comparative\s+(?:species|genome)|outgroup",
    }
    roles = {"targetSpecies": [], "referenceSpecies": [], "comparativeSpecies": [], "unassigned": []}
    # A role may follow the species, as in "Ft是目标物种". Resolve
    # comma-delimited clauses first so a path fragment such as ref_data cannot
    # assign a role or reverse the explicit Ft/At relationship.
    for clause in re.split(r"[,，;；。\n]+", text):
        clause_lower = clause.lower()
        for role, pattern in marker_patterns:
            if re.search(pattern, clause_lower):
                roles[role].extend(collect_species(clause))
                role_bound_marker = role_bound_marker_patterns[role]
                for role_bound_pattern in (
                    rf"(?i:(?:{role_bound_marker})\s*(?:为|是|作为|is|[:：=])?\s*)({_ROLE_BOUND_SPECIES_CODE})",
                    rf"({_ROLE_BOUND_SPECIES_CODE})(?i:\s*(?:是|为|作为|is)?\s*(?:{role_bound_marker}))",
                ):
                    roles[role].extend(
                        match.group(1)
                        for match in re.finditer(role_bound_pattern, clause)
                    )
    if any(roles[role] for role in ("targetSpecies", "referenceSpecies", "comparativeSpecies")):
        assigned = set(roles["targetSpecies"] + roles["referenceSpecies"] + roles["comparativeSpecies"])
        roles["unassigned"] = [sp for sp in collect_species(text) if sp not in assigned]
        return {key: _unique_keep_order(value) for key, value in roles.items()}

    for role, pattern in marker_patterns:
        for match in re.finditer(pattern, lower):
            role_markers.append((match.start(), role))
    role_markers.sort(key=lambda x: x[0])
    if role_markers:
        for idx, (start, role) in enumerate(role_markers):
            end = role_markers[idx + 1][0] if idx + 1 < len(role_markers) else len(text)
            roles[role].extend(collect_species(text[start:end]))
        assigned = set(roles["targetSpecies"] + roles["referenceSpecies"] + roles["comparativeSpecies"])
        all_species = collect_species(text)
        roles["unassigned"] = [sp for sp in all_species if sp not in assigned]
        return {key: _unique_keep_order(value) for key, value in roles.items()}

    mentions: List[tuple[int, str]] = []
    for match in re.finditer(_SPECIES_CODE_PATTERN, text, flags=re.IGNORECASE):
        code = canonical.get(match.group(0).lower())
        if code:
            mentions.append((match.start(), code))
    for code, keywords in SPECIES_KEYWORDS.items():
        for keyword in keywords:
            if len(keyword) <= 2 and keyword.lower() == code.lower():
                continue
            for match in re.finditer(re.escape(keyword.lower()), lower):
                mentions.append((match.start(), code))

    for pos, code in sorted(mentions, key=lambda x: x[0]):
        window = lower[max(0, pos - 18): pos]
        if any(token in window for token in ["参考", "reference", "ref", "seed_reference", "已知家族"]):
            roles["referenceSpecies"].append(code)
        elif any(token in window for token in ["比较", "对比", "comparative", "comparison", "outgroup", "外群"]):
            roles["comparativeSpecies"].append(code)
        elif any(token in window for token in ["目标", "target", "待鉴定", "研究物种"]):
            roles["targetSpecies"].append(code)
        else:
            roles["unassigned"].append(code)
    return {key: _unique_keep_order(value) for key, value in roles.items()}


def parse_gene_family_scope_selection(query: str) -> Dict[str, object]:
    """Parse target/reference/comparative species selection from free text."""
    roles = _species_mentions_with_roles(query)
    target = roles["targetSpecies"] or roles["unassigned"]
    reference = [sp for sp in roles["referenceSpecies"] if sp not in target]
    comparative = [sp for sp in roles["comparativeSpecies"] if sp not in target and sp not in reference]
    if reference and comparative:
        scope_mode = "target_plus_reference_plus_comparative"
    elif reference:
        scope_mode = "target_plus_reference"
    elif comparative:
        scope_mode = "target_plus_comparative"
    else:
        scope_mode = "target_only"
    return {
        "targetSpecies": target,
        "referenceSpecies": reference,
        "comparativeSpecies": comparative,
        "scopeMode": scope_mode,
    }


def parse_explicit_species_role_mentions(query: str) -> Dict[str, List[str]]:
    """Return explicit roles without promoting unassigned species to targets."""
    text = str(query or "")

    def mentioned_species(value: str) -> List[str]:
        parsed = _species_mentions_with_roles(value)
        return _unique_keep_order([
            species
            for key in ("targetSpecies", "referenceSpecies", "comparativeSpecies", "unassigned")
            for species in parsed.get(key) or []
        ])

    explicit_patterns = {
        "targetSpecies": r"目标物种|目标基因组|作为目标|是目标|\btarget(?:\s+species|\s+genome)?\b",
        "referenceSpecies": r"参考物种|参考基因组|作为参考|是参考|\breference(?:\s+species|\s+genome)?\b|\bseed_reference\b|\bknown_family_reference\b",
        "comparativeSpecies": r"比较物种|比较基因组|作为比较|是比较|作为外群|是外群|\bcomparative(?:\s+species|\s+genome)?\b|\boutgroup\b",
    }
    roles: Dict[str, List[str]] = {
        "targetSpecies": [],
        "referenceSpecies": [],
        "comparativeSpecies": [],
        "unassigned": [],
    }
    for clause in re.split(r"[,，;；。\n]+", text):
        clause_species = mentioned_species(clause)
        matched_roles = [
            key
            for key, pattern in explicit_patterns.items()
            if re.search(pattern, clause, flags=re.IGNORECASE)
        ]
        if len(matched_roles) == 1:
            roles[matched_roles[0]].extend(clause_species)

    assigned = set(
        roles["targetSpecies"]
        + roles["referenceSpecies"]
        + roles["comparativeSpecies"]
    )
    roles["unassigned"] = [
        species for species in mentioned_species(text) if species not in assigned
    ]
    return {key: _unique_keep_order(value) for key, value in roles.items()}


def parse_promoter_plot_revise_request(query: str) -> Dict:
    """Extract parameters for custom promoter cis-element plotting."""
    text = str(query or "").strip()
    lower = text.lower()
    if not text:
        return {}

    has_promoter = any(kw in lower for kw in ["promoter", "plantcare", "启动子", "顺式"])
    has_custom_plot = any(kw in lower for kw in [
        "promoter_plot_revise", "diy", "自定义", "重绘", "二次绘图", "只看", "只绘制", "只画",
        "不重新跑", "不重新提交", "已有启动子", "已有 plantcare", "已有plantcare", "custom", "replot",
    ])
    has_known_param = any(kw in text for kw in ["promoterElements", "promoterCategories", "promoterMatchMode", "promoterPlotModes", "promoterColorBy"])
    if not ((has_promoter and has_custom_plot) or has_known_param):
        return {}

    params: Dict[str, object] = {}
    prefix = _extract_prefix(text)
    if prefix:
        params["prefix"] = prefix

    def _extract_named(name: str) -> str:
        match = re.search(
            rf"{re.escape(name)}\s*[=:：]\s*(.*?)(?=\s*[,，；;。]?\s*promoter(?:Elements|Categories|MatchMode|PlotModes|TopN|OutputLabel)\s*[=:：]|[。；;\n]|$)",
            text,
            flags=re.IGNORECASE,
        )
        return match.group(1).strip().strip(",，。；; ") if match else ""

    named_elements = _extract_named("promoterElements")
    named_categories = _extract_named("promoterCategories")
    named_match = _extract_named("promoterMatchMode")
    named_modes = _extract_named("promoterPlotModes")
    named_color = _extract_named("promoterColorBy")
    named_label = _extract_named("promoterOutputLabel")

    known_categories = [
        "Core promoter", "Light response", "Hormone signaling", "Stress response", "TF binding", "Development", "Other",
    ]
    category_aliases = {
        "core promoter": "Core promoter",
        "light response": "Light response",
        "hormone signaling": "Hormone signaling",
        "stress response": "Stress response",
        "tf binding": "TF binding",
        "development": "Development",
        "other": "Other",
        "核心启动子": "Core promoter",
        "光响应": "Light response",
        "激素": "Hormone signaling",
        "激素响应": "Hormone signaling",
        "胁迫": "Stress response",
        "胁迫响应": "Stress response",
        "转录因子": "TF binding",
        "发育": "Development",
        "其他": "Other",
    }
    categories: List[str] = []
    if named_categories:
        raw_categories = re.split(r"[,，、/]", named_categories)
        for item in raw_categories:
            key = item.strip().lower()
            if key in category_aliases:
                categories.append(category_aliases[key])
            elif item.strip() in known_categories:
                categories.append(item.strip())
    for alias, category in category_aliases.items():
        if alias in lower and category not in categories:
            categories.append(category)

    elements: List[str] = []
    if named_elements:
        elements.extend([item.strip() for item in re.split(r"[,，、/]", named_elements) if item.strip()])
    else:
        # Prefer the phrase after "只看/只绘制/只画" for common PlantCARE element symbols.
        element_region = text
        match = re.search(r"(?:只看|只绘制|只画|元件|elements?)\s*([^。；;\n]+)", text, flags=re.IGNORECASE)
        if match:
            element_region = match.group(1)
        candidates = re.findall(r"\b[A-Za-z][A-Za-z0-9~-]{1,30}\b", element_region)
        excluded = {
            "Ft", "At", "Os", "Ls", "Zm", "Sl", "Lc", "Dl", "Ppy", "Py", "Cp", "Vv", "Lg",
            "FPKM", "TPM", "DIY", "PlantCARE", "promoter", "plot", "heatmap", "barplot", "exact", "contains", "regex",
            "position", "distribution", "Stress", "response", "Hormone", "signaling", "Light", "Core", "TF", "binding", "Development", "Other",
        }
        for candidate in candidates:
            if candidate not in excluded and candidate.lower() not in {x.lower() for x in excluded}:
                elements.append(candidate)

    if elements:
        params["promoterElements"] = ",".join(_unique_keep_order(elements))
    if categories:
        params["promoterCategories"] = ",".join(_unique_keep_order(categories))

    match_value = (named_match or "").lower()
    if "contains" in match_value or "包含" in lower or "关键词" in lower:
        params["promoterMatchMode"] = "contains"
    elif "regex" in match_value or "正则" in lower:
        params["promoterMatchMode"] = "regex"
    else:
        params["promoterMatchMode"] = "exact"

    modes: List[str] = []
    mode_text = f"{named_modes} {lower}"
    if "position_distribution" in mode_text or "位置分布" in lower or "位置" in lower:
        modes.append("position_distribution")
    if "heatmap" in mode_text or "热图" in lower:
        modes.append("heatmap")
    if "barplot" in mode_text or "柱状" in lower or "条形" in lower:
        modes.append("barplot")
    if not modes:
        modes = ["position_distribution", "heatmap"]
    params["promoterPlotModes"] = ",".join(_unique_keep_order(modes))

    named_color_lower = named_color.lower().strip()
    if named_color_lower:
        if named_color_lower in {"element", "elements", "元件"}:
            params["promoterColorBy"] = "element"
        elif named_color_lower in {"category", "categories", "类别", "大类"}:
            params["promoterColorBy"] = "category"
        else:
            params["promoterColorBy"] = "auto"
    elif "按元件" in lower or "元件着色" in lower or "元件颜色" in lower or "color by element" in lower:
        params["promoterColorBy"] = "element"
    elif "按类别" in lower or "类别着色" in lower or "大类着色" in lower or "color by category" in lower:
        params["promoterColorBy"] = "category"

    top_match = re.search(r"(?:top\s*n|top|前)\s*[:=：]?\s*(\d{1,3})", lower, flags=re.IGNORECASE)
    if top_match:
        params["promoterTopN"] = int(top_match.group(1))
    if named_label:
        params["promoterOutputLabel"] = re.sub(r"[^A-Za-z0-9_.-]+", "_", named_label).strip("._-")

    return params


WGS_STAGE_PHRASES = (
    ("wgs_maf_missing_filter", ("wgs maf缺失率过滤", "wgs maf missingness filter")),
    ("wgs_impute", ("wgs基因型填充", "wgs genotype imputation")),
    ("wgs_variant_filter", ("wgs变异过滤", "wgs variant filtering")),
    ("wgs_variant_select", ("wgs变异选择", "wgs variant selection")),
    ("wgs_joint_genotyping", ("wgs联合分型", "wgs joint genotyping")),
    ("wgs_gvcf_combine", ("wgs gvcf合并", "wgs gvcf combine")),
    ("wgs_haplotype_calling", ("wgs单样本变异检测", "wgs haplotype calling")),
    ("wgs_bam_qc", ("wgs bam质控", "wgs bam qc")),
    ("wgs_markdup", ("wgs去重复", "wgs mark duplicates")),
)


def _wgs_intent(stage: str) -> Dict:
    return {
        "gene_family": "",
        "species": "",
        "stages": [stage],
        "primary_stage": stage,
        "task_type": "analysis",
        "task_kind": "pipeline",
        "confidence": 0.98,
        "need_clarify": False,
        "clarify_type": None,
        "message": None,
    }


def parse_intent_from_query(query: str) -> Dict:
    """
    从查询中解析意图 - 硬编码规则（方案B）
    
    Args:
        query: 用户自然语言查询
        
    Returns:
        {
            "gene_family": 基因家族名称,
            "species": 物种代码,
            "stages": [分析阶段列表],
            "primary_stage": 主要分析阶段,
            "task_type": "analysis" | "feasibility" | "report",
            "confidence": 置信度,
            "need_clarify": 是否需要澄清,
            "clarify_type": 澄清类型,
            "message": 提示消息
        }
    """
    query_lower = query.lower()
    query_without_space = re.sub(r"\s+", "", query_lower)

    report_intent = resolve_gene_family_report_intent(query)
    if report_intent:
        return report_intent

    if any(token.replace(" ", "") in query_without_space for token in (
        "wgs全流程", "vcf全流程", "wgs full pipeline", "vcf full pipeline"
    )):
        return _wgs_intent("wgs_maf_missing_filter")
    for stage, phrases in WGS_STAGE_PHRASES:
        if any(phrase.replace(" ", "") in query_without_space for phrase in phrases):
            return _wgs_intent(stage)
    if any(token in query_lower for token in ("wgs align", "wgs_align", "wgs alignment", "wgs序列比对", "wgs比对")):
        return {"gene_family": "", "species": "", "stages": ["wgs_align"], "primary_stage": "wgs_align", "task_type": "analysis", "task_kind": "pipeline", "confidence": 0.98, "need_clarify": False, "clarify_type": None, "message": None}
    if any(token in query_lower for token in ("wgs index", "wgs_index", "reference index", "参考索引", "参考基因组索引")):
        return {"gene_family": "", "species": "", "stages": ["wgs_index"], "primary_stage": "wgs_index", "task_type": "analysis", "task_kind": "pipeline", "confidence": 0.98, "need_clarify": False, "clarify_type": None, "message": None}
    if any(token in query_lower for token in ("wgs", "fastp", "全基因组测序", "wgs质控", "测序质控")):
        return {"gene_family": "", "species": "", "stages": ["wgs_qc"], "primary_stage": "wgs_qc", "task_type": "analysis", "task_kind": "pipeline", "confidence": 0.98, "need_clarify": False, "clarify_type": None, "message": None}

    population = resolve_population_intent(query)
    if population:
        return {
            "gene_family": "",
            "species": "",
            "stages": population["stages"],
            "primary_stage": population["target"],
            "task_type": "analysis",
            "task_kind": "pipeline",
            "confidence": 0.98,
            "need_clarify": False,
            "clarify_type": None,
            "message": None,
            "prefix": population["prefix"],
            "params": population["params"],
        }

    gwas = resolve_gwas_intent(query)
    if gwas:
        return {
            "gene_family": "",
            "species": "",
            "stages": gwas["stages"],
            "primary_stage": gwas["target"],
            "task_type": "analysis",
            "task_kind": "pipeline",
            "confidence": 0.98,
            "need_clarify": False,
            "clarify_type": None,
            "message": None,
        }
    
    # 提取基因家族
    gene_family = _extract_gene_family(query, query_lower)
    
    # 提取物种
    species = _extract_species(query, query_lower)
    
    # 提取分析阶段
    stages = _extract_stages(query, query_lower)

    ncbi_params = parse_ncbi_fetch_request(query)
    sequence_extract_params = {} if ncbi_params else parse_sequence_extract_request(query)
    msa_region_params = {} if ncbi_params or sequence_extract_params else parse_msa_region_plot_request(query)
    promoter_plot_params = {} if ncbi_params or sequence_extract_params or msa_region_params else parse_promoter_plot_revise_request(query)
    if sequence_extract_params:
        stages = ["sequence_extract_by_id"]
    if ncbi_params:
        stages = ["ncbi_fetch_sequences"]
    if msa_region_params:
        stages = ["msa_region_plot"]
    if promoter_plot_params:
        stages = ["promoter_plot_revise"]
    
    # 确定任务类型
    task_type = _determine_task_type(query, query_lower)
    
    # 方案B: 处理不明确分析意图
    result = _handle_stage_resolution(query, query_lower, stages)
    
    parsed = {
        "gene_family": gene_family or "",
        "species": ncbi_params.get("ncbiSpecies") if ncbi_params.get("ncbiSpecies") else (species or ""),
        "stages": result["stages"],
        "primary_stage": result["stages"][0] if result["stages"] else None,
        "task_type": task_type,
        "task_kind": "pipeline",
        "confidence": result.get("confidence", 0.95 if stages else 0.7),
        "need_clarify": result.get("need_clarify", False),
        "clarify_type": result.get("clarify_type"),
        "message": result.get("message")
    }
    if parsed["primary_stage"] == "rnaseq_report":
        parsed["params"] = {
            "rnaseqReportAction": "open" if "打开" in query_lower else "generate"
        }
    if ncbi_params:
        parsed["params"] = ncbi_params
        parsed["prefix"] = ncbi_params.get("prefix", "")
        parsed["gene_family"] = "NCBI.fetch"
        parsed["task_kind"] = "utility"
        parsed["utility_tool"] = "ncbi_fetch_sequences"
        parsed["message"] = _build_ncbi_message(ncbi_params)
    if sequence_extract_params:
        parsed["params"] = sequence_extract_params
        parsed["prefix"] = sequence_extract_params.get("prefix", "")
        parsed["gene_family"] = "Sequence.extract"
        parsed["task_kind"] = "utility"
        parsed["utility_tool"] = "fasta_header_clean" if sequence_extract_params.get("sequenceExtractCleanHeaders") else "sequence_extract_by_id"
        parsed["message"] = _build_sequence_extract_message(sequence_extract_params)
    if msa_region_params:
        parsed["params"] = msa_region_params
        parsed["prefix"] = msa_region_params.get("prefix", "")
        parsed["gene_family"] = "MSA.region"
        parsed["task_kind"] = "utility"
        parsed["utility_tool"] = "msa_region_plot"
        parsed["message"] = _build_msa_region_plot_message(msa_region_params)
    if promoter_plot_params:
        parsed["params"] = promoter_plot_params
        parsed["prefix"] = promoter_plot_params.get("prefix", "")
        parsed["gene_family"] = "Promoter.custom"
        parsed["task_kind"] = "utility"
        parsed["utility_tool"] = "promoter_plot_revise"
        parsed["message"] = "我理解你想基于已有 PlantCARE/promoter 结果进行启动子自定义筛选绘图，不重新跑启动子预测。"
    if parsed["primary_stage"] in UTILITY_KEYWORD_STAGES:
        parsed["task_kind"] = "utility"
        parsed["utility_tool"] = parsed.get("utility_tool") or parsed["primary_stage"]
    if parsed["primary_stage"] in REPAIR_KEYWORD_STAGES:
        parsed["task_kind"] = "repair"
    scope_selection = parse_gene_family_scope_selection(query)
    if any(scope_selection.get(key) for key in ("targetSpecies", "referenceSpecies", "comparativeSpecies")):
        parsed["target_species"] = scope_selection.get("targetSpecies", [])
        parsed["reference_species"] = scope_selection.get("referenceSpecies", [])
        parsed["comparative_species"] = scope_selection.get("comparativeSpecies", [])
        parsed["scopeMode"] = scope_selection.get("scopeMode", "target_only")
        targets = parsed["target_species"]
        if targets:
            parsed["species"] = targets[0] if len(targets) == 1 else "Multi"
        params = parsed.setdefault("params", {})
        params.update(scope_selection)
    if parsed.get("gene_family") and parsed.get("species") and parsed.get("task_kind") != "utility":
        parsed["prefix"] = f"{parsed['species']}.{parsed['gene_family']}"
    return parsed


def deterministic_agent_chat_intent(
    query: str,
    *,
    rnaseq_context: bool = False,
) -> Optional[Dict]:
    """Return explicit chat intents that must not depend on the Router model."""
    intent = parse_intent_from_query(query)
    if intent.get("stages") == ["rnaseq_report"]:
        return intent
    query_lower = str(query or "").lower()
    query_without_space = re.sub(r"\s+", "", query_lower)
    is_generic_report_action = (
        rnaseq_context
        and "结果报告" in query_without_space
        and any(action in query_without_space for action in ("生成", "打开"))
    )
    if is_generic_report_action:
        return {
            "gene_family": "",
            "species": "",
            "stages": ["rnaseq_report"],
            "primary_stage": "rnaseq_report",
            "task_type": "report",
            "task_kind": "pipeline",
            "confidence": 0.99,
            "need_clarify": False,
            "clarify_type": None,
            "message": None,
            "params": {
                "rnaseqReportAction": (
                    "open" if "打开" in query_without_space else "generate"
                )
            },
        }
    return None


def parse_msa_region_plot_request(query: str) -> Dict:
    """Extract parameters for drawing a selected MSA alignment region."""
    text = str(query or "").strip()
    lower = text.lower()
    if not text:
        return {}

    has_msa = any(kw in lower for kw in ["msa", "alignment", "aligned", "多序列比对", "比对"])
    has_region_plot = any(kw in lower for kw in [
        "区间", "区域", "位置", "位点", "列", "范围", "片段", "局部", "重画", "二次绘图",
        "绘图", "画图", "出图", "plot", "figure", "region", "window", "columns",
        "保守区间", "保守位置", "保守窗口",
    ])
    if not (has_msa and has_region_plot):
        return {}

    params: Dict[str, object] = {}
    prefix_match = re.search(r"\b[A-Za-z][A-Za-z0-9_.-]*\.[A-Za-z0-9_.-]+\b", text)
    if prefix_match:
        params["prefix"] = prefix_match.group(0).strip(".,;，；")

    range_match = re.search(
        r"(?:从|位置|位点|列|columns?|cols?|start)?\s*(\d{1,6})\s*(?:-|~|–|—|到|至|:|,|，)\s*(?:位置|位点|列|end)?\s*(\d{1,6})",
        text,
        flags=re.IGNORECASE,
    )
    if not range_match:
        start_match = re.search(r"(?:start|起始|开始)\s*[:=]?\s*(\d{1,6})", text, flags=re.IGNORECASE)
        end_match = re.search(r"(?:end|结束|终止)\s*[:=]?\s*(\d{1,6})", text, flags=re.IGNORECASE)
        if start_match and end_match:
            range_match = (start_match, end_match)

    if range_match:
        if isinstance(range_match, tuple):
            start_col = int(range_match[0].group(1))
            end_col = int(range_match[1].group(1))
        else:
            start_col = int(range_match.group(1))
            end_col = int(range_match.group(2))
        if start_col > end_col:
            start_col, end_col = end_col, start_col
        params["msaStartCol"] = start_col
        params["msaEndCol"] = end_col
        params["msaAutoConserved"] = False

    auto_requested = any(kw in lower for kw in ["自动", "最保守", "保守窗口", "auto", "conserved window", "best window"])
    if auto_requested and "msaStartCol" not in params:
        params["msaAutoConserved"] = True

    window_match = re.search(r"(?:窗口|window)\s*(?:长度|len|length)?\s*[:=]?\s*(\d{1,4})", text, flags=re.IGNORECASE)
    if window_match:
        params["msaWindowLen"] = max(1, int(window_match.group(1)))

    nongap_match = re.search(r"(?:nongap|min[-_ ]?nongap|非缺口)\s*[:=]?\s*(0(?:\.\d+)?|1(?:\.0+)?)", text, flags=re.IGNORECASE)
    if nongap_match:
        params["msaMinNongap"] = float(nongap_match.group(1))

    return params or {"msaAutoConserved": False}


def parse_sequence_extract_request(query: str) -> Dict:
    """Extract parameters for local FASTA sequence extraction by IDs."""
    text = _normalize_utility_query(query)
    lower = text.lower()
    if not text:
        return {}

    has_header_clean = any(kw in lower for kw in [
        "id处理", "处理id", "修订id", "简化id", "清理id", "id简化",
        "去掉注释", "去除注释", "删除注释", "注释信息", "长注释",
        "clean header", "simple header", "simplify header", "header clean",
        "fasta header", "序列名", "序列头",
    ])
    has_extract = any(kw in lower for kw in ["提取", "筛选", "extract", "subseq", "pull out", "取出"])
    has_sequence = any(kw in lower for kw in ["序列", "fasta", "fa", "pep", "protein", "蛋白", "cds", "基因id", "id list", "id列表"])
    if has_header_clean and any(kw in lower for kw in ["序列", "fasta", "fa", "pep", "protein", "蛋白", "cds", "家族"]):
        paths = _extract_file_paths(text)
        fasta_paths = [
            path for path in paths
            if re.search(r"\.(fa|fasta|faa|fna|ffn|pep|cds)(?:\.gz)?$", path.lower())
            or any(x in path.lower() for x in ["pep", "protein", "cds", "fasta"])
        ]
        seq_type = "auto"
        if any(kw in lower for kw in ["pep", "protein", "蛋白"]):
            seq_type = "pep"
        elif "cds" in lower:
            seq_type = "cds"
        prefix = _extract_prefix(text) or _extract_family_prefix(text, lower)
        return {
            "sequenceExtractFasta": ",".join(fasta_paths),
            "sequenceExtractIdsFile": "",
            "sequenceExtractIds": "",
            "sequenceExtractSeqType": seq_type,
            "sequenceExtractRenameMode": "simple",
            "sequenceExtractCleanHeaders": True,
            "prefix": prefix or "sequence_clean",
        }

    if not (has_extract and has_sequence):
        return {}
    if "ncbi" in lower and any(kw in lower for kw in ["下载", "download"]):
        return {}

    paths = _extract_file_paths(text)
    fasta_paths = []
    id_paths = []
    for path in paths:
        name = path.lower()
        if re.search(r"\.(fa|fasta|faa|fna|ffn|pep|cds)(?:\.gz)?$", name) or any(x in name for x in ["pep", "protein", "cds", "fasta"]):
            fasta_paths.append(path)
        elif re.search(r"\.(txt|tsv|csv|list|ids)$", name):
            id_paths.append(path)

    ids_ref = _extract_ids_list_reference(text)
    if ids_ref and ids_ref not in id_paths:
        id_paths.append(ids_ref)

    inline_ids = _extract_sequence_ids(text)
    if not inline_ids and not id_paths:
        # If the user provided one non-FASTA path in an ID-list context, treat it as ID file.
        for path in paths:
            if path not in fasta_paths:
                id_paths.append(path)
                break

    if not inline_ids and not id_paths:
        return {}

    seq_type = "auto"
    if any(kw in lower for kw in ["pep", "protein", "蛋白"]):
        seq_type = "pep"
    elif "cds" in lower:
        seq_type = "cds"

    rename_mode = "keep"
    if any(kw in lower for kw in ["重命名", "重新命名", "rename", "命名为查询", "按查询命名"]):
        rename_mode = "query"
    elif any(kw in lower for kw in ["简化命名", "simple header", "简化header"]):
        rename_mode = "simple"

    prefix = _extract_prefix(text)
    if not prefix:
        source = id_paths[0] if id_paths else (fasta_paths[0] if fasta_paths else "")
        prefix = _prefix_from_path(source) if source else "sequence_extract"

    params: Dict = {
        "sequenceExtractFasta": ",".join(fasta_paths),
        "sequenceExtractIdsFile": id_paths[0] if id_paths else "",
        "sequenceExtractIds": " ".join(inline_ids),
        "sequenceExtractSeqType": seq_type,
        "sequenceExtractRenameMode": rename_mode,
        "prefix": prefix,
    }
    return params


def _normalize_utility_query(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    return re.sub(
        r"([A-Za-z0-9][A-Za-z0-9_.-]*)\s+\.(ids?|list|txt|tsv|csv)\b",
        r"\1.\2",
        normalized,
        flags=re.IGNORECASE,
    )


def _extract_ids_list_reference(text: str) -> Optional[str]:
    normalized = _normalize_utility_query(text)
    path = _extract_file_path(normalized)
    if path:
        return path
    match = re.search(
        r"(?<![A-Za-z0-9_.-])([A-Za-z0-9][A-Za-z0-9_.-]*\.(?:ids?|list|txt|tsv|csv))\b",
        normalized,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    prefix_match = re.search(r"\b([A-Za-z]{1,6}\.[A-Za-z0-9_-]+)\b", normalized)
    if prefix_match and re.search(r"(?:\.\s*)?(?:ids?|list|txt|tsv|csv)\b", normalized, re.IGNORECASE):
        return f"{prefix_match.group(1)}.ids"
    return None


def _species_code_from_prefix_like(value: str) -> Optional[str]:
    token = str(value or "").strip().split("/")[-1]
    match = re.match(r"([A-Za-z]{1,6})(?:[._-]|$)", token)
    if not match:
        return None
    raw = match.group(1).lower()
    canonical = {code.lower(): code for code in SPECIES_KEYWORDS}
    return canonical.get(raw)


def parse_ncbi_fetch_request(query: str) -> Dict:
    """Extract conservative NCBI sequence-download parameters from natural language."""
    text = _normalize_utility_query(query)
    lower = text.lower()
    if not text:
        return {}
    result_view_context = (
        any(kw in lower or kw in text for kw in ["下载好的", "已下载", "已经下载", "下载结果", "下载产物", "已有结果"])
        and any(kw in lower or kw in text for kw in ["展示", "显示", "列出", "查看", "列表", "明细"])
        and any(kw in lower or kw in text for kw in ["基因id", "gene id", "gene ids", "id列表", "ids", "id"])
    )
    if result_view_context:
        return {}
    existing_result_file_context = (
        bool(re.search(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:fa|fasta|faa|fna|ffn|pep|cds|txt|tsv|csv)(?=\s|$|[，,。；;的])", text, flags=re.IGNORECASE))
        and any(kw in lower or kw in text for kw in [
            "过滤", "只保留", "提取", "抽取", "查看", "展示", "显示", "列出", "预览",
            "前几行", "前几列", "表头", "列名", "id列", "蛋白id", "蛋白 id",
            "filter", "extract", "preview", "head",
        ])
        and not any(kw in lower or kw in text for kw in ["下载", "获取", "download"])
        and not re.search(r"(?<!ncbi_)fetch\b", lower)
    )
    if existing_result_file_context:
        return {}

    has_ncbi = "ncbi" in lower
    has_sequence_download = any(kw in lower for kw in [
        "下载", "获取", "fetch", "download", "序列", "protein", "蛋白", "pep", "cds", "geneid", "gene id",
    ])
    has_gene_list = any(kw in lower for kw in [
        "基因list", "基因列表", "基因id", "基因ids", "gene list", "gene_list", "gene id", "gene ids", "id列表", "id list",
        "id文件", "ids文件",
    ]) or bool(re.search(r"\.(?:ids?|list|txt|tsv|csv)(?=\s|$|[的，,。；;]|下载|获取|fetch|download|提取|筛选)", lower))
    has_path = bool(_extract_file_path(text))
    has_local_extract = any(kw in lower for kw in ["提取", "筛选", "extract", "subseq", "pull out", "取出"])
    has_explicit_download = any(kw in lower for kw in ["下载", "download", "fetch", "ncbi"])
    if has_local_extract and not has_explicit_download and (has_gene_list or has_path):
        return {}
    if not (has_ncbi or (has_sequence_download and (has_gene_list or has_path))):
        return {}

    params: Dict = {
        "ncbiMode": "gene",
        "ncbiIdType": "auto",
        "ncbiSeqTypes": "both",
        "ncbiTranscriptPolicy": "refseq_preferred",
        "ncbiAllowFamilyDownload": False,
    }

    path = _extract_ids_list_reference(text)
    if path:
        params["ncbiGeneIds"] = path

    inferred_code = _species_code_from_prefix_like(path or params.get("prefix", ""))
    species = _extract_latin_species(text) or _extract_species_scientific_name(text, lower)
    if not species and inferred_code:
        species = SPECIES_TO_LATIN.get(inferred_code) or ""
    if species:
        params["ncbiSpecies"] = species

    taxid = _extract_taxid(text)
    if not taxid and inferred_code:
        taxid = SPECIES_TO_TAXID.get(inferred_code) or ""
    if taxid:
        params["ncbiTaxid"] = taxid

    prefix = _extract_prefix(text)
    if not prefix and path:
        prefix = _prefix_from_path(path)
    if prefix:
        params["prefix"] = prefix

    email = _extract_email(text)
    if email:
        params["ncbiEmail"] = email

    if re.search(r"\b(geneid|gene id|ncbi gene)\b", lower) or "geneid" in lower:
        params["ncbiIdType"] = "geneid"
    elif any(kw in lower for kw in ["locus", "位点", "locus tag"]):
        params["ncbiIdType"] = "locus_tag"
    elif any(kw in lower for kw in ["symbol", "基因名", "基因名称"]):
        params["ncbiIdType"] = "symbol"

    if any(kw in lower for kw in ["家族", "family"]) and not path:
        params["ncbiMode"] = "family"
        params["ncbiAllowFamilyDownload"] = any(kw in lower for kw in ["确认下载", "允许批量下载", "allow-family-download", "直接下载全部"])

    wants_protein = any(kw in lower for kw in ["protein", "蛋白", "pep"])
    wants_cds = "cds" in lower
    if wants_protein and not wants_cds:
        params["ncbiSeqTypes"] = "protein"
    elif wants_cds and not wants_protein:
        params["ncbiSeqTypes"] = "cds"
    else:
        params["ncbiSeqTypes"] = "both"

    query_value = _extract_gene_query(text)
    if query_value and not path:
        params["ncbiQuery"] = query_value

    if not (params.get("ncbiGeneIds") or params.get("ncbiQuery")):
        return {}
    return params


def _extract_file_path(text: str) -> Optional[str]:
    patterns = [
        r"(?:基因\s*list|基因列表|基因\s*id|基因\s*ids|gene\s*list|gene_list|gene\s*id|gene\s*ids|id列表|id\s*list|ids?|id文件|ids文件|文件|file)\s*(?:放在|在|位于|是|为|:|：)?\s*([/~][^\s,，;；。]+)",
        r"([/~][^\s,，;；。]+(?:\.txt|\.tsv|\.csv|\.list|\.ids?))",
        r"(?:文件名|文件|file|基因\s*list|基因列表|id列表|id\s*list|id文件|ids文件)\s*(?:叫|名为|是|为|:|：)?\s*([A-Za-z0-9][A-Za-z0-9_.-]*\.(?:txt|tsv|csv|list|ids?))",
        r"(?<![A-Za-z0-9_.-])([A-Za-z0-9][A-Za-z0-9_.-]*\.(?:txt|tsv|csv|list|ids?))(?=\s|$|[的，,。；;]|下载|获取|fetch|download|提取|筛选)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip().strip("'\"")
    return None


def _extract_file_paths(text: str) -> List[str]:
    paths = []
    for match in re.finditer(r"([/~][^\s,，;；。'\"<>]+)", text):
        path = match.group(1).strip().rstrip(")")
        if path not in paths:
            paths.append(path)
    for match in re.finditer(
        r"(?<![A-Za-z0-9_.-])([A-Za-z0-9][A-Za-z0-9_.-]*\.(?:fa|fasta|faa|fna|ffn|pep|cds|txt|tsv|csv|list|ids?)(?:\.gz)?)(?=\s|$|[的，,。；;])",
        text,
        re.IGNORECASE,
    ):
        path = match.group(1).strip().rstrip(")")
        if path not in paths:
            paths.append(path)
    return paths


def _extract_sequence_ids(text: str) -> List[str]:
    cleaned = re.sub(r"[/~][^\s,，;；。'\"<>]+", " ", text)
    ids = []
    patterns = [
        r"\b[A-Za-z]{1,4}\d+[gGtT]\d+(?:\.\d+)?\b",
        r"\b[A-Za-z]{1,6}_\d+(?:\.\d+)?\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, cleaned):
            value = match.group(0)
            if value not in ids:
                ids.append(value)
    explicit = re.search(
        r"(?:基因\s*id|gene\s*id|(?<![A-Za-z0-9_.-])id(?!s\b))\s*(?:是|为|:|：)?\s*([A-Za-z0-9_.;,\s-]+)",
        cleaned,
        re.I,
    )
    if explicit:
        for token in re.split(r"[\s,;，；]+", explicit.group(1)):
            token = token.strip()
            if token and token.lower() not in {"list", "file", "pep", "protein", "cds"} and token not in ids:
                ids.append(token)
    return ids


def _prefix_from_path(path: str) -> str:
    name = str(path).rstrip("/").split("/")[-1]
    name = re.sub(r"\.(txt|tsv|csv|list|ids?)$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")
    return name or "NCBI.fetch"


def _extract_latin_species(text: str) -> Optional[str]:
    patterns = [
        r"(?:物种|species)\s*(?:是|为|:|：)?\s*([A-Z][a-z]+(?:\s+[a-z][a-z0-9.-]+){1,3})",
        r"\b([A-Z][a-z]+(?:\s+[a-z][a-z0-9.-]+){1,3})\b",
    ]
    stop_words = {"GeneID", "NCBI"}
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidate = match.group(1).strip()
            if candidate.split()[0] not in stop_words:
                return candidate
    return None


def _extract_species_scientific_name(text: str, lower: Optional[str] = None) -> Optional[str]:
    lower = lower if lower is not None else text.lower()
    for code, keywords in SPECIES_KEYWORDS.items():
        if any(kw.lower() in lower for kw in keywords):
            return SPECIES_TO_LATIN.get(code)
    return None


def _extract_taxid(text: str) -> Optional[str]:
    match = re.search(r"(?:taxid|taxon|taxonomy)\s*(?:是|为|:|：)?\s*(\d+)", text, re.IGNORECASE)
    return match.group(1) if match else None


def _extract_prefix(text: str) -> Optional[str]:
    match = re.search(r"(?:输出前缀|前缀|prefix)\s*(?:用|是|为|:|：)?\s*([A-Za-z0-9_.-]+)", text, re.IGNORECASE)
    return match.group(1) if match else None


def _extract_family_prefix(text: str, lower: str) -> Optional[str]:
    species = None
    for code, keywords in SPECIES_KEYWORDS.items():
        if any(kw.lower() in lower for kw in keywords):
            species = code
            break
    family = None
    for item in COMMON_FAMILIES:
        if item.lower() in lower or re.search(rf"(?<![A-Za-z0-9]){re.escape(item)}(?![A-Za-z0-9])", text, re.IGNORECASE):
            family = item.upper() if item.isupper() else item
            break
    if species and family:
        return f"{species}.{family}"
    if family:
        return family
    return None


def _extract_email(text: str) -> Optional[str]:
    match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    return match.group(0) if match else None


def _extract_gene_query(text: str) -> Optional[str]:
    patterns = [
        r"(?:基因|gene)\s*(?:是|为|:|：)?\s*([A-Za-z0-9_.-]+)",
        r"(?:下载|获取|fetch|download)\s*([A-Za-z0-9_.-]+)\s*(?:的)?(?:蛋白|protein|cds|序列)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if value.lower() not in {"list", "protein", "cds", "ncbi"}:
                return value
    return None


def _build_ncbi_message(params: Dict) -> str:
    source = params.get("ncbiGeneIds") or params.get("ncbiQuery") or "未指定"
    species = params.get("ncbiTaxid") or params.get("ncbiSpecies") or "未指定"
    return f"我理解你想从 NCBI 下载序列；输入为 {source}，物种限制为 {species}。"


def _build_sequence_extract_message(params: Dict) -> str:
    source = params.get("sequenceExtractFasta") or "自动从当前job查找FASTA"
    if params.get("sequenceExtractCleanHeaders"):
        return f"我理解你想清理FASTA序列ID/header；FASTA来源为 {source}，输出将保留每条序列header的第一个ID。"
    ids = params.get("sequenceExtractIdsFile") or params.get("sequenceExtractIds") or "未指定"
    return f"我理解你想按ID从本地FASTA提取序列；FASTA来源为 {source}，ID来源为 {ids}。"


def _build_msa_region_plot_message(params: Dict) -> str:
    if params.get("msaAutoConserved"):
        return f"我理解你想自动寻找 MSA 保守窗口并绘图；窗口长度为 {params.get('msaWindowLen', 60)}。"
    start_col = params.get("msaStartCol")
    end_col = params.get("msaEndCol")
    if start_col and end_col:
        return f"我理解你想对 MSA alignment 第 {start_col}-{end_col} 列进行局部绘图。"
    return "我理解你想对 MSA 指定区间进行局部绘图；请在确认弹窗中填写 alignment 起止列。"


def _stage_keyword_pattern(keyword: str) -> str:
    """Build a boundary-safe regex for one existing stage keyword."""
    escaped = re.escape(keyword.lower())
    if keyword and keyword[0].isascii() and keyword[0].isalnum():
        escaped = rf"(?<![a-z0-9_]){escaped}"
    if keyword and keyword[-1].isascii() and keyword[-1].isalnum():
        escaped = rf"{escaped}(?![a-z0-9_])"
    return escaped


def extract_negated_target_stages(query: str) -> List[str]:
    """Return explicitly excluded ordinary gene-family/RNA-seq target stages."""
    query_lower = str(query or "").lower()
    if any(marker in query_lower for marker in (
        "亚家族", "subfamily", "reference_classification", "subfamily_confirm",
        "build_subfamily_evidence", "subfamily_evidence", "subfamily_prediction",
    )):
        return []
    if re.search(r"(?:不是|并非)\s*(?:不执行|不单独执行|不要|不用|无需|不做|跳过|排除|去掉)", query_lower):
        return []

    eligible = set(CORE_STAGES) | set(RNASEQ_STAGES) | {"expression", "annotation", "msa"}
    chinese_prefix = r"(?:不单独执行|不执行|不要(?:执行|做|进行)?|不用(?:执行|做|进行)?|无需(?:执行|做|进行)?|不做|跳过|排除|去掉)\s*"
    english_prefix = r"(?:without(?:\s+(?:running|executing|performing))?|skip|exclude|do\s+not\s+(?:run|execute|perform)?)\s+"
    excluded = []
    for stage, keywords in STAGE_KEYWORDS.items():
        if stage not in eligible:
            continue
        for keyword in sorted(keywords, key=len, reverse=True):
            keyword_pattern = _stage_keyword_pattern(keyword)
            if (
                re.search(chinese_prefix + keyword_pattern, query_lower)
                or re.search(english_prefix + keyword_pattern, query_lower)
                or re.search(r"(?:^|[\s,，;；。:：])-\s*" + keyword_pattern, query_lower)
            ):
                excluded.append(stage)
                break
    return list(dict.fromkeys(excluded))


def _handle_stage_resolution(query: str, query_lower: str, stages: List[str]) -> Dict:
    """
    方案B: 处理阶段解析逻辑
    - 明确指定阶段 → 直接执行
    - "基因家族分析" → 基础基因家族链路
    - 有"分析"但不明确 → 引导澄清
    - 完全无法识别 → 引导澄清
    """
    excluded_stages = set(extract_negated_target_stages(query))
    stages = [stage for stage in stages if stage not in excluded_stages]

    # 情况1: 明确指定了具体阶段 → 直接执行。
    # 如果同时出现“完整/全流程”等词，则以基础基因家族链路为主体，
    # 再追加用户明确点名的 Expression/KaKs/PPI/种间共线性等可选阶段。
    if stages:
        resolved_stages = list(dict.fromkeys([*CORE_STAGES, *stages])) if _is_complete_workflow_query(query_lower) else stages
        resolved_stages = [stage for stage in resolved_stages if stage not in excluded_stages]
        return {
            "stages": resolved_stages,
            "need_clarify": False,
            "confidence": 0.95
        }

    # 情况1.5: 泛化 RNA-seq 请求 → 默认走候选基因发现流程
    # 例如“帮我执行rnaseq分析”“开始转录组分析”
    if any(marker in query_lower for marker in RNASEQ_MARKERS):
        if any(kw in query_lower for kw in ["分析", "执行", "运行", "开始", "流程", "pipeline"]):
            return {
                "stages": [
                    stage for stage in (
                        "rnaseq_de", "rnaseq_go_enrich", "rnaseq_kegg_enrich",
                        "rnaseq_fgsea", "rnaseq_wgcna", "rnaseq_candidate_family",
                    )
                    if stage not in excluded_stages
                ],
                "need_clarify": False,
                "confidence": 0.9,
                "note": "generic_rnaseq_workflow"
            }
    
    # 情况2: "基因家族分析" = 基础基因家族链路
    if _is_complete_workflow_query(query_lower):
        return {
            "stages": [stage for stage in CORE_STAGES if stage not in excluded_stages],
            "need_clarify": False,
            "confidence": 0.9,
            "note": "完整流程"
        }
    
    # 情况3: 有"分析"但不明确阶段 → 引导澄清
    if "分析" in query or "analyze" in query_lower:
        return {
            "stages": [],
            "need_clarify": True,
            "clarify_type": "choose_stages",
            "confidence": 0.5,
            "message": "请指定具体阶段，例如：鉴定、进化树、motif分析、domain分析等"
        }
    
    # 情况4: 完全无法识别
    return {
        "stages": [],
        "need_clarify": True,
        "clarify_type": "unknown",
        "confidence": 0.3,
        "message": "无法理解意图，请使用标准格式：'分析 [阶段] [基因家族] [物种]'"
    }


def _is_complete_workflow_query(query_lower: str) -> bool:
    """检查是否是完整流程查询"""
    for kw in COMPLETE_WORKFLOW_KEYWORDS:
        if kw in query_lower:
            return True
    return False


def _extract_gene_family(query: str, query_lower: str) -> Optional[str]:
    """提取基因家族"""
    # 优先匹配纯英文基因家族名（如 SPL, NAC, WRKY）
    # 第一种模式：英文名 + 基因家族
    pure_english_patterns = [
        r'([A-Za-z][A-Za-z0-9]*)\s*基因家族',  # SPL基因家族
        r'基因家族\s*([A-Za-z][A-Za-z0-9]*)',  # 基因家族SPL
    ]
    species_codes = {code.lower() for code in SPECIES_KEYWORDS}
    for pattern in pure_english_patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            raw_value = match.group(1)
            value = raw_value.upper()
            if raw_value.lower() in species_codes:
                continue
            if value not in EXCLUDE_WORDS and value.lower() not in {w.lower() for w in EXCLUDE_WORDS}:
                return value
    
    # 第二种模式：中英文混合（需要过滤）
    mixed_patterns = [
        r'(\w+)\s*基因家族',
        r'基因家族\s*(\w+)',
    ]
    for pattern in mixed_patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            extracted = match.group(1).upper()
            # 排除中文请求词和包含中文的结果
            if extracted not in EXCLUDE_WORDS and extracted.lower() not in {w.lower() for w in EXCLUDE_WORDS}:
                # 如果提取结果包含中文，尝试提取其中的纯英文部分
                english_only = re.sub(r'[^A-Za-z0-9]', '', extracted)
                if english_only and english_only.lower() in species_codes:
                    continue
                if english_only and english_only not in EXCLUDE_WORDS:
                    return english_only
                # 否则跳过，继续匹配下一个模式
    
    # 检查常见基因家族
    for fam in COMMON_FAMILIES:
        if fam.lower() in query_lower:
            return fam
    
    return None


def _extract_species(query: str, query_lower: str) -> Optional[str]:
    """提取物种"""
    for species, keywords in SPECIES_KEYWORDS.items():
        for kw in keywords:
            if kw in query_lower:
                return species
    return None


def _extract_stages(query: str, query_lower: str) -> List[str]:
    """提取分析阶段"""
    stages = []
    if is_gwas_fmt_intent(query):
        stages.append("gwas_fmt")
    matched_keywords = []  # 记录匹配到的关键词长度，用于优先级判断
    has_rnaseq_marker = any(marker in query_lower for marker in RNASEQ_MARKERS)
    requests_result_report = any(
        keyword in query_lower
        for keyword in ["生成结果报告", "打开结果报告", "结果报告", "生成报告", "打开报告"]
    )
    if has_rnaseq_marker and requests_result_report:
        return ["rnaseq_report"]
    extraction_context = any(kw in query_lower for kw in ["提取", "extract", "gffread", "筛选", "subseq"])
    explicit_gene_family_context = any(kw in query_lower for kw in ["基因家族", "家族表达", "表达量", "热图", "heatmap"])
    curated_member_context = (
        any(kw in query_lower for kw in ["修订后的", "修订后", "人工修订", "curated", "edited"])
        and any(kw in query_lower for kw in ["家族id", "成员id", "基因家族id", "member id", "members"])
    )
    if curated_member_context and any(kw in query_lower for kw in ["重新提取", "重建", "提取蛋白", "蛋白序列", "final_members"]):
        stages.append("identify_refine")
    if any(kw in query_lower for kw in ["确认亚家族", "亚家族确认", "确认亚家族标签", "人工确认亚家族", "subfamily confirm", "confirmed subfamily"]):
        stages.append("subfamily_confirm")
    rna_stage_hint = None
    if any(kw in query_lower for kw in ["候选家族", "候选基因家族", "家族排名", "tf注释", "tf family", "tffamily", "pfam注释", "deg注释", "差异基因注释", "差异基因家族", "candidate family"]):
        rna_stage_hint = "rnaseq_candidate_family"
    elif any(kw in query_lower for kw in ["fgsea", "gsea", "排序富集", "基因集富集"]):
        rna_stage_hint = "rnaseq_fgsea"
    elif any(kw in query_lower for kw in ["wgcna", "共表达", "加权共表达", "模块性状"]):
        rna_stage_hint = "rnaseq_wgcna"
    elif any(kw in query_lower for kw in ["样本质量", "样本质量评估", "样本检测", "sample qc", "sample quality", "pca", "样本相关性", "样本聚类"]):
        rna_stage_hint = "rnaseq_sample_qc"
    elif any(kw in query_lower for kw in ["差异表达", "差异分析", "差异基因", "deg", "deseq2", "edger"]) and not explicit_gene_family_context:
        rna_stage_hint = "rnaseq_de"
    elif any(kw in query_lower for kw in ["质控", "qc", "fastqc", "测序质量"]):
        rna_stage_hint = "rnaseq_qc"
    elif any(kw in query_lower for kw in ["定量", "quant", "featurecounts", "count矩阵", "计数矩阵"]):
        rna_stage_hint = "rnaseq_quant"
    elif any(kw in query_lower for kw in ["比对", "align", "mapping", "hisat2", "star"]) and not any(kw in query_lower for kw in ["多序列", "msa", "multiple alignment"]):
        rna_stage_hint = "rnaseq_align"

    if rna_stage_hint:
        stages.append(rna_stage_hint)
    
    for stage, keywords in STAGE_KEYWORDS.items():
        if stage in UTILITY_KEYWORD_STAGES or stage in REPAIR_KEYWORD_STAGES:
            continue
        if stage in RNASEQ_STAGES and stage != "rnaseq_candidate_family" and not has_rnaseq_marker:
            continue
        if stage == "expression" and has_rnaseq_marker:
            continue
        for kw in keywords:
            if kw in query_lower:
                stages.append(stage)
                matched_keywords.append((stage, kw, len(kw)))
                break
    
    # 优先级处理：如果同时匹配了 interspecies_synteny 和 synteny，只保留 interspecies_synteny
    # 因为 "种间共线性" 比 "共线性" 更具体
    if "interspecies_synteny" in stages and "synteny" in stages:
        stages.remove("synteny")

    # "启动子结合位点/TF结合位点/FIMO" 属于 TFBS 扫描，不是 PlantCARE 类启动子顺式元件分析。
    if "tfbs" in stages and "promoter" in stages:
        stages = [stage for stage in stages if stage != "promoter"]

    if "rnaseq_go_enrich" in stages or "rnaseq_kegg_enrich" in stages:
        stages = [stage for stage in stages if stage != "rnaseq_enrich"]

    if any(stage in stages for stage in RNASEQ_STAGES):
        stages = [stage for stage in stages if stage not in {"expression", "msa"}]

    if "rnaseq_candidate_family" in stages:
        stages = [stage for stage in stages if stage not in {"domain", "annotation", "identify"}]

    # Explicit utility calls. These must be more constrained than ordinary
    # pipeline routing so "根据修订后的家族ID重新提取蛋白" does not become
    # genome+GFF extraction.
    explicit_gff_extract_context = extraction_context and any(
        kw in query_lower
        for kw in ["gff", "gff3", "gtf", "gffread", "genome", "基因组"]
    )
    if explicit_gff_extract_context and not curated_member_context:
        if "pep" in query_lower or "蛋白" in query_lower:
            stages.append("prepare_extract_pep")
        if "cds" in query_lower:
            stages.append("prepare_extract_cds")
        stages = [stage for stage in stages if stage != "pep"]

    if any(kw in query_lower for kw in ["数据准备", "基因组准备", "genome_prepare", "prepare genome"]):
        stages.append("genome_prepare")
    if any(kw in query_lower for kw in ["整理文件", "组织文件", "整理数据", "标准化命名", "重命名文件", "config整理"]):
        stages.append("prepare_organize")
    if any(kw in query_lower for kw in ["修订gff", "规范化gff", "gff id", "gff整理"]):
        stages.append("prepare_normalize_gff")
    if any(kw in query_lower for kw in ["最长转录本", "最长cds", "primary transcript", "longest transcript"]):
        stages.append("prepare_longest_transcript")
    if any(kw in query_lower for kw in ["ncbi id", "ncbi修订", "ncbi简化", "ncbi header", "简化ncbi"]):
        stages.append("prepare_simplify_ncbi")

    if "identify_refine" in stages:
        stages = [stage for stage in stages if stage not in {"identify", "prepare_extract_pep", "prepare_extract_cds"}]
    if "subfamily_confirm" in stages:
        stages = [stage for stage in stages if stage != "reference_classification"]

    # 当前标准执行器没有泛 enrichment 阶段。无 RNA-seq 前缀的 GO/KEGG/富集请求
    # 也应路由到已接入的 RNA-seq 富集子阶段，避免回落到 identify/HMM 依赖。
    if not stages and any(kw in query_lower for kw in ["富集", "enrichment", "enrich", "go分析", "go富集", "kegg", "通路分析", "pathway"]):
        if "kegg" in query_lower or "通路" in query_lower or "pathway" in query_lower:
            stages = ["rnaseq_kegg_enrich"]
        elif re.search(r"(^|[^a-z])go([^a-z]|$)", query_lower) or "go富集" in query_lower or "go分析" in query_lower:
            stages = ["rnaseq_go_enrich"]
        else:
            stages = ["rnaseq_go_enrich", "rnaseq_kegg_enrich"]

    # 对“rnaseq分析/转录组分析”这类泛化请求给出默认 RNA-seq 主流程，
    # 避免空 stages 后在上层错误回落到 identify。
    if not stages and has_rnaseq_marker:
        if any(kw in query_lower for kw in ["分析", "执行", "运行", "开始", "流程", "pipeline"]):
            stages = ["rnaseq_de", "rnaseq_go_enrich", "rnaseq_kegg_enrich", "rnaseq_fgsea", "rnaseq_wgcna", "rnaseq_candidate_family"]
    
    # 只返回标准 catalog 中存在的阶段，避免 summary/旧别名进入执行链。
    known_stages = set(STAGE_ORDER)
    deduped = list(dict.fromkeys(stages))
    return [stage for stage in deduped if stage in known_stages]


def _determine_task_type(query: str, query_lower: str) -> str:
    """确定任务类型"""
    if any(kw in query_lower for kw in ["可行性", "是否可以", "能不能"]):
        return "feasibility"
    elif any(kw in query_lower for kw in ["报告", "总结", "生成报告"]):
        return "report"
    else:
        return "analysis"


# 便捷函数
def parse_gene_family(query: str) -> Optional[str]:
    """仅提取基因家族"""
    return _extract_gene_family(query, query.lower())


def parse_species(query: str) -> Optional[str]:
    """仅提取物种"""
    return _extract_species(query, query.lower())


def parse_stages(query: str) -> List[str]:
    """仅提取分析阶段"""
    return _extract_stages(query, query.lower())
