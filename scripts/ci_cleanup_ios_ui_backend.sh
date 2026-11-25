#!/usr/bin/env bash
# Stop only processes started by ci_ios_ui_backend.sh and its disposable PG service.
set -u
: "${RUNNER_TEMP:=${TMPDIR:-/tmp}}"

for name in api worker; do
  pid_file="$RUNNER_TEMP/globalbiztravel-$name.pid"
  [ -f "$pid_file" ] || continue
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  case "$pid" in
    ''|*[!0-9]*) continue ;;
  esac
  kill -TERM "$pid" 2>/dev/null || true
  for _ in 1 2 3 4 5; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
  done
  kill -KILL "$pid" 2>/dev/null || true
  rm -f "$pid_file"
done

brew services stop postgresql@18 >/dev/null 2>&1 || true
