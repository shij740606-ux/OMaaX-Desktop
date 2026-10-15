# `gwas_mlm` stage contract v1

## Boundary

- Stage: `gwas_mlm`.
- Runner: `python -m apps.cli.run_gwas_tassel_mlm`.
- Scientific action: TASSEL MLM and CMLM association.
- Dependencies: `gwas_fmt`, `gwas_kinship`, `gwas_trait`, `gwas_cov`.
- Required input roles: `sorted_vcf`, `kinship_matrix`, `trait_table`, `covariate_table`.
- Scientific output roles: `mlm_full`, `cmlm_full`, `mlm_pvalues`, `cmlm_pvalues`.

## Scientific validation

Inputs are explicit, already-resolved files. The phenotype samples must be a nonempty subset of the sorted VCF samples; the kinship matrix must match the VCF sample order; and the prepared covariate table must cover every phenotype sample. The selected phenotype must be finite and non-constant. The VCF must contain at least one estimable genotype marker.

The design matrix consists of an intercept and the first `K-1` prepared covariates. The last covariate is deterministically omitted, matching TASSEL `-excludeLastTrait`. Each candidate marker is classified before execution. A marker is estimable only when its alternate-allele dosage columns add positive marker degrees of freedom and leave positive residual degrees of freedom after marker-specific missingness; insufficient observations, covariate-rank loss, collinearity, and other non-estimable reasons are counted rather than causing failure when another marker is estimable. Multiallelic markers may consume more than one degree of freedom. Candidate, estimable, non-estimable, and per-reason counts are recorded. Preflight fails before TASSEL only when no marker is estimable.

Both P3D models are required: MLM with compression `None`, and CMLM with compression `Optimum`. TASSEL association tables are parsed only through explicit `Trait`, `Marker`, `Chr`, `Pos`, and `p` columns; coordinates are never derived from `Marker`. Standardized tables have exactly `chr`, `pos`, and `p` columns and include only numeric p-values in `(0,1]`. Valid, NaN, invalid, and duplicate-locus row counts are recorded separately for each model.

## Authority and identity

A relevant successful upstream Manifest is authoritative and must pass path, size, SHA256, status, and identity checks. Without a relevant upstream Manifest, root input discovery is restricted to the current job's `input/<job_id>` and `workspace/<job_id>/final`; zero candidates fail and multiple distinct candidates require confirmation. Model stages do not accept ad-hoc downstream file mixing.

The Manifest carries every applicable `genotype_scope_id`, `phenotype_scope_id`, `covariate_scope_id`, `model_scope_id`, `source_model_scope_id`, and `analysis_scope_id`. Identity materialization uses exact upstream artifact hashes after dependencies succeed; prefixes are labels only.

## Evidence and publication

Success requires all four nonempty validated scientific artifacts plus `log`, `environment`, `report`, and a core Manifest v1 dictionary with size and SHA256 for every artifact. If either model fails or either full/standardized output is missing or malformed, failure writes a failed Manifest with empty artifacts and the same root cause as the requested log. Outputs publish atomically under a per-identity writer lock; replacement faults restore the complete prior official set, and stale attempts cannot overwrite newer success. Prefix labels are preserved in the Manifest but mapped to a traversal-safe filename token. Raw shell source and raw TASSEL command artifacts are never published in `final/`.

The per-run reproducibility bundle contains `progress.json`, `commands.sh`, `environment.txt`, `checksums.sha256`, and `provenance.json`. Replay commands invoke only `python -m apps.cli.run_gwas_tassel_mlm`; raw underlying tool commands and source code are not published in `final/`.
