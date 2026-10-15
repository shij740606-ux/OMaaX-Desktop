#!/usr/bin/env bash
set -euo pipefail

INPUT_DIR="${INPUT_DIR:-.}"
OUTPUT_DIR="${OUTPUT_DIR:-outputs}"
SNP_VCF=""
INDEL_VCF=""
PROFILE_NAME="${PROFILE_NAME:-relaxed_snp_filter_v1}"
PREFIX="${PREFIX:-variants}"
SNP_MQ="${SNP_MQ:-30.0}"
SNP_FS="${SNP_FS:-100.0}"
SNP_SOR="${SNP_SOR:-4.0}"
INDEL_FS="${INDEL_FS:-300.0}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PRODUCTION_SCRIPT="$SCRIPT_DIR/10_hard_filter_clean.sh"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input-dir)
      INPUT_DIR="${2:-}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="${2:-}"
      shift 2
      ;;
    --input-file)
      shift 2
      ;;
    --snp-vcf)
      SNP_VCF="${2:-}"
      shift 2
      ;;
    --indel-vcf)
      INDEL_VCF="${2:-}"
      shift 2
      ;;
    --profile-name)
      PROFILE_NAME="${2:-}"
      shift 2
      ;;
    --prefix)
      PREFIX="${2:-}"
      shift 2
      ;;
    --snp-mq)
      SNP_MQ="${2:-}"
      shift 2
      ;;
    --snp-fs)
      SNP_FS="${2:-}"
      shift 2
      ;;
    --snp-sor)
      SNP_SOR="${2:-}"
      shift 2
      ;;
    --indel-fs)
      INDEL_FS="${2:-}"
      shift 2
      ;;
    --overwrite)
      shift
      ;;
    --*)
      echo "ERROR: unknown argument: $1" >&2
      exit 2
      ;;
    *)
      if [[ "$INPUT_DIR" == "." ]]; then
        INPUT_DIR="$1"
      elif [[ "$OUTPUT_DIR" == "outputs" ]]; then
        OUTPUT_DIR="$1"
      else
        echo "ERROR: unexpected positional argument: $1" >&2
        exit 2
      fi
      shift
      ;;
  esac
done

SNP_VCF="${SNP_VCF:-$INPUT_DIR/variants.snp.vcf.gz}"
INDEL_VCF="${INDEL_VCF:-$INPUT_DIR/variants.indel.vcf.gz}"
if [[ ! -f "$SNP_VCF" && -f "$INPUT_DIR/variants.snp.vcf.gz" ]]; then
  SNP_VCF="$INPUT_DIR/variants.snp.vcf.gz"
fi
if [[ ! -f "$INDEL_VCF" && -f "$INPUT_DIR/variants.indel.vcf.gz" ]]; then
  INDEL_VCF="$INPUT_DIR/variants.indel.vcf.gz"
fi

bash "$PRODUCTION_SCRIPT" \
  --snp-vcf "$SNP_VCF" \
  --indel-vcf "$INDEL_VCF" \
  --output-dir "$OUTPUT_DIR" \
  --profile-name "$PROFILE_NAME" \
  --prefix "$PREFIX" \
  --snp-mq "$SNP_MQ" \
  --snp-fs "$SNP_FS" \
  --snp-sor "$SNP_SOR" \
  --indel-fs "$INDEL_FS" \
  --overwrite

if [[ -f "$OUTPUT_DIR/clean.vcf.gz" ]]; then
  cp -f "$OUTPUT_DIR/clean.vcf.gz" "$OUTPUT_DIR/${PREFIX}.clean.vcf.gz"
fi
if [[ -f "$OUTPUT_DIR/clean.vcf.gz.tbi" ]]; then
  cp -f "$OUTPUT_DIR/clean.vcf.gz.tbi" "$OUTPUT_DIR/${PREFIX}.clean.vcf.gz.tbi"
fi
