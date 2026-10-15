# `wgs_variant_filter` stage contract v1

Stage: `wgs_variant_filter`

## Consumes

- From `wgs_variant_select`: `selected_snp_vcf`, `selected_snp_vcf_index`, `selected_indel_vcf`, and `selected_indel_vcf_index`.
- Parameters: `method=gatk|bcftools`, `gatk`, and `bcftools`.

## Provides

- `filtered_snp_vcf`, `filtered_snp_vcf_index`, `pass_snp_vcf`, `pass_snp_vcf_index`, `filtered_indel_vcf`, `filtered_indel_vcf_index`, `pass_indel_vcf`, `pass_indel_vcf_index`, and `variant_filter_report`.

## Canonical filenames

- `{prefix}.wgs_variant_filter.cohort.filtered.snp.vcf.gz`, `{prefix}.wgs_variant_filter.cohort.pass.snp.vcf.gz`, `{prefix}.wgs_variant_filter.cohort.filtered.indel.vcf.gz`, `{prefix}.wgs_variant_filter.cohort.pass.indel.vcf.gz`, with matching `.tbi` files, and `{prefix}.wgs_variant_filter.cohort.report.md`.

## Required indices

- Both selected VCF TBIs and the TBI for every filtered/PASS SNP/INDEL VCF are valid.

## Scientific identities

Preserve `wgs_sample_set_id` and `wgs_reference_id`; `wgs_analysis_id` includes consumed hashes, parameter snapshot, tool versions, and actual `method`, so GATK and bcftools differ.

## Parameter snapshot

Persist default `method=gatk`, tool versions, and GATK thresholds SNP `QD2/QUAL30/SOR3/FS60/MQ40/MQRankSum-12.5/ReadPosRankSum-8` and INDEL `QD2/QUAL30/FS200/ReadPosRankSum-20`, or the documented bcftools expression.

## Success and failure evidence

Each run works privately under `.attempts/<run_id>` and may atomically publish to `final/` only after postflight; an old success cannot satisfy the current run. `status=success` requires recorded full expressions, successful filtered/PASS extraction and indexing, canonical roles/names, and a report that records actual method without claiming equality. Only after GATK failure may user confirmation rerun `method=bcftools`; no automatic fallback is permitted. Any failure produces `artifacts={}`.
