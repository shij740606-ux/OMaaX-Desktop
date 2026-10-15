# `wgs_maf_missing_filter` stage contract v1

Stage: `wgs_maf_missing_filter`

## Consumes

- From `wgs_impute`: `imputed_snp_vcf` and `imputed_snp_vcf_index`.
- Parameters: `bcftools`, `maf=0.05`, and `max_missing=0.5`.

## Provides

- `final_snp_vcf`, `final_snp_vcf_index`, `maf_missing_report`, and `maf_missing_stats`.

## Canonical filenames

- `{prefix}.wgs_maf_missing_filter.cohort.final.snp.vcf.gz`, `{prefix}.wgs_maf_missing_filter.cohort.final.snp.vcf.gz.tbi`, `{prefix}.wgs_maf_missing_filter.cohort.report.md`, and `{prefix}.wgs_maf_missing_filter.cohort.stats.tsv`.

## Required indices

- The consumed `imputed_snp_vcf_index` TBI and produced `final_snp_vcf_index` TBI are valid.

## Scientific identities

Preserve `wgs_sample_set_id` and `wgs_reference_id`; record `wgs_analysis_id` from consumed hashes, parameter snapshot, bcftools version, and actual method.

## Parameter snapshot

Persist `bcftools`, resolved version, `maf=0.05`, and `max_missing=0.5`.

## Success and failure evidence

Each run works privately under `.attempts/<run_id>` and may atomically publish to `final/` only after postflight; an old success cannot satisfy the current run.

`status=success` requires an indexed final SNP VCF, exact sample preservation, per-record MAF/missing recheck, non-empty stats/report, and at least one usable GWAS variant. Zero variants or any command/index/postflight failure produces `artifacts={}`.
