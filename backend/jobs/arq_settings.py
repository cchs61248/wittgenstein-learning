"""Arq worker settings for curriculum background jobs."""
from __future__ import annotations

import logging
import os

from arq.connections import RedisSettings

from ..config import ARQ_JOB_TIMEOUT_S, ARQ_MAX_JOBS, DATABASE_URL, REDIS_URL
from ..db.database import close_db, init_db
from ..utils.logger import setup_logging
from ..db.inflight_lock import cleanup_dead_worker_locks
from ..memory import curriculum_checkpoint as ckpt
from .curriculum_job import run_curriculum_job
from .enqueue import clear_stale_arq_job, enqueue_curriculum_job

_log = logging.getLogger("wl.jobs.curriculum")


async def startup(ctx) -> None:
    setup_logging()
    await init_db(os.getenv("DATABASE_URL", DATABASE_URL))
    try:
        n = await cleanup_dead_worker_locks()
        if n:
            _log.info("worker startup: cleaned %d dead inflight locks", n)
    except Exception as e:
        _log.warning("worker startup: dead lock cleanup failed: %s", e)
    for sid in await ckpt.list_resumable_sessions():
        _log.info("worker startup: re-enqueue resumable session=%s", sid)
        n = await clear_stale_arq_job(ctx["redis"], sid)
        if n:
            _log.info("worker startup: cleared %d stale Arq key(s)  session=%s", n, sid)
        await enqueue_curriculum_job(ctx["redis"], sid)


async def shutdown(ctx) -> None:
    await close_db()


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(REDIS_URL)
    functions = [run_curriculum_job]
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = ARQ_MAX_JOBS
    job_timeout = ARQ_JOB_TIMEOUT_S
    health_check_interval = 30
    allow_abort_jobs = True
    max_tries = 3
    retry_jobs = True
