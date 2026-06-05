"""asyncpg-backed LLM result cache for curriculum pipeline."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import asyncpg

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
    row = await db.fetchrow(
        "SELECT result_json FROM llm_result_cache WHERE cache_key = $1",
        cache_key,
    )
    if not row:
        return None
    try:
        return _dict_to_response(json.loads(row[0]))
    except Exception:
        return None


async def get_row(cache_key: str) -> dict[str, Any] | None:
    db = await get_db()
    row = await db.fetchrow(
        "SELECT * FROM llm_result_cache WHERE cache_key = $1",
        cache_key,
    )
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
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
           ON CONFLICT (cache_key) DO UPDATE SET
            result_json = EXCLUDED.result_json,
            input_tokens = EXCLUDED.input_tokens,
            output_tokens = EXCLUDED.output_tokens,
            last_hit_at = CURRENT_TIMESTAMP""",
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
    )


async def record_hit(cache_key: str) -> None:
    db = await get_db()
    tag = await db.execute(
        """UPDATE llm_result_cache
           SET hit_count = hit_count + 1, last_hit_at = CURRENT_TIMESTAMP
           WHERE cache_key = $1""",
        cache_key,
    )
    # tag is e.g. "UPDATE 1"; return value unused by callers but parse for completeness
    return int(tag.split()[-1])


async def stats_by_content_hash(content_hash: str) -> dict[str, Any]:
    db = await get_db()
    row = await db.fetchrow(
        """SELECT COUNT(*) AS entries,
                  COALESCE(SUM(hit_count), 0) AS total_hits
           FROM llm_result_cache WHERE content_hash = $1""",
        content_hash,
    )
    if not row:
        return {"entries": 0, "total_hits": 0}
    return {"entries": row[0], "total_hits": row[1]}


async def evict_older_than(days: int) -> int:
    if days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=days)
    db = await get_db()
    tag = await db.execute(
        "DELETE FROM llm_result_cache WHERE created_at < $1",
        cutoff,
    )
    return int(tag.split()[-1])
