#!/bin/sh
set -e

# Windows bind-mount + Linux SQLite = disk I/O error on direct open.
# Copy DB to a named volume, run worker there, sync back periodically and on exit.
SEED_DB="/seed/learning.db"
WORK_DB="/data/learning.db"

mkdir -p /data
if [ -f "$SEED_DB" ]; then
  cp "$SEED_DB" "$WORK_DB"
  [ -f "${SEED_DB}-wal" ] && cp "${SEED_DB}-wal" "${WORK_DB}-wal" || true
  [ -f "${SEED_DB}-shm" ] && cp "${SEED_DB}-shm" "${WORK_DB}-shm" || true
fi

export DB_PATH="$WORK_DB"

sync_back() {
  if [ ! -f "$WORK_DB" ]; then
    return
  fi
  python - <<'PY' || true
import sqlite3
sqlite3.connect("/data/learning.db").execute("PRAGMA wal_checkpoint(TRUNCATE)")
PY
  cp -f "$WORK_DB" "$SEED_DB" 2>/dev/null || true
  [ -f "${WORK_DB}-wal" ] && cp -f "${WORK_DB}-wal" "${SEED_DB}-wal" || true
  [ -f "${WORK_DB}-shm" ] && cp -f "${WORK_DB}-shm" "${SEED_DB}-shm" || true
}

trap sync_back EXIT INT TERM

# 僅在容器退出時 sync 回 host，避免與本機 uvicorn 同時寫 DB 衝突
# 開發中可用: WORKER_SYNC_INTERVAL=30 docker compose ...
if [ -n "${WORKER_SYNC_INTERVAL:-}" ]; then
  (
    while sleep "$WORKER_SYNC_INTERVAL"; do
      sync_back
    done
  ) &
fi

exec python -m arq backend.jobs.arq_settings.WorkerSettings
