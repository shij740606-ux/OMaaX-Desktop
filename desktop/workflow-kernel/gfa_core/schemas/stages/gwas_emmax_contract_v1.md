# `gwas_emmax` stage contract v1

## Boundary

- Stage: `gwas_emmax`.
- Runner: `python -m apps.cli.run_gwas_emmax`.
- Scientific action: derive a retained-sample PLINK relationship matrix and
  PCA covariates, then run EMMAX association.
- Dependencies: `gwas_fmt`, `gwas_trait`.
- Required input roles: `sorted_vcf`, `trait_table`.
- Required producer evidence: the exact successful `gwas_fmt` Manifest
  authorizing `sorted_vcf` under the requested genotype scope and the exact
  successful `gwas_trait` Manifest authorizing `trait_table` under the
  requested phenotype scope. The runner accepts these two explicit Manifest
  paths and never scans for substitutes.
- Scientific output roles: `emmax_full`, a six-column TSV with exact header
  `SNPID, Chr, Pos, beta, SE, p`; and `emmax_pvalues`, a three-column TSV with
  exact header `chr, pos, p`. When the source schema does not provide a
  standard error, the stable `emmax_full` schema records the missing value as
  the literal `NA`; a standard error is never estimated or fabricated.

PLINK/EMMAX intermediates, including BED/BIM/FAM, REL/REL-ID, PCA,
TPED/TFAM, the canonical marker map, generated R source, and raw EMMAX files,
remain in the attempt directory and are never formal artifacts.

## Authority and identity

A relevant successful upstream Manifest is authoritative and must pass path,
size, SHA256, status, producer stage, artifact role, and scope checks. This
runner performs no directory discovery and model stages do not accept ad-hoc
downstream file mixing.

Both producer Manifests must be core Manifest `1.0` and carry a nonempty safe
`run_id`. The `gwas_trait` producer must additionally carry a valid
`gwas-analysis:<64hex>` identity. A valid core-v1 `gwas_fmt` producer may omit
`analysis_scope_id`; in that case its lineage is represented by its exact
genotype scope, Manifest SHA256, and run ID, and the downstream input record
omits `producer_analysis_scope_id` rather than inventing or emitting an empty
identity.

The resolved input bytes and both producer Manifest bytes are revalidated
immediately before success publication. Any path, size, or SHA256 mutation
fails the attempt without replacing an existing success.

The Manifest carries exact `genotype_scope_id`, `phenotype_scope_id`, derived
`model_scope_id`, and independently derived `analysis_scope_id`. This stage has
no covariate scope: kinship and PCA are stage-owned derivations from the sorted
VCF. Identity materialization uses exact upstream artifact hashes after
dependencies succeed; prefixes are labels only.

## Scientific invariants

- Before PLINK, each normalized `(CHROM, positive POS, uppercase REF,
  uppercase ALT)` tuple receives `v_` plus 24 lowercase SHA256 hex characters.
  Duplicate tuples and truncated-hash collisions fail. Only multiallelic ALT
  records are excluded; half calls use explicit PLINK policy `missing`.
- Trait IDs are a nonempty subset of unique VCF samples. FAM, TFAM, REL-ID and
  PCA sample identities and order are checked explicitly; no sample count is
  hard-coded.
- After PLINK transpose and before marker-rank estimation or EMMAX, TPED rows
  are parsed with exact width `4 + 2*N`. Marker IDs must be unique and belong
  to the canonical marker map. `0 0` is a missing diploid genotype; a half call
  with exactly one `0` fails. All nonzero alleles outside PLINK's `1/2`
  encoding also fail.
  Nonmissing diploid pairs are unordered, so `1 2` and `2 1` are one observed
  state. Under the frozen identity parameter
  `estimability_policy=at_least_two_observed_diploid_genotype_states.v1`, a
  marker with no observed states is excluded as `no_called_genotype`, a marker
  with exactly one observed state is excluded as `nonvariable_genotype`, and a
  marker with at least two observed states is retained. The TPED is atomically
  replaced by only retained rows; an empty retained set fails without replacing
  the candidate TPED.
- The PLINK lower triangle is bound through REL-ID and its original diagonal
  is preserved. The symmetric finite matrix must satisfy the frozen
  scale-aware PSD tolerance. Realized PCs are exactly
  `min(requested_pcs, N-2, marker_matrix_rank)` and the intercept-plus-PC design
  must be full rank with positive residual degrees of freedom. Marker-matrix
  rank is computed from the filtered TPED, and EMMAX receives that same TPED.
- EMMAX must exit zero. Its headerless `.ps` must use one consistent schema for
  the entire file: either the official Ubuntu `emmax` package schema with
  exactly three fields `SNPID beta p`, or the supported legacy upstream-binary
  schema with exactly four fields `SNPID beta SE p`. Mixed-width rows and all
  other field counts fail. Every canonical SNPID joins exactly once through
  the attempt-local marker map; coordinates are never inferred from SNPID
  text. Beta and p are finite and `0 < p <= 1`. In the four-field schema SE is
  numeric, finite, and nonnegative; in the three-field schema output SE is
  explicitly `NA`.
- Metrics record `emmax_candidate_marker_count`, `emmax_tested_count`,
  `no_called_genotype_excluded_count`,
  `nonvariable_genotype_excluded_count`, and `estimability_policy`, and must
  prove `candidate = no-called excluded + nonvariable excluded + tested`.
  The `.ps` parser's tested count must exactly equal the filtered TPED tested
  count; it never accepts a `NaN` sentinel or a missing tested marker. Metrics
  also record `source_ps_field_count`, `source_ps_schema`, `finite_se_count`,
  and `missing_se_count`. For the official three-field schema the SE counts are
  `0` and the tested-marker count respectively; for the four-field schema they
  are the tested-marker count and `0` respectively.

## Evidence and publication

Success requires nonempty validated scientific artifacts plus `environment`, `report`, and a core Manifest v1 dictionary with size and SHA256 for every artifact. Failure writes a failed Manifest with empty artifacts and the same sanitized root cause as the standard log. Outputs publish atomically per identity; the newest attempt controls status and stale attempts cannot overwrite it.

The report records candidate, exclusion, and tested counts, the estimability
policy, the detected source `.ps` schema, and finite/missing SE counts. The
environment records the same estimability counts and policy, both accepted
schemas, the `NA` missing-SE policy, and the detected source schema alongside
the verified EMMAX package, release, executable path, and SHA256.

The per-run reproducibility bundle contains `progress.json`, `commands.sh`, `environment.txt`, `checksums.sha256`, and `provenance.json`. Replay commands invoke only `python -m apps.cli.run_gwas_emmax`; raw underlying tool commands and source code are not published in `final/`.
