#!/usr/bin/env bash
# Provision a disposable, real PostgreSQL/PostGIS/pgvector backend for XCUITest.
# Runs on a macOS Actions runner so the iOS Simulator can reach API at 127.0.0.1.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

: "${RUNNER_TEMP:=${TMPDIR:-/tmp}}"
LOG_DIR="$RUNNER_TEMP/globalbiztravel-ui"
mkdir -p "$LOG_DIR"

PG_BIN="$(brew --prefix postgresql@18)/bin"
export PATH="$PG_BIN:$PATH"
PSQL="$PG_BIN/psql"
CREATEDB="$PG_BIN/createdb"
PG_ISREADY="$PG_BIN/pg_isready"

echo "== PostgreSQL service =="
brew services start postgresql@18
ready=0
for attempt in $(seq 1 90); do
  if "$PG_ISREADY" -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [ "$ready" -ne 1 ]; then
  echo "PostgreSQL did not become ready on 127.0.0.1:5432" >&2
  brew services list >&2 || true
  exit 1
fi

# The Homebrew service's local socket trusts the runner account; create the same
# restricted application/migration roles used by the Docker development stack.
"$PSQL" --dbname postgres --set ON_ERROR_STOP=1 <<'SQL'
CREATE ROLE gbt_owner LOGIN PASSWORD 'gbt_owner_pw'
  NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE gbt_app LOGIN PASSWORD 'gbt_app_pw'
  NOSUPERUSER NOCREATEDB NOCREATEROLE;
SQL

"$CREATEDB" --owner=gbt_owner gbt_os
"$PSQL" --dbname gbt_os --set ON_ERROR_STOP=1 <<'SQL'
ALTER SCHEMA public OWNER TO gbt_owner;
GRANT USAGE ON SCHEMA public TO gbt_app;
GRANT ALL ON SCHEMA public TO gbt_owner;
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
ALTER DEFAULT PRIVILEGES FOR ROLE gbt_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gbt_app;
ALTER DEFAULT PRIVILEGES FOR ROLE gbt_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO gbt_app;
SQL

"$PSQL" --dbname gbt_os --set ON_ERROR_STOP=1 --tuples-only --no-align <<'SQL'
SELECT current_setting('server_version');
SELECT postgis_full_version();
SELECT extversion FROM pg_extension WHERE extname = 'vector';
SQL

echo "== Python dependencies and schema =="
export PYTHONPATH="$ROOT/services/api:$ROOT/packages/planning-engine${PYTHONPATH:+:$PYTHONPATH}"
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
make migrate
make seed

echo "== Start API =="
API_LOG="$LOG_DIR/api.log"
nohup python -m uvicorn app.main:app --app-dir services/api \
  --host 127.0.0.1 --port 8080 >"$API_LOG" 2>&1 </dev/null &
API_PID=$!
echo "$API_PID" > "$RUNNER_TEMP/globalbiztravel-api.pid"

api_ready=0
for attempt in $(seq 1 90); do
  if curl --silent --show-error --fail http://127.0.0.1:8080/v1/health \
      --output "$LOG_DIR/health.json" 2>/dev/null; then
    if python -c 'import json,sys; assert json.load(open(sys.argv[1]))["database"] == "up"' \
        "$LOG_DIR/health.json" 2>/dev/null; then
      api_ready=1
      break
    fi
  fi
  if ! kill -0 "$API_PID" 2>/dev/null; then
    break
  fi
  sleep 2
done
if [ "$api_ready" -ne 1 ]; then
  echo "API failed readiness check; last log lines:" >&2
  tail -100 "$API_LOG" >&2 || true
  exit 1
fi
echo "API healthy with PostgreSQL-backed /v1/health"

echo "== Start persistent Worker =="
WORKER_LOG="$LOG_DIR/worker.log"
nohup python -m app.workers.main --poll-seconds 1 --verbose \
  >"$WORKER_LOG" 2>&1 </dev/null &
WORKER_PID=$!
echo "$WORKER_PID" > "$RUNNER_TEMP/globalbiztravel-worker.pid"

worker_ready=0
for attempt in $(seq 1 30); do
  if grep -q "启动：轮询" "$WORKER_LOG" 2>/dev/null; then
    worker_ready=1
    break
  fi
  if ! kill -0 "$WORKER_PID" 2>/dev/null; then
    break
  fi
  sleep 1
done
sleep 2
if [ "$worker_ready" -ne 1 ] || ! kill -0 "$WORKER_PID" 2>/dev/null; then
  echo "Worker failed to stay alive; last log lines:" >&2
  tail -100 "$WORKER_LOG" >&2 || true
  exit 1
fi
echo "API and persistent Worker are running; ready for live E2E and XCUITest."
