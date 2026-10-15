# RNA-seq Candidate Family Contract v1

## Stage

- Stage id: `rnaseq_candidate_family`
- Scope: RNA-seq experiment-level candidate gene-family prioritization
- Purpose: produce reproducible candidate family evidence for downstream result interpretation and discovery agents. This stage does not replace formal family identify/phylogeny.

## Required Inputs

- `final/{prefix}.rnaseq_de.*.DE_genes.txt`
- Target proteome FASTA or reusable genome-wide InterProScan TSV
- Optional GO/KEGG enrichment files:
  - `final/{prefix}.rnaseq_go_enrich.*.GO_enrich.txt`
  - `final/{prefix}.rnaseq_kegg_enrich.*.KEGG_enrich.txt`

## Optional Inputs

- `final/{prefix}.rnaseq_fgsea.*.fgsea_kegg.tsv`
- `final/{prefix}.rnaseq_wgcna.gene_modules.tsv`
- `final/{prefix}.rnaseq_wgcna.module_trait_correlation.tsv`
- `final/{prefix}.rnaseq_wgcna.module_trait_pvalue.tsv`
- `samples.yaml` with optional top-level `species` and `experiment` metadata
- RAG evidence, when enabled by an agent layer
- Reusable genome-wide annotation resources from the current job or a historical local workspace, when they are exposed into the current job by symlink and recorded in manifest provenance

Missing optional inputs must not fail the stage. They should produce empty support columns or context-neutral recommendations.

## Required Outputs

- `final/{prefix}.rnaseq_candidate_family.ranking.tsv`
- `final/{prefix}.rnaseq_candidate_family.gene_annotations.tsv`
- `final/{prefix}.rnaseq_candidate_family.enrichment_context.tsv`
- `final/{prefix}.rnaseq_candidate_family.family_enrichment.tsv`
- `final/{prefix}.rnaseq_candidate_family.evidence_matrix.tsv`
- `final/{prefix}.rnaseq_candidate_family.recommendation.tsv`
- `final/{prefix}.rnaseq_candidate_family.key_genes.tsv`
- `final/{prefix}.rnaseq_experiment_context.json`
- `final/{prefix}.rnaseq_candidate_family.rag_queries.json`
- `final/{prefix}.rnaseq_candidate_family.rag_references.json`
- `final/{prefix}.rnaseq_candidate_family_report.md`
- `final/{prefix}.rnaseq_candidate_family.manifest.json`

## Recommendation Modes

- `context_aware`: a treatment, trait, tissue, organ part, stage, genotype, or cell type context was explicitly provided in `samples.yaml` or upstream parameters.
- `context_neutral`: no explicit biological topic was provided. The report must still recommend families using RNA-seq internal evidence, but must not claim a specific stress, tissue, or developmental interpretation.

## Recommendation Tiers

- `priority_recommended`: supported by DEG evidence plus at least one objective support layer such as family enrichment, fgsea leading-edge overlap, or WGCNA module support.
- `high_response_needs_refinement`: large/broad families with strong response but requiring subfamily or hub-gene refinement before being used as a paper-level family target.
- `mechanism_relevant_limited_size`: smaller families with support in fgsea/WGCNA/family enrichment but limited DEG count.
- `context_neutral_candidate`: DEG-rich candidate retained in context-neutral mode.
- `candidate_pool`: retained for transparency but not a primary recommendation.

## Guardrails

- Recommendation tiers are rule-based summaries of objective evidence, not hidden biological scores.
- fgsea is pathway-level evidence and must not override gene-family evidence.
- WGCNA is module-level candidate evidence; small sample designs must be interpreted as exploratory.
- RAG evidence, when present, is external literature support and must not change statistical outputs.
- `rag_queries.json` is a RAG plan/query bundle, not a claim that literature retrieval has already succeeded.
- `rag_references.json` may be `pending_retrieval` until `/api/agent/rag` attaches real sources.
- Formal family membership still requires identify/domain QC and preferably phylogeny.
- If whole-proteome FASTA or InterProScan TSV is reused from another local workspace, manifest `inputs` must distinguish current-job accessible paths from `external_resource_sources`; status APIs must preserve the external source paths as provenance rather than rebasing them to the current job.
