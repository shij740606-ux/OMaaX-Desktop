"""
Lightweight authoritative stage catalog.

Keep this module dependency-free so agents, tools and API layers can import it
without triggering orchestrator initialization.
"""

import json
import os
from pathlib import Path

UTILITY_STAGES = {
    "ncbi_fetch_sequences",
    "sequence_extract_by_id",
    "msa_region_plot",
    "tree_plot_revise",
    "synteny_plot_revise",
    "promoter_plot_revise",
    "prepare_organize",
    "prepare_normalize_gff",
    "prepare_longest_transcript",
    "prepare_extract_pep",
    "prepare_extract_cds",
    "prepare_filter_fasta",
    "prepare_simplify_ncbi",
}

REPAIR_STAGES = {
    "identify_refine",
    "subfamily_confirm",
}

PIPELINE_STAGES = {
    "wgs_qc",
    "wgs_index",
    "wgs_align",
    "wgs_markdup",
    "wgs_bam_qc",
    "wgs_haplotype_calling",
    "wgs_gvcf_combine",
    "wgs_joint_genotyping",
    "wgs_variant_select",
    "wgs_variant_filter",
    "wgs_impute",
    "wgs_maf_missing_filter",
    "pop_pca",
    "pop_phylogeny",
    "pop_structure",
    "pop_ld_decay",
    "pop_snp_density",
    "gwas_fmt",
    "gwas_kinship",
    "gwas_trait",
    "gwas_cov",
    "gwas_mlm",
    "gwas_plot",
    "gwas_emmax",
    "gwas_emplot",
    "gwas_glm",
    "identify",
    "genome_prepare",
    "rnaseq_qc",
    "phylogeny",
    "reference_classification",
    "synteny",
    "pep",
    "msa",
    "motif",
    "domain",
    "structure",
    "promoter",
    "tfbs",
    "expression",
    "enrichment",
    "ortholog",
    "interspecies_synteny",
    "karyotype",
    "ppi",
    "kaks",
    "combo",
    "gene_family_report",
    "rnaseq_align",
    "rnaseq_quant",
    "rnaseq_sample_qc",
    "rnaseq_de",
    "rnaseq_wgcna",
    "annotation",
    "rnaseq_enrich",
    "rnaseq_fgsea",
    "rnaseq_go_enrich",
    "rnaseq_kegg_enrich",
    "rnaseq_candidate_family",
    "rnaseq_report",
}

