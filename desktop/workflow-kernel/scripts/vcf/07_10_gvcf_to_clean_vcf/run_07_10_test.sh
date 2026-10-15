#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REFERENCE="${REFERENCE:-/home/xia-lab/wutx_old/wutx/plantAnalysisAgent/vcf_code/vcf/02.Reference_Genome_Indexing/IRGSP.rice.chr.fasta}"
INPUT_DIR="${INPUT_DIR:-/home/xia-lab/wutx_old/wutx/plantAnalysisAgent/vcf_code/vcf/06.gvcf}"
OUTPUT_ROOT="${OUTPUT_ROOT:-/tmp/vcf_07_10_extracted_test}"
GATK_BIN="${GATK:-/data/miniconda3/envs/ASAW/bin/gatk}"
BCFTOOLS_BIN="${BCFTOOLS:-/data/miniconda3/envs/ASAW/bin/bcftools}"

mkdir -p "$OUTPUT_ROOT"

bash "$SCRIPT_DIR/07_combine_gvcf.sh" \
  --reference "$REFERENCE" \
  --input-dir "$INPUT_DIR" \
  --output-dir "$OUTPUT_ROOT/07.combined" \
  --output-name all.g.vcf.gz \
  --gatk "$GATK_BIN" \
  --overwrite

bash "$SCRIPT_DIR/08_genotype_gvcf.sh" \
  --reference "$REFERENCE" \
  --input-gvcf "$OUTPUT_ROOT/07.combined/all.g.vcf.gz" \
  --output-dir "$OUTPUT_ROOT/08.genotyped" \
  --output-name genotyped.vcf.gz \
  --gatk "$GATK_BIN" \
  --bcftools "$BCFTOOLS_BIN" \
  --java-options "-Xmx8G" \
  --overwrite

bash "$SCRIPT_DIR/09_select_variants.sh" \
  --input-vcf "$OUTPUT_ROOT/08.genotyped/genotyped.vcf.gz" \
  --output-dir "$OUTPUT_ROOT/09.selected" \
  --prefix variants \
  --gatk "$GATK_BIN" \
  --bcftools "$BCFTOOLS_BIN" \
  --java-options "-Xmx4G" \
  --overwrite

bash "$SCRIPT_DIR/10_hard_filter_clean.sh" \
  --reference "$REFERENCE" \
  --snp-vcf "$OUTPUT_ROOT/09.selected/variants.snp.vcf.gz" \
  --indel-vcf "$OUTPUT_ROOT/09.selected/variants.indel.vcf.gz" \
  --output-dir "$OUTPUT_ROOT/10.clean" \
  --prefix variants \
  --gatk "$GATK_BIN" \
  --bcftools "$BCFTOOLS_BIN" \
  --java-options "-Xmx4G" \
  --overwrite

clean_vcf="$OUTPUT_ROOT/10.clean/clean.vcf.gz"
sample_ids="$("$BCFTOOLS_BIN" query -l "$clean_vcf" | paste -sd, -)"
clean_count="$("$BCFTOOLS_BIN" view -H "$clean_vcf" | wc -l | awk '{print $1}')"

cat > "$OUTPUT_ROOT/test_summary.tsv" <<EOF
metric	value
reference	$REFERENCE
input_dir	$INPUT_DIR
output_root	$OUTPUT_ROOT
clean_vcf	$clean_vcf
sample_ids	$sample_ids
clean_snp_count	$clean_count
validation_scope	single_sample_test_data
EOF

cat "$OUTPUT_ROOT/test_summary.tsv"
