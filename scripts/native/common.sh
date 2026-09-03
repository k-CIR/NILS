#!/usr/bin/env bash
#
# scripts/native/common.sh — shared helper functions for the native (Docker-free)
# orchestration path. Source this file from manage-native.sh, not executed directly.
#
# See scripts/manage-native.sh for the CLI entry point.

set -euo pipefail

# ---------------------------------------------------------------------------
# find_free_port  <start_port>
#   Finds the first free TCP port on 127.0.0.1 starting from <start_port>.
#   Identical implementation to the one in scripts/manage.sh.
# ---------------------------------------------------------------------------
find_free_port() {
  local start_port="$1"
  python3 - <<PY
import socket
port = int($start_port)
while True:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        result = sock.connect_ex(("127.0.0.1", port))
        if result != 0:
            print(port)
            break
    port += 1
PY
}

# ---------------------------------------------------------------------------
# pg_bin_dir
#   Echoes the directory containing Postgres 16 binaries, or empty string if
#   the binaries are expected to be on PATH already (Linux apt case).
#
#   Resolution order:
#     1. brew --prefix postgresql@16 (macOS Homebrew keg-only)
#     2. /opt/homebrew/opt/postgresql@16/bin  (Apple Silicon)
#     3. /usr/local/opt/postgresql@16/bin     (Intel Mac)
#     4. relies on PATH (Linux / Windows)
# ---------------------------------------------------------------------------
pg_bin_dir() {
  # macOS Homebrew — postgresql@16 is keg-only, so its binaries are NOT on PATH
  if command -v brew &>/dev/null; then
    local brew_prefix
    brew_prefix="$(brew --prefix postgresql@16 2>/dev/null)" || true
    if [[ -n "$brew_prefix" && -d "${brew_prefix}/bin" ]]; then
      echo "${brew_prefix}/bin"
      return 0
    fi
  fi

  # Fallback paths for Homebrew on different architectures
  for candidate in \
    "/opt/homebrew/opt/postgresql@16/bin" \
    "/usr/local/opt/postgresql@16/bin"; do
    if [[ -x "${candidate}/initdb" ]]; then
      echo "$candidate"
      return 0
    fi
  done

  # Linux / other — assume binaries are on PATH
  if command -v initdb &>/dev/null && command -v pg_ctl &>/dev/null; then
    echo ""
    return 0
  fi

  # Nothing found — return empty; caller should handle the error
  echo "" >&2
  return 1
}

# ---------------------------------------------------------------------------
# is_pid_running  <pid>
#   Returns 0 if the process identified by <pid> exists, 1 otherwise.
# ---------------------------------------------------------------------------
is_pid_running() {
  kill -0 "$1" 2>/dev/null
}

# ---------------------------------------------------------------------------
# stop_pidfile  <pidfile>  <label>
#   If <pidfile> exists and the process it names is alive, send SIGTERM,
#   wait up to ~10 seconds (polling every 0.5 s), then SIGKILL if still
#   alive. Always removes the pidfile afterwards. Prints a one-line status
#   using <label>.
# ---------------------------------------------------------------------------
stop_pidfile() {
  local pidfile="$1"
  local label="$2"

  if [[ ! -f "$pidfile" ]]; then
    return 0
  fi

  local pid
  pid="$(cat "$pidfile" 2>/dev/null)" || {
    rm -f "$pidfile"
    return 0
  }

  if ! is_pid_running "$pid"; then
    echo "  $label (pid $pid) not running — cleaning up stale pidfile"
    rm -f "$pidfile"
    return 0
  fi

  echo "  Stopping $label (pid $pid)…"
  kill "$pid" 2>/dev/null || true

  local waited=0
  while [[ $waited -lt 20 ]]; do
    if ! is_pid_running "$pid"; then
      echo "  $label stopped"
      rm -f "$pidfile"
      return 0
    fi
    sleep 0.5
    ((waited++)) || true
  done

  # Timed out — force kill
  echo "  $label did not stop gracefully — sending SIGKILL"
  kill -9 "$pid" 2>/dev/null || true
  sleep 0.5
  rm -f "$pidfile"
  echo "  $label killed"
}

# ---------------------------------------------------------------------------
# cleanup_python_cache
#   Removes __pycache__ directories, *.pyc files, build dirs, and packaging
#   artifacts from backend/src and backend/.  Mirrors the function of the
#   same name in scripts/manage.sh.
# ---------------------------------------------------------------------------
cleanup_python_cache() {
  local backend_src="${PROJECT_ROOT:-$(pwd)}/backend/src"

  if [[ ! -d "$backend_src" ]]; then
    return 0
  fi

  echo "Cleaning Python cache from backend/src..."

  # Remove __pycache__ directories
  local pycache_count=0
  while IFS= read -r -d '' dir; do
    rm -rf "$dir" 2>/dev/null && ((pycache_count++)) || true
  done < <(find "$backend_src" -type d -name "__pycache__" -print0 2>/dev/null)

  # Remove .pyc files
  local pyc_count=0
  while IFS= read -r -d '' file; do
    rm -f "$file" 2>/dev/null && ((pyc_count++)) || true
  done < <(find "$backend_src" -type f -name "*.pyc" -print0 2>/dev/null)

  # Remove build directories
  local build_count=0
  for pattern in "build" "*.egg-info" "*.dist-info"; do
    while IFS= read -r -d '' dir; do
      rm -rf "$dir" 2>/dev/null && ((build_count++)) || true
    done < <(find "$backend_src" -type d -name "$pattern" -print0 2>/dev/null)
  done

  # Also clean the backend root for any build artifacts
  local backend_root="${PROJECT_ROOT:-$(pwd)}/backend"
  for dir in "$backend_root/build" "$backend_root"/*.egg-info; do
    if [[ -d "$dir" ]]; then
      rm -rf "$dir" 2>/dev/null && ((build_count++)) || true
    fi
  done

  echo "  Removed: $pycache_count __pycache__ dirs, $pyc_count .pyc files, $build_count build dirs"
}