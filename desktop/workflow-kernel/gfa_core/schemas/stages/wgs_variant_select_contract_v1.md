# `wgs_variant_select` stage contract v1

Stage: `wgs_variant_select`

## Consumes

- From `wgs_joint_genotyping`: `joint_vcf` and `joint_vcf_index`.
- Parameters: `method=gatk|bcftools`, `gatk`, and `bcftools`.

## Provides

- `selected_snp_vcf`, `selected_snp_vcf_index`, `selected_indel_vcf`, `selected_indel_vcf_index`, and `variant_select_report`.

## Canonical filenames

- `{prefix}.wgs_variant_select.cohort.selected.snp.vcf.gz`, `{prefix}.wgs_variant_select.cohort.selected.snp.vcf.gz.tbi`, `{prefix}.wgs_variant_select.cohort.selected.indel.vcf.gz`, `{prefix}.wgs_variant_select.cohort.selected.indel.vcf.gz.tbi`, and `{prefix}.wgs_variant_select.cohort.report.md`.

## Required indices

- The consumed `joint_vcf_index` TBI and both selected VCF TBIs are valid.

## Scientific identities

Preserve `wgs_sample_set_id` and `wgs_reference_id`; `wgs_analysis_id` includes consumed hashes, parameter snapshot, tool versions, and actual `method`, so GATK and bcftools differ.

## Parameter snapshot

Persist default `method=gatk`, `gatk`, `bcftools`, resolved versions, and selected SNP/INDEL type rules.

## Success and failure evidence

Each run works privately under `.attempts/<run_id>` and may atomically publish to `final/` only after postflight; an old success cannot satisfy the current run. `status=success` requires correctly typed indexed SNP and INDEL VCFs with identical canonical roles/names for both methods, plus a report that records the actual method. Only after GATK failure may user confirmation rerun `method=bcftools`; no automatic fallback is permitted. Any selection or index failure produces `artifacts={}`.
