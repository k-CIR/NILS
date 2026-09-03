#!/usr/bin/env bash
#
# scripts/manage-native.sh — Docker-free local-dev counterpart to
# scripts/manage.sh.
#
# scripts/manage.sh (Docker/podman path) is untouched by this file and
# remains the way production / remote (Karolinska) deployments run NILS.
# This script is for local development only: it runs a native Postgres 16
# cluster, the backend (uvicorn) and the frontend (vite) as plain background
# processes on the host, with no containers involved.
#
# The two scripts are independent and do not share state: they use different
# data directories (resource/db-native vs resource/db, resource/db_metadata)
# and, since both use find_free_port(), they will naturally avoid port
# collisions with each other if run at the same time — but if you pin a
# --db-dir/port explicitly, make sure it doesn't collide with a running
# Docker stack.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=./native/common.sh
source "$PROJECT_ROOT/scripts/native/common.sh"

DEFAULT_DB_DIR="$PROJECT_ROOT/resource/db-native"
DB_DIR="$DEFAULT_DB_DIR"
RUN_DIR="$PROJECT_ROOT/resource/run"

BACKEND_DIR="$PROJECT_ROOT/backend"
FRONTEND_DIR="$PROJECT_ROOT/frontend"

BACKEND_VENV="$BACKEND_DIR/.venv"
WORKER_VENV="$BACKEND_DIR/.venv-worker"

usage() {
  cat <<'EOF'
Usage: scripts/manage-native.sh <command> [options]

Commands:
  start            Start native Postgres, backend, and frontend
  stop             Stop all native processes started by this script
  status           Show running/stopped state and ports for each service
  test-frontend    Run `npm run test -- --run` in frontend/ directly
  test-backend     Run `python -m pytest tests` in backend/ directly

Options:
  --clean          Drop/recreate the native Postgres cluster before start (or
                    tear it down on stop). Also cleans Python cache.
  --data PATH      Local DICOM data root (start only, repeatable). Sets
                    DATA_ROOT(S) for the backend and VITE_DATA_ROOT /
                    VITE_USE_REAL_FILES=true for the frontend.
  --db-dir PATH    Override the native Postgres data directory
                    (default: resource/db-native). A single local cluster
                    hosts BOTH the app and metadata databases — there is no
                    separate --metadata-db-dir in native mode.
  --forward        Bind backend/frontend to 0.0.0.0 instead of 127.0.0.1.
                    Unlike Docker, there is no network isolation here, so the
                    HOST FIREWALL matters directly when using this flag.
  --with-worker    Also start the optional body-part-qc-worker (CPU-only on
                    macOS). Disabled by default — the backend already
                    degrades gracefully (HTTP 502/504) when it's unreachable.
  --help, -h       Show this help
EOF
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

command="$1"
shift

CLEAN=false
DATA_PATHS=()
FORWARD_PORTS=false
WITH_WORKER=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean)
      CLEAN=true
      shift
      ;;
    --data)
      DATA_PATHS+=("$(realpath "$2")")
      shift 2
      ;;
    --db-dir)
      DB_DIR="$(python3 - <<'PY'
import os, sys
path = sys.argv[1]
if path.startswith("~"):
    path = os.path.expanduser(path)
print(os.path.abspath(path))
PY
"$2")"
      shift 2
      ;;
    --forward)
      FORWARD_PORTS=true
      shift
      ;;
    --with-worker)
      WITH_WORKER=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

mkdir -p "$RUN_DIR"

PG_DATA_PID="$DB_DIR/postmaster.pid"
BACKEND_PIDFILE="$RUN_DIR/backend.pid"
FRONTEND_PIDFILE="$RUN_DIR/frontend.pid"
WORKER_PIDFILE="$RUN_DIR/worker.pid"
BACKEND_LOG="$RUN_DIR/backend.log"
FRONTEND_LOG="$RUN_DIR/frontend.log"
WORKER_LOG="$RUN_DIR/worker.log"
PG_LOG="$RUN_DIR/postgres.log"