STAGE_DEPENDENCIES = {
    "wgs_qc": [],
    "wgs_index": [],
    "wgs_align": ["wgs_qc", "wgs_index"],
    "wgs_markdup": ["wgs_align"],
    "wgs_bam_qc": ["wgs_markdup", "wgs_index"],
    "wgs_haplotype_calling": ["wgs_markdup", "wgs_bam_qc", "wgs_index"],
    "wgs_gvcf_combine": ["wgs_haplotype_calling", "wgs_index"],
    "wgs_joint_genotyping": ["wgs_gvcf_combine", "wgs_index"],
    "wgs_variant_select": ["wgs_joint_genotyping"],
    "wgs_variant_filter": ["wgs_variant_select"],
    "wgs_impute": ["wgs_variant_filter"],
    "wgs_maf_missing_filter": ["wgs_impute"],
    # Population-genomics summaries are sibling consumers of one VCF. They do
    # not form a serial chain; pop_structure may be selected explicitly by GWAS.
    "pop_pca": [],
    "pop_phylogeny": [],
    "pop_structure": [],
    "pop_ld_decay": [],
    "pop_snp_density": [],
    # GWAS formatting is rooted in an explicitly supplied external VCF.  It is
    # independent of the gene-family WGS preprocessing graph.
    "gwas_fmt": [],
    "gwas_kinship": ["gwas_fmt"],
    "gwas_trait": [],
    "gwas_cov": [],
    "gwas_mlm": ["gwas_fmt", "gwas_kinship", "gwas_trait", "gwas_cov"],
    "gwas_plot": ["gwas_mlm"],
    "gwas_emmax": ["gwas_fmt", "gwas_trait"],
    "gwas_emplot": ["gwas_emmax"],
    "gwas_glm": ["gwas_fmt", "gwas_trait", "gwas_cov"],
    "identify": [],
    # Repair/utility tasks are kept narrow.  User-reviewed subfamily
    # confirmation is the exception: it must not run before the predicted
    # reference classification exists for the same scope.
    "identify_refine": [],
    "subfamily_confirm": ["reference_classification"],
    "genome_prepare": [],
    "ncbi_fetch_sequences": [],
    "sequence_extract_by_id": [],
    "msa_region_plot": [],
    "tree_plot_revise": [],
    "synteny_plot_revise": [],
    "promoter_plot_revise": [],
    "prepare_organize": [],
    "prepare_normalize_gff": [],
    "prepare_longest_transcript": [],
    "prepare_extract_pep": [],
    "prepare_extract_cds": [],
    "prepare_filter_fasta": [],
    "prepare_simplify_ncbi": [],
    "rnaseq_qc": [],
    "phylogeny": ["identify"],
    "reference_classification": ["identify", "phylogeny", "motif", "structure", "domain", "combo"],
    "synteny": ["identify"],
    "pep": ["identify"],
    "msa": ["identify"],
    "motif": ["identify", "phylogeny"],
    "domain": ["identify", "phylogeny"],
    "structure": ["identify", "phylogeny"],
    "promoter": ["identify", "phylogeny"],
    "tfbs": ["genome_prepare", "identify"],
    "expression": ["identify", "phylogeny"],
    "enrichment": ["identify", "phylogeny"],
    "ortholog": ["identify"],
    "interspecies_synteny": ["identify"],
    "karyotype": ["identify", "synteny"],
    "ppi": ["identify"],
    "kaks": ["identify"],
    "combo": ["identify", "phylogeny", "motif", "structure", "domain"],
    "rnaseq_align": ["rnaseq_qc"],
    "rnaseq_quant": ["rnaseq_align"],
    "rnaseq_sample_qc": ["rnaseq_quant"],
    "rnaseq_de": ["rnaseq_sample_qc"],
    "rnaseq_wgcna": ["rnaseq_sample_qc"],
    "annotation": [],
    "rnaseq_enrich": ["rnaseq_de"],
    "rnaseq_fgsea": ["rnaseq_de"],
    "rnaseq_go_enrich": ["rnaseq_de"],
    "rnaseq_kegg_enrich": ["rnaseq_de"],
    "rnaseq_candidate_family": ["rnaseq_de", "rnaseq_go_enrich", "rnaseq_kegg_enrich", "rnaseq_fgsea"],
    # A report is a snapshot of whatever RNA-seq evidence is already successful.
    # Static dependencies would incorrectly prevent intentional mid-pipeline reports.
    "rnaseq_report": [],
}

PARALLEL_GROUPS = [
    ["pop_pca", "pop_phylogeny", "pop_structure", "pop_ld_decay", "pop_snp_density"],
    ["phylogeny", "pep", "synteny", "interspecies_synteny"],
    ["reference_classification"],
    ["motif", "domain", "structure", "promoter", "tfbs", "expression", "ortholog"],
    ["karyotype"],
    ["kaks", "ppi"],
]

DEFAULT_QUEUE_PREFIX = "plantai_dev_7880"
MIN_STAGE_CPU = 4
RESOURCE_CPU_CAPS_DISABLED = os.environ.get("BBAV5_DISABLE_STAGE_CPU_CAPS", "").strip().lower() in {"1", "true", "yes", "on"}

