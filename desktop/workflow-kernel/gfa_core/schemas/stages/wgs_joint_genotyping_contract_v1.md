# `wgs_joint_genotyping` stage contract v1

Stage: `wgs_joint_genotyping`

## Consumes

- From `wgs_gvcf_combine`: `combined_gvcf` and `combined_gvcf_index`; from `wgs_index`: `reference_fasta`, `reference_fai`, and `reference_dict`.
- Parameters: `gatk`, `max_alternate_alleles=6`, `calling_threshold=10`, and `include_non_variant_sites=false`.

## Provides

- Cohort `joint_vcf`, `joint_vcf_index`, `joint_summary`, and `joint_log`.

## Canonical filenames

- `{prefix}.wgs_joint_genotyping.cohort.joint.vcf.gz`, `{prefix}.wgs_joint_genotyping.cohort.joint.vcf.gz.tbi`, `{prefix}.wgs_joint_genotyping.cohort.summary.tsv`, and `{prefix}.wgs_joint_genotyping.cohort.joint.log`.

## Required indices

- The consumed `combined_gvcf_index` TBI and produced `joint_vcf_index` TBI are valid.

## Scientific identities

Preserve `wgs_sample_set_id` and `wgs_reference_id`; record `wgs_analysis_id` from consumed hashes, parameter snapshot, GATK version, and actual method.

## Parameter snapshot

Persist `gatk`, resolved version, `max_alternate_alleles=6`, `calling_threshold=10`, and `include_non_variant_sites=false`.

## Success and failure evidence

Each run works privately under `.attempts/<run_id>` and may atomically publish to `final/` only after postflight; an old success cannot satisfy the current run.

`status=success` requires an indexed parseable VCF, exact sample-set conservation, complete summary, and a real zero tool exit code. Any failure produces `artifacts={}`.
