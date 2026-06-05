#!/bin/sh
set -e

# PostgreSQL：worker 由 arq_settings.startup 讀 DATABASE_URL（compose 已設定）。
# 未經 compose 直接跑時給一個預設值。
export DATABASE_URL="${DATABASE_URL:-postgresql://wl:wl@postgres:5432/wl}"

exec python -m arq backend.jobs.arq_settings.WorkerSettings
