"""Machine-readable stage capabilities for planning and repair.

This module is the first structured layer above the existing prose contracts
and StageTool definitions.  It deliberately starts with the stages that are
most affected by scope, prefix, file resolution, and runtime repair.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ArtifactNeed:
    role: str
    kind: str
    required: bool = True
    source_roles: List[str] = field(default_factory=list)
    alternatives: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactProduct:
    role: str
    kind: str
    canonical_name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StageCapability:
    stage: str
    cli: str
    env: str
    category: str
    consumes: List[ArtifactNeed] = field(default_factory=list)
    provides: List[ArtifactProduct] = field(default_factory=list)
    scope_modes: List[str] = field(default_factory=list)
    preflight_checks: List[str] = field(default_factory=list)
    postflight_checks: List[str] = field(default_factory=list)
    repair_candidates: List[str] = field(default_factory=list)
    rerun_policy: str = "manual"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["consumes"] = [item.to_dict() for item in self.consumes]
        data["provides"] = [item.to_dict() for item in self.provides]
        return data


CAPABILITIES: Dict[str, StageCapability] = {
    "wgs_qc": StageCapability(
        stage="wgs_qc", cli="apps/cli/run_wgs_qc.py", env="test4py311", category="wgs",
        consumes=[ArtifactNeed("wgs_sample_config", "yaml")],
        provides=[
            ArtifactProduct("wgs_sample_config", "yaml"), ArtifactProduct("clean_reads_r1", "fastq_gz"),
            ArtifactProduct("clean_reads_r2", "fastq_gz"), ArtifactProduct("fastp_html", "html"),
            ArtifactProduct("fastp_json", "json"),
        ],
        scope_modes=["wgs_sample_set_id"],
        preflight_checks=["exactly_one_wgs_sample_config", "all_declared_fastqs_valid", "fastp_available"],
        postflight_checks=["all_samples_complete", "sample_set_conservation", "all_required_artifacts_hashed"],
    ),
    "wgs_index": StageCapability(
        stage="wgs_index", cli="apps/cli/run_wgs_index.py", env="test4py311", category="wgs",
        consumes=[ArtifactNeed("reference_fasta", "fasta")],
        provides=[ArtifactProduct("reference_fasta", "fasta"), ArtifactProduct("reference_fai", "fai"),
                  ArtifactProduct("reference_dict", "dict"), ArtifactProduct("bwa_mem2_index_component", "index")],
        scope_modes=["wgs_reference_id"],
        preflight_checks=["explicit_reference_fasta", "reference_parseable_unique_contigs", "index_tools_available"],
        postflight_checks=["required_reference_indices_complete", "all_required_artifacts_hashed"],
    ),
    "wgs_align": StageCapability(
        stage="wgs_align", cli="apps/cli/run_wgs_align.py", env="test4py311", category="wgs",
        consumes=[ArtifactNeed("clean_reads_r1", "fastq_gz"), ArtifactNeed("clean_reads_r2", "fastq_gz"),
                  ArtifactNeed("reference_fasta", "fasta"), ArtifactNeed("reference_fai", "fai"),
                  ArtifactNeed("reference_dict", "dict"), ArtifactNeed("bwa_mem2_index_component", "index")],
        provides=[ArtifactProduct("sorted_bam", "bam"), ArtifactProduct("sorted_bam_index", "bai"),
                  ArtifactProduct("align_log", "log")],
        scope_modes=["wgs_sample_set_id", "wgs_reference_id"],
        preflight_checks=["authoritative_qc_manifest", "authoritative_index_manifest", "alignment_tools_available"],
        postflight_checks=["bam_quickcheck", "bam_index_valid", "read_group_sample_identity", "sample_set_conservation"],
    ),
    "pop_pca": StageCapability(
        stage="pop_pca", cli="apps/cli/run_pop_pca.py", env="ASAW", category="population_genomics",
        consumes=[ArtifactNeed("population_vcf", "vcf_gz"), ArtifactNeed("sample_group", "table", required=False)],
        provides=[
            ArtifactProduct("pca_scores", "table"), ArtifactProduct("pca_eigenvalues", "table"),
            ArtifactProduct("pca_variance_table", "table"), ArtifactProduct("pca_pruning_markers", "id_list"),
            ArtifactProduct("pca_pc1_pc2_plot", "figure"), ArtifactProduct("pca_scree_plot", "figure"),
            ArtifactProduct("pca_tassel_covariate", "tassel_covariate"), ArtifactProduct("report", "report"),
            ArtifactProduct("environment", "environment"), ArtifactProduct("manifest", "manifest"),
        ],
        scope_modes=["genotype_sha256", "population_analysis_sha256"],
        preflight_checks=["population_vcf_valid", "plink_available", "sample_group_valid_if_present"],
        postflight_checks=["biallelic_marker_policy_recorded", "pruned_markers_nonempty", "pca_dimensions_valid", "all_required_artifacts_hashed"],
    ),
    "pop_phylogeny": StageCapability(
        stage="pop_phylogeny", cli="apps/cli/run_pop_phylogeny.py", env="ASAW", category="population_genomics",
        consumes=[ArtifactNeed("population_vcf", "vcf_gz")],
        provides=[
            ArtifactProduct("phylogeny_alignment_phylip", "phylip"), ArtifactProduct("phylogeny_newick", "newick"),
            ArtifactProduct("phylogeny_rectangular_plot", "figure"), ArtifactProduct("phylogeny_circular_plot", "figure"),
            ArtifactProduct("alignment_qc", "json"), ArtifactProduct("tree_qc", "json"),
            ArtifactProduct("report", "report"), ArtifactProduct("environment", "environment"),
            ArtifactProduct("manifest", "manifest"),
        ],
        scope_modes=["genotype_sha256", "population_analysis_sha256"],
        preflight_checks=["population_vcf_valid", "tassel_available", "fasttree_available", "r_ape_available"],
        postflight_checks=["alignment_matches_vcf_samples", "tree_tips_match_vcf_samples", "all_required_artifacts_hashed"],
    ),
    "pop_structure": StageCapability(
        stage="pop_structure", cli="apps/cli/run_pop_structure.py", env="ASAW", category="population_genomics",
        consumes=[ArtifactNeed("population_vcf", "vcf_gz")],
        provides=[
            ArtifactProduct("ld_pruned_bed", "plink_bed"), ArtifactProduct("ld_pruned_bim", "plink_bim"),
            ArtifactProduct("ld_pruned_fam", "plink_fam"), ArtifactProduct("pruning_markers", "id_list"),
            ArtifactProduct("cv_table", "table"), ArtifactProduct("best_k_q", "matrix"),
            ArtifactProduct("best_k_p", "matrix"), ArtifactProduct("structure_best_k_plot", "figure"),
            ArtifactProduct("structure_cv_plot", "figure"), ArtifactProduct("report", "report"),
            ArtifactProduct("environment", "environment"), ArtifactProduct("manifest", "manifest"),
        ],
        scope_modes=["genotype_sha256", "population_analysis_sha256"],
        preflight_checks=["population_vcf_valid", "plink_available", "admixture_available", "k_range_valid"],
        postflight_checks=["all_requested_k_complete", "q_fam_order_conservation", "best_k_from_finite_cv", "all_required_artifacts_hashed"],
    ),
    "pop_ld_decay": StageCapability(
        stage="pop_ld_decay", cli="apps/cli/run_pop_ld_decay.py", env="ASAW", category="population_genomics",
        consumes=[ArtifactNeed("population_vcf", "vcf_gz"), ArtifactNeed("sample_group", "table", required=False)],
        provides=[
            ArtifactProduct("ld_all_stat", "table_gz"), ArtifactProduct("ld_all_plot_png", "figure"),
            ArtifactProduct("ld_all_plot_pdf", "figure"), ArtifactProduct("group_sample_qc", "json"),
            ArtifactProduct("report", "report"), ArtifactProduct("environment", "environment"),
            ArtifactProduct("manifest", "manifest"),
        ],
        scope_modes=["genotype_sha256", "population_analysis_sha256"],
        preflight_checks=["population_vcf_valid", "bcftools_available", "poplddecay_available", "sample_group_valid_if_present"],
        postflight_checks=["requested_group_outputs_complete", "ld_statistics_nonempty", "all_required_artifacts_hashed"],
    ),
    "pop_snp_density": StageCapability(
        stage="pop_snp_density", cli="apps/cli/run_pop_snp_density.py", env="ASAW", category="population_genomics",
        consumes=[ArtifactNeed("population_vcf", "vcf_gz")],
        provides=[
            ArtifactProduct("snp_coordinates", "table"), ArtifactProduct("density_1mb_table", "table"),
            ArtifactProduct("density_500kb_table", "table"), ArtifactProduct("density_1mb_plot", "figure"),
            ArtifactProduct("density_500kb_plot", "figure"), ArtifactProduct("density_heatmap_500kb", "figure"),
            ArtifactProduct("chromosome_snp_count_plot", "figure"), ArtifactProduct("report", "report"),
            ArtifactProduct("environment", "environment"), ArtifactProduct("manifest", "manifest"),
        ],
        scope_modes=["genotype_sha256", "population_analysis_sha256"],
        preflight_checks=["population_vcf_valid", "chromosome_policy_valid", "r_cmplot_available"],
        postflight_checks=["density_window_count_conservation", "canonical_plots_from_current_attempt", "all_required_artifacts_hashed"],
    ),
    "wgs_markdup": StageCapability(
        stage="wgs_markdup", cli="apps/cli/run_wgs_markdup.py", env="test4py311", category="wgs",
        consumes=[ArtifactNeed("sorted_bam", "bam"), ArtifactNeed("sorted_bam_index", "bai")],
        provides=[ArtifactProduct("markdup_bam", "bam"), ArtifactProduct("markdup_bam_index", "bai"),
                  ArtifactProduct("markdup_metrics", "txt"), ArtifactProduct("markdup_log", "log")],
        scope_modes=["wgs_sample_set_id", "wgs_reference_id"],
        preflight_checks=["authoritative_align_manifest", "gatk_available"],
        postflight_checks=["bam_quickcheck", "bam_index_valid", "markdup_metrics_valid",
                           "read_group_sample_identity", "sample_set_conservation"],
    ),
    "wgs_bam_qc": StageCapability(
        stage="wgs_bam_qc", cli="apps/cli/run_wgs_bam_qc.py", env="rnaseq", category="wgs",
        consumes=[ArtifactNeed("markdup_bam", "bam"), ArtifactNeed("markdup_bam_index", "bai"),
                  ArtifactNeed("reference_fai", "fai")],
        provides=[ArtifactProduct("bam_flagstat", "txt"), ArtifactProduct("bam_depth_summary", "tsv"),
                  ArtifactProduct("bam_qc_summary", "tsv"), ArtifactProduct("bam_qc_report", "markdown")],
        scope_modes=["wgs_sample_set_id", "wgs_reference_id"],
        preflight_checks=["authoritative_markdup_manifest", "authoritative_index_manifest", "samtools_available"],
        postflight_checks=["all_bams_measured", "zero_coverage_in_reference_denominator",
                           "sample_set_conservation"],
    ),
    "wgs_haplotype_calling": StageCapability(
        stage="wgs_haplotype_calling",
        cli="apps/cli/run_wgs_haplotype_calling.py",
        env="ASAW",
        category="wgs",
        consumes=[
            ArtifactNeed("markdup_bam", "bam"),
            ArtifactNeed("markdup_bam_index", "bai"),
            ArtifactNeed("bam_flagstat", "txt"),
            ArtifactNeed("bam_depth_summary", "tsv"),
            ArtifactNeed("bam_qc_summary", "tsv"),
            ArtifactNeed("bam_qc_report", "markdown"),
            ArtifactNeed("reference_fasta", "fasta"),
        ],
        provides=[
            ArtifactProduct("gvcf", "vcf_gz"),
            ArtifactProduct("gvcf_index", "tbi"),
            ArtifactProduct("haplotype_log", "log"),
        ],
        scope_modes=["wgs_sample_set_id", "wgs_reference_id"],
        preflight_checks=[
            "authoritative_markdup_manifest",
            "authoritative_bam_qc_manifest",
            "authoritative_index_manifest",
            "gatk_available",
        ],
        postflight_checks=[
            "gvcf_content_valid",
            "gvcf_index_valid",
            "internal_sample_identity",
            "sample_set_conservation",
        ],
    ),
    "wgs_gvcf_combine": StageCapability(
        stage="wgs_gvcf_combine",
        cli="apps/cli/run_wgs_gvcf_combine.py",
        env="ASAW",
        category="wgs",
        consumes=[
            ArtifactNeed("gvcf", "vcf_gz"),
            ArtifactNeed("gvcf_index", "tbi"),
            ArtifactNeed("reference_fasta", "fasta"),
            ArtifactNeed("reference_fai", "fai"),
            ArtifactNeed("reference_dict", "dict"),
        ],
        provides=[
            ArtifactProduct("combined_gvcf", "vcf_gz"),
            ArtifactProduct("combined_gvcf_index", "tbi"),
            ArtifactProduct("combine_log", "log"),
        ],
        scope_modes=["wgs_sample_set_id", "wgs_reference_id"],
        preflight_checks=[
            "authoritative_haplotype_manifest",
            "authoritative_index_manifest",
            "all_input_gvcf_indices_queryable",
            "gatk_available",
        ],
        postflight_checks=[
            "combined_gvcf_content_valid",
            "combined_gvcf_index_valid",
            "sample_set_conservation",
        ],
    ),
    "wgs_joint_genotyping": StageCapability(
        stage="wgs_joint_genotyping",
        cli="apps/cli/run_wgs_joint_genotyping.py",
        env="ASAW",
        category="wgs",
        consumes=[
            ArtifactNeed("combined_gvcf", "vcf_gz"),
            ArtifactNeed("combined_gvcf_index", "tbi"),
            ArtifactNeed("reference_fasta", "fasta"),
            ArtifactNeed("reference_fai", "fai"),
            ArtifactNeed("reference_dict", "dict"),
        ],
        provides=[
            ArtifactProduct("joint_vcf", "vcf_gz"),
            ArtifactProduct("joint_vcf_index", "tbi"),
            ArtifactProduct("joint_summary", "tsv"),
            ArtifactProduct("joint_log", "log"),
        ],
        scope_modes=["wgs_sample_set_id", "wgs_reference_id"],
        preflight_checks=[
            "authoritative_combine_manifest",
            "authoritative_index_manifest",
            "combined_gvcf_index_queryable",
            "gatk_available",
        ],
        postflight_checks=[
            "joint_vcf_content_valid",
            "joint_vcf_index_valid",
            "joint_summary_valid",
            "sample_set_conservation",
        ],
    ),
    "wgs_variant_select": StageCapability(
        stage="wgs_variant_select",
        cli="apps/cli/run_wgs_variant_select.py",
        env="ASAW",
        category="wgs",
        consumes=[
            ArtifactNeed("joint_vcf", "vcf_gz"),
            ArtifactNeed("joint_vcf_index", "tbi"),
        ],
        provides=[
            ArtifactProduct("selected_snp_vcf", "vcf_gz"),
            ArtifactProduct("selected_snp_vcf_index", "tbi"),
            ArtifactProduct("selected_indel_vcf", "vcf_gz"),
            ArtifactProduct("selected_indel_vcf_index", "tbi"),
            ArtifactProduct("variant_select_report", "markdown"),
        ],
        scope_modes=["wgs_sample_set_id", "wgs_reference_id"],
        preflight_checks=[
            "authoritative_joint_manifest",
            "joint_vcf_index_queryable",
            "selected_method_available",
        ],
        postflight_checks=[
            "selected_vcf_types_valid",
            "selected_vcf_indices_valid",
            "sample_set_conservation",
            "actual_method_recorded",
        ],
    ),
    "wgs_variant_filter": StageCapability(
        stage="wgs_variant_filter",
        cli="apps/cli/run_wgs_variant_filter.py",
        env="ASAW",
        category="wgs",
        consumes=[
            ArtifactNeed("selected_snp_vcf", "vcf_gz"),
            ArtifactNeed("selected_snp_vcf_index", "tbi"),
            ArtifactNeed("selected_indel_vcf", "vcf_gz"),
            ArtifactNeed("selected_indel_vcf_index", "tbi"),
        ],
        provides=[
            ArtifactProduct("filtered_snp_vcf", "vcf_gz"),
            ArtifactProduct("filtered_snp_vcf_index", "tbi"),
            ArtifactProduct("pass_snp_vcf", "vcf_gz"),
            ArtifactProduct("pass_snp_vcf_index", "tbi"),
            ArtifactProduct("filtered_indel_vcf", "vcf_gz"),
            ArtifactProduct("filtered_indel_vcf_index", "tbi"),
            ArtifactProduct("pass_indel_vcf", "vcf_gz"),
            ArtifactProduct("pass_indel_vcf_index", "tbi"),
            ArtifactProduct("variant_filter_report", "markdown"),
        ],
        scope_modes=["wgs_sample_set_id", "wgs_reference_id"],
        preflight_checks=[
            "authoritative_variant_select_manifest",
            "all_selected_vcf_indices_queryable",
            "selected_method_available",
        ],
        postflight_checks=[
            "full_filter_expressions_recorded",
            "all_filtered_and_pass_indices_valid",
            "pass_semantics_valid",
            "sample_set_conservation",
            "actual_method_recorded",
        ],
    ),
    "wgs_impute": StageCapability(
        stage="wgs_impute",
        cli="apps/cli/run_wgs_impute.py",
        env="test4py311",
        category="wgs",
        consumes=[
            ArtifactNeed("pass_snp_vcf", "vcf_gz"),
            ArtifactNeed("pass_snp_vcf_index", "tbi"),
        ],
        provides=[
            ArtifactProduct("imputed_snp_vcf", "vcf_gz"),
            ArtifactProduct("imputed_snp_vcf_index", "tbi"),
            ArtifactProduct("impute_report", "markdown"),
            ArtifactProduct("impute_log", "log"),
        ],
        scope_modes=["wgs_sample_set_id", "wgs_reference_id"],
        preflight_checks=[
            "authoritative_variant_filter_manifest",
            "pass_snp_vcf_index_queryable",
            "java_and_beagle_available",
        ],
        postflight_checks=[
            "imputed_vcf_content_valid",
            "imputed_vcf_index_valid",
            "sample_set_conservation",
            "ordered_site_key_conservation",
        ],
    ),
    "wgs_maf_missing_filter": StageCapability(
        stage="wgs_maf_missing_filter",
        cli="apps/cli/run_wgs_maf_missing_filter.py",
        env="ASAW",
        category="wgs",
        consumes=[
            ArtifactNeed("imputed_snp_vcf", "vcf_gz"),
            ArtifactNeed("imputed_snp_vcf_index", "tbi"),
        ],
        provides=[
            ArtifactProduct("final_snp_vcf", "vcf_gz"),
            ArtifactProduct("final_snp_vcf_index", "tbi"),
            ArtifactProduct("maf_missing_report", "markdown"),
            ArtifactProduct("maf_missing_stats", "tsv"),
        ],
        scope_modes=["wgs_sample_set_id", "wgs_reference_id"],
        preflight_checks=[
            "authoritative_impute_manifest",
            "imputed_snp_vcf_index_queryable",
            "bcftools_fill_tags_available",
        ],
        postflight_checks=[
            "sample_set_conservation",
            "gt_derived_maf_missing_thresholds",
            "exact_retained_site_set",
            "final_snp_vcf_index_valid",
            "at_least_one_gwas_snp",
        ],
    ),
    "gwas_fmt": StageCapability(
        stage="gwas_fmt",
        cli="apps/cli/run_gwas_tassel.py",
        env="test4py311",
        category="gwas",
        consumes=[ArtifactNeed("external_vcf", "vcf")],
        provides=[
            ArtifactProduct("sorted_vcf", "vcf_gz", "{prefix}.gwas_fmt.sorted.vcf.gz"),
            ArtifactProduct("tassel_hapmap", "hapmap", "{prefix}.gwas_fmt.hapmap.hmp.txt"),
            ArtifactProduct("gwas_fmt_manifest", "manifest", "{prefix}.gwas_fmt.manifest.json"),
        ],
        scope_modes=["genotype_sha256"],
        preflight_checks=["external_vcf_present", "vcf_header_valid", "tassel_available"],
        postflight_checks=["sample_conservation", "variant_conservation", "artifact_sha256_present"],
        repair_candidates=["external_vcf_reselect", "tassel_environment_repair"],
        rerun_policy="manual",
    ),
    "gwas_kinship": StageCapability(
        stage="gwas_kinship",
        cli="apps/cli/run_gwas_kinship.py",
        env="test4py311",
        category="gwas",
        consumes=[ArtifactNeed("sorted_vcf", "vcf_gz")],
        provides=[
            ArtifactProduct("kinship_matrix", "matrix", "{prefix}.gwas_kinship.kinship.txt"),
            ArtifactProduct("gwas_kinship_manifest", "manifest", "{prefix}.gwas_kinship.manifest.json"),
        ],
        scope_modes=["genotype_sha256"],
        preflight_checks=["gwas_fmt_manifest_valid", "tassel_available"],
        postflight_checks=["kinship_square", "sample_order_conservation", "kinship_psd"],
    ),
    "gwas_trait": StageCapability(
        stage="gwas_trait",
        cli="apps/cli/run_gwas_trait.py",
        env="test4py311",
        category="gwas",
        consumes=[ArtifactNeed("phenotype_table", "table")],
        provides=[
            ArtifactProduct("trait_table", "tassel_trait", "{prefix}.gwas_trait.trait.txt"),
            ArtifactProduct("gwas_trait_manifest", "manifest", "{prefix}.gwas_trait.manifest.json"),
        ],
        scope_modes=["phenotype_sha256"],
        preflight_checks=["sample_and_trait_columns_selected"],
        postflight_checks=["sample_ids_unique", "trait_numeric_finite", "trait_variance_positive"],
    ),
    "gwas_cov": StageCapability(
        stage="gwas_cov",
        cli="apps/cli/run_gwas_covariate.py",
        env="test4py311",
        category="gwas",
        consumes=[ArtifactNeed("admixture_q", "matrix"), ArtifactNeed("sample_fam", "plink_fam")],
        provides=[
            ArtifactProduct("covariate_table", "tassel_covariate", "{prefix}.gwas_cov.covariate.txt"),
            ArtifactProduct("gwas_cov_manifest", "manifest", "{prefix}.gwas_cov.manifest.json"),
        ],
        scope_modes=["covariate_sha256"],
        preflight_checks=["q_fam_pair_confirmed", "source_genotype_lineage_valid"],
        postflight_checks=["q_fam_order_conservation", "q_rows_sum_to_one", "last_q_column_omitted"],
    ),
    "gwas_mlm": StageCapability(
        stage="gwas_mlm",
        cli="apps/cli/run_gwas_tassel_mlm.py",
        env="test4py311",
        category="gwas",
        consumes=[
            ArtifactNeed("sorted_vcf", "vcf_gz"),
            ArtifactNeed("kinship_matrix", "matrix"),
            ArtifactNeed("trait_table", "tassel_trait"),
            ArtifactNeed("covariate_table", "tassel_covariate"),
        ],
        provides=[
            ArtifactProduct("mlm_full", "association_table", "{prefix}.gwas_mlm.mlm.full.tsv"),
            ArtifactProduct("cmlm_full", "association_table", "{prefix}.gwas_mlm.cmlm.full.tsv"),
            ArtifactProduct("mlm_pvalues", "pvalue_table", "{prefix}.gwas_mlm.mlm.pvalues.tsv"),
            ArtifactProduct("cmlm_pvalues", "pvalue_table", "{prefix}.gwas_mlm.cmlm.pvalues.tsv"),
            ArtifactProduct("manifest", "manifest", "{prefix}.gwas_mlm.manifest.json"),
            ArtifactProduct("report", "report", "{prefix}.gwas_mlm.report.md"),
            ArtifactProduct("environment", "environment", "{prefix}.gwas_mlm.environment.txt"),
        ],
        scope_modes=["model_scope"],
        preflight_checks=["upstream_identity_match", "model_design_estimable"],
        postflight_checks=["mlm_and_cmlm_success", "association_columns_valid"],
    ),
    "gwas_plot": StageCapability(
        stage="gwas_plot",
        cli="apps/cli/run_gwas_plot.py",
        env="test4py311",
        category="gwas",
        consumes=[
            ArtifactNeed("mlm_pvalues", "pvalue_table"),
            ArtifactNeed("cmlm_pvalues", "pvalue_table"),
        ],
        provides=[
            ArtifactProduct("mlm_manhattan", "figure", "{prefix}.gwas_plot.mlm_manhattan.png"),
            ArtifactProduct("mlm_qq", "figure", "{prefix}.gwas_plot.mlm_qq.png"),
            ArtifactProduct("cmlm_manhattan", "figure", "{prefix}.gwas_plot.cmlm_manhattan.png"),
            ArtifactProduct("cmlm_qq", "figure", "{prefix}.gwas_plot.cmlm_qq.png"),
            ArtifactProduct("manifest", "manifest", "{prefix}.gwas_plot.manifest.json"),
            ArtifactProduct("report", "report", "{prefix}.gwas_plot.report.md"),
            ArtifactProduct("environment", "environment", "{prefix}.gwas_plot.environment.txt"),
        ],
        scope_modes=["source_model_scope"],
        preflight_checks=["mlm_manifest_valid"],
        postflight_checks=["plot_files_valid", "bonferroni_denominator_recorded"],
    ),
    "gwas_emmax": StageCapability(
        stage="gwas_emmax",
        cli="apps/cli/run_gwas_emmax.py",
        env="test4py311",
        category="gwas",
        consumes=[ArtifactNeed("sorted_vcf", "vcf_gz"), ArtifactNeed("trait_table", "tassel_trait")],
        provides=[
            ArtifactProduct("emmax_full", "association_table", "{prefix}.gwas_emmax.emmax.full.tsv"),
            ArtifactProduct("emmax_pvalues", "pvalue_table", "{prefix}.gwas_emmax.emmax.pvalues.tsv"),
            ArtifactProduct("manifest", "manifest", "{prefix}.gwas_emmax.manifest.json"),
            ArtifactProduct("report", "report", "{prefix}.gwas_emmax.report.md"),
            ArtifactProduct("environment", "environment", "{prefix}.gwas_emmax.environment.txt"),
        ],
        scope_modes=["model_scope"],
        preflight_checks=["upstream_identity_match", "plink_and_emmax_available", "model_design_estimable"],
        postflight_checks=["sample_order_conservation", "kinship_psd", "association_columns_valid"],
    ),
    "gwas_emplot": StageCapability(
        stage="gwas_emplot",
        cli="apps/cli/run_gwas_emmax_plot.py",
        env="test4py311",
        category="gwas",
        consumes=[ArtifactNeed("emmax_pvalues", "pvalue_table")],
        provides=[
            ArtifactProduct("emmax_manhattan", "figure", "{prefix}.gwas_emplot.emmax_manhattan.png"),
            ArtifactProduct("emmax_qq", "figure", "{prefix}.gwas_emplot.emmax_qq.png"),
            ArtifactProduct("manifest", "manifest", "{prefix}.gwas_emplot.manifest.json"),
            ArtifactProduct("report", "report", "{prefix}.gwas_emplot.report.md"),
            ArtifactProduct("environment", "environment", "{prefix}.gwas_emplot.environment.txt"),
        ],
        scope_modes=["source_model_scope"],
        preflight_checks=["emmax_manifest_valid", "upstream_identity_match"],
        postflight_checks=["plot_files_valid", "bonferroni_denominator_recorded"],
    ),
    "gwas_glm": StageCapability(
        stage="gwas_glm",
        cli="apps/cli/run_gwas_tassel_glm.py",
        env="test4py311",
        category="gwas",
        consumes=[
            ArtifactNeed("sorted_vcf", "vcf_gz"),
            ArtifactNeed("trait_table", "tassel_trait"),
            ArtifactNeed("covariate_table", "tassel_covariate"),
        ],
        provides=[
            ArtifactProduct("glm_full", "association_table", "{prefix}.gwas_glm.glm.full.txt"),
            ArtifactProduct("glm_structure_full", "association_table", "{prefix}.gwas_glm.glm_structure.full.txt"),
            ArtifactProduct("glm_pvalues", "pvalue_table", "{prefix}.gwas_glm.glm.pvalues.tsv"),
            ArtifactProduct("glm_structure_pvalues", "pvalue_table", "{prefix}.gwas_glm.glm_structure.pvalues.tsv"),
            ArtifactProduct("manifest", "manifest", "{prefix}.gwas_glm.manifest.json"),
            ArtifactProduct("report", "report", "{prefix}.gwas_glm.report.md"),
            ArtifactProduct("environment", "environment", "{prefix}.gwas_glm.environment.txt"),
        ],
        scope_modes=["model_scope"],
        preflight_checks=["upstream_identity_match", "model_design_estimable"],
        postflight_checks=["glm_success", "association_columns_valid"],
    ),
    "genome_prepare": StageCapability(
        stage="genome_prepare",
        cli="apps/cli/run_genome_prepare.py",
        env="test4py311",
        category="prepare",
        consumes=[
            ArtifactNeed("raw_genome", "genome_fasta", required=False),
            ArtifactNeed("raw_gff", "gff", required=False),
            ArtifactNeed("raw_protein", "protein_fasta", required=False),
            ArtifactNeed("raw_cds", "cds_fasta", required=False),
            ArtifactNeed("config", "config_xlsx", required=False),
        ],
        provides=[
            ArtifactProduct("prepared_protein", "protein_fasta", "{Species}.{Family}.pep.fa"),
            ArtifactProduct("prepared_cds", "cds_fasta", "{Species}.{Family}.cds.fa"),
            ArtifactProduct("prepared_gff", "gff", "{Species}_final.gff3"),
            ArtifactProduct("genome_prepare_manifest", "manifest", "{prefix}.genome_prepare.manifest.json"),
        ],
        scope_modes=["all_species"],
        preflight_checks=["config_or_input_files_present"],
        postflight_checks=["prepared_pep_not_genome_like", "final_gff_not_empty", "longest_transcript_outputs_present"],
        repair_candidates=["genome_prepare_extract_sequences", "genome_prepare_final_gff_empty"],
        rerun_policy="auto_after_safe_repair",
    ),
    "ncbi_fetch_sequences": StageCapability(
        stage="ncbi_fetch_sequences",
        cli="apps/cli/run_ncbi_fetch_sequences.py",
        env="test4py311",
        category="utility",
        consumes=[
            ArtifactNeed("gene_id_list", "id_list", alternatives=["inline_ids", "uploaded_filename", "absolute_path"]),
        ],
        provides=[
            ArtifactProduct("downloaded_protein", "protein_fasta", "{prefix}.ncbi_fetch.protein.fa"),
            ArtifactProduct("downloaded_cds", "cds_fasta", "{prefix}.ncbi_fetch.cds.fa"),
            ArtifactProduct("ncbi_fetch_manifest", "manifest", "{prefix}.ncbi_fetch.manifest.json"),
        ],
        scope_modes=["utility"],
        preflight_checks=["id_list_resolves_from_input_final_or_text"],
        postflight_checks=["downloaded_sequences_nonempty", "headers_have_primary_ids"],
        repair_candidates=["ncbi_reference_family_symlink", "manifest_recovery"],
        rerun_policy="manual_for_network_failure",
    ),
    "sequence_extract_by_id": StageCapability(
        stage="sequence_extract_by_id",
        cli="apps/cli/run_sequence_extract_by_id.py",
        env="test4py311",
        category="utility",
        consumes=[
            ArtifactNeed("source_fasta", "fasta", alternatives=["final_artifact", "uploaded_filename", "absolute_path"]),
            ArtifactNeed("id_list", "id_list", alternatives=["inline_ids", "uploaded_filename", "absolute_path"]),
        ],
        provides=[
            ArtifactProduct("extracted_sequences", "fasta", "{prefix}.sequence_extract.fa"),
            ArtifactProduct("sequence_extract_manifest", "manifest", "{prefix}.sequence_extract.manifest.json"),
        ],
        scope_modes=["utility"],
        preflight_checks=["source_fasta_resolved", "ids_resolved"],
        postflight_checks=["extracted_records_nonempty"],
        repair_candidates=["source_fasta_candidate_confirmation", "id_alias_matching"],
        rerun_policy="confirm_when_multiple_sources",
    ),
    "identify": StageCapability(
        stage="identify",
        cli="apps/cli/run_identify.py",
        env="test4py311",
        category="gene_family",
        consumes=[
            ArtifactNeed("target_proteome", "protein_fasta", source_roles=["target", "blast_database"], alternatives=["genome_prepare_pep", "config_pep", "input_pep"]),
            ArtifactNeed("reference_family_pep", "protein_fasta", required=False, source_roles=["reference", "blast_query"], alternatives=["ncbi_fetch_symlink", "ref_root"]),
            ArtifactNeed("hmm_profile", "hmm", required=False, alternatives=["required_pfams", "local_pfam"]),
            ArtifactNeed("scope_file", "scope_json", required=False),
        ],
        provides=[
            ArtifactProduct("family_members_id", "id_list", "{prefix}.identified_members.id"),
            ArtifactProduct("family_members_pep", "protein_fasta", "{prefix}.identified_members.pep.fa"),
            ArtifactProduct("interproscan_tsv", "table", "{prefix}.identified_members.interproscan.tsv"),
            ArtifactProduct("identify_manifest", "manifest", "{prefix}.identify.manifest.json"),
        ],
        scope_modes=["target_only"],
        preflight_checks=["target_protein_resolved", "pfam_or_hmm_resolved"],
        postflight_checks=["identified_members_nonempty", "interproscan_domain_status_recorded"],
        repair_candidates=["pfam_remote_404_fallback", "protein_stop_marker_clean", "artifact_reresolve"],
        rerun_policy="auto_once_after_safe_repair",
    ),
    "phylogeny": StageCapability(
        stage="phylogeny",
        cli="apps/cli/run_phylogeny.py",
        env="test4py311",
        category="gene_family",
        consumes=[
            ArtifactNeed("family_members_pep", "protein_fasta", alternatives=["identify_manifest", "final_members_pep"]),
            ArtifactNeed("reference_family_pep", "protein_fasta", required=False, source_roles=["reference"]),
            ArtifactNeed("scope_file", "scope_json", required=False),
        ],
        provides=[
            ArtifactProduct("msa", "alignment_fasta", "{prefix}.pep.msa.fa"),
            ArtifactProduct("tree", "tree", "{prefix}.tree.nwk"),
            ArtifactProduct("phylogeny_manifest", "manifest", "{prefix}.phylogeny.manifest.json"),
        ],
        scope_modes=["target_only", "target_plus_reference", "all_selected"],
        preflight_checks=["protein_input_resolved", "scope_selected_species_have_family_pep"],
        postflight_checks=["tree_tips_match_input_records", "scoped_input_recorded"],
        repair_candidates=["protein_stop_marker_clean", "ncbi_reference_family_symlink"],
        rerun_policy="auto_once_after_safe_repair",
    ),
    "reference_classification": StageCapability(
        stage="reference_classification",
        cli="apps/cli/run_reference_classification.py",
        env="test4py311",
        category="gene_family",
        consumes=[
            ArtifactNeed("tree", "tree", alternatives=["phylogeny_manifest", "final_tree"]),
            ArtifactNeed("reference_subfamily", "table", source_roles=["reference"], alternatives=["input_reference_labels", "final_reference_labels"]),
            ArtifactNeed("scope_file", "scope_json", required=False),
        ],
        provides=[
            ArtifactProduct("gene_subfamily", "table", "{prefix}.geneID.subfamily"),
            ArtifactProduct("reference_classification_manifest", "manifest", "{prefix}.reference_classification.manifest.json"),
        ],
        scope_modes=["target_plus_reference"],
        preflight_checks=["tree_resolved", "reference_labels_resolved"],
        postflight_checks=["subfamily_labels_nonempty", "target_genes_classified"],
        repair_candidates=["reference_label_reresolve", "tree_scope_mismatch_report"],
        rerun_policy="manual",
    ),
    "motif": StageCapability(
        stage="motif",
        cli="apps/cli/run_motif.py",
        env="test4py311",
        category="gene_family",
        consumes=[
            ArtifactNeed("family_members_pep", "protein_fasta", alternatives=["scoped_phylogeny_input", "final_members_pep"]),
            ArtifactNeed("tree", "tree", alternatives=["phylogeny_manifest", "final_tree"]),
            ArtifactNeed("gene_subfamily", "table", required=False, alternatives=["reference_classification", "input_subfamily"]),
        ],
        provides=[
            ArtifactProduct("motif_plot", "figure", "{prefix}.motif.png"),
            ArtifactProduct("motif_manifest", "manifest", "{prefix}.motif.manifest.json"),
        ],
        scope_modes=["target_only", "target_plus_reference", "all_selected"],
        preflight_checks=["protein_input_resolved", "tree_resolved"],
        postflight_checks=["motif_outputs_nonempty"],
        repair_candidates=["protein_stop_marker_clean"],
        rerun_policy="auto_once_after_safe_repair",
    ),
    "domain": StageCapability(
        stage="domain",
        cli="apps/cli/run_domain.py",
        env="test4py311",
        category="gene_family",
        consumes=[
            ArtifactNeed("family_members_pep", "protein_fasta", alternatives=["scoped_phylogeny_input", "final_members_pep"]),
            ArtifactNeed("tree", "tree", alternatives=["phylogeny_manifest", "final_tree"]),
            ArtifactNeed("pfam_database", "hmm_database"),
            ArtifactNeed("gene_subfamily", "table", required=False),
        ],
        provides=[
            ArtifactProduct("domain_plot", "figure", "{prefix}.domain.png"),
            ArtifactProduct("domain_table", "table", "{prefix}.domain.tsv"),
            ArtifactProduct("domain_manifest", "manifest", "{prefix}.domain.manifest.json"),
        ],
        scope_modes=["target_only", "target_plus_reference", "all_selected"],
        preflight_checks=["pfam_database_indexed", "protein_input_resolved", "tree_resolved"],
        postflight_checks=["domain_outputs_nonempty"],
        repair_candidates=["protein_stop_marker_clean", "pfam_index_missing_report"],
        rerun_policy="auto_once_after_safe_repair",
    ),
    "structure": StageCapability(
        stage="structure",
        cli="apps/cli/run_structure.py",
        env="test4py311",
        category="gene_family",
        consumes=[
            ArtifactNeed("family_members_id", "id_list", alternatives=["identify_manifest", "final_members_id"]),
            ArtifactNeed("tree", "tree", alternatives=["phylogeny_manifest", "final_tree"]),
            ArtifactNeed("target_gff", "gff", source_roles=["target"], alternatives=["scope_gff", "genome_prepare_gff", "input_gff"]),
            ArtifactNeed("gene_subfamily", "table", required=False),
        ],
        provides=[
            ArtifactProduct("structure_plot", "figure", "{prefix}.structure.png"),
            ArtifactProduct("structure_manifest", "manifest", "{prefix}.structure.manifest.json"),
        ],
        scope_modes=["target_only", "target_plus_reference", "all_selected"],
        preflight_checks=["member_ids_resolved", "target_gff_resolved", "tree_resolved"],
        postflight_checks=["id_gff_overlap_nonzero", "structure_plot_exists"],
        repair_candidates=["gff_id_overlap_report", "artifact_reresolve"],
        rerun_policy="manual_after_report",
    ),
    "combo": StageCapability(
        stage="combo",
        cli="apps/cli/run_combo.py",
        env="test4py311",
        category="gene_family",
        consumes=[
            ArtifactNeed("tree", "tree"),
            ArtifactNeed("motif_result", "table", required=False),
            ArtifactNeed("domain_result", "table", required=False),
            ArtifactNeed("structure_result", "table", required=False),
            ArtifactNeed("gene_subfamily", "table", required=False),
        ],
        provides=[
            ArtifactProduct("combo_plot", "figure", "{prefix}.combo.png"),
            ArtifactProduct("combo_manifest", "manifest", "{prefix}.combo.manifest.json"),
        ],
        scope_modes=["target_only", "target_plus_reference", "all_selected"],
        preflight_checks=["tree_resolved", "at_least_one_annotation_panel_resolved"],
        postflight_checks=["combo_plot_exists"],
        repair_candidates=["panel_artifact_reresolve"],
        rerun_policy="manual",
    ),
    "tfbs": StageCapability(
        stage="tfbs",
        cli="apps/cli/run_tfbs.py",
        env="test4py311",
        category="gene_family",
        consumes=[
            ArtifactNeed("family_members_id", "id_list", alternatives=["identify_manifest", "final_members_id"]),
            ArtifactNeed("target_gff", "gff", source_roles=["target"], alternatives=["scope_gff", "genome_prepare_gff", "input_gff"]),
            ArtifactNeed("target_genome", "genome_fasta", source_roles=["target"], alternatives=["scope_genome", "input_genome"]),
            ArtifactNeed("plant_tf_motif_database", "meme", alternatives=["PlantTFDB", "JASPAR_CORE_plants"]),
            ArtifactNeed("taxonomy", "ncbi_taxdump", required=False),
        ],
        provides=[
            ArtifactProduct("tfbs_sites", "table", "{prefix}.tfbs.sites.tsv"),
            ArtifactProduct("tfbs_gene_motif_matrix", "table", "{prefix}.tfbs.gene_motif_matrix.tsv"),
            ArtifactProduct("tfbs_enrichment", "table", "{prefix}.tfbs.enrichment.tsv"),
            ArtifactProduct("tfbs_report", "markdown", "{prefix}.tfbs.report.md"),
            ArtifactProduct("tfbs_manifest", "manifest", "{prefix}.tfbs.manifest.json"),
        ],
        scope_modes=["target_only"],
        preflight_checks=["member_ids_resolved", "target_gff_resolved", "target_genome_resolved", "motif_database_resolved"],
        postflight_checks=["fimo_outputs_nonempty", "tfbs_manifest_artifacts_present"],
        repair_candidates=["gff_id_overlap_report", "motif_database_fallback_to_jaspar", "artifact_reresolve"],
        rerun_policy="manual",
    ),
    "rnaseq_report": StageCapability(
        stage="rnaseq_report",
        cli="apps/cli/run_rnaseq_report.py",
        env="test4py311",
        category="rnaseq",
        consumes=[
            ArtifactNeed("successful_rnaseq_manifests", "manifest", alternatives=["final_manifest_scan"]),
        ],
        provides=[
            ArtifactProduct("rnaseq_report", "markdown", "{prefix}.rnaseq_report.md"),
            ArtifactProduct("rnaseq_report_evidence", "json", "{prefix}.rnaseq_report.evidence.json"),
            ArtifactProduct("rnaseq_report_model", "json", "{prefix}.rnaseq_report.model.json"),
            ArtifactProduct("rnaseq_report_manifest", "manifest", "{prefix}.rnaseq_report.manifest.json"),
        ],
        scope_modes=["rnaseq_scope_id"],
        preflight_checks=["at_least_one_successful_rnaseq_manifest", "prefix_scope_identity_consistent"],
        postflight_checks=["deterministic_markdown_exists", "evidence_snapshot_exists", "coverage_recorded"],
        repair_candidates=["manifest_recovery", "regenerate_from_current_snapshot"],
        rerun_policy="manual_or_after_selected_batch_terminal",
    ),
    "summary": StageCapability(
        stage="summary",
        cli="apps/cli/run_summary.py",
        env="test4py311",
        category="summary",
        consumes=[
            ArtifactNeed("stage_manifests", "manifest", alternatives=["final_manifest_scan"]),
            ArtifactNeed("scope_file", "scope_json", required=False),
        ],
        provides=[
            ArtifactProduct("summary_report", "markdown", "{prefix}.summary_report.md"),
            ArtifactProduct("summary_evidence_index", "markdown", "{prefix}.summary_evidence_index.md"),
            ArtifactProduct("summary_evidence", "json", "{prefix}.summary.evidence.json"),
            ArtifactProduct("deepseek_metadata", "json", "{prefix}.summary.deepseek.json"),
            ArtifactProduct("summary_manifest", "manifest", "{prefix}.summary.manifest.json"),
        ],
        scope_modes=["target_only", "target_plus_reference", "all_selected", "rnaseq"],
        preflight_checks=["at_least_one_success_manifest"],
        postflight_checks=["summary_contains_stage_evidence", "deepseek_report_generation_proven"],
        repair_candidates=["manifest_recovery", "artifact_reresolve"],
        rerun_policy="auto_after_manifest_recovery",
    ),
    "gene_family_report": StageCapability(
        stage="gene_family_report",
        cli="apps/cli/run_gene_family_report.py",
        env="test4py311",
        category="report",
        consumes=[],
        provides=[
            ArtifactProduct("gene_family_report", "markdown", "{prefix}.gene_family_report.md"),
            ArtifactProduct("gene_family_report_evidence", "json", "{prefix}.gene_family_report.evidence.json"),
            ArtifactProduct("gene_family_report_model", "json", "{prefix}.gene_family_report.model.json"),
            ArtifactProduct("gene_family_report_manifest", "manifest", "{prefix}.gene_family_report.manifest.json"),
        ],
        scope_modes=["target_only", "target_plus_reference", "all_selected"],
        preflight_checks=["at_least_one_successful_planned_manifest"],
        postflight_checks=["formal_report_four_files_exist"],
        rerun_policy="open_reuses_fresh_generate_rebuilds",
    ),
}


def get_stage_capability(stage: str) -> Optional[StageCapability]:
    return CAPABILITIES.get(stage)


def list_stage_capabilities() -> List[str]:
    return sorted(CAPABILITIES)


def stage_capability_dict(stage: str) -> Optional[Dict[str, Any]]:
    capability = get_stage_capability(stage)
    return capability.to_dict() if capability else None


def all_stage_capabilities() -> Dict[str, Dict[str, Any]]:
    return {stage: capability.to_dict() for stage, capability in sorted(CAPABILITIES.items())}
