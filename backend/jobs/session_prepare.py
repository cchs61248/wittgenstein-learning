"""Persist session stub, source chunks, and checkpoint meta before Arq enqueue."""
from __future__ import annotations

import logging

from ..memory import curriculum_checkpoint as ckpt
from ..memory import session_memory

_log = logging.getLogger("wl.jobs.curriculum")


async def prepare_curriculum_session(
    *,
    session_id: str,
    user_id: str,
    source_chunks: list[dict],
    content_hash: str,
    target_depth: str,
    question_mode: str,
    provider_name: str | None,
    model_name: str | None,
    source_file_ids: list[str] | None,
    sources_json: list[dict],
    same_material: bool,
    order_decision: dict | None = None,
) -> None:
    await session_memory.create_generating_stub(
        session_id,
        user_id,
        content_hash,
        source_file_ids=source_file_ids or [],
        sources_json=sources_json,
        provider_name=provider_name,
        model_name=model_name,
        question_mode=question_mode,
        target_depth=target_depth,
        same_material=same_material,
    )
    await session_memory.insert_source_chunks(session_id, source_chunks)
    if source_file_ids:
        await session_memory.purge_source_uploads(session_id, source_file_ids)
    await ckpt.upsert_checkpoint(
        session_id,
        content_hash=content_hash,
        pipeline_meta={
            "user_id": user_id,
            "target_depth": target_depth,
            "question_mode": question_mode,
            "provider_name": provider_name,
            "model_name": model_name,
            "same_material": same_material,
            "order_decision": order_decision,
        },
    )
    _log.info(
        "prepare_curriculum_session  session=%s  chunks=%d  content_hash=%s  "
        "purged_uploads=%d  same_material=%s",
        session_id,
        len(source_chunks),
        content_hash[:12],
        len(source_file_ids or []),
        same_material,
    )
