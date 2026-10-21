# Invoked directly over SSH, before loading the Python workflow runner.
# user_runtime.sh is prepended by the native application.
unset PYTHONHOME PYTHONPATH
case "$omaax_repair_action" in
  status)
    [ ! -L "$HOME/omaax" ] && [ ! -L "$omaax_runtime_root" ] || exit 1
    omaax_latest_log=$(ls -t "$omaax_runtime_root"/repair-*.log 2>/dev/null | head -n 1)
    if [ -n "$omaax_latest_log" ] && [ -f "$omaax_latest_log" ] && [ ! -L "$omaax_latest_log" ]; then
      printf 'log=%s\n' "$omaax_latest_log"
      tail -c 24000 "$omaax_latest_log"
    else printf 'No repair log yet\n'; fi
    ;;
  inspect)
    printf 'host=%s\narchitecture=%s\nuid=%s\n' "$(hostname)" "$(uname -m)" "$(id -u)"
    getconf GNU_LIBC_VERSION 2>/dev/null || true
    for tool in bash curl wget tar sha256sum flock; do
      command -v "$tool" || true
    done
    if omaax_find_python; then printf 'python=%s\n' "$OMAAX_PYTHON"
    else printf 'python=unavailable; run server_environment action repair\n'; fi
    if [ -x "$omaax_runtime_root/micromamba" ]; then
      "$omaax_runtime_root/micromamba" --version
    fi
    ;;
  repair)
    umask 077
    [ ! -L "$HOME/omaax" ] && [ ! -L "$omaax_runtime_root" ] || exit 1
    mkdir -p "$omaax_runtime_root" || exit 1
    omaax_repair_log=$(mktemp "$omaax_runtime_root/repair-XXXXXXXX.log") || exit 1
    printf 'log=%s\n' "$omaax_repair_log"
    # Only the application runtime is changed; user Conda and data are untouched.
    omaax_install_runtime analysis >"$omaax_repair_log" 2>&1
    omaax_repair_code=$?
    printf '\ninstaller_exit=%s\n' "$omaax_repair_code" >>"$omaax_repair_log"
    tail -c 24000 "$omaax_repair_log"
    [ "$omaax_repair_code" -eq 0 ] || exit "$omaax_repair_code"
    "$omaax_runtime_env/bin/python3" -c 'import ssl, sqlite3, ctypes, yaml' >>"$omaax_repair_log" 2>&1 || {
      tail -c 24000 "$omaax_repair_log"; exit 1;
    }
    omaax_find_python || exit 1
    printf '\nready=true\npython=%s\n' "$OMAAX_PYTHON"
    ;;
  *) printf 'Unknown environment operation\n' >&2; exit 2 ;;
esac
