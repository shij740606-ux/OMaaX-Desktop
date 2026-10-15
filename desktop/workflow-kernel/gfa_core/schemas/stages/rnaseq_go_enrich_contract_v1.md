# RNA-Seq GO Enrichment Stage Contract v1

## Scope

`rnaseq_go_enrich` consumes RNA-seq DE foreground genes and a GO background mapping, then writes GO enrichment results as an independent stage.

## Inputs

- DE foreground: `workspace/<job_id>/final/*DE_genes.txt`
- GO background, one of:
  - uploaded/generated `*gene2go*.txt` or `*gene2go*.tsv`
  - supported OrgDb species code: `ath`, `hsa`, `mmu`, `rno`, `dme`, `sce`
  - automatic local annotation via `run_annotation.py` when background source is `auto` and no uploaded background exists

Automatic local annotation for GO runs the GO scope of `run_annotation.py` (`--annotation-scope go`), producing `gene2go` and `final/{prefix}.annotation_go.manifest.json`. It must not require KofamScan/gene2ko when only GO enrichment is requested.

## Outputs

Final outputs under `workspace/<job_id>/final/`:

- `{prefix}.rnaseq_go_enrich.manifest.json`
- `{prefix}.rnaseq_go_enrich_report.md`
- `{prefix}.rnaseq_go_enrich.{comparison}.GO_enrich.txt`

Stage workspace under `workspace/<job_id>/rnaseq_go_enrich/`:

- `commands.sh`
- `environment.yml`
- `checksums.sha256`
- optional R logs/intermediate files

## Manifest

The manifest `stage` field must be `rnaseq_go_enrich`. Required metrics:

- `de_genes_input`
- `go_enriched_terms`
- `comparisons`
- `annotated_background_genes`
- `foreground_mode`

## Failure Semantics

Missing DE files, missing/empty GO background after annotation, invalid OrgDb species, or enrichment execution failure must produce a failed manifest where possible and return a user-facing diagnostic that names the missing dependency.
