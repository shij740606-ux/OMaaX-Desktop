#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash 08_genotype_gvcf.sh \
    --reference REF.fa \
    --input-gvcf all.g.vcf.gz \
    --output-dir DIR \
    [--output-name genotyped.vcf.gz] \
    [--gatk gatk] \
    [--bcftools bcftools] \
    [--tmp-dir DIR] \
    [--java-options "-Xmx8G"] \
    [--max-alternate-alleles 6] \
    [--stand-call-conf 10.0] \
    [--prefilter-gvcf] \
    [--intervals chr:start-end] \
    [--overwrite]

Runs GATK GenotypeGVCFs on a combined GVCF.

By default this script does not use interval fallback. If --intervals is set,
the run is explicitly marked as subset_interval=true in metrics.
EOF
}

REFERENCE=""
INPUT_GVCF=""
OUTPUT_DIR=""
OUTPUT_NAME="genotyped.vcf.gz"
GATK_BIN="${GATK:-gatk}"
BCFTOOLS_BIN="${BCFTOOLS:-bcftools}"
TMP_DIR=""
JAVA_OPTIONS="-Xmx8G"
MAX_ALT=6
STAND_CALL_CONF="10.0"
PREFILTER_GVCF=0
INTERVALS=""
OVERWRITE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reference) REFERENCE="${2:-}"; shift 2 ;;
    --input-gvcf) INPUT_GVCF="${2:-}"; shift 2 ;;
    --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
    --output-name) OUTPUT_NAME="${2:-}"; shift 2 ;;
    --gatk) GATK_BIN="${2:-}"; shift 2 ;;
    --bcftools) BCFTOOLS_BIN="${2:-}"; shift 2 ;;
    --tmp-dir) TMP_DIR="${2:-}"; shift 2 ;;
    --java-options) JAVA_OPTIONS="${2:-}"; shift 2 ;;
    --max-alternate-alleles) MAX_ALT="${2:-}"; shift 2 ;;
    --stand-call-conf) STAND_CALL_CONF="${2:-}"; shift 2 ;;
    --prefilter-gvcf) PREFILTER_GVCF=1; shift ;;
    --intervals) INTERVALS="${2:-}"; shift 2 ;;
    --overwrite) OVERWRITE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$REFERENCE" || -z "$INPUT_GVCF" || -z "$OUTPUT_DIR" ]]; then
  echo "ERROR: --reference, --input-gvcf and --output-dir are required" >&2
  usage >&2
  exit 2
fi
if [[ ! -f "$REFERENCE" ]]; then echo "ERROR: reference not found: $REFERENCE" >&2; exit 1; fi
if [[ ! -f "$INPUT_GVCF" ]]; then echo "ERROR: input GVCF not found: $INPUT_GVCF" >&2; exit 1; fi
if ! command -v "$GATK_BIN" >/dev/null 2>&1; then echo "ERROR: GATK not found: $GATK_BIN" >&2; exit 1; fi
if ! command -v "$BCFTOOLS_BIN" >/dev/null 2>&1; then echo "ERROR: bcftools not found: $BCFTOOLS_BIN" >&2; exit 1; fi

mkdir -p "$OUTPUT_DIR"
TMP_DIR="${TMP_DIR:-$OUTPUT_DIR/tmp}"
mkdir -p "$TMP_DIR"

OUTPUT_VCF="$OUTPUT_DIR/$OUTPUT_NAME"
FILTERED_GVCF="$OUTPUT_DIR/prefiltered.g.vcf.gz"
METRICS_JSON="$OUTPUT_DIR/genotype_gvcf_metrics.json"
LOG_FILE="$OUTPUT_DIR/genotype_gvcf.log"

if [[ -e "$OUTPUT_VCF" || -e "$OUTPUT_VCF.tbi" || -e "$OUTPUT_VCF.idx" ]]; then
  if [[ "$OVERWRITE" -ne 1 ]]; then
    echo "ERROR: output already exists. Use --overwrite to replace: $OUTPUT_VCF" >&2
    exit 1
  fi
  stamp="$(date +%Y%m%d_%H%M%S)"
  [[ -e "$OUTPUT_VCF" ]] && mv "$OUTPUT_VCF" "$OUTPUT_VCF.bak.$stamp"
  [[ -e "$OUTPUT_VCF.tbi" ]] && mv "$OUTPUT_VCF.tbi" "$OUTPUT_VCF.tbi.bak.$stamp"
  [[ -e "$OUTPUT_VCF.idx" ]] && mv "$OUTPUT_VCF.idx" "$OUTPUT_VCF.idx.bak.$stamp"
fi

count_records() {
  "$BCFTOOLS_BIN" view -H "$1" 2>/dev/null | wc -l | awk '{print $1}'
}

sample_ids() {
  "$BCFTOOLS_BIN" query -l "$1" 2>/dev/null | paste -sd, -
}

