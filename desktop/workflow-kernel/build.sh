#!/usr/bin/env bash
# build.sh —— BIOFLOW 内核编译硬化(复制自 tooling/compile/compile_sdk.sh, 白名单 glob 改造)。
# 白名单(明文):app/main.py(uvicorn 入口)、app/config.py(运维可读面)+ __init__.py。
# 用法:compile_sdk.sh <source-root> <output-dir> [python]
set -euo pipefail
umask 022

SOURCE_ROOT="${1:?Usage: $0 <source-root> <output-dir> [python]}"
OUTPUT_DIR="${2:?Usage: $0 <source-root> <output-dir> [python]}"
PYTHON="${3:-python3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VISIBILITY_TOOL="${ELF_VISIBILITY_TOOL:-$SCRIPT_DIR/compile_tools/elf_visibility.py}"

SOURCE_ROOT="$(realpath "$SOURCE_ROOT")"
OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"

[[ -d "$SOURCE_ROOT" ]] || { echo "Kernel source missing: $SOURCE_ROOT" >&2; exit 2; }
[[ -x "$PYTHON" ]] || { echo "Python is not executable: $PYTHON" >&2; exit 2; }
[[ -f "$VISIBILITY_TOOL" ]] || { echo "ELF visibility tool missing: $VISIBILITY_TOOL" >&2; exit 2; }
for command_name in gcc strip rsync; do command -v "$command_name" >/dev/null; done

case "$OUTPUT_DIR" in
  "/"|"$(realpath -m "$HOME")"|"$SOURCE_ROOT")
    echo "Unsafe output directory: $OUTPUT_DIR" >&2; exit 2 ;;
esac
[[ ! -e "$OUTPUT_DIR" ]] || { echo "Output exists; refusing to overwrite: $OUTPUT_DIR" >&2; exit 2; }

echo "[BUILD 1/5] Copying source (excluding tests)"
mkdir -p "$OUTPUT_DIR"
rsync -a --exclude '__pycache__' --exclude 'test_*.py' --exclude 'data' --exclude '.env' \
  --exclude 'build.sh' --exclude 'docker' --exclude 'tests' --exclude 'stage_image_map.json' \
  --exclude 'compile_tools' \
  "$SOURCE_ROOT/" "$OUTPUT_DIR/"

# 白名单明文:main.py + config.py + __init__.py(编译前从发现集排除)
# BIOFLOW 内核白名单(支持 glob, case pattern):
# cli/** 全部明文 = 阶段入口(-m cli.run_X 需明文 .py 可执行)
# gfa_core/mas/config.py = 运维可读面
# gfa_core/mas/llm/** = LLM 直连参数面
# __init__.py 恒明文(包标识)
# 附加白名单: cython 3.2.4 编译失败模块(dev 上游潜伏 bug/cython 限制), 明文保留行为与 dev 一致
#  - conversation_tools.py: load_canonical_workflow_contract 未声明(dev 死路径)
#  - intent_v2.py: re 局部遮蔽(cython 引用前赋值诊断)
#  - knowledge.py: 缺 import os(dev 潜伏 NameError)
#  - core/orchestrator.py: language 未声明(dev 潜伏 bug)
#  - reproducibility.py: 缺 import shutil(dev 潜伏 NameError)
#  - scripts/findFile/downBge-m3.py: 模块名含连字符非法(工具脚本)
#  - mas/workflow_models.py + mas/workflow_contracts.py: _WorkflowModel/_ContractModel 的
#    model_copy 覆盖方法(dev 上游潜伏 bug): cython 编译为 cyfunction 后 pydantic 视为
#    未注解字段报 model-field-missing-annotation
WHITELIST_PY=(
  "cli/**"
  "gfa_core/mas/config.py"
  "gfa_core/mas/llm/**"
  "gfa_core/mas/agents/conversation_tools.py"
  "gfa_core/mas/agents/intent_v2.py"
  "gfa_core/mas/agents/knowledge.py"
  "gfa_core/mas/core/orchestrator.py"
  "gfa_core/mas/workflow_models.py"
  "gfa_core/mas/workflow_contracts.py"
  "gfa_core/reproducibility.py"
  "scripts/findFile/downBge-m3.py"
)
_is_whitelisted() {
  local rel="$1"
  [[ "$(basename "$rel")" == "__init__.py" ]] && return 0
  for w in "${WHITELIST_PY[@]}"; do
    case "$rel" in
      $w) return 0 ;;
    esac
  done
  return 1
}

echo "[BUILD 2/5] Discovering Python modules"
PY_FILES=()
while IFS= read -r -d '' path; do PY_FILES+=("$path"); done \
  < <(find "$OUTPUT_DIR" -type f -name '*.py' -print0)
FILTERED=()
for py_file in "${PY_FILES[@]}"; do
  rel="${py_file#"$OUTPUT_DIR"/}"
  if _is_whitelisted "$rel"; then
    echo "  [whitelist] $rel (明文保留)"
    continue
  fi
  FILTERED+=("$py_file")
done
PY_FILES=("${FILTERED[@]}")
TOTAL="${#PY_FILES[@]}"
(( TOTAL > 0 )) || { echo "No kernel modules selected" >&2; exit 3; }

INCLUDE_PY="$("$PYTHON" -c 'import sysconfig; print(sysconfig.get_config_var("INCLUDEPY") or "")')"
EXT_SUFFIX="$("$PYTHON" -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX") or ".so")')"

echo "[BUILD 3/5] Cython preflight ($TOTAL modules)"
COUNT=0; FAILURES=0
for py_file in "${PY_FILES[@]}"; do
  COUNT=$((COUNT+1))
  directory="$(dirname "$py_file")"
  module="$(basename "$py_file" .py)"
  c_file="$directory/$module.c"
  echo "  [$COUNT/$TOTAL] ${py_file#"$OUTPUT_DIR"/}"
  if ! "$PYTHON" -m cython -3 \
      --directive always_allow_keywords=true,annotation_typing=False \
      --no-docstrings "$py_file" -o "$c_file" 2>/dev/null; then
    FAILURES=$((FAILURES+1))
  fi
done
(( FAILURES == 0 )) || { echo "Kernel Cython failed: $FAILURES" >&2; exit 3; }

echo "[BUILD 4/5] GCC hidden visibility + strip"
COUNT=0
for py_file in "${PY_FILES[@]}"; do
  COUNT=$((COUNT+1))
  directory="$(dirname "$py_file")"
  module="$(basename "$py_file" .py)"
  c_file="$directory/$module.c"
  so_file="$directory/$module$EXT_SUFFIX"
  gcc -shared -pthread -fPIC -O2 -fvisibility=hidden \
    -I"$INCLUDE_PY" -o "$so_file" "$c_file"
  strip --strip-unneeded "$so_file"
  "$PYTHON" -B "$VISIBILITY_TOOL" --hide-non-pyinit --check "$so_file" >/dev/null
  rm -f -- "$c_file" "$py_file"
done

echo "[BUILD 5/5] Final checks"
SO_COUNT="$(find "$OUTPUT_DIR" -type f -name '*.so' | wc -l)"
[[ "$SO_COUNT" -eq "$TOTAL" ]] || { echo "count mismatch: $TOTAL vs $SO_COUNT" >&2; exit 4; }
if find "$OUTPUT_DIR" -type f -name '*.c' -print -quit | grep -q .; then
  echo "Generated C source remains" >&2; exit 4
fi
echo "Kernel compile+harden complete: modules=$SO_COUNT output=$OUTPUT_DIR"
