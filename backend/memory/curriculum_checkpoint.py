"""Curriculum pipeline checkpoint CRUD — region-level resume after restart."""
from __future__ import annotations

import json
from typing import Any

from ..db.database import get_db

_PIPELINE_VERSION = "v2"


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _row_to_dict(row) -> dict[str, Any]:
    return {
        "session_id": row["session_id"],
        "content_hash": row["content_hash"],
        "pipeline_version": row["pipeline_version"],
        "pipeline_meta": _loads(row["pipeline_meta_json"], {}),
        "required_outline": _loads(row["required_outline_json"], None),
        "regions": _loads(row["regions_json"], []),
        "completed_region_ids": _loads(row["completed_region_ids_json"], []),
        "all_candidates": _loads(row["all_candidates_json"], []),
        "summary_parts": _loads(row["summary_parts_json"], []),
        "meter_breakdown": _loads(row["meter_json"], {}).get("breakdown", {}),
        "last_region_id": row["last_region_id"],
    }


async def load_checkpoint(session_id: str) -> dict[str, Any] | None:
    db = await get_db()
    row = await db.fetchrow(
        "SELECT * FROM curriculum_checkpoints WHERE session_id = $1",
        session_id,
    )
    if not row:
        return None
    return _row_to_dict(row)


async def upsert_checkpoint(
    session_id: str,
    *,
    content_hash: str | None = None,
    pipeline_version: str | None = None,
    pipeline_meta: dict | None = None,
    required_outline: dict | None = None,
    regions: list | None = None,
    completed_region_ids: list | None = None,
    all_candidates: list | None = None,
    summary_parts: list | None = None,
    meter_breakdown: dict | None = None,
    last_region_id: str | None = None,
) -> None:
    existing = await load_checkpoint(session_id)
    if existing:
        merged = dict(existing)
    else:
        if content_hash is None:
            raise ValueError("content_hash required for new checkpoint")
        merged = {
            "session_id": session_id,
            "content_hash": content_hash,
            "pipeline_version": pipeline_version or _PIPELINE_VERSION,
            "pipeline_meta": pipeline_meta or {},
            "required_outline": None,
            "regions": [],
            "completed_region_ids": [],
            "all_candidates": [],
            "summary_parts": [],
            "meter_breakdown": {},
            "last_region_id": None,
        }

    if content_hash is not None:
        merged["content_hash"] = content_hash
    if pipeline_version is not None:
        merged["pipeline_version"] = pipeline_version
    if pipeline_meta is not None:
        merged["pipeline_meta"] = {**merged.get("pipeline_meta", {}), **pipeline_meta}
    if required_outline is not None:
        merged["required_outline"] = required_outline
    if regions is not None:
        merged["regions"] = regions
    if completed_region_ids is not None:
        merged["completed_region_ids"] = completed_region_ids
    if all_candidates is not None:
        merged["all_candidates"] = all_candidates
    if summary_parts is not None:
        merged["summary_parts"] = summary_parts
    if meter_breakdown is not None:
        merged["meter_breakdown"] = meter_breakdown
    if last_region_id is not None:
        merged["last_region_id"] = last_region_id

    db = await get_db()
    # upsert_checkpoint is a single INSERT...ON CONFLICT statement → no explicit tx needed
    await db.execute(
        """INSERT INTO curriculum_checkpoints
           (session_id, content_hash, pipeline_version, pipeline_meta_json,
            required_outline_json, regions_json, completed_region_ids_json,
            all_candidates_json, summary_parts_json, meter_json, last_region_id,
            updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, CURRENT_TIMESTAMP)
           ON CONFLICT (session_id) DO UPDATE SET
            content_hash = EXCLUDED.content_hash,
            pipeline_version = EXCLUDED.pipeline_version,
            pipeline_meta_json = EXCLUDED.pipeline_meta_json,
            required_outline_json = EXCLUDED.required_outline_json,
            regions_json = EXCLUDED.regions_json,
            completed_region_ids_json = EXCLUDED.completed_region_ids_json,
            all_candidates_json = EXCLUDED.all_candidates_json,
            summary_parts_json = EXCLUDED.summary_parts_json,
            meter_json = EXCLUDED.meter_json,
            last_region_id = EXCLUDED.last_region_id,
            updated_at = CURRENT_TIMESTAMP""",
        session_id,
        merged["content_hash"],
        merged.get("pipeline_version", _PIPELINE_VERSION),
        json.dumps(merged.get("pipeline_meta") or {}, ensure_ascii=False),
        json.dumps(merged.get("required_outline"), ensure_ascii=False)
        if merged.get("required_outline") is not None
        else None,
        json.dumps(merged.get("regions") or [], ensure_ascii=False),
        json.dumps(merged.get("completed_region_ids") or [], ensure_ascii=False),
        json.dumps(merged.get("all_candidates") or [], ensure_ascii=False),
        json.dumps(merged.get("summary_parts") or [], ensure_ascii=False),
        json.dumps(
            {"breakdown": merged.get("meter_breakdown") or {}},
            ensure_ascii=False,
        ),
        merged.get("last_region_id"),
    )


async def delete_checkpoint(session_id: str) -> None:
    db = await get_db()
    await db.execute(
        "DELETE FROM curriculum_checkpoints WHERE session_id = $1",
        session_id,
    )


async def list_resumable_sessions() -> list[str]:
    """generating sessions with checkpoint + source_chunks."""
    db = await get_db()
    rows = await db.fetch(
        """SELECT s.session_id
           FROM sessions s
           INNER JOIN curriculum_checkpoints c ON c.session_id = s.session_id
           WHERE s.status = 'generating'
             AND EXISTS (
               SELECT 1 FROM source_chunks sc WHERE sc.session_id = s.session_id
             )
           ORDER BY c.updated_at ASC""",
    )
    return [row[0] for row in rows]
