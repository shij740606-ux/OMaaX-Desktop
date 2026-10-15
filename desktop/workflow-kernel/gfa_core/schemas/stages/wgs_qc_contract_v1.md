# `wgs_qc` stage contract v1

Stage: `wgs_qc`

## Consumes

- Root artifact role `wgs_sample_config` from the user-confirmed `wgs_samples.yaml`; every listed sample is required.
- Parameters: `fastp`, `threads`, `max_jobs`, `q=5`, `u=50`, `n=10`, `l=50`.

## Provides

- `wgs_sample_config`, `clean_reads_r1`, `clean_reads_r2`, `fastp_html`, and `fastp_json`; each clean-read and fastp role carries its `sample_id`.

## Canonical filenames

- `{prefix}.wgs_qc.{sample_id}.clean.R1.fastq.gz`, `{prefix}.wgs_qc.{sample_id}.clean.R2.fastq.gz`, `{prefix}.wgs_qc.{sample_id}.fastp.html`, and `{prefix}.wgs_qc.{sample_id}.fastp.json`.

## Required indices

None; FASTQ/HTML/JSON outputs are non-empty regular files.

## Scientific identities

Record `wgs_sample_set_id` from normalized sample IDs and R1/R2 content SHA256s, and `wgs_analysis_id` from the input hashes, parameter snapshot, fastp version, and actual method.

## Parameter snapshot

Persist the declared fastp path/version, `threads`, `max_jobs`, `q`, `u`, `n`, and `l`.

## Success and failure evidence

Each run works privately under `.attempts/<run_id>` and may atomically publish to `final/` only after postflight; an old success cannot satisfy the current run.

`status=success` requires all declared samples to have non-empty clean R1/R2, HTML, and JSON outputs and identical input/output sample sets. Any sample or tool failure produces a failed Manifest with `artifacts={}`.
