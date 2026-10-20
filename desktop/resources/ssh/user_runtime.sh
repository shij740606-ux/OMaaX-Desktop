# OMaaX user-owned runtime. No system package manager or privilege elevation.
omaax_runtime_root="$HOME/omaax/.runtime"
omaax_runtime_env="$omaax_runtime_root/env"
omaax_find_python() {
  OMAAX_PYTHON=''
  for omaax_candidate in "$omaax_runtime_env/bin/python3" "${CONDA_PREFIX:-/nonexistent}/bin/python3" "${CONDA_EXE%/conda}/python3" "$(command -v python3 2>/dev/null)" "$HOME/miniforge3/bin/python3" "$HOME/miniconda3/bin/python3" "$HOME/anaconda3/bin/python3" "$HOME/../../apps/Anaconda3/bin/python3" /opt/conda/bin/python3 /opt/anaconda3/bin/python3; do
    if [ -n "$omaax_candidate" ] && [ -x "$omaax_candidate" ] && "$omaax_candidate" -c 'import sys;sys.exit(sys.version_info < (3,9))' >/dev/null 2>&1; then
      OMAAX_PYTHON="$omaax_candidate"; export OMAAX_PYTHON; return 0
    fi
  done
  return 1
}
omaax_install_runtime() (
  set -eu
  umask 077
  [ "$(id -u)" != 0 ] || { echo '请使用普通服务器账号准备环境' >&2; exit 1; }
  [ ! -L "$HOME/omaax" ] && [ ! -L "$omaax_runtime_root" ] && [ ! -L "$omaax_runtime_env" ] || { echo '环境目录不能是符号链接' >&2; exit 1; }
  mkdir -p "$omaax_runtime_root"
  # Kernel-owned lock is released even if an interrupted SSH connection kills the installer.
  command -v flock >/dev/null || { echo '服务器缺少环境安装锁工具 flock' >&2; exit 1; }
  [ ! -L "$omaax_runtime_root/install.lock" ] || exit 1
  exec 9>"$omaax_runtime_root/install.lock"
  flock -w 780 9 || { echo '另一个环境配置仍在进行，请稍后重试' >&2; exit 1; }
  if [ -e "$omaax_runtime_env" ] && [ ! -f "$omaax_runtime_env/conda-meta/history" ]; then
    omaax_incomplete="$omaax_runtime_root/env-incomplete-$(date +%s)-$$"
    [ ! -e "$omaax_incomplete" ] || exit 1
    mv "$omaax_runtime_env" "$omaax_incomplete"
  fi
  case "$(uname -m)" in
    x86_64) omaax_arch=linux-64; omaax_sha=e7274528ceb9c20d048a428d6c22d7e02e268f8ffb762c4c365422347c8b8ba2;;
    aarch64|arm64) omaax_arch=linux-aarch64; omaax_sha=02ed4fe982c8ecbf9c5b1759c07ee29063994842b65fdaab439db77b9b00510a;;
    *) echo '当前服务器架构尚不支持自动配置' >&2; exit 1;;
  esac
  unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
  omaax_archive="$omaax_runtime_root/micromamba-2.3.3-$omaax_arch.tar.bz2"
  omaax_url="https://conda.anaconda.org/conda-forge/$omaax_arch/micromamba-2.3.3-0.tar.bz2"
  command -v sha256sum >/dev/null && command -v tar >/dev/null || { echo '服务器缺少下载校验或解压工具' >&2; exit 1; }
  if ! printf '%s  %s\n' "$omaax_sha" "$omaax_archive" | sha256sum -c - >/dev/null 2>&1; then
    [ ! -L "$omaax_archive" ] && [ ! -L "$omaax_archive.part" ] || exit 1
    if command -v curl >/dev/null 2>&1; then curl --fail --location --proto '=https' --connect-timeout 20 --max-time 180 --max-filesize 33554432 --output "$omaax_archive.part" "$omaax_url" >&2
    elif command -v wget >/dev/null 2>&1; then wget --https-only --timeout=30 --tries=2 -O "$omaax_archive.part" "$omaax_url" >&2
    else echo '服务器需要 curl 或 wget 才能下载环境' >&2; exit 1; fi
    printf '%s  %s\n' "$omaax_sha" "$omaax_archive.part" | sha256sum -c - >&2
    mv "$omaax_archive.part" "$omaax_archive"
  fi
  [ ! -L "$omaax_runtime_root/micromamba" ] || exit 1
  tar -xjOf "$omaax_archive" bin/micromamba > "$omaax_runtime_root/micromamba"
  chmod 700 "$omaax_runtime_root/micromamba"
  export MAMBA_ROOT_PREFIX="$omaax_runtime_root/mamba"
  omaax_operation=create
  [ ! -f "$omaax_runtime_env/conda-meta/history" ] || omaax_operation=install
  if [ "${1:-python}" = analysis ]; then
    "$omaax_runtime_root/micromamba" --no-rc "$omaax_operation" --yes --prefix "$omaax_runtime_env" --override-channels --channel-priority strict -c conda-forge 'python=3.12' micromamba pyyaml >&2
  else
    "$omaax_runtime_root/micromamba" --no-rc "$omaax_operation" --yes --prefix "$omaax_runtime_env" --override-channels --channel-priority strict -c conda-forge 'python=3.12' >&2
  fi
)
omaax_prepare_python() {
  omaax_find_python && return 0
  omaax_install_runtime python || return 1
  omaax_find_python
}
