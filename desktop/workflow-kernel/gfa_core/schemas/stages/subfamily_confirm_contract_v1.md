# subfamily_confirm Contract v1

## Purpose

Persist user-reviewed subfamily labels from `reference_classification` prediction outputs. For `joint_multi_species` classifications, one joint confirmation table is saved first and then projected into per-species confirmed label files for downstream single-species workflows.

## Required Inputs

- Edited or predicted subfamily table containing `gene_id` and `confirmed_subfamily`/`subfamily`.
- Optional source prediction manifest from `reference_classification`. Joint projections must keep this source manifest as provenance.
- Planning and interactive review parameters should read upstream predictions with `analysisRoute = subfamily_prediction`; only the saved confirmation result is validated as `confirmed_subfamily`.

## Outputs

- `final/{prefix}.confirmed_subfamily.tsv`
- `final/{prefix}.confirmed_subfamily.review.tsv`
- `final/{prefix}.confirmed_subfamily_report.md`
- `final/{prefix}.subfamily_confirm.manifest.json`

For a joint confirmation prefix such as `Target.SPL.target-Ls.ref-At.comp-Ha-Os.subfamily_prediction`, the confirm API must also emit per-species projections for each classifiable species:

- `final/Ls.SPL.confirmed_subfamily.tsv`
- `final/Ha.SPL.confirmed_subfamily.tsv`
- `final/Os.SPL.confirmed_subfamily.tsv`
- corresponding `*.confirmed_subfamily.review.tsv` and `*.subfamily_confirm.manifest.json` files

## Manifest

The manifest stage must be `subfamily_confirm` and status must be `success` when all declared artifacts exist. Its result identity is always confirmed-label output:

- `analysis_route = confirmed_subfamily`
- `member_set_id = {scope_id}:confirmed_subfamily:all_members` when `scope_id` is available

The upstream prediction identity must be recorded only as provenance, for example `source_prediction_manifest`, `source_prediction_prefix`, or `sourceAnalysisRoute = subfamily_prediction`. Do not validate a `subfamily_confirm` result with `analysis_route = subfamily_prediction`.

Per-species projection manifests must include:

- `projection.type = per_species_projection_from_joint_subfamily_confirmation`
- `projection.source_joint_prefix`
- `projection.source_prediction_manifest`
- `projection.species` and `projection.family`
- row counts for source and projected labels

## Downstream Use

Formal downstream gene-family analyses should prefer per-species `confirmed_subfamily.tsv` or uploaded target subfamily files. If only `reference_classification.subfamily.tsv` exists, planning must open/insert subfamily confirmation before subfamily-aware downstream analysis instead of silently consuming unconfirmed prediction labels.
