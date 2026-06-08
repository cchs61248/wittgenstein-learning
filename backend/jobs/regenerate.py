"""Retry a watchdog-failed session：以既有 source_chunks 全量重生。

清掉不可信的 partial 狀態（checkpoint / inflight lock），重建乾淨 checkpoint
後重新走既有 Arq 入口（或 in-process resume）。
"""
from __future__ import annotations

import asyncio
import logging

from ..config import REDIS_URL, DEFAULT_PROVIDER
from ..db.database import get_db
from ..db.inflight_lock import release
from ..memory import session_memory
from ..memory import curriculum_checkpoint as ckpt
from ..llm.provider_factory import create_provider
from ..orchestrator.curriculum_resume import resume_generating_session_background
from .enqueue import inflight_key, clear_stale_arq_job, enqueue_curriculum_job

_log = logging.getLogger("wl.jobs.curriculum")


class RegenerateError(Exception):
    """無法重生（呼叫端映射為 HTTP 409）。"""


async def regenerate_failed_session(session_id: str, *, use_arq: bool) -> dict:
    """對 watchdog 標 failed 的 session 重新觸發生成。

    status 非 'failed' 或無 source_chunks → raise RegenerateError（呼叫端映射 409）。
    回傳 {"status": "generating", "session_id": ...}。
    """
    row = await session_memory.get_session(session_id)
    if not row:
        raise RegenerateError("session_not_found")
    if row.get("status") != "failed":
        raise RegenerateError(f"not_failed:{row.get('status')}")
    chunks = await session_memory.get_source_chunks(session_id)
    if not chunks:
        raise RegenerateError("no_source_chunks")

    # 盡量保留原 pipeline_meta（含 order_decision）；無 checkpoint 則由 session row 重建
    old = await ckpt.load_checkpoint(session_id)
    if old and old.get("pipeline_meta"):
        meta = dict(old["pipeline_meta"])
    else:
        sm = row.get("same_material")
        meta = {
            "user_id": row["user_id"],
            "target_depth": row.get("target_depth") or "intermediate",
            "question_mode": row.get("question_mode") or "short_answer",
            "provider_name": row.get("provider_name"),
            "model_name": row.get("model_name"),
            "same_material": True if sm is None else bool(sm),
            "order_decision": None,
        }

    # 清掉不可信的 partial 狀態。release 後到 enqueue 之間鎖是空的，但
    # enqueue_curriculum_job（與 in-process resume）都會自行 acquire，故恰好一條重生會跑。
    await ckpt.delete_checkpoint(session_id)
    await release(inflight_key(session_id))

    db = await get_db()
    await db.execute(
        "UPDATE sessions SET status='generating', updated_at=now(), "
        "total_stages=0, current_stage_id=0, stages_json='[]' "
        "WHERE session_id=$1 AND status='failed'",
        session_id,
    )
    # 重建乾淨 checkpoint，讓 Arq resume 入口可運作。
    # 顯式傳 completed_region_ids=[] 強制清空狀態，不依賴「delete 先於 upsert」的隱含順序。
    await ckpt.upsert_checkpoint(
        session_id,
        content_hash=row["content_hash"],
        pipeline_meta=meta,
        completed_region_ids=[],
    )

    if use_arq:
        from arq import create_pool
        from arq.connections import RedisSettings
        pool = await create_pool(RedisSettings.from_dsn(REDIS_URL))
        try:
            await clear_stale_arq_job(pool, session_id)
            job_id = await enqueue_curriculum_job(pool, session_id)
        finally:
            await pool.close()
        _log.info("regenerate: re-enqueued  session=%s  job_id=%s", session_id, job_id)
    else:
        asyncio.create_task(
            resume_generating_session_background(
                session_id,
                lambda provider, model: create_provider(
                    provider or DEFAULT_PROVIDER, model=model,
                ),
            )
        )
        _log.info("regenerate: scheduled in-process resume  session=%s", session_id)
    return {"status": "generating", "session_id": session_id}
