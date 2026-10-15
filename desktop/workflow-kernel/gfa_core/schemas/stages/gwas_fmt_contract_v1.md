# `gwas_fmt` stage contract v1

## Identity and boundary

- Stage: `gwas_fmt`.
- Runner: `python -m apps.cli.run_gwas_tassel`.
- Canonical stage ID: `gwas_fmt`.
- `18_gwas_tassel_convert` is provenance only (`parameters.source_label`), never a stage ID.
- Explicit format conversion only: A bare `gwas` request is not this stage. This stage is not a complete GWAS and does not perform association testing.
- This remains a root-capable stage with no synthesized pipeline dependency. If an applicable upstream Manifest declares a VCF, that Manifest is authoritative and invalid Manifest evidence must not fall back to filename discovery. Otherwise the stage resolves VCF candidates only from the current job's `input/<job_id>` and `workspace/<job_id>/final`: one semantic candidate is bound automatically, multiple candidates require explicit confirmation, and zero candidates block execution.
- `genotype_scope_id` is `genotype:<full lowercase SHA256 of input VCF bytes>`.

## Required input

- `external_vcf`: readable VCF or VCF.GZ with a `#CHROM` header, more than zero samples, and more than zero sites.
- Candidate selection is deterministic. LLM, modification time, file size, filename wording, and directory priority must not choose between distinct VCFs.
- `tassel_pipeline`: an existing executable path or executable discoverable on `PATH`.

## Execution

1. TASSEL `-SortGenotypeFilePlugin ... -fileType VCF` writes a sorted VCF.GZ.
2. TASSEL `-importGuess ... -exportType Hapmap` writes HapMap.
3. Input VCF, sorted VCF, and HapMap must have identical sample sets and variant counts. TASSEL's deterministic `sample_sample` alias and sample reordering are accepted only through an unambiguous one-to-one map.
4. `unsupported_marker_policy=mask` is the compatibility default. Reversible diploid biallelic A/C/G/T SNPs receive strict locus, allele, and genotype checks. Indels, `*`, multiallelic, non-diploid, or otherwise non-reversible markers may be emitted by TASSEL but are explicitly counted as lossy and are excluded from the strict genotype claim. `unsupported_marker_policy=fail` rejects such input before TASSEL execution.
5. Chromosome spelling that differs only by letter case is accepted and counted in `normalized_contig_count`; other locus changes fail.

## Required manifest evidence

The success or failure record is a core Manifest v1 document. Its `artifacts` field is a dictionary keyed by artifact role.

### Success evidence

- A core Manifest v1 record with `stage`, `prefix`, `job_id`, `status=success`, start/end timestamps, and `elapsed_sec`.
- `genotype_scope_id`, input path/size/SHA256, TASSEL executable and Xmx, and source label.
- Complete input/sorted/HapMap sample and variant counts plus `strict_marker_count`, `unsupported_marker_count`, `normalized_contig_count`, `strict_genotype_conservation`, `lossy_conversion`, and warnings. These are the required input/sorted/HapMap sample and variant counts for a successful conversion.
- `genotype_conservation=true` is a whole-cohort claim and is therefore false for lossy mask-mode output. Successful lossy output instead requires `strict_genotype_conservation=true` for the reversible subset.
- Exactly the `sorted_vcf`, `tassel_hapmap`, `environment`, and `report` artifact roles, each with semantic role, workspace-relative path, size, media type, and SHA256.
- structured software evidence including a proven TASSEL version, plus environment and report evidence.
- One sanitized PlantAI runner command beginning with `python -m apps.cli.run_gwas_tassel` and standard `logs/` provenance. Resolved launcher paths are allowed only as structured software/environment metadata; raw TASSEL commands and resolved launcher paths must never appear in executable runner/replay command fields.

### Failure evidence

- A failed Manifest is mandatory after any post-argument-parsing failure. It is a core Manifest v1 record with `stage`, `prefix`, `job_id`, `status=failed`, start/end timestamps and `elapsed_sec`.
- When the input digest has been established, retain `genotype_scope_id` and input SHA256; before that point these digest-derived fields may be absent.
- Record the sanitized public `error`, `artifacts={}`, runner/log provenance when available, and any already-computed metrics applicable at the failure point.
- Not required on failure: `software`, TASSEL version, `environment`, `report`, or full input/sorted/HapMap conservation metrics.
- Standard `logs/` provenance carries execution output and ends with the same sanitized public error recorded by the failed Manifest.

## Artifact roles

The only formal artifact roles are `sorted_vcf`, `tassel_hapmap`, `environment`, and `report`. Logs remain under standard `logs/` ownership and are referenced by provenance rather than registered as artifacts.

- `sorted_vcf`
- `tassel_hapmap`
- `environment`
- `report`

The user-visible core files are the manifest, report, sorted VCF, and TASSEL HapMap. Environment is structured evidence and is validated with the artifact set, but is not a user-visible core result. The current stage does not produce or promise images or HTML.

## Stage-local reproducibility

- Each post-digest attempt writes `reproducibility/gwas_fmt/<sanitized-prefix>.<input-sha256-prefix>/` containing exactly `progress.json`, `commands.sh`, `environment.txt`, `checksums.sha256`, and `provenance.json`.
- The `commands.sh` file contains only the sanitized PlantAI runner command beginning with `python -m apps.cli.run_gwas_tassel`; `provenance.json` records the same runner-only replay command and structured software evidence. Resolved launcher paths are allowed only as structured software/environment metadata; raw TASSEL commands and resolved launcher paths must never appear in executable runner/replay command fields.
- The stage-local bundle references the standard `logs/` provenance and the formal `sorted_vcf`, `tassel_hapmap`, `environment`, and `report` artifacts when present.
- This stage-local bundle does not alter or replace global reproducibility behavior.
