#!/bin/sh
set -e

# 直接 bind-mount 本機 ./data → /app/data（CURRICULUM_USE_ARQ=1：uvicorn 只 enqueue，worker 寫 DB）
export DB_PATH="${DB_PATH:-../data/learning.db}"

exec python -m arq backend.jobs.arq_settings.WorkerSettings
