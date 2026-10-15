# pop_ld_decay contract v1

## Identity and inputs

`pop_ld_decay` consumes `population_vcf` and optionally a two-column `sample_group`; exact genotype and population-analysis scopes are mandatory.

When both request scopes are empty, they may be recovered only from one successful Manifest for the same job, prefix, stage and input hashes after artifact integrity validation. Partial scopes and ambiguous or invalid Manifests fail closed; complete explicit scopes remain exact. Published Manifests conform to core `manifest_v1`.

## Method

Run PopLDdecay with defaults `MAF=0.05`, `Miss=0.2`, `MaxDist=500 kb`, `bin1=10`, `bin2=500`. Group sample IDs are unique, present in the VCF, and mapped to reversible safe slugs. Default `min_group_samples=3` and `invalid_group_policy=fail`; skipped groups require an explicit alternative policy and report entry.

## Required artifacts and gates

Roles: `ld_all_stat`, `ld_all_plot_png`, `ld_all_plot_pdf`, `group_sample_qc`, `report`, `environment`, `manifest`; each valid group additionally has `ld_group_stat` and its own PNG/PDF curve, and two or more valid groups require `ld_multi_plot_png/pdf`. bcftools, PopLDdecay and plotting commands must be versioned. Curves use pair-count-weighted mean r² in explicit physical-distance bins. Empty statistics, missing requested curves, or non-zero subprocess exits fail the stage.
