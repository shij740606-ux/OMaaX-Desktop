# RNA-Seq KEGG Enrichment Stage Contract v1

## Scope

`rnaseq_kegg_enrich` consumes RNA-seq DE foreground genes and a gene-to-KO background mapping, then writes KEGG/KO enrichment results as an independent stage.

## Inputs

- DE foreground: `workspace/<job_id>/final/*DE_genes.txt`
- KEGG background:
  - uploaded/generated `*gene2ko*.txt` or `*gene2ko*.tsv`
  - automatic local annotation via `run_annotation.py` when background source is `auto` and no uploaded background exists

Automatic local annotation for KEGG runs the KEGG scope of `run_annotation.py` (`--annotation-scope kegg`), producing `gene2ko` and `final/{prefix}.annotation_kegg.manifest.json`. It must not require InterProScan/gene2go when only KEGG enrichment is requested. KEGG enrichment requires non-empty KofamScan-derived or uploaded `gene2ko`.

## Outputs

Final outputs under `workspace/<job_id>/final/`:

- `{prefix}.rnaseq_kegg_enrich.manifest.json`
- `{prefix}.rnaseq_kegg_enrich_report.md`
- `{prefix}.rnaseq_kegg_enrich.{comparison}.KEGG_enrich.txt`

Stage workspace under `workspace/<job_id>/rnaseq_kegg_enrich/`:

- `commands.sh`
- `environment.yml`
- `checksums.sha256`
- optional intermediate files/logs

## Manifest

The manifest `stage` field must be `rnaseq_kegg_enrich`. Required metrics:

- `de_genes_input`
- `kegg_enriched_pathways`
- `comparisons`
- `annotated_background_genes`
- `foreground_mode`

## Failure Semantics

Missing DE files, missing/empty `gene2ko`, incomplete KofamScan profiles, or enrichment execution failure must produce a failed manifest where possible and return a user-facing diagnostic that names the missing dependency.
