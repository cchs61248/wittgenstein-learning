"""Persist session stub, source chunks, and checkpoint meta before Arq enqueue."""
from __future__ import annotations

from ..memory import curriculum_checkpoint as ckpt
from ..memory import session_memory


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
        },
    )
