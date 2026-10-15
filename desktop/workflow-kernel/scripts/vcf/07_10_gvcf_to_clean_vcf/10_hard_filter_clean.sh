#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash 10_hard_filter_clean.sh \
    --snp-vcf variants.snp.vcf.gz \
    --indel-vcf variants.indel.vcf.gz \
    --output-dir DIR \
    [--reference REF.fa] \
    [--gatk gatk] \
    [--bcftools bcftools] \
    [--java-options "-Xmx4G"] \
    [--prefix variants] \
    [--profile-name default_hard_filter_v1] \
    [--snp-qd 2.0] \
    [--snp-qual 30.0] \
    [--snp-sor 3.0] \
    [--snp-fs 60.0] \
    [--snp-mq 40.0] \
    [--indel-qd 2.0] \
    [--indel-qual 30.0] \
    [--indel-fs 200.0] \
    [--overwrite]

Applies GATK hard filters and creates a GWAS-ready clean VCF from PASS
biallelic SNPs.
EOF
}

SNP_VCF=""
INDEL_VCF=""
OUTPUT_DIR=""
REFERENCE=""
GATK_BIN="${GATK:-gatk}"
BCFTOOLS_BIN="${BCFTOOLS:-bcftools}"
JAVA_OPTIONS="-Xmx4G"
PREFIX="variants"
OVERWRITE=0
PROFILE_NAME="default_hard_filter_v1"
PARAMETER_SOURCE="default"
SNP_QD="2.0"
SNP_QUAL="30.0"
SNP_SOR="3.0"
SNP_FS="60.0"
SNP_MQ="40.0"
INDEL_QD="2.0"
INDEL_QUAL="30.0"
INDEL_FS="200.0"
ORIGINAL_ARGS=("$@")

mark_cli_parameter() {
  PARAMETER_SOURCE="cli"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --snp-vcf) SNP_VCF="${2:-}"; shift 2 ;;
    --indel-vcf) INDEL_VCF="${2:-}"; shift 2 ;;
    --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
    --reference) REFERENCE="${2:-}"; shift 2 ;;
    --gatk) GATK_BIN="${2:-}"; shift 2 ;;
    --bcftools) BCFTOOLS_BIN="${2:-}"; shift 2 ;;
    --java-options) JAVA_OPTIONS="${2:-}"; shift 2 ;;
    --prefix) PREFIX="${2:-}"; shift 2 ;;
    --profile-name) PROFILE_NAME="${2:-}"; shift 2 ;;
    --snp-qd) SNP_QD="${2:-}"; mark_cli_parameter; shift 2 ;;
    --snp-qual) SNP_QUAL="${2:-}"; mark_cli_parameter; shift 2 ;;
    --snp-sor) SNP_SOR="${2:-}"; mark_cli_parameter; shift 2 ;;
    --snp-fs) SNP_FS="${2:-}"; mark_cli_parameter; shift 2 ;;
    --snp-mq) SNP_MQ="${2:-}"; mark_cli_parameter; shift 2 ;;
    --indel-qd) INDEL_QD="${2:-}"; mark_cli_parameter; shift 2 ;;
    --indel-qual) INDEL_QUAL="${2:-}"; mark_cli_parameter; shift 2 ;;
    --indel-fs) INDEL_FS="${2:-}"; mark_cli_parameter; shift 2 ;;
    --overwrite) OVERWRITE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$SNP_VCF" || -z "$INDEL_VCF" || -z "$OUTPUT_DIR" ]]; then
  echo "ERROR: --snp-vcf, --indel-vcf and --output-dir are required" >&2
  usage >&2
  exit 2