# Stage defaults are deliberately at least 4 CPU. Resource caps are enabled by
# default and may be overridden by the frontend resource governor.
STAGE_RESOURCE_PROFILES = {
    "wgs_qc": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 8},
    "wgs_index": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 8},
    "wgs_align": {"queue": "heavy", "resource_class": "heavy", "default_cpu": 6, "max_cpu": 8},
    "wgs_markdup": {"queue": "heavy", "resource_class": "heavy", "default_cpu": 4, "max_cpu": 8},
    "wgs_bam_qc": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 8},
    "wgs_haplotype_calling": {"queue": "heavy", "resource_class": "heavy", "default_cpu": 4, "max_cpu": 8},
    "wgs_gvcf_combine": {"queue": "heavy", "resource_class": "heavy", "default_cpu": 4, "max_cpu": 8},
    "wgs_joint_genotyping": {"queue": "heavy", "resource_class": "heavy", "default_cpu": 4, "max_cpu": 8},
    "wgs_variant_select": {"queue": "heavy", "resource_class": "heavy", "default_cpu": 2, "max_cpu": 4},
    "wgs_variant_filter": {"queue": "heavy", "resource_class": "heavy", "default_cpu": 2, "max_cpu": 4},
    "wgs_impute": {"queue": "heavy", "resource_class": "heavy", "default_cpu": 2, "max_cpu": 4},
    "wgs_maf_missing_filter": {"queue": "medium", "resource_class": "medium", "default_cpu": 2, "max_cpu": 4},
    "pop_pca": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 4},
    "pop_phylogeny": {"queue": "heavy", "resource_class": "heavy", "default_cpu": 6, "max_cpu": 6},
    "pop_structure": {"queue": "heavy", "resource_class": "heavy", "default_cpu": 6, "max_cpu": 6},
    "pop_ld_decay": {"queue": "heavy", "resource_class": "heavy", "default_cpu": 6, "max_cpu": 6},
    "pop_snp_density": {"queue": "light", "resource_class": "light", "default_cpu": 4, "max_cpu": 4},
    "gwas_fmt": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 4},
    "gwas_kinship": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 4},
    "gwas_trait": {"queue": "light", "resource_class": "light", "default_cpu": 4, "max_cpu": 4},
    "gwas_cov": {"queue": "light", "resource_class": "light", "default_cpu": 4, "max_cpu": 4},
    "gwas_mlm": {"queue": "heavy", "resource_class": "heavy", "concurrency_group": "gwas_model_heavy", "default_cpu": 6, "max_cpu": 6},
    "gwas_plot": {"queue": "light", "resource_class": "light", "default_cpu": 4, "max_cpu": 4},
    "gwas_emmax": {"queue": "heavy", "resource_class": "heavy", "concurrency_group": "gwas_model_heavy", "default_cpu": 6, "max_cpu": 6},
    "gwas_emplot": {"queue": "light", "resource_class": "light", "default_cpu": 4, "max_cpu": 4},
    "gwas_glm": {"queue": "heavy", "resource_class": "heavy", "concurrency_group": "gwas_model_heavy", "default_cpu": 6, "max_cpu": 6},
    # CPU-heavy local tools.
    "phylogeny": {"queue": "heavy", "resource_class": "heavy", "default_cpu": 6, "max_cpu": 6},
    "synteny": {"queue": "heavy", "resource_class": "heavy", "default_cpu": 6, "max_cpu": 6},
    "interspecies_synteny": {"queue": "heavy", "resource_class": "heavy", "default_cpu": 6, "max_cpu": 6},
    "annotation": {"queue": "rnaseq", "resource_class": "rnaseq", "default_cpu": 8, "max_cpu": 10},

    # Moderate CPU tasks.
    "identify": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 4},
    "identify_refine": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 4},
    "subfamily_confirm": {"queue": "light", "resource_class": "light", "default_cpu": 4, "max_cpu": 4},
    "motif": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 4},
    "domain": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 4},
    "reference_classification": {"queue": "light", "resource_class": "light", "default_cpu": 4, "max_cpu": 4},
    "kaks": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 4},
    "ortholog": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 4},
    "msa": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 4},
    "genome_prepare": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 4},
    "ncbi_fetch_sequences": {"queue": "light", "resource_class": "web", "default_cpu": 4, "max_cpu": 4},
    "sequence_extract_by_id": {"queue": "light", "resource_class": "light", "default_cpu": 4, "max_cpu": 4},
    "msa_region_plot": {"queue": "light", "resource_class": "light", "default_cpu": 4, "max_cpu": 4},
    "tree_plot_revise": {"queue": "light", "resource_class": "light", "default_cpu": 4, "max_cpu": 4},
    "synteny_plot_revise": {"queue": "light", "resource_class": "light", "default_cpu": 4, "max_cpu": 4},
    "promoter_plot_revise": {"queue": "light", "resource_class": "light", "default_cpu": 4, "max_cpu": 4},
    "prepare_organize": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 4},
    "prepare_normalize_gff": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 4},
    "prepare_longest_transcript": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 4},
    "prepare_extract_pep": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 4},
    "prepare_extract_cds": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 4},
    "prepare_filter_fasta": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 4},
    "prepare_simplify_ncbi": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 4},

    # Web/API/light plotting/data-shaping tasks.
    "structure": {"queue": "light", "resource_class": "light", "default_cpu": 4, "max_cpu": 4},
    "promoter": {"queue": "light", "resource_class": "web", "default_cpu": 4, "max_cpu": 4},
    "tfbs": {"queue": "medium", "resource_class": "medium", "default_cpu": 4, "max_cpu": 4},
    "pep": {"queue": "light", "resource_class": "light", "default_cpu": 4, "max_cpu": 4},
    "ppi": {"queue": "light", "resource_class": "web", "default_cpu": 4, "max_cpu": 4},
    "karyotype": {"queue": "light", "resource_class": "light", "default_cpu": 4, "max_cpu": 4},
    "expression": {"queue": "light", "resource_class": "light", "default_cpu": 4, "max_cpu": 4},
    "enrichment": {"queue": "light", "resource_class": "light", "default_cpu": 4, "max_cpu": 4},
    "combo": {"queue": "light", "resource_class": "light", "default_cpu": 4, "max_cpu": 4},

    # RNA-seq stages are isolated because they can dominate CPU, disk IO and RAM.
    "rnaseq_qc": {"queue": "rnaseq", "resource_class": "rnaseq", "default_cpu": 8, "max_cpu": 20},
    "rnaseq_align": {"queue": "rnaseq", "resource_class": "rnaseq", "default_cpu": 8, "max_cpu": 20},
    "rnaseq_quant": {"queue": "rnaseq", "resource_class": "rnaseq", "default_cpu": 6, "max_cpu": 8},
    "rnaseq_sample_qc": {"queue": "rnaseq", "resource_class": "rnaseq", "default_cpu": 4, "max_cpu": 4},
    "rnaseq_de": {"queue": "rnaseq", "resource_class": "rnaseq", "default_cpu": 4, "max_cpu": 4},
    "rnaseq_wgcna": {"queue": "rnaseq", "resource_class": "rnaseq", "default_cpu": 4, "max_cpu": 4},
    "rnaseq_enrich": {"queue": "rnaseq", "resource_class": "rnaseq", "default_cpu": 4, "max_cpu": 4},
    "rnaseq_go_enrich": {"queue": "rnaseq", "resource_class": "rnaseq", "default_cpu": 4, "max_cpu": 4},
    "rnaseq_kegg_enrich": {"queue": "rnaseq", "resource_class": "rnaseq", "default_cpu": 4, "max_cpu": 4},
    "rnaseq_fgsea": {"queue": "rnaseq", "resource_class": "rnaseq", "default_cpu": 4, "max_cpu": 4},
    "rnaseq_candidate_family": {"queue": "rnaseq", "resource_class": "rnaseq", "default_cpu": 4, "max_cpu": 4},
    "rnaseq_report": {"queue": "light", "resource_class": "light", "default_cpu": 4, "max_cpu": 4},
}

