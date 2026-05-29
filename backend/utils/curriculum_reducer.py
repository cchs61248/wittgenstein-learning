"""Outcome → stage conversion used by stage_composer and small-file pipeline.

Originally also hosted the global reducer (rule merge / LLM unsure / Plan B
fuzzy attach). Those helpers were removed in the V2 unification; only the
outcomes → stages converter remains.
"""
from __future__ import annotations


def outcomes_to_stages(
    outcomes: list[dict],
    chunks_lookup: dict[str, str] | None = None,
) -> list[dict]:
    stages: list[dict] = []
    lookup = chunks_lookup or {}
    for idx, o in enumerate(outcomes):
        chunk_ids: list[str] = []
        source_chunks_meta: list[dict] = []
        for ev in [o.get("primary_evidence", {}), *(o.get("supporting_evidence") or [])]:
            if not ev:
                continue
            for cid in ev.get("chunk_ids") or []:
                if cid not in chunk_ids:
                    chunk_ids.append(cid)
                    source_chunks_meta.append({
                        "chunk_id": cid,
                        "quote": lookup.get(cid) or "",
                        "note": ev.get("source_id", ""),
                    })
        stages.append({
            "stage_id": idx + 1,
            "node_id": f"{(idx // 3) + 1}.{(idx % 3) + 1}",
            "title": o.get("title", f"階段 {idx + 1}"),
            "teaching_goal": o.get("teaching_goal", ""),
            "key_concepts": o.get("key_concepts") or [],
            "source_chunk_ids": chunk_ids,
            "source_chunks": source_chunks_meta,
            "prerequisites": [],
            "estimated_questions": 2,
            "primary_source_id": (o.get("primary_evidence") or {}).get("source_id"),
        })
    return stages
