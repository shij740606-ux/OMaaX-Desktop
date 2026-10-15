#!/usr/bin/env bash
# Check bbav5 project paths, resource links, and core tool startup.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
    set -a
    . "$ENV_FILE"
    set +a
fi

PROJECT_ROOT="${BBAV5_PROJECT_ROOT:-$PROJECT_DIR}"
# 镜像标准形态: 无 conda 本体; 当前容器 PATH 即环境 bin(默认当前 shell 的 PATH)。
CONDA_BASE="${BBAV5_CONDA_BASE:-}"
DEFAULT_ENV="${BBAV5_DEFAULT_ENV:-}"
GFPY_ENV="${BBAV5_GFPY_ENV:-}"
INTERPROSCAN_BIN="${INTERPROSCAN_BIN:-$PROJECT_ROOT/tools/interproscan-5.78-109.0/interproscan.sh}"

failures=0

ok() {
    printf '[OK] %s\n' "$1"
}

warn() {
    printf '[WARN] %s\n' "$1"
}

fail() {
    printf '[FAIL] %s\n' "$1"
    failures=$((failures + 1))
}

check_path() {
    local label="$1"
    local path="$2"
    if [ -e "$path" ]; then
        ok "$label exists: $path -> $(readlink -f "$path" 2>/dev/null || printf '%s' "$path")"
    else
        fail "$label missing: $path"
    fi
}

check_writable() {
    local label="$1"
    local path="$2"
    if [ -w "$path" ]; then
        ok "$label writable: $path"
    else
        fail "$label not writable: $path"
    fi
}

run_check() {
    local label="$1"
    shift
    if "$@" >/tmp/bbav5_env_check.out 2>/tmp/bbav5_env_check.err; then
        ok "$label"
    else
        fail "$label"
        sed 's/^/  stderr: /' /tmp/bbav5_env_check.err | head -5
    fi
}

printf 'bbav5 environment check\n'
printf 'PROJECT_ROOT=%s\n' "$PROJECT_ROOT"
printf 'CONDA_BASE=%s\n' "$CONDA_BASE"

if [ "$PROJECT_ROOT" = "$PROJECT_DIR" ]; then
    ok "BBAV5_PROJECT_ROOT matches this project"
else
    fail "BBAV5_PROJECT_ROOT differs from this project: expected $PROJECT_DIR"
fi

check_path ".env" "$ENV_FILE"
check_path "input" "$PROJECT_ROOT/input"
check_path "workspace" "$PROJECT_ROOT/workspace"
check_path "data/hmm/Pfam-A.hmm" "$PROJECT_ROOT/data/hmm/Pfam-A.hmm"
check_path "data/kegg_new" "$PROJECT_ROOT/data/kegg_new"
check_path "InterProScan" "$INTERPROSCAN_BIN"

check_writable "input" "$PROJECT_ROOT/input"
check_writable "workspace" "$PROJECT_ROOT/workspace"

# 镜像标准形态: 工具一律 PATH 裸命令(容器内 python -B -m cli.run_xxx 同环境)。
run_check "hmmsearch starts" hmmsearch -h
run_check "hmmscan starts" hmmscan -h
run_check "blastp starts" blastp -version
run_check "Rscript starts" Rscript -e 'cat("OK\n")'

# 镜像标准形态: 当前环境 lib 即 /opt/conda/lib(由当前解释器位置推导)。
lib_dir="$(dirname "$(dirname "$(command -v python || command -v python3)")")/lib"
if [ ! -d "$lib_dir" ]; then
    warn "python lib dir not found: $lib_dir"
else
    broken="$(find "$lib_dir" -maxdepth 1 -xtype l -print 2>/dev/null | head -20)"
    if [ -n "$broken" ]; then
        warn "environment has broken top-level library symlinks:"
        printf '%s\n' "$broken" | sed 's/^/  /'
    else
        ok "top-level library symlinks resolve"
    fi
fi

if [ "$failures" -eq 0 ]; then
    ok "environment check completed"
else
    fail "environment check completed with $failures failure(s)"
fi

exit "$failures"
