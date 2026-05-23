"""Programmatic macro-region planning (no LLM required for baseline)."""
from __future__ import annotations

import math
import re
from typing import Any

from .small_curriculum import _case_tokens

_RULE_SECTION_RE = re.compile(r"^法則\s*\d+", re.IGNORECASE)


def overlap_chunk_count(region_size: int) -> int:
    if region_size <= 0:
        return 0
    return min(8, max(2, math.ceil(region_size * 0.10)))


def _is_numbered_rule_title(title: str | None) -> bool:
    return bool(_RULE_SECTION_RE.match((title or "").strip()))


def _listicle_rule_ratio(chunks: list[dict]) -> float:
    if not chunks:
        return 0.0
    rule_count = sum(
        1 for c in chunks if _is_numbered_rule_title(c.get("section_title"))
    )
    return rule_count / len(chunks)


def _listicle_must_cover_topics(group: list[dict], *, max_topics: int = 8) -> list[str]:
    topics: list[str] = []
    for c in group:
        title = (c.get("section_title") or "").strip()
        if _is_numbered_rule_title(title) and title not in topics:
            topics.append(title)
    return topics[:max_topics]


def is_listicle_source(source_chunks: list[dict]) -> bool:
    """True when ≥40% chunks carry 法則 N section titles (numbered-rule books)."""
    return _listicle_rule_ratio(source_chunks) >= 0.4


def _group_key(chunk: dict) -> tuple:
    return (
        chunk.get("source_id") or chunk.get("source_index", 0),
        (chunk.get("section_title") or "").strip() or None,
    )


def _split_oversized_groups(
    groups: list[list[dict]],
    chunks_per_region: int,
    max_group_size: int,
) -> list[list[dict]]:
    """Split groups exceeding max_group_size into fixed-size sub-groups."""
    out: list[list[dict]] = []
    for group in groups:
        if len(group) <= max_group_size:
            out.append(group)
            continue
        for i in range(0, len(group), chunks_per_region):
            sub = group[i : i + chunks_per_region]
            if sub:
                out.append(sub)
    return out


def plan_macro_regions(
    source_chunks: list[dict],
    *,
    chunks_per_region: int = 25,
    max_group_size: int = 40,
) -> list[dict]:
    """
    Two-tier fallback (V2 baseline):
    (a) group by source + section_title when present;
        groups exceeding max_group_size force-split into fixed-size sub-groups
        (avoids 1-region degradation when epub chunker yields single shared title).
    (b) fixed-size windows within each source.

    Tier (c) LLM boundary refinement from region head/tail 500 chars is V2.1
    (see MacroRegionPlannerAgent — currently delegates here).
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
        listicle = has_sections and _listicle_rule_ratio(chunks) >= 0.4
        if listicle:
            # 法則 1…50 listicle：按固定窗口分 region，避免 50 個 one-chunk region
            groups = [
                chunks[i : i + chunks_per_region]
                for i in range(0, len(chunks), chunks_per_region)
            ]
        elif has_sections:
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
            groups = _split_oversized_groups(groups, chunks_per_region, max_group_size)
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
            cover_topics = _listicle_must_cover_topics(group) if listicle else []
            expected = max(1, min(8, math.ceil(len(group) / 3)))
            if cover_topics:
                expected = max(expected, min(len(cover_topics), 8))
            regions.append({
                "region_id": f"region_{region_idx:03d}",
                "source_id": str(source_id),
                "chunk_id_range": [chunk_ids[0], chunk_ids[-1]],
                "chunk_ids": chunk_ids,
                "title": (group[0].get("section_title") or f"區塊 {region_idx + 1}").strip(),
                "expected_stage_count": expected,
                "overlap_before": overlap,
                "overlap_after": overlap,
                "must_cover_topics": cover_topics,
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


def enrich_regions_with_outline_topics(
    regions: list[dict],
    required_outline: dict | None,
    source_chunks: list[dict],
    *,
    max_topics_per_region: int = 8,
) -> list[dict]:
    """將 content outline 的 named_cases / titles 依 chunk 歸屬注入 region must_cover_topics。"""
    if not regions or not required_outline:
        return regions

    by_id = {c["chunk_id"]: c for c in source_chunks if c.get("chunk_id")}
    topics: list[str] = []
    for key in ("named_cases", "required_stage_titles"):
        for item in required_outline.get(key) or []:
            label = str(item).strip()
            if label and label not in topics:
                topics.append(label)
    if not topics:
        return regions

    for region in regions:
        region_ids = set(region.get("chunk_ids") or [])
        region_text = " ".join(
            str(by_id[cid].get("text") or "")
            for cid in region_ids
            if cid in by_id
        )
        if not region_text:
            continue
        assigned = list(region.get("must_cover_topics") or [])
        for topic in topics:
            if topic in assigned:
                continue
            tokens = _case_tokens(topic) or [topic]
            if any(t in region_text for t in tokens if len(t) >= 2):
                assigned.append(topic)
        if assigned:
            region["must_cover_topics"] = assigned[:max_topics_per_region]
            region["expected_stage_count"] = max(
                int(region.get("expected_stage_count") or 1),
                min(len(assigned), max_topics_per_region),
            )
    return regions