fi
resolve_existing_input() {
  local path="$1"
  local base
  base="$(basename "$path")"
  if [[ -f "$path" ]]; then
    printf "%s" "$path"
  elif [[ -f "$base" ]]; then
    printf "%s" "$base"
  elif [[ -f "raw_test_data/$base" ]]; then
    printf "%s" "raw_test_data/$base"
  else
    printf "%s" "$path"
  fi
}
SNP_VCF="$(resolve_existing_input "$SNP_VCF")"
INDEL_VCF="$(resolve_existing_input "$INDEL_VCF")"
if [[ ! -f "$SNP_VCF" ]]; then echo "ERROR: SNP VCF not found: $SNP_VCF" >&2; exit 1; fi
if [[ ! -f "$INDEL_VCF" ]]; then echo "ERROR: INDEL VCF not found: $INDEL_VCF" >&2; exit 1; fi
if [[ -n "$REFERENCE" && ! -f "$REFERENCE" ]]; then echo "ERROR: reference not found: $REFERENCE" >&2; exit 1; fi
if ! command -v "$GATK_BIN" >/dev/null 2>&1; then echo "ERROR: GATK not found: $GATK_BIN" >&2; exit 1; fi
if ! command -v "$BCFTOOLS_BIN" >/dev/null 2>&1; then echo "ERROR: bcftools not found: $BCFTOOLS_BIN" >&2; exit 1; fi
validate_number() {
  local name="$1"
  local value="$2"
  python3 - "$name" "$value" <<'PY'
import sys
name, value = sys.argv[1], sys.argv[2]
try:
    numeric = float(value)
except ValueError:
    raise SystemExit(f"ERROR: {name} must be numeric, got {value!r}")
if numeric < 0:
    raise SystemExit(f"ERROR: {name} must be non-negative, got {value!r}")
PY
}

validate_number "--snp-qd" "$SNP_QD"
validate_number "--snp-qual" "$SNP_QUAL"
validate_number "--snp-sor" "$SNP_SOR"
validate_number "--snp-fs" "$SNP_FS"
validate_number "--snp-mq" "$SNP_MQ"
validate_number "--indel-qd" "$INDEL_QD"
validate_number "--indel-qual" "$INDEL_QUAL"
validate_number "--indel-fs" "$INDEL_FS"

mkdir -p "$OUTPUT_DIR"
SNP_FILTERED="$OUTPUT_DIR/${PREFIX}.snp.hard_filtered.vcf.gz"
INDEL_FILTERED="$OUTPUT_DIR/${PREFIX}.indel.hard_filtered.vcf.gz"
SNP_PASS="$OUTPUT_DIR/${PREFIX}.snp.hard_filtered.pass.vcf.gz"
INDEL_PASS="$OUTPUT_DIR/${PREFIX}.indel.hard_filtered.pass.vcf.gz"
CLEAN_VCF="$OUTPUT_DIR/clean.vcf.gz"
METRICS_JSON="$OUTPUT_DIR/hard_filter_metrics.json"
LOG_FILE="$OUTPUT_DIR/hard_filter_clean.log"

if [[ "$OVERWRITE" -ne 1 ]]; then
  for f in "$SNP_FILTERED" "$INDEL_FILTERED" "$SNP_PASS" "$INDEL_PASS" "$CLEAN_VCF"; do
    if [[ -e "$f" || -e "$f.tbi" || -e "$f.idx" ]]; then
      echo "ERROR: output already exists. Use --overwrite to replace: $f" >&2
      exit 1
    fi
  done
else
  rm -f "$SNP_FILTERED" "$SNP_FILTERED.tbi" "$SNP_FILTERED.idx" \
    "$INDEL_FILTERED" "$INDEL_FILTERED.tbi" "$INDEL_FILTERED.idx" \
    "$SNP_PASS" "$SNP_PASS.tbi" "$INDEL_PASS" "$INDEL_PASS.tbi" \
    "$CLEAN_VCF" "$CLEAN_VCF.tbi"
fi

count_records() {
  "$BCFTOOLS_BIN" view -H "$1" 2>/dev/null | wc -l | awk '{print $1}'
}

filter_name() {
  local prefix="$1"
  local threshold="$2"
  local label="${threshold%.0}"
  label="${label//./p}"
  label="${label//-/m}"
  printf "%s%s" "$prefix" "$label"
}

gatk_base=("$GATK_BIN" --java-options "$JAVA_OPTIONS" VariantFiltration)
if [[ -n "$REFERENCE" ]]; then
  gatk_base+=(-R "$REFERENCE")
