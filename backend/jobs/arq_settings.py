"""Arq worker settings for curriculum background jobs."""
from __future__ import annotations

import asyncio
import logging
import sqlite3

from arq.connections import RedisSettings

from ..config import ARQ_JOB_TIMEOUT_S, ARQ_MAX_JOBS, DB_PATH, REDIS_URL
from ..db.database import close_db, init_db
from ..utils.logger import setup_logging
from ..db.inflight_lock import cleanup_dead_worker_locks
from ..memory import curriculum_checkpoint as ckpt
from .curriculum_job import run_curriculum_job
from .enqueue import clear_stale_arq_job, enqueue_curriculum_job

_log = logging.getLogger("wl.jobs.curriculum")


async def _init_db_with_retry(db_path: str, *, attempts: int = 3) -> None:
    """Handle transient SQLite disk I/O errors on bind mounts."""
    last_err: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            await init_db(db_path)
            return
        except sqlite3.OperationalError as e:
            last_err = e
            msg = str(e).lower()
            if "disk i/o error" not in msg and "database is locked" not in msg:
                raise
            _log.warning("init_db transient error (attempt %d/%d): %s", i, attempts, e)
            await close_db()
            if i < attempts:
                await asyncio.sleep(0.5 * i)
    assert last_err is not None
    raise last_err


async def startup(ctx) -> None:
    setup_logging()
    await _init_db_with_retry(DB_PATH)
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
