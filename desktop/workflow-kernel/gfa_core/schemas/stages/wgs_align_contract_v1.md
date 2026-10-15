# `wgs_align` stage contract v1

Stage: `wgs_align`

## Consumes

- From `wgs_qc`: `clean_reads_r1` and `clean_reads_r2`; from `wgs_index`: `reference_fasta`, `reference_fai`, `reference_dict`, and `bwa_mem2_index_component`.
- Parameters: `bwa_mem2`, `samtools`, `threads`, `max_jobs`, and `platform=illumina`.

## Provides

- Per-sample `sorted_bam`, `sorted_bam_index`, and `align_log`.

## Canonical filenames

- `{prefix}.wgs_align.{sample_id}.sorted.bam`, `{prefix}.wgs_align.{sample_id}.sorted.bam.bai`, and `{prefix}.wgs_align.{sample_id}.align.log`.

## Required indices

- Every `sorted_bam` has a valid matching `sorted_bam_index` BAI.

## Scientific identities

Preserve `wgs_sample_set_id` and `wgs_reference_id`; record `wgs_analysis_id` from all consumed artifact hashes, parameter snapshot, tool versions, and actual method.

## Parameter snapshot

Persist `bwa_mem2`, `samtools`, `threads`, `max_jobs`, `platform`, and resolved versions.

## Success and failure evidence

Each run works privately under `.attempts/<run_id>` and may atomically publish to `final/` only after postflight; an old success cannot satisfy the current run.

`status=success` requires parseable indexed BAMs, RG `SM` equal to each `sample_id`, and exact input/output sample-set conservation. Any aligner, sort, index, or postflight failure produces `artifacts={}`.