fi
SNP_QD_NAME="$(filter_name QD "$SNP_QD")"
SNP_QUAL_NAME="$(filter_name QUAL "$SNP_QUAL")"
SNP_SOR_NAME="$(filter_name SOR "$SNP_SOR")"
SNP_FS_NAME="$(filter_name FS "$SNP_FS")"
SNP_MQ_NAME="$(filter_name MQ "$SNP_MQ")"
INDEL_QD_NAME="$(filter_name QD "$INDEL_QD")"
INDEL_QUAL_NAME="$(filter_name QUAL "$INDEL_QUAL")"
INDEL_FS_NAME="$(filter_name FS "$INDEL_FS")"
COMMAND_RENDERED="$(printf '%q ' "$0" "${ORIGINAL_ARGS[@]}")"

start_time="$(date +%s)"
{
  echo "========================================"
  echo "VariantFiltration and clean VCF"
  echo "Time: $(date)"
  echo "SNP input: $SNP_VCF"
  echo "INDEL input: $INDEL_VCF"
  echo "Clean VCF: $CLEAN_VCF"
  echo "Parameter profile: $PROFILE_NAME"
  echo "Parameter source: $PARAMETER_SOURCE"
  echo "SNP filters: QD < $SNP_QD; QUAL < $SNP_QUAL; SOR > $SNP_SOR; FS > $SNP_FS; MQ < $SNP_MQ"
  echo "INDEL filters: QD < $INDEL_QD; QUAL < $INDEL_QUAL; FS > $INDEL_FS"
  echo "========================================"
} | tee "$LOG_FILE"

"${gatk_base[@]}" \
  -V "$SNP_VCF" \
  -O "$SNP_FILTERED" \
  --filter-expression "QD < $SNP_QD" --filter-name "$SNP_QD_NAME" \
  --filter-expression "QUAL < $SNP_QUAL" --filter-name "$SNP_QUAL_NAME" \
  --filter-expression "SOR > $SNP_SOR" --filter-name "$SNP_SOR_NAME" \
  --filter-expression "FS > $SNP_FS" --filter-name "$SNP_FS_NAME" \
  --filter-expression "MQ < $SNP_MQ" --filter-name "$SNP_MQ_NAME" 2>&1 | tee -a "$LOG_FILE"
"$GATK_BIN" IndexFeatureFile -I "$SNP_FILTERED" 2>&1 | tee -a "$LOG_FILE"

"${gatk_base[@]}" \
  -V "$INDEL_VCF" \
  -O "$INDEL_FILTERED" \
  --filter-expression "QD < $INDEL_QD" --filter-name "$INDEL_QD_NAME" \
  --filter-expression "QUAL < $INDEL_QUAL" --filter-name "$INDEL_QUAL_NAME" \
  --filter-expression "FS > $INDEL_FS" --filter-name "$INDEL_FS_NAME" 2>&1 | tee -a "$LOG_FILE"
"$GATK_BIN" IndexFeatureFile -I "$INDEL_FILTERED" 2>&1 | tee -a "$LOG_FILE"

"$BCFTOOLS_BIN" view -f PASS "$SNP_FILTERED" -Oz -o "$SNP_PASS" 2>&1 | tee -a "$LOG_FILE"
"$BCFTOOLS_BIN" index -t "$SNP_PASS" 2>&1 | tee -a "$LOG_FILE"
"$BCFTOOLS_BIN" view -f PASS "$INDEL_FILTERED" -Oz -o "$INDEL_PASS" 2>&1 | tee -a "$LOG_FILE"
"$BCFTOOLS_BIN" index -t "$INDEL_PASS" 2>&1 | tee -a "$LOG_FILE"

"$BCFTOOLS_BIN" view -m2 -M2 -v snps -f PASS "$SNP_FILTERED" -Oz -o "$CLEAN_VCF" 2>&1 | tee -a "$LOG_FILE"
"$BCFTOOLS_BIN" index -t "$CLEAN_VCF" 2>&1 | tee -a "$LOG_FILE"

