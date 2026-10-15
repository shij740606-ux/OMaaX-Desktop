# reference_classification Contract v1

## Purpose

Assign target-species gene family members to reference-defined subfamilies using a joint target + reference phylogenetic tree.

Normally this stage does not rebuild the publication phylogenetic tree. It annotates tree tips with subfamily labels and confidence evidence for downstream motif/domain/structure/combo plots. If the declared tree contains target tips but no reference tips, the runner may build a lightweight target + reference helper tree solely for classification and record that tree in the manifest.

## Required Inputs

- `final/{prefix}.tree.nwk`
  - Preferred input: joint phylogenetic tree containing target tips and reference tips.
  - Fallback: target-only tree plus target proteins and reference proteins, allowing the stage to build a helper joint tree.
- Reference subfamily file
  - Two-column text/TSV file: `reference_gene_id<TAB>subfamily`.
  - Can be passed explicitly or discovered from `input/{job_id}` / `final`.

## Parameters

- `prefix`: analysis prefix.
- `target_prefix`: target tree-tip prefix, e.g. `Ft` or `Os`.
- `reference_prefix`: reference tree-tip prefix, e.g. `At`.
- `reference_subfamily`: optional path to the reference subfamily table.
- `write_gene_subfamily`: whether to write compatible `final/{prefix}.geneID.subfamily` if absent.
- `overwrite_gene_subfamily`: whether to overwrite an existing compatible subfamily file.

## Outputs

- `final/{prefix}.reference_classification.assignment.tsv`
  - Full evidence table with nearest reference, nearest3 references, clade evidence, assigned subfamily, confidence and note.
- `final/{prefix}.reference_classification.subfamily.tsv`
  - Two-column plotting table: `gene_id<TAB>subfamily`.
- `final/{prefix}.reference_classification.confidence.tsv`
  - Lightweight plotting metadata: `gene_id, subfamily, confidence, note`.
- `final/{prefix}.geneID.subfamily`
  - Backward-compatible plotting table. This file is written only when absent unless overwrite is requested.
- `final/{prefix}.reference_classification_report.md`
- `final/{prefix}.reference_classification.manifest.json`
- `final/{prefix}.reference_joint.tree.nwk` when a helper joint tree is auto-built.

## Joint Multi-Species Mode

When `analysis_mode=joint_multi_species`, target and comparative species are classified together with the reference species in one evidence scope. The stage writes one joint prediction prefix, for example `Target.SPL.target-Ls.ref-At.comp-Ha-Os.subfamily_prediction.*`. The prediction manifest must declare `classifiable_species` as target plus comparative species and must not rewrite comparative species as target role.

The joint prediction table is an input to one joint review dialog. It is not a per-species confirmed label file until `subfamily_confirm` saves user-reviewed labels and emits per-species projections.

## Manifest

The manifest stage must be `reference_classification` and status must be `success` when all declared artifacts exist.

## Downstream Use

`reference_classification.subfamily.tsv` and compatible `geneID.subfamily` are target prediction outputs. They are valid inputs for `subfamily_confirm` and for explicit prediction-review plots, but downstream/evidence stages must not silently auto-detect them as confirmed labels. Formal plotting stages should use `confirmed_subfamily.tsv` by default, or no subfamily coloring when confirmation is absent.

Reference-known subfamily files such as `At.SPL.subfamily` remain valid inputs for this stage and are not target prediction fallback.

Low-confidence target assignments must not modify tree tip IDs. Plotting layers may display a visual marker such as `*` using the assignment/confidence table.
