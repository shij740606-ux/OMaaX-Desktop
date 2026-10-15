# `gwas_glm` stage contract v1

## Boundary

- Stage: `gwas_glm`.
- Canonical runner: `python -m apps.cli.run_gwas_tassel_glm`.
- Method: TASSEL 5.2.96 `FixedEffectLMPlugin`, once without population structure and once with Q structure using `-excludeLastTrait`.
- Dependencies: exact successful `gwas_fmt`, `gwas_trait`, and `gwas_cov` producer Manifests.
- Required roles: `sorted_vcf`, `trait_table`, and `covariate_table`.
- Scientific roles: `glm_full`, `glm_structure_full`, `glm_pvalues`, and `glm_structure_pvalues`.

The stage does not consume HapMap, kinship, MLM, EMMAX, or a directory scan. Both models and all four scientific outputs are mandatory.

## Authority and identity

The caller supplies one explicit producer Manifest for each required role. Safe `--job-id`, the resolved workspace basename, and every producer `job_id` must be identical. Every Manifest must have version `1.0`, the exact producer stage, prefix, safe nonempty run ID, and `status=success`; trait and covariate producers must also carry a valid producer analysis scope, while the accepted legacy `gwas_fmt` v1 Manifest may omit it. Each authorized artifact is confined to the current job workspace and must match its workspace-relative `path`, optional `full_path`, regular-file size, and SHA256. `gwas_fmt` and `gwas_cov` must carry the requested common `genotype_scope_id`; trait and covariate scopes must equal the requested identities. Any relevant invalid or mixed lineage fails closed. A requested worker log must resolve within the current job `logs/` directory and must not alias any input artifact or producer Manifest by path or inode.

The materialized Manifest carries `genotype_scope_id`, `phenotype_scope_id`, `covariate_scope_id`, derived `model_scope_id`, and derived `analysis_scope_id`. Its identity recipe includes all three authoritative hashes, method `tassel_fixed_effect_glm.v1`, contract `v1`, TASSEL version, and every fixed parameter. Prefix is only a label.

## Scientific validation

Phenotype IDs are nonempty, unique, and a subset of unique VCF samples. The trait is one finite, nonconstant column. The accepted TASSEL covariate table covers every phenotype sample, contains at least two finite Q columns, and omits its last Q column deterministically.

Before TASSEL, the runner validates both the intercept-only GLM design and the structured intercept plus Q1..Q(K-1) design. Each base design must have full rank and positive residual degrees of freedom. Markers are parsed from VCF `GT`; marker-specific missing calls are excluded. Nonmissing unphased allele tuples are canonicalized into discrete genotype classes and deterministically treatment-coded with one baseline, matching TASSEL `DiscreteSitesFELM`/`FactorModelEffect`; a marker with C observed classes can contribute C-1 degrees of freedom. For each model the observed-sample base rank must be preserved, marker incremental degrees of freedom must be positive, and marker-specific residual degrees of freedom must be positive. Candidate, estimable, non-estimable, per-reason, marker-df, and residual-df metrics are recorded independently. Either model having zero estimable markers fails preflight.

The unstructured command uses forks 1–2 and `combine3`; the structured command uses forks 1–3, `-q ... -excludeLastTrait`, and `combine4`. Both use `FixedEffectLMPlugin`. The fixed defaults are `maxP=1.0`, `permute=false`, `nperm=0`, `genotypeComponent=Genotype`, `minClassSize=0`, `biallelicOnly=false`, and `appendAddDom=false`. TASSEL version is proven by a Stage26-local 30-second bounded combined-output probe and must be exactly 5.2.96. Each child must exit zero and produce its exact statistics file, `glm1.txt` or `glm_structure1.txt`; genotype-effect `*2.txt` files are never formal artifacts.

Full statistics require exactly these 18 columns in order: `Trait`, `Marker`, `Chr`, `Pos`, `marker_F`, `p`, `marker_Rsq`, `add_F`, `add_p`, `dom_F`, `dom_p`, `marker_df`, `marker_MS`, `error_df`, `error_MS`, `model_df`, `model_MS`, `minorObs`. Coordinates come only from explicit `Chr` and `Pos` and must exist in the authoritative VCF. Only the exact cell spelling `NaN` is accepted as TASSEL's missing p-value; such rows still require valid identity, locus, and finite integral df cells, remain in full output, and are excluded from standardized output. Otherwise p must be finite in `(0,1]` and marker/error df must be positive. Standardized files have exactly `chr`, `pos`, and `p` and must be nonempty. Authoritative inputs and producer Manifests are rehashed after both children and before publication.

## Evidence and publication

Success publishes the four scientific files plus `log`, `report.md`, `environment.txt`, and one Manifest v1. Each artifact record is a dictionary containing role, workspace-relative path, full path, media type, size, and SHA256. The report records all five scopes, trait, sample/candidate counts, both design and estimability summaries, valid/NaN rows, Q omission policy, and TASSEL version. Environment records the canonical Python runner/runtime, resolved Python, Python version, resolved TASSEL pipeline, TASSEL version, and platform.

Raw child output stays attempt-private. Public/requested logs remove classpaths, pipeline argv, underlying commands, and absolute paths while retaining versions, progress, warnings, and actionable diagnostics. On nonzero child exit, `ERROR`/`Exception`/`Caused by` lines are prioritized ahead of a short sanitized tail before the public length bound, so long launcher noise cannot hide a terminal cause. Publication is attempt-local and atomic under a per-identity writer lock. Replacement failure restores the preceding complete official set; a stale attempt cannot overwrite a newer success and retains a five-file bundle marked `superseded`. That bundle points only to its attempt-local Manifest/artifacts and hashes those attempt files; it never labels stale hashes with the winner's public paths. A published success clears only an older failed marker under the prefix failure lock and preserves a newer failure. A failure Manifest has the actionable root cause and empty `artifacts`. `final/` contains no raw Shell/R/Python source, underlying TASSEL commands, or `*2.txt` intermediates.

Every run has exactly five reproducibility files under `reproducibility/gwas_glm/<safe-prefix>.<analysis12>/<run_id>/`: `progress.json`, `commands.sh`, `environment.txt`, `checksums.sha256`, and `provenance.json`. `commands.sh` invokes only `python -m apps.cli.run_gwas_tassel_glm` with explicit canonical arguments and never exposes TASSEL internals.
