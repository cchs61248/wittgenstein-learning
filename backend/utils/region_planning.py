"""Programmatic macro-region planning (no LLM required for baseline)."""
from __future__ import annotations

import math
from typing import Any


def overlap_chunk_count(region_size: int) -> int:
    if region_size <= 0:
        return 0
    return min(8, max(2, math.ceil(region_size * 0.10)))


def _group_key(chunk: dict) -> tuple:
    return (
        chunk.get("source_id") or chunk.get("source_index", 0),
        (chunk.get("section_title") or "").strip() or None,
    )


def plan_macro_regions(source_chunks: list[dict], *, chunks_per_region: int = 25) -> list[dict]:
    """
    Three-tier fallback:
    (a) group by source + section_title when present
    (b) fixed-size windows within each source
    """
    if not source_chunks:
        return []

    sorted_chunks = sorted(source_chunks, key=lambda c: c.get("order_index", 0))
    by_source: dict[Any, list[dict]] = {}
    for c in sorted_chunks:
        sid = c.get("source_id") or c.get("source_index", 0)
        by_source.setdefault(sid, []).append(c)

    regions: list[dict] = []
    region_idx = 0

    for source_id, chunks in by_source.items():
        has_sections = any((c.get("section_title") or "").strip() for c in chunks)
        if has_sections:
            groups: list[list[dict]] = []
            current: list[dict] = []
            last_key = None
            for c in chunks:
                key = _group_key(c)[1]
                if last_key is not None and key != last_key and current:
                    groups.append(current)
                    current = []
                current.append(c)
                last_key = key
            if current:
                groups.append(current)
        else:
            groups = [
                chunks[i : i + chunks_per_region]
                for i in range(0, len(chunks), chunks_per_region)
            ]

        for group in groups:
            if not group:
                continue
            chunk_ids = [c["chunk_id"] for c in group]
            overlap = overlap_chunk_count(len(group))
            regions.append({
                "region_id": f"region_{region_idx:03d}",
                "source_id": str(source_id),
                "chunk_id_range": [chunk_ids[0], chunk_ids[-1]],
                "chunk_ids": chunk_ids,
                "title": (group[0].get("section_title") or f"區塊 {region_idx + 1}").strip(),
                "expected_stage_count": max(1, min(8, math.ceil(len(group) / 3))),
                "overlap_before": overlap,
                "overlap_after": overlap,
                "must_cover_topics": [],
            })
            region_idx += 1

    return regions


def slice_region_chunks(
    source_chunks: list[dict],
    region: dict,
    all_regions: list[dict],
    region_index: int,
) -> list[dict]:
    """Return region chunks plus overlap from neighbors."""
    by_id = {c["chunk_id"]: c for c in source_chunks}
    ids = set(region.get("chunk_ids") or [])
    if region_index > 0:
        prev = all_regions[region_index - 1]
        prev_ids = prev.get("chunk_ids") or []
        ov = region.get("overlap_before") or 0
        ids.update(prev_ids[-ov:] if ov else [])
    if region_index < len(all_regions) - 1:
        nxt = all_regions[region_index + 1]
        nxt_ids = nxt.get("chunk_ids") or []
        ov = region.get("overlap_after") or 0
        ids.update(nxt_ids[:ov] if ov else [])
    ordered = sorted(
        (by_id[cid] for cid in ids if cid in by_id),
        key=lambda c: c.get("order_index", 0),
    )
    return ordered
