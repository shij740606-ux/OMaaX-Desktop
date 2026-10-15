# `gwas_kinship` stage contract v1

## Boundary

- Stage: `gwas_kinship`.
- Runner: `python -m apps.cli.run_gwas_kinship`.
- Scientific action: TASSEL centered IBS kinship.
- Dependencies: `gwas_fmt`.
- Required input roles: `sorted_vcf`.
- Scientific output roles: `kinship_matrix`.

## Authority and identity

A relevant successful upstream Manifest is authoritative and must pass path, size, SHA256, status, and identity checks. Without a relevant upstream Manifest, root input discovery is restricted to the current job's `input/<job_id>` and `workspace/<job_id>/final`; zero candidates fail and multiple distinct candidates require confirmation. Model stages do not accept ad-hoc downstream file mixing.

The Manifest carries every applicable `genotype_scope_id`, `phenotype_scope_id`, `covariate_scope_id`, `model_scope_id`, `source_model_scope_id`, and `analysis_scope_id`. Identity materialization uses exact upstream artifact hashes after dependencies succeed; prefixes are labels only.

## Evidence and publication

Success requires nonempty validated scientific artifacts plus `environment`, `report`, and a core Manifest v1 dictionary with size and SHA256 for every artifact. Failure writes a failed Manifest with empty artifacts and the same sanitized root cause as the standard log. Outputs publish atomically per identity; the newest attempt controls status and stale attempts cannot overwrite it.

The per-run reproducibility bundle contains `progress.json`, `commands.sh`, `environment.txt`, `checksums.sha256`, and `provenance.json`. Replay commands invoke only `python -m apps.cli.run_gwas_kinship`; raw underlying tool commands and source code are not published in `final/`.
