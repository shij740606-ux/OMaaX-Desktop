# `wgs_gvcf_combine` stage contract v1

Stage: `wgs_gvcf_combine`

## Consumes

- From `wgs_haplotype_calling`: `gvcf` and `gvcf_index`; from `wgs_index`: `reference_fasta`, `reference_fai`, and `reference_dict`.
- Parameter: `gatk`.

## Provides

- Cohort `combined_gvcf`, `combined_gvcf_index`, and `combine_log`.

## Canonical filenames

- `{prefix}.wgs_gvcf_combine.cohort.combined.g.vcf.gz`, `{prefix}.wgs_gvcf_combine.cohort.combined.g.vcf.gz.tbi`, and `{prefix}.wgs_gvcf_combine.cohort.combine.log`.

## Required indices

- All consumed `gvcf_index` TBIs and the produced `combined_gvcf_index` TBI are valid.

## Scientific identities

Preserve `wgs_sample_set_id` and `wgs_reference_id`; record `wgs_analysis_id` from every GVCF/index hash, parameter snapshot, GATK version, and actual method.

## Parameter snapshot

Persist `gatk`, its resolved version, and the complete declared input sample IDs.

## Success and failure evidence

Each run works privately under `.attempts/<run_id>` and may atomically publish to `final/` only after postflight; an old success cannot satisfy the current run.

`status=success` requires every declared indexed GVCF to participate and the combined header sample set to match exactly. Missing, duplicate, invalid, or uncombined input produces `artifacts={}`.
