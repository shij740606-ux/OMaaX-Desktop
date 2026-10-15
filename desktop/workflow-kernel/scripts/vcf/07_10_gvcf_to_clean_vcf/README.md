# VCF 07-10: gVCF to clean VCF

This directory contains independent extraction scripts for the VCF/resequencing
pipeline tail. The original large resequencing script is not modified.

## Steps

1. `07_combine_gvcf.sh`: collect GVCFs and run GATK `CombineGVCFs`.
2. `08_genotype_gvcf.sh`: run GATK `GenotypeGVCFs`.
3. `09_select_variants.sh`: split genotyped VCF into SNP and INDEL VCFs.
4. `10_hard_filter_clean.sh`: apply hard filters and emit GWAS-ready `clean.vcf.gz`.

## Important Boundary

`08_genotype_gvcf.sh` does not silently fall back to a small interval. If
`--intervals` is supplied, the metrics file marks `subset_interval=true`.

## Test Data

The test runner uses the current single-sample fixture:

```text
/home/xia-lab/wutx_old/wutx/plantAnalysisAgent/vcf_code/vcf/06.gvcf/12.g.vcf.gz
```

This validates script correctness and chain connectivity only. It is not a
population GWAS result.

## Run

```bash
env PATH=/data/miniconda3/envs/ASAW/bin:/usr/bin:/bin \
  bash scripts/vcf/07_10_gvcf_to_clean_vcf/run_07_10_test.sh
```

Default output:

```text
/tmp/vcf_07_10_extracted_test
```
