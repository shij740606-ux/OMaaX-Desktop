# `wgs_index` stage contract v1

Stage: `wgs_index`

## Consumes

- Explicit root reference FASTA and parameters `samtools`, `bwa_mem2`, `gatk`, and `threads`.

## Provides

- `reference_fasta`, `reference_fai`, `reference_dict`, and one `bwa_mem2_index_component` record per required component.

## Canonical filenames

- `{prefix}.wgs_index.reference.fasta`, `{prefix}.wgs_index.reference.fasta.fai`, `{prefix}.wgs_index.reference.dict`, and `{prefix}.wgs_index.reference.{component}`.

## Required indices

- `reference_fai` (`.fai`), `reference_dict` (`.dict`), and BWA-MEM2 `.0123`, `.amb`, `.ann`, `.bwt.2bit.64`, and `.pac` components; classic BWA `.bwt` and `.sa` are not required.

## Scientific identities

Record `wgs_reference_id` from reference FASTA SHA256 and `wgs_analysis_id` from reference/index hashes, parameter snapshot, tool versions, and actual method.

## Parameter snapshot

Persist `samtools`, `bwa_mem2`, `gatk`, `threads`, their resolved versions, and the component set.

## Success and failure evidence

Each run works privately under `.attempts/<run_id>` and may atomically publish to `final/` only after postflight; an old success cannot satisfy the current run.

`status=success` requires a non-empty parseable reference with unique contigs and every required index matching its `wgs_reference_id`. Any build or validation failure produces a failed Manifest with `artifacts={}`.
