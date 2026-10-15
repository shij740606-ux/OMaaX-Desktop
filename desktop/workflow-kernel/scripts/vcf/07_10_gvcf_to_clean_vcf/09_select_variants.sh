#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash 09_select_variants.sh \
    --input-vcf genotyped.vcf.gz \
    --output-dir DIR \
    [--gatk gatk] \
    [--bcftools bcftools] \
    [--java-options "-Xmx4G"] \
    [--prefix variants] \
    [--overwrite]

Splits a genotyped VCF into SNP and INDEL VCFs using GATK SelectVariants.
EOF
}

INPUT_VCF=""
OUTPUT_DIR=""
GATK_BIN="${GATK:-gatk}"
BCFTOOLS_BIN="${BCFTOOLS:-bcftools}"
JAVA_OPTIONS="-Xmx4G"
PREFIX="variants"
OVERWRITE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input-vcf) INPUT_VCF="${2:-}"; shift 2 ;;
    --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
    --gatk) GATK_BIN="${2:-}"; shift 2 ;;
    --bcftools) BCFTOOLS_BIN="${2:-}"; shift 2 ;;
    --java-options) JAVA_OPTIONS="${2:-}"; shift 2 ;;
    --prefix) PREFIX="${2:-}"; shift 2 ;;
    --overwrite) OVERWRITE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$INPUT_VCF" || -z "$OUTPUT_DIR" ]]; then
  echo "ERROR: --input-vcf and --output-dir are required" >&2
  usage >&2
  exit 2
fi
if [[ ! -f "$INPUT_VCF" ]]; then echo "ERROR: input VCF not found: $INPUT_VCF" >&2; exit 1; fi
if ! command -v "$GATK_BIN" >/dev/null 2>&1; then echo "ERROR: GATK not found: $GATK_BIN" >&2; exit 1; fi
if ! command -v "$BCFTOOLS_BIN" >/dev/null 2>&1; then echo "ERROR: bcftools not found: $BCFTOOLS_BIN" >&2; exit 1; fi

mkdir -p "$OUTPUT_DIR"
SNP_VCF="$OUTPUT_DIR/${PREFIX}.snp.vcf.gz"
INDEL_VCF="$OUTPUT_DIR/${PREFIX}.indel.vcf.gz"
METRICS_JSON="$OUTPUT_DIR/variant_selection_metrics.json"
LOG_FILE="$OUTPUT_DIR/select_variants.log"

if [[ "$OVERWRITE" -ne 1 ]]; then
  for f in "$SNP_VCF" "$INDEL_VCF"; do
    if [[ -e "$f" || -e "$f.tbi" || -e "$f.idx" ]]; then
      echo "ERROR: output already exists. Use --overwrite to replace: $f" >&2
      exit 1
    fi
  done
else
  rm -f "$SNP_VCF" "$SNP_VCF.tbi" "$SNP_VCF.idx" "$INDEL_VCF" "$INDEL_VCF.tbi" "$INDEL_VCF.idx"
fi

count_records() {
  "$BCFTOOLS_BIN" view -H "$1" 2>/dev/null | wc -l | awk '{print $1}'
}

start_time="$(date +%s)"
{
  echo "========================================"
  echo "SelectVariants"
  echo "Time: $(date)"
  echo "Input VCF: $INPUT_VCF"
  echo "SNP output: $SNP_VCF"
  echo "INDEL output: $INDEL_VCF"
  echo "========================================"
} | tee "$LOG_FILE"

if [[ ! -f "$INPUT_VCF.tbi" && ! -f "$INPUT_VCF.idx" ]]; then
  "$BCFTOOLS_BIN" index -t "$INPUT_VCF" 2>&1 | tee -a "$LOG_FILE"
fi

"$GATK_BIN" --java-options "$JAVA_OPTIONS" SelectVariants \
  -V "$INPUT_VCF" \
  -O "$SNP_VCF" \
  -select-type SNP 2>&1 | tee -a "$LOG_FILE"
"$GATK_BIN" IndexFeatureFile -I "$SNP_VCF" 2>&1 | tee -a "$LOG_FILE"

"$GATK_BIN" --java-options "$JAVA_OPTIONS" SelectVariants \
  -V "$INPUT_VCF" \
  -O "$INDEL_VCF" \
  -select-type INDEL 2>&1 | tee -a "$LOG_FILE"
"$GATK_BIN" IndexFeatureFile -I "$INDEL_VCF" 2>&1 | tee -a "$LOG_FILE"

TOTAL_COUNT="$(count_records "$INPUT_VCF")"
SNP_COUNT="$(count_records "$SNP_VCF")"
INDEL_COUNT="$(count_records "$INDEL_VCF")"
SAMPLE_COUNT="$("$BCFTOOLS_BIN" query -l "$INPUT_VCF" 2>/dev/null | wc -l | awk '{print $1}')"
SAMPLES="$("$BCFTOOLS_BIN" query -l "$INPUT_VCF" 2>/dev/null | paste -sd, -)"
end_time="$(date +%s)"

python3 - "$METRICS_JSON" <<PY
import json, pathlib
metrics = {
  "schema_version": "vcf_select_variants_metrics_v1",
  "status": "success",
  "input_vcf": "$INPUT_VCF",
  "snp_vcf": "$SNP_VCF",
  "indel_vcf": "$INDEL_VCF",
  "total_variant_count": int("$TOTAL_COUNT"),
  "snp_count": int("$SNP_COUNT"),
  "indel_count": int("$INDEL_COUNT"),
  "sample_count": int("$SAMPLE_COUNT"),
  "sample_ids": [x for x in "$SAMPLES".split(",") if x],
  "wall_sec": int($end_time - $start_time),
}
pathlib.Path("$METRICS_JSON").write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\\n", encoding="utf-8")
PY

cat "$METRICS_JSON" | tee -a "$LOG_FILE"
