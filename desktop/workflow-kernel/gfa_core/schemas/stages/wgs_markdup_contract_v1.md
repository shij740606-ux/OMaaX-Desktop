# `wgs_markdup` stage contract v1

Stage: `wgs_markdup`

## Consumes

- From `wgs_align`: `sorted_bam` and `sorted_bam_index` per sample.
- Parameters: `gatk`, `max_jobs`, and `remove_duplicates=true`.

## Provides

- Per-sample `markdup_bam`, `markdup_bam_index`, `markdup_metrics`, and `markdup_log`.

## Canonical filenames

- `{prefix}.wgs_markdup.{sample_id}.markdup.bam`, `{prefix}.wgs_markdup.{sample_id}.markdup.bam.bai`, `{prefix}.wgs_markdup.{sample_id}.markdup.metrics.txt`, and `{prefix}.wgs_markdup.{sample_id}.markdup.log`.

## Required indices

- Every `markdup_bam` has a valid `markdup_bam_index` BAI.

## Scientific identities

Preserve `wgs_sample_set_id` and `wgs_reference_id` from the authoritative upstream Manifest; record `wgs_analysis_id` from consumed hashes, parameter snapshot, GATK version, and actual method.

## Parameter snapshot

Persist `gatk`, its resolved version, `max_jobs`, `remove_duplicates=true`, and `CREATE_INDEX=true`.

## Success and failure evidence

Each run works privately under `.attempts/<run_id>` and may atomically publish to `final/` only after postflight; an old success cannot satisfy the current run.

`status=success` requires all input BAMs to publish valid BAM, BAI, metrics, and log roles with the same sample set. Any sample, tool, index, or validation failure produces `artifacts={}`.
