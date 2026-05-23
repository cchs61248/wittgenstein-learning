"""Rule-based + LLM curriculum reducer utilities."""
from __future__ import annotations

import json
from typing import Any

from ..utils.fuzzy_match import concept_overlap_score, similarity
from .reducer_constants import (
    FUZZY_ATTACH_THRESHOLD,
    MERGE_CONFIDENCE_MIN,
    RULE_MERGE_KC_THRESHOLD,
    RULE_MERGE_TG_SIM,
    RULE_MERGED_CONFIDENCE,
    UNSURE_KC_SCORE,
    UNSURE_TG_SIM,
    UNSURE_TITLE_SIM,
)

# V2.1: comparison / conflict stages not implemented; LLM conflict output is downgraded.
CONFLICT_SUPPORTED = False


def rule_merge_candidates(
    candidates: list[dict],
    threshold: float = RULE_MERGE_KC_THRESHOLD,
) -> tuple[list[list[int]], list[tuple[int, int]]]:
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
            title_a = (a.get("title") or "").strip()
            title_b = (b.get("title") or "").strip()
            title_sim = similarity(title_a, title_b)
            tg_sim = similarity(tg_a, tg_b)
            kc_score = concept_overlap_score(
                [str(x) for x in kc_a],
                [str(x) for x in kc_b],
            )
            chunks_a = set(a.get("source_chunk_ids") or [])
            chunks_b = set(b.get("source_chunk_ids") or [])
            if chunks_a and chunks_b:
                chunk_overlap = len(chunks_a & chunks_b) / min(len(chunks_a), len(chunks_b))
            else:
                chunk_overlap = 0.0
            # hard rule: 標題完全相同 + chunk 子集關係（overlap ≥ 80%）強制 merge。
            # 處理 LLM reducer 對「同名 title 來自不同 region 的重複 candidate」
            # 0 accepted 的失敗模式（gemini / claude 已觀察）。
            if title_a and title_a == title_b and chunk_overlap >= 0.8:
                union(i, j)
            elif tg_sim >= RULE_MERGE_TG_SIM and kc_score >= threshold:
                union(i, j)
            elif tg_sim >= UNSURE_TG_SIM or title_sim >= UNSURE_TITLE_SIM or kc_score >= UNSURE_KC_SCORE:
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


def split_outcome_from_candidate(
    candidate: dict,
    outcome_id: str,
    *,
    source_index: int,
) -> dict[str, Any]:
    """Step C: one candidate → one split outcome (never drop a stage)."""
    outcome = build_outcome_from_group([candidate], [0], outcome_id)
    outcome["_source_indices"] = [source_index]
    return outcome


def build_outcome_from_group(
    candidates: list[dict],
    indices: list[int],
    outcome_id: str,
) -> dict[str, Any]:
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
    outcome: dict[str, Any] = {
        "outcome_id": outcome_id,
        "title": primary.get("title") or "未命名",
        "teaching_goal": primary.get("teaching_goal") or "",
        "key_concepts": concepts,
        "primary_evidence": {
            "source_id": primary.get("source_id", ""),
            "chunk_ids": primary.get("source_chunk_ids") or [],
        },
        "supporting_evidence": [
            {
                "source_id": s.get("source_id", ""),
                "chunk_ids": s.get("source_chunk_ids") or [],
            }
            for s in supporting
        ],
        "merge_decision": "merged" if len(indices) > 1 else "split",
        "merge_confidence": RULE_MERGED_CONFIDENCE if len(indices) > 1 else 1.0,
        "_source_indices": list(indices),
    }
    return outcome


def _chunk_ids_from_outcome(outcome: dict) -> set[str]:
    ids: set[str] = set()
    primary = outcome.get("primary_evidence") or {}
    ids.update(primary.get("chunk_ids") or [])
    for ev in outcome.get("supporting_evidence") or []:
        ids.update(ev.get("chunk_ids") or [])
    return ids


def _indices_for_outcome(candidates: list[dict], outcome: dict) -> set[int]:
    if outcome.get("_source_indices") is not None:
        return set(outcome["_source_indices"])
    chunk_ids = _chunk_ids_from_outcome(outcome)
    matched: set[int] = set()
    for i, c in enumerate(candidates):
        cids = set(c.get("source_chunk_ids") or [])
        if cids & chunk_ids:
            matched.add(i)
    return matched


def _normalize_llm_outcome(raw: dict) -> dict:
    outcome = dict(raw)
    decision = (outcome.get("merge_decision") or "split").strip().lower()
    if decision == "conflict":
        if not CONFLICT_SUPPORTED:
            outcome["merge_decision"] = "split"
            outcome["conflict_deferred"] = True
            outcome["conflict_note"] = "V2.1: comparison stages not implemented"
        decision = outcome["merge_decision"]
    if isinstance(outcome.get("primary_evidence"), list):
        outcome["primary_evidence"] = (outcome["primary_evidence"] or [{}])[0]
    return outcome


