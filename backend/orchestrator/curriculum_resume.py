"""Resume interrupted curriculum pipeline from DB checkpoint."""
from __future__ import annotations

import logging

from ..memory import curriculum_checkpoint as ckpt
from ..memory import session_memory
from ..db.inflight_lock import acquire, release
from .curriculum_pipeline_v2 import run_start_session_v2

_log = logging.getLogger("wl.orchestrator.v2")


async def _null_emit(msg: dict) -> None:
    _log.debug("resume emit (discarded)  type=%s", msg.get("type"))


async def resume_generating_session(
    orch,
    session_id: str,
    *,
    emit=None,
) -> bool:
    """Continue a generating session from checkpoint. Returns False if not resumable."""
    emit = emit or _null_emit
    row = await session_memory.get_session(session_id)
    checkpoint = await ckpt.load_checkpoint(session_id)
    if not row or row.get("status") != "generating" or not checkpoint:
        return False
    chunks = await session_memory.get_source_chunks(session_id)
    if not chunks:
        _log.warning("resume skipped: no source_chunks  session=%s", session_id)
        return False

    meta = checkpoint.get("pipeline_meta") or {}
    user_id = meta.get("user_id") or row["user_id"]
    target_depth = (
        meta.get("target_depth") or row.get("target_depth") or "intermediate"
    )
    question_mode = (
        meta.get("question_mode") or row.get("question_mode") or "short_answer"
    )
    provider_name = meta.get("provider_name") or row.get("provider_name")
    model_name = meta.get("model_name") or row.get("model_name")

    _log.info(
        "resume_generating_session  session=%s  skip_regions=%d",
        session_id,
        len(checkpoint.get("completed_region_ids") or []),
    )

    await run_start_session_v2(
        orch,
        session_id=session_id,
        user_id=user_id,
        source_chunks=chunks,
        target_depth=target_depth,
        question_mode=question_mode,
        provider_name=provider_name,
        model_name=model_name,
        emit=emit,
    )
    return True


async def resume_generating_session_background(
    session_id: str,
    llm_factory,
) -> None:
    """Startup / ops background resume with inflight dedup."""
    key = f"{session_id}:start"
    if not await acquire(key, session_id=session_id, kind="start_session"):
        _log.info("resume skipped: inflight lock held  session=%s", session_id)
        return
    try:
        row = await session_memory.get_session(session_id)
        if not row or row.get("status") != "generating":
            return
        meta_ckpt = await ckpt.load_checkpoint(session_id)
        if not meta_ckpt:
            return
        meta = meta_ckpt.get("pipeline_meta") or {}
        provider = meta.get("provider_name") or row.get("provider_name")
        model = meta.get("model_name") or row.get("model_name")
        llm = llm_factory(provider, model)
        from .learning_orchestrator import LearningOrchestrator

        orch = LearningOrchestrator(llm)
        await resume_generating_session(orch, session_id)
    except Exception as e:
        _log.warning("background resume failed  session=%s  err=%s", session_id, e)
    finally:
        await release(key)