# ---------------------------------------------------------------------------
# Postgres helpers
# ---------------------------------------------------------------------------

_pg_bin() {
  local bindir
  bindir="$(pg_bin_dir)" || {
    echo "ERROR: could not locate PostgreSQL 16 binaries (initdb/pg_ctl)." >&2
    echo "  macOS: brew install postgresql@16" >&2
    echo "  Linux: sudo apt install postgresql-16" >&2
    exit 1
  }
  if [[ -n "$bindir" ]]; then
    echo "$bindir/$1"
  else
    echo "$1"
  fi
}

pg_is_initialized() {
  [[ -f "$DB_DIR/PG_VERSION" ]]
}

pg_is_running() {
  pg_is_initialized || return 1
  "$(_pg_bin pg_ctl)" -D "$DB_DIR" status >/dev/null 2>&1
}

PG_PORT_FILE="$RUN_DIR/postgres.port"

pg_current_port() {
  # We start Postgres with an explicit `-p <port>` override (not written into
  # postgresql.conf), so the actual listening port is recorded by
  # start_postgres() into $PG_PORT_FILE and read back here.
  if [[ -f "$PG_PORT_FILE" ]]; then
    cat "$PG_PORT_FILE"
  else
    echo "5432"
  fi
}

init_postgres() {
  if pg_is_initialized; then
    return 0
  fi
  echo "Initializing native Postgres data directory: $DB_DIR"
  mkdir -p "$DB_DIR"
  "$(_pg_bin initdb)" -D "$DB_DIR" -U postgres --auth=trust >/dev/null
}

start_postgres() {
  local port="$1"
  init_postgres
  if pg_is_running; then
    echo "Postgres already running (data dir: $DB_DIR)"
    return 0
  fi
  local shared_buffers="${NATIVE_PG_SHARED_BUFFERS:-256MB}"
  local work_mem="${NATIVE_PG_WORK_MEM:-16MB}"
  echo "Starting native Postgres on port $port (data dir: $DB_DIR)..."
  # NOTE: shared_buffers/work_mem here are a sane middle-ground for a
  # workstation running everything in one process — not an attempt to
  # replicate the Docker metadata-db's dedicated 4GB/32GB tuning. Override
  # via NATIVE_PG_SHARED_BUFFERS / NATIVE_PG_WORK_MEM if needed.
  "$(_pg_bin pg_ctl)" -D "$DB_DIR" -l "$PG_LOG" -w -o "-p $port -c shared_buffers=$shared_buffers -c work_mem=$work_mem -c max_connections=50 -c statement_timeout=120000 -c idle_in_transaction_session_timeout=300000" start
  echo "$port" > "$PG_PORT_FILE"
}

stop_postgres() {
  if ! pg_is_initialized; then
    return 0
  fi
  if ! pg_is_running; then
    echo "Postgres not running"
    return 0
  fi
  echo "Stopping native Postgres (data dir: $DB_DIR)..."
  "$(_pg_bin pg_ctl)" -D "$DB_DIR" -m fast stop
  rm -f "$PG_PORT_FILE"
}

ensure_roles_and_dbs() {
  local port="$1"
  local psql
  psql="$(_pg_bin psql)"

  # initdb was run with `-U postgres`, so the bootstrap superuser is already
  # named "postgres" — just make sure it has the expected password (idempotent).
  "$psql" -X -p "$port" -U postgres -d postgres -c "ALTER ROLE postgres WITH PASSWORD 'postgres'" >/dev/null 2>&1 || true

  for db in neurotoolkit neurotoolkit_metadata; do
    local has_db
    has_db="$("$psql" -X -A -t -p "$port" -U postgres -d postgres -c "SELECT 1 FROM pg_database WHERE datname='$db'" 2>/dev/null || true)"
    if [[ "$has_db" != "1" ]]; then
      echo "Creating database: $db"
      "$(_pg_bin createdb)" -p "$port" -U postgres "$db"
    fi
  done
}