def integrate_llm_outcomes(
    candidates: list[dict],
    step_a_outcomes: list[dict],
    llm_outcomes: list[dict],
    unsure_pairs: list[tuple[int, int]],
) -> list[dict]:
    """
    Step B: accept LLM merge when confidence >= MERGE_CONFIDENCE_MIN.
    Step C: confidence < MERGE_CONFIDENCE_MIN → keep Step A splits (default split).
    """
    outcomes = [dict(o) for o in step_a_outcomes]

    for raw in llm_outcomes:
        llm_o = _normalize_llm_outcome(raw)
        conf = float(llm_o.get("merge_confidence") or 0)
        decision = (llm_o.get("merge_decision") or "split").strip().lower()

        if conf < MERGE_CONFIDENCE_MIN or decision != "merged":
            continue

        merge_indices = _indices_for_outcome(candidates, llm_o)
        if len(merge_indices) < 2:
            for i, j in unsure_pairs:
                if _outcome_references_pair(llm_o, candidates, i, j):
                    merge_indices = {i, j}
                    break
        if len(merge_indices) < 2:
            continue

        merge_set = set(merge_indices)
        outcomes = [
            o for o in outcomes
            if not set(o.get("_source_indices") or []).issubset(merge_set)
        ]
        rebuilt = build_outcome_from_group(
            candidates, sorted(merge_set), llm_o.get("outcome_id", "lo_llm")
        )
        rebuilt["merge_decision"] = "merged"
        rebuilt["merge_confidence"] = conf
        if llm_o.get("title"):
            rebuilt["title"] = llm_o["title"]
        if llm_o.get("teaching_goal"):
            rebuilt["teaching_goal"] = llm_o["teaching_goal"]
        outcomes.append(rebuilt)

    outcomes = ensure_candidate_coverage(candidates, outcomes)
    return outcomes


def _outcome_references_pair(outcome: dict, candidates: list[dict], i: int, j: int) -> bool:
    chunk_ids = _chunk_ids_from_outcome(outcome)
    ci = set(candidates[i].get("source_chunk_ids") or [])
    cj = set(candidates[j].get("source_chunk_ids") or [])
    return bool(chunk_ids & ci) and bool(chunk_ids & cj)


def ensure_candidate_coverage(candidates: list[dict], outcomes: list[dict]) -> list[dict]:
    """Step C safety net: every candidate index must appear in at least one outcome."""
    covered: set[int] = set()
    for o in outcomes:
        covered.update(o.get("_source_indices") or [])
    for i in range(len(candidates)):
        if i not in covered:
            outcomes.append(
                split_outcome_from_candidate(
                    candidates[i], f"lo_split_{i + 1:03d}", source_index=i
                )
            )
    return outcomes


def strip_internal_fields(outcomes: list[dict]) -> list[dict]:
    cleaned: list[dict] = []
    for o in outcomes:
        c = {k: v for k, v in o.items() if not k.startswith("_")}
        cleaned.append(c)
    return cleaned


def build_step_a_outcomes(candidates: list[dict], merged_groups: list[list[int]]) -> list[dict]:
    outcomes: list[dict] = []
    for gi, indices in enumerate(merged_groups):
        outcomes.append(build_outcome_from_group(candidates, indices, f"lo_{gi + 1:03d}"))
    return outcomes


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


def attach_supporting_by_fuzzy_match(
    stages: list[dict],
    extra_chunks: list[dict],
    threshold: float = FUZZY_ATTACH_THRESHOLD,
) -> list[dict]:
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
    stripped = (raw or "").strip()
    # Prefer outer JSON array (extract_json may grab inner object from [{...}])
    if stripped.startswith("["):
        end = stripped.rfind("]")
        candidate = stripped[: end + 1] if end > 0 else stripped
    else:
        candidate = extract_json(raw)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("outcomes"), list):
            return data["outcomes"]
        if data.get("outcome_id") or data.get("merge_decision"):
            return [data]
    return []


def measure_merge_accuracy(
    candidates: list[dict],
    outcomes: list[dict],
    expected_merge_pairs: list[tuple[int, int]],
) -> float:
    """Go/No-Go helper: fraction of expected merge pairs sharing one outcome."""
    if not expected_merge_pairs:
        return 1.0
    correct = 0
    for i, j in expected_merge_pairs:
        share = False
        for o in outcomes:
            indices = o.get("_source_indices")
            if indices is None:
                continue
            idx_set = set(indices)
            if i in idx_set and j in idx_set and o.get("merge_decision") == "merged":
                share = True
                break
        if share:
            correct += 1
    return correct / len(expected_merge_pairs)


def pair_is_merged(outcomes: list[dict], i: int, j: int) -> bool:
    for o in outcomes:
        indices = o.get("_source_indices")
        if not indices:
            continue
        idx_set = set(indices)
        if i in idx_set and j in idx_set and o.get("merge_decision") == "merged":
            return True
    return False


def measure_split_accuracy(
    outcomes: list[dict],
    pairs_should_split: list[tuple[int, int]],
) -> float:
    """Go/No-Go: fraction of pairs correctly kept separate (no false merge)."""
    if not pairs_should_split:
        return 1.0
    correct = sum(
        1 for i, j in pairs_should_split
        if not pair_is_merged(outcomes, i, j)
    )
    return correct / len(pairs_should_split)


def measure_unsure_abstain_rate(agent_result: dict, *, expected_merge: bool) -> float:
    """
    Per-run unsure proxy: LLM had unsure pairs but zero parsed outcomes accepted.
    Aggregate average across merge cases approximates plan 'unsure 率'.
    """
    if not expected_merge:
        return 0.0
    unsure = int(agent_result.get("unsure_pair_count") or 0)
    if unsure == 0:
        return 0.0
    llm_out = int(agent_result.get("llm_outcome_count") or 0)
    return 1.0 if llm_out == 0 else 0.0
