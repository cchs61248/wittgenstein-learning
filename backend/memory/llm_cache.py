"""SQLite-backed LLM result cache for curriculum pipeline."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from ..db.database import get_db
from ..llm.base_provider import LLMResponse


def _response_to_dict(resp: LLMResponse) -> dict:
    return {
        "content": resp.content,
        "input_tokens": resp.input_tokens,
        "output_tokens": resp.output_tokens,
        "model": resp.model,
        "finish_reason": resp.finish_reason,
    }


def _dict_to_response(data: dict) -> LLMResponse:
    return LLMResponse(
        content=data["content"],
        input_tokens=int(data.get("input_tokens") or 0),
        output_tokens=int(data.get("output_tokens") or 0),
        model=data.get("model") or "",
        finish_reason=data.get("finish_reason") or "stop",
    )


async def get(cache_key: str) -> LLMResponse | None:
    db = await get_db()
    async with db.execute(
        "SELECT result_json FROM llm_result_cache WHERE cache_key = ?",
        (cache_key,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    try:
        return _dict_to_response(json.loads(row[0]))
    except Exception:
        return None


async def get_row(cache_key: str) -> dict[str, Any] | None:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM llm_result_cache WHERE cache_key = ?",
        (cache_key,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    return dict(row)


async def put(
    cache_key: str,
    *,
    agent_name: str,
    model_name: str,
    prompt_version: str,
    result: LLMResponse,
    content_hash: str | None = None,
    region_id: str | None = None,
    scope: str = "curriculum",
) -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO llm_result_cache
           (cache_key, scope, content_hash, agent_name, region_id, prompt_version,
            model_name, result_json, input_tokens, output_tokens)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(cache_key) DO UPDATE SET
            result_json = excluded.result_json,
            input_tokens = excluded.input_tokens,
            output_tokens = excluded.output_tokens,
            last_hit_at = CURRENT_TIMESTAMP""",
        (
            cache_key,
            scope,
            content_hash,
            agent_name,
            region_id,
            prompt_version,
            model_name,
            json.dumps(_response_to_dict(result), ensure_ascii=False),
            result.input_tokens,
            result.output_tokens,
        ),
    )
    await db.commit()


async def record_hit(cache_key: str) -> None:
    db = await get_db()
    await db.execute(
        """UPDATE llm_result_cache
           SET hit_count = hit_count + 1, last_hit_at = CURRENT_TIMESTAMP
           WHERE cache_key = ?""",
        (cache_key,),
    )
    await db.commit()


async def stats_by_content_hash(content_hash: str) -> dict[str, Any]:
    db = await get_db()
    async with db.execute(
        """SELECT COUNT(*) AS entries,
                  COALESCE(SUM(hit_count), 0) AS total_hits
           FROM llm_result_cache WHERE content_hash = ?""",
        (content_hash,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return {"entries": 0, "total_hits": 0}
    return {"entries": row[0], "total_hits": row[1]}


async def evict_older_than(days: int) -> int:
    if days <= 0:
        return 0
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat(sep=" ")
    db = await get_db()
    cur = await db.execute(
        "DELETE FROM llm_result_cache WHERE created_at < ?",
        (cutoff,),
    )
    await db.commit()
    return cur.rowcount