# ---------------------------------------------------------------------------
# Backend helpers
# ---------------------------------------------------------------------------

_pip_install() {
  # Installs into the given venv, preferring `uv pip` (works even when the
  # venv has no pip, e.g. venvs created by `uv venv`), falling back to the
  # venv's own pip (bootstrapping it with ensurepip if missing).
  local venv="$1"
  shift
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$venv/bin/python" "$@"
    return
  fi
  if [[ ! -x "$venv/bin/pip" ]]; then
    "$venv/bin/python" -m ensurepip --upgrade >/dev/null
  fi
  "$venv/bin/pip" install -q "$@"
}

ensure_backend_venv() {
  if [[ ! -x "$BACKEND_VENV/bin/python" ]]; then
    echo "Creating backend virtualenv: $BACKEND_VENV"
    local py
    py="$(command -v python3.11 || command -v python3.12 || command -v python3)"
    "$py" -m venv "$BACKEND_VENV"
  fi
  echo "Installing backend package (editable, with dev deps)..."
  (cd "$BACKEND_DIR" && _pip_install "$BACKEND_VENV" -e ".[dev]")

  if ! command -v dcm2niix >/dev/null 2>&1; then
    echo "WARNING: dcm2niix not found on PATH. DICOM extraction/classification"
    echo "  needs a build with -DUSE_JPEGLS=ON -DUSE_OPENJPEG=ON."
    echo "  macOS: brew install dcm2niix   |   Linux: build from source (see backend/Dockerfile)"
  fi
}

