#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash scripts/vcf/07_combine_gvcf.sh \
    --reference REF.fa \
    --input-dir DIR \
    --output-dir DIR \
    [--output-name all.g.vcf.gz] \
    [--gatk gatk] \
    [--tmp-dir DIR] \
    [--manifest gvcf_files.txt] \
    [--overwrite]

Collects and combines GVCF files with GATK CombineGVCFs.

Accepted directory patterns:
  *.g.vcf.gz
  *.gvcf.gz
  *.g.vcf
  *.gvcf

If --manifest is provided, each non-empty non-comment line must be a GVCF path.
Relative manifest paths are resolved relative to --input-dir.
EOF
}

REFERENCE=""
INPUT_DIR=""
OUTPUT_DIR=""
OUTPUT_NAME="all.g.vcf.gz"
GATK_BIN="${GATK:-gatk}"
TMP_DIR=""
MANIFEST=""
OVERWRITE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reference)
      REFERENCE="${2:-}"; shift 2 ;;
    --input-dir)
      INPUT_DIR="${2:-}"; shift 2 ;;
    --output-dir)
      OUTPUT_DIR="${2:-}"; shift 2 ;;
    --output-name)
      OUTPUT_NAME="${2:-}"; shift 2 ;;
    --gatk)
      GATK_BIN="${2:-}"; shift 2 ;;
    --tmp-dir)
      TMP_DIR="${2:-}"; shift 2 ;;
    --manifest)
      MANIFEST="${2:-}"; shift 2 ;;
    --overwrite)
      OVERWRITE=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2 ;;
  esac
done

if [[ -z "$REFERENCE" || -z "$INPUT_DIR" || -z "$OUTPUT_DIR" ]]; then
  echo "ERROR: --reference, --input-dir and --output-dir are required" >&2
  usage >&2
  exit 2
fi

if [[ ! -f "$REFERENCE" ]]; then
  echo "ERROR: reference FASTA not found: $REFERENCE" >&2
  exit 1
fi

if [[ ! -d "$INPUT_DIR" ]]; then
  echo "ERROR: input directory not found: $INPUT_DIR" >&2
  exit 1
fi

if ! command -v "$GATK_BIN" >/dev/null 2>&1; then
  echo "ERROR: GATK executable not found: $GATK_BIN" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
TMP_DIR="${TMP_DIR:-$OUTPUT_DIR/tmp}"
mkdir -p "$TMP_DIR"

OUTPUT_GVCF="$OUTPUT_DIR/$OUTPUT_NAME"
LOG_FILE="$OUTPUT_DIR/combine_gvcf.log"

if [[ -e "$OUTPUT_GVCF" || -e "$OUTPUT_GVCF.tbi" || -e "$OUTPUT_GVCF.idx" ]]; then
  if [[ "$OVERWRITE" -ne 1 ]]; then
    echo "ERROR: output already exists. Use --overwrite to replace: $OUTPUT_GVCF" >&2
    exit 1
  fi
  stamp="$(date +%Y%m%d_%H%M%S)"
  [[ -e "$OUTPUT_GVCF" ]] && mv "$OUTPUT_GVCF" "$OUTPUT_GVCF.bak.$stamp"
  [[ -e "$OUTPUT_GVCF.tbi" ]] && mv "$OUTPUT_GVCF.tbi" "$OUTPUT_GVCF.tbi.bak.$stamp"
  [[ -e "$OUTPUT_GVCF.idx" ]] && mv "$OUTPUT_GVCF.idx" "$OUTPUT_GVCF.idx.bak.$stamp"
fi

declare -a GVCF_FILES=()
declare -A SEEN=()

