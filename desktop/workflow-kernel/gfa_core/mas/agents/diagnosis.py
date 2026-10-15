"""
DiagnosisAgent V3 - 错误诊断与自动修复
集成Knowledge查询、自动修复、前端通知和全局迭代计数
"""
import re
import json
import shutil
import shlex
import gzip
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from .base import AgentMessage, AgentState, BaseAgent
from gfa_core.mas.llm import LLMManager
from gfa_core.mas.stage_catalog import STAGE_DEPENDENCIES as CATALOG_STAGE_DEPENDENCIES
from gfa_core.project_paths import PROJECT_ROOT
from gfa_core.ppi_provenance import ppi_diagnosis_code


STAGE_SCRIPT_MAP = {
    "pop_pca": "run_pop_pca.py",
    "pop_phylogeny": "run_pop_phylogeny.py",
    "pop_structure": "run_pop_structure.py",
    "pop_ld_decay": "run_pop_ld_decay.py",
    "pop_snp_density": "run_pop_snp_density.py",
    "gwas_fmt": "run_gwas_tassel.py",
    "identify": "run_identify.py",
    "phylogeny": "run_phylogeny.py",
    "motif": "run_motif.py",
    "domain": "run_domain.py",
    "structure": "run_structure.py",
    "promoter": "run_promoter.py",
    "synteny": "run_synteny.py",
    "interspecies_synteny": "run_interspecies_synteny.py",
    "karyotype": "run_karyotype.py",
    "combo": "run_combo.py",
    "ppi": "run_ppi.py",
    "expression": "run_expression.py",
    "pep": "run_pep.py",
    "rnaseq_qc": "run_rnaseq_qc.py",
    "rnaseq_align": "run_rnaseq_align.py",
    "rnaseq_quant": "run_rnaseq_quant.py",
    "rnaseq_de": "run_rnaseq_de.py",
    "rnaseq_enrich": "run_rnaseq_enrich_v2.py",
    "rnaseq_go_enrich": "run_rnaseq_enrich.py",
    "rnaseq_kegg_enrich": "run_rnaseq_enrich.py",
    "rnaseq_fgsea": "run_rnaseq_fgsea.py",
    "annotation": "run_annotation.py",
    "annotation_go": "run_annotation.py",
    "annotation_kegg": "run_annotation.py",
    "rnaseq_wgcna": "run_rnaseq_wgcna.py",
    "rnaseq_candidate_family": "run_rnaseq_candidate_family.py",
    "rnaseq_report": "run_rnaseq_report.py",
    "rnaseq_sample_qc": "run_rnaseq_sample_qc.py",
    "genome_prepare": "run_genome_prepare.py",
    "genome_prepare_v2": "run_genome_prepare.py",
    "ortholog": "run_ortholog.py",
    "ncbi_fetch_sequences": "run_ncbi_fetch_sequences.py",
    "sequence_extract_by_id": "run_sequence_extract_by_id.py",
}
RAP_TRANSCRIPT_RE = re.compile(r"^(Os\d{2})t(\d+)(?:-\d+)?$", re.IGNORECASE)
KNOWN_COPY_PATCH_CODES = {
    "python_import_path_error",
    "protein_fasta_stop_markers",
    "r_table_comment_char",
    "success_with_zero_alignment_rate",
    "success_with_zero_qc_reads",
    "fgsea_rank_gene2ko_id_mismatch",
    "wgcna_matrix_comment_char",
    "quant_fpkm_tpm_comment_char",
}
RUNTIME_REPAIR_CODES = {
    "protein_fasta_stop_markers",
    "identify_pfam_remote_404",
    "identify_pfam_partial_fetch",
    "identify_hmm_model_mismatch",
    "pfam_remote_404",
    "identify_missing_hmm_or_pfam",
    "ncbi_family_reference_link_missing",
    "manifest_missing",
    "manifest_missing_stage_incomplete",
    "ortholog_intermediate_not_materialized",
    "genome_prepare_final_gff_empty",
    "genome_prepare_pep_looks_like_genome",
    "genome_prepare_cds_looks_like_genome",
    "gene_gff_id_overlap_low",
}
REPAIR_POLICY_BY_CODE = {
    "gwas_fmt_external_vcf_missing": ("report_only", "provide_external_vcf"),
    "gwas_fmt_tassel_missing": ("report_only", "configure_tassel_pipeline"),
    "gwas_fmt_manifest_failed": ("report_only", "inspect_gwas_fmt_manifest_error"),
    "protein_fasta_stop_markers": ("auto", "protein_stop_marker_clean"),
    "genome_prepare_final_gff_empty": ("auto", "restore_final_gff_from_source"),
    "genome_prepare_pep_looks_like_genome": ("auto", "extract_pep_from_genome_gff"),
    "genome_prepare_cds_looks_like_genome": ("auto", "extract_cds_from_genome_gff"),
    "gene_gff_id_overlap_low": ("report_only", "write_id_overlap_report"),
    "identify_pfam_remote_404": ("auto", "prepare_local_pfam_hmm"),
    "identify_pfam_partial_fetch": ("auto", "prepare_local_pfam_hmm"),
    "identify_hmm_model_mismatch": ("auto", "prepare_local_pfam_hmm"),
    "pfam_remote_404": ("auto", "prepare_local_pfam_hmm"),
    "ncbi_family_reference_link_missing": ("auto", "create_family_reference_pep_link"),
    "hmm_or_pfam_missing": ("confirm", "provide_hmm_or_required_pfams"),
    "identify_missing_hmm_or_pfam": ("auto", "prepare_local_pfam_hmm"),
    "manifest_missing_stage_incomplete": ("auto", "recover_manifest_from_final_artifacts"),
    "manifest_missing": ("auto", "recover_manifest_from_final_artifacts"),
    "ortholog_intermediate_not_materialized": ("auto", "materialize_ortholog_from_intermediate"),
    "manifest_failed": ("report_only", "inspect_stage_log"),
    "stage_runtime_same_file_copy": ("dev_fix", "guard_same_file_copy"),
    "expression_missing_gene_ids": ("report_only", "provide_gene_family_member_ids"),
    "expression_missing_sample_txt": ("report_only", "provide_or_convert_sample_table"),
    "rnaseq_auto_expression_missing": ("report_only", "rerun_rnaseq_de_or_expression_rnaseq_auto"),
    "rnaseq_auto_expression_failed": ("report_only", "inspect_expression_log"),
    "rnaseq_context_missing": ("dev_fix", "align_rnaseq_manifest_context"),
    "stage_identity_contract_mismatch": ("dev_fix", "align_manifest_identity_with_scope_contract"),
    "combo_artifact_scope_mismatch": ("dev_fix", "align_combo_output_naming_with_scope_contract"),
    "domain_scoped_identify_guard": ("report_only", "align_domain_backend_guard_with_scope_contract"),
    "upstream_genome_prepare_partial_success": ("report_only", "repair_or_exclude_failed_species"),
    "plantcare_id_mismatch": ("report_only", "rerun_plantcare_for_current_promoters"),
    "multi_target_single_species_stage": ("report_only", "split_target_scope_per_species"),
    "kaks_scope_missing_reference": ("dev_fix", "plan_kaks_with_target_plus_reference_scope"),
    "interspecies_pair_no_hits": ("report_only", "inspect_proteome_or_relax_similarity_threshold"),
    "go_foreground_background_id_mismatch": ("confirm", "use_auto_annotation_background"),
    "id_fasta_mismatch": ("report_only", "convert_gene_or_transcript_ids_before_sequence_extraction"),
    "blast_binding_or_output_failure": ("report_only", "check_extension_stage_query_reference_binding"),
    "misplaced_outputs": ("dev_fix", "fix_extension_wrapper_output_directory"),
    "hmmer_no_hits_or_threshold": ("report_only", "inspect_hmmer_hits_and_thresholds"),
    "hmmer_postprocess_empty": ("dev_fix", "fix_hmmer_postprocess_parser"),
}
ISSUE_LAYER_BY_CODE = {
    "gwas_fmt_external_vcf_missing": "input",
    "gwas_fmt_tassel_missing": "tool_runtime",
    "gwas_fmt_manifest_failed": "artifact_status",
    "multi_target_single_species_stage": "planning",
    "kaks_scope_missing_reference": "planning",
    "hmm_or_pfam_missing": "input",
    "identify_missing_hmm_or_pfam": "input",
    "identify_pfam_partial_fetch": "tool_runtime",
    "identify_hmm_model_mismatch": "tool_runtime",
    "protein_fasta_stop_markers": "input_repair",
    "missing_final_member_protein": "artifact_status",
    "manifest_missing": "artifact_status",
    "manifest_missing_stage_incomplete": "artifact_status",
    "ortholog_intermediate_not_materialized": "artifact_status",
    "manifest_failed": "artifact_status",
    "stage_runtime_same_file_copy": "execution",
    "expression_missing_gene_ids": "input",
    "expression_missing_sample_txt": "input",
    "rnaseq_auto_expression_missing": "execution",
    "rnaseq_auto_expression_failed": "execution",
    "rnaseq_context_missing": "artifact_status",
    "stage_identity_contract_mismatch": "artifact_status",
    "combo_artifact_scope_mismatch": "artifact_status",
    "domain_scoped_identify_guard": "execution",
    "success_with_empty_metrics": "artifact_status",
    "success_with_zero_alignment_rate": "artifact_status",
    "success_with_zero_qc_reads": "artifact_status",
    "tool_version_format": "tool_runtime",
    "pfam_remote_404": "tool_runtime",
    "identify_pfam_remote_404": "tool_runtime",
    "interspecies_pair_no_hits": "tool_runtime",
    "id_mismatch": "input_contract",
    "plantcare_id_mismatch": "input_contract",
    "gene_gff_id_overlap_low": "input_contract",
    "go_foreground_background_id_mismatch": "input_contract",
    "id_fasta_mismatch": "input_contract",
    "blast_binding_or_output_failure": "execution",
    "misplaced_outputs": "execution",
    "hmmer_no_hits_or_threshold": "tool_runtime",
    "hmmer_postprocess_empty": "execution",
}
ISSUE_LAYER_PRIORITY = {
    "planning": 0,
    "input_contract": 1,
    "input": 2,
    "tool_runtime": 3,
    "artifact_status": 4,
    "input_repair": 5,
    "report": 6,
}
DIAGNOSIS_STAGE_COVERAGE = {
    "pop_pca": {"preflight": ["population_vcf", "plink"], "postflight": ["scientific_identity", "required_artifacts"], "failure": ["manifest_error"]},
    "pop_phylogeny": {"preflight": ["population_vcf", "tassel", "fasttree"], "postflight": ["alignment_qc", "tree_qc"], "failure": ["manifest_error"]},
    "pop_structure": {"preflight": ["population_vcf", "plink", "admixture", "k_range"], "postflight": ["all_k", "q_fam", "best_k"], "failure": ["manifest_error"]},
    "pop_ld_decay": {"preflight": ["population_vcf", "group_qc", "poplddecay"], "postflight": ["statistics", "plots"], "failure": ["manifest_error"]},
    "pop_snp_density": {"preflight": ["population_vcf", "chromosome_policy", "cmplot"], "postflight": ["count_conservation", "canonical_plots"], "failure": ["manifest_error"]},
    "gwas_fmt": {
        "preflight": ["external_vcf", "vcf_header", "tassel_available"],
        "postflight": ["sorted_vcf", "tassel_hapmap", "sample_variant_conservation", "artifact_sha256"],
        "failure": ["tassel_exit_code", "conversion_cardinality_mismatch", "genotype_scope_mismatch"],
    },
    "genome_prepare": {
        "preflight": ["config_exists", "declared_files_exist", "fasta_content_type", "gff_nonempty"],
        "postflight": ["manifest_status", "species_result_status", "final_gff_nonempty", "pep_cds_content_type"],
        "failure": ["log_root_cause", "runtime_repair_candidates"],
    },
    "identify": {
        "preflight": ["genome_prepare_quality", "target_protein_available", "hmm_or_required_pfams"],
        "postflight": ["final_members_nonempty", "protein_stop_markers", "domain_check"],
        "failure": ["hmm_pfam_missing", "target_content_type", "header_length"],
    },
    "identify_refine": {
        "preflight": ["curated_id_list", "protein_source", "id_overlap"],
        "postflight": ["refined_members_nonempty", "refined_protein_nonempty"],
        "failure": ["missing_ids", "id_overlap_zero"],
    },
    "phylogeny": {
        "preflight": ["final_member_protein", "protein_stop_markers", "sequence_count"],
        "postflight": ["tree_nonempty", "plot_nonempty"],
        "failure": ["mafft_iqtree_error", "protein_format"],
    },
    "reference_classification": {
        "preflight": ["phylogeny_tree", "reference_subfamily"],
        "postflight": ["assignment_table", "subfamily_table"],
        "failure": ["missing_phylogeny_tree", "missing_reference_subfamily"],
    },
    "motif": {
        "preflight": ["final_member_protein", "protein_stop_markers", "sequence_count"],
        "postflight": ["meme_outputs", "motif_plot"],
        "failure": ["meme_empty", "protein_format"],
    },
    "domain": {
        "preflight": ["final_member_protein", "pfam_database", "protein_stop_markers"],
        "postflight": ["domain_table", "domain_plot"],
        "failure": ["pfam_unavailable", "hmmscan_parse"],
    },
    "structure": {
        "preflight": ["final_members", "gff_overlap"],
        "postflight": ["structure_outputs"],
        "failure": ["id_overlap_zero", "tool_unavailable"],
    },
    "promoter": {
        "preflight": ["final_members", "genome", "gff_overlap"],
        "postflight": ["promoter_fasta", "cis_element_outputs"],
        "failure": ["gff_id_mismatch", "genome_missing"],
    },
    "synteny": {
        "preflight": ["whole_genome_pep", "gff_nonempty", "family_ids", "id_overlap"],
        "postflight": ["anchors", "plots"],
        "failure": ["mcscanx_empty", "gff_id_mismatch"],
    },
    "interspecies_synteny": {
        "preflight": ["target_ref_pep", "target_ref_gff", "species_selection"],
        "postflight": ["pairwise_anchors", "jcvi_plot", "tree_plot"],
        "failure": ["missing_whole_proteome", "species_config_misread"],
    },
    "karyotype": {
        "preflight": ["gff_nonempty", "family_ids", "chromosome_assignment"],
        "postflight": ["karyotype_plot", "unplaced_report"],
        "failure": ["empty_gff", "all_unplaced"],
    },
    "combo": {
        "preflight": ["phylogeny", "motif", "domain", "structure"],
        "postflight": ["combo_plot"],
        "failure": ["missing_upstream_artifacts"],
    },
    "ortholog": {
        "preflight": ["target_family_pep", "reference_family_pep", "scope_reference_species"],
        "postflight": ["ortholog_pairs", "ortholog_manifest", "intermediate_materialized"],
        "failure": ["blast_failed", "intermediate_not_materialized", "post_processing_failed"],
    },
    "ppi": {
        "preflight": ["family_ids", "string_species_or_orthologs", "ppi_provenance_identity"],
        "postflight": ["ppi_edges", "ppi_plot"],
        "failure": [
            "string_unavailable",
            "ortholog_missing",
            "ppi_requires_ortholog_mapping",
            "ppi_reference_data_missing",
            "ppi_invalid_ortholog_mapping",
            "ppi_string_id_mapping_low",
        ],
    },
    "expression": {
        "preflight": ["expression_matrix", "sample_info", "family_ids_optional"],
        "postflight": ["heatmap", "expression_table"],
        "failure": ["matrix_id_mismatch", "sample_group_error"],
    },
    "pep": {
        "preflight": ["final_member_protein", "protein_stop_markers"],
        "postflight": ["physicochemical_table", "feature_plots", "tool_availability"],
        "failure": ["protein_format", "external_tool_unavailable"],
    },
    "rnaseq_qc": {
        "preflight": ["samples_yaml", "fastq_pairs", "duplicate_fastq"],
        "postflight": ["clean_fastq", "qc_metrics"],
        "failure": ["missing_fastq", "sample_config_error"],
    },
    "rnaseq_align": {
        "preflight": ["clean_fastq", "genome_index"],
        "postflight": ["bam_nonempty", "alignment_rate"],
        "failure": ["index_missing", "zero_alignment"],
    },
    "rnaseq_quant": {
        "preflight": ["bam_files", "gtf_gff", "featurecounts_ready"],
        "postflight": ["count_matrix", "fpkm_tpm", "matrix_parse"],
        "failure": ["featurecounts_error", "comment_char_parse"],
    },
    "extension_stage": {
        "preflight": ["publish_gate", "semantic_input_binding", "explicit_file_roles"],
        "postflight": ["output_contract", "empty_outputs", "manifest_quality", "prefix_consistency"],
        "failure": ["diagnosis_report", "stdout_stderr", "repair_candidate_gate"],
    },
    "rnaseq_de": {
        "preflight": ["count_matrix", "samples_yaml", "comparisons"],
        "postflight": ["de_results", "significant_gene_counts"],
        "failure": ["comparison_missing", "design_error"],
    },
    "rnaseq_sample_qc": {
        "preflight": ["count_or_vst_matrix", "sample_table"],
        "postflight": ["pca", "correlation", "duplicate_samples"],
        "failure": ["matrix_parse", "sample_mismatch"],
    },
    "rnaseq_go_enrich": {
        "preflight": ["de_results", "gene2go_or_annotation", "id_overlap"],
        "postflight": ["go_results", "foreground_background_overlap"],
        "failure": ["missing_gene2go", "id_overlap_zero"],
    },
    "rnaseq_kegg_enrich": {
        "preflight": ["de_results", "gene2ko_or_annotation", "id_overlap"],
        "postflight": ["kegg_results", "foreground_background_overlap"],
        "failure": ["missing_gene2ko", "id_overlap_zero"],
    },
    "rnaseq_enrich": {
        "preflight": ["de_results", "background_source", "go_kegg_background"],
        "postflight": ["go_kegg_results", "id_overlap"],
        "failure": ["missing_background", "id_overlap_zero"],
    },
    "rnaseq_fgsea": {
        "preflight": ["de_ranks", "gene2ko", "rank_background_overlap"],
        "postflight": ["fgsea_results", "pathway_size"],
        "failure": ["rank_gene2ko_id_mismatch", "kegg_species"],
    },
    "rnaseq_wgcna": {
        "preflight": ["expression_or_count_matrix", "sample_table"],
        "postflight": ["module_count", "grey_ratio", "soft_threshold"],
        "failure": ["matrix_parse", "sample_size"],
    },
    "rnaseq_candidate_family": {
        "preflight": ["de_results", "annotation_sources", "optional_enrichment_wgcna"],
        "postflight": ["candidate_family_ranking", "evidence_matrix", "recommendation", "rag_queries", "rag_references"],
        "failure": ["annotation_missing", "id_mapping"],
    },
    "rnaseq_report": {
        "preflight": ["successful_rnaseq_manifest", "prefix_scope_identity"],
        "postflight": ["report_markdown", "evidence_snapshot", "coverage_status"],
        "failure": ["no_successful_rnaseq_evidence", "scope_mismatch", "artifact_missing"],
    },
    "annotation": {
        "preflight": ["protein_background", "tool_availability"],
        "postflight": ["gene2go", "gene2ko", "annotation_coverage"],
        "failure": ["interproscan_unavailable", "kofamscan_unavailable"],
    },
    "annotation_go": {
        "preflight": ["protein_background", "interproscan_availability"],
        "postflight": ["gene2go", "annotation_go_manifest", "go_progress"],
        "failure": ["interproscan_unavailable", "go_terms_empty"],
    },
    "annotation_kegg": {
        "preflight": ["protein_background", "kofamscan_availability"],
        "postflight": ["gene2ko", "annotation_kegg_manifest", "kegg_progress"],
        "failure": ["kofamscan_unavailable", "ko_hits_empty"],
    },
}


