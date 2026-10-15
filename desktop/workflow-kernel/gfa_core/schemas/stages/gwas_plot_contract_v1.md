# `gwas_plot` stage contract v1

## Boundary

- Stage: `gwas_plot`.
- Canonical runner: `python -m apps.cli.run_gwas_plot`.
- Scientific action: CMplot Manhattan and QQ rendering for the Stage 22 MLM and CMLM results.
- Dependency: `gwas_mlm`.
- Authoritative input roles: `mlm_pvalues` and `cmlm_pvalues`.
- Scientific output roles, exactly: `mlm_manhattan`, `mlm_qq`, `cmlm_manhattan`, and `cmlm_qq`.
- Additional success evidence roles: `report` and `environment`.

## Required invocation

The CLI requires workspace, job ID, prefix, exact MLM and CMLM p-value paths,
`genotype_scope_id`, `phenotype_scope_id`, `covariate_scope_id`,
`source_model_scope_id`, separate MLM and CMLM tested-hypothesis counts, an
explicit executable Rscript path, and a log path. The established 11-sample
Stage 22 validation uses `93563` tested hypotheses for each model. The TASSEL
full-result `row_count=93564` is not a denominator because it includes the
`Marker=None` pseudo-row.

## Scientific validation and plotting

- Each input header is literally `chr\tpos\tp`; every physical data line has exactly three literal tab-separated fields. TSV quote interpretation is disabled, so quoted headers, quoted embedded tabs, and quoted newlines are invalid.
- `chr` is nonempty, `pos` is a positive integer, and `p` is finite and in `(0,1]`.
- Each model must contain at least one plottable row and satisfy
  `tested_hypotheses >= valid_p_rows >= plotted_rows`.
- Duplicate hypotheses are preserved. Duplicate coordinate row counts are recorded per model.
- One deterministic reversible contig-to-positive-integer map is derived from the naturally ordered union of both tables (`1,2,10`, not `1,10,2`). The report and Manifest record the map, reverse map, order, and per-model contig row counts. Contig biology is not inferred from names when no authoritative classification metadata is supplied.
- Bonferroni thresholds are model-specific and exactly `0.05 / tested_hypotheses`.
- Plotting uses installed CMplot through the explicit Rscript executable. The attempt-local R source opens explicit PNG devices and calls `CMplot(..., file.output=FALSE)` for all four plots. Both Manhattan and QQ calls receive the full CMplot-compatible `SNP`, `Chromosome`, `Position`, `P` table. Package installation and substitute plotting libraries are prohibited.

## Authority and identity

The Manifest preserves the authoritative `genotype_scope_id`,
`phenotype_scope_id`, and `covariate_scope_id`. The Stage 22 model identity is
stored as `source_model_scope_id`; this plot stage does not mint a new
`model_scope_id`. A new `analysis_scope_id` is materialized from both exact
input SHA256 values, tested-hypothesis counts and plotting parameters, method
and contract versions, preserved data scopes, and proven R and CMplot versions.
The authoritative hashes are captured before plotting and rechecked unchanged
after CMplot completes and before publication. A log path that aliases either
authoritative input is rejected before the log or input can be mutated. Prefixes
are labels only.

## Atomic publication and evidence

All four PNGs form one atomic scientific publication. Before publication each
must have the PNG signature, a 13-byte IHDR chunk, and positive width and
height. A missing or corrupt image produces a failed core Manifest v1 with
`artifacts={}` and publishes no partial scientific result.

On success, `final/` contains only the four scientific PNG artifacts, a
human-readable Markdown `report`, an `environment` record, and the successful
core Manifest v1. Every artifact record carries path, size, media type, and
SHA256. Raw R or shell source, the temporary R script, CMplot input tables, the
underlying R command, and the complete log remain outside `final/` under the
attempt/log locations. Contig strings are preserved exactly in Manifest mapping
data and Markdown table metacharacters are escaped in the human-readable report.

Every run writes the five-file reproducibility bundle:
`progress.json`, `commands.sh`, `environment.txt`, `checksums.sha256`, and
`provenance.json`. `commands.sh` invokes only
`python -m apps.cli.run_gwas_plot` and never exposes the underlying CMplot
command or R source.