start_time="$(date +%s)"
{
  echo "========================================"
  echo "GenotypeGVCFs"
  echo "Time: $(date)"
  echo "Reference: $REFERENCE"
  echo "Input GVCF: $INPUT_GVCF"
  echo "Output VCF: $OUTPUT_VCF"
  echo "Prefilter GVCF: $PREFILTER_GVCF"
  echo "Intervals: ${INTERVALS:-none}"
  echo "GATK: $("$GATK_BIN" --version 2>&1 | head -1)"
  echo "bcftools: $("$BCFTOOLS_BIN" --version 2>&1 | head -1)"
  echo "========================================"
} | tee "$LOG_FILE"

if [[ ! -f "$INPUT_GVCF.tbi" && ! -f "$INPUT_GVCF.idx" ]]; then
  echo "Indexing input GVCF: $INPUT_GVCF" | tee -a "$LOG_FILE"
  "$GATK_BIN" IndexFeatureFile -I "$INPUT_GVCF" 2>&1 | tee -a "$LOG_FILE"
fi

ORIG_RECORDS="$(count_records "$INPUT_GVCF")"
GENOTYPE_INPUT="$INPUT_GVCF"
FILTERED_RECORDS="$ORIG_RECORDS"

if [[ "$PREFILTER_GVCF" -eq 1 ]]; then
  echo "Prefiltering complex GVCF records with bcftools" | tee -a "$LOG_FILE"
  "$BCFTOOLS_BIN" view "$INPUT_GVCF" \
    -e 'COUNT(ALT)>6 || MAX(STRLEN(ALT))>50 || MAX(STRLEN(REF))>50 || QUAL<20' \
    -Oz -o "$FILTERED_GVCF" 2>&1 | tee -a "$LOG_FILE"
  "$BCFTOOLS_BIN" index -t "$FILTERED_GVCF" 2>&1 | tee -a "$LOG_FILE"
  GENOTYPE_INPUT="$FILTERED_GVCF"
  FILTERED_RECORDS="$(count_records "$FILTERED_GVCF")"
fi

cmd=("$GATK_BIN" --java-options "$JAVA_OPTIONS" GenotypeGVCFs
  -R "$REFERENCE"
  -V "$GENOTYPE_INPUT"
  -O "$OUTPUT_VCF"
  --max-alternate-alleles "$MAX_ALT"
  --standard-min-confidence-threshold-for-calling "$STAND_CALL_CONF"
  --include-non-variant-sites false
  --tmp-dir "$TMP_DIR")
if [[ -n "$INTERVALS" ]]; then
  cmd+=(-L "$INTERVALS")
fi

echo "Running: ${cmd[*]}" | tee -a "$LOG_FILE"
"${cmd[@]}" 2>&1 | tee -a "$LOG_FILE"

if [[ ! -f "$OUTPUT_VCF" ]]; then
  echo "ERROR: GenotypeGVCFs finished but output is missing: $OUTPUT_VCF" >&2
  exit 1
fi
if [[ ! -f "$OUTPUT_VCF.tbi" && ! -f "$OUTPUT_VCF.idx" ]]; then
  "$BCFTOOLS_BIN" index -t "$OUTPUT_VCF" 2>&1 | tee -a "$LOG_FILE"
fi

FINAL_RECORDS="$(count_records "$OUTPUT_VCF")"
BCFTOOLS_SNP_PREVIEW_COUNT="$("$BCFTOOLS_BIN" view -H -v snps "$OUTPUT_VCF" 2>/dev/null | wc -l | awk '{print $1}')"
BCFTOOLS_INDEL_PREVIEW_COUNT="$("$BCFTOOLS_BIN" view -H -v indels "$OUTPUT_VCF" 2>/dev/null | wc -l | awk '{print $1}')"
SAMPLES="$(sample_ids "$OUTPUT_VCF")"
SAMPLE_COUNT="$("$BCFTOOLS_BIN" query -l "$OUTPUT_VCF" 2>/dev/null | wc -l | awk '{print $1}')"
end_time="$(date +%s)"

python3 - "$METRICS_JSON" <<PY
import json, pathlib
metrics = {
  "schema_version": "vcf_genotype_gvcf_metrics_v1",
  "status": "success",
  "reference": "$REFERENCE",
  "input_gvcf": "$INPUT_GVCF",
  "genotype_input": "$GENOTYPE_INPUT",
  "output_vcf": "$OUTPUT_VCF",
  "prefilter_gvcf": bool($PREFILTER_GVCF),
  "subset_interval": bool("$INTERVALS"),
  "intervals": "$INTERVALS",
  "original_records": int("$ORIG_RECORDS"),
  "filtered_records": int("$FILTERED_RECORDS"),
  "final_records": int("$FINAL_RECORDS"),
  "bcftools_snp_preview_count": int("$BCFTOOLS_SNP_PREVIEW_COUNT"),
  "bcftools_indel_preview_count": int("$BCFTOOLS_INDEL_PREVIEW_COUNT"),
  "sample_count": int("$SAMPLE_COUNT"),
  "sample_ids": [x for x in "$SAMPLES".split(",") if x],
  "wall_sec": int($end_time - $start_time),
}
pathlib.Path("$METRICS_JSON").write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\\n", encoding="utf-8")
PY

cat "$METRICS_JSON" | tee -a "$LOG_FILE"
