"""Arq worker entrypoint for curriculum pipeline."""
from __future__ import annotations

import logging
import os

from ..config import DATABASE_URL, DEFAULT_PROVIDER
from ..db.database import close_db, init_db
from ..db.inflight_lock import release
from ..llm.provider_factory import create_provider
from ..memory import curriculum_checkpoint as ckpt
from ..memory import session_memory
from ..orchestrator.curriculum_resume import _null_emit, resume_generating_session
from ..orchestrator.learning_orchestrator import LearningOrchestrator
from .enqueue import inflight_key

_log = logging.getLogger("wl.jobs.curriculum")


async def run_curriculum_job(ctx, session_id: str) -> dict:
    """Run or resume curriculum pipeline for session_id (Arq worker)."""
    await init_db(os.getenv("DATABASE_URL", DATABASE_URL))
    key = inflight_key(session_id)
    try:
        row = await session_memory.get_session(session_id)
        if not row:
            return {"status": "missing", "session_id": session_id}
        if row.get("status") != "generating":
            return {
                "status": "skipped",
                "session_id": session_id,
                "session_status": row.get("status"),
            }

        meta_ckpt = await ckpt.load_checkpoint(session_id)
        meta = (meta_ckpt or {}).get("pipeline_meta") or {}
        provider = meta.get("provider_name") or row.get("provider_name") or DEFAULT_PROVIDER
        model = meta.get("model_name") or row.get("model_name")
        from ..llm.caching_provider import maybe_wrap_curriculum_llm
        llm = maybe_wrap_curriculum_llm(
            create_provider(provider, model=model),
            content_hash=row.get("content_hash"),
        )
        orch = LearningOrchestrator(llm)

        _log.info(
            "run_curriculum_job start  session=%s  try=%s",
            session_id,
            ctx.get("job_try", 1),
        )
        ok = await resume_generating_session(orch, session_id, emit=_null_emit)
        if not ok:
            chunks = await session_memory.get_source_chunks(session_id)
            if not chunks:
                return {"status": "no_chunks", "session_id": session_id}
            _log.warning(
                "run_curriculum_job: no checkpoint, cannot resume  session=%s",
                session_id,
            )
            return {"status": "no_checkpoint", "session_id": session_id}

        row_after = await session_memory.get_session(session_id)
        return {
            "status": "done",
            "session_id": session_id,
            "session_status": row_after.get("status") if row_after else None,
        }
    except Exception as e:
        _log.exception("run_curriculum_job failed  session=%s  err=%s", session_id, e)
        raise
    finally:
        await release(key)
        await close_db()
