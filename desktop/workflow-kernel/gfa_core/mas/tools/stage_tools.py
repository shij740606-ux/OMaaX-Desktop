"""
Stage-level Tool Registry for Multi-Agent System
将bbav20311的分析阶段脚本封装为Agent可调用的Tool
"""
import os
import subprocess
import sys
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Optional, Dict, List
from datetime import datetime

from gfa_core.project_paths import (
    CONDA_BASE,
    PROJECT_ROOT,
    get_conda_env_bin,
    get_conda_env_rscript,
    get_configured_path,
    get_wgs_bcftools,
    get_wgs_java,
    get_wgs_samtools,
)

CLI_DIR = PROJECT_ROOT / "cli"
CONDA_ENV = "test4py311"
# 镜像标准形态: 每镜像=单一环境, 工具一律 PATH 裸命令(运行容器 PATH 即目标环境)
ASAW_BIN = Path("")
ASAW_RSCRIPT = Path("Rscript")
TASSEL_PIPELINE = get_configured_path("TASSEL_PIPELINE", "run_pipeline.pl")
GWAS_EMMAX_BIN = get_configured_path("GWAS_EMMAX_BIN", "emmax")
WGS_BEAGLE_JAR = get_configured_path("WGS_BEAGLE_JAR")

# 镜像标准形态: 跨环境由编排层映射镜像; 单一环境工具一律裸命令
CONDA_ENVS = {
    "test4py311": Path(sys.executable),
    "rnaseq": Path(sys.executable),
    "rnaR": Path("Rscript"),
}


def _stage_result_is_success(
    message: Any,
    *,
    manifest_stage: str,
    expected_stage: str,
    manifest_status: str = "success",
) -> bool:
    """Classify one stage result without treating optional skip notes as failures."""
    matches_stage = bool(manifest_stage and manifest_stage == expected_stage)
    if isinstance(message, str) and message.startswith("❌"):
        return False
    if isinstance(message, str) and message.startswith("✅"):
        return matches_stage

    failure_tokens = (
        " requires ",
        " required ",
        " not found",
        " missing ",
        " failed",
        "失败",
        "缺少",
        "找不到",
        "不存在",
    )
    lower_message = message.lower() if isinstance(message, str) else ""
    if isinstance(message, str) and (
        any(token in lower_message for token in failure_tokens if token.isascii())
        or any(token in message for token in failure_tokens if not token.isascii())
    ):
        return False
    return bool(matches_stage and manifest_status in {"success", "partial_success"})


@dataclass
class StageTool:
    """分析阶段工具定义"""
    id: str
    name: str
    script: str
    category: str
    description: str
    required_inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    timeout: int = 3600
    conda_env: str = CONDA_ENV


