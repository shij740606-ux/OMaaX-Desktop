# `wgs_haplotype_calling` stage contract v1

Stage: `wgs_haplotype_calling`

## Consumes

- From `wgs_markdup`: `markdup_bam` and `markdup_bam_index`; from successful `wgs_bam_qc`: `bam_qc_summary`; from `wgs_index`: `reference_fasta`, `reference_fai`, and `reference_dict`.
- Parameters: `gatk`, `native_pair_hmm_threads`, and `max_jobs`.

## Provides

- Per-sample `gvcf`, `gvcf_index`, and `haplotype_log`.

## Canonical filenames

- `{prefix}.wgs_haplotype_calling.{sample_id}.g.vcf.gz`, `{prefix}.wgs_haplotype_calling.{sample_id}.g.vcf.gz.tbi`, and `{prefix}.wgs_haplotype_calling.{sample_id}.haplotype.log`.

## Required indices

- Every `gvcf` has a valid matching `gvcf_index` TBI.

## Scientific identities

Preserve `wgs_sample_set_id` and `wgs_reference_id`; record `wgs_analysis_id` from consumed hashes, parameter snapshot, GATK version, and actual method.

## Parameter snapshot

Persist `gatk`, resolved version, `native_pair_hmm_threads`, `max_jobs`, and `ERC=GVCF`.

## Success and failure evidence

Each run works privately under `.attempts/<run_id>` and may atomically publish to `final/` only after postflight; an old success cannot satisfy the current run.

`status=success` requires successful Stage 05 evidence, one indexed parseable GVCF per BAM, matching internal sample IDs, and exact sample-set conservation. Any tool, index, or content failure produces `artifacts={}`.