class DiagnosisAgent(BaseAgent):
    """
    诊断Agent V3
    
    功能：
    1. 分析错误日志，定位问题
    2. 调用Knowledge查询修复方案
    3. 自动修复常见问题
    4. 生成前端通知消息
    5. 集成全局迭代计数
    6. 记录错误模式
    """
    name = "diagnosis"

    @staticmethod
    def ppi_identity_diagnosis(payload: Dict[str, Any]) -> Dict[str, Any]:
        provenance = payload.get("ppi_provenance") if isinstance(payload.get("ppi_provenance"), dict) else {}
        selection_mode = str(provenance.get("selection_mode") or payload.get("selection_mode") or "unresolved")
        code = str(provenance.get("diagnosis_code") or ppi_diagnosis_code(selection_mode, str(payload.get("status") or "")))
        return {
            "code": code,
            "selection_mode": selection_mode,
            "selected_string_taxon_id": str(provenance.get("selected_string_taxon_id") or ""),
            "snapshot": dict(provenance.get("string_snapshot") or {}),
        }
    role = "Error Diagnosis Specialist"
    
    # 常见错误模式及修复方案
    ERROR_PATTERNS = {
        "missing_input_file": {
            "patterns": [r"文件不存在", r"FileNotFoundError", r"No such file", r"找不到文件"],
            "suggestion_zh": "检查输入文件路径是否正确，确保文件已上传到input目录",
            "suggestion_en": "Check if input file path is correct; ensure files are uploaded to the input directory",
            "auto_fix": False
        },
        "hmm_not_found": {
            "patterns": [r"required_pfams is empty", r"No HMM file", r"找不到HMM"],
            "suggestion_zh": "请上传HMM模型文件或指定--required-pfams参数",
            "suggestion_en": "Please upload HMM model file or specify --required-pfams parameter",
            "auto_fix": False
        },
        "blast_db_error": {
            "patterns": [r"BLAST Database error", r"makeblastdb failed"],
            "suggestion_zh": "Identify BLAST 固定用目标物种全蛋白 FASTA 建库；请优先检查目标全蛋白 FASTA 是否存在、非空且为有效蛋白FASTA格式，再检查参考家族蛋白 query 文件。",
            "suggestion_en": "Identify BLAST uses target species full protein FASTA for database; check that target FASTA exists, is non-empty and valid, then check reference family protein query file.",
            "auto_fix": False
        },
        "memory_error": {
            "patterns": [r"MemoryError", r"out of memory", r"内存不足"],
            "suggestion_zh": "减少--cpu参数值或分批处理，建议使用--cpu 4",
            "suggestion_en": "Reduce --cpu parameter or use batch processing; recommend --cpu 4",
            "auto_fix": True,
            "fix_action": "reduce_cpu"
        },
        "rnaseq_library_strandness_error": {
            "patterns": [r"library_strandness", r"链特异性", r"rna-strandness", r"featureCounts.*-s"],
            "suggestion_zh": "请在 samples.yaml 的 rnaseq.library_strandness 或每个 samples.<sample>.library_strandness 中明确填写 non_stranded / forward / reverse；混合链特异性样本当前不能用单次 featureCounts 定量。",
            "suggestion_en": "Please specify non_stranded / forward / reverse in samples.yaml rnaseq.library_strandness or each samples.<sample>.library_strandness; mixed strand-specific samples cannot be quantified in a single featureCounts run.",
            "auto_fix": False
        },
        "timeout_error": {
            "patterns": [r"Timeout", r"timed out", r"超时"],
            "suggestion_zh": "增加超时时间或检查输入数据大小",
            "suggestion_en": "Increase timeout or check input data size",
            "auto_fix": False
        },
        "conda_env_error": {
            "patterns": [r"conda", r"Environment", r"找不到命令"],
            "suggestion_zh": "检查conda环境是否正确激活，确保工具已安装",
            "suggestion_en": "Check that conda environment is activated and tools are installed",
            "auto_fix": False
        },
        "permission_error": {
            "patterns": [r"Permission denied", r"权限不足"],
            "suggestion_zh": "检查文件权限，确保有读写权限",
            "suggestion_en": "Check file permissions; ensure read/write access",
            "auto_fix": False
        },
        "invalid_parameter": {
            "patterns": [r"unrecognized arguments", r"invalid parameter", r"参数错误"],
            "suggestion_zh": "检查参数名称是否正确，注意使用短横线而非下划线",
            "suggestion_en": "Check parameter name; use hyphens instead of underscores",
            "auto_fix": True,
            "fix_action": "fix_parameter_format"
        },
        "tool_not_found": {
            "patterns": [
                r"No such file or directory: 'mafft'",
                r"No such file or directory: 'makeblastdb'",
                r"No such file or directory: 'iqtree'",
                r"No such file or directory: 'hmmsearch'",
                r"command not found: mafft",
                r"command not found: blastp",
                r"command not found: iqtree",
                r"command not found: hmmsearch",
                r"RuntimeError: MAFFT失败.*No such file",
                r"RuntimeError:.*失败.*No such file or directory"
            ],
            "suggestion_zh": "生物信息学工具未安装或未添加到PATH。建议：1) 激活conda环境: conda activate test4py311 2) 安装缺失工具: conda install -c bioconda mafft blast iqtree hmmer",
            "suggestion_en": "Bioinformatics tools not installed or not in PATH. Suggestions: 1) activate conda environment: conda activate test4py311 2) install missing tools: conda install -c bioconda mafft blast iqtree hmmer",
            "auto_fix": False,
            "severity": "high"
        },
        "dependency_failed": {
            "patterns": [
                r"依赖阶段.*未完成",
                r"derived_from.*manifest.*not found",
                r"前置阶段.*未执行",
                r"请先执行.*阶段",
                r"requires.*stage.*not completed",
                r"dependency.*not satisfied"
            ],
            "suggestion_zh": "前置分析阶段未完成，系统将自动先执行依赖阶段",
            "suggestion_en": "Prerequisite analysis stage not completed; system will execute dependency stage first automatically",
            "auto_fix": True,
            "fix_action": "execute_dependencies",
            "severity": "high"
        },
        "go_foreground_background_id_mismatch": {
            "patterns": [
                r"GO foreground/background ID mismatch",
                r"no DE genes overlap uploaded gene2go background",
                r"foreground.*background.*ID mismatch",
                r"gene2go.*DE genes.*overlap=0"
            ],
            "suggestion": "当前 gene2go 背景与 DE 基因 ID 体系不一致。应提示用户确认本地自动注释，生成与当前蛋白/DE ID 匹配的 gene2go 后再做 GO 富集。",
            "auto_fix": False,
            "severity": "high"
        }
    }
    
    # 参数修复映射
    PARAMETER_FIXES = {
        "dom_i_evalue_max": "dom-i-evalue-max",
        "hmm_cov_min": "hmm-cov-min",
        "blast_evalue": "blast-evalue",
        "idEvalue": "id-evalue",
        "idCoverage": "id-coverage"
    }
    
    def __init__(self, llm_manager: LLMManager):
        super().__init__(llm_manager)
        self.error_history: List[Dict] = []
        self.auto_fix_count = 0
    
    async def execute(self, state: AgentState, message: AgentMessage) -> AgentState:
        """执行诊断"""
        print(f"[DiagnosisAgent] 开始诊断...")
        
        error_info = message.payload.get("error", {})
        execution_result = message.payload.get("execution_result", {})
        
        # 提取错误信息
        stage = error_info.get("stage") or execution_result.get("stage", "unknown")
        error_message = error_info.get("message") or error_info.get("error") or execution_result.get("error") or execution_result.get("message") or ""
        stderr = error_info.get("stderr") or execution_result.get("stderr", "")
        stdout = error_info.get("stdout") or execution_result.get("stdout", "")
        full_error = f"{error_message}\n{stderr}\n{stdout}"
        
        print(f"[DiagnosisAgent] 诊断阶段: {stage}")
        
        # 诊断错误
        diagnosis = await self._diagnose_error(stage, full_error, error_info)
        
        # 如果未匹配到已知模式，调用 Knowledge Agent 查询
        if diagnosis.get("error_type") == "unknown":
            print(f"[DiagnosisAgent] 未知错误，尝试调用 Knowledge Agent...")
            knowledge_result = await self._query_knowledge_for_error(full_error)
            if knowledge_result:
                diagnosis = knowledge_result
        
        # 获取当前迭代计数
        iteration_count = state.results.get("iteration_count", 0)
        max_iterations = state.results.get("max_iterations", 3)
        
        # 检查是否达到最大迭代次数
        if iteration_count >= max_iterations:
            diagnosis["can_auto_fix"] = False
            diagnosis["suggestion"] = f"已达到最大迭代次数({max_iterations})，停止自动修复。"
            diagnosis["stop_iteration"] = True
        
        # 尝试自动修复
        if diagnosis.get("can_auto_fix") and diagnosis.get("fix_action"):
            fix_result = await self._attempt_auto_fix(
                diagnosis["fix_action"], error_info, full_error, diagnosis
            )
            diagnosis["fix_result"] = fix_result
            
            if fix_result and fix_result.get("success"):
                self.auto_fix_count += 1
                iteration_count += 1
                state.results["iteration_count"] = iteration_count
                state.results["auto_fix_count"] = self.auto_fix_count
                diagnosis["iteration_count"] = iteration_count
                
                # 生成前端通知
                notification = self._generate_notification(stage, diagnosis, fix_result)
                diagnosis["notification"] = notification
                state.results["auto_fix_notification"] = notification
        
        # 更新状态
        state.results["diagnosis"] = diagnosis
        state.results["can_auto_fix"] = diagnosis.get("can_auto_fix", False)
        
        state.messages.append({
            "role": "diagnosis",
            "content": f"🔍 诊断完成: {diagnosis.get('error_type', 'unknown')}",
        })
        
        return state
    
    async def _diagnose_error(self, stage: str, error_text: str, error_info: Dict) -> Dict:
        """诊断错误"""
        # 尝试匹配已知错误模式
        matched_pattern = self._match_error_pattern(error_text)
        
        if matched_pattern:
            error_type = matched_pattern["error_type"]
            suggestion = matched_pattern["suggestion"]
            can_auto_fix = matched_pattern.get("auto_fix", False)
            fix_action = matched_pattern.get("fix_action")
            
            return {
                "error_type": error_type,
                "stage": stage,
                "suggestion": suggestion,
                "can_auto_fix": can_auto_fix,
                "fix_action": fix_action,
                "confidence": 0.9,
                "error_text": error_text[:500]
            }
        
        # 未知错误
        return {
            "error_type": "unknown",
            "stage": stage,
            "suggestion": "无法自动诊断，请检查错误日志",
            "can_auto_fix": False,
            "requires_user_confirmation": True,
            "confirmation_reason": "未知错误不能自动修复，需要用户查看日志后确认下一步。",
            "confidence": 0.3,
            "error_text": error_text[:500]
        }

    def diagnose_job_stage(
        self,
        job_id: str,
        stage: str,
        prefix: str = "",
        project_root: Path = PROJECT_ROOT,
        prepare_patch_workspace: bool = False,
        scope_id: str = "",
        params: Optional[Dict[str, Any]] = None,
        language: str = "zh",
    ) -> Dict[str, Any]:
        """只读审计某个 job/stage 的产物、manifest、日志和输入配置。

        该方法不重跑阶段、不修改输入、不修改全局脚本。它只返回结构化问题、
        用户确认需求以及可选的当前 job 修复副本工作区。
        """
        workspace = project_root / "workspace" / job_id
        final_dir = workspace / "final"
        input_dir = project_root / "input" / job_id
        logs_dir = workspace / "logs"
        issues: List[Dict[str, Any]] = []
        evidence: List[str] = []
        params = params or {}
        stage_context = self._stage_context_from_params(stage, prefix, params)
        scope_id = scope_id or self._scope_id_from_params(params) or str(stage_context.get("scope_id") or "")

        def add_issue(
            code: str,
            severity: str,
            message: str,
            recommendation: str,
            *,
            requires_confirmation: bool = True,
            runtime_repair: bool = False,
            safe_patch_candidate: bool = False,
            blocks_downstream: bool = False,
            evidence_items: Optional[List[str]] = None,
        ) -> None:
            layer = ISSUE_LAYER_BY_CODE.get(code, "report")
            issues.append({
                "code": code,
                "severity": severity,
                "layer": layer,
                "issue_layer": layer,
                "message": message,
                "recommendation": recommendation,
                "requires_user_confirmation": requires_confirmation,
                "runtime_repair": runtime_repair,
                "safe_patch_candidate": safe_patch_candidate,
                "blocks_downstream": blocks_downstream,
                "evidence": evidence_items or [],
            })

        manifest = self._load_stage_manifest(final_dir, stage, prefix, scope_id=scope_id)
        if manifest and not stage_context:
            stage_context = manifest.get("stage_context") or {
                "scope_id": manifest.get("scope_id", ""),
                "scope_mode": manifest.get("scope_mode", ""),
                "analysis_route": manifest.get("analysis_route", ""),
                "member_set_id": manifest.get("member_set_id", ""),
                "target_species": manifest.get("target_species", []),
                "reference_species": manifest.get("reference_species", []),
                "comparative_species": manifest.get("comparative_species", []),
            }
        if manifest:
            evidence.append(f"manifest={manifest.get('_path')}")
            status = manifest.get("status", "unknown")
            if status not in {"success", "partial_success"}:
                manifest_detail = str(manifest.get("error") or manifest.get("message") or "").strip()
                log_text = self._latest_stage_log_text(logs_dir, stage, limit=6000)
                log_lines = []
                if log_text:
                    for line in log_text.splitlines():
                        stripped = line.strip()
                        if not stripped:
                            continue
                        if any(token in stripped for token in ["Traceback", "Error", "ERROR", "失败", "FileNotFoundError", "cannot", "Cannot", "missing", "未找到"]):
                            log_lines.append(stripped)
                    if not log_lines:
                        log_lines = [line.strip() for line in log_text.splitlines()[-8:] if line.strip()]
                detail_items = []
                if manifest_detail:
                    detail_items.append(manifest_detail[:500])
                if log_lines:
                    detail_items.append("日志摘要: " + " | ".join(log_lines[-6:])[:900])
                add_issue(
                    "manifest_failed",
                    "blocking",
                    f"{stage} manifest 状态为 {status}",
                    "查看日志摘要定位根因；若是缺上游产物，应先补跑对应上游阶段后重试。",
                    blocks_downstream=True,
                    evidence_items=detail_items,
                )
        else:
            stage_dir = workspace / stage
            has_intermediate = stage_dir.exists() and any(stage_dir.rglob("*"))
            latest_log = self._latest_stage_log_text(logs_dir, stage, limit=8000)
            root_cause_found = False
            if "SameFileError" in latest_log or "are the same file" in latest_log:
                add_issue(
                    "stage_runtime_same_file_copy",
                    "blocking",
                    f"{stage} 阶段脚本在收尾复制文件时把同一个文件复制到自身，导致运行崩溃。",
                    "这是阶段脚本的运行时保护缺失，不是用户输入文件或 manifest 收尾问题。应修复同文件 copy guard 后重新执行该阶段。",
                    requires_confirmation=False,
                    blocks_downstream=True,
                    evidence_items=[latest_log[-1600:] if latest_log else str(logs_dir)],
                )
                root_cause_found = True
            if "ModuleNotFoundError: No module named 'apps'" in latest_log:
                add_issue(
                    "python_import_path_error",
                    "blocking",
                    f"{stage} 启动失败：阶段脚本无法导入项目模块 apps。",
                    "这是工程启动路径问题，不是用户输入文件问题。应在阶段脚本中加入项目根目录到 sys.path，或用模块方式从项目根目录执行。",
                    requires_confirmation=True,
                    safe_patch_candidate=True,
                    blocks_downstream=True,
                    evidence_items=[latest_log[-1200:]],
                )
                root_cause_found = True
            if "requires Gene_Family" in latest_log and "known_family" in latest_log or "family_pep requires Gene_Family" in latest_log:
                add_issue(
                    "known_family_missing_gene_family",
                    "blocking",
                    f"{stage} 读取 config.xlsx 失败：known-family/family_pep 行缺少 Gene_Family。",
                    "应检查 config.xlsx 中已知家族 seed 文件行，例如 At.NAC.pep.fasta 应填写 Gene_Family=NAC、Species_Role=seed_reference、File_Scope=known_family。",
                    requires_confirmation=True,
                    blocks_downstream=True,
                    evidence_items=[latest_log[-1200:]],
                )
                root_cause_found = True
            if stage == "domain" and "Domain stage requires identify results" in latest_log:
                add_issue(
                    "domain_scoped_identify_guard",
                    "blocking",
                    "domain 后端执行守卫要求 legacy identify manifest，但当前 scope 流程已有同 scope 的成员蛋白和系统发育输入。",
                    "这是后端执行层与 scope 契约不一致，不应按 manifest 缺失自动恢复；应修订 domain 后端守卫，使其接受 scope_file/同 scope phylogeny manifest 解析到的成员蛋白。",
                    requires_confirmation=False,
                    blocks_downstream=True,
                    evidence_items=[latest_log[-1200:]],
                )
                root_cause_found = True
            if stage == "kaks":
                scope_data = self._scope_from_params(params)
                ref_species = params.get("referenceSpecies") or params.get("reference_species") or params.get("refSpeciesList") or []
                if isinstance(ref_species, str):
                    ref_species = [item.strip() for item in re.split(r"[,;，；\s]+", ref_species) if item.strip()]
                if not ref_species and scope_data:
                    ref_species = scope_data.get("reference_species") or []
                scope_mode = str(params.get("scopeMode") or params.get("scope_mode") or (scope_data.get("scope_mode") if scope_data else "") or "")
                missing_ref_failure = "KaKs scope 中没有 reference_species" in latest_log or (bool(params) and not ref_species and scope_mode in {"", "target_only"})
                if missing_ref_failure:
                    add_issue(
                        "kaks_scope_missing_reference",
                        "blocking",
                        "Ka/Ks 是 target-vs-reference 分析，但本次 scope 没有 reference_species。",
                        "应在规划阶段使用 target_plus_reference scope，并从 config/用户参数中带入参考物种；若用户未指定，按项目默认参考物种处理。",
                        requires_confirmation=False,
                        blocks_downstream=True,
                        evidence_items=[latest_log[-1200:] if latest_log else json.dumps(params, ensure_ascii=False)[:1200]],
                    )
                    root_cause_found = True
            if stage == "ortholog":
                from gfa_core.ortholog_materialize import ortholog_pairs_has_data

                ortholog_dir = workspace / "ortholog"
                final_pairs = final_dir / f"{prefix}.ortholog_pairs.txt" if prefix else None
                intermediate_pairs = sorted(ortholog_dir.glob("*_pairs.txt")) if ortholog_dir.exists() else []
                intermediate_with_data = [path for path in intermediate_pairs if ortholog_pairs_has_data(path)]
                final_missing = (
                    final_pairs is None
                    or not final_pairs.exists()
                    or final_pairs.stat().st_size == 0
                    or not ortholog_pairs_has_data(final_pairs)
                )
                if intermediate_with_data and final_missing:
                    add_issue(
                        "ortholog_intermediate_not_materialized",
                        "blocking",
                        "Ortholog 已在 ortholog/ 生成 BLAST 中间产物，但 final/ 映射文件与 manifest 缺失。",
                        "复用现有 intermediate pairs 执行 ortholog 收尾落盘（无需重跑 BLAST），完成后再运行 kaks/ppi。",
                        requires_confirmation=False,
                        runtime_repair=True,
                        blocks_downstream=True,
                        evidence_items=[str(path) for path in intermediate_with_data[:5]],
                    )
                    root_cause_found = True
                if "Ortholog post-processing failed" in latest_log:
                    add_issue(
                        "ortholog_post_processing_failed",
                        "blocking",
                        "Ortholog BLAST 已完成，但 backend 收尾落盘失败。",
                        "检查日志中的 post-processing 异常；可重跑 ortholog 阶段，系统会复用 ortholog/ 现有 pairs。",
                        blocks_downstream=True,
                        evidence_items=[latest_log[-1200:] if latest_log else str(logs_dir)],
                    )
                    root_cause_found = True
            if stage == "expression":
                gene_id_candidates = (
                    list(input_dir.glob("*geneID*"))
                    + list(input_dir.glob("*.final_members.id"))
                    + list(final_dir.glob("*geneID*"))
                    + list(final_dir.glob("*.final_members.id"))
                )
                if not gene_id_candidates:
                    add_issue(
                        "expression_missing_gene_ids",
                        "blocking",
                        "Expression(使用FPKM) 已找到/可使用表达矩阵，但缺少目标基因列表。",
                        "请先运行 Identify 生成 *.final_members.id，或上传要绘图的基因列表文件（*geneID* 或 *.final_members.id）。不要把全基因组 FPKM 矩阵直接当作基因家族成员列表。",
                        requires_confirmation=False,
                        blocks_downstream=True,
                        evidence_items=[str(input_dir), str(final_dir)],
                    )
                    root_cause_found = True
                sample_candidates = list(input_dir.glob("sample.txt")) + list(final_dir.glob("sample.txt"))
                if not sample_candidates:
                    add_issue(
                        "expression_missing_sample_txt",
                        "warning" if root_cause_found else "blocking",
                        "Expression 阶段缺少 sample.txt 分组文件。",
                        "当前 job 有 samples.yaml 时可转换为 sample.txt；但基因家族绘图仍必须先提供目标基因列表。",
                        requires_confirmation=False,
                        blocks_downstream=not root_cause_found,
                        evidence_items=[str(input_dir / "samples.yaml")],
                    )
                    root_cause_found = True
            if has_intermediate and not (stage == "ortholog" and root_cause_found):
                add_issue(
                    "manifest_missing_stage_incomplete",
                    "warning",
                    f"未找到 {stage} 阶段 manifest，但阶段目录已有中间产物。",
                    "该阶段可能正在运行或未完成收尾。不要中断进程；等待阶段结束后再做最终诊断。",
                    requires_confirmation=False,
                    blocks_downstream=False,
                    evidence_items=[str(stage_dir)],
                )
            elif not root_cause_found:
                add_issue(
                    "manifest_missing",
                    "blocking",
                    f"未找到 {stage} 阶段 manifest",
                    "先确认该阶段是否已执行；若已执行但未产出 manifest，应检查阶段日志。",
                    requires_confirmation=False,
                    blocks_downstream=True,
                )

        identity_status = self._add_stage_identity_status_issue(job_id, stage, prefix, params, manifest, add_issue, gate="diagnose")
        if identity_status:
            evidence.append(f"stage_identity_status={json.dumps(identity_status, ensure_ascii=False)[:1200]}")

        if stage == "gwas_fmt":
            self._audit_gwas_fmt(manifest, add_issue)
        elif stage.startswith("pop_"):
            self._audit_population_stage(stage, manifest, add_issue)
        elif stage == "rnaseq_qc":
            self._audit_rnaseq_qc(input_dir, final_dir, manifest, add_issue)
        elif stage == "rnaseq_align":
            self._audit_rnaseq_align(final_dir, manifest, add_issue)
        elif stage == "rnaseq_sample_qc":
            self._audit_rnaseq_sample_qc(final_dir, logs_dir, manifest, add_issue)
        elif stage == "rnaseq_quant":
            self._audit_rnaseq_quant(final_dir, logs_dir, manifest, add_issue)
        elif stage == "rnaseq_go_enrich":
            self._audit_rnaseq_go_enrich(final_dir, manifest, add_issue)
        elif stage == "rnaseq_fgsea":
            self._audit_rnaseq_fgsea(final_dir, logs_dir, manifest, add_issue)
        elif stage == "rnaseq_wgcna":
            self._audit_rnaseq_wgcna(final_dir, logs_dir, manifest, add_issue)
        else:
            self._audit_generic_gene_family_stage(stage, final_dir, logs_dir, manifest, add_issue, prefix)

        issues = self._prioritize_issues(self._dedupe_issues(issues))
        highest = self._highest_severity(issues)
        repair_plan = self._build_repair_plan(stage, issues)
        primary_issue = issues[0] if issues else None
        primary_code = str((primary_issue or {}).get("code") or "")
        primary_layer = str(
            (primary_issue or {}).get("layer")
            or (primary_issue or {}).get("issue_layer")
            or ISSUE_LAYER_BY_CODE.get(primary_code, "report")
        )
        block_sidecar_repairs = (
            primary_layer == "planning"
            or (
                primary_layer == "artifact_status"
                and primary_code in {"manifest_missing", "manifest_missing_stage_incomplete", "manifest_failed"}
            )
        )
        safe_patch_candidates = [
            issue for issue in issues
            if issue.get("safe_patch_candidate") and issue.get("code") in KNOWN_COPY_PATCH_CODES
        ]
        if block_sidecar_repairs:
            safe_patch_candidates = []
        needs_confirmation = repair_plan.get("requires_user_confirmation", False)
        patch_workspace = None
        if prepare_patch_workspace and safe_patch_candidates:
            patch_workspace = self.prepare_repair_workspace(
                job_id=job_id,
                stage=stage,
                issue=safe_patch_candidates[0],
                project_root=project_root,
            )
        ppi_identity_diagnosis = self.ppi_identity_diagnosis(manifest or params) if stage == "ppi" else {}

        return {
            "stage": stage,
            "job_id": job_id,
            "prefix": prefix,
            "stage_context": stage_context,
            "scope_id": scope_id,
            "status": "ok" if not issues else "needs_attention",
            "severity": highest,
            "primary_issue": primary_issue,
            "requires_user_confirmation": needs_confirmation,
            "issues": issues,
            "evidence": evidence,
            "repair_plan": repair_plan,
            "safe_patch_candidates": [issue["code"] for issue in safe_patch_candidates],
            "patch_workspace": patch_workspace,
            "ppi_identity_diagnosis": ppi_identity_diagnosis,
            "policy": {
                "auto_modify_input": False,
                "auto_modify_global_code": False,
                "auto_rerun_stage": False,
                "patch_scope": "job_reproducibility_copy_only",
            },
        }

    def diagnose_stage_gate(
        self,
        job_id: str,
        stage: str,
        prefix: str = "",
        gate: str = "preflight",
        project_root: Path = PROJECT_ROOT,
        params: Optional[Dict[str, Any]] = None,
        language: str = "zh",
    ) -> Dict[str, Any]:
        """Global QA gate for stage preflight/postflight/failure.

        This is intentionally job-local and deterministic: it reads manifests,
        logs and files, assigns issue codes, and exposes runtime repair actions.
        It never edits global source code.
        """
        gate = gate if gate in {"preflight", "postflight", "failure"} else "preflight"
        requested_stage = stage
        stage = {"genome_prepare_v2": "genome_prepare"}.get(stage, stage)
        workspace = project_root / "workspace" / job_id
        final_dir = workspace / "final"
        input_dir = project_root / "input" / job_id
        logs_dir = workspace / "logs"
        issues: List[Dict[str, Any]] = []
        evidence: List[str] = []

        def add_issue(
            code: str,
            severity: str,
            message: str,
            recommendation: str,
            *,
            requires_confirmation: bool = False,
            runtime_repair: bool = False,
            safe_patch_candidate: bool = False,
            blocks_downstream: bool = False,
            evidence_items: Optional[List[str]] = None,
        ) -> None:
            layer = ISSUE_LAYER_BY_CODE.get(code, "report")
            issues.append({
                "code": code,
                "severity": severity,
                "layer": layer,
                "issue_layer": layer,
                "message": message,
                "recommendation": recommendation,
                "requires_user_confirmation": requires_confirmation,
                "runtime_repair": runtime_repair,
                "safe_patch_candidate": safe_patch_candidate,
                "blocks_downstream": blocks_downstream,
                "evidence": evidence_items or [],
            })

        params = params or {}
        stage_context = self._stage_context_from_params(stage, prefix, params)
        scope_id = self._scope_id_from_params(params) or str(stage_context.get("scope_id") or "")
        manifest = self._load_stage_manifest(final_dir, stage, prefix, scope_id=scope_id)
        if stage == "genome_prepare" and gate == "preflight" and self._genome_prepare_manifest_stale(input_dir, manifest):
            evidence.append("genome_prepare_previous_manifest_ignored=input_updated_after_failed_manifest")
            manifest = None
        if manifest and not stage_context:
            stage_context = manifest.get("stage_context") or {
                "scope_id": manifest.get("scope_id", ""),
                "scope_mode": manifest.get("scope_mode", ""),
                "analysis_route": manifest.get("analysis_route", ""),
                "member_set_id": manifest.get("member_set_id", ""),
                "target_species": manifest.get("target_species", []),
                "reference_species": manifest.get("reference_species", []),
                "comparative_species": manifest.get("comparative_species", []),
            }
        if gate in {"postflight", "failure"}:
            base = self.diagnose_job_stage(job_id, stage, prefix, project_root, scope_id=scope_id, params=params)
            for issue in base.get("issues", []):
                issues.append(issue)
            evidence.extend(base.get("evidence", []))
            if manifest:
                evidence.append(f"manifest={manifest.get('_path')}")

        identity_status = self._add_stage_identity_status_issue(job_id, stage, prefix, params, manifest, add_issue, gate=gate)
        if identity_status:
            evidence.append(f"stage_identity_status={json.dumps(identity_status, ensure_ascii=False)[:1200]}")

        if params.get("source") == "methodology" or params.get("methodology"):
            self._gate_saved_methodology(stage, final_dir, input_dir, params, add_issue, evidence)

        if stage == "gwas_fmt":
            if gate == "preflight":
                self._audit_gwas_fmt_preflight(params, add_issue)
            else:
                self._audit_gwas_fmt(manifest, add_issue)
        elif stage.startswith("pop_"):
            self._audit_population_stage(stage, manifest, add_issue)
        elif stage == "genome_prepare":
            self._gate_genome_prepare(input_dir, final_dir, logs_dir, manifest, gate, add_issue)
        elif stage == "identify":
            self._gate_identify(input_dir, final_dir, logs_dir, prefix, params, gate, add_issue)
        elif stage in {"phylogeny", "motif", "domain", "pep"}:
            self._gate_protein_stage(stage, final_dir, prefix, manifest, add_issue, params)
        elif stage in {"structure", "promoter", "synteny", "karyotype"}:
            self._gate_gff_member_stage(stage, final_dir, prefix, manifest, add_issue, params)
        elif stage == "reference_classification":
            self._gate_reference_classification(input_dir, final_dir, logs_dir, prefix, params, add_issue)
        elif stage == "interspecies_synteny":
            self._gate_interspecies_synteny(input_dir, final_dir, add_issue)
        elif stage in {"rnaseq_qc", "rnaseq_align", "rnaseq_quant", "rnaseq_de", "rnaseq_sample_qc", "rnaseq_go_enrich", "rnaseq_kegg_enrich", "rnaseq_enrich", "rnaseq_fgsea", "rnaseq_wgcna"}:
            # Reuse the existing RNA-seq audits for both postflight and failure.
            if gate != "preflight":
                if stage == "rnaseq_qc":
                    self._audit_rnaseq_qc(input_dir, final_dir, manifest, add_issue)
                elif stage == "rnaseq_align":
                    self._audit_rnaseq_align(final_dir, manifest, add_issue)
                elif stage == "rnaseq_sample_qc":
                    self._audit_rnaseq_sample_qc(final_dir, logs_dir, manifest, add_issue)
                elif stage == "rnaseq_quant":
                    self._audit_rnaseq_quant(final_dir, logs_dir, manifest, add_issue)
                elif stage == "rnaseq_de":
                    self._audit_rnaseq_de_auto_expression(final_dir, logs_dir, manifest, add_issue)
                elif stage in {"rnaseq_go_enrich", "rnaseq_enrich"}:
                    self._audit_rnaseq_go_enrich(final_dir, manifest, add_issue)
                elif stage == "rnaseq_fgsea":
                    self._audit_rnaseq_fgsea(final_dir, logs_dir, manifest, add_issue)
                elif stage == "rnaseq_wgcna":
                    self._audit_rnaseq_wgcna(final_dir, logs_dir, manifest, add_issue)

        if gate == "failure":
            self._augment_failure_with_llm(
                job_id=job_id,
                stage=stage,
                prefix=prefix,
                input_dir=input_dir,
                final_dir=final_dir,
                logs_dir=logs_dir,
                manifest=manifest,
                params=params,
                issues=issues,
                evidence=evidence,
                add_issue=add_issue,
            )

        issues = self._prioritize_issues(self._dedupe_issues(issues))
        highest = self._highest_severity(issues)
        repair_plan = self._build_repair_plan(stage, issues)
        primary_issue = issues[0] if issues else None
        runtime_repairs = list(dict.fromkeys(
            issue["code"] for issue in issues
            if issue.get("runtime_repair") or issue.get("code") in RUNTIME_REPAIR_CODES
        ))
        blocking = any(issue.get("severity") == "blocking" or issue.get("blocks_downstream") for issue in issues)
        ppi_identity_diagnosis = self.ppi_identity_diagnosis(manifest or params) if stage == "ppi" else {}
        return {
            "stage": stage,
            "requested_stage": requested_stage,
            "job_id": job_id,
            "prefix": prefix,
            "stage_context": stage_context,
            "scope_id": scope_id,
            "gate": gate,
            "status": "pass" if not blocking else "blocked",
            "severity": highest,
            "primary_issue": primary_issue,
            "coverage": DIAGNOSIS_STAGE_COVERAGE.get(stage, {}),
            "issues": issues,
            "evidence": evidence,
            "repair_plan": repair_plan,
            "requires_user_confirmation": repair_plan.get("requires_user_confirmation", False),
            "runtime_repair_candidates": runtime_repairs,
            "ppi_identity_diagnosis": ppi_identity_diagnosis,
            "policy": {
                "auto_modify_input": True,
                "auto_modify_global_code": False,
                "auto_rerun_stage": gate in {"preflight", "failure"},
                "patch_scope": "job_local_runtime_repair",
            },
        }

    def _gate_saved_methodology(self, stage: str, final_dir: Path, input_dir: Path, params: Dict[str, Any], add_issue, evidence: List[str]) -> None:
        family = str(params.get("geneFamily") or params.get("gene_family") or "").strip()
        targets = [str(x).strip() for x in (params.get("targetSpecies") or params.get("target_species") or []) if str(x).strip()]
        refs = [str(x).strip() for x in (params.get("referenceSpecies") or params.get("reference_species") or params.get("refSpeciesList") or []) if str(x).strip()]
        if params.get("requiresConfirmedSubfamily") and stage not in {"reference_classification", "subfamily_confirm"}:
            confirmed = []
            if final_dir.exists():
                for path in final_dir.glob("*confirmed_subfamily.tsv"):
                    name = path.name
                    if family and family not in name:
                        continue
                    if targets and not any(target in name for target in targets):
                        continue
                    if path.stat().st_size > 0:
                        confirmed.append(str(path))
            if not confirmed:
                add_issue(
                    "methodology_confirmed_subfamily_missing",
                    "blocking",
                    "保存方法学复现链路要求先完成 confirmed subfamily，再执行确认后常规分析。",
                    "先执行 reference_classification 并在亚家族确认弹窗保存 confirmed_subfamily.tsv。",
                    blocks_downstream=True,
                    evidence_items=["missing=confirmed_subfamily.tsv", "missing=subfamily_confirm.manifest.json"],
                )
            else:
                evidence.append("methodology_confirmed_subfamily=" + confirmed[0])
        if stage == "expression" and (params.get("source") == "methodology" or params.get("methodology")):
            matrix_files = []
            if input_dir.exists():
                for pattern in ("*TPM*", "*tpm*", "*FPKM*", "*fpkm*", "*expression*matrix*", "*expr*matrix*"):
                    matrix_files.extend(str(p) for p in input_dir.rglob(pattern) if p.is_file() and p.name != "config.xlsx")
            geo_id = str(params.get("geoId") or params.get("geo_id") or "").strip()
            if not matrix_files and not geo_id:
                add_issue(
                    "methodology_expression_input_missing",
                    "blocking",
                    "保存方法学记录了组织表达来源，但当前 job 未提供表达矩阵，也未在 expression 参数中确认 GEO ID。",
                    "上传 TPM/FPKM/count 表达矩阵，或在 expression 弹窗中填写 GEO ID 后再执行；raw RNA-seq accession 仍需单独授权。",
                    blocks_downstream=True,
                )
        if str(params.get("analysisRoute") or params.get("analysis_route") or "").startswith("raw_rnaseq"):
            add_issue(
                "methodology_raw_rnaseq_requires_user_consent",
                "blocking",
                "Raw RNA-seq accession 重分析需要用户明确授权，当前只作为 blocked 子流程记录。",
                "获得授权并准备 FASTQ/samples.yaml 后再展开 RNA-seq 子流程。",
                requires_confirmation=True,
                blocks_downstream=True,
            )
        if stage in {"reference_classification", "interspecies_synteny"} and not refs:
            add_issue(
                "methodology_reference_species_missing",
                "blocking",
                "保存方法学链路需要 referenceSpecies（当前 HDZIP 复现为 Si vs At），但参数中缺失参考物种。",
                "在 config.xlsx 或执行参数中补齐 referenceSpecies/refSpeciesList，例如 At。",
                blocks_downstream=True,
            )

    def apply_runtime_repairs(
        self,
        job_id: str,
        stage: str,
        prefix: str = "",
        diagnosis: Optional[Dict[str, Any]] = None,
        project_root: Path = PROJECT_ROOT,
        *,
        execute_heavy: bool = False,
    ) -> Dict[str, Any]:
        """Apply safe job-local runtime repairs, never global source patches."""
        diagnosis = diagnosis or self.diagnose_stage_gate(job_id, stage, prefix, "preflight", project_root)
        issues = diagnosis.get("issues", [])
        repair_plan = diagnosis.get("repair_plan") or self._build_repair_plan(stage, issues)
        auto_codes = {
            action.get("issue_code")
            for action in repair_plan.get("actions", [])
            if action.get("policy") == "auto"
        }
        if any(issue.get("code", "").startswith("genome_prepare_") for issue in issues) and self.is_stage_running(job_id, "genome_prepare"):
            return {
                "success": False,
                "status": "running_stage_blocked",
                "stage": stage,
                "job_id": job_id,
                "message": "genome_prepare 当前仍在运行，拒绝执行 job-local 修复以避免覆盖正在生成的产物。",
                "results": [],
            }
        results = []
        extracted_species: set[str] = set()
        for issue in issues:
            code = issue.get("code")
            if code not in auto_codes:
                continue
            if code == "protein_fasta_stop_markers":
                results.append(self._apply_protein_stop_marker_fix(job_id, stage, prefix, issue, project_root))
            elif code == "gene_gff_id_overlap_low":
                results.append(self._write_gene_gff_overlap_report(job_id, stage, prefix, issue, project_root))
            elif code == "genome_prepare_final_gff_empty":
                results.append(self._repair_empty_final_gff(job_id, issue, project_root))
            elif code in {"genome_prepare_pep_looks_like_genome", "genome_prepare_cds_looks_like_genome"}:
                species = str(self._issue_payload(issue).get("species") or "")
                if species and species in extracted_species:
                    continue
                results.append(self._repair_genome_prepare_extract_sequences(job_id, issue, project_root, execute_heavy=execute_heavy))
                if species:
                    extracted_species.add(species)
            elif code in {"identify_pfam_remote_404", "pfam_remote_404", "identify_missing_hmm_or_pfam", "identify_pfam_partial_fetch", "identify_hmm_model_mismatch"}:
                results.append(self._repair_identify_local_pfam_hmm(job_id, prefix, issue, project_root))
            elif code == "ncbi_family_reference_link_missing":
                results.append(self._repair_ncbi_family_reference_link(job_id, issue, project_root))
            elif code in {"manifest_missing", "manifest_missing_stage_incomplete"}:
                results.append(self._repair_missing_manifest_from_artifacts(job_id, stage, prefix, issue, project_root))
            elif code == "ortholog_intermediate_not_materialized":
                results.append(self._repair_ortholog_materialize_from_intermediate(job_id, prefix, issue, project_root))
        changed = any(item.get("success") and item.get("changed", True) for item in results)
        should_rerun = any(bool(item.get("should_rerun_stage")) for item in results)
        return {
            "success": all(item.get("success", False) for item in results) if results else True,
            "status": "repaired" if changed else "no_repair_needed",
            "stage": stage,
            "job_id": job_id,
            "repair_plan": repair_plan,
            "results": results,
            "should_rerun_stage": should_rerun,
            "global_code_changed": False,
        }

    def _build_repair_plan(self, stage: str, issues: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Normalize issue-specific fixes into a common repair contract.

        Policies:
        - auto: safe job-local action; may be applied without user input.
        - confirm: needs user confirmation or missing user parameter.
        - report_only: diagnostic/report action only; no data mutation.
        - dev_fix: global source change or tool installation required.
        """
        actions: List[Dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        primary_issue = issues[0] if issues else {}
        primary_code = str(primary_issue.get("code") or "")
        primary_layer = str(
            primary_issue.get("layer")
            or primary_issue.get("issue_layer")
            or ISSUE_LAYER_BY_CODE.get(primary_code, "report")
        )
        sidecar_repairs_blocked = (
            primary_layer == "planning"
            or (
                primary_layer == "artifact_status"
                and primary_code in {"manifest_missing", "manifest_missing_stage_incomplete", "manifest_failed"}
            )
        )
        for issue in issues:
            code = str(issue.get("code") or "unknown")
            policy, action = REPAIR_POLICY_BY_CODE.get(code, ("report_only", "review_issue"))
            layer = str(issue.get("layer") or issue.get("issue_layer") or ISSUE_LAYER_BY_CODE.get(code, "report"))
            if issue.get("runtime_repair") and code in RUNTIME_REPAIR_CODES and code != "gene_gff_id_overlap_low":
                policy, action = REPAIR_POLICY_BY_CODE.get(code, ("auto", code))
            if issue.get("safe_patch_candidate") and code in KNOWN_COPY_PATCH_CODES and code not in RUNTIME_REPAIR_CODES:
                policy, action = "dev_fix", "prepare_job_patch_then_upgrade_source"
            if issue.get("requires_user_confirmation") and policy == "auto":
                policy = "confirm"
            if layer == "planning" or (sidecar_repairs_blocked and code != primary_code):
                policy = "report_only"
            key = (code, action)
            if key in seen:
                continue
            seen.add(key)
            actions.append({
                "issue_code": code,
                "policy": policy,
                "action": action,
                "layer": layer,
                "issue_layer": layer,
                "stage": stage,
                "severity": issue.get("severity", "warning"),
                "message": issue.get("message", ""),
                "recommendation": issue.get("recommendation", ""),
                "safe_job_local": policy in {"auto", "report_only"},
                "requires_user_confirmation": policy == "confirm",
                "global_code_change": policy == "dev_fix",
            })
        policies = {action["policy"] for action in actions}
        return {
            "stage": stage,
            "actions": actions,
            "has_auto": "auto" in policies,
            "has_confirm": "confirm" in policies,
            "has_report_only": "report_only" in policies,
            "has_dev_fix": "dev_fix" in policies,
            "requires_user_confirmation": "confirm" in policies,
            "auto_action_count": sum(1 for action in actions if action["policy"] == "auto"),
            "confirm_action_count": sum(1 for action in actions if action["policy"] == "confirm"),
            "dev_fix_count": sum(1 for action in actions if action["policy"] == "dev_fix"),
        }

    def _add_stage_identity_status_issue(
        self,
        job_id: str,
        stage: str,
        prefix: str,
        params: Dict[str, Any],
        manifest: Optional[Dict[str, Any]],
        add_issue,
        *,
        gate: str = "diagnose",
    ) -> Optional[Dict[str, Any]]:
        """Explain manifest-success/status-failure cases with the shared scope identity contract."""
        if not manifest or str(manifest.get("status") or "") not in {"success", "partial_success"}:
            return None
        try:
            from gfa_core.mas.tools.stage_identity import resolve_stage_identity, get_stage_status_for_identity, explain_status_mismatch

            identity = resolve_stage_identity(
                stage=stage,
                prefix=prefix,
                params={**(params or {}), "prefix": prefix},
                job_id=job_id,
            )
            report = get_stage_status_for_identity(job_id=job_id, identity=identity, strict_identity=True)
            if report.completed:
                return report.to_dict()
            evidence_items = [
                f"manifest_status={report.manifest_status}",
                f"manifest_path={report.manifest_path}",
                explain_status_mismatch(identity, report),
            ]
            if gate == "preflight":
                # A successful manifest from a different scope route means the current
                # request still needs to run. It must not block the run before the
                # formal stage has a chance to create matching artifacts.
                return report.to_dict()
            add_issue(
                "stage_identity_contract_mismatch",
                "blocking",
                f"{stage} manifest 已成功生成，但与当前 scope identity 契约不匹配。",
                "检查并补齐 manifest 的 scope_id、analysis_route、member_set_id/stage_context；不要把该问题误判为未生成输出。",
                requires_confirmation=True,
                safe_patch_candidate=True,
                blocks_downstream=True,
                evidence_items=evidence_items,
            )
            return report.to_dict()
        except Exception as exc:
            return {"status": "identity_status_unavailable", "error": str(exc)}

    def _stage_context_from_params(self, stage: str, prefix: str, params: Dict[str, Any]) -> Dict[str, Any]:
        raw = params.get("stageContext") or params.get("stage_context")
        if isinstance(raw, dict):
            return raw
        try:
            from gfa_core.gene_family_stage_context import build_stage_context, scope_data_from_params

            scope_data = self._scope_from_params(params) or scope_data_from_params(prefix, params)
            context = build_stage_context(
                stage=stage,
                prefix=prefix,
                scope_data=scope_data,
                scope_file=params.get("scopeFile") or params.get("scope_file") or "",
                analysis_route=params.get("analysisRoute") or params.get("analysis_route") or "",
                member_set_id=params.get("memberSetId") or params.get("member_set_id") or "",
            )
            return context.to_dict()
        except Exception:
            return {}

    def prepare_repair_workspace(
        self,
        job_id: str,
        stage: str,
        issue: Dict[str, Any],
        project_root: Path = PROJECT_ROOT,
    ) -> Dict[str, Any]:
        """为当前 job 准备修复副本目录，不修改副本内容、不执行副本。

        后续若用户确认，执行层可以在该副本上应用最小 patch 并用同样输入/输出契约测试。
        """
        script_name = STAGE_SCRIPT_MAP.get(stage)
        if not script_name:
            return {"success": False, "error": f"No script mapping for stage: {stage}"}
        original_script = project_root / "cli" / script_name
        if not original_script.exists():
            return {"success": False, "error": f"Original script not found: {original_script}"}

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        patch_dir = project_root / "workspace" / job_id / "reproducibility" / "patches" / stage / ts
        patch_dir.mkdir(parents=True, exist_ok=True)
        patched_script = patch_dir / f"{original_script.stem}.patched{original_script.suffix}"
        shutil.copy2(original_script, patched_script)
        report = {
            "stage": stage,
            "job_id": job_id,
            "created_at": datetime.now().isoformat(),
            "repair_type": "contract_preserving_job_patch_workspace",
            "issue": issue,
            "original_script": str(original_script),
            "patched_script": str(patched_script),
            "input_changed": False,
            "output_contract_changed": False,
            "analysis_parameters_changed": False,
            "global_code_changed": False,
            "requires_user_approval_before_execution": True,
        }
        report_path = patch_dir / "patch_report.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return {
            "success": True,
            "patch_dir": str(patch_dir),
            "patched_script": str(patched_script),
            "patch_report": str(report_path),
        }

    def apply_confirmed_fix(
        self,
        job_id: str,
        stage: str,
        prefix: str = "",
        diagnosis: Optional[Dict[str, Any]] = None,
        fix_data: Optional[Dict[str, Any]] = None,
        project_root: Path = PROJECT_ROOT,
    ) -> Dict[str, Any]:
        """用户确认后应用 job 级修复副本流程。

        默认只准备副本和 patch 报告；只有 fix_data.execute_patch 为 True 时才执行。
        执行时必须使用 patched script，且输入/输出路径由原命令或用户确认命令保持不变。
        """
        fix_data = fix_data or {}
        diagnosis = diagnosis or self.diagnose_job_stage(job_id, stage, prefix, project_root)
        action = fix_data.get("action") or "prepare_job_patch"
        issues = diagnosis.get("issues", [])
        issue_code = fix_data.get("issue_code")
        issue = None
        for item in issues:
            if issue_code and item.get("code") == issue_code:
                issue = item
                break
            if not issue_code and item.get("safe_patch_candidate") and item.get("code") in KNOWN_COPY_PATCH_CODES:
                issue = item
                break
        if issue is None:
            return {
                "success": False,
                "status": "blocked",
                "message": "没有可安全副本修复的问题；需要用户手动处理或补充确认信息。",
                "diagnosis": diagnosis,
            }

        if issue.get("code") == "protein_fasta_stop_markers":
            return self._apply_protein_stop_marker_fix(
                job_id=job_id,
                stage=stage,
                prefix=prefix,
                issue=issue,
                project_root=project_root,
            )

        patch_ws = self.prepare_repair_workspace(job_id, stage, issue, project_root)
        if not patch_ws.get("success"):
            return patch_ws

        patch_result = self._apply_known_patch_to_copy(
            issue_code=issue.get("code", ""),
            patched_script=Path(patch_ws["patched_script"]),
        )
        patch_ws["patch_application"] = patch_result
        self._update_patch_report(Path(patch_ws["patch_report"]), {"patch_application": patch_result})

        if action == "prepare_job_patch" or not fix_data.get("execute_patch", False):
            return {
                "success": True,
                "status": "prepared",
                "message": "已准备当前 job 的修复副本；尚未执行。",
                "patch_workspace": patch_ws,
                "requires_user_confirmation_before_execution": True,
            }

        if self.is_stage_running(job_id, stage):
            return {
                "success": False,
                "status": "running_stage_blocked",
                "message": f"{job_id}/{stage} 当前仍在运行，拒绝执行修复副本以避免覆盖正在生成的产物。",
                "patch_workspace": patch_ws,
            }

        cmd_result = self._build_patched_command(
            job_id=job_id,
            stage=stage,
            prefix=prefix,
            patched_script=Path(patch_ws["patched_script"]),
            fix_data=fix_data,
            project_root=project_root,
        )
        if not cmd_result.get("success"):
            self._update_patch_report(Path(patch_ws["patch_report"]), {"execution": cmd_result})
            return {
                "success": False,
                "status": "needs_command",
                "message": cmd_result.get("message", "无法构建修复副本执行命令"),
                "patch_workspace": patch_ws,
            }

        backup = self._backup_failed_run(job_id, stage, prefix, project_root)
        execution = self._execute_patched_command(
            cmd=cmd_result["cmd"],
            cwd=project_root,
            timeout=int(fix_data.get("timeout", 3600)),
        )
        recovery = {
            "used_patched_script": True,
            "patched_script": patch_ws["patched_script"],
            "original_script": str(project_root / "cli" / STAGE_SCRIPT_MAP.get(stage, "")),
            "patch_report": patch_ws["patch_report"],
            "reason": issue.get("message"),
            "issue_code": issue.get("code"),
            "contract_preserved": True,
            "input_changed": False,
            "output_contract_changed": False,
            "analysis_parameters_changed": False,
            "backup": backup,
            "executed_at": datetime.now().isoformat(),
        }
        manifest_update = self._annotate_recovery_manifest(job_id, stage, prefix, recovery, project_root)
        execution_record = {
            "command": cmd_result["cmd"],
            "execution": execution,
            "recovery": recovery,
            "manifest_update": manifest_update,
        }
        self._update_patch_report(Path(patch_ws["patch_report"]), {"execution": execution_record})
        return {
            "success": execution.get("returncode") == 0,
            "status": "executed" if execution.get("returncode") == 0 else "execution_failed",
            "message": "修复副本执行完成" if execution.get("returncode") == 0 else "修复副本执行失败",
            "patch_workspace": patch_ws,
            **execution_record,
        }

    def is_stage_running(self, job_id: str, stage: str) -> bool:
        """检查同一 job/stage 是否有正在运行的命令。"""
        try:
            proc = subprocess.run(
                ["ps", "-eo", "pid,ppid,cmd"],
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
        except Exception:
            return False
        script = STAGE_SCRIPT_MAP.get(stage, stage)
        for line in proc.stdout.splitlines():
            if job_id in line and script in line:
                if "ps -eo" not in line and "rg " not in line:
                    return True
        return False

    def _scope_id_from_params(self, params: Dict[str, Any]) -> str:
        raw = str(params.get("scope_id") or params.get("scopeId") or "").strip()
        if raw:
            return raw
        scope_file = params.get("scopeFile") or params.get("scope_file")
        if not scope_file:
            return ""
        try:
            path = Path(str(scope_file))
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            data = json.loads(path.read_text(encoding="utf-8"))
            return str(data.get("scope_id") or "").strip()
        except Exception:
            return ""

    def _scope_from_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        scope_file = params.get("scopeFile") or params.get("scope_file")
        if not scope_file:
            return {}
        try:
            path = Path(str(scope_file))
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _manifest_path_value(self, raw: Any, final_dir: Path, manifest: Optional[Dict[str, Any]]) -> Optional[Path]:
        if not raw:
            return None
        p = Path(str(raw))
        if p.is_absolute():
            return p
        workspace = (manifest or {}).get("workspace_root") or (manifest or {}).get("workspace")
        if workspace:
            candidate = Path(str(workspace)) / p
            if candidate.exists():
                return candidate
        if str(p).startswith("final/"):
            return final_dir / p.name
        return PROJECT_ROOT / p

    def _artifact_path_from_manifest(self, manifest: Optional[Dict[str, Any]], final_dir: Path, keys: List[str]) -> Optional[Path]:
        artifacts = (manifest or {}).get("artifacts", {})
        if not isinstance(artifacts, dict):
            return None
        for key in keys:
            artifact = artifacts.get(key)
            raw = None
            if isinstance(artifact, dict):
                raw = artifact.get("full_path") or artifact.get("path") or artifact.get("relpath_or_hint") or artifact.get("file")
            elif isinstance(artifact, str):
                raw = artifact
            path = self._manifest_path_value(raw, final_dir, manifest)
            if path and path.exists() and path.stat().st_size > 0:
                return path
        return None

    def _clean_fasta_candidate(self, protein_fasta: Path) -> Path:
        clean_name = re.sub(r"(\.fa(?:sta)?)$", r".clean\1", protein_fasta.name, flags=re.IGNORECASE)
        if clean_name == protein_fasta.name:
            clean_name = protein_fasta.name + ".clean.fa"
        return protein_fasta.with_name(clean_name)

    def _protein_fasta_for_prefix(self, final_dir: Path, prefix: str, manifest: Optional[Dict[str, Any]]) -> Optional[Path]:
        candidates: List[Path] = []
        params = (manifest or {}).get("parameters", {})
        if isinstance(params, dict):
            for key in ("protein_fasta", "protein_source", "scope_input_fasta", "proteins", "fasta"):
                path = self._manifest_path_value(params.get(key), final_dir, manifest)
                if path:
                    clean_path = self._clean_fasta_candidate(path)
                    if clean_path.exists() and clean_path.stat().st_mtime >= path.stat().st_mtime:
                        candidates.append(clean_path)
                    candidates.append(path)
        artifacts = (manifest or {}).get("artifacts", {})
        if isinstance(artifacts, dict):
            for key in ("final_members", "final_proteins", "protein_fasta", "proteins", "scope_input_fasta", "input_fasta"):
                artifact = artifacts.get(key)
                if isinstance(artifact, dict):
                    raw = artifact.get("full_path") or artifact.get("path") or artifact.get("relpath_or_hint")
                    if raw:
                        p = self._manifest_path_value(raw, final_dir, manifest)
                        if p:
                            candidates.append(p)
                elif isinstance(artifact, str):
                    p = self._manifest_path_value(artifact, final_dir, manifest)
                    if p:
                        candidates.append(p)
        if prefix:
            candidates.append(final_dir / f"{prefix}.final_members.pep.clean.fa")
            candidates.append(final_dir / f"{prefix}.final_members.pep.fa")
            candidates.append(final_dir / f"{prefix}.final_members.pep.fasta")
        candidates.extend(sorted(final_dir.glob("*final_members.pep.clean.fa")))
        candidates.extend(sorted(final_dir.glob("*final_members.pep.fa")))
        for path in candidates:
            if path.exists() and path.is_file() and path.stat().st_size > 0:
                return path
        return None

    def _protein_stop_marker_stats(self, protein_fasta: Path) -> Dict[str, Any]:
        stats = {
            "protein_fasta": str(protein_fasta),
            "sequence_count": 0,
            "sequences_with_stop_markers": 0,
            "stop_marker_count": 0,
        }
        current_changed = False
        try:
            with open(protein_fasta, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if line.startswith(">"):
                        if stats["sequence_count"] and current_changed:
                            stats["sequences_with_stop_markers"] += 1
                        stats["sequence_count"] += 1
                        current_changed = False
                        continue
                    count = line.count("*")
                    if count:
                        stats["stop_marker_count"] += count
                        current_changed = True
                if stats["sequence_count"] and current_changed:
                    stats["sequences_with_stop_markers"] += 1
        except Exception as exc:
            stats["error"] = str(exc)
        return stats

    def _write_clean_protein_fasta(self, source: Path, clean_fasta: Path) -> Dict[str, Any]:
        stats = self._protein_stop_marker_stats(source)
        clean_fasta.parent.mkdir(parents=True, exist_ok=True)
        with open(source, "r", encoding="utf-8", errors="ignore") as src, open(clean_fasta, "w", encoding="utf-8") as dst:
            for line in src:
                dst.write(line if line.startswith(">") else line.replace("*", ""))
        stats.update({
            "clean_fasta": str(clean_fasta),
            "created_at": datetime.now().isoformat(),
            "policy": "job-local derived FASTA; original final_members.pep.fa is unchanged",
        })
        clean_fasta.with_suffix(clean_fasta.suffix + ".repair.json").write_text(
            json.dumps(stats, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return stats

    def _normalize_gene_id_for_match(self, value: str) -> str:
        text = str(value or "").strip()
        if "|" in text:
            text = text.split("|")[-1]
        for prefix in ("gene:", "transcript:", "CDS:", "mRNA:"):
            if text.startswith(prefix):
                text = text[len(prefix):]
        text = re.sub(r"_[PT]\d+$", "", text)
        text = re.sub(r"\.\d+$", "", text)
        return text.upper()

    def _read_final_member_ids(self, final_dir: Path, prefix: str, manifest: Optional[Dict[str, Any]] = None) -> List[str]:
        candidates = []
        params = (manifest or {}).get("parameters", {})
        if isinstance(params, dict):
            for key in ("gene_id_file", "member_id_file"):
                path = self._manifest_path_value(params.get(key), final_dir, manifest)
                if path:
                    candidates.append(path)
        artifact_path = self._artifact_path_from_manifest(
            manifest,
            final_dir,
            ["final_members_id", "member_ids", "gene_ids"],
        )
        if artifact_path:
            candidates.append(artifact_path)
        if prefix:
            candidates.append(final_dir / f"{prefix}.final_members.id")
        candidates.extend(sorted(final_dir.glob("*final_members.id")))
        for path in candidates:
            if path.exists() and path.stat().st_size > 0:
                return [line.strip() for line in path.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip()]
        return []

    def _scope_gff_paths(self, params: Dict[str, Any]) -> List[Path]:
        scope = self._scope_from_params(params)
        if not scope:
            return []
        wanted = set(scope.get("selected_species") or scope.get("target_species") or [])
        paths: List[Path] = []
        files = scope.get("files") if isinstance(scope.get("files"), dict) else {}
        for species, by_type in files.items():
            if wanted and species not in wanted:
                continue
            if not isinstance(by_type, dict):
                continue
            for item in by_type.get("gff", []) or []:
                if not isinstance(item, dict):
                    continue
                raw = item.get("path")
                if not raw:
                    continue
                p = Path(str(raw))
                if not p.is_absolute():
                    p = PROJECT_ROOT / p
                if p.exists() and p.stat().st_size > 0:
                    paths.append(p)
        return paths

    def _find_job_gff3(self, final_dir: Path, prefix: str, params: Optional[Dict[str, Any]] = None) -> Optional[Path]:
        if isinstance(params, dict):
            for key in ("gff3_source", "gff3", "gff_file"):
                raw = params.get(key)
                if raw:
                    path = Path(str(raw))
                    if not path.is_absolute():
                        path = PROJECT_ROOT / path
                    if path.exists() and path.stat().st_size > 0:
                        return path
            gff3_sources = params.get("gff3_sources")
            if isinstance(gff3_sources, list):
                for item in gff3_sources:
                    if not isinstance(item, dict):
                        continue
                    raw = item.get("path")
                    if not raw:
                        continue
                    path = Path(str(raw))
                    if not path.is_absolute():
                        path = PROJECT_ROOT / path
                    if path.exists() and path.stat().st_size > 0:
                        return path
        scoped_paths = self._scope_gff_paths(params or {})
        if scoped_paths:
            return scoped_paths[0]
        job_id = final_dir.parent.name
        species = prefix.split(".", 1)[0] if prefix and "." in prefix else prefix
        candidates: List[Path] = []
        if species:
            candidates.extend([
                final_dir / f"{species}_final.gff3",
                final_dir.parent / "input" / f"{species}_final.gff3",
                PROJECT_ROOT / "input" / job_id / f"{species}_final.gff3",
            ])
        candidates.extend(sorted(final_dir.glob("*.gff3")))
        candidates.extend(sorted((PROJECT_ROOT / "input" / job_id).glob("*.gff3")) if (PROJECT_ROOT / "input" / job_id).exists() else [])
        for path in candidates:
            if path.exists() and path.stat().st_size > 0:
                return path
        return None

    def _read_gff_gene_ids(self, gff3: Path, limit: int = 500000) -> set[str]:
        ids: set[str] = set()
        opener = gzip.open if str(gff3).lower().endswith(".gz") else open
        with opener(gff3, "rt", encoding="utf-8", errors="ignore") as handle:
            for idx, line in enumerate(handle):
                if idx > limit:
                    break
                if not line.strip() or line.startswith("#"):
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 9:
                    continue
                if parts[2] not in {"gene", "mRNA", "transcript", "CDS"}:
                    continue
                attrs = parts[8]
                for item in attrs.split(";"):
                    if item.startswith("ID=") or item.startswith("Parent="):
                        raw = item.split("=", 1)[1].split(",", 1)[0]
                        norm = self._normalize_gene_id_for_match(raw)
                        if norm:
                            ids.add(norm)
        return ids

    def _apply_protein_stop_marker_fix(
        self,
        job_id: str,
        stage: str,
        prefix: str,
        issue: Dict[str, Any],
        project_root: Path = PROJECT_ROOT,
    ) -> Dict[str, Any]:
        workspace = project_root / "workspace" / job_id
        final_dir = workspace / "final"
        manifest = self._load_stage_manifest(final_dir, stage, prefix)
        payload = self._issue_payload(issue)
        protein_fasta = Path(str(payload.get("protein_fasta"))).expanduser() if payload.get("protein_fasta") else None
        if protein_fasta and not protein_fasta.is_absolute():
            protein_fasta = project_root / protein_fasta
        if not protein_fasta or not protein_fasta.exists():
            protein_fasta = self._protein_fasta_for_prefix(final_dir, prefix, manifest)
        if not protein_fasta:
            return {
                "success": False,
                "status": "blocked",
                "message": f"未找到可清理的蛋白 FASTA：final/{prefix}.final_members.pep.fa",
                "issue": issue,
            }
        if protein_fasta.parent != final_dir:
            clean_fasta = self._clean_fasta_candidate(protein_fasta)
        else:
            clean_fasta = final_dir / f"{prefix or protein_fasta.stem}.final_members.pep.clean.fa"
        repair = self._write_clean_protein_fasta(protein_fasta, clean_fasta)
        return {
            "success": True,
            "status": "prepared",
            "message": "已生成去除 '*' stop marker 的 job-local 蛋白 FASTA。后续 phylogeny/motif/domain/pep 将优先使用该文件。",
            "issue_code": issue.get("code"),
            "repair": repair,
            "input_changed": False,
            "global_code_changed": False,
            "requires_user_confirmation_before_execution": False,
        }

    def _repair_backup_path(self, path: Path) -> Optional[Path]:
        if not path.exists():
            return None
        backup_dir = path.parent / "diagnosis_repair_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{path.name}"
        shutil.copy2(path, backup)
        return backup

    def _move_to_repair_backup(self, path: Path) -> Optional[Path]:
        """Move an invalid input out of the way before writing a replacement.

        Moving is intentional here: sequence-repair outputs may be symlinks to
        large source files. Writing through the symlink would modify the source.
        """
        if not path.exists() and not path.is_symlink():
            return None
        backup_dir = path.parent / "diagnosis_repair_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{path.name}"
        shutil.move(str(path), str(backup))
        return backup

    def _issue_payload(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        for item in issue.get("evidence", []):
            if not isinstance(item, str):
                continue
            text = item.strip()
            if text.startswith("{") and text.endswith("}"):
                try:
                    return json.loads(text)
                except Exception:
                    continue
        return {}

    def _write_gene_gff_overlap_report(
        self,
        job_id: str,
        stage: str,
        prefix: str,
        issue: Dict[str, Any],
        project_root: Path = PROJECT_ROOT,
    ) -> Dict[str, Any]:
        payload = self._issue_payload(issue)
        workspace = project_root / "workspace" / job_id
        report_dir = workspace / "final" / "diagnosis_reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"{prefix or stage}.{stage}.gene_gff_id_overlap.report.json"
        report = {
            "issue_code": issue.get("code"),
            "stage": stage,
            "prefix": prefix,
            "created_at": datetime.now().isoformat(),
            "diagnosis": payload or issue,
            "policy": "job-local report only; no gene IDs or GFF attributes were modified",
            "next_action": "Review unmatched ID examples and choose a mapping rule before changing IDs.",
        }
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return {
            "success": True,
            "changed": False,
            "status": "reported",
            "issue_code": issue.get("code"),
            "message": "已生成 GFF/成员 ID 重叠诊断报告；该问题不能静默改 ID。",
            "report": str(report_path),
            "global_code_changed": False,
        }

    def _repair_identify_local_pfam_hmm(
        self,
        job_id: str,
        prefix: str,
        issue: Dict[str, Any],
        project_root: Path = PROJECT_ROOT,
    ) -> Dict[str, Any]:
        """Prepare a job-local query HMM from config/family mapping/local Pfam resources."""
        input_dir = project_root / "input" / job_id
        workspace = project_root / "workspace" / job_id
        if not input_dir.exists():
            return {
                "success": False,
                "changed": False,
                "status": "blocked",
                "issue_code": issue.get("code"),
                "message": f"input directory not found: {input_dir}",
            }
        try:
            from cli.run_identify import (
                _pfam_alias_to_accession,
                build_required_pfam_hmm,
                read_analysis_family,
                read_required_pfams_from_config,
            )
        except Exception as exc:
            return {
                "success": False,
                "changed": False,
                "status": "blocked",
                "issue_code": issue.get("code"),
                "message": f"无法导入 identify Pfam fallback 工具: {exc}",
            }

        payload = self._issue_payload(issue)
        required_pfams = str(
            payload.get("required_pfams")
            or payload.get("requiredPfams")
            or payload.get("pfam")
            or payload.get("pfams")
            or ""
        ).strip()
        source = "issue_evidence"
        if not required_pfams:
            required_pfams = read_required_pfams_from_config(input_dir, workspace)
            source = "config"

        family = ""
        if not required_pfams:
            parts = [part for part in str(prefix or "").split(".") if part]
            if len(parts) >= 2:
                family = parts[-1]
            elif parts and parts[0].lower() not in {"multi", "target", "reference", "comparative"}:
                family = parts[0]
            if not family:
                family = read_analysis_family(input_dir, workspace)
            mapped = _pfam_alias_to_accession(family) if family else None
            if mapped:
                required_pfams = mapped
                source = f"family_alias:{family}"

        if not required_pfams:
            return {
                "success": False,
                "changed": False,
                "status": "blocked",
                "issue_code": issue.get("code"),
                "message": "未能从 issue/config/prefix 推断 Required_Pfams；请在 config.xlsx 的 Analysis.Required_Pfams 填写 PFxxxxx，或上传 HMM。",
            }

        cache_dir = input_dir / "genome_prepare" / "hmm_cache"
        try:
            hmm_path, queries, fetch_map = build_required_pfam_hmm(required_pfams, None, cache_dir)
        except Exception as exc:
            return {
                "success": False,
                "changed": False,
                "status": "blocked",
                "issue_code": issue.get("code"),
                "required_pfams": required_pfams,
                "source": source,
                "message": f"本地 Pfam/HMM fallback 失败: {exc}",
            }

        record = {
            "issue_code": issue.get("code"),
            "created_at": datetime.now().isoformat(),
            "job_id": job_id,
            "prefix": prefix,
            "required_pfams": required_pfams,
            "queries": queries,
            "fetch_map": fetch_map,
            "source": source,
            "query_hmm": str(hmm_path),
            "global_code_changed": False,
            "next_action": "rerun identify with the same job; run_identify.py will discover this HMM cache before remote lookup.",
        }
        record_path = cache_dir / f"{Path(hmm_path).stem}.diagnosis_repair.json"
        record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        return {
            "success": True,
            "changed": True,
            "status": "prepared",
            "issue_code": issue.get("code"),
            "message": "已基于 config/family/Pfam 本地资源生成 identify 可用的 job-local 查询 HMM。",
            "required_pfams": required_pfams,
            "query_hmm": str(hmm_path),
            "repair_record": str(record_path),
            "input_changed": True,
            "global_code_changed": False,
            "should_rerun_stage": True,
            "requires_user_confirmation_before_execution": False,
        }

    def _repair_ncbi_family_reference_link(
        self,
        job_id: str,
        issue: Dict[str, Any],
        project_root: Path = PROJECT_ROOT,
    ) -> Dict[str, Any]:
        payload = self._issue_payload(issue)
        protein_fasta = Path(str(payload.get("protein_fasta") or "")).expanduser()
        expected = Path(str(payload.get("expected_reference_pep") or "")).expanduser()
        if protein_fasta and not protein_fasta.is_absolute():
            protein_fasta = project_root / protein_fasta
        if expected and not expected.is_absolute():
            expected = project_root / expected
        if not protein_fasta.exists() or protein_fasta.stat().st_size == 0:
            return {
                "success": False,
                "changed": False,
                "status": "blocked",
                "issue_code": issue.get("code"),
                "message": f"NCBI protein FASTA not found or empty: {protein_fasta}",
            }
        if not expected:
            parts = protein_fasta.name.split(".")
            if len(parts) < 4:
                return {
                    "success": False,
                    "changed": False,
                    "status": "blocked",
                    "issue_code": issue.get("code"),
                    "message": f"无法从文件名推断 Species/Family: {protein_fasta.name}",
                }
            expected = protein_fasta.parent / f"{parts[0]}.{parts[1]}.pep.fa"
        expected.parent.mkdir(parents=True, exist_ok=True)
        if expected.exists() and expected.stat().st_size > 0:
            return {
                "success": True,
                "changed": False,
                "status": "already_exists",
                "issue_code": issue.get("code"),
                "message": f"标准家族参考蛋白已存在: {expected}",
                "reference_pep": str(expected),
            }
        try:
            if expected.exists() or expected.is_symlink():
                expected.unlink()
            os.symlink(protein_fasta.name, expected)
            link_type = "symlink"
        except Exception:
            shutil.copy2(protein_fasta, expected)
            link_type = "copy"
        record = {
            "issue_code": issue.get("code"),
            "created_at": datetime.now().isoformat(),
            "job_id": job_id,
            "protein_fasta": str(protein_fasta),
            "reference_pep": str(expected),
            "link_type": link_type,
            "global_code_changed": False,
        }
        record_path = expected.with_suffix(expected.suffix + ".diagnosis_repair.json")
        record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        return {
            "success": True,
            "changed": True,
            "status": "prepared",
            "issue_code": issue.get("code"),
            "message": f"已创建标准家族参考蛋白 {expected.name} ({link_type})。",
            "protein_fasta": str(protein_fasta),
            "reference_pep": str(expected),
            "repair_record": str(record_path),
            "input_changed": False,
            "global_code_changed": False,
            "requires_user_confirmation_before_execution": False,
        }

    def _repair_missing_manifest_from_artifacts(
        self,
        job_id: str,
        stage: str,
        prefix: str,
        issue: Dict[str, Any],
        project_root: Path = PROJECT_ROOT,
    ) -> Dict[str, Any]:
        """Recover a minimal success manifest only when required final artifacts exist."""
        workspace = project_root / "workspace" / job_id
        final_dir = workspace / "final"
        final_dir.mkdir(parents=True, exist_ok=True)
        manifest_stage = {"ncbi_fetch": "ncbi_fetch_sequences"}.get(stage, stage)
        manifest_alias = "ncbi_fetch" if manifest_stage == "ncbi_fetch_sequences" else manifest_stage
        manifest_path = final_dir / f"{prefix}.{manifest_alias}.manifest.json" if prefix else final_dir / f"{manifest_alias}.manifest.json"
        if manifest_path.exists() and manifest_path.stat().st_size > 0:
            return {
                "success": True,
                "changed": False,
                "status": "already_exists",
                "issue_code": issue.get("code"),
                "message": f"Manifest 已存在: {manifest_path}",
                "manifest_path": str(manifest_path),
            }
        if self.is_stage_running(job_id, manifest_stage):
            return {
                "success": False,
                "changed": False,
                "status": "running_stage_blocked",
                "issue_code": issue.get("code"),
                "message": f"{job_id}/{manifest_stage} 当前仍在运行，拒绝恢复 manifest。",
            }

        if manifest_stage == "identify":
            manifest = self._build_recovered_identify_manifest(job_id, prefix, workspace, final_dir)
        elif manifest_stage == "ncbi_fetch_sequences":
            manifest = self._build_recovered_ncbi_fetch_manifest(job_id, prefix, workspace, final_dir)
        else:
            return {
                "success": False,
                "changed": False,
                "status": "unsupported_stage",
                "issue_code": issue.get("code"),
                "message": f"暂不支持 {manifest_stage} 的 manifest 自动恢复；需要先定义该阶段核心产物契约。",
            }

        if not manifest:
            return {
                "success": False,
                "changed": False,
                "status": "insufficient_artifacts",
                "issue_code": issue.get("code"),
                "message": f"{manifest_stage} final 核心产物不足，不能安全恢复 manifest。",
            }

        manifest["recovered_manifest"] = {
            "recovered": True,
            "issue_code": issue.get("code"),
            "created_at": datetime.now().isoformat(),
            "policy": "job-local manifest recovery from verified non-empty final artifacts",
            "global_code_changed": False,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return {
            "success": True,
            "changed": True,
            "status": "recovered",
            "issue_code": issue.get("code"),
            "message": f"已根据 final 核心产物恢复 {manifest_stage} manifest。",
            "manifest_path": str(manifest_path),
            "input_changed": False,
            "global_code_changed": False,
            "requires_user_confirmation_before_execution": False,
        }

    def _repair_ortholog_materialize_from_intermediate(
        self,
        job_id: str,
        prefix: str,
        issue: Dict[str, Any],
        project_root: Path = PROJECT_ROOT,
    ) -> Dict[str, Any]:
        """Reuse ortholog/*_pairs.txt and run backend materialization without rerunning BLAST."""
        if self.is_stage_running(job_id, "ortholog"):
            return {
                "success": False,
                "changed": False,
                "status": "running_stage_blocked",
                "issue_code": issue.get("code"),
                "message": f"{job_id}/ortholog 当前仍在运行，拒绝执行 intermediate 落盘修复。",
            }

        workspace = project_root / "workspace" / job_id
        final_dir = workspace / "final"
        ortholog_dir = workspace / "ortholog"
        from gfa_core.ortholog_materialize import ortholog_pairs_has_data

        intermediate_pairs = [path for path in sorted(ortholog_dir.glob("*_pairs.txt")) if ortholog_pairs_has_data(path)] if ortholog_dir.exists() else []
        if not intermediate_pairs:
            return {
                "success": False,
                "changed": False,
                "status": "insufficient_artifacts",
                "issue_code": issue.get("code"),
                "message": "ortholog/ 下没有可复用的 *_pairs.txt，无法执行 intermediate 落盘修复。",
            }

        scope_file = ""
        if prefix:
            scope_candidates = sorted(final_dir.glob(f"{prefix}.*.scope.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            for candidate in scope_candidates:
                try:
                    scope_data = json.loads(candidate.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if scope_data.get("reference_species") or scope_data.get("target_species"):
                    scope_file = str(candidate)
                    break

        try:
            from apps.web.pipeline_backend import JobInfo, run_stage

            job = JobInfo(
                job_id=job_id,
                input_dir=str(project_root / "input" / job_id),
                workspace_dir=str(workspace),
                logs_dir=str(workspace / "logs"),
                created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            msg, manifest_path = run_stage(
                stage="ortholog",
                job=job,
                prefix=prefix,
                scope_file=scope_file,
            )
        except Exception as exc:
            return {
                "success": False,
                "changed": False,
                "status": "repair_failed",
                "issue_code": issue.get("code"),
                "message": f"ortholog intermediate 落盘修复失败: {exc}",
            }

        if not manifest_path or not Path(manifest_path).exists() or str(msg).startswith("❌") or str(msg).startswith("⚠️"):
            return {
                "success": False,
                "changed": False,
                "status": "repair_failed",
                "issue_code": issue.get("code"),
                "message": msg or "ortholog intermediate 落盘修复未生成 manifest",
            }

        return {
            "success": True,
            "changed": True,
            "status": "recovered",
            "issue_code": issue.get("code"),
            "message": msg,
            "manifest_path": str(manifest_path),
            "input_changed": False,
            "global_code_changed": False,
            "requires_user_confirmation_before_execution": False,
            "should_rerun_stage": False,
        }

    def _build_recovered_identify_manifest(
        self,
        job_id: str,
        prefix: str,
        workspace: Path,
        final_dir: Path,
    ) -> Optional[Dict[str, Any]]:
        if not prefix:
            return None
        pep = final_dir / f"{prefix}.final_members.pep.fa"
        ids = final_dir / f"{prefix}.final_members.id"
        if not pep.exists() or pep.stat().st_size == 0 or not ids.exists() or ids.stat().st_size == 0:
            return None
        sequence_count = self._count_fasta_records(pep)
        id_count = self._count_nonempty_lines(ids)
        if sequence_count <= 0 or id_count <= 0:
            return None
        artifacts = {
            "final_members": self._manifest_artifact(workspace, pep, "fasta", artifact="final_members"),
            "final_members_id": self._manifest_artifact(workspace, ids, "id_list", artifact="final_members_id"),
        }
        interpro = final_dir / f"{prefix}.final_members.interproscan.tsv"
        interpro_status = final_dir / f"{prefix}.interproscan_domain_check.txt"
        interpro_input = final_dir / f"{prefix}.final_members.interproscan.input.pep.fa"
        if interpro.exists() and interpro.stat().st_size > 0:
            artifacts["interproscan_domain_check"] = self._manifest_artifact(workspace, interpro, "tsv", artifact="interproscan_domain_check")
        if interpro_status.exists() and interpro_status.stat().st_size > 0:
            artifacts["interproscan_domain_check_status"] = self._manifest_artifact(workspace, interpro_status, "text", artifact="interproscan_domain_check_status")
        if interpro_input.exists() and interpro_input.stat().st_size > 0:
            artifacts["interproscan_clean_input"] = self._manifest_artifact(workspace, interpro_input, "fasta", artifact="interproscan_clean_input")
        species_results = []
        for species_pep in sorted((workspace / "identify").glob("*/final/*.final_gfmembers.pep.fa")):
            species = species_pep.name.split(".", 1)[0]
            members = self._count_fasta_records(species_pep)
            species_results.append({
                "species": species,
                "file_prefix": species,
                "input_file": "",
                "final_members": members,
                "final_pep_file": self._relpath(workspace, species_pep),
            })
            artifacts[f"{species}_final_members"] = self._manifest_artifact(
                workspace,
                species_pep,
                "fasta",
                artifact=f"{species}_final_members",
                extra={"species": species, "members": members},
            )
        return {
            "manifest_version": "1.0",
            "stage": "identify",
            "status": "success",
            "prefix": prefix,
            "job_id": job_id,
            "timestamp": datetime.now().isoformat(),
            "created_at": datetime.now().isoformat(),
            "completed_at": datetime.now().isoformat(),
            "workspace_root": str(workspace.resolve()),
            "workspace": str(final_dir.resolve()),
            "species_count": len(species_results) or 1,
            "species_results": species_results,
            "metrics": {
                "final_members": sequence_count,
                "final_member_ids": id_count,
                "sequence_count": sequence_count,
            },
            "artifacts": artifacts,
            "warnings": [
                "Manifest was recovered from final artifacts; original run parameters and elapsed time are unavailable."
            ],
        }

    def _build_recovered_ncbi_fetch_manifest(
        self,
        job_id: str,
        prefix: str,
        workspace: Path,
        final_dir: Path,
    ) -> Optional[Dict[str, Any]]:
        if not prefix:
            return None
        protein = final_dir / f"{prefix}.ncbi_fetch.protein.fa"
        cds = final_dir / f"{prefix}.ncbi_fetch.cds.fa"
        mapping = final_dir / f"{prefix}.ncbi_fetch.mapping.tsv"
        if not any(path.exists() and path.stat().st_size > 0 for path in [protein, cds, mapping]):
            return None
        artifacts: Dict[str, Any] = {}
        if mapping.exists() and mapping.stat().st_size > 0:
            artifacts["mapping"] = self._manifest_artifact(workspace, mapping, "tsv", artifact="mapping")
        for key, suffix, typ in [
            ("unresolved", "unresolved.tsv", "tsv"),
            ("ambiguous", "ambiguous.tsv", "tsv"),
            ("report", "report.md", "markdown"),
        ]:
            path = final_dir / f"{prefix}.ncbi_fetch.{suffix}"
            if path.exists() and path.stat().st_size > 0:
                artifacts[key] = self._manifest_artifact(workspace, path, typ, artifact=key)
        protein_records = self._count_fasta_records(protein) if protein.exists() else 0
        cds_records = self._count_fasta_records(cds) if cds.exists() else 0
        if protein.exists() and protein.stat().st_size > 0:
            artifacts["protein_fasta"] = self._manifest_artifact(workspace, protein, "fasta", artifact="protein_fasta")
        if cds.exists() and cds.stat().st_size > 0:
            artifacts["cds_fasta"] = self._manifest_artifact(workspace, cds, "fasta", artifact="cds_fasta")
        family_ref = final_dir / f"{prefix}.pep.fa"
        if family_ref.exists() and family_ref.stat().st_size > 0:
            artifacts["family_reference_pep"] = self._manifest_artifact(workspace, family_ref, "fasta", artifact="family_reference_pep")
        status = "success" if protein_records or cds_records else "partial_success"
        return {
            "manifest_version": "1.0",
            "stage": "ncbi_fetch_sequences",
            "status": status,
            "prefix": prefix,
            "job_id": job_id,
            "created_at": datetime.now().isoformat(),
            "completed_at": datetime.now().isoformat(),
            "parameters": {},
            "metrics": {
                "protein_records": protein_records,
                "cds_records": cds_records,
                "mapping_rows": max(self._count_nonempty_lines(mapping) - 1, 0) if mapping.exists() else 0,
            },
            "artifacts": artifacts,
            "warnings": [
                "Manifest was recovered from final artifacts; original NCBI query parameters and elapsed time are unavailable."
            ],
        }

    def _manifest_artifact(self, workspace: Path, path: Path, typ: str, *, artifact: str = "", extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        record = {
            "artifact": artifact or path.stem,
            "file": path.name,
            "path": self._relpath(workspace, path),
            "full_path": str(path.resolve()),
            "relpath_or_hint": self._relpath(workspace, path),
            "type": typ,
            "size": path.stat().st_size if path.exists() else 0,
            "sha256": self._sha256(path) if path.exists() else "",
        }
        if extra:
            record.update(extra)
        return record

    def _relpath(self, root: Path, path: Path) -> str:
        try:
            return str(path.relative_to(root))
        except Exception:
            return str(path)

    def _sha256(self, path: Path) -> str:
        import hashlib

        h = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _count_fasta_records(self, path: Path) -> int:
        if not path.exists():
            return 0
        count = 0
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if line.startswith(">"):
                    count += 1
        return count

    def _count_nonempty_lines(self, path: Path) -> int:
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return sum(1 for line in handle if line.strip())

    def _repair_empty_final_gff(self, job_id: str, issue: Dict[str, Any], project_root: Path = PROJECT_ROOT) -> Dict[str, Any]:
        input_dir = project_root / "input" / job_id
        payload_text = "\n".join(str(x) for x in issue.get("evidence", []))
        match = re.search(r"final_gff=([^\n]+)", payload_text)
        final_gff = Path(match.group(1).strip()) if match else None
        if not final_gff or not final_gff.exists():
            return {"success": False, "status": "blocked", "issue_code": issue.get("code"), "message": "未能定位空 final GFF。"}
        species = self._species_from_path_name(final_gff)
        candidates = self._candidate_source_gffs(input_dir, species)
        if not candidates:
            return {"success": False, "status": "blocked", "issue_code": issue.get("code"), "message": f"{species} 没有可用的非空原始 GFF。"}
        source = candidates[0]
        backup = self._repair_backup_path(final_gff)
        shutil.copy2(source, final_gff)
        record = {
            "issue_code": issue.get("code"),
            "species": species,
            "source_gff": str(source),
            "repaired_gff": str(final_gff),
            "backup": str(backup) if backup else "",
            "created_at": datetime.now().isoformat(),
            "policy": "job-local runtime repair; global source code unchanged",
        }
        repair_dir = input_dir / "genome_prepare"
        repair_dir.mkdir(parents=True, exist_ok=True)
        (repair_dir / f"{species}.gff.repair.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return {"success": True, "changed": True, "status": "repaired", "issue_code": issue.get("code"), "repair": record}

    def _repair_genome_prepare_extract_sequences(
        self,
        job_id: str,
        issue: Dict[str, Any],
        project_root: Path = PROJECT_ROOT,
        *,
        execute_heavy: bool = False,
    ) -> Dict[str, Any]:
        payload = self._issue_payload(issue)
        species = str(payload.get("species") or "").strip()
        if not species:
            return {"success": False, "status": "blocked", "issue_code": issue.get("code"), "message": "无法从诊断证据中识别物种。"}
        input_dir = project_root / "input" / job_id
        genome_candidates = self._candidate_source_genomes(input_dir, species)
        payload_genome = payload.get("genome")
        if payload_genome:
            payload_path = Path(str(payload_genome))
            genome_candidates.insert(0, payload_path if payload_path.is_absolute() else input_dir / payload_path)
        genome = next((p for p in genome_candidates if p.exists() and p.stat().st_size > 0 and self._fasta_profile(p).get("looks_nucleotide")), None)
        gff_candidates = self._candidate_source_gffs(input_dir, species)
        payload_gff = payload.get("gff")
        if payload_gff:
            payload_path = Path(str(payload_gff))
            gff_candidates.insert(0, payload_path if payload_path.is_absolute() else input_dir / payload_path)
        gff = next((p for p in gff_candidates if p.exists() and p.stat().st_size > 0), None)
        if not genome or not gff:
            return {
                "success": False,
                "status": "blocked",
                "issue_code": issue.get("code"),
                "message": f"{species} 缺少可用于 gffread 的 genome 或非空 GFF。",
                "genome_candidates": [str(p) for p in genome_candidates],
                "gff_candidates": [str(p) for p in gff_candidates],
            }
        if not execute_heavy:
            return {
                "success": True,
                "changed": False,
                "status": "ready",
                "issue_code": issue.get("code"),
                "message": "已确认可用 genome+GFF，可在确认后执行 gffread 提取 pep/cds。",
                "species": species,
                "genome": str(genome),
                "gff": str(gff),
            }
        pep_out = input_dir / f"{species}.pep.fasta"
        cds_out = input_dir / f"{species}.cds.fasta"
        backups = []
        for path in (pep_out, cds_out):
            backup = self._move_to_repair_backup(path)
            if backup:
                backups.append(str(backup))
        gffread = project_root / "software" / "gffread"
        if not gffread.exists():
            gffread = Path(shutil.which("gffread") or "")
        cmd = [str(gffread) if gffread.exists() else "gffread", str(gff), "-g", str(genome), "-y", str(pep_out), "-x", str(cds_out)]
        proc = subprocess.run(cmd, cwd=str(project_root), text=True, capture_output=True, check=False, timeout=3600)
        record = {
            "issue_code": issue.get("code"),
            "species": species,
            "cmd": cmd,
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-2000:],
            "stderr_tail": proc.stderr[-2000:],
            "genome": str(genome),
            "gff": str(gff),
            "pep_out": str(pep_out),
            "cds_out": str(cds_out),
            "backups": backups,
            "created_at": datetime.now().isoformat(),
            "policy": "job-local runtime repair using gffread; global source code unchanged",
        }
        repair_dir = input_dir / "genome_prepare"
        repair_dir.mkdir(parents=True, exist_ok=True)
        (repair_dir / f"{species}.gffread_extract.repair.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        success = proc.returncode == 0 and pep_out.exists() and pep_out.stat().st_size > 0 and cds_out.exists() and cds_out.stat().st_size > 0
        return {
            "success": success,
            "changed": success,
            "status": "repaired" if success else "repair_failed",
            "issue_code": issue.get("code"),
            "repair": record,
            "should_rerun_stage": success,
        }

    def _apply_known_patch_to_copy(self, issue_code: str, patched_script: Path) -> Dict[str, Any]:
        """对副本应用已知低风险补丁。若全局脚本已修复，则记录 no-op。"""
        text = patched_script.read_text(encoding="utf-8")
        original = text
        notes = []

        if issue_code == "python_import_path_error":
            marker = "# DiagnosisAgent patch: ensure project root is importable"
            if marker in text:
                notes.append("Patch already present: project root is added to sys.path.")
            else:
                lines = text.splitlines()
                insert_at = 0
                if lines and lines[0].startswith("#!"):
                    insert_at = 1
                while insert_at < len(lines) and (
                    not lines[insert_at].strip()
                    or lines[insert_at].startswith("#")
                    or lines[insert_at].startswith('"""')
                    or lines[insert_at].startswith("'''")
                ):
                    insert_at += 1
                    if insert_at > 20:
                        break
                patch_lines = [
                    marker,
                    "import sys as _diagnosis_sys",
                    "from pathlib import Path as _DiagnosisPath",
                    f"_DIAGNOSIS_PROJECT_ROOT = _DiagnosisPath({str(PROJECT_ROOT)!r})",
                    "if str(_DIAGNOSIS_PROJECT_ROOT) not in _diagnosis_sys.path:",
                    "    _diagnosis_sys.path.insert(0, str(_DIAGNOSIS_PROJECT_ROOT))",
                    "",
                ]
                lines[insert_at:insert_at] = patch_lines
                text = "\n".join(lines) + ("\n" if original.endswith("\n") else "")
                notes.append("Inserted project root sys.path guard into patched script copy.")
        elif issue_code == "r_table_comment_char":
            old = 'read.table(kv$count_matrix, header = TRUE, row.names = 1, sep = "\\t", check.names = FALSE)'
            new = 'read.table(kv$count_matrix, header = TRUE, row.names = 1, sep = "\\t", check.names = FALSE, quote = "", comment.char = "")'
            if old in text:
                text = text.replace(old, new)
                notes.append("Disabled R comment parsing for count matrix reads.")
            elif "comment.char = \"\"" in text:
                notes.append("Patch already present: comment.char is disabled.")
        elif issue_code == "success_with_zero_alignment_rate":
            if "Overall alignment rate:" in text and "Total pairs:" in text:
                notes.append("Patch already present: HISAT2 new-summary parser is supported.")
            else:
                notes.append("No automatic text patch template matched; manual patch required.")
        elif issue_code == "success_with_zero_qc_reads":
            if "Total read pairs processed:" in text and "Pairs written (passing filters):" in text:
                notes.append("Patch already present: cutadapt paired-end parser is supported.")
            else:
                notes.append("No automatic text patch template matched; manual patch required.")
        elif issue_code == "fgsea_rank_gene2ko_id_mismatch":
            if "regexec(\"^(Os[0-9]{2})t([0-9]+)(-[0-9]+)?$\"" in text:
                notes.append("Patch already present: RAP transcript IDs are normalized to RAP gene IDs for fgsea.")
            elif "normalize_gene_id <- function(x)" in text and "x" in text:
                notes.append(
                    "No direct source patch applied: fgsea R code is generated inside Python; "
                    "apply the global RAP normalization patch to run_rnaseq_fgsea.py and regenerate the stage script."
                )
            else:
                notes.append("No automatic text patch template matched; manual patch required.")
        elif issue_code == "wgcna_matrix_comment_char":
            old = 'read.table(path, header = TRUE, row.names = 1, sep = "\\t", check.names = FALSE)'
            new = 'read.table(path, header = TRUE, row.names = 1, sep = "\\t",\n             check.names = FALSE, quote = "", comment.char = "")'
            if old in text:
                text = text.replace(old, new)
                notes.append("Disabled R comment parsing for WGCNA matrix reads.")
            elif 'comment.char = ""' in text and "read_matrix <- function" in text:
                notes.append("Patch already present: WGCNA matrix reads disable R comment parsing.")
            else:
                notes.append("No automatic text patch template matched; manual patch required.")
        elif issue_code == "quant_fpkm_tpm_comment_char":
            notes.append(
                "The known patch is in scripts/calculate_fpkm_tpm_simple.R, not the copied Python runner. "
                "Ensure count/GTF reads set quote = \"\" and comment.char = \"\" before rerunning quant."
            )
        else:
            notes.append(f"No known automatic patch template for issue: {issue_code}")

        changed = text != original
        if changed:
            patched_script.write_text(text, encoding="utf-8")
        return {
            "success": True,
            "changed": changed,
            "issue_code": issue_code,
            "notes": notes,
        }

    def _build_patched_command(
        self,
        job_id: str,
        stage: str,
        prefix: str,
        patched_script: Path,
        fix_data: Dict[str, Any],
        project_root: Path,
    ) -> Dict[str, Any]:
        cmd = fix_data.get("cmd") or fix_data.get("command")
        if isinstance(cmd, str):
            cmd = shlex.split(cmd)
        elif not isinstance(cmd, list):
            manifest = self._load_stage_manifest(project_root / "workspace" / job_id / "final", stage, prefix)
            cmd = manifest.get("cmd") if manifest else None
        if not isinstance(cmd, list) or not cmd:
            return {
                "success": False,
                "message": "缺少可执行命令。请在 fix_data.cmd 中提供原阶段命令，DiagnosisAgent 会替换为 patched script。",
            }

        original_name = STAGE_SCRIPT_MAP.get(stage, "")
        patched_cmd = []
        replaced = False
        for part in cmd:
            if original_name and (str(part).endswith(original_name) or str(part).endswith(f"apps/cli/{original_name}")):
                patched_cmd.append(str(patched_script))
                replaced = True
            else:
                patched_cmd.append(str(part))
        if not replaced and patched_script.suffix == ".py":
            # Conservative fallback for commands like ["python", "-m", ...] is not safe.
            return {
                "success": False,
                "message": "原命令中未找到阶段脚本路径，无法保证使用 patched script 执行。",
                "cmd": cmd,
            }
        return {"success": True, "cmd": patched_cmd}

    def _execute_patched_command(self, cmd: List[str], cwd: Path, timeout: int) -> Dict[str, Any]:
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd),
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
            return {
                "returncode": proc.returncode,
                "stdout_tail": proc.stdout[-4000:],
                "stderr_tail": proc.stderr[-4000:],
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "returncode": 124,
                "stdout_tail": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
                "stderr_tail": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
                "error": f"patched command timed out after {timeout}s",
            }
        except Exception as exc:
            return {"returncode": 1, "error": str(exc), "stdout_tail": "", "stderr_tail": ""}

    def _backup_failed_run(self, job_id: str, stage: str, prefix: str, project_root: Path) -> Dict[str, Any]:
        workspace = project_root / "workspace" / job_id
        backup_dir = workspace / "reproducibility" / "failed_runs" / stage / datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        manifest = self._load_stage_manifest(workspace / "final", stage, prefix)
        if manifest and manifest.get("_path"):
            src = Path(manifest["_path"])
            if src.exists():
                dst = backup_dir / src.name
                shutil.copy2(src, dst)
                copied.append(str(dst))
        logs_dir = workspace / "logs"
        logs = sorted(logs_dir.glob(f"*_{stage}.log"), key=lambda p: p.stat().st_mtime, reverse=True) if logs_dir.exists() else []
        if logs:
            dst = backup_dir / logs[0].name
            shutil.copy2(logs[0], dst)
            copied.append(str(dst))
        return {"backup_dir": str(backup_dir), "copied": copied}

    def _annotate_recovery_manifest(
        self,
        job_id: str,
        stage: str,
        prefix: str,
        recovery: Dict[str, Any],
        project_root: Path,
    ) -> Dict[str, Any]:
        manifest = self._load_stage_manifest(project_root / "workspace" / job_id / "final", stage, prefix)
        if not manifest or not manifest.get("_path"):
            return {"success": False, "message": "No manifest found to annotate."}
        path = Path(manifest.pop("_path"))
        manifest["recovery"] = recovery
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"success": True, "manifest_path": str(path)}

    def _update_patch_report(self, report_path: Path, updates: Dict[str, Any]) -> None:
        try:
            data = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
            data.update(updates)
            report_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            return

    def _open_text_maybe_gzip(self, path: Path):
        if str(path).lower().endswith(".gz"):
            return gzip.open(path, "rt", encoding="utf-8", errors="ignore")
        return open(path, "r", encoding="utf-8", errors="ignore")

    def _fasta_profile(self, path: Path, max_bases: int = 50000) -> Dict[str, Any]:
        profile = {
            "path": str(path),
            "exists": path.exists(),
            "size": path.stat().st_size if path.exists() else 0,
            "headers": 0,
            "letters": 0,
            "nucleotide_letters": 0,
            "non_nucleotide_letters": 0,
            "max_record_len": 0,
            "first_header": "",
        }
        if not path.exists() or path.stat().st_size == 0:
            return profile
        current_len = 0
        try:
            with self._open_text_maybe_gzip(path) as handle:
                for raw in handle:
                    if raw.startswith(">"):
                        if current_len > profile["max_record_len"]:
                            profile["max_record_len"] = current_len
                        current_len = 0
                        profile["headers"] += 1
                        if not profile["first_header"]:
                            profile["first_header"] = raw.strip()[:120]
                        continue
                    seq = re.sub(r"[^A-Za-z*]", "", raw)
                    if not seq:
                        continue
                    current_len += len(seq)
                    upper = seq.upper().replace("*", "")
                    profile["letters"] += len(upper)
                    profile["nucleotide_letters"] += sum(1 for ch in upper if ch in {"A", "C", "G", "T", "U", "N"})
                    profile["non_nucleotide_letters"] += sum(1 for ch in upper if ch not in {"A", "C", "G", "T", "U", "N"})
                    if profile["letters"] >= max_bases:
                        break
                if current_len > profile["max_record_len"]:
                    profile["max_record_len"] = current_len
        except Exception as exc:
            profile["error"] = str(exc)
        letters = max(int(profile["letters"] or 0), 1)
        profile["nucleotide_ratio"] = round(float(profile["nucleotide_letters"]) / letters, 4)
        profile["non_nucleotide_ratio"] = round(float(profile["non_nucleotide_letters"]) / letters, 4)
        profile["looks_nucleotide"] = profile["letters"] > 0 and profile["nucleotide_ratio"] >= 0.92
        profile["looks_protein"] = profile["letters"] > 0 and profile["non_nucleotide_ratio"] >= 0.08
        profile["looks_genome_like"] = bool(profile["looks_nucleotide"] and profile["max_record_len"] >= 5000)
        return profile

    def _species_from_path_name(self, path: Path) -> str:
        name = path.name
        if "_final.gff" in name:
            return name.split("_final.gff", 1)[0]
        if "." in name:
            return name.split(".", 1)[0]
        return path.stem

    def _species_path_rank(self, path: Path, species: str) -> Tuple[int, int, str]:
        species_l = species.lower()
        name_l = path.name.lower()
        parent_l = path.parent.name.lower()
        score = 100
        if parent_l == species_l:
            score = min(score, 0)
        if name_l.startswith(f"{species_l}_final."):
            score = min(score, 1)
        if name_l.startswith(f"{species_l}."):
            score = min(score, 2)
        if name_l.startswith(f"{species_l}_") or name_l.startswith(f"{species_l}-"):
            score = min(score, 3)
        if species_l in name_l:
            score = min(score, 5)
        return (score, len(path.name), str(path))

    def _dedupe_existing_paths(self, candidates: List[Path]) -> List[Path]:
        unique: List[Path] = []
        seen = set()
        for path in candidates:
            try:
                resolved = path.resolve()
            except Exception:
                resolved = path
            if resolved in seen or not path.exists() or path.stat().st_size == 0:
                continue
            seen.add(resolved)
            unique.append(path)
        return unique

    def _candidate_source_gffs(self, input_dir: Path, species: str) -> List[Path]:
        species_candidates: List[Path] = []
        all_candidates: List[Path] = []
        for base in [input_dir / species, input_dir]:
            if base.exists():
                species_candidates.extend(sorted(base.glob(f"{species}_final.gff3")))
                species_candidates.extend(sorted(base.glob(f"{species}.gff")))
                species_candidates.extend(sorted(base.glob(f"{species}.gff3")))
                species_candidates.extend(sorted(base.glob(f"{species}*.gff")))
                species_candidates.extend(sorted(base.glob(f"{species}*.gff3")))
                all_candidates.extend(sorted(base.glob("*.gff")))
                all_candidates.extend(sorted(base.glob("*.gff3")))
        species_unique = self._dedupe_existing_paths(species_candidates)
        if species_unique:
            return sorted(species_unique, key=lambda p: self._species_path_rank(p, species))
        all_unique = self._dedupe_existing_paths(all_candidates)
        if len(all_unique) == 1:
            return all_unique
        return []

    def _candidate_source_genomes(self, input_dir: Path, species: str) -> List[Path]:
        candidates: List[Path] = []
        suffixes = ["fasta", "fa", "fna", "fasta.gz", "fa.gz", "fna.gz"]
        exact_names = []
        for suffix in suffixes:
            exact_names.extend([
                f"{species}.genome.{suffix}",
                f"{species}.genomic.{suffix}",
                f"{species}.dna.{suffix}",
                f"{species}.{suffix}",
            ])
        patterns = []
        for suffix in suffixes:
            patterns.extend([
                f"{species}*genome*.{suffix}",
                f"{species}*genomic*.{suffix}",
                f"{species}*dna*.{suffix}",
                f"{species}*Chr*.{suffix}",
                f"{species}*.{suffix}",
                f"*{species}*genome*.{suffix}",
                f"*{species}*genomic*.{suffix}",
                f"*{species}*dna*.{suffix}",
                f"*{species}*Chr*.{suffix}",
            ])
        for base in [input_dir / species, input_dir]:
            if not base.exists():
                continue
            for name in exact_names:
                candidates.append(base / name)
            for pattern in patterns:
                candidates.extend(sorted(base.glob(pattern)))

        blocked_tokens = ("pep", "protein", "cds", "transcript", "mrna")
        unique = [
            path for path in self._dedupe_existing_paths(candidates)
            if not any(token in path.name.lower() for token in blocked_tokens)
        ]
        return sorted(unique, key=lambda p: self._species_path_rank(p, species))

    def _gate_genome_prepare(self, input_dir: Path, final_dir: Path, logs_dir: Path, manifest: Optional[Dict[str, Any]], gate: str, add_issue) -> None:
        species_files: Dict[str, Dict[str, Path]] = {}
        for path in sorted(input_dir.glob("*.pep.fasta")) + sorted(input_dir.glob("*.pep.fa")):
            species_files.setdefault(self._species_from_path_name(path), {})["pep"] = path
        for path in sorted(input_dir.glob("*.cds.fasta")) + sorted(input_dir.glob("*.cds.fa")):
            species_files.setdefault(self._species_from_path_name(path), {})["cds"] = path
        for path in sorted(input_dir.glob("*_final.gff3")):
            species_files.setdefault(self._species_from_path_name(path), {})["gff"] = path
        for path in (
            sorted(input_dir.glob("*.genome.fasta"))
            + sorted(input_dir.glob("*.genome.fa"))
            + sorted(input_dir.glob("*.genome.fna"))
            + sorted(input_dir.glob("*.genome.fasta.gz"))
            + sorted(input_dir.glob("*.genome.fa.gz"))
            + sorted(input_dir.glob("*.genome.fna.gz"))
        ):
            species_files.setdefault(self._species_from_path_name(path), {})["genome"] = path

        metrics = (manifest or {}).get("metrics", {})
        for result in metrics.get("species_results", []) if isinstance(metrics, dict) else []:
            if not isinstance(result, dict):
                continue
            species = str(result.get("species") or "").strip()
            files = result.get("files") if isinstance(result.get("files"), dict) else {}
            artifacts = result.get("artifacts") if isinstance(result.get("artifacts"), dict) else {}
            if species:
                species_files.setdefault(species, {})
            for key in ("pep", "cds", "gff", "genome"):
                value = files.get(key) or (artifacts.get(key, {}) if isinstance(artifacts.get(key), dict) else {}).get("full_path")
                if value and species:
                    species_files[species][key] = Path(value)
            if species and result.get("success") is False:
                add_issue(
                    "genome_prepare_species_failed",
                    "blocking",
                    f"genome_prepare 中物种 {species} 失败：{result.get('error') or 'unknown error'}",
                    "阻断依赖该物种的 identify/共线性等下游阶段；先修复该物种输入或从本次分析排除。",
                    blocks_downstream=True,
                    evidence_items=[json.dumps(result, ensure_ascii=False)[:1000]],
                )

        for species, files in sorted(species_files.items()):
            gff = files.get("gff") or input_dir / f"{species}_final.gff3"
            if gff.exists() and gff.stat().st_size == 0:
                sources = self._candidate_source_gffs(input_dir, species)
                add_issue(
                    "genome_prepare_final_gff_empty",
                    "blocking",
                    f"{species}_final.gff3 为空，下游结构、启动子、共线性和核型图会失败。",
                    "优先选择同物种非空原始 GFF 作为 job-local 运行级修复；修复后重跑 genome_prepare 或阻断下游。",
                    runtime_repair=bool(sources),
                    blocks_downstream=True,
                    evidence_items=[
                        f"final_gff={gff}",
                        "candidate_gffs=" + json.dumps([str(p) for p in sources[:5]], ensure_ascii=False),
                    ],
                )
            pep = files.get("pep")
            if pep and pep.exists():
                profile = self._fasta_profile(pep)
                if profile.get("looks_genome_like"):
                    payload = {"species": species, "pep": str(pep), "profile": profile}
                    if files.get("genome"):
                        payload["genome"] = str(files["genome"])
                    if files.get("gff"):
                        payload["gff"] = str(files["gff"])
                    add_issue(
                        "genome_prepare_pep_looks_like_genome",
                        "blocking",
                        f"{species} pep 文件看起来像基因组/核酸序列，不像蛋白序列。",
                        "不能把该文件作为蛋白输入；若有 genome+GFF，可用 gffread 在 job-local 重新提取 pep。",
                        runtime_repair=True,
                        blocks_downstream=True,
                        evidence_items=[json.dumps(payload, ensure_ascii=False)],
                    )
            cds = files.get("cds")
            if cds and cds.exists():
                profile = self._fasta_profile(cds)
                if profile.get("looks_genome_like"):
                    payload = {"species": species, "cds": str(cds), "profile": profile}
                    if files.get("genome"):
                        payload["genome"] = str(files["genome"])
                    if files.get("gff"):
                        payload["gff"] = str(files["gff"])
                    add_issue(
                        "genome_prepare_cds_looks_like_genome",
                        "warning",
                        f"{species} cds 文件像整段基因组而不是 CDS 集合。",
                        "若有 genome+GFF，可用 gffread 在 job-local 重新提取 CDS；否则标记该物种 CDS 不可用于 Ka/Ks。",
                        runtime_repair=True,
                        blocks_downstream=False,
                        evidence_items=[json.dumps(payload, ensure_ascii=False)],
                    )

    def _gate_identify(self, input_dir: Path, final_dir: Path, logs_dir: Path, prefix: str, params: Dict[str, Any], gate: str, add_issue) -> None:
        genome_prepare_manifests = sorted(final_dir.glob("*.genome_prepare.manifest.json"))
        for manifest_path in genome_prepare_manifests:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if manifest.get("status") == "partial_success":
                add_issue(
                    "upstream_genome_prepare_partial_success",
                    "blocking",
                    "上游 genome_prepare 为 partial_success，identify 不应静默继续使用有问题物种。",
                    "先运行 genome_prepare postflight 诊断，修复或排除失败物种后再鉴定。",
                    blocks_downstream=True,
                    evidence_items=[str(manifest_path)],
                )
                self._gate_genome_prepare(input_dir, final_dir, logs_dir, manifest, "postflight", add_issue)
        has_hmm = any(input_dir.glob("*.hmm")) or any((input_dir / "genome_prepare").glob("hmm_cache/*.hmm")) if (input_dir / "genome_prepare").exists() else any(input_dir.glob("*.hmm"))
        required_pfams = str(params.get("requiredPfams") or params.get("required_pfams") or "").strip()
        if gate == "preflight" and not has_hmm and not required_pfams:
            add_issue(
                "identify_missing_hmm_or_pfam",
                "blocking",
                "identify 缺少 HMM 文件或 required_pfams 参数。",
                "上传 HMM，或提供 Pfam 号并确保 Pfam-A.hmm 可用。",
                blocks_downstream=True,
            )
        if gate == "failure":
            log_text = self._latest_stage_log_text(logs_dir, "identify", limit=8000)
            if re.search(r"HTTPError|HTTP Error\s+404|urllib\.error\.HTTPError", log_text, re.IGNORECASE) and re.search(r"pfam|hmm|xfam|interpro", log_text, re.IGNORECASE):
                add_issue(
                    "identify_pfam_remote_404",
                    "blocking",
                    "Identify 下载 Pfam/HMM 远程资源时遇到 HTTP 404。",
                    "这通常不是用户缺少 protein 输入，而是 family 名称未规范化为 PF accession、远程 Pfam URL 变化，或本地 HMM 缓存未优先使用。应优先把 SPL/SBP 等家族名映射到 PF03110，使用本地缓存 HMM；没有缓存时再访问远程。",
                    blocks_downstream=True,
                    evidence_items=[log_text[-1200:]],
                )
        if gate in {"postflight", "failure"}:
            for status_path in sorted(final_dir.glob("*interproscan_domain_check.txt")):
                text = status_path.read_text(encoding="utf-8", errors="ignore") if status_path.exists() else ""
                if "status: failed" not in text:
                    continue
                if "Java not found" in text or "broken symbolic link" in text or "unsupported reparse tag" in text:
                    add_issue(
                        "interproscan_java_unavailable",
                        "warning",
                        "Identify 已完成，但 InterProScan domain check 因 Java PATH/坏链接失败，成员确认表缺少结构域证据。",
                        "修复 interproscanpy311 的 JAVA_HOME/PATH，使 java 指向 $CONDA_PREFIX/lib/jvm/bin/java；然后重跑 identify 或补跑 InterProScan/domain_review。",
                        blocks_downstream=False,
                        evidence_items=[str(status_path), text[-1200:]],
                    )
                    break

    def _gate_protein_stage(
        self,
        stage: str,
        final_dir: Path,
        prefix: str,
        manifest: Optional[Dict[str, Any]],
        add_issue,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        protein_fasta = self._protein_fasta_for_prefix(final_dir, prefix or (manifest or {}).get("prefix", ""), manifest)
        if not protein_fasta:
            add_issue(
                "missing_final_member_protein",
                "blocking",
                f"{stage} 缺少 final_members protein FASTA。",
                "先完成 identify/identify_refine，或提供可用成员蛋白文件。",
                blocks_downstream=True,
            )
            return
        profile = self._fasta_profile(protein_fasta)
        if profile.get("looks_nucleotide") and not profile.get("looks_protein"):
            add_issue(
                "protein_input_looks_nucleotide",
                "blocking",
                f"{stage} 的蛋白输入看起来像核酸序列。",
                "不能继续建树/MEME/domain/PEP；应回到 genome_prepare/identify 修复蛋白来源。",
                blocks_downstream=True,
                evidence_items=[json.dumps(profile, ensure_ascii=False)],
            )
        stop_stats = self._protein_stop_marker_stats(protein_fasta)
        if stop_stats.get("stop_marker_count", 0) > 0:
            clean_candidate = protein_fasta.with_name(protein_fasta.name.replace(".final_members.pep.fa", ".final_members.pep.clean.fa"))
            clean_stats = self._protein_stop_marker_stats(clean_candidate) if clean_candidate.exists() else {}
            if (
                clean_candidate.exists()
                and clean_candidate.stat().st_mtime >= protein_fasta.stat().st_mtime
                and clean_stats.get("stop_marker_count", 1) == 0
            ):
                return
            add_issue(
                "protein_fasta_stop_markers",
                "warning",
                f"蛋白 FASTA 含 {stop_stats['stop_marker_count']} 个 '*' stop marker。",
                "可自动生成 job-local clean FASTA，原始文件不变。",
                runtime_repair=True,
                evidence_items=[json.dumps(stop_stats, ensure_ascii=False)],
            )

    def _gate_gff_member_stage(
        self,
        stage: str,
        final_dir: Path,
        prefix: str,
        manifest: Optional[Dict[str, Any]],
        add_issue,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        params = params or {}
        if stage in {"synteny", "ppi", "pep"}:
            self._gate_protein_stage(stage, final_dir, prefix, manifest, add_issue, params)
        effective_prefix = prefix or (manifest or {}).get("prefix", "")
        member_ids = self._read_final_member_ids(final_dir, effective_prefix, manifest)
        gff3 = self._find_job_gff3(final_dir, effective_prefix, params)
        if not member_ids:
            add_issue("missing_final_member_ids", "blocking", f"{stage} 缺少 final_members.id。", "先完成 identify/identify_refine。", blocks_downstream=True)
            return
        if not gff3:
            add_issue("missing_nonempty_gff", "blocking", f"{stage} 缺少非空 GFF3。", "先修复 genome_prepare 的 GFF 输出。", blocks_downstream=True)

    def _gate_interspecies_synteny(self, input_dir: Path, final_dir: Path, add_issue) -> None:
        peps = [p for p in list(input_dir.glob("*.pep.fasta")) + list(input_dir.glob("*.pep.fa")) if p.exists() and p.stat().st_size > 0]
        gffs = [p for p in list(input_dir.glob("*_final.gff3")) + list(input_dir.glob("*.gff3")) + list(input_dir.glob("*.gff")) if p.exists() and p.stat().st_size > 0]
        if len(peps) < 2:
            add_issue("interspecies_missing_whole_proteomes", "blocking", "种间共线性至少需要两个物种的全基因组蛋白 FASTA。", "检查 config.xlsx 中 whole_genome pep 行。", blocks_downstream=True)
        if len(gffs) < 2:
            add_issue("interspecies_missing_gffs", "blocking", "种间共线性至少需要两个物种的非空 GFF。", "检查 genome_prepare 是否产生非空 *_final.gff3。", blocks_downstream=True)

    def _gate_reference_classification(
        self,
        input_dir: Path,
        final_dir: Path,
        logs_dir: Path,
        prefix: str,
        params: Dict[str, Any],
        add_issue,
    ) -> None:
        scope_id = self._scope_id_from_params(params)
        analysis_route = str(params.get("analysisRoute") or params.get("analysis_route") or "").strip()
        target_species = params.get("targetSpecies") or params.get("target_species") or []
        comparative_species = params.get("comparativeSpecies") or params.get("comparative_species") or []
        reference_species = params.get("referenceSpecies") or params.get("reference_species") or []
        if isinstance(target_species, str):
            target_species = [item.strip() for item in re.split(r"[,;，；\s]+", target_species) if item.strip()]
        if isinstance(comparative_species, str):
            comparative_species = [item.strip() for item in re.split(r"[,;，；\s]+", comparative_species) if item.strip()]
        if isinstance(reference_species, str):
            reference_species = [item.strip() for item in re.split(r"[,;，；\s]+", reference_species) if item.strip()]
        classifiable_species = []
        for item in [*target_species, *comparative_species]:
            if item and item not in classifiable_species:
                classifiable_species.append(item)
        family_for_identity = str(params.get("geneFamily") or "").strip()
        if not family_for_identity and "." in prefix:
            family_for_identity = prefix.split(".")[-1]
        selected_species_codes = {str(item).strip().lower() for item in [*classifiable_species, *reference_species] if str(item).strip()}
        if family_for_identity and family_for_identity.lower() in selected_species_codes:
            add_issue(
                "scope_family_species_code_conflict",
                "blocking",
                f"reference_classification 的 geneFamily={family_for_identity} 与物种代码冲突。",
                "请以 config.xlsx 的 Analysis.Family 作为 family，并确认 prefix 形如 Lc.SPL 而不是 Lc.At。",
                blocks_downstream=True,
            )
        if analysis_route == "subfamily_prediction" and not reference_species:
            add_issue(
                "subfamily_missing_reference_species",
                "blocking",
                "亚家族预测缺少 referenceSpecies，不能降级为 target_only。",
                "请在 config.xlsx 中设置 seed_reference/known_family reference，或在参数中显式提供 referenceSpecies。",
                blocks_downstream=True,
            )
        per_target_reference_route = (
            analysis_route == "subfamily_prediction"
            and bool(classifiable_species)
            and bool(reference_species)
        )
        if per_target_reference_route:
            # In this route reference_classification owns per-species target/comparative+reference
            # phylogeny/evidence generation. Requiring the parent aggregate tree here
            # would reintroduce meaningless multi-species+reference plots.
            phylo_manifest = None
        else:
            phylo_manifest = self._load_stage_manifest(final_dir, "phylogeny", prefix, scope_id=scope_id)
        tree_candidates: List[Path] = []
        if phylo_manifest:
            artifacts = phylo_manifest.get("artifacts") or {}
            tree_artifact = artifacts.get("tree") if isinstance(artifacts, dict) else None
            if isinstance(tree_artifact, dict):
                for key in ("full_path", "path"):
                    value = tree_artifact.get(key)
                    if value:
                        p = Path(str(value))
                        tree_candidates.append(p if p.is_absolute() else PROJECT_ROOT / p)
        tree_candidates.append(final_dir / f"{prefix}.tree.nwk")
        if (not per_target_reference_route) and not any(p.exists() and p.stat().st_size > 0 for p in tree_candidates):
            latest_phylogeny_log = self._latest_stage_log_text(logs_dir, "phylogeny", limit=1200)
            evidence_items = [str(p) for p in tree_candidates]
            if latest_phylogeny_log:
                evidence_items.append(latest_phylogeny_log[-1200:])
            add_issue(
                "missing_phylogeny_tree",
                "blocking",
                "reference_classification 缺少同一分析范围的 phylogeny 树文件。",
                "普通 reference_classification 需要同 scope 的 phylogeny；亚家族支线会按每个 target/comparative 物种分别生成 物种+reference bootstrap 树。",
                blocks_downstream=True,
                evidence_items=evidence_items,
            )

        reference_file = str(params.get("referenceSubfamilyFile") or params.get("reference_subfamily_file") or "").strip()
        reference_prefix = str(params.get("referencePrefix") or params.get("reference_prefix") or "At").strip() or "At"
        family = str(params.get("geneFamily") or "").strip()
        if not family and "." in prefix:
            family = prefix.split(".")[-1]
        subfamily_candidates: List[Path] = []
        if reference_file:
            p = Path(reference_file)
            subfamily_candidates.append(p if p.is_absolute() else PROJECT_ROOT / p)
        if family:
            for root in (final_dir, input_dir):
                subfamily_candidates.extend([
                    root / f"{reference_prefix}.{family}.subfamily",
                    root / f"{reference_prefix}.{family}.subfamily.tsv",
                    root / f"{reference_prefix}.{family}.geneID.subfamily",
                ])
        subfamily_candidates.extend(list(final_dir.glob("*subfamily*")) + list(input_dir.glob("*subfamily*")))
        if not any(p.exists() and p.stat().st_size > 0 for p in subfamily_candidates):
            add_issue(
                "missing_reference_subfamily",
                "blocking",
                "reference_classification 缺少参考物种亚家族标注文件。",
                "请提供参考物种亚家族文件，例如 At.SPL.subfamily，或在参数中指定 referenceSubfamilyFile。",
                blocks_downstream=True,
                evidence_items=[str(p) for p in subfamily_candidates[:12]],
            )

    def _load_stage_manifest(self, final_dir: Path, stage: str, prefix: str = "", scope_id: str = "") -> Optional[Dict[str, Any]]:
        stage_aliases = {
            "ncbi_fetch_sequences": ["ncbi_fetch_sequences", "ncbi_fetch"],
            "sequence_extract_by_id": ["sequence_extract_by_id", "sequence_extract"],
        }
        aliases = stage_aliases.get(stage, [stage])
        if scope_id and prefix:
            try:
                from gfa_core.gene_family_scope import sanitize_token

                scoped_token = sanitize_token(scope_id)
            except Exception:
                scoped_token = re.sub(r"[^A-Za-z0-9_.-]+", "-", scope_id).strip(".-_")
            for alias in aliases:
                path = final_dir / f"{prefix}.{scoped_token}.{alias}.manifest.json"
                if path.exists():
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                        data["_path"] = str(path)
                        return data
                    except Exception:
                        continue
        patterns = []
        for alias in aliases:
            if prefix:
                patterns.append(f"{prefix}.{alias}.manifest.json")
            patterns.extend([f"*.{alias}.manifest.json", f"*{alias}*.manifest.json"])
        for pattern in patterns:
            for path in sorted(final_dir.glob(pattern), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    manifest_stage = data.get("stage")
                    if scope_id and str(data.get("scope_id") or "") != scope_id:
                        continue
                    if manifest_stage == stage or manifest_stage in aliases or any(alias in path.name for alias in aliases):
                        data["_path"] = str(path)
                        return data
                except Exception:
                    continue
        return None

    def _genome_prepare_manifest_stale(self, input_dir: Path, manifest: Optional[Dict[str, Any]]) -> bool:
        """Ignore old failed genome_prepare manifests after config/input changes."""
        if not manifest:
            return False
        if str(manifest.get("status") or "").lower() == "success":
            return False
        raw_path = str(manifest.get("_path") or "")
        if not raw_path:
            return False
        manifest_path = Path(raw_path)
        if not manifest_path.exists():
            return False
        manifest_mtime = manifest_path.stat().st_mtime
        for path in input_dir.rglob("*"):
            if not path.is_file():
                continue
            try:
                rel_parts = path.relative_to(input_dir).parts
            except Exception:
                rel_parts = ()
            if rel_parts and rel_parts[0] in {"final", "genome_prepare"}:
                continue
            if path.stat().st_mtime > manifest_mtime:
                return True
        return False

    def _audit_rnaseq_qc(self, input_dir: Path, final_dir: Path, manifest: Optional[Dict[str, Any]], add_issue) -> None:
        config = self._find_rnaseq_config(input_dir)
        duplicates = self._find_duplicate_fastq_pairs(config, input_dir) if config else []
        if duplicates:
            add_issue(
                "duplicate_fastq_pair",
                "blocking",
                "RNA-seq 样本配置中存在多个样本复用同一对 FASTQ。",
                "暂停流程，确认样本配置。可以提供一键命名/路径修订，但必须由用户确认正确文件。",
                blocks_downstream=True,
                evidence_items=duplicates,
            )

        metrics = (manifest or {}).get("metrics", {})
        artifacts = (manifest or {}).get("artifacts", {})
        if (manifest or {}).get("status") == "success" and artifacts:
            if metrics.get("total_clean_reads", 0) == 0 or metrics.get("total_raw_reads", 0) == 0:
                add_issue(
                    "success_with_zero_qc_reads",
                    "warning",
                    "QC 产物存在且 manifest 为 success，但 reads 统计为 0。",
                    "这通常是日志解析兼容问题。可在当前 job 修复副本中修正解析器并用已有 cutadapt 日志验证。",
                    requires_confirmation=True,
                    safe_patch_candidate=True,
                    evidence_items=[
                        f"total_raw_reads={metrics.get('total_raw_reads')}",
                        f"total_clean_reads={metrics.get('total_clean_reads')}",
                        f"artifact_count={len(artifacts)}",
                    ],
                )

    def _audit_population_stage(self, stage: str, manifest: Optional[Dict[str, Any]], add_issue) -> None:
        if not manifest:
            return
        if manifest.get("status") != "success":
            add_issue(
                f"{stage}_manifest_failed", "blocking",
                f"{stage} failed: {str(manifest.get('error') or 'unknown error')[:500]}",
                "Inspect the stage attempt log and repair the reported input/tool/postflight failure.",
                requires_confirmation=False, blocks_downstream=True,
            )
            return
        if not str(manifest.get("genotype_scope_id") or "").startswith("genotype:") or not str(manifest.get("analysis_scope_id") or "").startswith("population-analysis:"):
            add_issue(
                "population_identity_incomplete", "blocking",
                f"{stage} success manifest lacks complete population scientific identity.",
                "Rerun the stage through its canonical CLI.", requires_confirmation=False, blocks_downstream=True,
            )
        artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
        if not artifacts or any(not isinstance(item, dict) or not item.get("sha256") for item in artifacts.values()):
            add_issue(
                "population_artifact_evidence_incomplete", "blocking",
                f"{stage} manifest lacks hashed artifact evidence.",
                "Rerun the stage and require all postflight artifacts.", requires_confirmation=False, blocks_downstream=True,
            )

    def _audit_gwas_fmt(self, manifest: Optional[Dict[str, Any]], add_issue) -> None:
        if not manifest:
            return
        if manifest.get("status") != "success":
            detail = str(manifest.get("error") or manifest.get("message") or "unknown failure").strip()
            detail_lower = detail.lower()
            if "unsupported hapmap marker" in detail_lower:
                code = "gwas_fmt_unsupported_markers_rejected"
                suggestion = "输入含 HapMap 不可逆 marker；使用 mask 明确记录有损转换，或先过滤后保持 fail 策略。"
            elif "genotype" in detail_lower or "locus" in detail_lower or "allele" in detail_lower:
                code = "gwas_fmt_genotype_conservation_failed"
                suggestion = "检查 manifest.error 指定的 locus/GT；不要将该失败降级为样本数或文件缺失问题。"
            else:
                code = "gwas_fmt_manifest_failed"
                suggestion = "按 manifest.error 检查外部 VCF、TASSEL 路径或转换一致性后重跑。"
            add_issue(
                code,
                "blocking",
                f"gwas_fmt manifest failed: {detail[:500]}",
                suggestion,
                blocks_downstream=True,
                evidence_items=[f"manifest_error={detail[:500]}"],
            )
            return
        metrics = manifest.get("metrics") if isinstance(manifest.get("metrics"), dict) else {}
        if metrics.get("sample_conservation") is not True or metrics.get("variant_conservation") is not True:
            add_issue(
                "conversion_cardinality_mismatch",
                "blocking",
                "GWAS 格式转换未证明样本和位点数量守恒。",
                "检查 TASSEL 排序与 HapMap 输出，并重新运行 gwas_fmt。",
                blocks_downstream=True,
            )
        parameters = manifest.get("parameters") if isinstance(manifest.get("parameters"), dict) else {}
        if parameters.get("unsupported_marker_policy") == "mask" and metrics.get("lossy_conversion") is True:
            if metrics.get("strict_genotype_conservation") is not True:
                add_issue(
                    "gwas_fmt_strict_genotype_conservation_failed",
                    "blocking",
                    "GWAS mask 模式未证明可逆 marker 子集的 genotype conservation。",
                    "检查 manifest.error 指向的可逆 SNP locus/GT 并重跑；不可用全体 genotype_conservation 代替。",
                    blocks_downstream=True,
                )
        artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), list) else []
        roles = {str(item.get("role") or "") for item in artifacts if isinstance(item, dict)}
        missing_roles = sorted({"sorted_vcf", "tassel_hapmap"} - roles)
        unhashed = [str(item.get("role") or "unknown") for item in artifacts if isinstance(item, dict) and not item.get("sha256")]
        if missing_roles or unhashed:
            add_issue(
                "gwas_fmt_artifact_evidence_incomplete",
                "blocking",
                "GWAS 格式转换产物角色或 SHA256 证据不完整。",
                "重新运行 gwas_fmt 并使用 manifest 中的语义 artifact roles。",
                blocks_downstream=True,
                evidence_items=[f"missing_roles={missing_roles}", f"unhashed_roles={unhashed}"],
            )

    @staticmethod
    def _audit_gwas_fmt_preflight(params: Dict[str, Any], add_issue) -> None:
        vcf_text = str(params.get("gwasVcf") or params.get("gwas_vcf") or "").strip()
        vcf_path = Path(vcf_text).expanduser() if vcf_text else None
        if vcf_path is None or not vcf_path.is_absolute() or not vcf_path.is_file():
            add_issue(
                "gwas_fmt_external_vcf_missing",
                "blocking",
                f"gwas_fmt external VCF is missing or invalid: {vcf_text or 'not provided'}",
                "提供存在的 VCF/VCF.GZ 全路径到 gwasVcf。",
                blocks_downstream=True,
            )
        elif vcf_path.is_file():
            from gfa_core.gwas_fmt_context import semantic_external_vcf_preflight
            semantic = semantic_external_vcf_preflight(params)
            for issue in semantic.get("issues") or []:
                if issue.get("code") != "invalid_external_vcf":
                    continue
                add_issue(
                    "gwas_fmt_external_vcf_semantic_invalid",
                    "blocking",
                    str(issue.get("message") or "VCF semantic validation failed"),
                    "确认 gzip 可读、存在 #CHROM、且 samples/sites 均大于 0。",
                    blocks_downstream=True,
                )
        tassel_text = str(params.get("tasselPipeline") or params.get("tassel_pipeline") or "run_pipeline.pl").strip()
        tassel_path = Path(tassel_text).expanduser()
        tassel_available = (
            tassel_path.is_file() if (tassel_path.is_absolute() or "/" in tassel_text)
            else shutil.which(tassel_text) is not None
        )
        if not tassel_available:
            add_issue(
                "gwas_fmt_tassel_missing",
                "blocking",
                f"TASSEL pipeline executable was not found: {tassel_text}",
                "设置 tasselPipeline 为可执行 run_pipeline.pl 的全路径。",
                blocks_downstream=True,
            )
    def _audit_rnaseq_align(self, final_dir: Path, manifest: Optional[Dict[str, Any]], add_issue) -> None:
        metrics = (manifest or {}).get("metrics", {})
        bams = list(final_dir.glob("*.sorted.bam"))
        logs = list(final_dir.glob("*_hisat2.log"))
        if (manifest or {}).get("status") == "success" and bams and metrics.get("average_alignment_rate", 0) == 0:
            add_issue(
                "success_with_zero_alignment_rate",
                "warning",
                "比对 BAM/HISAT2 日志存在且 manifest 为 success，但平均比对率为 0。",
                "这通常是 HISAT2 日志格式解析问题。可在当前 job 修复副本中修正解析器并用现有 hisat2.log 验证。",
                requires_confirmation=True,
                safe_patch_candidate=True,
                evidence_items=[
                    f"bam_count={len(bams)}",
                    f"hisat2_log_count={len(logs)}",
                    f"average_alignment_rate={metrics.get('average_alignment_rate')}",
                ],
            )

    def _audit_rnaseq_sample_qc(self, final_dir: Path, logs_dir: Path, manifest: Optional[Dict[str, Any]], add_issue) -> None:
        if (manifest or {}).get("status") == "failed":
            error = str((manifest or {}).get("error", ""))
            log_text = self._latest_stage_log_text(logs_dir, "rnaseq_sample_qc")
            if "did not have" in log_text and "elements" in log_text:
                add_issue(
                    "r_table_comment_char",
                    "warning",
                    "样本 QC 读取 count matrix 失败，R read.table 可能把基因 ID 中的 # 当成注释。",
                    "可在当前 job 修复副本中关闭 R 表格读取的 comment.char，并使用原 count matrix 验证。",
                    requires_confirmation=True,
                    safe_patch_candidate=True,
                    evidence_items=[error[:300], log_text[-500:]],
                )

            if "number of samples and the number of model coefficients are equal" in log_text or "no replicates to estimate the dispersion" in log_text:
                add_issue(
                    "rnaseq_sample_qc_condition_scope",
                    "blocking",
                    "样本 QC 的分组 scope 可能把重复编号写进 condition，导致每个 condition 只有 1 个样本并触发 DESeq2 设计矩阵失败。",
                    "将 sample config 中的 condition 规范为生物条件（例如 Peel.1/Peel.2/Peel.3 -> Peel），样本 QC 使用 blind VST；差异分析前仍需人工确认真实分组。",
                    requires_confirmation=False,
                    safe_patch_candidate=True,
                    blocks_downstream=True,
                    evidence_items=[error[:300], log_text[-700:]],
                )

        corr = final_dir / f"{(manifest or {}).get('prefix', '')}.rnaseq_sample_qc.sample_correlation.tsv"
        if not corr.exists():
            candidates = sorted(final_dir.glob("*.rnaseq_sample_qc.sample_correlation.tsv"))
            corr = candidates[0] if candidates else corr
        high_pairs = self._find_high_cross_sample_correlations(corr)
        if high_pairs:
            add_issue(
                "near_duplicate_samples",
                "blocking",
                "样本相关性矩阵发现近乎重复样本，可能是样本复用、标签错误或重复上传。",
                "暂停下游解释，人工确认样本配置和 FASTQ 来源；不能由 agent 自动改样本语义。",
                blocks_downstream=True,
                evidence_items=high_pairs[:10],
            )

    def _audit_rnaseq_quant(self, final_dir: Path, logs_dir: Path, manifest: Optional[Dict[str, Any]], add_issue) -> None:
        log_text = self._latest_stage_log_text(logs_dir, "rnaseq_quant", limit=8000)
        if "DESeq2 FPKM/TPM calculation failed" not in log_text:
            return
        if re.search(r"line\s+\d+\s+did not have\s+\d+\s+elements", log_text, re.IGNORECASE):
            add_issue(
                "quant_fpkm_tpm_comment_char",
                "warning",
                "rnaseq_quant 主计数矩阵成功，但 FPKM/TPM 计算失败并回退，R 读取 count/GTF 时可能把基因 ID 中的 # 当成注释。",
                "全局脚本应关闭 R 表格读取的 quote/comment.char；修复后用相同 count matrix 和 GTF 重新计算 FPKM/TPM。",
                requires_confirmation=True,
                safe_patch_candidate=True,
                blocks_downstream=False,
                evidence_items=[log_text[-1000:]],
            )

    def _audit_rnaseq_go_enrich(self, final_dir: Path, manifest: Optional[Dict[str, Any]], add_issue) -> None:
        metrics = (manifest or {}).get("metrics", {})
        if (manifest or {}).get("status") != "success":
            return

        overlap = metrics.get("go_foreground_background_overlap")
        zero_overlap = False
        if isinstance(overlap, dict) and overlap:
            zero_overlap = any(
                isinstance(item, dict)
                and int(item.get("foreground_genes", 0) or 0) > 0
                and int(item.get("annotated_background_genes", 0) or 0) > 0
                and int(item.get("overlap_genes", 0) or 0) == 0
                for item in overlap.values()
            )

        if not overlap and int(metrics.get("go_enriched_terms", 0) or 0) == 0:
            zero_overlap = self._go_has_zero_foreground_background_overlap(final_dir)

        if zero_overlap:
            add_issue(
                "go_foreground_background_id_mismatch",
                "blocking",
                "GO 富集前景 DE 基因与 gene2go 背景基因没有交集，通常是 gene/transcript ID 体系不一致。",
                "富集脚本应自动执行安全 ID 规范化；若仍为 0 交集，应阻断下游解释并向用户展示前景/背景 ID 示例。",
                requires_confirmation=True,
                safe_patch_candidate=True,
                blocks_downstream=True,
                evidence_items=[
                    f"go_enriched_terms={metrics.get('go_enriched_terms')}",
                    f"annotated_background_genes={metrics.get('annotated_background_genes')}",
                    f"overlap={json.dumps(overlap, ensure_ascii=False)[:500]}",
                ],
            )

    def _audit_rnaseq_fgsea(self, final_dir: Path, logs_dir: Path, manifest: Optional[Dict[str, Any]], add_issue) -> None:
        metrics = (manifest or {}).get("metrics", {})
        params = (manifest or {}).get("parameters", {})
        species_name = str(params.get("species_name", "") or "")
        kegg_organism = str(params.get("kegg_organism", "") or "")
        if species_name and any(ord(ch) > 127 for ch in species_name) and not kegg_organism:
            add_issue(
                "kegg_species_name_non_ascii",
                "warning",
                "fgsea 参数中的物种名含非 ASCII 字符，可能导致 KEGG organism 自动推断失败。",
                "请用户确认物种名是否应为 'Oryza sativa' 或直接填写 KEGG organism 'osa'；agent 不应静默修改实验物种参数。",
                requires_confirmation=True,
                safe_patch_candidate=False,
                blocks_downstream=False,
                evidence_items=[f"species_name={species_name}", "kegg_organism is empty"],
            )
        log_text = self._latest_stage_log_text(logs_dir, "rnaseq_fgsea", limit=8000)
        failed = (manifest or {}).get("status") == "failed"
        zero_mapping = int(metrics.get("genes_with_pathway_in_ranks", 0) or 0) == 0
        suspicious_log = "No KEGG pathway gene sets passed min/max size filters" in log_text or "fgsea failed" in str((manifest or {}).get("error", ""))
        if not (failed or zero_mapping or suspicious_log):
            return

        overlap = self._fgsea_de_gene2ko_overlap(final_dir)
        if overlap and int(overlap.get("raw_overlap", 0)) == 0 and int(overlap.get("normalized_overlap", 0)) > 0:
            add_issue(
                "fgsea_rank_gene2ko_id_mismatch",
                "blocking",
                "fgsea 排名基因与 gene2ko 背景原始 ID 没有交集，但安全 ID 规范化后可恢复交集。",
                "fgsea 脚本应自动执行与 GO/KEGG 富集一致的 ID 规范化；用户确认后可用当前 job 修复副本验证，再决定是否升级全局代码。",
                requires_confirmation=True,
                safe_patch_candidate=True,
                blocks_downstream=True,
                evidence_items=[
                    f"raw_overlap={overlap.get('raw_overlap')}",
                    f"normalized_overlap={overlap.get('normalized_overlap')}",
                    f"de_file={overlap.get('de_file')}",
                    f"gene2ko={overlap.get('gene2ko')}",
                    f"foreground_examples={overlap.get('foreground_examples')}",
                    f"background_examples={overlap.get('background_examples')}",
                ],
            )
        elif failed:
            add_issue(
                "fgsea_failed_needs_review",
                "blocking",
                "fgsea 阶段失败，但未能确认是可自动修复的 ID 规范化问题。",
                "展示日志尾部和输入 ID 示例，由用户确认后再进入副本修复或参数调整；不要静默重跑。",
                requires_confirmation=True,
                safe_patch_candidate=False,
                blocks_downstream=True,
                evidence_items=[str((manifest or {}).get("error", ""))[:500], log_text[-1000:]],
            )

    def _audit_rnaseq_wgcna(self, final_dir: Path, logs_dir: Path, manifest: Optional[Dict[str, Any]], add_issue) -> None:
        if (manifest or {}).get("status") != "failed":
            return
        log_text = self._latest_stage_log_text(logs_dir, "rnaseq_wgcna", limit=8000)
        if re.search(r"line\s+\d+\s+did not have\s+\d+\s+elements", log_text, re.IGNORECASE):
            add_issue(
                "wgcna_matrix_comment_char",
                "blocking",
                "WGCNA 读取表达矩阵失败，R read.table 很可能把基因 ID 中的 # 当成注释导致行列数不一致。",
                "WGCNA 脚本应关闭矩阵读取的 quote/comment.char；确认后可用当前 job 修复副本验证，再决定是否升级全局代码。",
                requires_confirmation=True,
                safe_patch_candidate=True,
                blocks_downstream=True,
                evidence_items=[log_text[-1000:]],
            )

    def _audit_generic_gene_family_stage(self, stage: str, final_dir: Path, logs_dir: Path, manifest: Optional[Dict[str, Any]], add_issue, prefix: str = "") -> None:
        log_text = self._latest_stage_log_text(logs_dir, stage)
        if stage == "identify" and isinstance(manifest, dict):
            hmm_models = manifest.get("hmm_models") if isinstance(manifest.get("hmm_models"), dict) else {}
            missing_hmms = hmm_models.get("missing_requested_pfams") or []
            hmm_status = str(hmm_models.get("status") or "").lower()
            if missing_hmms or hmm_status == "failed":
                missing_text = ",".join(str(x) for x in missing_hmms) if missing_hmms else "unknown"
                add_issue(
                    "identify_pfam_partial_fetch",
                    "blocking",
                    f"Identify 请求的 Pfam/HMM 模型未全部加载：{missing_text}。",
                    "重新构建 job-local query HMM，并验证 requested Pfams 与 loaded ACC 完全一致后再重跑 identify。",
                    blocks_downstream=True,
                    evidence_items=[json.dumps(hmm_models, ensure_ascii=False)[:1200]],
                )
        text = log_text
        if manifest:
            try:
                text = (text + "\n" + json.dumps(manifest, ensure_ascii=False))[-12000:]
            except Exception:
                pass
        generic_patterns = [
            ("id_mismatch", r"No GFF IDs matched|No .*IDs matched|ID.*不匹配|无法匹配.*ID", "ID 匹配失败，可能是 FASTA/GFF 命名体系不一致。"),
            ("plantcare_upload_timeout", r"Timeout waiting for PlantCARE result|No PlantCARE result returned|No result file found", "PlantCARE 未返回本次任务结果，可能是上传 FASTA 超过官网大小限制或外部队列/邮件失败。"),
            ("plantcare_id_mismatch", r"PlantCARE result IDs do not match the extracted promoter FASTA IDs", "PlantCARE 结果文件与本次提取的启动子 FASTA ID 完全不重叠。"),
            ("multi_target_single_species_stage", r"一次只能分析一个目标物种|only.*one target species|single target", "单物种阶段收到了多个 target species，需要按目标物种拆分执行。"),
            ("interspecies_pair_no_hits", r"BLAST failed or produced no hits|produced no hits", "种间同源搜索没有产生有效 hits，可能是蛋白文件、压缩格式、阈值或物种距离问题。"),
            ("empty_required_output", r"No successful results|produced no hits|output.*empty|结果为空", "核心输出为空，需要检查输入、ID 转换或阈值。"),
            ("tool_version_format", r"line \d+ did not have|unexpected format|parse.*failed", "工具输出或输入表格式与解析器不兼容。"),
            ("hmm_or_pfam_missing", r"NO_HMM_FILE_ERROR|必须上传至少一个HMM文件|required_pfams is empty|No HMM file|找不到HMM|Pfam database not found|Missing Pfam index", "基因家族鉴定缺少 HMM 模型或 Pfam 参数/数据库索引。"),
            ("pfam_remote_404", r"HTTPError|HTTP Error\s+404|urllib\.error\.HTTPError", "远程 Pfam/HMM 资源访问失败，可能是 URL 变化或家族名没有规范化为 PF accession。"),
        ]
        for code, pattern, message in generic_patterns:
            search_text = text
            if (manifest or {}).get("status") == "success":
                # Runtime error signatures must be backed by runtime logs.
                # Successful manifests may contain descriptive strings that match
                # generic failure regexes without representing a failed output.
                search_text = log_text
            if code == "plantcare_upload_timeout" and stage != "promoter":
                continue
            if code == "multi_target_single_species_stage":
                # This issue must come from an actual runtime error/log line.
                # Successful multi-target identify manifests legitimately contain
                # artifact descriptions like "Single target species final ...".
                if stage == "identify":
                    continue
                search_text = log_text
            if re.search(pattern, search_text, re.IGNORECASE):
                if code == "hmm_or_pfam_missing":
                    recommendation = "Identify 需要二选一：上传家族 HMM 文件，或提供 required_pfams（如 PF03110）并确保 Pfam-A.hmm/索引可用。"
                elif code == "pfam_remote_404":
                    recommendation = "优先使用本地 Pfam/HMM 缓存；把家族名/结构域别名规范化为 PF accession，例如 SPL/SBP -> PF03110，再重试。"
                elif code == "id_mismatch":
                    recommendation = "先生成 ID 匹配报告，确认 final_members.id、蛋白 FASTA header 与 GFF3 gene/mRNA/CDS ID 的对应关系；不要静默改 ID。"
                elif code == "plantcare_upload_timeout":
                    recommendation = "检查 promoter/analysis/gene.upstream.changeID.fasta 大小；若超过约 100 KB，应按上传字节大小分批提交 PlantCARE 并合并结果，而不是只延长 timeout。"
                elif code == "plantcare_id_mismatch":
                    recommendation = "不要复用其他物种或其他 gene set 的 PlantCARE tab；应使用本次生成的 promoter FASTA 重新提交 PlantCARE，或上传与本次 FASTA header 匹配的 tab。"
                elif code == "multi_target_single_species_stage":
                    recommendation = "该阶段应自动拆成每个 target species 单独执行，并写回原 multi-target scope 的聚合 manifest；若仍失败，检查 scope 拆分逻辑。"
                elif code == "interspecies_pair_no_hits":
                    recommendation = "先确认 target/reference 全蛋白 FASTA 已物化为 plain FASTA，避免 DIAMOND/BLAST 链路直接处理 .gz 造成空结果；再检查序列有效性、evalue 阈值，必要时记录该物种对为 partial_success。"
                else:
                    recommendation = "先展示错误点和候选修复；若属于解析兼容问题，可确认使用当前 job 修复副本验证。"
                is_identify_root_cause = stage == "identify" and code in {
                    "hmm_or_pfam_missing",
                    "pfam_remote_404",
                }
                add_issue(
                    code,
                    "blocking" if is_identify_root_cause else "warning",
                    message,
                    recommendation,
                    requires_confirmation=(code == "hmm_or_pfam_missing"),
                    safe_patch_candidate=(code == "tool_version_format"),
                    blocks_downstream=is_identify_root_cause,
                    evidence_items=[text[-800:]],
                )

        metrics = (manifest or {}).get("metrics", {})
        artifacts = (manifest or {}).get("artifacts", {})
        effective_prefix = (manifest or {}).get("prefix") or prefix
        if stage == "combo" and manifest and str(manifest.get("scope_id") or "").strip():
            try:
                from gfa_core.mas.tools.stage_status import get_stage_status

                status = get_stage_status(
                    job_id=str(manifest.get("job_id") or final_dir.parent.name),
                    prefix=str(effective_prefix),
                    stage="combo",
                    workspace_root=final_dir.parent.parent,
                    scope_id=str(manifest.get("scope_id") or ""),
                )
                scope_errors = [err for err in (status.errors or []) if "scope mismatch" in str(err).lower()]
                if scope_errors:
                    add_issue(
                        "combo_artifact_scope_mismatch",
                        "blocking",
                        "Combo 组合图已生成，但产物文件名没有携带完整 scope token，结果校验无法确认它属于本次 target/reference 范围。",
                        "这是输出命名与 scoped 契约不一致的问题；应让 combo manifest 指向完整 scope 命名的 PDF/PNG，或同步放宽校验规则。",
                        requires_confirmation=False,
                        safe_patch_candidate=False,
                        blocks_downstream=True,
                        evidence_items=scope_errors,
                    )
            except Exception as exc:
                add_issue(
                    "combo_status_audit_failed",
                    "warning",
                    "Combo 结果状态复核失败，无法确认 scoped artifact 是否满足契约。",
                    "检查 stage_status 工具和 combo manifest 的 scope_id/artifacts 字段。",
                    evidence_items=[str(exc)],
                )
        if stage in {"ncbi_fetch_sequences", "ncbi_fetch"}:
            self._audit_ncbi_family_reference_link(final_dir, effective_prefix, add_issue)
        protein_fasta = self._protein_fasta_for_prefix(final_dir, effective_prefix, manifest)
        if protein_fasta and stage in {"identify", "phylogeny", "motif", "domain", "pep", "ppi"}:
            stop_stats = self._protein_stop_marker_stats(protein_fasta)
            if stop_stats.get("stop_marker_count", 0) > 0:
                clean_candidate = protein_fasta.with_name(protein_fasta.name.replace(".final_members.pep.fa", ".final_members.pep.clean.fa"))
                clean_stats = self._protein_stop_marker_stats(clean_candidate) if clean_candidate.exists() else {}
                clean_is_ready = (
                    clean_candidate.exists()
                    and clean_candidate.stat().st_mtime >= protein_fasta.stat().st_mtime
                    and clean_stats.get("stop_marker_count", 1) == 0
                )
                if not clean_is_ready:
                    add_issue(
                        "protein_fasta_stop_markers",
                        "warning",
                        f"蛋白 FASTA 含有 {stop_stats['stop_marker_count']} 个 '*' stop marker，涉及 {stop_stats['sequences_with_stop_markers']} 条序列。",
                        "生成 job-local clean FASTA，保留原始文件不变；下游 MAFFT/MEME/hmmscan/PEP 使用 clean FASTA。",
                        safe_patch_candidate=True,
                        evidence_items=[json.dumps(stop_stats, ensure_ascii=False)],
                    )
        if stage in {"structure", "promoter", "synteny", "karyotype"}:
            params = (manifest or {}).get("parameters", {})
            member_ids = self._read_final_member_ids(final_dir, effective_prefix, manifest)
            gff3 = self._find_job_gff3(final_dir, effective_prefix, params if isinstance(params, dict) else None)
            if member_ids and gff3:
                member_norm = {self._normalize_gene_id_for_match(item) for item in member_ids}
                gff_norm = self._read_gff_gene_ids(gff3)
                overlap = member_norm & gff_norm
                ratio = len(overlap) / max(len(member_norm), 1)
                if ratio < 0.8:
                    evidence = {
                        "final_member_ids": len(member_norm),
                        "gff_ids": len(gff_norm),
                        "overlap": len(overlap),
                        "overlap_ratio": round(ratio, 4),
                        "gff3": str(gff3),
                        "example_unmatched": sorted(member_norm - gff_norm)[:10],
                    }
                    add_issue(
                        "gene_gff_id_overlap_low",
                        "warning" if overlap else "blocking",
                        f"final_members.id 与 GFF3 的规范化 ID 重叠率偏低：{len(overlap)}/{len(member_norm)}。",
                        "这通常是蛋白ID/转录本ID/基因ID体系不一致。应先确认映射规则，再决定是否生成修订后的成员ID或规范化GFF；系统不会静默改ID。",
                        requires_confirmation=False,
                        safe_patch_candidate=False,
                        blocks_downstream=(overlap == 0),
                        evidence_items=[json.dumps(evidence, ensure_ascii=False)],
                    )
        metric_required_stages = {
            "identify", "phylogeny", "motif", "domain", "structure", "promoter",
            "synteny", "interspecies_synteny", "karyotype", "ppi", "expression", "pep",
        }
        if stage in metric_required_stages and (manifest or {}).get("status") == "success" and artifacts and self._metrics_look_empty(metrics):
            if stage == "identify" and self._identify_manifest_has_member_metrics(manifest or {}):
                return
            add_issue(
                "success_with_empty_metrics",
                "warning",
                f"{stage} 产物存在且 manifest 为 success，但关键 metrics 看起来为空或为 0。",
                "需要检查 manifest 统计逻辑；如果只是统计解析问题，可确认用当前 job 修复副本验证。",
                safe_patch_candidate=True,
                evidence_items=[f"metrics={json.dumps(metrics, ensure_ascii=False)[:500]}", f"artifact_count={len(artifacts)}"],
                    )

    def _audit_ncbi_family_reference_link(self, final_dir: Path, prefix: str, add_issue) -> None:
        candidates: List[Path] = []
        if prefix:
            candidates.extend([
                final_dir / f"{prefix}.ncbi_fetch.protein.fa",
                final_dir / f"{prefix}.ncbi_fetch.protein.fasta",
            ])
        candidates.extend(sorted(final_dir.glob("*.ncbi_fetch.protein.fa")))
        candidates.extend(sorted(final_dir.glob("*.ncbi_fetch.protein.fasta")))
        seen: set[str] = set()
        for protein_fa in candidates:
            try:
                key = str(protein_fa.resolve())
            except Exception:
                key = str(protein_fa)
            if key in seen or not protein_fa.exists() or protein_fa.stat().st_size == 0:
                continue
            seen.add(key)
            parts = protein_fa.name.split(".")
            if len(parts) < 4 or parts[2] != "ncbi_fetch":
                continue
            species, family = parts[0], parts[1]
            expected = final_dir / f"{species}.{family}.pep.fa"
            if expected.exists() and expected.stat().st_size > 0:
                continue
            add_issue(
                "ncbi_family_reference_link_missing",
                "warning",
                f"NCBI 下载蛋白已存在，但缺少标准家族参考名 {expected.name}。",
                "创建 job-local 软链接/副本，统一为 {Species}.{Family}.pep.fa，供 identify/phylogeny/reference classification 复用。",
                requires_confirmation=False,
                safe_patch_candidate=True,
                blocks_downstream=False,
                evidence_items=[json.dumps({
                    "protein_fasta": str(protein_fa),
                    "expected_reference_pep": str(expected),
                    "species": species,
                    "family": family,
                }, ensure_ascii=False)],
            )

    def _audit_rnaseq_de_auto_expression(self, final_dir: Path, logs_dir: Path, manifest: Optional[Dict[str, Any]], add_issue) -> None:
        if not manifest or manifest.get("status") != "success":
            return
        prefix = str(manifest.get("prefix") or "").strip()
        expression_manifest = final_dir / f"{prefix}.expression.manifest.json" if prefix else None
        if expression_manifest is None:
            matches = sorted(final_dir.glob("*.expression.manifest.json"))
            expression_manifest = matches[0] if matches else None
        if not manifest.get("rnaseq_scope_id"):
            add_issue(
                "rnaseq_context_missing",
                "warning",
                "rnaseq_de manifest 缺少 rnaseq_scope_id，后续表达图、富集和 WGCNA 难以稳定归属到同一 RNA-seq 实验。",
                "使用当前 rnaseq context 逻辑重跑 rnaseq_de，或修复 manifest context 后再刷新状态。",
                safe_patch_candidate=True,
            )
        if not expression_manifest or not expression_manifest.exists():
            add_issue(
                "rnaseq_auto_expression_missing",
                "warning",
                "rnaseq_de 已成功，但未找到自动触发生成的 Expression(使用FPKM) manifest。",
                "检查 rnaseq_de 后端自动触发日志；必要时使用 run_expression.py --mode rnaseq_auto 基于真实 FPKM/TPM/VST 重跑。",
                safe_patch_candidate=True,
                evidence_items=[f"expected_expression_manifest={expression_manifest}"],
            )
            return
        try:
            expr_manifest = json.loads(expression_manifest.read_text(encoding="utf-8"))
        except Exception as exc:
            add_issue(
                "rnaseq_auto_expression_failed",
                "warning",
                "Expression(使用FPKM) manifest 存在但无法解析。",
                "查看 expression 日志并重跑 rnaseq_auto 表达绘图。",
                safe_patch_candidate=True,
                evidence_items=[f"manifest={expression_manifest}", f"parse_error={exc}"],
            )
            return
        if expr_manifest.get("status") != "success":
            add_issue(
                "rnaseq_auto_expression_failed",
                "warning",
                "rnaseq_de 自动触发的 Expression(使用FPKM) 阶段未成功。",
                "查看 expression 日志并使用完整真实 FPKM/TPM/VST 数据重跑 rnaseq_auto。",
                safe_patch_candidate=True,
                evidence_items=[f"manifest={expression_manifest}", f"status={expr_manifest.get('status')}", f"error={expr_manifest.get('error', '')}"],
            )

    def _go_has_zero_foreground_background_overlap(self, final_dir: Path) -> bool:
        gene2go = self._find_gene2go(final_dir)
        if not gene2go:
            return False
        background_raw, background_norm = self._read_gene2go_gene_sets(gene2go)
        if not background_raw:
            return False
        for de_file in sorted(final_dir.glob("*.rnaseq_de.*.DE_genes.txt")):
            foreground_raw, foreground_norm = self._read_de_gene_sets(de_file)
            if not foreground_raw:
                continue
            raw_overlap = len(foreground_raw & background_raw)
            normalized_overlap = len(foreground_norm & background_norm)
            if raw_overlap == 0 and normalized_overlap >= 0:
                return True
        return False

    def _find_gene2go(self, final_dir: Path) -> Optional[Path]:
        preferred = final_dir / "gene2go.txt"
        if preferred.exists():
            return preferred
        matches = sorted(final_dir.glob("*gene2go*.txt"))
        return matches[0] if matches else None

    def _find_gene2ko(self, final_dir: Path) -> Optional[Path]:
        preferred = final_dir / "gene2ko.txt"
        if preferred.exists():
            return preferred
        matches = sorted(final_dir.glob("*gene2ko*.txt"))
        return matches[0] if matches else None

    def _fgsea_de_gene2ko_overlap(self, final_dir: Path) -> Dict[str, Any]:
        gene2ko = self._find_gene2ko(final_dir)
        de_files = sorted(final_dir.glob("*.rnaseq_de.*.DE_results.txt"))
        if not gene2ko or not de_files:
            return {}
        background_raw, background_norm = self._read_gene2ko_gene_sets(gene2ko)
        if not background_raw:
            return {}
        best: Dict[str, Any] = {}
        for de_file in de_files:
            foreground_raw, foreground_norm = self._read_de_gene_sets(de_file)
            if not foreground_raw:
                continue
            raw_overlap = len(foreground_raw & background_raw)
            normalized_overlap = len(foreground_norm & background_norm)
            item = {
                "de_file": str(de_file),
                "gene2ko": str(gene2ko),
                "foreground_genes": len(foreground_raw),
                "background_genes": len(background_raw),
                "raw_overlap": raw_overlap,
                "normalized_overlap": normalized_overlap,
                "foreground_examples": sorted(foreground_raw)[:5],
                "background_examples": sorted(background_raw)[:5],
            }
            if not best or normalized_overlap > int(best.get("normalized_overlap", -1)):
                best = item
            if raw_overlap == 0 and normalized_overlap > 0:
                return item
        return best

    def _read_gene2go_gene_sets(self, path: Path) -> Tuple[set, set]:
        raw_genes = set()
        normalized_genes = set()
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip() or line.startswith("#"):
                    continue
                gene = re.split(r"[\t, ]+", line.strip())[0]
                raw_genes.add(gene)
                normalized_genes.add(self._normalize_enrichment_gene_id(gene))
        except Exception:
            return set(), set()
        raw_genes.discard("")
        normalized_genes.discard("")
        return raw_genes, normalized_genes

    def _read_gene2ko_gene_sets(self, path: Path) -> Tuple[set, set]:
        raw_genes = set()
        normalized_genes = set()
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip() or line.startswith("#"):
                    continue
                gene = re.split(r"[\t, ]+", line.strip())[0]
                raw_genes.add(gene)
                normalized_genes.add(self._normalize_enrichment_gene_id(gene))
        except Exception:
            return set(), set()
        raw_genes.discard("")
        normalized_genes.discard("")
        return raw_genes, normalized_genes

    def _read_de_gene_sets(self, path: Path) -> Tuple[set, set]:
        raw_genes = set()
        normalized_genes = set()
        try:
            for idx, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
                if idx == 0:
                    continue
                if not line.strip() or line.startswith("#"):
                    continue
                gene = line.split("\t", 1)[0].strip()
                normalized = self._normalize_enrichment_gene_id(gene)
                if normalized and normalized.lower() not in {"gene", "gene_id", "geneid", "id", "x"}:
                    raw_genes.add(gene)
                    normalized_genes.add(normalized)
        except Exception:
            return set(), set()
        return raw_genes, normalized_genes

    def _normalize_enrichment_gene_id(self, gene_id: str) -> str:
        value = (gene_id or "").strip().strip('"').lstrip(">")
        value = re.sub(r"^[a-z][a-z0-9]{2,4}:", "", value)
        value = re.split(r"\s+", value, maxsplit=1)[0]
        previous = None
        suffix_patterns = [
            re.compile(r"(?i)([._-](?:mrna|rna|transcript|cds|protein|pep|peptide)\d*[A-Za-z]*)$"),
            re.compile(r"(?i)([._-][tp]\d+[A-Za-z]?)$"),
            re.compile(r"(?i)([._-][rp][A-Z])$"),
            re.compile(r"(\.\d+)$"),
        ]
        while value and value != previous:
            previous = value
            for pattern in suffix_patterns:
                value = pattern.sub("", value)
        match = RAP_TRANSCRIPT_RE.match(value)
        if match:
            return f"{match.group(1)}g{match.group(2)}"
        return value

    def _find_rnaseq_config(self, input_dir: Path) -> Optional[Path]:
        for pattern in ("samples*.yaml", "samples*.yml", "*sample*.yaml", "*sample*.yml"):
            matches = sorted(input_dir.glob(pattern))
            if matches:
                return matches[0]
        return None

    def _find_duplicate_fastq_pairs(self, config_path: Path, input_dir: Path) -> List[str]:
        try:
            import yaml
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return []
        samples = cfg.get("samples") or {}
        seen: Dict[Tuple[str, str], str] = {}
        duplicates = []
        for sample, value in samples.items():
            if isinstance(value, dict):
                r1 = value.get("r1") or value.get("read1") or ""
                r2 = value.get("r2") or value.get("read2") or ""
            elif isinstance(value, list) and len(value) >= 2:
                r1, r2 = value[0], value[1]
            else:
                continue
            p1 = str((input_dir / r1).resolve()) if r1 and not Path(str(r1)).is_absolute() else str(r1)
            p2 = str((input_dir / r2).resolve()) if r2 and not Path(str(r2)).is_absolute() else str(r2)
            key = (p1, p2)
            if key in seen:
                duplicates.append(f"{seen[key]} 与 {sample} 复用: {Path(p1).name} / {Path(p2).name}")
            else:
                seen[key] = str(sample)
        return duplicates

    def _latest_stage_log_text(self, logs_dir: Path, stage: str, limit: int = 4000) -> str:
        if not logs_dir.exists():
            return ""
        logs = sorted(logs_dir.glob(f"*_{stage}.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not logs:
            return ""
        try:
            text = logs[0].read_text(encoding="utf-8", errors="ignore")
            return text[-limit:]
        except Exception:
            return ""

    def _find_high_cross_sample_correlations(self, path: Path, threshold: float = 0.999) -> List[str]:
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if not lines:
                return []
            header = lines[0].split("\t")[1:]
            pairs = []
            for row in lines[1:]:
                parts = row.split("\t")
                sample = parts[0]
                for other, value in zip(header, parts[1:]):
                    if sample >= other:
                        continue
                    try:
                        corr = float(value)
                    except Exception:
                        continue
                    if corr >= threshold:
                        pairs.append(f"{sample} vs {other}: correlation={corr}")
            return pairs
        except Exception:
            return []

    def _metrics_look_empty(self, metrics: Any) -> bool:
        if not isinstance(metrics, dict) or not metrics:
            return True
        numeric_values = []
        for value in metrics.values():
            if isinstance(value, (int, float)):
                numeric_values.append(value)
        return bool(numeric_values) and all(value == 0 for value in numeric_values)

    def _identify_manifest_has_member_metrics(self, manifest: Dict[str, Any]) -> bool:
        species_results = manifest.get("species_results")
        if not isinstance(species_results, list):
            return False
        for item in species_results:
            if not isinstance(item, dict):
                continue
            final_members = item.get("final_members")
            try:
                if int(final_members) > 0:
                    return True
            except Exception:
                pass
            review = item.get("domain_review")
            if isinstance(review, dict):
                try:
                    if int(review.get("default_keep_count") or 0) > 0:
                        return True
                except Exception:
                    pass
        return False

    def _dedupe_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        seen: set[Tuple[str, str]] = set()
        for issue in issues:
            key = (str(issue.get("code") or ""), str(issue.get("message") or ""))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(issue)
        return deduped

    def _prioritize_issues(self, issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Put actionable root causes before derived manifest symptoms."""
        if not issues:
            return []
        root_cause_codes = {
            "hmm_or_pfam_missing",
            "identify_missing_hmm_or_pfam",
            "pfam_remote_404",
            "identify_pfam_remote_404",
            "identify_pfam_partial_fetch",
            "identify_hmm_model_mismatch",
            "python_import_path_error",
            "stage_runtime_same_file_copy",
            "known_family_missing_gene_family",
            "id_mismatch",
            "plantcare_id_mismatch",
            "multi_target_single_species_stage",
            "interspecies_pair_no_hits",
            "genome_prepare_final_gff_empty",
            "genome_prepare_pep_looks_like_genome",
            "genome_prepare_cds_looks_like_genome",
            "domain_scoped_identify_guard",
            "kaks_scope_missing_reference",
        }
        has_specific_root = any(str(issue.get("code") or "") in root_cause_codes for issue in issues)
        if has_specific_root:
            issues = [
                issue for issue in issues
                if str(issue.get("code") or "") not in {"manifest_missing", "manifest_missing_stage_incomplete"}
            ]
        severity_weight = {"blocking": 0, "warning": 1, "info": 2, "ok": 3}
        code_weight = {
            "hmm_or_pfam_missing": 0,
            "identify_missing_hmm_or_pfam": 0,
            "pfam_remote_404": 1,
            "identify_pfam_remote_404": 1,
            "identify_pfam_partial_fetch": 1,
            "identify_hmm_model_mismatch": 1,
            "python_import_path_error": 2,
            "stage_runtime_same_file_copy": 2,
            "known_family_missing_gene_family": 3,
            "id_mismatch": 4,
            "plantcare_id_mismatch": 5,
            "multi_target_single_species_stage": 6,
            "interspecies_pair_no_hits": 7,
            "domain_scoped_identify_guard": 8,
            "manifest_failed": 80,
            "manifest_missing": 90,
            "manifest_missing_stage_incomplete": 91,
        }
        return sorted(
            issues,
            key=lambda issue: (
                ISSUE_LAYER_PRIORITY.get(
                    str(
                        issue.get("layer")
                        or issue.get("issue_layer")
                        or ISSUE_LAYER_BY_CODE.get(str(issue.get("code") or ""), "report")
                    ),
                    50,
                ),
                code_weight.get(str(issue.get("code") or ""), 50),
                severity_weight.get(str(issue.get("severity") or "warning"), 1),
            ),
        )

    def _highest_severity(self, issues: List[Dict[str, Any]]) -> str:
        order = {"ok": 0, "info": 1, "warning": 2, "blocking": 3}
        highest = "ok"
        for issue in issues:
            sev = issue.get("severity", "warning")
            if order.get(sev, 2) > order.get(highest, 0):
                highest = sev
        return highest

    def _augment_failure_with_llm(
        self,
        *,
        job_id: str,
        stage: str,
        prefix: str,
        input_dir: Path,
        final_dir: Path,
        logs_dir: Path,
        manifest: Optional[Dict[str, Any]],
        params: Dict[str, Any],
        issues: List[Dict[str, Any]],
        evidence: List[str],
        add_issue,
    ) -> None:
        """Use an LLM only for failure-log interpretation, after deterministic checks.

        Deterministic gates still own file/manifest facts and safe repairs. The LLM
        is a bounded classifier for unknown or under-specified runtime failures; it
        cannot mark a safe patch candidate or modify data/code.
        """
        if str(os.environ.get("GF_DIAGNOSIS_LLM_ENABLED", "1")).lower() in {"0", "false", "no", "off"}:
            return
        log_text = self._latest_stage_log_text(logs_dir, stage, limit=6000)
        if len(log_text.strip()) < 40:
            return
        if any(str(issue.get("source") or "") == "llm" for issue in issues):
            return

        deterministic_codes = [
            str(issue.get("code") or "")
            for issue in issues
            if issue.get("code")
        ]
        # If a highly specific deterministic root cause already exists, do not
        # spend LLM calls. Generic manifest/output symptoms still get LLM context.
        generic_codes = {
            "manifest_missing",
            "manifest_failed",
            "manifest_missing_stage_incomplete",
            "empty_required_output",
            "tool_version_format",
            "success_with_empty_metrics",
        }
        specific_blockers = [
            code for code in deterministic_codes
            if code not in generic_codes
            and not code.startswith("protein_fasta_stop_markers")
        ]
        if specific_blockers and str(os.environ.get("GF_DIAGNOSIS_LLM_FORCE", "0")).lower() not in {"1", "true", "yes", "on"}:
            return

        self._last_llm_failure_skip_reason = ""
        llm_issue = self._llm_failure_issue(
            job_id=job_id,
            stage=stage,
            prefix=prefix,
            log_text=log_text,
            params=params,
            manifest=manifest,
            input_summary=self._summarize_dir_for_llm(input_dir),
            final_summary=self._summarize_dir_for_llm(final_dir),
            deterministic_issues=issues,
        )
        if not llm_issue:
            reason = str(getattr(self, "_last_llm_failure_skip_reason", "") or "").strip()
            if reason:
                evidence.append(f"llm_failure_diagnosis=skipped:{reason[:240]}")
            return
        add_issue(
            llm_issue["code"],
            llm_issue["severity"],
            llm_issue["message"],
            llm_issue["recommendation"],
            requires_confirmation=True,
            safe_patch_candidate=False,
            blocks_downstream=llm_issue["severity"] == "blocking",
            evidence_items=llm_issue.get("evidence", []),
        )
        if issues:
            issues[-1]["source"] = "llm"
            issues[-1]["confidence"] = llm_issue.get("confidence")
            issues[-1]["suspected_layer"] = llm_issue.get("suspected_layer", "unknown")
            issues[-1]["suggested_rule"] = llm_issue.get("suggested_rule", {})
        evidence.append("llm_failure_diagnosis=attempted")
        self._remember_llm_diagnosis_case(job_id, stage, prefix, log_text, llm_issue)

    def _summarize_dir_for_llm(self, path: Path, limit: int = 40) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        rows: List[Dict[str, Any]] = []
        try:
            files = sorted([p for p in path.rglob("*") if p.is_file()], key=lambda p: p.stat().st_mtime, reverse=True)
        except Exception:
            return []
        for item in files[:limit]:
            try:
                rows.append({
                    "path": str(item.relative_to(path)),
                    "size": item.stat().st_size,
                })
            except Exception:
                continue
        return rows

    def _llm_failure_issue(
        self,
        *,
        job_id: str,
        stage: str,
        prefix: str,
        log_text: str,
        params: Dict[str, Any],
        manifest: Optional[Dict[str, Any]],
        input_summary: List[Dict[str, Any]],
        final_summary: List[Dict[str, Any]],
        deterministic_issues: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        try:
            from gfa_core.mas.config import config as mas_config
            from gfa_core.mas.llm import LLMManager, LLMMessage

            llm_manager = self.llm_manager or LLMManager(config=mas_config.load_llm_config())
            manifest_digest = {}
            if isinstance(manifest, dict):
                manifest_digest = {
                    "status": manifest.get("status"),
                    "stage": manifest.get("stage"),
                    "prefix": manifest.get("prefix"),
                    "error": str(manifest.get("error") or manifest.get("message") or "")[:1000],
                    "path": manifest.get("_path"),
                }
            context = {
                "job_id": job_id,
                "stage": stage,
                "prefix": prefix,
                "params": {k: v for k, v in (params or {}).items() if k not in {"openai_api_key", "api_key", "password"}},
                "manifest": manifest_digest,
                "deterministic_issues": [
                    {
                        "code": item.get("code"),
                        "severity": item.get("severity"),
                        "message": item.get("message"),
                    }
                    for item in deterministic_issues[-8:]
                ],
                "input_files_recent": input_summary[:30],
                "final_files_recent": final_summary[:30],
                "log_tail": log_text[-4000:],
            }
            prompt = (
                "你是 bbav5 基因家族/RNA-seq 流水线的失败诊断器。"
                "规则层已经做过文件、manifest 和基础日志检查；你只负责解释失败日志中规则未覆盖或不够具体的根因。"
                "不要编造不存在的文件；证据必须来自给定 JSON。"
                "不要提出直接修改全局代码的自动动作，只能给出候选根因和人工/工程修订建议。"
                "请只返回一个 JSON 对象，字段如下："
                "root_cause_category, severity(info|warning|blocking), suspected_layer(input|parameter|environment|external_resource|code_compatibility|unknown), "
                "message, recommendation, evidence(list), confidence(0-1), suggested_rule{issue_code, pattern_keywords, generalization}。\n\n"
                f"诊断上下文 JSON:\n{json.dumps(context, ensure_ascii=False, indent=2)}"
            )
            response = llm_manager.call(
                [LLMMessage(role="user", content=prompt)],
                temperature=0.1,
                max_tokens=1200,
            )
            parsed = self._parse_llm_json(response.content)
            if not parsed:
                return None
            category = self._sanitize_issue_token(parsed.get("root_cause_category") or parsed.get("suggested_rule", {}).get("issue_code") or "unknown_failure")
            severity = str(parsed.get("severity") or "warning").lower()
            if severity not in {"info", "warning", "blocking"}:
                severity = "warning"
            confidence = parsed.get("confidence", 0.5)
            try:
                confidence = max(0.0, min(float(confidence), 1.0))
            except Exception:
                confidence = 0.5
            message = str(parsed.get("message") or f"LLM 推断 {stage} 失败根因：{category}").strip()[:500]
            recommendation = str(parsed.get("recommendation") or "请结合日志和产物检查该候选根因。").strip()[:800]
            evidence_values = parsed.get("evidence") if isinstance(parsed.get("evidence"), list) else []
            suggested_rule = parsed.get("suggested_rule") if isinstance(parsed.get("suggested_rule"), dict) else {}
            return {
                "code": f"llm_{category}",
                "severity": severity,
                "message": message,
                "recommendation": recommendation,
                "evidence": [str(item)[:500] for item in evidence_values[:5]],
                "confidence": confidence,
                "suspected_layer": str(parsed.get("suspected_layer") or "unknown"),
                "suggested_rule": suggested_rule,
            }
        except Exception as exc:
            self._last_llm_failure_skip_reason = str(exc)
            print(f"[DiagnosisAgent] LLM failure diagnosis skipped: {exc}")
            return None

    def _parse_llm_json(self, text: str) -> Optional[Dict[str, Any]]:
        raw = str(text or "").strip()
        if not raw:
            return None
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
            raw = re.sub(r"```$", "", raw).strip()
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            pass
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _sanitize_issue_token(self, value: Any) -> str:
        token = re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
        return token[:80] or "unknown_failure"

    def _remember_llm_diagnosis_case(self, job_id: str, stage: str, prefix: str, log_text: str, issue: Dict[str, Any]) -> None:
        try:
            memory_dir = PROJECT_ROOT / "memory_storage"
            memory_dir.mkdir(parents=True, exist_ok=True)
            signature = re.sub(r"\s+", " ", log_text[-800:]).strip()[:800]
            record = {
                "created_at": datetime.now().isoformat(),
                "job_id": job_id,
                "stage": stage,
                "prefix": prefix,
                "log_signature": signature,
                "issue": issue,
            }
            path = memory_dir / "diagnosis_cases.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            print(f"[DiagnosisAgent] Failed to remember LLM diagnosis case: {exc}")
    
    def _match_error_pattern(self, error_text: str, language: str = "zh") -> Optional[Dict]:
        """匹配错误模式"""
        error_lower = error_text.lower()

        priority_order = [
            "tool_not_found",
            "dependency_failed",
            "go_foreground_background_id_mismatch",
            "memory_error",
            "invalid_parameter",
            "conda_env_error",
            "hmm_not_found",
            "blast_db_error",
            "missing_input_file",
            "permission_error",
            "timeout_error"
        ]

        for error_type in priority_order:
            if error_type in self.ERROR_PATTERNS:
                pattern_info = self.ERROR_PATTERNS[error_type]
                for pattern in pattern_info["patterns"]:
                    if re.search(pattern, error_text, re.IGNORECASE):
                        suggestion_key = "suggestion_en" if language == "en" else "suggestion_zh"
                        return {
                            "error_type": error_type,
                            "suggestion": pattern_info.get(suggestion_key, pattern_info.get("suggestion_zh", "")),
                            "auto_fix": pattern_info.get("auto_fix", False),
                            "fix_action": pattern_info.get("fix_action"),
                            "severity": pattern_info.get("severity", "medium")
                        }
        
        # 未匹配到已知模式，返回None，由调用方决定是否查询Knowledge
        return None
    
    async def _query_knowledge_for_error(self, error_text: str) -> Optional[Dict]:
        """调用 Knowledge Agent 查询错误解决方案"""
        try:
            from .knowledge import create_knowledge_agent
            from gfa_core.mas.llm import LLMManager
            from .base import AgentMessage, AgentState
            import uuid
            
            print(f"[DiagnosisAgent] 未知错误，调用 Knowledge Agent 查询...")
            
            # 创建 Knowledge Agent
            llm_manager = LLMManager()
            knowledge_agent = create_knowledge_agent(llm_manager)
            
            # 构建查询
            query = f"基因家族分析错误：{error_text[:200]}，如何解决？"
            
            message = AgentMessage(
                sender="diagnosis",
                receiver="knowledge",
                message_type="query",
                payload={"query": query, "mode": "consultation"}
            )
            
            state = AgentState(
                session_id=f"diagnosis_{uuid.uuid4().hex[:8]}",
                results={},
                messages=[]
            )
            
            # 执行查询
            result_state = await knowledge_agent.execute(state, message)
            answer = result_state.results.get("knowledge_response", "")
            
            if answer and len(answer) > 20:
                print(f"[DiagnosisAgent] Knowledge Agent 返回解决方案")
                return {
                    "error_type": "unknown",
                    "suggestion": answer[:500],
                    "auto_fix": False,
                    "severity": "medium",
                    "source": "knowledge"
                }
        except Exception as e:
            print(f"[DiagnosisAgent] Knowledge 查询失败: {e}")
        
        return None
    
    async def _attempt_auto_fix(self, fix_action: str, error_info: Dict, 
                               error_text: str, diagnosis: Dict) -> Dict:
        """尝试自动修复"""
        if fix_action == "reduce_cpu":
            return self._fix_reduce_cpu(error_info)
        elif fix_action == "fix_parameter_format":
            return self._fix_parameter_format(error_info)
        elif fix_action == "execute_dependencies":
            return await self._fix_execute_dependencies(error_info, error_text)
        else:
            return {"success": False, "message": f"Unknown fix action: {fix_action}"}
    
    def _fix_reduce_cpu(self, error_info: Dict) -> Dict:
        """修复：减少CPU使用"""
        current_params = error_info.get("params", {})
        current_cpu = current_params.get("cpu", 8)
        new_cpu = max(1, current_cpu // 2)
        
        return {
            "success": True,
            "action": "reduce_cpu",
            "changes": {"cpu": {"from": current_cpu, "to": new_cpu}},
            "message": f"将CPU从{current_cpu}减少到{new_cpu}",
            "new_params": {**current_params, "cpu": new_cpu}
        }
    
    def _fix_parameter_format(self, error_info: Dict) -> Dict:
        """修复：参数格式"""
        current_params = error_info.get("params", {})
        fixed_params = {}
        changes = {}
        
        for key, value in current_params.items():
            if key in self.PARAMETER_FIXES:
                new_key = self.PARAMETER_FIXES[key]
                fixed_params[new_key] = value
                changes[key] = {"to": new_key}
            else:
                fixed_params[key] = value
        
        return {
            "success": True,
            "action": "fix_parameter_format",
            "changes": changes,
            "message": f"修复了{len(changes)}个参数名称",
            "new_params": fixed_params
        }
    
    async def _fix_execute_dependencies(self, error_info: Dict, error_text: str) -> Dict:
        """修复：自动执行依赖阶段"""
        current_params = error_info.get("params", {})
        stage = error_info.get("stage", "unknown")
        
        # 从错误文本中提取缺失的依赖阶段
        import re
        missing_deps = []
        dep_patterns = [
            r"请先执行\s*(\w+)\s*阶段",
            r"requires\s*(\w+)\s*stage",
            r"依赖阶段\s*(\w+)\s*未完成",
            r"(\w+)\s*stage\s*not completed"
        ]
        
        for pattern in dep_patterns:
            matches = re.findall(pattern, error_text, re.IGNORECASE)
            missing_deps.extend(matches)
        
        # 根据主规划目录推断依赖，避免诊断链路与 PlanningAgentV2 分叉。
        if stage in CATALOG_STAGE_DEPENDENCIES and not missing_deps:
            missing_deps = list(CATALOG_STAGE_DEPENDENCIES[stage])
        
        return {
            "success": True,
            "action": "execute_dependencies",
            "changes": {"added_stages": missing_deps},
            "message": f"需要前置执行依赖阶段: {', '.join(missing_deps)}",
            "new_params": {
                **current_params,
                "execute_dependencies_first": True,
                "missing_dependencies": missing_deps
            }
        }
    
    def _generate_notification(self, stage: str, diagnosis: Dict, fix_result: Dict) -> str:
        """生成前端通知消息"""
        error_type = diagnosis.get("error_type", "unknown")
        action = fix_result.get("action", "")
        changes = fix_result.get("changes", {})
        
        notification_parts = [
            f"🔧 自动修复通知",
            f"阶段: {stage}",
            f"错误类型: {error_type}",
            f"修复操作: {action}",
        ]
        
        if changes:
            notification_parts.append("参数变更:")
            for key, change in changes.items():
                if isinstance(change, dict) and "from" in change and "to" in change:
                    notification_parts.append(f"  - {key}: {change['from']} → {change['to']}")
                elif isinstance(change, dict) and "to" in change:
                    notification_parts.append(f"  - {key} → {change['to']}")
                else:
                    notification_parts.append(f"  - {key}: {change}")
        
        notification_parts.append("正在重新执行...")
        
        return "\n".join(notification_parts)
    
    def record_error(self, error_info: Dict, diagnosis: Dict):
        """记录错误到历史"""
        self.error_history.append({
            "error": error_info,
            "diagnosis": diagnosis,
            "timestamp": datetime.now().isoformat()
        })
        
        if len(self.error_history) > 100:
            self.error_history = self.error_history[-100:]


def create_diagnosis_agent(llm_manager: LLMManager):
    """创建DiagnosisAgent实例"""
    return DiagnosisAgent(llm_manager)