start_backend() {
  local port="$1" pg_port="$2" bind_host="$3"

  local database_url="postgresql+psycopg://postgres:postgres@localhost:${pg_port}/neurotoolkit"
  local metadata_url="postgresql+psycopg://postgres:postgres@localhost:${pg_port}/neurotoolkit_metadata"

  local data_roots_json=""
  if [[ ${#DATA_PATHS[@]} -gt 0 ]]; then
    data_roots_json="["
    for i in "${!DATA_PATHS[@]}"; do
      [[ $i -gt 0 ]] && data_roots_json+=","
      data_roots_json+="\"${DATA_PATHS[$i]}\""
    done
    data_roots_json+="]"
  fi

  echo "Starting backend on http://${bind_host}:${port} ..."
  (
    cd "$BACKEND_DIR"
    env PYTHONPATH="$BACKEND_DIR/src" \
        DATABASE_URL="$database_url" \
        METADATA_DATABASE_URL="$metadata_url" \
        DATA_ROOTS="$data_roots_json" \
        BODY_PART_WORKER_URL="${BODY_PART_WORKER_URL:-}" \
        "$BACKEND_VENV/bin/python" -m uvicorn api.server:create_app --factory \
          --host "$bind_host" --port "$port" --reload \
          > "$BACKEND_LOG" 2>&1 &
    echo $! > "$BACKEND_PIDFILE"
  )
}

# ---------------------------------------------------------------------------
# Worker helpers (optional, --with-worker)
# ---------------------------------------------------------------------------

ensure_worker_venv() {
  if [[ ! -x "$WORKER_VENV/bin/python" ]]; then
    echo "Creating body-part-qc-worker virtualenv: $WORKER_VENV"
    local py
    py="$(command -v python3.11 || command -v python3.12 || command -v python3)"
    "$py" -m venv "$WORKER_VENV"
  fi
  (cd "$BACKEND_DIR" && _pip_install "$WORKER_VENV" -e ".[dev]")

  if ! "$WORKER_VENV/bin/python" -c "import torch" >/dev/null 2>&1; then
    echo "Installing CPU-only torch + worker deps into $WORKER_VENV (this can take a while)..."
    _pip_install "$WORKER_VENV" --index-url https://download.pytorch.org/whl/cpu torch torchvision
    _pip_install "$WORKER_VENV" "open_clip_torch>=2.24" "transformers>=4.50,<5" "scikit-learn>=1.4" "joblib>=1.3"
  fi
}

start_worker() {
  local port="$1" bind_host="$2"
  local hf_cache="$PROJECT_ROOT/resource/hf_cache"
  mkdir -p "$hf_cache"

  echo "Starting body-part-qc-worker on http://${bind_host}:${port} (CPU-only)..."
  echo "  NOTE: no CUDA on macOS — inference runs on CPU and can be slow."
  echo "  HF models will be downloaded on first use into: $hf_cache"
  (
    cd "$BACKEND_DIR"
    env PYTHONPATH="$BACKEND_DIR/src" \
        HF_HOME="$hf_cache" \
        BODY_PART_DEVICE="${BODY_PART_DEVICE:-cpu}" \
        BODY_PART_BATCH="${BODY_PART_BATCH:-32}" \
        "$WORKER_VENV/bin/python" -m uvicorn qc.body_part.server:app \
          --host "$bind_host" --port "$port" \
          > "$WORKER_LOG" 2>&1 &
    echo $! > "$WORKER_PIDFILE"
  )
}

# ---------------------------------------------------------------------------
# Frontend helpers
# ---------------------------------------------------------------------------

ensure_frontend_deps() {
  if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
    echo "Installing frontend dependencies (npm install)..."
    (cd "$FRONTEND_DIR" && npm install)
  fi
}

start_frontend() {
  local port="$1" backend_port="$2" bind_host="$3"
  local vite_data_root="${DATA_PATHS[0]:-}"
  local use_real_files="false"
  [[ ${#DATA_PATHS[@]} -gt 0 ]] && use_real_files="true"

  echo "Starting frontend on http://${bind_host}:${port} ..."
  (
    cd "$FRONTEND_DIR"
    env VITE_API_URL="http://localhost:${backend_port}" \
        VITE_DATA_ROOT="$vite_data_root" \
        VITE_USE_REAL_FILES="$use_real_files" \
        APP_ACCESS_TOKEN="${APP_ACCESS_TOKEN:-}" \
        npm run dev -- --host "$bind_host" --port "$port" \
          > "$FRONTEND_LOG" 2>&1 &
    echo $! > "$FRONTEND_PIDFILE"
  )
}

# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

cmd_start() {
  if $CLEAN; then
    echo "Stopping any running native services before --clean..."
    stop_pidfile "$FRONTEND_PIDFILE" "frontend" || true
    stop_pidfile "$WORKER_PIDFILE" "worker" || true
    stop_pidfile "$BACKEND_PIDFILE" "backend" || true
    stop_postgres || true
    if [[ -d "$DB_DIR" ]]; then
      echo "Removing native Postgres data directory: $DB_DIR"
      rm -rf "$DB_DIR"
    fi
    cleanup_python_cache
  fi

  local pg_port backend_port frontend_port worker_port
  if pg_is_running; then
    pg_port="$(pg_current_port)"
  else
    pg_port="$(find_free_port 5432)"
  fi
  backend_port="$(find_free_port 8010)"
  frontend_port="$(find_free_port 5173)"

  local bind_host="127.0.0.1"
  if $FORWARD_PORTS; then
    bind_host="0.0.0.0"
    echo "Mode: EXTERNAL (accessible from network/Tailscale)"
    echo "NOTE: native mode has no container network isolation — your HOST"
    echo "      FIREWALL now controls what's actually reachable."
  else
    echo "Mode: LOCALHOST ONLY"
  fi

  echo "Postgres port: $pg_port"
  echo "Backend port:  $backend_port"
  echo "Frontend port: $frontend_port"
  echo "Postgres data directory: $DB_DIR"

  if [[ ${#DATA_PATHS[@]} -gt 0 ]]; then
    echo "Data paths: ${DATA_PATHS[*]}"
  fi

  start_postgres "$pg_port"
  ensure_roles_and_dbs "$pg_port"

  ensure_backend_venv
  start_backend "$backend_port" "$pg_port" "$bind_host"

  if $WITH_WORKER; then
    local worker_port
    worker_port="$(find_free_port 8030)"
    export BODY_PART_WORKER_URL="http://127.0.0.1:${worker_port}"
    ensure_worker_venv
    start_worker "$worker_port" "$bind_host"
    echo "Worker port:   $worker_port"
  fi

  ensure_frontend_deps
  start_frontend "$frontend_port" "$backend_port" "$bind_host"

  echo ""
  echo "✓ Native services started"
  echo "  Frontend: http://localhost:${frontend_port}"
  echo "  Backend:  http://localhost:${backend_port}"
  if $WITH_WORKER; then
    echo "  Worker:   ${BODY_PART_WORKER_URL}"
  fi
  echo "  Logs: $RUN_DIR/{backend,frontend,worker,postgres}.log"
  if ! $FORWARD_PORTS; then
    echo "  Use --forward to expose externally"
  fi
}

cmd_stop() {
  stop_pidfile "$FRONTEND_PIDFILE" "frontend"
  stop_pidfile "$WORKER_PIDFILE" "worker"
  stop_pidfile "$BACKEND_PIDFILE" "backend"
  stop_postgres

  if $CLEAN; then
    if [[ -d "$DB_DIR" ]]; then
      echo "Removing native Postgres data directory: $DB_DIR"
      rm -rf "$DB_DIR"
    fi
    cleanup_python_cache
  fi
}

cmd_status() {
  echo "Postgres  (data dir: $DB_DIR)"
  if pg_is_running; then
    echo "  running — port $(pg_current_port)"
  else
    echo "  stopped"
  fi

  for entry in "backend:$BACKEND_PIDFILE" "frontend:$FRONTEND_PIDFILE" "worker:$WORKER_PIDFILE"; do
    local label="${entry%%:*}"
    local pidfile="${entry#*:}"
    if [[ -f "$pidfile" ]]; then
      local pid
      pid="$(cat "$pidfile" 2>/dev/null || true)"
      if [[ -n "$pid" ]] && is_pid_running "$pid"; then
        echo "$label: running (pid $pid)"
      else
        echo "$label: stopped (stale pidfile)"
      fi
    else
      echo "$label: stopped"
    fi
  done
}

cmd_test_backend() {
  local started_pg_here=false
  local pg_port
  if pg_is_running; then
    pg_port="$(pg_current_port)"
    echo "Using already-running native Postgres on port $pg_port"
  else
    pg_port="$(find_free_port 5432)"
    start_postgres "$pg_port"
    started_pg_here=true
  fi
  ensure_roles_and_dbs "$pg_port"
  ensure_backend_venv

  echo "Running backend tests..."
  local status=0
  (
    cd "$BACKEND_DIR"
    env PYTHONPATH="$BACKEND_DIR/src" \
        DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:${pg_port}/neurotoolkit" \
        METADATA_DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:${pg_port}/neurotoolkit_metadata" \
        "$BACKEND_VENV/bin/python" -m pytest tests
  ) || status=$?

  if $started_pg_here; then
    stop_postgres
  fi
  return $status
}

cmd_test_frontend() {
  (cd "$FRONTEND_DIR" && npm run test -- --run)
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

case "$command" in
  start)
    cmd_start
    ;;
  stop)
    cmd_stop
    ;;
  status)
    cmd_status
    ;;
  test-backend)
    cmd_test_backend
    ;;
  test-frontend)
    cmd_test_frontend
    ;;
  *)
    echo "Unknown command: $command" >&2
    usage
    exit 1
    ;;
esac
