"""Reusable template-case discovery for extension-stage conversion.

This module turns successful, already-executed bbav5 stage evidence into a small
set of representative conversion templates.  It is deliberately stage-agnostic:
new extension APIs should reuse these archetypes instead of inventing a fresh
wrapper/spec/validation pattern for every analysis stage.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TemplateArchetype:
    archetype_id: str
    label: str
    stages: tuple[str, ...]
    analysis_type: str
    tool_family: str
    input_roles: dict[str, dict[str, Any]]
    output_contract: list[dict[str, Any]]
    comparator_plan: dict[str, Any]
    risk_checks: tuple[str, ...]
    prompt_rules: tuple[str, ...]
    code_hints: tuple[str, ...] = ()


@dataclass
class TemplateCase:
    archetype_id: str
    stage: str
    manifest_path: str
    prefix: str
    scope_id: str
    status: str
    command: str
    code_files: list[str] = field(default_factory=list)
    input_files: list[dict[str, Any]] = field(default_factory=list)
    output_files: list[dict[str, Any]] = field(default_factory=list)
    artifact_types: list[str] = field(default_factory=list)
    risk_checks: list[str] = field(default_factory=list)
    output_contract: list[dict[str, Any]] = field(default_factory=list)
    comparator_plan: dict[str, Any] = field(default_factory=dict)


PROJECT_RELATIVE_CODE = {
    "sequence_extraction": ("cli/run_sequence_extract_by_id.py",),
    "hmmer_domain": ("cli/run_domain.py", "scripts/tree_domain.R"),
    "motif_meme": ("cli/run_motif.py", "scripts/get_meme_pos.py", "scripts/tree_motif.R"),
    "phylogeny_tree": ("cli/run_phylogeny.py", "scripts/plot_tree.R", "scripts/tree_plot.R"),
    "reference_classification": ("cli/run_reference_classification.py", "scripts/reference_classification_from_tree.py", "scripts/tree_plot.R"),
    "synteny_collinearity": ("cli/run_synteny.py", "cli/run_interspecies_synteny.py", "scripts/plot_interspecies_synteny.R"),
    "gene_structure_gff": ("cli/run_structure.py",),
    "promoter_cis_element": ("cli/run_promoter.py",),
    "rnaseq_quant_matrix": ("cli/run_rnaseq_quant.py",),
    "rnaseq_de_table": ("cli/run_rnaseq_de.py",),
    "kaks_pairwise": ("cli/run_kaks_v2.py",),
    "pep_properties": ("cli/run_pep.py",),
}


ARCHETYPES: tuple[TemplateArchetype, ...] = (
    TemplateArchetype(
        archetype_id="sequence_extraction",
        label="ID list plus FASTA extraction/cleaning",
        stages=("sequence_extract", "identify", "identify_refine"),
        analysis_type="sequence_extraction",
        tool_family="seqtk",
        input_roles={
            "input_pep_fasta": {"role": "target_pep_fasta", "required": True},
            "input_id_file": {"role": "target_id_list", "required": True},
        },
        output_contract=[
            {"name_pattern": "*.fa", "semantic_role": "extracted_fasta", "compare_mode": "fasta", "required": True},
            {"name_pattern": "*.id", "semantic_role": "id_list", "compare_mode": "record_set", "required": False},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": []},
        risk_checks=("fasta_header_count_nonzero", "id_set_preserved", "no_orphan_sequence_lines"),
        prompt_rules=(
            "Use input_pep_fasta/input_cds_fasta plus input_id_file for sequence extraction.",
            "Never rewrite ID/header cleaning logic; preserve original command semantics and compare FASTA headers.",
        ),
    ),
    TemplateArchetype(
        archetype_id="hmmer_domain",
        label="HMMER search/domain table plus optional R plot",
        stages=("domain",),
        analysis_type="hmmer_search",
        tool_family="hmmer",
        input_roles={"input_fasta": {"role": "target_pep_fasta", "required": True}, "input_hmm": {"role": "hmm_profile", "required": False}},
        output_contract=[
            {"name_pattern": "*.domtblout", "semantic_role": "hmmer_domtblout", "compare_mode": "hmmer_domtblout", "required": True},
            {"name_pattern": "*.tblout", "semantic_role": "hmmer_tblout", "compare_mode": "normalized_text", "required": False},
            {"name_pattern": "*.out", "semantic_role": "hmmer_stdout", "compare_mode": "normalized_text", "required": False},
            {"name_pattern": "*.png", "semantic_role": "plot_png", "compare_mode": "file_presence", "required": False},
            {"name_pattern": "*.pdf", "semantic_role": "plot_pdf", "compare_mode": "file_presence", "required": False},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["absolute paths", "runtime log lines"]},
        risk_checks=("domtblout_min_22_columns", "domain_coordinates_valid", "evalue_threshold_preserved"),
        prompt_rules=(
            "For HMMER, preserve hmmscan/hmmsearch/hmmbuild command order exactly.",
            "Compare domtblout/tblout/out separately; a non-empty plot is not enough evidence.",
        ),
    ),
    TemplateArchetype(
        archetype_id="motif_meme",
        label="MEME motif directory plus XML/TXT and motif-position tables",
        stages=("motif",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_fasta": {"role": "target_pep_fasta", "required": True}},
        output_contract=[
            {"name_pattern": "meme.xml", "semantic_role": "meme_xml", "compare_mode": "xml", "required": True},
            {"name_pattern": "meme.txt", "semantic_role": "meme_txt", "compare_mode": "normalized_text", "required": True},
            {"name_pattern": "*.motif_prot.txt", "semantic_role": "motif_positions", "compare_mode": "record_set", "required": False},
            {"name_pattern": "logo*.png", "semantic_role": "motif_logo", "compare_mode": "file_presence", "required": False},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["MEME runtime", "absolute paths"]},
        risk_checks=("meme_xml_parseable", "motif_count_preserved", "motif_coordinate_table_nonempty"),
        prompt_rules=("Treat MEME output directory as a directory artifact; do not flatten it into one text file.",),
    ),
    TemplateArchetype(
        archetype_id="phylogeny_tree",
        label="MSA plus IQ-TREE/Newick tree outputs",
        stages=("phylogeny", "msa_region_plot"),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_fasta": {"role": "target_pep_fasta", "required": True}},
        output_contract=[
            {"name_pattern": "*.msa.fa", "semantic_role": "multiple_sequence_alignment", "compare_mode": "fasta", "required": True},
            {"name_pattern": "*.nwk", "semantic_role": "newick_tree", "compare_mode": "newick", "required": True},
            {"name_pattern": "*.treefile", "semantic_role": "iqtree_treefile", "compare_mode": "newick", "required": False},
            {"name_pattern": "*.iqtree", "semantic_role": "iqtree_report", "compare_mode": "normalized_text", "required": False},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["thread count", "runtime", "stochastic support ties"]},
        risk_checks=("alignment_sequence_count_preserved", "tree_tip_set_preserved", "newick_parseable"),
        prompt_rules=("For tree pipelines, compare tip sets and alignment records; byte-level equality may be too strict for logs.",),
    ),
    TemplateArchetype(
        archetype_id="reference_classification",
        label="Target/reference subfamily assignment from tree",
        stages=("reference_classification", "subfamily_confirm"),
        analysis_type="table_filter",
        tool_family="python",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "*.assignment.tsv", "semantic_role": "assignment_table", "compare_mode": "table", "required": True},
            {"name_pattern": "*.confidence.tsv", "semantic_role": "confidence_table", "compare_mode": "table", "required": False},
            {"name_pattern": "*.subfamily.tsv", "semantic_role": "subfamily_table", "compare_mode": "table", "required": True},
            {"name_pattern": "*.nwk", "semantic_role": "classification_tree", "compare_mode": "newick", "required": False},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["plot rendering"]},
        risk_checks=("target_reference_roles_preserved", "assignment_ids_preserved", "subfamily_labels_nonempty"),
        prompt_rules=("Do not infer target/reference direction from file size; use explicit species and role semantics.",),
    ),
    TemplateArchetype(
        archetype_id="synteny_collinearity",
        label="BLAST/DIAMOND plus GFF/BED collinearity workflow",
        stages=("synteny", "interspecies_synteny", "ortholog"),
        analysis_type="blast_search",
        tool_family="blast",
        input_roles={
            "input_query_fasta": {"role": "target_pep_fasta", "required": True},
            "input_reference_fasta": {"role": "reference_pep_fasta", "required": True},
            "input_file": {"role": "annotation_gff", "required": False},
        },
        output_contract=[
            {"name_pattern": "*.blast", "semantic_role": "blast_outfmt6", "compare_mode": "blast_outfmt6", "required": True},
            {"name_pattern": "*.gff", "semantic_role": "mcscan_gff", "compare_mode": "gff", "required": False},
            {"name_pattern": "*.bed", "semantic_role": "bed_coordinates", "compare_mode": "bed", "required": False},
            {"name_pattern": "*.collinearity", "semantic_role": "collinearity", "compare_mode": "normalized_text", "required": False},
            {"name_pattern": "*.anchors", "semantic_role": "jcvi_anchors", "compare_mode": "record_set", "required": False},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["BLAST database side files"]},
        risk_checks=("query_reference_not_swapped", "blast_column_count_12", "gff_coordinates_valid"),
        prompt_rules=("For BLAST/synteny, input_query_fasta and input_reference_fasta are semantic roles; never choose direction by file size.",),
    ),
    TemplateArchetype(
        archetype_id="gene_structure_gff",
        label="GFF gene structure filtering/table plus optional plot",
        stages=("structure",),
        analysis_type="gff_processing",
        tool_family="python",
        input_roles={
            "input_file": {"role": "annotation_gff", "required": True},
            "input_id_file": {"role": "target_id_list", "required": False},
        },
        output_contract=[
            {"name_pattern": "*.structure.filtered.gff3", "semantic_role": "gene_structure_gff", "compare_mode": "gff", "required": True},
            {"name_pattern": "*.structure.tsv", "semantic_role": "gene_structure_table", "compare_mode": "table", "required": True},
            {"name_pattern": "*.png", "semantic_role": "plot_png", "compare_mode": "file_presence", "required": False},
            {"name_pattern": "*.pdf", "semantic_role": "plot_pdf", "compare_mode": "file_presence", "required": False},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["plot rendering", "absolute paths"]},
        risk_checks=("gff_coordinates_valid", "gene_id_set_preserved", "structure_table_nonempty"),
        prompt_rules=(
            "For gene structure, filtered GFF3 and structure table are required evidence; plot files alone are insufficient.",
            "Preserve feature coordinates and gene IDs when filtering or reformatting GFF/GFF3 records.",
        ),
    ),
    TemplateArchetype(
        archetype_id="promoter_cis_element",
        label="Promoter/cis-element table plus BED/count summaries",
        stages=("promoter",),
        analysis_type="table_filter",
        tool_family="python",
        input_roles={
            "input_file": {"role": "generic_input_file", "required": True},
        },
        output_contract=[
            {"name_pattern": "*cis_elements.tsv", "semantic_role": "cis_element_table", "compare_mode": "table", "required": True},
            {"name_pattern": "*element_counts.tsv", "semantic_role": "element_counts", "compare_mode": "table", "required": True},
            {"name_pattern": "*promoter_summary.tsv", "semantic_role": "promoter_summary", "compare_mode": "table", "required": True},
            {"name_pattern": "*.bed", "semantic_role": "promoter_bed", "compare_mode": "bed", "required": False},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["absolute paths", "plot rendering"]},
        risk_checks=("cis_element_table_nonempty", "element_counts_consistent", "promoter_bed_coordinates_valid"),
        prompt_rules=(
            "For promoter/cis-element work, preserve gene/element rows and count summaries; report or plot output alone is not sufficient.",
            "Treat PlantCARE-style tables and BED coordinates as tabular evidence and keep filtering thresholds explicit.",
        ),
    ),
    TemplateArchetype(
        archetype_id="rnaseq_quant_matrix",
        label="RNA-seq quantification matrix post-processing subset",
        stages=("rnaseq_quant",),
        analysis_type="table_filter",
        tool_family="python",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "output.count_matrix.tsv", "semantic_role": "rnaseq_count_matrix", "compare_mode": "table", "required": True},
            {"name_pattern": "output.tpm_matrix.tsv", "semantic_role": "rnaseq_tpm_matrix", "compare_mode": "table", "required": True},
            {"name_pattern": "quant_summary.tsv", "semantic_role": "rnaseq_quant_summary", "compare_mode": "table", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["subset row limit declared in test data"]},
        risk_checks=("quant_matrix_rows_nonempty", "sample_columns_preserved", "count_tpm_gene_ids_aligned"),
        prompt_rules=(
            "Only convert lightweight RNA-seq quantification table post-processing; do not wrap salmon indexing, alignment, or full quantification workflows as one extension.",
            "Declare row-limited test data explicitly and compare count/TPM matrices separately.",
        ),
    ),
    TemplateArchetype(
        archetype_id="rnaseq_de_table",
        label="RNA-seq differential-expression table post-processing subset",
        stages=("rnaseq_de",),
        analysis_type="table_filter",
        tool_family="python",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "output.de_results.tsv", "semantic_role": "rnaseq_de_results", "compare_mode": "table", "required": True},
            {"name_pattern": "top_de_preview.tsv", "semantic_role": "rnaseq_de_top_preview", "compare_mode": "table", "required": True},
            {"name_pattern": "de_summary.tsv", "semantic_role": "rnaseq_de_summary", "compare_mode": "table", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["subset row limit declared in test data"]},
        risk_checks=("de_table_rows_nonempty", "gene_id_column_preserved", "statistical_columns_preserved"),
        prompt_rules=(
            "Only convert lightweight RNA-seq DE result-table post-processing; do not wrap DESeq2 modeling or full enrichment workflows as one extension.",
            "Preserve gene IDs and statistical columns when filtering or previewing DE tables.",
        ),
    ),
    TemplateArchetype(
        archetype_id="kaks_pairwise",
        label="Pairwise CDS/PEP/codon alignment plus yn00 result directories",
        stages=("kaks",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "*.kaks_results.csv", "semantic_role": "kaks_results", "compare_mode": "table", "required": True},
            {"name_pattern": "yn00.out", "semantic_role": "yn00_output", "compare_mode": "normalized_text", "required": False},
            {"name_pattern": "codon_aln.fa", "semantic_role": "codon_alignment", "compare_mode": "fasta", "required": False},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["floating point formatting"]},
        risk_checks=("pair_count_preserved", "codon_alignment_triplet_length", "kaks_numeric_columns_valid"),
        prompt_rules=("Nested pair directories are part of the result; do not only compare the summary CSV.",),
    ),
    TemplateArchetype(
        archetype_id="pep_properties",
        label="Protein properties/subcellular/TMHMM tables plus R plots",
        stages=("pep",),
        analysis_type="enrichment_or_stats",
        tool_family="python",
        input_roles={"input_fasta": {"role": "target_pep_fasta", "required": True}},
        output_contract=[
            {"name_pattern": "*properties.tsv", "semantic_role": "protein_properties", "compare_mode": "table", "required": True},
            {"name_pattern": "*subcellular*.tsv", "semantic_role": "subcellular_table", "compare_mode": "table", "required": False},
            {"name_pattern": "*.3line", "semantic_role": "tmhmm_topology", "compare_mode": "normalized_text", "required": False},
            {"name_pattern": "*.png", "semantic_role": "plot_png", "compare_mode": "file_presence", "required": False},
            {"name_pattern": "*.pdf", "semantic_role": "plot_pdf", "compare_mode": "file_presence", "required": False},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["web-service timestamp", "plot rendering"]},
        risk_checks=("protein_count_preserved", "numeric_property_columns_valid", "external_service_outputs_recorded"),
        prompt_rules=("For external/web-service dependent tools, prefer existing result files as raw behavior evidence when rerun is not reproducible.",),
    ),
    TemplateArchetype(
        archetype_id="gwas_trait_vcf_qc",
        label="GWAS trait/VCF sample matching and QC metrics",
        stages=("gwas_trait_vcf_qc",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "gwas_metrics.json", "semantic_role": "gwas_metrics", "compare_mode": "json", "required": True},
            {"name_pattern": "sample_match_report.tsv", "semantic_role": "gwas_sample_match_report", "compare_mode": "table", "required": True},
            {"name_pattern": "trait.clean.tsv", "semantic_role": "gwas_clean_trait_table", "compare_mode": "table", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["absolute paths", "tool runtime logs"]},
        risk_checks=("vcf_sample_count_nonzero", "trait_vcf_sample_match_rate_recorded", "missing_trait_samples_reported"),
        prompt_rules=(
            "For GWAS trait/VCF QC, preserve sample IDs and trait values exactly.",
            "Do not replace sample matching logic with inferred or reordered identifiers.",
        ),
    ),
    TemplateArchetype(
        archetype_id="gwas_vcf_filter_index",
        label="GWAS VCF filtering, bgzip compression, and tabix indexing",
        stages=("gwas_vcf_filter",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "filtered.vcf.gz", "semantic_role": "gwas_filtered_vcf_gz", "compare_mode": "file_presence", "required": True},
            {"name_pattern": "filtered.vcf.gz.tbi", "semantic_role": "gwas_filtered_vcf_index", "compare_mode": "file_presence", "required": True},
            {"name_pattern": "gwas_metrics.json", "semantic_role": "gwas_metrics", "compare_mode": "json", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["compressed VCF block layout", "tabix index binary layout", "absolute paths"]},
        risk_checks=("filtered_vcf_exists", "tabix_index_exists", "variant_retention_rate_recorded"),
        prompt_rules=(
            "For VCF filtering, preserve bcftools filter semantics and write bgzip/tabix outputs into the declared output directory.",
            "Do not treat compressed VCF byte equality as the only evidence; metrics and index presence are required.",
        ),
    ),
    TemplateArchetype(
        archetype_id="gwas_trait_prepare_match",
        label="GWAS trait table preparation aligned to VCF samples",
        stages=("gwas_trait_prepare",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "gwas_metrics.json", "semantic_role": "gwas_metrics", "compare_mode": "json", "required": True},
            {"name_pattern": "sample_match_report.tsv", "semantic_role": "gwas_sample_match_report", "compare_mode": "table", "required": True},
            {"name_pattern": "trait.prepared.tsv", "semantic_role": "gwas_prepared_trait_table", "compare_mode": "table", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["absolute paths", "tool runtime logs"]},
        risk_checks=("matched_sample_count_recorded", "trait_table_nonempty", "duplicate_trait_ids_reported"),
        prompt_rules=(
            "For GWAS trait preparation, preserve phenotype column selection and sample ordering from the VCF.",
            "Report unmatched VCF and trait samples instead of silently dropping them.",
        ),
    ),
    TemplateArchetype(
        archetype_id="gwas_plink_pca",
        label="GWAS PLINK PCA from VCF subset",
        stages=("gwas_pca",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "plink_pca.eigenvec", "semantic_role": "gwas_plink_pca_eigenvec", "compare_mode": "table", "required": True},
            {"name_pattern": "plink_pca.eigenval", "semantic_role": "gwas_plink_pca_eigenval", "compare_mode": "table", "required": True},
            {"name_pattern": "gwas_metrics.json", "semantic_role": "gwas_metrics", "compare_mode": "json", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["absolute paths", "PLINK log timestamps"]},
        risk_checks=("plink_sample_count_nonzero", "pca_component_count_recorded", "eigenvec_rows_match_samples"),
        prompt_rules=(
            "For PLINK PCA, preserve --double-id and --allow-extra-chr behavior when present.",
            "Keep eigenvec/eigenval outputs as primary evidence; PLINK log files alone are insufficient.",
        ),
    ),
    TemplateArchetype(
        archetype_id="gwas_trait_blup_format",
        label="GWAS BLUP-compatible trait formatting",
        stages=("gwas_trait_blup_format",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "heading_days_BLUP_value.tsv", "semantic_role": "gwas_blup_trait_table", "compare_mode": "table", "required": True},
            {"name_pattern": "heading_days_BLUP_value.tassel.tsv", "semantic_role": "gwas_tassel_trait_table", "compare_mode": "table", "required": True},
            {"name_pattern": "sample_match_report.tsv", "semantic_role": "gwas_sample_match_report", "compare_mode": "table", "required": True},
            {"name_pattern": "gwas_metrics.json", "semantic_role": "gwas_metrics", "compare_mode": "json", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["absolute paths", "tool runtime logs"]},
        risk_checks=("blup_trait_table_nonempty", "tassel_trait_table_nonempty", "sample_match_rate_recorded"),
        prompt_rules=(
            "For BLUP-compatible trait formatting, preserve sample IDs, phenotype column choice, duplicate-sample policy, and VCF sample ordering.",
            "Do not infer or invent phenotype values; summarize duplicate IDs with the declared mean policy.",
        ),
    ),
    TemplateArchetype(
        archetype_id="gwas_plink_impute",
        label="GWAS PLINK missing-genotype imputation package",
        stages=("gwas_impute",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "all.impute.vcf.gz", "semantic_role": "gwas_imputed_vcf_gz", "compare_mode": "file_presence", "required": True},
            {"name_pattern": "all.impute.vcf.gz.tbi", "semantic_role": "gwas_imputed_vcf_index", "compare_mode": "file_presence", "required": True},
            {"name_pattern": "imputed_bed.bed", "semantic_role": "gwas_imputed_plink_bed", "compare_mode": "file_presence", "required": True},
            {"name_pattern": "imputed_bed.bim", "semantic_role": "gwas_imputed_plink_bim", "compare_mode": "file_presence", "required": True},
            {"name_pattern": "imputed_bed.fam", "semantic_role": "gwas_imputed_plink_fam", "compare_mode": "file_presence", "required": True},
            {"name_pattern": "gwas_metrics.json", "semantic_role": "gwas_metrics", "compare_mode": "json", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["compressed VCF block layout", "PLINK binary layout", "absolute paths"]},
        risk_checks=("imputed_vcf_index_exists", "plink_bed_triplet_exists", "missing_rate_before_after_recorded"),
        prompt_rules=(
            "For PLINK imputation, preserve VCF-to-BED, --fill-missing-a2, VCF recode, bgzip, and tabix command semantics.",
            "Compare binary PLINK/VCF artifacts by presence plus metrics; do not require byte equality for compressed or binary files.",
        ),
    ),
    TemplateArchetype(
        archetype_id="gwas_tassel_compatible",
        label="GWAS TASSEL-compatible HapMap/trait/association package",
        stages=("gwas_tassel_assoc",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "hapmap.hmp.txt", "semantic_role": "gwas_tassel_hapmap", "compare_mode": "table", "required": True},
            {"name_pattern": "tassel_trait.tsv", "semantic_role": "gwas_tassel_trait_table", "compare_mode": "table", "required": True},
            {"name_pattern": "glm_pvalue.txt", "semantic_role": "gwas_tassel_glm_pvalue_table", "compare_mode": "table", "required": True},
            {"name_pattern": "tassel_probe.json", "semantic_role": "gwas_tassel_probe_record", "compare_mode": "json", "required": True},
            {"name_pattern": "gwas_metrics.json", "semantic_role": "gwas_metrics", "compare_mode": "json", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["absolute paths", "TASSEL runtime logs", "floating point formatting"]},
        risk_checks=("hapmap_rows_nonzero", "trait_rows_nonzero", "tassel_probe_result_recorded"),
        prompt_rules=(
            "For TASSEL-compatible GWAS, preserve HapMap sample columns, trait sample IDs, p-value table rows, and the real TASSEL probe record.",
            "Do not claim a full TASSEL GLM/MLM/CMLM run when the validation fixture only records compatibility/probe evidence.",
        ),
    ),
    TemplateArchetype(
        archetype_id="gwas_gemma_assoc",
        label="GWAS GEMMA association on bounded VCF/trait subset",
        stages=("gwas_assoc",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "lm.assoc.txt", "semantic_role": "gwas_gemma_assoc_table", "compare_mode": "table", "required": True},
            {"name_pattern": "gwas_metrics.json", "semantic_role": "gwas_metrics", "compare_mode": "json", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["absolute paths", "GEMMA runtime logs", "floating point formatting"]},
        risk_checks=("association_rows_nonzero", "phenotype_sample_match_recorded", "top_marker_and_min_p_recorded"),
        prompt_rules=(
            "For GEMMA association, preserve PLINK conversion, phenotype alignment, and GEMMA model options.",
            "Do not report a plot or log as the primary association result; the association table and metrics are required.",
        ),
    ),
    TemplateArchetype(
        archetype_id="vcf_fastp_clean",
        label="Resequencing FASTQ cleaning/QC",
        stages=("vcf_fastp_clean",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "12.clean.R1.fastq", "semantic_role": "vcf_clean_fastq_r1", "compare_mode": "file_presence", "required": True},
            {"name_pattern": "12.clean.R2.fastq", "semantic_role": "vcf_clean_fastq_r2", "compare_mode": "file_presence", "required": True},
            {"name_pattern": "vcf_metrics.json", "semantic_role": "vcf_metrics", "compare_mode": "json", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["runtime logs", "absolute paths"]},
        risk_checks=("paired_fastq_record_counts_match", "clean_fastq_outputs_present", "qc_metrics_recorded"),
        prompt_rules=("For FASTQ cleaning, preserve paired-end R1/R2 pairing and report record counts/QC metrics.",),
    ),
    TemplateArchetype(
        archetype_id="vcf_reference_index",
        label="Reference FASTA index manifest",
        stages=("vcf_reference_index",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "reference_index_metrics.json", "semantic_role": "vcf_reference_index_metrics", "compare_mode": "json", "required": True},
            {"name_pattern": "reference.fai.preview", "semantic_role": "vcf_reference_fai_preview", "compare_mode": "normalized_text", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["absolute paths"]},
        risk_checks=("reference_contig_count_recorded", "fai_preview_nonempty", "index_files_declared"),
        prompt_rules=("For reference indexing, record BWA/SAMtools/GATK index presence separately; do not silently rebuild a different reference.",),
    ),
    TemplateArchetype(
        archetype_id="vcf_bwa_align_sort",
        label="BWA alignment and coordinate-sorted BAM evidence",
        stages=("vcf_bwa_align_sort",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "bwa_align_metrics.json", "semantic_role": "vcf_bwa_align_metrics", "compare_mode": "json", "required": True},
            {"name_pattern": "align_flagstat.txt", "semantic_role": "vcf_align_flagstat", "compare_mode": "normalized_text", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["tool runtime logs", "absolute paths"]},
        risk_checks=("mapped_read_metrics_recorded", "sorted_bam_stage_declared", "sample_id_preserved"),
        prompt_rules=("For BWA alignment, preserve read-group/sample identity and sort/index semantics; logs alone are insufficient evidence.",),
    ),
    TemplateArchetype(
        archetype_id="vcf_markduplicates",
        label="GATK MarkDuplicates evidence",
        stages=("vcf_markduplicates",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "markduplicates_metrics.json", "semantic_role": "vcf_markduplicates_metrics", "compare_mode": "json", "required": True},
            {"name_pattern": "markduplicates.metrics.txt", "semantic_role": "vcf_markduplicates_metrics_table", "compare_mode": "normalized_text", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["tool runtime logs", "absolute paths"]},
        risk_checks=("duplicate_metrics_recorded", "marked_bam_stage_declared", "sample_id_preserved"),
        prompt_rules=("For duplicate marking, preserve MarkDuplicates metrics and BAM/index output semantics.",),
    ),
    TemplateArchetype(
        archetype_id="vcf_bam_stats",
        label="BAM flagstat/depth summaries",
        stages=("vcf_bam_stats",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "bam_stats_metrics.json", "semantic_role": "vcf_bam_stats_metrics", "compare_mode": "json", "required": True},
            {"name_pattern": "flagstat_summary.txt", "semantic_role": "vcf_flagstat_summary", "compare_mode": "normalized_text", "required": True},
            {"name_pattern": "depth_summary.txt", "semantic_role": "vcf_depth_summary", "compare_mode": "normalized_text", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["absolute paths"]},
        risk_checks=("flagstat_summary_nonempty", "depth_summary_nonempty", "coverage_metrics_recorded"),
        prompt_rules=("For BAM statistics, keep flagstat and depth evidence as separate required outputs.",),
    ),
    TemplateArchetype(
        archetype_id="vcf_haplotypecaller_gvcf",
        label="GATK HaplotypeCaller GVCF evidence",
        stages=("vcf_haplotypecaller_gvcf",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "12.g.vcf.gz", "semantic_role": "vcf_sample_gvcf", "compare_mode": "file_presence", "required": True},
            {"name_pattern": "12.g.vcf.gz.tbi", "semantic_role": "vcf_sample_gvcf_index", "compare_mode": "file_presence", "required": True},
            {"name_pattern": "haplotypecaller_metrics.json", "semantic_role": "vcf_haplotypecaller_metrics", "compare_mode": "json", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["compressed VCF block layout", "absolute paths"]},
        risk_checks=("gvcf_index_present", "sample_id_recorded", "gvcf_record_count_recorded"),
        prompt_rules=("For HaplotypeCaller, keep GVCF and tabix index as primary evidence and record sample/count metrics.",),
    ),
    TemplateArchetype(
        archetype_id="vcf_combine_gvcf",
        label="GATK CombineGVCFs cohort GVCF",
        stages=("vcf_combine_gvcf",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "cohort.g.vcf.gz", "semantic_role": "vcf_cohort_gvcf", "compare_mode": "file_presence", "required": True},
            {"name_pattern": "combine_gvcf_metrics.json", "semantic_role": "vcf_combine_gvcf_metrics", "compare_mode": "json", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["compressed VCF block layout", "absolute paths"]},
        risk_checks=("gvcf_input_count_recorded", "cohort_gvcf_present", "sample_count_recorded"),
        prompt_rules=("For CombineGVCFs, explicitly collect .g.vcf.gz/.g.vcf/.gvcf.gz/.gvcf or require a manifest; do not use unsafe interval fallback.",),
    ),
    TemplateArchetype(
        archetype_id="vcf_genotype_gvcf",
        label="GATK GenotypeGVCFs raw cohort VCF",
        stages=("vcf_genotype_gvcf",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "cohort.raw.vcf.gz", "semantic_role": "vcf_raw_cohort_vcf", "compare_mode": "file_presence", "required": True},
            {"name_pattern": "genotype_gvcf_metrics.json", "semantic_role": "vcf_genotype_gvcf_metrics", "compare_mode": "json", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["compressed VCF block layout", "absolute paths"]},
        risk_checks=("raw_vcf_present", "variant_count_recorded", "sample_count_recorded"),
        prompt_rules=("For GenotypeGVCFs, remove silent small-interval fallback and declare any test interval explicitly.",),
    ),
    TemplateArchetype(
        archetype_id="vcf_select_variants",
        label="GATK SelectVariants SNP/INDEL split",
        stages=("vcf_select_variants",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "cohort.snp.vcf.gz", "semantic_role": "vcf_snp_vcf", "compare_mode": "file_presence", "required": True},
            {"name_pattern": "cohort.indel.vcf.gz", "semantic_role": "vcf_indel_vcf", "compare_mode": "file_presence", "required": True},
            {"name_pattern": "select_variants_metrics.json", "semantic_role": "vcf_select_variants_metrics", "compare_mode": "json", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["compressed VCF block layout", "absolute paths"]},
        risk_checks=("snp_vcf_present", "indel_vcf_present", "variant_type_counts_recorded"),
        prompt_rules=("For SelectVariants, keep SNP and INDEL outputs separate and record type-specific counts.",),
    ),
    TemplateArchetype(
        archetype_id="vcf_hard_filter_clean",
        label="GATK hard-filtered clean SNP VCF",
        stages=("vcf_hard_filter_clean",),
        analysis_type="generic_cli",
        tool_family="generic",
        input_roles={"input_file": {"role": "generic_input_file", "required": True}},
        output_contract=[
            {"name_pattern": "clean.vcf.gz", "semantic_role": "vcf_clean_snp_vcf", "compare_mode": "file_presence", "required": True},
            {"name_pattern": "clean.vcf.gz.tbi", "semantic_role": "vcf_clean_snp_vcf_index", "compare_mode": "file_presence", "required": True},
            {"name_pattern": "hard_filter_metrics.json", "semantic_role": "vcf_hard_filter_metrics", "compare_mode": "json", "required": True},
        ],
        comparator_plan={"mode": "pipeline_golden_diff", "default_compare": "structured", "allowed_differences": ["compressed VCF block layout", "absolute paths"]},
        risk_checks=("clean_vcf_present", "pass_variant_count_recorded", "filter_thresholds_recorded"),
        prompt_rules=("For hard filtering, keep SNP/INDEL filter thresholds explicit and expose the clean PASS SNP VCF for downstream GWAS.",),
    ),
)

ARCHETYPE_BY_ID = {item.archetype_id: item for item in ARCHETYPES}


def conversion_template_guidance() -> dict[str, Any]:
    """Return compact prompt guidance shared by conversion and repair prompts."""
    return {
        item.archetype_id: {
            "label": item.label,
            "analysis_type": item.analysis_type,
            "tool_family": item.tool_family,
            "input_roles": item.input_roles,
            "output_contract": item.output_contract,
            "risk_checks": list(item.risk_checks),
            "rules": list(item.prompt_rules),
        }
        for item in ARCHETYPES
    }


def discover_template_cases(job_workspace: Path, *, project_root: Path | None = None, max_per_archetype: int = 1) -> list[TemplateCase]:
    """Discover representative template cases from a completed bbav5 job workspace."""
    job_workspace = Path(job_workspace).absolute()
    project_root = Path(project_root).absolute() if project_root else _infer_project_root(job_workspace)
    final_dir = job_workspace / "final"
    manifests = sorted(final_dir.glob("*manifest.json"))
    selected: dict[str, list[TemplateCase]] = {item.archetype_id: [] for item in ARCHETYPES}
    for manifest_path in manifests:
        data = _read_json(manifest_path)
        if not data or data.get("status") != "success":
            continue
        archetype = classify_manifest(data)
        if not archetype:
            continue
        bucket = selected[archetype.archetype_id]
        if len(bucket) >= max_per_archetype:
            continue
        case = _case_from_manifest(data, manifest_path, job_workspace, project_root, archetype)
        if case.output_files:
            bucket.append(case)
    cases: list[TemplateCase] = []
    for archetype in ARCHETYPES:
        cases.extend(selected[archetype.archetype_id])
    return cases


def classify_manifest(manifest: dict[str, Any]) -> TemplateArchetype | None:
    stage = str(manifest.get("stage") or "")
    artifact_text = json.dumps(manifest.get("artifacts") or {}, ensure_ascii=False).lower()
    cmd_text = " ".join(_command_list(manifest.get("cmd"))).lower()
    for item in ARCHETYPES:
        if stage in item.stages:
            if item.archetype_id == "sequence_extraction":
                if "final_members" in artifact_text or "candidate_members" in artifact_text or "seqtk" in cmd_text:
                    return item
                continue
            return item
    if "hmmscan" in cmd_text or "domtblout" in artifact_text:
        return ARCHETYPE_BY_ID["hmmer_domain"]
    if "meme" in cmd_text or "meme.xml" in artifact_text:
        return ARCHETYPE_BY_ID["motif_meme"]
    if "iqtree" in cmd_text or "mafft" in cmd_text:
        return ARCHETYPE_BY_ID["phylogeny_tree"]
    if "blast" in artifact_text and ("collinearity" in artifact_text or "anchors" in artifact_text):
        return ARCHETYPE_BY_ID["synteny_collinearity"]
    return None


def case_to_candidate_spec(case: TemplateCase) -> dict[str, Any]:
    """Build a reusable conversion-spec skeleton from a discovered template case."""
    archetype = ARCHETYPE_BY_ID[case.archetype_id]
    command_template = _semantic_command_template(archetype)
    return {
        "schema_version": "extension_stage_v2",
        "template_type": "completed_job_template_case",
        "original_command": case.command,
        "command_template": command_template,
        "analysis_type": archetype.analysis_type,
        "tool_family": archetype.tool_family,
        "input_roles": archetype.input_roles,
        "parameters": {},
        "outputs": {"directory": "outputs", "patterns": sorted({item["name_pattern"] for item in archetype.output_contract})},
        "output_contract": archetype.output_contract,
        "comparator_plan": archetype.comparator_plan,
        "requires_user_confirmation": False,
        "notes": f"Template case generated from successful stage {case.stage}: {case.manifest_path}",
        "template_case": asdict(case),
    }


def build_template_case_bundle(job_workspace: Path, *, project_root: Path | None = None, max_per_archetype: int = 1) -> dict[str, Any]:
    cases = discover_template_cases(job_workspace, project_root=project_root, max_per_archetype=max_per_archetype)
    return {
        "schema_version": "extension_template_case_bundle_v1",
        "job_workspace": str(Path(job_workspace).absolute()),
        "case_count": len(cases),
        "archetypes": [asdict(item) for item in ARCHETYPES],
        "cases": [asdict(case) for case in cases],
        "candidate_specs": [case_to_candidate_spec(case) for case in cases],
        "validation": validate_template_case_bundle(cases),
    }


def validate_template_case_bundle(cases: list[TemplateCase]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    seen = {case.archetype_id for case in cases}
    recommended = {"sequence_extraction", "hmmer_domain", "motif_meme", "phylogeny_tree", "reference_classification", "gene_structure_gff", "promoter_cis_element"}
    missing_recommended = sorted(recommended - seen)
    if missing_recommended:
        warnings.append("missing recommended archetype(s): " + ", ".join(missing_recommended))
    for case in cases:
        if case.status != "success":
            errors.append(f"{case.archetype_id}: source manifest is not success")
        if not case.command:
            errors.append(f"{case.archetype_id}: missing command evidence")
        if not case.output_files:
            errors.append(f"{case.archetype_id}: missing output artifact evidence")
        if not case.output_contract:
            errors.append(f"{case.archetype_id}: missing output contract")
    return {"status": "passed" if not errors else "failed", "errors": errors, "warnings": warnings, "covered_archetypes": sorted(seen)}


def write_template_case_bundle(bundle: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "extension_template_cases.json"
    md_path = output_dir / "extension_template_cases.md"
    json_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(_bundle_markdown(bundle), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def _case_from_manifest(data: dict[str, Any], manifest_path: Path, job_workspace: Path, project_root: Path, archetype: TemplateArchetype) -> TemplateCase:
    artifacts = data.get("artifacts") or {}
    output_files: list[dict[str, Any]] = []
    artifact_types: set[str] = set()
    for name, raw in _artifact_items(artifacts):
        if not isinstance(raw, dict):
            continue
        rel = str(raw.get("path") or raw.get("name") or "")
        full = str(raw.get("full_path") or "")
        resolved = _resolve_artifact_path(job_workspace, rel, full)
        item = {
            "artifact": raw.get("artifact") or name,
            "path": rel,
            "full_path": str(resolved) if resolved else full,
            "type": raw.get("type") or _infer_file_type(rel),
            "size": int(raw.get("size") or (resolved.stat().st_size if resolved and resolved.is_file() else 0)),
            "exists": bool(resolved and resolved.exists()),
        }
        artifact_types.add(str(item["type"] or ""))
        output_files.append(item)
    cmd = _manifest_command(data)
    return TemplateCase(
        archetype_id=archetype.archetype_id,
        stage=str(data.get("stage") or ""),
        manifest_path=manifest_path.relative_to(job_workspace).as_posix(),
        prefix=str(data.get("prefix") or data.get("output_prefix") or ""),
        scope_id=str(data.get("scope_id") or data.get("scope") or _scope_from_manifest_name(manifest_path.name)),
        status=str(data.get("status") or ""),
        command=cmd or _log_command(job_workspace, str(data.get("stage") or ""), str(data.get("prefix") or data.get("output_prefix") or "")),
        code_files=[str(project_root / rel) for rel in PROJECT_RELATIVE_CODE.get(archetype.archetype_id, ()) if (project_root / rel).exists()],
        input_files=_input_files(data, cmd),
        output_files=output_files,
        artifact_types=sorted(x for x in artifact_types if x),
        risk_checks=list(archetype.risk_checks),
        output_contract=[dict(item) for item in archetype.output_contract],
        comparator_plan=dict(archetype.comparator_plan),
    )


def _log_command(job_workspace: Path, stage: str, prefix: str) -> str:
    logs_dir = job_workspace / "logs"
    if not logs_dir.exists() or not stage:
        return ""
    candidates = sorted(logs_dir.glob(f"*_{stage}.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    prefix_matches = []
    for path in candidates:
        text = path.read_text(encoding="utf-8", errors="ignore")[:20000]
        if prefix and prefix in text:
            prefix_matches.append(path)
    for path in prefix_matches or candidates:
        command = _first_log_command(path)
        if command:
            return command
    return ""


def _first_log_command(path: Path) -> str:
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if line.startswith("CMD:"):
            return line.split("CMD:", 1)[1].strip()
        if line.startswith("[CMD]"):
            return line.split("[CMD]", 1)[1].strip()
    return ""


def _manifest_command(data: dict[str, Any]) -> str:
    cmd = data.get("cmd") or data.get("command") or ""
    parts = _command_list(cmd)
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return " ".join(shlex.quote(str(part)) for part in parts)


def _command_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _artifact_items(artifacts: Any) -> list[tuple[str, Any]]:
    if isinstance(artifacts, dict):
        return list(artifacts.items())
    if isinstance(artifacts, list):
        return [(str(index), item) for index, item in enumerate(artifacts)]
    return []


def _input_files(data: dict[str, Any], command: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw_inputs = data.get("inputs") or []
    if isinstance(raw_inputs, list):
        for item in raw_inputs[:30]:
            if isinstance(item, dict):
                rows.append({
                    "path": item.get("path") or "",
                    "full_path": item.get("full_path") or "",
                    "size": item.get("size") or 0,
                    "sha256": item.get("sha256") or "",
                    "source": "manifest.inputs",
                })
    for token in shlex.split(command) if command else []:
        if "/" not in token:
            continue
        path = Path(token)
        if path.exists() and path.is_file():
            rows.append({"path": path.name, "full_path": str(path), "size": path.stat().st_size, "sha256": "", "source": "command"})
    dedup: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("full_path") or row.get("path") or "")
        if key and key not in dedup:
            dedup[key] = row
    return list(dedup.values())[:50]


def _semantic_command_template(archetype: TemplateArchetype) -> str:
    parts = ["bash {code_dir}/run_extension.sh", "--input-dir {input_dir}"]
    for placeholder in sorted(archetype.input_roles):
        if placeholder.startswith("input_") and placeholder != "input_dir":
            parts.append(f"--{placeholder.replace('_', '-')} {{{placeholder}}}")
    parts.append("--output-dir {output_dir}")
    return " ".join(parts)


def _resolve_artifact_path(job_workspace: Path, rel: str, full: str) -> Path | None:
    candidates = []
    if full:
        candidates.append(Path(full))
    if rel:
        candidates.append(job_workspace / rel)
    for path in candidates:
        if path.exists():
            return path
    return candidates[0] if candidates else None


def _infer_project_root(job_workspace: Path) -> Path:
    marker = Path("workspace")
    parts = job_workspace.parts
    if marker.name in parts:
        index = parts.index(marker.name)
        return Path(*parts[:index])
    return job_workspace.parent.parent


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _infer_file_type(path: str) -> str:
    suffix = Path(path).suffix.lower().lstrip(".")
    if suffix in {"fa", "fasta", "faa", "fna"}:
        return "fasta"
    if suffix in {"tsv", "csv"}:
        return "table"
    if suffix in {"png", "pdf", "svg"}:
        return "image"
    return suffix or "file"


def _scope_from_manifest_name(name: str) -> str:
    return name.removesuffix(".manifest.json")


def _bundle_markdown(bundle: dict[str, Any]) -> str:
    lines = ["# Extension Template Cases", "", f"Job workspace: `{bundle.get('job_workspace')}`", "", f"Case count: {bundle.get('case_count')}", ""]
    validation = bundle.get("validation") or {}
    lines.extend([f"Validation: `{validation.get('status')}`", ""])
    if validation.get("warnings"):
        lines.append("Warnings:")
        for warning in validation.get("warnings") or []:
            lines.append(f"- {warning}")
        lines.append("")
    lines.append("| Archetype | Stage | Prefix | Outputs | Risks |")
    lines.append("|---|---|---|---:|---|")
    for case in bundle.get("cases") or []:
        risks = ", ".join(case.get("risk_checks") or [])
        lines.append(f"| `{case.get('archetype_id')}` | `{case.get('stage')}` | `{case.get('prefix')}` | {len(case.get('output_files') or [])} | {risks} |")
    lines.append("")
    lines.append("## Usage")
    lines.append("")
    lines.append("Use these cases as behavior baselines for API conversion and repair templates. API output remains a candidate until deterministic wrapping, original-vs-converted golden comparison, and publish gates pass.")
    lines.append("")
    return "\n".join(lines)
