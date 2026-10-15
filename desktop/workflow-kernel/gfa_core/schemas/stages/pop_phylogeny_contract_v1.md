# pop_phylogeny contract v1

## Identity and inputs

`pop_phylogeny` is a root population-genomics stage consuming `population_vcf`. Its manifest requires exact genotype and population-analysis scopes.

When both request scopes are empty, they may be recovered only from one successful Manifest for the same job, prefix, stage and input hashes after artifact integrity validation. Partial scopes and ambiguous or invalid Manifests fail closed; complete explicit scopes remain exact. Published Manifests conform to core `manifest_v1`.

## Method

Filter to biallelic single-nucleotide SNPs, then use TASSEL `SortGenotypeFilePlugin` and `ExportPlugin` with `Phylip_Inter`, followed by `FastTree -nt -gtr` and `ape` plots. Genotypes are decoded from REF/ALT: homozygotes use their allele, heterozygotes use the matching IUPAC symbol, and missing calls use the recorded missing symbol. Allele indexes are never mapped to fixed A/C/G/T bases. TASSEL label changes require a reversible collision-free label map.

## Required artifacts and gates

Roles: `phylogeny_alignment_phylip`, `phylogeny_newick`, `phylogeny_rectangular_plot`, `phylogeny_circular_plot`, `alignment_qc`, `tree_qc`, `report`, `environment`, `manifest`. Alignment sequence count and tree tips must exactly equal the unique VCF sample set; sequences are non-empty/equal length; Newick is parseable and branch lengths finite. Any tool or artifact failure is terminal.

This is an exploratory SNP-only population tree, not an ascertainment-corrected publication phylogeny.
