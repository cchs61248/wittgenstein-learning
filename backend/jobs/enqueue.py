"""Enqueue curriculum pipeline jobs to Arq."""
from __future__ import annotations

import logging

from ..db.inflight_lock import acquire, release

_log = logging.getLogger("wl.jobs.curriculum")

INFLIGHT_KIND = "start_session"


def inflight_key(session_id: str) -> str:
    return f"{session_id}:start"


async def enqueue_curriculum_job(redis, session_id: str) -> str | None:
    """
    Enqueue run_curriculum_job for session_id.
    Returns Arq job id, or None if inflight lock already held (job running/queued).
    """
    key = inflight_key(session_id)
    if not await acquire(key, session_id=session_id, kind=INFLIGHT_KIND):
        _log.info("enqueue skipped: inflight lock held  session=%s", session_id)
        return None
    try:
        job = await redis.enqueue_job(
            "run_curriculum_job",
            session_id,
            _job_id=f"curriculum:{session_id}",
        )
        if job is None:
            await release(key)
            _log.info("enqueue skipped: job already queued  session=%s", session_id)
            return None
        _log.info("curriculum job enqueued  session=%s  job_id=%s", session_id, job.job_id)
        return job.job_id
    except Exception:
        await release(key)
        raise
