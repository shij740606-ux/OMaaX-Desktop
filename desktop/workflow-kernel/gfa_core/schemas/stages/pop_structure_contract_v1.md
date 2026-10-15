# pop_structure contract v1

## Identity and inputs

`pop_structure` is a root population-genomics stage consuming `population_vcf`. `pop_struct` is a read-only legacy alias and is never executable. Exact genotype and population-analysis scopes are mandatory.

When both request scopes are empty, they may be recovered only from one successful Manifest for the same job, prefix, stage and input hashes after artifact integrity validation. Partial scopes and ambiguous or invalid Manifests fail closed; complete explicit scopes remain exact. Published Manifests conform to core `manifest_v1`.

## Method

Filter biallelic single-nucleotide SNPs, PLINK-prune with defaults `50 10 0.2`, create BED/BIM/FAM, and run ADMIXTURE directly on BED. Defaults are K=2..10, five CV folds and an explicit fixed random seed; `k_max < n_samples`. Threads come from the stage resource request.

## Required artifacts and gates

Roles: `ld_pruned_bed`, `ld_pruned_bim`, `ld_pruned_fam`, `pruning_markers`, per-K `admixture_q`, `admixture_p` and logs, `cv_table`, `best_k_q`, `best_k_p`, `structure_best_k_plot`, `structure_cv_plot`, `report`, `environment`, `manifest`. Every requested K must have finite CV and valid Q/P files. Q rows match FAM order, have K finite values in [0,1], and sum to one within tolerance. Minimum CV selects best K; exact ties select lower K and record the tie set. There is no K=5 fallback.

Best-K and an explicitly selected GWAS-K are different artifact semantics.