class StageToolExecutor:
    """分析阶段工具执行器 - 复用 pipeline_backend.run_stage()"""
    
    def __init__(self, project_root: Path = PROJECT_ROOT):
        self.project_root = project_root
        self.cli_dir = project_root / "apps" / "cli"
        self.workspace_root = project_root / "workspace"
        self.input_root = project_root / "input"

    @staticmethod
    def _has_non_comment_data(path: Path) -> bool:
        try:
            if not path.exists() or path.stat().st_size == 0:
                return False
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for raw in handle:
                    line = raw.strip()
                    if line and not line.startswith("#"):
                        return True
        except Exception:
            return False
        return False

    def _preflight_enrich_background(
        self,
        stage_tool: StageTool,
        job_id: str,
        enrich_type: str,
        enrich_background_source: str,
        enrich_species: str,
    ) -> dict | None:
        """Block implicit long annotation when enrichment background is missing."""
        if stage_tool.id not in {"rnaseq_enrich", "rnaseq_go_enrich", "rnaseq_kegg_enrich"}:
            return None

        source = (enrich_background_source or "auto").strip().lower()
        if source in {"auto", "annotation", "eggnog", "auto_annotation"}:
            return None

        input_dir = self.input_root / job_id
        final_dir = self.workspace_root / job_id / "final"
        gene2go_files = (
            list(input_dir.glob("*gene2go*.txt")) + list(input_dir.glob("*gene2go*.tsv")) +
            list(final_dir.glob("*gene2go*.txt")) + list(final_dir.glob("*gene2go*.tsv"))
        )
        gene2ko_files = (
            list(input_dir.glob("*gene2ko*.txt")) + list(input_dir.glob("*gene2ko*.tsv")) +
            list(final_dir.glob("*gene2ko*.txt")) + list(final_dir.glob("*gene2ko*.tsv"))
        )
        gene2go_files = [p for p in gene2go_files if self._has_non_comment_data(p) and ".test." not in p.name]
        gene2ko_files = [p for p in gene2ko_files if self._has_non_comment_data(p) and ".test." not in p.name]

        enrich_type_val = (enrich_type or "go").strip().lower()
        if enrich_type_val not in {"go", "kegg", "both"}:
            enrich_type_val = "go"
        need_go = enrich_type_val in {"go", "both"}
        need_kegg = enrich_type_val in {"kegg", "both"}
        supported_orgdb_species = {"ath", "hsa", "mmu", "rno", "dme", "sce"}
        species_val = (enrich_species or "").strip().lower()

        if source == "orgdb":
            if need_kegg:
                msg = (
                    "❌ 当前 KEGG 富集不支持使用 OrgDb 作为背景。请上传 gene2ko.txt/gene2ko.tsv，"
                    "或选择“自动注释 + 继续富集”生成 gene2ko。"
                )
                return {"status": "error", "stage": stage_tool.id, "message": msg, "error": msg}
            if species_val not in supported_orgdb_species:
                msg = (
                    f"❌ RNA-seq GO 富集选择了 OrgDb，但物种代码 '{species_val or '未指定'}' 不受支持。"
                    "支持：ath, hsa, mmu, rno, dme, sce。请上传 gene2go 背景文件，"
                    "或选择“自动注释 + 继续富集”。"
                )
                return {"status": "error", "stage": stage_tool.id, "message": msg, "error": msg}
            return None

        missing_go = need_go and not gene2go_files and species_val not in supported_orgdb_species
        missing_kegg = need_kegg and not gene2ko_files
        if source == "gene2go":
            missing_go = need_go and not gene2go_files

        if missing_go or missing_kegg:
            missing = []
            if missing_go:
                missing.append("gene2go.txt/gene2go.tsv")
            if missing_kegg:
                missing.append("gene2ko.txt/gene2ko.tsv")
            msg = (
                "❌ 无法进行 RNA-seq 富集分析：缺少背景注释文件。\n\n"
                f"当前 job 没有找到可用的 {' 和 '.join(missing)}。\n"
                "请选择一种处理方式：上传已有背景文件后重试，或确认使用本地按需自动注释后继续富集。"
                "GO 缺背景时运行 InterProScan，KEGG 缺背景时运行 KofamScan。"
            )
            return {"status": "error", "stage": stage_tool.id, "message": msg, "error": msg}

        return None
    
    async def execute(self, stage_tool: StageTool, job_id: str, prefix: str, 
                extra_params: dict = None, input_files: dict = None) -> dict:
        """执行分析阶段 - 使用 pipeline_backend.run_stage()"""
        import asyncio
        from datetime import datetime
        
        # 延迟导入避免循环依赖
        try:
            from apps.web.pipeline_backend import run_stage, JobInfo
        except ImportError as e:
            return {
                "status": "error",
                "error": f"Failed to import pipeline_backend: {e}",
                "stage": stage_tool.id
            }
        
        # 确保目录存在
        workspace = self.workspace_root / job_id
        workspace.mkdir(parents=True, exist_ok=True)
        input_dir = self.input_root / job_id
        input_dir.mkdir(parents=True, exist_ok=True)
        logs_dir = workspace / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        
        # 构建 JobInfo
        job = JobInfo(
            job_id=job_id,
            input_dir=str(input_dir),
            workspace_dir=str(workspace),
            logs_dir=str(logs_dir),
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        
        requested_cpu = extra_params.get("cpu", None) if extra_params else None
        try:
            from gfa_core.mas.stage_catalog import clamp_stage_cpu

            cpu = clamp_stage_cpu(stage_tool.id, requested_cpu)
        except Exception:
            try:
                cpu = max(1, min(int(requested_cpu), 4))
            except (TypeError, ValueError):
                cpu = 4
        if extra_params is not None:
            extra_params["requested_cpu"] = requested_cpu
            extra_params["cpu"] = cpu
        ref_species_value = (extra_params or {}).get("refSpecies") or None
        if isinstance(ref_species_value, str):
            ref_species_value = [item.strip() for item in ref_species_value.split(",") if item.strip()]
        rnaseq_foreground_mode = (extra_params or {}).get("rnaseqForegroundMode", "auto")
        enrich_background_source = (extra_params or {}).get("enrichBackgroundSource", "auto")
        enrich_species = (extra_params or {}).get("enrichSpecies", "")
        enrich_key_type = (extra_params or {}).get("enrichKeyType", "auto")
        enrich_type = (extra_params or {}).get("enrichType", stage_tool.parameters.get("enrichType", "go"))
        if stage_tool.id == "rnaseq_go_enrich":
            enrich_type = "go"
        elif stage_tool.id == "rnaseq_kegg_enrich":
            enrich_type = "kegg"
        kegg_organism = (extra_params or {}).get("keggOrganism", "")
        annotation_applications = (extra_params or {}).get("annotationApplications", ["Pfam", "ProSiteProfiles"])
        target_species = (extra_params or {}).get("targetSpecies") or None
        interspecies_ref_species = (extra_params or {}).get("refSpeciesList") or None
        interspecies_blast_engine = (extra_params or {}).get("blastEngine", "diamond") or "diamond"
        interspecies_plot_mode = (extra_params or {}).get("interspeciesPlotMode", "both") or "both"

        def param_value(*keys, default=None):
            params = extra_params or {}
            for key in keys:
                if key in params and params.get(key) is not None and params.get(key) != "":
                    return params.get(key)
            return default

        def bool_param(*keys, default=False):
            value = param_value(*keys, default=default)
            if isinstance(value, str):
                text = value.strip().lower()
                if text in {"1", "true", "yes", "y", "on"}:
                    return True
                if text in {"0", "false", "no", "n", "off"}:
                    return False
                return default
            return bool(value)

        preflight_error = self._preflight_enrich_background(
            stage_tool,
            job_id,
            enrich_type,
            enrich_background_source,
            enrich_species,
        )
        if preflight_error:
            return preflight_error
        
        # 在线程池中执行同步的 run_stage
        loop = asyncio.get_event_loop()
        
        # 定义带工作目录切换的执行函数
        def run_stage_with_cwd():
            import os
            try:
                original_cwd = os.getcwd()
            except FileNotFoundError:
                # Celery workers can inherit a deleted project/workspace cwd after
                # local project roots are moved or symlinked. Fall back to the
                # stable project root before running stage code.
                original_cwd = str(self.project_root)
            try:
                os.chdir(self.project_root)
                value = run_stage(
                    stage=stage_tool.id,
                    job=job,
                    prefix=prefix,
                    cpu=cpu,
                    required_pfams=(extra_params or {}).get("requiredPfams") or None,
                    ref_species=ref_species_value,
                    id_evalue=(extra_params or {}).get("idEvalue", 0.001),
                    id_coverage=(extra_params or {}).get("idCoverage", 0.3),
                    blast_evalue=(extra_params or {}).get("blastEvalue", (extra_params or {}).get("blast_evalue", 1e-5)),
                    blast_identity=(extra_params or {}).get("blastIdentity", (extra_params or {}).get("blast_identity", 30.0)),
                    phy_mafft=(extra_params or {}).get("mafftParams", f"--auto --thread {cpu}"),
                    phy_iqtree=(extra_params or {}).get("iqtreeParams", f"-m MFP -bb 1000 -nt {cpu}"),
                    tree_file=(extra_params or {}).get("treeFile", "") or (extra_params or {}).get("tree_file", ""),
                    reference_subfamily_file=(extra_params or {}).get("referenceSubfamilyFile", ""),
                    reference_target_prefix=(extra_params or {}).get("referenceTargetPrefix", ""),
                    reference_prefix=(extra_params or {}).get("referencePrefix", "At"),
                    reference_overwrite_gene_subfamily=bool((extra_params or {}).get("referenceOverwriteGeneSubfamily", False)),
                    motif_nmotifs=(extra_params or {}).get("nmotifs", 10),
                    motif_minw=(extra_params or {}).get("minw", 6),
                    motif_maxw=(extra_params or {}).get("maxw", 200),
                    dom_evalue=(extra_params or {}).get("domEvalue", 1e-5),
                    dom_coverage=(extra_params or {}).get("domCoverage", 0.3),
                    id_transform=(extra_params or {}).get("idTransform", "none"),
                    geo_id=(extra_params or {}).get("geoId", ""),
                    rnaseq_mode=(extra_params or {}).get("rnaseqMode", "upload"),
                    species_name=(extra_params or {}).get("speciesName", ""),
                    top_pathways=(extra_params or {}).get("topPathways", 20),
                    rnaseq_foreground_mode=rnaseq_foreground_mode,
                    enrich_background_source=enrich_background_source,
                    enrich_species=enrich_species,
                    enrich_key_type=enrich_key_type,
                    enrich_type=enrich_type,
                    kegg_organism=kegg_organism,
                    annotation_applications=annotation_applications,
                    rnaseq_sample_config=(extra_params or {}).get("rnaseqSampleConfig", ""),
                    wgs_sample_config=param_value("wgsSampleConfig", "wgs_sample_config", default=""),
                    wgs_reference_fasta=param_value("wgsReferenceFasta", "wgs_reference_fasta", default=""),
                    wgs_qc_manifest=param_value("wgsQcManifest", "wgs_qc_manifest", default=""),
                    wgs_index_manifest=param_value("wgsIndexManifest", "wgs_index_manifest", default=""),
                    wgs_sample_set_id=param_value("wgsSampleSetId", "wgs_sample_set_id", default=""),
                    wgs_reference_id=param_value("wgsReferenceId", "wgs_reference_id", default=""),
                    wgs_align_manifest=param_value("wgsAlignManifest", "wgs_align_manifest", default=""),
                    wgs_markdup_manifest=param_value("wgsMarkdupManifest", "wgs_markdup_manifest", default=""),
                    wgs_bam_qc_manifest=param_value("wgsBamQcManifest", "wgs_bam_qc_manifest", default=""),
                    wgs_haplotype_manifest=param_value("wgsHaplotypeManifest", "wgs_haplotype_manifest", default=""),
                    wgs_combine_manifest=param_value("wgsCombineManifest", "wgs_combine_manifest", default=""),
                    wgs_joint_manifest=param_value("wgsJointManifest", "wgs_joint_manifest", default=""),
                    wgs_select_manifest=param_value("wgsSelectManifest", "wgs_select_manifest", default=""),
                    wgs_filter_manifest=param_value("wgsFilterManifest", "wgs_filter_manifest", default=""),
                    wgs_impute_manifest=param_value("wgsImputeManifest", "wgs_impute_manifest", default=""),
                    wgs_analysis_id=param_value("wgsAnalysisId", "wgs_analysis_id", default=""),
                    wgs_index_analysis_id=param_value("wgsIndexAnalysisId", "wgs_index_analysis_id", default=""),
                    wgs_markdup_analysis_id=param_value("wgsMarkdupAnalysisId", "wgs_markdup_analysis_id", default=""),
                    wgs_bam_qc_analysis_id=param_value("wgsBamQcAnalysisId", "wgs_bam_qc_analysis_id", default=""),
                    wgs_haplotype_analysis_id=param_value("wgsHaplotypeAnalysisId", "wgs_haplotype_analysis_id", default=""),
                    wgs_combine_analysis_id=param_value("wgsCombineAnalysisId", "wgs_combine_analysis_id", default=""),
                    wgs_joint_analysis_id=param_value("wgsJointAnalysisId", "wgs_joint_analysis_id", default=""),
                    wgs_select_analysis_id=param_value("wgsSelectAnalysisId", "wgs_select_analysis_id", default=""),
                    wgs_filter_analysis_id=param_value("wgsFilterAnalysisId", "wgs_filter_analysis_id", default=""),
                    wgs_impute_analysis_id=param_value("wgsImputeAnalysisId", "wgs_impute_analysis_id", default=""),
                    wgs_fastp=param_value("wgsFastp", "wgs_fastp", default=str(ASAW_BIN / "fastp")),
                    wgs_samtools=param_value("wgsSamtools", "wgs_samtools", default=str(ASAW_BIN / "samtools")),
                    wgs_bwa_mem2=param_value("wgsBwaMem2", "wgs_bwa_mem2", default=str(ASAW_BIN / "bwa-mem2")),
                    wgs_gatk=param_value("wgsGatk", "wgs_gatk", default=str(ASAW_BIN / "gatk")),
                    wgs_threads=param_value("wgsThreads", "wgs_threads", default=1),
                    wgs_max_jobs=param_value("wgsMaxJobs", "wgs_max_jobs", default=1),
                    wgs_bcftools=param_value("wgsBcftools", "wgs_bcftools", default=get_wgs_bcftools()),
                    wgs_java=param_value("wgsJava", "wgs_java", default=get_wgs_java()),
                    wgs_beagle_jar=param_value("wgsBeagleJar", "wgs_beagle_jar", default=WGS_BEAGLE_JAR),
                    wgs_memory=param_value("wgsMemory", "wgs_memory", default="8g"),
                    wgs_window=param_value("wgsWindow", "wgs_window", default=40.0),
                    wgs_overlap=param_value("wgsOverlap", "wgs_overlap", default=2.0),
                    wgs_ne=param_value("wgsNe", "wgs_ne", default=100000),
                    wgs_iterations=param_value("wgsIterations", "wgs_iterations", default=12),
                    wgs_phase_states=param_value("wgsPhaseStates", "wgs_phase_states", default=280),
                    wgs_imp_states=param_value("wgsImpStates", "wgs_imp_states", default=1600),
                    wgs_cluster=param_value("wgsCluster", "wgs_cluster", default=0.005),
                    wgs_max_alternate_alleles=param_value("wgsMaxAlternateAlleles", "wgs_max_alternate_alleles", default=6),
                    wgs_calling_threshold=param_value("wgsCallingThreshold", "wgs_calling_threshold", default=10),
                    wgs_include_non_variant_sites=bool_param("wgsIncludeNonVariantSites", "wgs_include_non_variant_sites", default=False),
                    wgs_maf=param_value("wgsMaf", "wgs_maf", default=0.05),
                    wgs_max_missing=param_value("wgsMaxMissing", "wgs_max_missing", default=0.5),
                    wgs_method=param_value("wgsMethod", "wgs_method", default="gatk"),
                    wgs_native_pair_hmm_threads=param_value("wgsNativePairHmmThreads", "wgs_native_pair_hmm_threads", default=1),
                    wgs_coverage_threshold=param_value("wgsCoverageThreshold", "wgs_coverage_threshold", default=1),
                    wgs_platform=param_value("wgsPlatform", "wgs_platform", default="illumina"),
                    rnaseq_sample_parallel_jobs=(extra_params or {}).get("sample_parallel_jobs") or (extra_params or {}).get("sampleParallelJobs") or 0,
                    rnaseq_threads_per_sample=(extra_params or {}).get("threads_per_sample") or (extra_params or {}).get("threadsPerSample") or 0,
                    rnaseq_project_max_cpu=(extra_params or {}).get("rnaseq_project_max_cpu") or (extra_params or {}).get("rnaseqProjectMaxCpu") or 0,
                    rnaseq_resource_decision=(extra_params or {}).get("resource_decision") or (extra_params or {}).get("resourceDecision") or None,
                    rnaseq_qc_tool=param_value("rnaseqQcTool", "qc_tool", default="fastp"),
                    rnaseq_fastp_bin=param_value("rnaseqFastpBin", "fastp_bin", default="tools/fastp"),
                    rnaseq_fastp_detect_adapter_for_pe=bool_param("rnaseqFastpDetectAdapterForPe", "detect_adapter_for_pe", default=True),
                    rnaseq_fastp_cut_front=bool_param("rnaseqFastpCutFront", "cut_front", default=True),
                    rnaseq_fastp_cut_tail=bool_param("rnaseqFastpCutTail", "cut_tail", default=True),
                    rnaseq_fastp_cut_window_size=param_value("rnaseqFastpCutWindowSize", "cut_window_size", default=4),
                    rnaseq_fastp_cut_mean_quality=param_value("rnaseqFastpCutMeanQuality", "cut_mean_quality", default=20),
                    rnaseq_fastp_qualified_quality_phred=param_value("rnaseqFastpQualifiedQualityPhred", "qualified_quality_phred", default=20),
                    rnaseq_fastp_unqualified_percent_limit=param_value("rnaseqFastpUnqualifiedPercentLimit", "unqualified_percent_limit", default=30),
                    rnaseq_fastp_n_base_limit=param_value("rnaseqFastpNBaseLimit", "n_base_limit", default=5),
                    rnaseq_fastp_length_required=param_value("rnaseqFastpLengthRequired", "length_required", default=30),
                    rnaseq_splice_site_source=param_value("rnaseqSpliceSiteSource", "splice_site_source", default="auto"),
                    rnaseq_known_splice_site=param_value("rnaseqKnownSpliceSite", "known_splice_site", default=""),
                    rnaseq_quant_mode=param_value("rnaseqQuantMode", "quant_mode", default="dual"),
                    rnaseq_abundance_engine=param_value("rnaseqAbundanceEngine", "abundance_engine", default="auto"),
                    rnaseq_transcriptome_fasta=param_value("rnaseqTranscriptomeFasta", "transcriptome_fasta", default=""),
                    rnaseq_tx2gene=param_value("rnaseqTx2gene", "tx2gene", default=""),
                    rnaseq_salmon_libtype=param_value("rnaseqSalmonLibtype", "salmon_libtype", default="A"),
                    rnaseq_tximport_counts_from_abundance=param_value("rnaseqTximportCountsFromAbundance", "tximport_counts_from_abundance", default="no"),
                    rnaseq_kallisto_bias=bool((extra_params or {}).get("rnaseqKallistoBias") or (extra_params or {}).get("kallisto_bias")),
                    rnaseq_de_padj=param_value("rnaseqDePadj", "padj", default=0.05),
                    rnaseq_de_log2fc=param_value("rnaseqDeLog2fc", "log2fc", default=1.0),
                    enrich_pvalue_cutoff=param_value("enrichPvalueCutoff", "pvalue_cutoff", default=0.05),
                    enrich_qvalue_cutoff=param_value("enrichQvalueCutoff", "qvalue_cutoff", default=0.05),
                    go_obo=param_value("goObo", "go_obo", default="data/go/go-basic.obo"),
                    kegg_level=param_value("keggLevel", "kegg_level", default="pathway"),
                    kegg_filter_scope=param_value("keggFilterScope", "kegg_filter_scope", default="plant"),
                    kegg_highlight_mode=param_value("keggHighlightMode", "kegg_highlight_mode", default="auto"),
                    kegg_no_network=bool((extra_params or {}).get("keggNoNetwork") or (extra_params or {}).get("no_kegg_network")),
                    kegg_skip_pathview=bool((extra_params or {}).get("keggSkipPathview") or (extra_params or {}).get("skip_pathview")),
                    enrich_plot_type=(extra_params or {}).get("enrichPlotType") or (extra_params or {}).get("plot_type") or "r_plot",
                    rnaseq_count_matrix=(extra_params or {}).get("rnaseqCountMatrix", ""),
                    gwas_vcf=param_value("gwasVcf", "gwas_vcf", default=""),
                    tassel_pipeline=param_value("tasselPipeline", "tassel_pipeline", default="run_pipeline.pl"),
                    tassel_xmx=param_value("tasselXmx", "tassel_xmx", default="10g"),
                    gwas_unsupported_marker_policy=param_value(
                        "gwasUnsupportedMarkerPolicy", "unsupported_marker_policy", default="mask"
                    ),
                    genotype_scope_id=param_value(
                        "genotypeScopeId", "genotype_scope_id", default=""
                    ),
                    phenotype_file=param_value(
                        "phenotypeFile", "phenotype_file", default=""
                    ),
                    phenotype_sample_column=param_value(
                        "phenotypeSampleColumn", "phenotype_sample_column", "sample_column", default=""
                    ),
                    phenotype_trait_column=param_value(
                        "phenotypeTraitColumn", "phenotype_trait_column", "trait_column", default=""
                    ),
                    phenotype_trait_name=param_value(
                        "phenotypeTraitName", "phenotype_trait_name", "trait_name", default=""
                    ),
                    gwas_q_file=param_value(
                        "gwasQFile", "gwas_q_file", default=""
                    ),
                    gwas_fam_file=param_value(
                        "gwasFamFile", "gwas_fam_file", default=""
                    ),
                    gwas_q_k=param_value(
                        "gwasQK", "gwas_q_k", default=2
                    ),
                    gwas_sorted_vcf=param_value(
                        "gwasSortedVcf", "gwas_sorted_vcf", default=""
                    ),
                    gwas_kinship_file=param_value(
                        "gwasKinshipFile", "gwas_kinship_file", default=""
                    ),
                    gwas_trait_file=param_value(
                        "gwasTraitFile", "gwas_trait_file", default=""
                    ),
                    gwas_covariate_file=param_value(
                        "gwasCovariateFile", "gwas_covariate_file", default=""
                    ),
                    phenotype_scope_id=param_value(
                        "phenotypeScopeId", "phenotype_scope_id", default=""
                    ),
                    covariate_scope_id=param_value(
                        "covariateScopeId", "covariate_scope_id", default=""
                    ),
                    gwas_mlm_pvalues=param_value(
                        "gwasMlmPvalues", "gwas_mlm_pvalues", default=""
                    ),
                    gwas_cmlm_pvalues=param_value(
                        "gwasCmlmPvalues", "gwas_cmlm_pvalues", default=""
                    ),
                    gwas_mlm_tested_hypotheses=param_value(
                        "gwasMlmTestedHypotheses", "gwas_mlm_tested_hypotheses", default=None
                    ),
                    gwas_cmlm_tested_hypotheses=param_value(
                        "gwasCmlmTestedHypotheses", "gwas_cmlm_tested_hypotheses", default=None
                    ),
                    gwas_rscript=param_value(
                        "gwasRscript", "gwas_rscript",
                        default=str(ASAW_RSCRIPT),
                    ),
                    source_model_scope_id=param_value(
                        "sourceModelScopeId", "source_model_scope_id", default=""
                    ),
                    gwas_plink=param_value(
                        "gwasPlink", "gwas_plink",
                        default=str(ASAW_BIN / "plink"),
                    ),
                    gwas_emmax=param_value(
                        "gwasEmmax", "gwas_emmax",
                        default=GWAS_EMMAX_BIN,
                    ),
                    gwas_requested_pcs=param_value(
                        "gwasRequestedPcs", "gwas_requested_pcs", default=3,
                    ),
                    gwas_emmax_pvalues=param_value(
                        "gwasEmmaxPvalues", "gwas_emmax_pvalues", default="",
                    ),
                    gwas_emmax_manifest=param_value(
                        "gwasEmmaxManifest", "gwas_emmax_manifest", default="",
                    ),
                    gwas_emmax_tested_hypotheses=param_value(
                        "gwasEmmaxTestedHypotheses", "gwas_emmax_tested_hypotheses", default=None,
                    ),
                    population_vcf=param_value(
                        "populationVcf", "population_vcf", default="",
                    ),
                    population_sample_group=param_value(
                        "populationSampleGroup", "population_sample_group", default="",
                    ),
                    population_params={
                        "plink": param_value("popPlink", default=str(ASAW_BIN / "plink")),
                        "tassel": param_value("popTassel", default=TASSEL_PIPELINE),
                        "fasttree": param_value("popFasttree", default=str(ASAW_BIN / "FastTree")),
                        "admixture": param_value("popAdmixture", default=str(ASAW_BIN / "admixture")),
                        "bcftools": param_value("popBcftools", default="scripts/pysam_bcftools.py"),
                        "poplddecay": param_value("popLddecay", default=str(ASAW_BIN / "PopLDdecay")),
                        "plot_one": param_value("popPlotOne", default="scripts/plot_pop_ld_decay.py"),
                        "plot_multi": param_value("popPlotMulti", default="scripts/plot_pop_ld_decay.py"),
                        "rscript": param_value("popRscript", default=str(ASAW_RSCRIPT)),
                        "k_min": param_value("popKMin", default=2),
                        "k_max": param_value("popKMax", default=10),
                        "cv_folds": param_value("popCvFolds", default=5),
                        "random_seed": param_value("popRandomSeed", default=20260723),
                        "gwas_k": param_value("popGwasK", default=0),
                        "chromosome_policy": param_value(
                            "popChromosomePolicy", default="all_vcf_contigs",
                        ),
                        "chromosomes": param_value("popChromosomes", default=""),
                    },
                    rnaseq_expression_matrix=(extra_params or {}).get("rnaseqExpressionMatrix", ""),
                    rnaseq_sample_table=(extra_params or {}).get("rnaseqSampleTable", ""),
                    fgsea_comparison=(extra_params or {}).get("fgseaComparison", ""),
                    fgsea_min_size=(extra_params or {}).get("fgseaMinSize", 15),
                    fgsea_max_size=(extra_params or {}).get("fgseaMaxSize", 500),
                    fgsea_nperm=(extra_params or {}).get("fgseaNperm", 10000),
                    fgsea_seed=(extra_params or {}).get("fgseaSeed", 1),
                    wgcna_min_mean=(extra_params or {}).get("wgcnaMinMean", 1.0),
                    wgcna_max_genes=(extra_params or {}).get("wgcnaMaxGenes", 8000),
                    wgcna_min_module_size=(extra_params or {}).get("wgcnaMinModuleSize", 30),
                    wgcna_merge_cut_height=(extra_params or {}).get("wgcnaMergeCutHeight", 0.25),
                    wgcna_powers=(extra_params or {}).get("wgcnaPowers", "1,2,3,4,5,6,7,8,9,10,12,14,16,18,20"),
                    ppi_species=(extra_params or {}).get("ppiSpecies") or (extra_params or {}).get("ppi_species") or "auto",
                    normalization_species_label=(extra_params or {}).get("normalizationSpeciesLabel") or (extra_params or {}).get("normalization_species_label") or "",
                    ppi_reference_taxon_id=(extra_params or {}).get("ppiReferenceTaxonId") or (extra_params or {}).get("ppi_reference_taxon_id") or "",
                    ppi_reference_storage_key=(extra_params or {}).get("ppiReferenceStorageKey") or (extra_params or {}).get("ppi_reference_storage_key") or "",
                    ppi_reference_selection_mode=(extra_params or {}).get("ppiReferenceSelectionMode") or (extra_params or {}).get("ppi_reference_selection_mode") or "",
                    ppi_reference_user_confirmed=bool_param("ppiReferenceUserConfirmed", "ppi_reference_user_confirmed", default=False),
                    dependency_owner=(extra_params or {}).get("dependencyOwner") or (extra_params or {}).get("dependency_owner") or "",
                    ortholog_mapping_owner=(extra_params or {}).get("orthologMappingOwner") or (extra_params or {}).get("ortholog_mapping_owner") or "",
                    ppi_score=(extra_params or {}).get("ppiScore", 200),
                    genome_prepare_type=(extra_params or {}).get("genomePrepareType", "auto"),
                    kaks_neutral_rate=(extra_params or {}).get("kaksNeutralRate", 7.08e-9),
                    tfbs_upstream_length=(extra_params or {}).get("tfbsUpstreamLength", 2000),
                    tfbs_motif_db_mode=(extra_params or {}).get("tfbsMotifDbMode", "auto"),
                    tfbs_fimo_pvalue=(extra_params or {}).get("tfbsFimoPvalue", 0.0001),
                    tfbs_fimo_qvalue=(extra_params or {}).get("tfbsFimoQvalue", 0.05),
                    tfbs_background_mode=(extra_params or {}).get("tfbsBackgroundMode", "none"),
                    tfbs_max_nearest_rank=(extra_params or {}).get("tfbsMaxNearestRank", "order"),
                    tfbs_top_motifs=(extra_params or {}).get("tfbsTopMotifs", 30),
                    tfbs_scan_both_strands=bool((extra_params or {}).get("tfbsScanBothStrands", True)),
                    scope_file=(extra_params or {}).get("scopeFile", "") or (extra_params or {}).get("scope_file", ""),
                    scope_id=param_value("scopeId", "scope_id", default=""),
                    rnaseq_scope_id=param_value("rnaseqScopeId", "rnaseq_scope_id", default=""),
                    analysis_route=(extra_params or {}).get("analysisRoute", "") or (extra_params or {}).get("analysis_route", ""),
                    member_set_id=(extra_params or {}).get("memberSetId", "") or (extra_params or {}).get("member_set_id", ""),
                    analysis_mode=(extra_params or {}).get("analysisMode", "") or (extra_params or {}).get("analysis_mode", ""),
                    expected_stages=param_value("expectedStages", "expected_stages", default=[]),
                    stage_statuses=param_value("stageStatuses", "stage_statuses", default={}),
                    report_action=param_value("reportAction", "report_action", default="generate"),
                    report_use_model=bool_param("reportUseModel", "report_use_model", default=True),
                    species_queue=(extra_params or {}).get("speciesQueue", []) or (extra_params or {}).get("species_queue", []),
                    target_species=target_species,
                    interspecies_ref_species=interspecies_ref_species,
                    interspecies_blast_engine=interspecies_blast_engine,
                    interspecies_plot_mode=interspecies_plot_mode,
                    ncbi_query=(extra_params or {}).get("ncbiQuery", ""),
                    ncbi_gene_ids=(extra_params or {}).get("ncbiGeneIds", ""),
                    ncbi_mode=(extra_params or {}).get("ncbiMode", "gene"),
                    ncbi_id_type=(extra_params or {}).get("ncbiIdType", "auto"),
                    ncbi_taxid=(extra_params or {}).get("ncbiTaxid", ""),
                    ncbi_species=(extra_params or {}).get("ncbiSpecies", ""),
                    ncbi_seq_types=(extra_params or {}).get("ncbiSeqTypes", "both"),
                    ncbi_transcript_policy=(extra_params or {}).get("ncbiTranscriptPolicy", "refseq_preferred"),
                    ncbi_allow_family_download=bool((extra_params or {}).get("ncbiAllowFamilyDownload", False)),
                    ncbi_email=(extra_params or {}).get("ncbiEmail", ""),
                    sequence_extract_fasta=(extra_params or {}).get("sequenceExtractFasta", ""),
                    sequence_extract_ids_file=(extra_params or {}).get("sequenceExtractIdsFile", ""),
                    sequence_extract_ids=(extra_params or {}).get("sequenceExtractIds", ""),
                    sequence_extract_seq_type=(extra_params or {}).get("sequenceExtractSeqType", "auto"),
                    sequence_extract_rename_mode=(extra_params or {}).get("sequenceExtractRenameMode", "keep"),
                    sequence_extract_clean_headers=bool((extra_params or {}).get("sequenceExtractCleanHeaders", False)),
                )
                return value
            finally:
                try:
                    os.chdir(original_cwd)
                except FileNotFoundError:
                    os.chdir(self.project_root)
        
        try:
            from concurrent.futures import ThreadPoolExecutor

            stage_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix=f"stage-{stage_tool.id}",
            )
            stage_future = stage_executor.submit(run_stage_with_cwd)
            deadline = loop.time() + stage_tool.timeout
            try:
                # Poll the concurrent future with an event-loop timer so both
                # completion and the stage timeout remain observably bounded.
                while not stage_future.done():
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        stage_future.cancel()
                        raise asyncio.TimeoutError
                    await asyncio.sleep(min(0.1, remaining))
                result_msg, manifest_path = stage_future.result()
            finally:
                stage_executor.shutdown(wait=False, cancel_futures=True)
            
            # 解析 manifest
            manifest = None
            if manifest_path and manifest_path.exists():
                try:
                    with open(manifest_path) as f:
                        manifest = json.load(f)
                except Exception as e:
                    manifest = {"error": str(e), "path": str(manifest_path)}
            
            # 判断成功/失败：run_stage 返回的业务消息优先于旧 manifest。
            # 例如 rnaseq_enrich 缺背景文件时可能返回上游 rnaseq_de manifest；
            # 如果继续用旧 manifest.status=success 判断，会把业务失败误判为成功。
            manifest_stage = str((manifest or {}).get("stage") or "")
            manifest_matches_stage = bool(manifest and manifest_stage == stage_tool.id)
            success = _stage_result_is_success(
                result_msg,
                manifest_stage=manifest_stage,
                expected_stage=stage_tool.id,
                manifest_status=str((manifest or {}).get("status") or ""),
            )
            
            payload = {
                "status": "success" if success else "error",
                "message": result_msg,
                "stage": stage_tool.id,
                "manifest": manifest,
                "manifest_path": str(manifest_path) if manifest_path else None,
                "requested_cpu": requested_cpu,
                "effective_cpu": cpu,
                "timestamp": datetime.now().isoformat()
            }
            if manifest and not manifest_matches_stage:
                payload["manifest_stage_mismatch"] = {
                    "expected": stage_tool.id,
                    "actual": manifest_stage,
                    "manifest_path": str(manifest_path) if manifest_path else None,
                }
            if not success:
                payload["error"] = result_msg
                if (
                    isinstance(manifest, dict)
                    and manifest.get("fallback_method") == "bcftools"
                ):
                    payload["fallback_method"] = manifest["fallback_method"]
            return payload
            
        except asyncio.TimeoutError:
            return {
                "status": "timeout",
                "error": f"Stage {stage_tool.id} timed out after {stage_tool.timeout}s",
                "stage": stage_tool.id
            }
        except Exception as e:
            import traceback
            return {
                "status": "error",
                "error": str(e),
                "traceback": traceback.format_exc(),
                "stage": stage_tool.id
            }