DEFAULT_STAGE_RESOURCE_PROFILE = {
    "queue": "medium",
    "resource_class": "medium",
    "default_cpu": 4,
    "max_cpu": 4,
}


def _resource_config_path() -> Path:
    root = Path(os.environ.get("PROJECT_ROOT", Path(__file__).resolve().parents[2])).resolve()
    state_dir = Path(os.environ.get("BBAV5_RESOURCE_STATE_DIR", root / "workspace" / "resource_governor"))
    return state_dir / "config.json"


def _stage_cpu_overrides() -> dict:
    path = _resource_config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    overrides = data.get("stage_cpu_overrides", {})
    return overrides if isinstance(overrides, dict) else {}


def _apply_stage_cpu_override(stage: str, profile: dict) -> dict:
    override = _stage_cpu_overrides().get(stage)
    if not isinstance(override, dict):
        return profile
    try:
        max_cpu = int(override.get("max_cpu", profile.get("max_cpu", profile.get("default_cpu", 1))))
    except (TypeError, ValueError):
        max_cpu = int(profile.get("max_cpu", profile.get("default_cpu", 1)))
    try:
        default_cpu = int(override.get("default_cpu", profile.get("default_cpu", max_cpu)))
    except (TypeError, ValueError):
        default_cpu = int(profile.get("default_cpu", max_cpu))
    max_cpu = max(MIN_STAGE_CPU, max_cpu)
    default_cpu = max(MIN_STAGE_CPU, default_cpu)
    profile["default_cpu"] = default_cpu
    profile["max_cpu"] = max_cpu
    profile["cpu_override"] = True
    return profile

