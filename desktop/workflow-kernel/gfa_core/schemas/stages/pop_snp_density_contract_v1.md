# pop_snp_density contract v1

## Identity and inputs

`pop_snp_density` is a root population-genomics stage consuming `population_vcf`; exact genotype and population-analysis scopes are mandatory.

When both request scopes are empty, they may be recovered only from one successful Manifest for the same job, prefix, stage and input hashes after artifact integrity validation. Partial scopes and ambiguous or invalid Manifests fail closed; complete explicit scopes remain exact. Published Manifests conform to core `manifest_v1`.

## Method

Read SNP CHROM/POS/ID directly from VCF. `chromosome_policy` is one of `all_vcf_contigs`, `rice_1_12`, or `explicit`; rice filtering is never implicit. Rice aliases `1/Chr1/chr1` may normalize only when collision-free. Defaults are physical windows of 1,000,000 bp and 500,000 bp.

## Required artifacts and gates

Roles: `snp_coordinates`, `density_1mb_table`, `density_500kb_table`, `density_1mb_plot`, `density_500kb_plot`, `density_heatmap_500kb`, `chromosome_snp_count_plot`, `report`, `environment`, `manifest`. Each density-table count sum must equal the selected SNP count. The heatmap uses true 500 kb genomic windows. CMplot outputs from the current attempt are captured under canonical names; any required missing or stale figure fails postflight.
