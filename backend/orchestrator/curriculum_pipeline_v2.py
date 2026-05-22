"""Curriculum pipeline V2 — macro regions, per-region split, global reduce."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import TYPE_CHECKING, Any

from ..agents.base_agent import AgentContext
from ..agents.global_curriculum_reducer import GlobalCurriculumReducerAgent, GlobalCurriculumReducerError
from ..agents.global_curriculum_verifier import verify_global_coverage
from ..agents.macro_region_planner import MacroRegionPlannerAgent
from ..agents.stage_composer import StageComposerAgent
from ..memory import session_memory
from ..utils.canonicalize_apply import apply_canonical_mappings
from ..utils.curriculum_reducer import attach_supporting_by_fuzzy_match, outcomes_to_stages
from ..utils.fuzzy_match import concepts_match
from ..utils.region_planning import slice_region_chunks
from ..utils.stage_budget import compute_dynamic_max_stages

if TYPE_CHECKING:
    from .learning_orchestrator import LearningOrchestrator

_log = logging.getLogger("wl.orchestrator.v2")


def _dedupe_candidates(candidates: list[dict], threshold: float = 0.85) -> list[dict]:
    merged: list[dict] = []
    for c in candidates:
        placed = False
        for m in merged:
            if concepts_match(
                [str(x) for x in m.get("key_concepts") or []],
                [str(x) for x in c.get("key_concepts") or []],
                threshold=threshold,
            ):
                m_ids = m.setdefault("source_chunk_ids", [])
                for cid in c.get("source_chunk_ids") or []:
                    if cid not in m_ids:
                        m_ids.append(cid)
                placed = True
                break
        if not placed:
            merged.append(dict(c))
    return merged


def _splitter_stages_to_candidates(stages: list[dict], region: dict) -> list[dict]:
    out: list[dict] = []
    for s in stages:
        out.append({
            "region_id": region.get("region_id"),
            "source_id": region.get("source_id"),
            "title": s.get("title"),
            "teaching_goal": s.get("teaching_goal", ""),
            "key_concepts": s.get("key_concepts") or [],
            "source_chunk_ids": s.get("source_chunk_ids") or [],
            "confidence": 0.9,
        })
    return out


async def run_start_session_v2(
    orch: "LearningOrchestrator",
    *,
    session_id: str,
    user_id: str,
    source_chunks: list[dict],
    target_depth: str,
    question_mode: str,
    provider_name: str | None,
    model_name: str | None,
    emit,
    source_file_ids: list[str] | None = None,
) -> None:
    hash_seed = "".join(c["text"][:80] for c in source_chunks)
    content_hash = hashlib.sha256(hash_seed.encode()).hexdigest()[:16]
    sources_manifest = _build_sources_manifest(source_chunks)

    _log.info(
        "start_session_v2  session=%s  chunks=%d  sources=%d",
        session_id, len(source_chunks), len(sources_manifest),
    )

    await session_memory.create_generating_stub(
        session_id, user_id, content_hash,
        source_file_ids=source_file_ids or [],
        sources_json=sources_manifest,
    )
    await session_memory.insert_source_chunks(session_id, source_chunks)
    if source_file_ids:
        await session_memory.purge_source_uploads(session_id, source_file_ids)
    await emit({"type": "session_generating", "payload": {"session_id": session_id}})

    required_outline: dict | None = None
    if source_chunks:
        outline_ctx = AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload={"source_chunks": source_chunks},
        )
        try:
            required_outline = await orch.content_outliner.run(outline_ctx)
            _log.info(
                "v2 outline done  session=%s  cases=%d  titles=%d",
                session_id,
                len(required_outline.get("named_cases") or []),
                len(required_outline.get("required_stage_titles") or []),
            )
        except Exception as e:
            _log.warning("v2 content_outline failed  session=%s  err=%s", session_id, e)

    regions = await MacroRegionPlannerAgent(orch.splitter.llm, orch.splitter.token_counter).run(
        AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload={"source_chunks": source_chunks},
        )
    )
    regions = regions.get("regions") or []
    await emit({
        "type": "region_done",
        "payload": {"session_id": session_id, "region_count": len(regions)},
    })

    source_count = len(sources_manifest) or 1
    max_stages = compute_dynamic_max_stages(
        source_chunks, source_count=source_count, required_outline=required_outline,
    )
    per_region_max = max(3, max_stages // max(len(regions), 1))

    all_candidates: list[dict] = []
    summary_parts: list[str] = []

    for ri, region in enumerate(regions):
        region_chunks = slice_region_chunks(source_chunks, region, regions, ri)
        if not region_chunks:
            continue
        splitter_ctx_payload: dict = {
            "source_chunks": region_chunks,
            "max_stages": min(per_region_max, region.get("expected_stage_count", 5) + 2),
            "target_depth": target_depth,
        }
        if required_outline:
            splitter_ctx_payload["required_outline"] = required_outline
        ctx = AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload=splitter_ctx_payload,
        )
        try:
            split_result = await orch.splitter.run(ctx)
        except Exception as e:
            _log.warning("v2 region split failed  region=%s  err=%s", region.get("region_id"), e)
            continue
        region_stages = split_result.get("stages") or []
        summary_parts.append(split_result.get("summary") or "")
        verify_ctx = AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload={"source_chunks": region_chunks, "stages": region_stages},
        )
        try:
            vresult = await orch.splitter_verifier.run(verify_ctx)
            if not vresult.get("aligned"):
                _log.warning(
                    "v2 region verifier failed  region=%s  missing=%s",
                    region.get("region_id"), vresult.get("missing_options"),
                )
        except Exception as e:
            _log.warning("v2 region verifier error  region=%s  err=%s", region.get("region_id"), e)
        all_candidates.extend(_splitter_stages_to_candidates(region_stages, region))
        await emit({
            "type": "region_done",
            "payload": {
                "session_id": session_id,
                "region_id": region.get("region_id"),
                "stage_count": len(region_stages),
            },
        })

    all_candidates = _dedupe_candidates(all_candidates)
    summary = " ".join(s for s in summary_parts if s).strip() or "V2 課程路徑"

    use_plan_b = os.getenv("CURRICULUM_V2_PLAN_B") == "1"
    quality_warnings: dict | None = None
    stages: list[dict]

    if use_plan_b:
        primary_source = sources_manifest[0]["source_id"] if sources_manifest else "src_0"
        primary_candidates = [c for c in all_candidates if c.get("source_id") == primary_source]
        other_chunks = [
            c for c in source_chunks
            if c.get("source_id") != primary_source
        ]
        stages = outcomes_to_stages([
            {
                "outcome_id": f"lo_{i+1:03d}",
                "title": c.get("title", ""),
                "teaching_goal": c.get("teaching_goal", ""),
                "key_concepts": c.get("key_concepts") or [],
                "primary_evidence": {"source_id": primary_source, "chunk_ids": c.get("source_chunk_ids") or []},
                "supporting_evidence": [],
                "merge_decision": "split",
                "merge_confidence": 1.0,
            }
            for i, c in enumerate(primary_candidates)
        ])
        stages = attach_supporting_by_fuzzy_match(stages, other_chunks)
        quality_warnings = {"plan_b_active": True, "primary_source_id": primary_source}
    else:
        reducer = GlobalCurriculumReducerAgent(orch.splitter.llm, orch.splitter.token_counter)
        reducer_ctx = AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload={"candidate_stages": all_candidates, "use_llm": True},
        )
        try:
            reduce_result = await reducer.run(reducer_ctx)
            outcomes = reduce_result.get("outcomes") or []
        except GlobalCurriculumReducerError:
            raise
        except Exception as e:
            _log.warning("v2 reducer failed  session=%s  err=%s", session_id, e)
            outcomes = []
        fail_mode = os.getenv("REDUCER_FAIL_MODE", "hard").strip().lower()
        if not outcomes:
            if fail_mode == "soft":
                stages = outcomes_to_stages([
                    {
                        "outcome_id": f"lo_{i+1:03d}",
                        "title": c.get("title", ""),
                        "teaching_goal": c.get("teaching_goal", ""),
                        "key_concepts": c.get("key_concepts") or [],
                        "primary_evidence": {
                            "source_id": c.get("source_id", ""),
                            "chunk_ids": c.get("source_chunk_ids") or [],
                        },
                        "supporting_evidence": [],
                        "merge_decision": "split",
                        "merge_confidence": 1.0,
                    }
                    for i, c in enumerate(all_candidates)
                ])
                quality_warnings = {"reducer_fallback_flat": True}
            else:
                await session_memory.abandon_generating_stub(session_id)
                raise RuntimeError("GlobalCurriculumReducer 未產出任何 unified outcomes")
        else:
            composer = StageComposerAgent()
            stages = composer.compose(outcomes)

    await emit({
        "type": "reduce_done",
        "payload": {"session_id": session_id, "candidate_count": len(all_candidates), "stage_count": len(stages)},
    })

    gverify = verify_global_coverage(stages, source_chunks, required_outline)
    if not gverify.get("aligned"):
        soft = os.getenv("SPLITTER_FAIL_MODE", "hard").strip().lower() == "soft"
        qw = {
            "splitter_verifier_failed": True,
            "missing_options": gverify.get("missing_options") or [],
            "reason": gverify.get("reason") or "global verifier",
        }
        if soft:
            quality_warnings = {**(quality_warnings or {}), **qw}
        else:
            _log.warning("v2 global verifier failed  session=%s  %s", session_id, gverify)

    new_concepts = sorted({c for s in stages for c in s.get("key_concepts", [])})
    if content_hash and new_concepts:
        try:
            from ..memory import longterm_memory
            historical_pool = await longterm_memory.get_concept_canonical_pool(
                user_id=user_id, source_signature=content_hash, limit=80,
            )
            canon_ctx = AgentContext(
                session_id=session_id, user_id=user_id,
                task_payload={"new_concepts": new_concepts, "historical_pool": historical_pool},
            )
            canon_result = await orch.canonicalizer.run(canon_ctx)
            stages = apply_canonical_mappings(stages, canon_result["mappings"])
        except Exception as e:
            _log.warning("v2 canonicalize failed  session=%s  err=%s", session_id, e)

    nodes = [
        {"node_id": s["node_id"], "stage_id": s["stage_id"], "title": s["title"]}
        for s in stages
    ]
    orch._pending_stages = stages
    orch._pending_start_args = {
        "session_id": session_id,
        "user_id": user_id,
        "content_hash": content_hash,
        "summary": summary,
        "question_mode": question_mode,
    }

    await session_memory.create_pending_session(
        session_id=session_id,
        user_id=user_id,
        content_hash=content_hash,
        summary=summary,
        stages=stages,
        nodes=nodes,
        provider_name=provider_name,
        model_name=model_name,
        question_mode=question_mode,
        source_file_ids=source_file_ids or [],
        quality_warnings=quality_warnings,
    )

    km_payload: dict = {"nodes": nodes, "summary": summary}
    if quality_warnings:
        km_payload["quality_warnings"] = quality_warnings
    await emit({"type": "knowledge_map", "payload": km_payload})
    await emit({
        "type": "composer_done",
        "payload": {"session_id": session_id, "stage_count": len(stages)},
    })


def _build_sources_manifest(source_chunks: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for c in source_chunks:
        sid = c.get("source_id") or f"src_{c.get('source_index', 0)}"
        if sid not in seen:
            seen[sid] = {
                "source_id": sid,
                "source_index": c.get("source_index", 0),
                "source_label": c.get("source_label") or sid,
            }
    return sorted(seen.values(), key=lambda x: x.get("source_index", 0))
