# `wgs_bam_qc` stage contract v1

Stage: `wgs_bam_qc`

## Consumes

- From `wgs_markdup`: `markdup_bam` and `markdup_bam_index`; from `wgs_index`: `reference_fai`.
- Parameters: `samtools`, `coverage_threshold=1`, `threads`, and `max_jobs`.

## Provides

- Per-sample `bam_flagstat` and `bam_depth_summary`, plus cohort `bam_qc_summary` and `bam_qc_report`.

## Canonical filenames

- `{prefix}.wgs_bam_qc.{sample_id}.flagstat.txt`, `{prefix}.wgs_bam_qc.{sample_id}.depth.tsv`, `{prefix}.wgs_bam_qc.cohort.summary.tsv`, and `{prefix}.wgs_bam_qc.cohort.report.md`.

## Required indices

- Every consumed `markdup_bam_index` BAI and the consumed `reference_fai` are valid; no new BAM/VCF index is produced.

## Scientific identities

Preserve `wgs_sample_set_id` and `wgs_reference_id`; record `wgs_analysis_id` from consumed hashes, parameter snapshot, samtools version, and actual method.

## Parameter snapshot

Persist `samtools`, `coverage_threshold=1`, `threads`, `max_jobs`, and metric name `coverage_ge_1x`.

## Success and failure evidence

Each run works privately under `.attempts/<run_id>` and may atomically publish to `final/` only after postflight; an old success cannot satisfy the current run.

`status=success` requires flagstat/depth for every sample, zero-coverage positions in the reference-length denominator, and exact sample-set conservation; low coverage does not remove samples. Any failure produces `artifacts={}`.