STAGE_ORDER = [
    "wgs_qc",
    "wgs_index",
    "wgs_align",
    "wgs_markdup",
    "wgs_bam_qc",
    "wgs_haplotype_calling",
    "wgs_gvcf_combine",
    "wgs_joint_genotyping",
    "wgs_variant_select",
    "wgs_variant_filter",
    "wgs_impute",
    "wgs_maf_missing_filter",
    "pop_pca",
    "pop_phylogeny",
    "pop_structure",
    "pop_ld_decay",
    "pop_snp_density",
    "gwas_fmt",
    "gwas_kinship",
    "gwas_trait",
    "gwas_cov",
    "gwas_mlm",
    "gwas_plot",
    "gwas_emmax",
    "gwas_emplot",
    "gwas_glm",
    "genome_prepare",
    "identify",
    "identify_refine",
    "subfamily_confirm",
    "ncbi_fetch_sequences",
    "sequence_extract_by_id",
    "msa_region_plot",
    "tree_plot_revise",
    "synteny_plot_revise",
    "promoter_plot_revise",
    "prepare_organize",
    "prepare_normalize_gff",
    "prepare_longest_transcript",
    "prepare_extract_pep",
    "prepare_extract_cds",
    "prepare_filter_fasta",
    "prepare_simplify_ncbi",
    "rnaseq_qc",
    "phylogeny",
    "reference_classification",
    "synteny",
    "pep",
    "interspecies_synteny",
    "msa",
    "motif",
    "structure",
    "domain",
    "promoter",
    "tfbs",
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
    "rnaseq_report",
    "rnaseq_enrich",
    "gene_family_report",
]

