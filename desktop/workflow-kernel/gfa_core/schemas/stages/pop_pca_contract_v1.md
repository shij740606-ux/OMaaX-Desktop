# pop_pca contract v1

## Identity and inputs

`pop_pca` is a root population-genomics stage. It requires `population_vcf` and accepts an optional two-column `sample_group`. The manifest must carry the exact `genotype_scope_id` and `population-analysis:<sha256>` `analysis_scope_id`.

When both request scopes are empty, they may be recovered only from one successful Manifest for the same job, prefix, stage and input hashes after artifact integrity validation. Partial scopes and ambiguous or invalid Manifests fail closed; complete explicit scopes remain exact. Published Manifests conform to core `manifest_v1`.

## Method

Keep only biallelic single-nucleotide SNP records, run PLINK LD pruning with defaults `50 10 0.2`, and calculate at most `min(n_samples-1, n_pruned_markers)` PCs. Group metadata may change plot colours only. Variance percentages are published only when their denominator covers all calculated non-zero components.

## Required artifacts and gates

Roles: `pca_scores`, `pca_eigenvalues`, `pca_variance_table`, `pca_pruning_markers`, `pca_pc1_pc2_plot`, `pca_scree_plot`, `pca_tassel_covariate`, `report`, `environment`, `manifest`. Scores must preserve the VCF sample set without duplicates; pruning must retain at least one marker; the TASSEL file must use formal `<Covariate>/<Trait>` structure. Every artifact is non-empty and SHA256-addressed.

Execution uses an attempt directory and publishes atomically only after postflight. A failed manifest has no artifacts.
