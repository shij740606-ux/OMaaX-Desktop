# `wgs_impute` stage contract v1

Stage: `wgs_impute`

## Consumes

- From `wgs_variant_filter`: `pass_snp_vcf` and `pass_snp_vcf_index`.
- Parameters: `java`, `beagle_jar`, `memory=8g`, `window=40`, `overlap=2`, `ne=100000`, `iterations=12`, `phase_states=280`, `imp_states=1600`, and `cluster=0.005`.

## Provides

- `imputed_snp_vcf`, `imputed_snp_vcf_index`, `impute_report`, and `impute_log`.

## Canonical filenames

- `{prefix}.wgs_impute.cohort.imputed.snp.vcf.gz`, `{prefix}.wgs_impute.cohort.imputed.snp.vcf.gz.tbi`, `{prefix}.wgs_impute.cohort.report.md`, and `{prefix}.wgs_impute.cohort.impute.log`.

## Required indices

- The consumed `pass_snp_vcf_index` TBI and produced `imputed_snp_vcf_index` TBI are valid.

## Scientific identities

Preserve `wgs_sample_set_id` and `wgs_reference_id`; record `wgs_analysis_id` from consumed hashes, full Beagle parameter snapshot, Java/Beagle versions, and actual method.

## Parameter snapshot

Persist `java`, `beagle_jar`, their versions, `memory=8g`, `window=40`, `overlap=2`, `ne=100000`, `iterations=12`, `phase_states=280`, `imp_states=1600`, and `cluster=0.005`.

## Success and failure evidence

Each run works privately under `.attempts/<run_id>` and may atomically publish to `final/` only after postflight; an old success cannot satisfy the current run.

`status=success` requires a real zero Beagle exit code, indexed parseable imputed SNP VCF, unchanged sample set, and the declared site-conservation policy. Any Java, Beagle, index, or postflight failure produces `artifacts={}`.