STAGE_INFO = {
    "wgs_qc": {"name": "WGS质控", "estimated_time": 30, "cpu": 4},
    "wgs_index": {"name": "WGS参考索引", "estimated_time": 15, "cpu": 4},
    "wgs_align": {"name": "WGS序列比对", "estimated_time": 120, "cpu": 6},
    "wgs_markdup": {"name": "WGS去重复", "estimated_time": 90, "cpu": 4},
    "wgs_bam_qc": {"name": "WGS BAM质控", "estimated_time": 30, "cpu": 4},
    "wgs_haplotype_calling": {"name": "WGS单样本变异检测", "estimated_time": 120, "cpu": 4},
    "wgs_gvcf_combine": {"name": "WGS GVCF合并", "estimated_time": 60, "cpu": 4},
    "wgs_joint_genotyping": {"name": "WGS联合分型", "estimated_time": 90, "cpu": 4},
    "wgs_variant_select": {"name": "WGS变异类型选择", "estimated_time": 30, "cpu": 2},
    "wgs_variant_filter": {"name": "WGS变异硬过滤", "estimated_time": 30, "cpu": 2},
    "wgs_impute": {"name": "WGS基因型填充", "estimated_time": 60, "cpu": 2},
    "wgs_maf_missing_filter": {"name": "WGS MAF与缺失率过滤", "estimated_time": 30, "cpu": 2},
    "pop_pca": {"name": "群体PCA分析", "estimated_time": 30, "cpu": 4},
    "pop_phylogeny": {"name": "群体系统树分析", "estimated_time": 45, "cpu": 6},
    "pop_structure": {"name": "群体结构分析", "estimated_time": 90, "cpu": 6},
    "pop_ld_decay": {"name": "群体LD衰减分析", "estimated_time": 60, "cpu": 6},
    "pop_snp_density": {"name": "SNP密度分析", "estimated_time": 20, "cpu": 4},
    "gwas_fmt": {"name": "GWAS VCF排序与HapMap转换", "estimated_time": 20, "cpu": 4},
    "gwas_kinship": {"name": "GWAS TASSEL亲缘关系矩阵", "estimated_time": 20, "cpu": 4},
    "gwas_trait": {"name": "GWAS表型数据准备", "estimated_time": 5, "cpu": 4},
    "gwas_cov": {"name": "GWAS群体结构协变量准备", "estimated_time": 5, "cpu": 4},
    "gwas_mlm": {"name": "GWAS TASSEL MLM/CMLM", "estimated_time": 60, "cpu": 6},
    "gwas_plot": {"name": "GWAS MLM结果绘图", "estimated_time": 10, "cpu": 4},
    "gwas_emmax": {"name": "GWAS EMMAX关联分析", "estimated_time": 60, "cpu": 6},
    "gwas_emplot": {"name": "GWAS EMMAX结果绘图", "estimated_time": 10, "cpu": 4},
    "gwas_glm": {"name": "GWAS TASSEL GLM", "estimated_time": 60, "cpu": 6},
    "identify": {"name": "基因家族鉴定", "estimated_time": 5, "cpu": 4},
    "identify_refine": {"name": "修订鉴定成员", "estimated_time": 5, "cpu": 4},
    "subfamily_confirm": {"name": "确认亚家族分类", "estimated_time": 3, "cpu": 4},
    "phylogeny": {"name": "系统发育分析", "estimated_time": 30, "cpu": 6},
    "reference_classification": {"name": "参考亚家族分类", "estimated_time": 5, "cpu": 4},
    "motif": {"name": "Motif分析", "estimated_time": 20, "cpu": 4},
    "domain": {"name": "结构域分析", "estimated_time": 15, "cpu": 4},
    "structure": {"name": "基因结构分析", "estimated_time": 10, "cpu": 4},
    "promoter": {"name": "启动子分析", "estimated_time": 20, "cpu": 4},
    "tfbs": {"name": "TF结合位点预测", "estimated_time": 30, "cpu": 4},
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
    "gene_family_report": {"name": "基因家族结果报告", "estimated_time": 2, "cpu": 1},
    "genome_prepare": {"name": "基因组准备", "estimated_time": 10, "cpu": 4},
    "ncbi_fetch_sequences": {"name": "NCBI序列下载", "estimated_time": 5, "cpu": 4},
    "sequence_extract_by_id": {"name": "按ID提取序列", "estimated_time": 5, "cpu": 4},
    "msa_region_plot": {"name": "MSA区间绘图", "estimated_time": 3, "cpu": 4},
    "synteny_plot_revise": {"name": "种内共线性Circos重绘", "estimated_time": 3, "cpu": 4},
    "promoter_plot_revise": {"name": "启动子自定义绘图", "estimated_time": 3, "cpu": 4},
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
    "rnaseq_enrich": {"name": "RNA-seq富集分析", "estimated_time": 15, "cpu": 4},
    "rnaseq_fgsea": {"name": "RNA-seq fgsea分析", "estimated_time": 15, "cpu": 4},
    "rnaseq_candidate_family": {"name": "RNA-seq候选家族筛选", "estimated_time": 20, "cpu": 4},
    "rnaseq_report": {"name": "RNA-seq结果报告", "estimated_time": 2, "cpu": 4},
}

EXECUTABLE_STAGES = {
    "wgs_qc",
    "wgs_index",
    "wgs_align",
    "wgs_markdup",
    "wgs_bam_qc",
    "wgs_haplotype_calling",
    "wgs_gvcf_combine",
    "wgs_joint_genotyping",
    "wgs_variant_select",
    "wgs_variant_filter",
    "wgs_impute",
    "wgs_maf_missing_filter",
    "pop_pca",
    "pop_phylogeny",
    "pop_structure",
    "pop_ld_decay",
    "pop_snp_density",
    "gwas_fmt",
    "gwas_kinship",
    "gwas_trait",
    "gwas_cov",
    "gwas_mlm",
    "gwas_plot",
    "gwas_emmax",
    "gwas_emplot",
    "gwas_glm",
    "identify",
    "identify_refine",
    "subfamily_confirm",
    "genome_prepare",
    "ncbi_fetch_sequences",
    "sequence_extract_by_id",
    "msa_region_plot",
    "tree_plot_revise",
    "synteny_plot_revise",
    "promoter_plot_revise",
    "prepare_organize",
    "prepare_normalize_gff",
    "prepare_longest_transcript",
    "prepare_extract_pep",
    "prepare_extract_cds",
    "prepare_filter_fasta",
    "prepare_simplify_ncbi",
    "rnaseq_qc",
    "phylogeny",
    "reference_classification",
    "synteny",
    "pep",
    "interspecies_synteny",
    "motif",
    "domain",
    "structure",
    "promoter",
    "tfbs",
    "expression",
    "ortholog",
    "karyotype",
    "ppi",
    "kaks",
    "combo",
    "gene_family_report",
    "rnaseq_align",
    "rnaseq_quant",
    "rnaseq_sample_qc",
    "rnaseq_de",
    "rnaseq_wgcna",
    "annotation",
    "rnaseq_go_enrich",
    "rnaseq_kegg_enrich",
    "rnaseq_enrich",
    "rnaseq_fgsea",
    "rnaseq_candidate_family",
    "rnaseq_report",
}