class StageToolRegistry:
    """分析阶段工具注册表"""
    
    STAGE_DEFINITIONS = [
        {
            "id": "pop_pca", "name": "Population PCA", "script": "run_pop_pca.py",
            "category": "population_genomics", "description": "LD-pruned PLINK PCA",
            "required_inputs": ["population_vcf"], "outputs": ["pca_scores", "pca_variance_table", "pca_pc1_pc2_plot"],
            "parameters": {"cpu": 4, "ldR2": 0.2}, "timeout": 7200, "conda_env": "ASAW",
        },
        {
            "id": "pop_phylogeny", "name": "Population Phylogeny", "script": "run_pop_phylogeny.py",
            "category": "population_genomics", "description": "TASSEL PHYLIP and FastTree GTR population tree",
            "required_inputs": ["population_vcf"], "outputs": ["phylogeny_alignment_phylip", "phylogeny_newick", "phylogeny_rectangular_plot", "phylogeny_circular_plot"],
            "parameters": {"cpu": 6, "javaMemory": "5G"}, "timeout": 14400, "conda_env": "ASAW",
        },
        {
            "id": "pop_structure", "name": "Population Structure", "script": "run_pop_structure.py",
            "category": "population_genomics", "description": "LD-pruned ADMIXTURE K sweep with complete CV validation",
            "required_inputs": ["population_vcf"], "outputs": ["cv_table", "best_k_q", "best_k_p", "structure_best_k_plot"],
            "parameters": {"cpu": 6, "kMin": 2, "kMax": 10, "cvFolds": 5}, "timeout": 28800, "conda_env": "ASAW",
        },
        {
            "id": "pop_ld_decay", "name": "Population LD Decay", "script": "run_pop_ld_decay.py",
            "category": "population_genomics", "description": "PopLDdecay for all samples and validated groups",
            "required_inputs": ["population_vcf"], "outputs": ["ld_all_stat", "ld_all_plot_png", "ld_all_plot_pdf"],
            "parameters": {"cpu": 6, "maxDistKb": 500, "maf": 0.05, "missing": 0.2}, "timeout": 28800, "conda_env": "ASAW",
        },
        {
            "id": "pop_snp_density", "name": "SNP Density", "script": "run_pop_snp_density.py",
            "category": "population_genomics", "description": "Physical-window SNP density with verified CMplot outputs",
            "required_inputs": ["population_vcf"], "outputs": ["snp_coordinates", "density_1mb_table", "density_500kb_table", "density_1mb_plot", "density_500kb_plot"],
            "parameters": {"cpu": 4, "chromosomePolicy": "all_vcf_contigs"}, "timeout": 7200, "conda_env": "ASAW",
        },
        {
            "id": "gwas_fmt",
            "name": "GWAS VCF Sort and HapMap Conversion",
            "script": "run_gwas_tassel.py",
            "category": "gwas",
            "description": "Sort an explicitly supplied external VCF and convert it to TASSEL HapMap format",
            "required_inputs": ["external_vcf"],
            "outputs": ["sorted_vcf", "tassel_hapmap"],
            "parameters": {"cpu": 4, "tasselXmx": "10g"},
            "timeout": 7200,
            "conda_env": "test4py311",
        },
        {
            "id": "gwas_kinship",
            "name": "GWAS TASSEL Kinship",
            "script": "run_gwas_kinship.py",
            "category": "gwas",
            "description": "Calculate a centered IBS kinship matrix from the authoritative sorted VCF",
            "required_inputs": ["sorted_vcf"],
            "outputs": ["kinship_matrix"],
            "parameters": {"cpu": 4, "tasselXmx": "10g"},
            "timeout": 7200,
            "conda_env": "test4py311",
        },
        {
            "id": "gwas_trait",
            "name": "GWAS Trait Preparation",
            "script": "run_gwas_trait.py",
            "category": "gwas",
            "description": "Validate and convert one selected phenotype column to TASSEL trait format",
            "required_inputs": ["phenotype_table"],
            "outputs": ["trait_table"],
            "parameters": {"cpu": 4, "missingPolicy": "reject"},
            "timeout": 1800,
            "conda_env": "test4py311",
        },
        {
            "id": "gwas_cov",
            "name": "GWAS Covariate Preparation",
            "script": "run_gwas_covariate.py",
            "category": "gwas",
            "description": "Pair ADMIXTURE Q with PLINK IID order and emit K minus one TASSEL covariates",
            "required_inputs": ["admixture_q", "sample_fam"],
            "outputs": ["covariate_table"],
            "parameters": {"cpu": 4, "famIdColumn": "IID", "omitLastQ": True},
            "timeout": 1800,
            "conda_env": "test4py311",
        },
        {
            "id": "gwas_mlm",
            "name": "GWAS TASSEL MLM/CMLM",
            "script": "run_gwas_tassel_mlm.py",
            "category": "gwas",
            "description": "Run identity-matched TASSEL MLM and CMLM association models",
            "required_inputs": ["sorted_vcf", "kinship_matrix", "trait_table", "covariate_table"],
            "outputs": ["mlm_full", "cmlm_full", "mlm_pvalues", "cmlm_pvalues"],
            "parameters": {"cpu": 6, "tasselXmx": "20g"},
            "timeout": 14400,
            "conda_env": "test4py311",
        },
        {
            "id": "gwas_plot",
            "name": "GWAS MLM Plot",
            "script": "run_gwas_plot.py",
            "category": "gwas",
            "description": "Render four Manhattan and QQ plots from authoritative MLM/CMLM p-values",
            "required_inputs": ["mlm_pvalues", "cmlm_pvalues"],
            "outputs": ["mlm_manhattan", "mlm_qq", "cmlm_manhattan", "cmlm_qq", "manifest", "report", "environment"],
            "parameters": {"cpu": 4},
            "timeout": 3600,
            "conda_env": "test4py311",
        },
        {
            "id": "gwas_emmax",
            "name": "GWAS EMMAX",
            "script": "run_gwas_emmax.py",
            "category": "gwas",
            "description": "Run PLINK-backed EMMAX with preserved sample and marker identity",
            "required_inputs": ["sorted_vcf", "trait_table"],
            "outputs": ["emmax_full", "emmax_pvalues", "manifest", "report", "environment"],
            "parameters": {"cpu": 6, "gwasRequestedPcs": 3},
            "timeout": 14400,
            "conda_env": "test4py311",
        },
        {
            "id": "gwas_emplot",
            "name": "GWAS EMMAX Plot",
            "script": "run_gwas_emmax_plot.py",
            "category": "gwas",
            "description": "Render Manhattan and QQ plots from authoritative EMMAX p-values",
            "required_inputs": ["emmax_pvalues"],
            "outputs": ["emmax_manhattan", "emmax_qq", "manifest", "report", "environment"],
            "parameters": {"cpu": 4},
            "timeout": 3600,
            "conda_env": "test4py311",
        },
        {
            "id": "gwas_glm",
            "name": "GWAS TASSEL GLM",
            "script": "run_gwas_tassel_glm.py",
            "category": "gwas",
            "description": "Run an identity-matched TASSEL fixed-effect GLM",
            "required_inputs": ["sorted_vcf", "trait_table", "covariate_table"],
            "outputs": ["glm_full", "glm_structure_full", "glm_pvalues", "glm_structure_pvalues", "manifest", "report", "environment"],
            "parameters": {"cpu": 6, "tasselXmx": "20g"},
            "timeout": 14400,
            "conda_env": "test4py311",
        },
        # 1. Gene Identification
        {
            "id": "identify",
            "name": "Gene Family Identification",
            "script": "run_identify.py",
            "category": "identification",
            "description": "Identify target-species gene family members using HMM/Pfam plus fixed BLAST evidence where reference family proteins query the target whole-proteome database",
            "required_inputs": ["target whole-proteome protein FASTA for HMM search and BLAST database", "HMM/Pfam or reference family protein FASTA used as BLAST query evidence"],
            "outputs": ["identified_members.pep.fa", "identified_members.id"],
            "parameters": {"cpu": 8, "dom_i_evalue_max": 0.001, "hmm_cov_min": 0.3, "blast_evalue": "1e-05", "blast_identity_min": 30.0, "blast_database_role": "target_proteome", "blast_query_role": "reference_family_pep", "blast_candidate_id_column": "sseqid"},
            "timeout": 7200
        },
        {
            "id": "identify_refine",
            "name": "Refine Identify Members",
            "script": "run_identify_refine.py",
            "category": "identification",
            "description": "Build curated_members.id and curated_members.pep.fa from a curated member ID list",
            "required_inputs": ["id", "protein"],
            "outputs": ["curated_members.pep.fa", "curated_members.id", "identify_refine.manifest.json"],
            "parameters": {"cpu": 4},
            "timeout": 1800
        },
        # 2. Phylogeny
        {
            "id": "phylogeny",
            "name": "Phylogenetic Analysis",
            "script": "run_phylogeny.py",
            "category": "phylogeny",
            "description": "Build a scoped phylogenetic tree using target/reference/comparative family proteins",
            "required_inputs": ["identify final member proteins or scoped family protein FASTA"],
            "outputs": ["tree.nwk", "phylogeny.png"],
            "parameters": {"cpu": 8, "bootstrap": 1000, "model": "MFP"},
            "timeout": 14400
        },
        {
            "id": "reference_classification",
            "name": "Reference-guided Subfamily Classification",
            "script": "run_reference_classification.py",
            "category": "phylogeny",
            "description": "Transfer reference-species subfamily labels to target genes from a joint phylogenetic tree, with confidence evidence",
            "required_inputs": ["tree", "reference_subfamily"],
            "outputs": ["reference_classification.assignment.tsv", "reference_classification.subfamily.tsv", "reference_classification.manifest.json"],
            "parameters": {"cpu": 4, "referencePrefix": "At"},
            "timeout": 1800
        },
        {
            "id": "subfamily_confirm",
            "name": "Confirmed Subfamily Labels",
            "script": "run_subfamily_confirm.py",
            "category": "phylogeny",
            "description": "Freeze user-reviewed subfamily labels for downstream phylogeny, motif, domain, structure and promoter plots",
            "required_inputs": ["reference_classification.subfamily.tsv or user-edited confirmed_subfamily table"],
            "outputs": ["confirmed_subfamily.tsv", "confirmed_subfamily.review.tsv", "subfamily_confirm.manifest.json"],
            "parameters": {"cpu": 4},
            "timeout": 600
        },
        # 3. Motif Analysis
        {
            "id": "motif",
            "name": "Motif Analysis",
            "script": "run_motif.py",
            "category": "motif",
            "description": "Analyze motifs on the scoped protein set used for the current tree",
            "required_inputs": ["scoped protein FASTA from phylogeny or identify"],
            "outputs": ["motif_results"],
            "parameters": {"nmotifs": 10, "min_width": 6, "max_width": 200, "cpu": 8},
            "timeout": 7200
        },
        # 4. Domain Analysis
        {
            "id": "domain",
            "name": "Domain Analysis",
            "script": "run_domain.py",
            "category": "domain",
            "description": "Analyze Pfam domains on the scoped protein set used for the current tree",
            "required_inputs": ["scoped protein FASTA", "Pfam-A.hmm"],
            "outputs": ["domain_results"],
            "parameters": {"cpu": 8, "e_value": 1e-5},
            "timeout": 7200
        },
        # 5. Structure Analysis
        {
            "id": "structure",
            "name": "Structure Analysis",
            "script": "run_structure.py",
            "category": "structure",
            "description": "Draw gene exon/intron/UTR structure for identified family members using target GFF3 files",
            "required_inputs": ["final_members.id", "target GFF3"],
            "outputs": ["structure_results"],
            "parameters": {"cpu": 8},
            "timeout": 7200
        },
        # 6. PEP Analysis (Protein Features)
        {
            "id": "pep",
            "name": "Protein Feature Analysis",
            "script": "run_pep.py",
            "category": "protein",
            "description": "Analyze protein physicochemical properties on the scoped protein set used for the current tree",
            "required_inputs": ["scoped protein FASTA"],
            "outputs": ["pep_properties.tsv", "pep_report.md"],
            "parameters": {"cpu": 8},
            "timeout": 3600
        },
        # 7. Promoter Analysis
        {
            "id": "promoter",
            "name": "Promoter Analysis",
            "script": "run_promoter.py",
            "category": "promoter",
            "description": "Analyze promoter regions for one target species using PlantCARE",
            "required_inputs": ["final_members.id", "single target GFF3", "single target genome FASTA"],
            "outputs": ["promoter_results"],
            "parameters": {"cpu": 8},
            "timeout": 7200
        },
        {
            "id": "tfbs",
            "name": "TF Binding Site Prediction",
            "script": "run_tfbs.py",
            "category": "promoter",
            "description": "Extract target promoters and scan plant TF motif databases with MEME Suite FIMO",
            "required_inputs": ["final_members.id", "single target GFF3", "single target genome FASTA", "plant TF motif database in MEME format"],
            "outputs": ["tfbs.sites.tsv", "tfbs.enrichment.tsv", "tfbs.gene_motif_matrix.tsv", "tfbs.manifest.json"],
            "parameters": {
                "cpu": 4,
                "upstream_length": 2000,
                "motif_db_mode": "auto",
                "fimo_pvalue": 0.0001,
                "fimo_qvalue": 0.05,
                "background_mode": "none",
            },
            "timeout": 14400
        },
        # 7. Synteny Analysis（种内）
        {
            "id": "synteny",
            "name": "Intraspecies synteny (MCScanX)",
            "script": "run_synteny.py",
            "category": "synteny",
            "description": "种内共线性：MCScanX/Circos，单个目标物种内的全蛋白组和染色体区块",
            "required_inputs": ["single target pep", "single target gff", "single target genome"],
            "outputs": ["synteny_results"],
            "parameters": {"cpu": 8},
            "timeout": 7200
        },
        # 8. Karyotype
        {
            "id": "karyotype",
            "name": "Karyotype Analysis",
            "script": "run_karyotype.py",
            "category": "karyotype",
            "description": "Generate per-target-species karyotype visualizations for family members",
            "required_inputs": ["final_members.id", "target GFF3", "target genome FASTA"],
            "outputs": ["karyotype_results"],
            "parameters": {"cpu": 8},
            "timeout": 3600
        },
        # 9. Interspecies Synteny（种间）
        {
            "id": "interspecies_synteny",
            "name": "Interspecies synteny",
            "script": "run_interspecies_synteny.py",
            "category": "comparative",
            "description": "物种间共线性：按 target/reference/comparative scope 进行多物种互比和 JCVI 绘图",
            "required_inputs": ["target family pep", "reference family pep", "target/ref full pep", "target/ref gff"],
            "outputs": ["interspecies_results"],
            "parameters": {"cpu": 8},
            "timeout": 7200
        },
        # 10. Expression Analysis
        {
            "id": "expression",
            "name": "Expression Analysis",
            "script": "run_expression.py",
            "category": "expression",
            "description": "Draw FPKM/TPM expression plots for one selected target species, or record RNA-seq DE-triggered expression plots",
            "required_inputs": ["single target member IDs", "FPKM/TPM matrix or GEO accession", "sample.txt"],
            "outputs": ["expression_heatmaps", "expression_manifest"],
            "parameters": {"cpu": 8},
            "timeout": 3600
        },
        # 11. Ka/Ks Analysis
        {
            "id": "kaks",
            "name": "Ka/Ks Analysis",
            "script": "run_kaks_v2.py",
            "category": "evolution",
            "description": "Calculate Ka/Ks ratios for scoped target-to-reference ortholog pairs using PAML",
            "required_inputs": ["target CDS/PEP", "reference CDS/PEP", "ortholog mapping"],
            "outputs": ["kaks_results"],
            "parameters": {"cpu": 8, "model": "YN00"},
            "timeout": 7200
        },
        # 12. Ortholog Identification
        {
            "id": "ortholog",
            "name": "Ortholog Identification",
            "script": "run_ortholog.py",
            "category": "evolution",
            "description": "Identify orthologous gene-family mappings from selected target species to reference species",
            "required_inputs": ["target family pep", "reference family pep"],
            "outputs": ["ortholog_pairs", "orthologs", "ortholog_blastp", "ortholog_manifest"],
            "parameters": {"cpu": 8, "evalue": 1e-5},
            "timeout": 3600
        },
        # 13. PPI Analysis
        {
            "id": "ppi",
            "name": "PPI Analysis",
            "script": "run_ppi.py",
            "category": "interaction",
            "description": "Protein-protein interaction analysis using STRING (requires ortholog mapping to reference species)",
            "required_inputs": ["target member IDs", "ortholog mapping for multi-target/non-reference IDs"],
            "outputs": ["ppi_network"],
            "parameters": {"species": "ath", "score": 200},
            "timeout": 3600
        },
        # 14. Summary
        {
            "id": "summary",
            "name": "Whole-job Summary",
            "script": "run_summary.py",
            "category": "report",
            "description": "Aggregate all stage manifests, artifacts, metrics and consistency checks by scope",
            "required_inputs": ["stage manifests"],
            "outputs": ["summary_report.md", "summary.files.tsv", "summary.manifest.json"],
            "parameters": {"scope_aware": True},
            "timeout": 1800
        },
        {
            "id": "gene_family_report",
            "name": "Gene Family Result Report",
            "script": "run_gene_family_report.py",
            "category": "report",
            "description": "Generate or open the formal report for exactly the confirmed gene-family stages",
            "required_inputs": [],
            "outputs": [
                "gene_family_report",
                "gene_family_report_evidence",
                "gene_family_report_model",
                "gene_family_report_manifest",
            ],
            "parameters": {"action": "generate", "useModel": True},
            "timeout": 1800
        },
        # 15. Genome Prepare
        {
            "id": "genome_prepare",
            "name": "Genome Preparation",
            "script": "run_genome_prepare.py",
            "category": "preprocessing",
            "description": "Prepare genome data for analysis",
            "required_inputs": ["genome_fasta", "gff"],
            "outputs": ["prepared_genome"],
            "parameters": {"cpu": 8},
            "timeout": 3600
        },
        {
            "id": "sequence_extract_by_id",
            "name": "Utility: Extract/Clean FASTA Sequences",
            "script": "run_sequence_extract_by_id.py",
            "category": "utility",
            "description": "Extract protein/CDS/FASTA records by ID, or clean FASTA headers without an ID list",
            "required_inputs": [],
            "outputs": ["sequence_extract.fa", "sequence_extract.mapping.tsv", "sequence_extract.manifest.json"],
            "parameters": {"cpu": 4},
            "timeout": 1800
        },
        {
            "id": "prepare_organize",
            "name": "Organize Genome Input Files",
            "script": "run_prepare_substep.py",
            "category": "utility",
            "description": "Organize files by config.xlsx, standardize names, merge reference family FASTA and write species_config.json",
            "required_inputs": ["config.xlsx"],
            "outputs": ["species_config.json", "standardized_inputs"],
            "parameters": {"cpu": 4},
            "timeout": 1800
        },
        {
            "id": "prepare_normalize_gff",
            "name": "Normalize GFF IDs",
            "script": "run_prepare_substep.py",
            "category": "utility",
            "description": "Normalize JGI/Ensembl/NCBI GFF ID prefixes and attributes",
            "required_inputs": ["gff"],
            "outputs": ["normalized.gff3"],
            "parameters": {"cpu": 4},
            "timeout": 1800
        },
        {
            "id": "prepare_longest_transcript",
            "name": "Extract Longest Transcript",
            "script": "run_prepare_substep.py",
            "category": "utility",
            "description": "Select longest transcript/CDS per gene and write filtered GFF plus ID lists",
            "required_inputs": ["gff"],
            "outputs": ["longest.gff3", "longest.ids"],
            "parameters": {"cpu": 4},
            "timeout": 3600
        },
        {
            "id": "prepare_extract_pep",
            "name": "Extract PEP from Genome and GFF",
            "script": "run_prepare_substep.py",
            "category": "utility",
            "description": "Use gffread to extract peptide sequences from genome FASTA and GFF",
            "required_inputs": ["genome", "gff"],
            "outputs": ["pep.fasta"],
            "parameters": {"cpu": 4},
            "timeout": 3600
        },
        {
            "id": "prepare_extract_cds",
            "name": "Extract CDS from Genome and GFF",
            "script": "run_prepare_substep.py",
            "category": "utility",
            "description": "Use gffread to extract CDS sequences from genome FASTA and GFF",
            "required_inputs": ["genome", "gff"],
            "outputs": ["cds.fasta"],
            "parameters": {"cpu": 4},
            "timeout": 3600
        },
        {
            "id": "prepare_filter_fasta",
            "name": "Filter FASTA by ID List",
            "script": "run_prepare_substep.py",
            "category": "utility",
            "description": "Use seqtk subseq to extract FASTA records using generated or uploaded ID lists",
            "required_inputs": ["fasta", "id_list"],
            "outputs": ["subset.fasta"],
            "parameters": {"cpu": 4},
            "timeout": 1800
        },
        {
            "id": "prepare_simplify_ncbi",
            "name": "Simplify NCBI IDs",
            "script": "run_prepare_substep.py",
            "category": "utility",
            "description": "Normalize NCBI CDS headers and simplify NCBI GFF attributes",
            "required_inputs": ["gff", "cds"],
            "outputs": ["ncbi_simplified.gff3", "ncbi_normalized.cds.fasta"],
            "parameters": {"cpu": 4},
            "timeout": 1800
        },
        # 13b. NCBI sequence fetch
        {
            "id": "ncbi_fetch_sequences",
            "name": "NCBI Sequence Fetch",
            "script": "run_ncbi_fetch_sequences.py",
            "category": "utility",
            "description": "Fetch protein and CDS FASTA from NCBI Gene IDs or taxon-scoped gene/family queries",
            "required_inputs": ["gene_ids_or_query"],
            "outputs": ["protein_fasta", "cds_fasta", "mapping", "unresolved", "ambiguous"],
            "parameters": {"cpu": 4},
            "timeout": 1800,
            "conda_env": "test4py311"
        },
        # 14. RNA-seq QC
        {"id": "wgs_qc", "name": "WGS QC", "script": "run_wgs_qc.py", "category": "wgs", "description": "Paired-end WGS quality control with fastp", "required_inputs": ["wgs_sample_config"], "outputs": ["clean_reads_r1", "clean_reads_r2", "fastp_html", "fastp_json"], "parameters": {"cpu": 4}, "timeout": 7200, "conda_env": "test4py311"},
        {
            "id": "wgs_index",
            "name": "WGS Reference Index",
            "script": "run_wgs_index.py",
            "category": "wgs",
            "description": "Build samtools, GATK and BWA-MEM2 reference indices",
            "required_inputs": ["reference_fasta"],
            "outputs": ["reference_fasta", "reference_fai", "reference_dict", "bwa_mem2_index_component"],
            "parameters": {"cpu": 4},
            "timeout": 7200,
            "conda_env": "test4py311",
        },
        {
            "id": "wgs_align",
            "name": "WGS Alignment",
            "script": "run_wgs_align.py",
            "category": "wgs",
            "description": "Manifest-driven BWA-MEM2 alignment and coordinate sorting",
            "required_inputs": ["wgs_qc_manifest", "wgs_index_manifest"],
            "outputs": ["sorted_bam", "sorted_bam_index", "align_log"],
            "parameters": {"cpu": 6},
            "timeout": 86400,
            "conda_env": "test4py311",
        },
        {
            "id": "wgs_markdup",
            "name": "WGS Mark Duplicates",
            "script": "run_wgs_markdup.py",
            "category": "wgs",
            "description": "Manifest-driven GATK duplicate removal",
            "required_inputs": ["wgs_align_manifest"],
            "outputs": ["markdup_bam", "markdup_bam_index", "markdup_metrics", "markdup_log"],
            "parameters": {"cpu": 4},
            "timeout": 86400,
            "conda_env": "test4py311",
        },
        {
            "id": "wgs_bam_qc",
            "name": "WGS BAM QC",
            "script": "run_wgs_bam_qc.py",
            "category": "wgs",
            "description": "Manifest-driven samtools flagstat and reference-denominator coverage",
            "required_inputs": ["wgs_markdup_manifest", "wgs_index_manifest"],
            "outputs": ["bam_flagstat", "bam_depth_summary", "bam_qc_summary", "bam_qc_report"],
            "parameters": {"cpu": 4},
            "timeout": 86400,
            "conda_env": "rnaseq",
        },
        {
            "id": "wgs_haplotype_calling",
            "name": "WGS Haplotype Calling",
            "script": "run_wgs_haplotype_calling.py",
            "category": "wgs",
            "description": "Manifest-driven per-sample GATK GVCF calling",
            "required_inputs": [
                "wgs_markdup_manifest",
                "wgs_bam_qc_manifest",
                "wgs_index_manifest",
            ],
            "outputs": ["gvcf", "gvcf_index", "haplotype_log"],
            "parameters": {"cpu": 4},
            "timeout": 172800,
            "conda_env": "ASAW",
        },
        {
            "id": "wgs_gvcf_combine",
            "name": "WGS GVCF Combine",
            "script": "run_wgs_gvcf_combine.py",
            "category": "wgs",
            "description": "Manifest-driven GATK cohort GVCF combination",
            "required_inputs": [
                "wgs_haplotype_manifest",
                "wgs_index_manifest",
            ],
            "outputs": [
                "combined_gvcf",
                "combined_gvcf_index",
                "combine_log",
            ],
            "parameters": {"cpu": 4},
            "timeout": 172800,
            "conda_env": "ASAW",
        },
        {
            "id": "wgs_joint_genotyping",
            "name": "WGS Joint Genotyping",
            "script": "run_wgs_joint_genotyping.py",
            "category": "wgs",
            "description": "Manifest-driven GATK cohort joint genotyping",
            "required_inputs": [
                "wgs_combine_manifest",
                "wgs_index_manifest",
            ],
            "outputs": [
                "joint_vcf",
                "joint_vcf_index",
                "joint_summary",
                "joint_log",
            ],
            "parameters": {"cpu": 4},
            "timeout": 172800,
            "conda_env": "ASAW",
        },
        {
            "id": "wgs_variant_select",
            "name": "WGS Variant Select",
            "script": "run_wgs_variant_select.py",
            "category": "wgs",
            "description": "Manifest-driven SNP and INDEL selection",
            "required_inputs": ["wgs_joint_manifest"],
            "outputs": [
                "selected_snp_vcf",
                "selected_snp_vcf_index",
                "selected_indel_vcf",
                "selected_indel_vcf_index",
                "variant_select_report",
            ],
            "parameters": {"cpu": 2, "method": "gatk"},
            "timeout": 172800,
            "conda_env": "ASAW",
        },
        {
            "id": "wgs_variant_filter",
            "name": "WGS Variant Filter",
            "script": "run_wgs_variant_filter.py",
            "category": "wgs",
            "description": "Manifest-driven SNP and INDEL hard filtering",
            "required_inputs": ["wgs_select_manifest"],
            "outputs": [
                "filtered_snp_vcf",
                "filtered_snp_vcf_index",
                "pass_snp_vcf",
                "pass_snp_vcf_index",
                "filtered_indel_vcf",
                "filtered_indel_vcf_index",
                "pass_indel_vcf",
                "pass_indel_vcf_index",
                "variant_filter_report",
            ],
            "parameters": {"cpu": 2, "method": "gatk"},
            "timeout": 172800,
            "conda_env": "ASAW",
        },
        {
            "id": "wgs_impute",
            "name": "WGS Imputation",
            "script": "run_wgs_impute.py",
            "category": "wgs",
            "description": "Manifest-driven Beagle SNP imputation",
            "required_inputs": ["wgs_filter_manifest"],
            "outputs": [
                "imputed_snp_vcf",
                "imputed_snp_vcf_index",
                "impute_report",
                "impute_log",
            ],
            "parameters": {"cpu": 2},
            "timeout": 172800,
            "conda_env": "test4py311",
        },
        {
            "id": "wgs_maf_missing_filter",
            "name": "WGS MAF/Missing Filter",
            "script": "run_wgs_maf_missing_filter.py",
            "category": "wgs",
            "description": "Manifest-driven final SNP MAF and missing filter",
            "required_inputs": ["wgs_impute_manifest"],
            "outputs": [
                "final_snp_vcf",
                "final_snp_vcf_index",
                "maf_missing_report",
                "maf_missing_stats",
            ],
            "parameters": {"cpu": 2},
            "timeout": 172800,
            "conda_env": "ASAW",
        },
        {
            "id": "rnaseq_qc",
            "name": "RNA-seq QC",
            "script": "run_rnaseq_qc.py",
            "category": "rnaseq",
            "description": "RNA-seq quality control",
            "required_inputs": ["fastq"],
            "outputs": ["qc_report"],
            "parameters": {"cpu": 8},
            "timeout": 7200,
            "conda_env": "rnaseq"
        },
        # 15. RNA-seq Align
        {
            "id": "rnaseq_align",
            "name": "RNA-seq Alignment",
            "script": "run_rnaseq_align.py",
            "category": "rnaseq",
            "description": "Align RNA-seq reads to genome",
            "required_inputs": ["fastq", "genome_index"],
            "outputs": ["bam_files"],
            "parameters": {"cpu": 8},
            "timeout": 14400,
            "conda_env": "rnaseq"
        },
        # 16. RNA-seq Quant
        {
            "id": "rnaseq_quant",
            "name": "RNA-seq Quantification",
            "script": "run_rnaseq_quant.py",
            "category": "rnaseq",
            "description": "Quantify gene expression from RNA-seq",
            "required_inputs": ["bam", "gtf"],
            "outputs": ["expression_matrix"],
            "parameters": {"cpu": 8},
            "timeout": 7200,
            "conda_env": "rnaseq"
        },
        # 17. RNA-seq DE
        {
            "id": "rnaseq_de",
            "name": "Differential Expression",
            "script": "run_rnaseq_de.py",
            "category": "rnaseq",
            "description": "Differential expression analysis using DESeq2",
            "required_inputs": ["counts", "sample_info"],
            "outputs": ["de_results"],
            "parameters": {"cpu": 8},
            "timeout": 3600,
            "conda_env": "rnaseq"
        },
        # 17b. RNA-seq Sample Quality Assessment
        {
            "id": "rnaseq_sample_qc",
            "name": "RNA-seq Sample Quality Assessment",
            "script": "run_rnaseq_sample_qc.py",
            "category": "rnaseq",
            "description": "DESeq2 VST, PCA, sample correlation, sample distance and hierarchical clustering",
            "required_inputs": ["count_matrix"],
            "outputs": ["pca", "sample_correlation", "sample_clustering"],
            "parameters": {"cpu": 4},
            "timeout": 3600,
            "conda_env": "rnaseq"
        },
        # 17c. RNA-seq WGCNA
        {
            "id": "rnaseq_wgcna",
            "name": "RNA-seq WGCNA",
            "script": "run_rnaseq_wgcna.py",
            "category": "rnaseq",
            "description": "Weighted gene co-expression network analysis from VST/log expression or raw counts",
            "required_inputs": ["expression_matrix_or_count_matrix"],
            "outputs": ["gene_modules", "module_trait_correlation"],
            "parameters": {"max_genes": 8000, "min_module_size": 30, "merge_cut_height": 0.25},
            "timeout": 7200,
            "conda_env": "rnaseq"
        },
        # 18. Background Annotation
        {
            "id": "annotation",
            "name": "Background Annotation",
            "script": "run_annotation.py",
            "category": "annotation",
            "description": "Generate GO/KO background annotations from protein sequences with local InterProScan and KofamScan",
            "required_inputs": ["protein"],
            "outputs": ["gene2go.txt", "gene2ko.txt", "interproscan.tsv", "kofamscan.detail.tsv", "annotation.manifest.json", "annotation_go.manifest.json", "annotation_kegg.manifest.json"],
            "parameters": {"cpu": 8},
            "timeout": 43200,
            "conda_env": "test4py311"
        },
        # 19. RNA-seq GO Enrich
        {
            "id": "rnaseq_go_enrich",
            "name": "RNA-seq GO Enrichment",
            "script": "run_rnaseq_enrich.py",
            "category": "rnaseq",
            "description": "GO enrichment analysis for RNA-seq DE genes",
            "required_inputs": ["de_results"],
            "outputs": ["GO_enrich"],
            "parameters": {"cpu": 8, "top_pathways": 20, "enrichType": "go"},
            "timeout": 43200,
            "conda_env": "rnaseq"
        },
        # 20. RNA-seq KEGG Enrich
        {
            "id": "rnaseq_kegg_enrich",
            "name": "RNA-seq KEGG Enrichment",
            "script": "run_rnaseq_enrich.py",
            "category": "rnaseq",
            "description": "KEGG enrichment analysis for RNA-seq DE genes",
            "required_inputs": ["de_results"],
            "outputs": ["KEGG_enrich"],
            "parameters": {"cpu": 8, "top_pathways": 20, "enrichType": "kegg"},
            "timeout": 43200,
            "conda_env": "rnaseq"
        },
        # 21. RNA-seq Enrich
        {
            "id": "rnaseq_enrich",
            "name": "Enrichment Analysis",
            "script": "run_rnaseq_enrich.py",
            "category": "rnaseq",
            "description": "GO and KEGG enrichment analysis",
            "required_inputs": ["de_results"],
            "outputs": ["enrichment_results"],
            "parameters": {"cpu": 8, "top_pathways": 20},
            "timeout": 43200,
            "conda_env": "rnaseq"
        },
        # 22. RNA-seq fgsea
        {
            "id": "rnaseq_fgsea",
            "name": "RNA-seq fgsea",
            "script": "run_rnaseq_fgsea.py",
            "category": "rnaseq",
            "description": "Plant-compatible KEGG fgsea from DESeq2 ranks and local gene2ko annotations",
            "required_inputs": ["de_results", "gene2ko"],
            "outputs": ["fgsea_kegg"],
            "parameters": {"min_size": 15, "max_size": 500, "nperm": 10000},
            "timeout": 3600,
            "conda_env": "rnaseq"
        },
        # 23. RNA-seq Candidate Family
        {
            "id": "rnaseq_candidate_family",
            "name": "RNA-seq Candidate Family",
            "script": "run_rnaseq_candidate_family.py",
            "category": "rnaseq",
            "description": "Rank candidate gene families by integrating DE genes and functional annotations",
            "required_inputs": ["de_results"],
            "outputs": ["candidate_family_ranking", "gene_annotations", "enrichment_context", "family_enrichment", "evidence_matrix", "recommendation", "key_genes", "experiment_context", "rag_queries", "rag_references"],
            "parameters": {"cpu": 4},
            "timeout": 7200,
            "conda_env": "rnaseq"
        },
        # 24. Formal RNA-seq Result Report
        {
            "id": "rnaseq_report",
            "name": "RNA-seq Result Report",
            "script": "run_rnaseq_report.py",
            "category": "rnaseq",
            "description": "Generate a scoped formal Markdown report from successful RNA-seq manifests",
            "required_inputs": [],
            "outputs": ["rnaseq_report", "rnaseq_report_evidence", "rnaseq_report_model"],
            "parameters": {"cpu": 4},
            "timeout": 1800,
            "conda_env": "test4py311"
        },
        # Combo Plot - 组合图
        {
            "id": "combo",
            "name": "Combo Plot",
            "script": "run_combo.py",
            "category": "visualization",
            "description": "Combine phylogeny, motif, structure and domain into one figure",
            "required_inputs": ["tree", "motif", "structure", "domain"],
            "outputs": ["combo_plot.pdf", "combo_plot.png"],
            "parameters": {"cpu": 4},
            "timeout": 3600
        }
    ]
    
    def __init__(self):
        self._stages: Dict[str, StageTool] = {}
        self._executor = StageToolExecutor()
        self._register_all_stages()
    
    def _register_all_stages(self):
        """注册所有分析阶段"""
        for defn in self.STAGE_DEFINITIONS:
            self.register(StageTool(**defn))
    
    def register(self, stage: StageTool) -> None:
        """注册分析阶段"""
        self._stages[stage.id] = stage
    
    def get(self, stage_id: str) -> Optional[StageTool]:
        """获取分析阶段定义"""
        return self._stages.get(stage_id)
    
    def list_all(self) -> List[StageTool]:
        """列出所有分析阶段"""
        return list(self._stages.values())
    
    def list_by_category(self, category: str) -> List[StageTool]:
        """按类别列出分析阶段"""
        return [s for s in self._stages.values() if s.category == category]
    
    async def execute(self, stage_id: str, job_id: str, prefix: str,
                extra_params: dict = None, input_files: dict = None) -> dict:
        """执行分析阶段"""
        stage = self.get(stage_id)
        if stage is None:
            return {
                "status": "error",
                "error": f"Stage '{stage_id}' not found",
                "available_stages": list(self._stages.keys())
            }
        return await self._executor.execute(stage, job_id, prefix, extra_params, input_files)
    
    def get_execution_order(self, start_stage: str = "identify") -> List[str]:
        """获取标准分析流程的执行顺序"""
        standard_flow = [
            "identify",
            "phylogeny",
            "motif",
            "domain",
            "structure",
            "promoter",
            "tfbs",
            "synteny",
            "karyotype",
            "interspecies_synteny",
            "expression"
        ]
        if start_stage not in standard_flow:
            return standard_flow
        idx = standard_flow.index(start_stage)
        return standard_flow[idx:]


# 全局注册表实例
stage_registry = StageToolRegistry()


def create_stage_registry() -> StageToolRegistry:
    """创建新的StageToolRegistry实例"""
    return StageToolRegistry()
