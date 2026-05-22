"""Rule-based + LLM curriculum reducer utilities."""
from __future__ import annotations

import json
from typing import Any

from ..utils.fuzzy_match import concept_overlap_score, similarity


def rule_merge_candidates(candidates: list[dict], threshold: float = 0.85) -> tuple[list[dict], list[tuple[int, int]]]:
    """
    Step A: auto-merge obvious pairs.
    Returns (merged_groups, unsure_pairs) where each group is list of candidate indices.
    """
    n = len(candidates)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    unsure: list[tuple[int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            a, b = candidates[i], candidates[j]
            tg_a = (a.get("teaching_goal") or "").strip()
            tg_b = (b.get("teaching_goal") or "").strip()
            kc_a = a.get("key_concepts") or []
            kc_b = b.get("key_concepts") or []
            title_sim = similarity(a.get("title", ""), b.get("title", ""))
            tg_sim = similarity(tg_a, tg_b)
            kc_score = concept_overlap_score(
                [str(x) for x in kc_a],
                [str(x) for x in kc_b],
            )
            if tg_sim >= 0.9 and kc_score >= threshold:
                union(i, j)
            elif tg_sim >= 0.75 or title_sim >= 0.8 or kc_score >= 0.7:
                unsure.append((i, j))

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    merged = [sorted(idx_list) for idx_list in groups.values()]
    merged_unsure = [
        p for p in unsure
        if find(p[0]) != find(p[1])
    ]
    return merged, merged_unsure


def build_outcome_from_group(candidates: list[dict], indices: list[int], outcome_id: str) -> dict[str, Any]:
    primary = candidates[indices[0]]
    supporting = [candidates[i] for i in indices[1:]]
    chunk_ids: list[str] = []
    for c in [primary, *supporting]:
        chunk_ids.extend(c.get("source_chunk_ids") or [])
    seen: set[str] = set()
    unique_chunks: list[str] = []
    for cid in chunk_ids:
        if cid not in seen:
            seen.add(cid)
            unique_chunks.append(cid)
    concepts: list[str] = []
    for c in [primary, *supporting]:
        for k in c.get("key_concepts") or []:
            ks = str(k)
            if ks not in concepts:
                concepts.append(ks)
    return {
        "outcome_id": outcome_id,
        "title": primary.get("title") or "未命名",
        "teaching_goal": primary.get("teaching_goal") or "",
        "key_concepts": concepts,
        "primary_evidence": [{
            "source_id": primary.get("source_id", ""),
            "chunk_ids": primary.get("source_chunk_ids") or [],
        }],
        "supporting_evidence": [
            {
                "source_id": s.get("source_id", ""),
                "chunk_ids": s.get("source_chunk_ids") or [],
            }
            for s in supporting
        ],
        "merge_decision": "merged" if len(indices) > 1 else "split",
        "merge_confidence": 0.95 if len(indices) > 1 else 1.0,
    }


def outcomes_to_stages(outcomes: list[dict]) -> list[dict]:
    stages: list[dict] = []
    for idx, o in enumerate(outcomes):
        chunk_ids: list[str] = []
        source_chunks_meta: list[dict] = []
        for ev in [o.get("primary_evidence", {}), *(o.get("supporting_evidence") or [])]:
            if not ev:
                continue
            for cid in ev.get("chunk_ids") or []:
                if cid not in chunk_ids:
                    chunk_ids.append(cid)
                    source_chunks_meta.append({"chunk_id": cid, "quote": "", "note": ev.get("source_id", "")})
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


def attach_supporting_by_fuzzy_match(stages: list[dict], extra_chunks: list[dict], threshold: float = 0.85) -> list[dict]:
    """Plan B: attach chunks from non-primary sources to nearest stage by fuzzy match."""
    for chunk in extra_chunks:
        cid = chunk.get("chunk_id")
        if not cid:
            continue
        title_hint = (chunk.get("section_title") or chunk.get("text", "")[:80]).strip()
        best_stage = None
        best_score = 0.0
        for stage in stages:
            score = max(
                similarity(title_hint, stage.get("title", "")),
                similarity(title_hint, stage.get("teaching_goal", "")),
            )
            if score > best_score:
                best_score = score
                best_stage = stage
        if best_stage and best_score >= threshold:
            ids = best_stage.setdefault("source_chunk_ids", [])
            if cid not in ids:
                ids.append(cid)
            meta = best_stage.setdefault("source_chunks", [])
            meta.append({
                "chunk_id": cid,
                "quote": chunk.get("text", "")[:500],
                "note": f"supporting:{chunk.get('source_id', '')}",
            })
    return stages


def parse_reducer_llm_output(raw: str) -> list[dict]:
    from ..utils import extract_json
    data = json.loads(extract_json(raw))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("outcomes"), list):
        return data["outcomes"]
    return []