def is_stage_executable(stage: str) -> bool:
    return stage in EXECUTABLE_STAGES


def is_utility_stage(stage: str) -> bool:
    return stage in UTILITY_STAGES


def is_repair_stage(stage: str) -> bool:
    return stage in REPAIR_STAGES


def is_pipeline_stage(stage: str) -> bool:
    return stage in PIPELINE_STAGES


def get_queue_prefix() -> str:
    return os.environ.get("BBAV5_QUEUE_PREFIX", DEFAULT_QUEUE_PREFIX).strip() or DEFAULT_QUEUE_PREFIX


def get_stage_resource_profile(stage: str) -> dict:
    profile = dict(DEFAULT_STAGE_RESOURCE_PROFILE)
    profile.update(STAGE_RESOURCE_PROFILES.get(stage, {}))
    return _apply_stage_cpu_override(stage, profile)


def get_stage_queue(stage: str, queue_prefix: str | None = None) -> str:
    prefix = (queue_prefix or get_queue_prefix()).strip() or DEFAULT_QUEUE_PREFIX
    profile = get_stage_resource_profile(stage)
    return f"{prefix}.{profile['queue']}"


def get_stage_default_cpu(stage: str) -> int:
    return int(get_stage_resource_profile(stage)["default_cpu"])


def clamp_stage_cpu(stage: str, requested_cpu=None) -> int:
    profile = get_stage_resource_profile(stage)
    default_cpu = int(profile["default_cpu"])
    try:
        cpu = int(requested_cpu)
    except (TypeError, ValueError):
        cpu = default_cpu
    if cpu < MIN_STAGE_CPU:
        cpu = MIN_STAGE_CPU
    if RESOURCE_CPU_CAPS_DISABLED:
        return max(MIN_STAGE_CPU, cpu)
    max_cpu = int(profile["max_cpu"])
    return max(MIN_STAGE_CPU, min(cpu, max_cpu))


def get_unsupported_stages(stages: list[str]) -> list[str]:
    return [stage for stage in stages if not is_stage_executable(stage)]


def get_stage_dependencies(stage: str) -> list[str]:
    return STAGE_DEPENDENCIES.get(stage, [])


def get_all_dependencies(stage: str, visited=None) -> list[str]:
    if visited is None:
        visited = set()
    if stage in visited:
        return []

    visited.add(stage)
    all_deps = []
    for dep in STAGE_DEPENDENCIES.get(stage, []):
        all_deps.extend(get_all_dependencies(dep, visited))
        if dep not in all_deps:
            all_deps.append(dep)
    return all_deps


def complete_dependencies(stages: list[str]) -> list[str]:
    if (
        stages
        and "subfamily_confirm" not in stages
        and all(stage in UTILITY_STAGES or stage in REPAIR_STAGES for stage in stages)
    ):
        return list(dict.fromkeys(stages))

    required = set(stages)

    changed = True
    while changed:
        changed = False
        for stage in list(required):
            for dep in STAGE_DEPENDENCIES.get(stage, []):
                if dep not in required:
                    required.add(dep)
                    changed = True

    ordered = []
    remaining = required.copy()
    order_index = {stage: idx for idx, stage in enumerate(STAGE_ORDER)}

    while remaining:
        ready = {s for s in remaining if all(d in ordered for d in STAGE_DEPENDENCIES.get(s, []))}
        if not ready:
            ordered.extend(sorted(remaining, key=lambda s: order_index.get(s, len(STAGE_ORDER))))
            break

        for s in stages:
            if s in ready:
                ordered.append(s)
                remaining.remove(s)

        for s in sorted(ready, key=lambda item: order_index.get(item, len(STAGE_ORDER))):
            if s not in stages:
                ordered.append(s)
                remaining.remove(s)

    return ordered
