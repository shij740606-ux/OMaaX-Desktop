# TFBS Contract v1

## Stage

- `stage`: `tfbs`
- CLI: `apps/cli/run_tfbs.py`
- Runtime: `test4py311`

## Required Inputs

- GFF3/GFF annotation for one target species.
- Genome FASTA for the same target species.
- Family gene ID list, normally `final/{prefix}.final_members.id`.
- Plant TF motif database in MEME format:
  - PlantTFDB species motif set, selected by exact species or NCBI taxonomy nearest lineage.
  - JASPAR CORE plants MEME file as exact/auto fallback.

## Default Parameters

- `upstream_length`: `2000`
- `motif_db_mode`: `auto`
- `fimo_pvalue`: `1e-4`
- `fimo_qvalue`: `0.05`
- `background_mode`: `none` by default. Set `all_annotated_promoters` only when the user explicitly requests whole-genome/all-annotated promoter background scanning.
- `max_nearest_rank`: `order`
- `top_motifs`: `30`
- `scan_both_strands`: `true`

## Standard Outputs

Under `workspace/<job_id>/final/`:

- `{prefix}.tfbs.sites.tsv`
- `{prefix}.tfbs.motif_summary.tsv`
- `{prefix}.tfbs.gene_motif_matrix.tsv`
- `{prefix}.tfbs.enrichment.tsv`
- `{prefix}.tfbs.motif_source_decision.tsv`
- `{prefix}.tfbs.report.md`
- `{prefix}.tfbs.*.png`
- `{prefix}.tfbs_phylo_heatmap.png` and `.pdf`
- `{prefix}.tfbs_promoter_architecture.png` and `.pdf`
- `{prefix}.tfbs_enrichment_dotplot.png` and `.pdf`
- `{prefix}.tfbs_position_density.png` and `.pdf`
- `{prefix}.tfbs_tf_gene_network.png` and `.pdf`
- `{prefix}.tfbs_motif_source_summary.png` and `.pdf`
- `{prefix}.tfbs_all_motif_count_heatmap.png` and `.pdf`
- `{prefix}.tfbs.manifest.json` or `{prefix}.{scope_id}.tfbs.manifest.json`

Under `workspace/<job_id>/tfbs/analysis/`:

- `{prefix}.family_promoters.fa`
- `{prefix}.background_promoters.fa` when background mode requests a background.
- `selected_motifs*.meme`
- `fimo_family/`
- `fimo_background/` when background scanning is enabled.

## Manifest Requirements

The manifest must include:

- `stage = "tfbs"`
- `status = "success"`
- `prefix`
- `scope_id`, `scope_mode`, `analysis_route`, `member_set_id` when available.
- `stage_context`
- `parameters`
- `methodology.official_sources`
- `methodology.motif_source`
- `artifacts`
- `figures`
- `metrics`

## Method Basis

- Promoter extraction follows the bbav5 promoter stage contract using GFF3 + genome FASTA + family gene IDs.
- Site scanning uses MEME Suite FIMO.
- Motif databases are JASPAR CORE plants and PlantTFDB plant TF binding motif sets.
- Taxonomic nearest-species selection uses NCBI taxdump lineage.