add_gvcf() {
  local item="$1"
  [[ -z "$item" ]] && return 0
  if [[ "$item" != /* ]]; then
    item="$INPUT_DIR/$item"
  fi
  if [[ ! -f "$item" ]]; then
    echo "ERROR: listed GVCF not found: $item" >&2
    exit 1
  fi
  case "$item" in
    *.g.vcf.gz|*.gvcf.gz|*.g.vcf|*.gvcf)
      ;;
    *)
      echo "ERROR: unsupported GVCF suffix: $item" >&2
      exit 1
      ;;
  esac
  if [[ -z "${SEEN[$item]+x}" ]]; then
    GVCF_FILES+=("$item")
    SEEN["$item"]=1
  fi
}

if [[ -n "$MANIFEST" ]]; then
  if [[ ! -f "$MANIFEST" ]]; then
    echo "ERROR: manifest not found: $MANIFEST" >&2
    exit 1
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" ]] && continue
    add_gvcf "$line"
  done < "$MANIFEST"
else
  shopt -s nullglob
  for pattern in "*.g.vcf.gz" "*.gvcf.gz" "*.g.vcf" "*.gvcf"; do
    for file in "$INPUT_DIR"/$pattern; do
      add_gvcf "$file"
    done
  done
  shopt -u nullglob
fi

if [[ "${#GVCF_FILES[@]}" -eq 0 ]]; then
  echo "ERROR: no GVCF files found in $INPUT_DIR" >&2
  echo "Accepted suffixes: .g.vcf.gz, .gvcf.gz, .g.vcf, .gvcf" >&2
  exit 1
fi

{
  echo "========================================"
  echo "CombineGVCFs"
  echo "Time: $(date)"
  echo "Reference: $REFERENCE"
  echo "Input dir: $INPUT_DIR"
  echo "Output: $OUTPUT_GVCF"
  echo "GATK: $("$GATK_BIN" --version 2>&1 | head -1)"
  echo "GVCF count: ${#GVCF_FILES[@]}"
  printf '  %s\n' "${GVCF_FILES[@]}"
  echo "========================================"
} | tee "$LOG_FILE"

for file in "${GVCF_FILES[@]}"; do
  if [[ ! -f "$file.tbi" && ! -f "$file.idx" ]]; then
    echo "Indexing input: $file" | tee -a "$LOG_FILE"
    "$GATK_BIN" IndexFeatureFile -I "$file" 2>&1 | tee -a "$LOG_FILE"
  fi
done

cmd=("$GATK_BIN" CombineGVCFs -R "$REFERENCE")
for file in "${GVCF_FILES[@]}"; do
  cmd+=(-V "$file")
done
cmd+=(-O "$OUTPUT_GVCF" --tmp-dir "$TMP_DIR")

start_time="$(date +%s)"
echo "Running: ${cmd[*]}" | tee -a "$LOG_FILE"
"${cmd[@]}" 2>&1 | tee -a "$LOG_FILE"

if [[ ! -f "$OUTPUT_GVCF" ]]; then
  echo "ERROR: CombineGVCFs finished but output is missing: $OUTPUT_GVCF" >&2
  exit 1
fi

if [[ "$OUTPUT_GVCF" == *.gz ]]; then
  gzip -t "$OUTPUT_GVCF"
fi

if [[ ! -f "$OUTPUT_GVCF.tbi" && ! -f "$OUTPUT_GVCF.idx" ]]; then
  echo "Indexing output: $OUTPUT_GVCF" | tee -a "$LOG_FILE"
  "$GATK_BIN" IndexFeatureFile -I "$OUTPUT_GVCF" 2>&1 | tee -a "$LOG_FILE"
fi

end_time="$(date +%s)"
duration="$((end_time - start_time))"

if [[ "$OUTPUT_GVCF" == *.gz ]]; then
  data_lines="$(gzip -cd "$OUTPUT_GVCF" 2>/dev/null | awk 'BEGIN{n=0} !/^#/{n++} END{print n}')"
else
  data_lines="$(awk 'BEGIN{n=0} !/^#/{n++} END{print n}' "$OUTPUT_GVCF")"
fi
{
  echo "========================================"
  echo "CombineGVCFs completed"
  echo "Duration: $duration seconds"
  echo "Input GVCFs: ${#GVCF_FILES[@]}"
  echo "Output: $OUTPUT_GVCF"
  echo "Data lines: $data_lines"
  echo "Log: $LOG_FILE"
  echo "========================================"
} | tee -a "$LOG_FILE"

if [[ "$data_lines" -eq 0 ]]; then
  echo "ERROR: combined GVCF has no variant/non-variant records" >&2
  exit 1
fi