SNP_INPUT_COUNT="$(count_records "$SNP_VCF")"
INDEL_INPUT_COUNT="$(count_records "$INDEL_VCF")"
SNP_FILTERED_COUNT="$(count_records "$SNP_FILTERED")"
INDEL_FILTERED_COUNT="$(count_records "$INDEL_FILTERED")"
SNP_PASS_COUNT="$(count_records "$SNP_PASS")"
INDEL_PASS_COUNT="$(count_records "$INDEL_PASS")"
CLEAN_COUNT="$(count_records "$CLEAN_VCF")"
SAMPLE_COUNT="$("$BCFTOOLS_BIN" query -l "$CLEAN_VCF" 2>/dev/null | wc -l | awk '{print $1}')"
SAMPLES="$("$BCFTOOLS_BIN" query -l "$CLEAN_VCF" 2>/dev/null | paste -sd, -)"
end_time="$(date +%s)"

PROFILE_NAME="$PROFILE_NAME" \
PARAMETER_SOURCE="$PARAMETER_SOURCE" \
SNP_QD="$SNP_QD" \
SNP_QUAL="$SNP_QUAL" \
SNP_SOR="$SNP_SOR" \
SNP_FS="$SNP_FS" \
SNP_MQ="$SNP_MQ" \
INDEL_QD="$INDEL_QD" \
INDEL_QUAL="$INDEL_QUAL" \
INDEL_FS="$INDEL_FS" \
COMMAND_RENDERED="$COMMAND_RENDERED" \
python3 - "$METRICS_JSON" <<PY
import json, os, pathlib
metrics = {
  "schema_version": "vcf_hard_filter_clean_metrics_v1",
  "status": "success",
  "parameter_profile": os.environ["PROFILE_NAME"],
  "parameter_source": os.environ["PARAMETER_SOURCE"],
  "thresholds": {
    "snp": {
      "QD": float(os.environ["SNP_QD"]),
      "QUAL": float(os.environ["SNP_QUAL"]),
      "SOR": float(os.environ["SNP_SOR"]),
      "FS": float(os.environ["SNP_FS"]),
      "MQ": float(os.environ["SNP_MQ"]),
    },
    "indel": {
      "QD": float(os.environ["INDEL_QD"]),
      "QUAL": float(os.environ["INDEL_QUAL"]),
      "FS": float(os.environ["INDEL_FS"]),
    },
  },
  "snp_input_vcf": "$SNP_VCF",
  "indel_input_vcf": "$INDEL_VCF",
  "snp_hard_filtered_vcf": "$SNP_FILTERED",
  "indel_hard_filtered_vcf": "$INDEL_FILTERED",
  "snp_pass_vcf": "$SNP_PASS",
  "indel_pass_vcf": "$INDEL_PASS",
  "clean_vcf": "$CLEAN_VCF",
  "snp_input_count": int("$SNP_INPUT_COUNT"),
  "indel_input_count": int("$INDEL_INPUT_COUNT"),
  "snp_filtered_count": int("$SNP_FILTERED_COUNT"),
  "indel_filtered_count": int("$INDEL_FILTERED_COUNT"),
  "snp_pass_count": int("$SNP_PASS_COUNT"),
  "indel_pass_count": int("$INDEL_PASS_COUNT"),
  "clean_snp_count": int("$CLEAN_COUNT"),
  "sample_count": int("$SAMPLE_COUNT"),
  "sample_ids": [x for x in "$SAMPLES".split(",") if x],
  "clean_rule": "PASS biallelic SNPs from SNP hard-filtered VCF",
  "command_rendered": os.environ["COMMAND_RENDERED"].strip(),
  "wall_sec": int($end_time - $start_time),
}
pathlib.Path("$METRICS_JSON").write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\\n", encoding="utf-8")
PY

cat "$METRICS_JSON" | tee -a "$LOG_FILE"

if [[ "$CLEAN_COUNT" -eq 0 ]]; then
  echo "ERROR: clean VCF has no records" >&2
  exit 1
fi
