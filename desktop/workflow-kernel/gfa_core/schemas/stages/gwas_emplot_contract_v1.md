# `gwas_emplot` stage contract v1

## Boundary

- Stage: `gwas_emplot`.
- Runner: `python -m apps.cli.run_gwas_emmax_plot`.
- Scientific action: EMMAX Manhattan and QQ plotting.
- Dependencies: `gwas_emmax`.
- Required input role: `emmax_pvalues` from one explicit successful
  `gwas_emmax` producer Manifest.
- Scientific output roles: `emmax_manhattan`, `emmax_qq`.
- Evidence output roles: `report`, `environment`, plus the core-v1 Manifest.

## Authority and identity

A successful `gwas_emmax` Manifest is authoritative and must match its core-v1
version, safe nonempty run ID, status, artifact role/path/full path/size/SHA256,
and requested genotype, phenotype, and source-model lineage. Stage 25 has no
directory-scan or caller-path fallback. `metrics.emmax_tested_count` is the
authoritative multiple-testing denominator and must match the materialized CLI
value.

The Manifest carries `genotype_scope_id`, `phenotype_scope_id`, the Stage 24
`model_scope_id` as `source_model_scope_id`, and its newly derived
`analysis_scope_id`. It carries neither `covariate_scope_id` nor a new
`model_scope_id`. Identity materialization uses the exact upstream artifact
hash, tested count, fixed plot parameters, method/contract versions, proven
R/CMplot versions, and all three lineage scopes; prefixes are labels only.

## Scientific validation

`emmax_pvalues` is literal TSV with the exact header `chr\tpos\tp` and exactly
three fields per physical data row. It is nonempty; contig is nonempty;
position is a positive integer; and p is finite in `(0,1]`. Duplicate
hypotheses are preserved and counted. The contract requires
`tested_hypotheses >= valid_p_rows >= plotted_rows > 0`; Bonferroni is exactly
`0.05 / tested_hypotheses`.

All observed contigs are plotted in deterministic natural order through a
reversible positive-integer map. Manhattan and QQ plots both receive the full
four-column CMplot table `SNP,Chromosome,Position,P`. The explicit Rscript and
installed CMplot versions must be proven. Required PNGs must have a valid PNG
signature/IHDR and positive dimensions before atomic publication.

## Evidence and publication

Success requires nonempty validated scientific artifacts plus `environment`, `report`, and a core Manifest v1 dictionary with size and SHA256 for every artifact. Failure writes a failed Manifest with empty artifacts and the same sanitized root cause as the standard log. Outputs publish atomically per identity; the newest attempt controls status and stale attempts cannot overwrite it.

The per-run reproducibility bundle contains `progress.json`, `commands.sh`, `environment.txt`, `checksums.sha256`, and `provenance.json`. Replay commands invoke only `python -m apps.cli.run_gwas_emmax_plot`; raw underlying tool commands and source code are not published in `final/`.
