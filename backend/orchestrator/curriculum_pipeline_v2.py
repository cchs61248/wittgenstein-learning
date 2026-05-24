"""Curriculum pipeline V2 — macro regions, per-region split, global reduce."""
from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

from ..utils.content_hash import compute_content_hash

from ..agents.base_agent import AgentContext
from ..agents.global_curriculum_reducer import GlobalCurriculumReducerAgent, GlobalCurriculumReducerError
from ..agents.global_curriculum_verifier import verify_global_coverage
from ..agents.macro_region_planner import MacroRegionPlannerAgent
from ..agents.stage_composer import StageComposerAgent
from ..memory import session_memory
from ..utils.canonicalize_apply import apply_canonical_mappings
from ..utils.curriculum_reducer import attach_supporting_by_fuzzy_match, outcomes_to_stages
from ..utils.fuzzy_match import concepts_match
from ..utils.reducer_constants import MAX_MERGED_OUTCOME_CHUNKS
from ..utils.region_planning import (
    enrich_regions_with_outline_topics,
    is_listicle_source,
    slice_region_chunks,
)
from ..utils.small_curriculum import (
    best_chunk_for_case,
    candidates_to_stages_flat,
    dedupe_key_concept_aliases,
    ensure_orphan_chunks_attached,
    finalize_curriculum_stages,
    finalize_small_file_stages,
    filter_false_verifier_misses,
    filter_missing_named_cases,
    is_compact_curriculum,
    is_small_file,
    merge_duplicate_topic_stages,
    normalize_case_name,
    normalize_stages_pre_verify,
    pending_enum_label_misses,
    prune_phantom_key_concepts,
    source_count as count_sources,
    use_per_source_split_path,
    use_single_split_path,
    prune_toc_listicle_chunks,
    split_oversized_stages,
    trim_stage_key_concepts,
    zero_region_overlaps,
)
from ..utils.stage_budget import compute_dynamic_max_stages
from ..utils.curriculum_llm_meter import CurriculumLlmMeter, assess_curriculum_cost

if TYPE_CHECKING:
    from .learning_orchestrator import LearningOrchestrator

_log = logging.getLogger("wl.orchestrator.v2")


def _dedupe_candidates(candidates: list[dict], threshold: float = 0.85) -> list[dict]:
    """合併 kc 相似 candidate；合併後 chunk 數超過 cap 則拒絕合併、保留獨立 candidate。

    chunk cap 來自 sess_live_049d39ce stage 2 案例：region_001/002/005 splitter 各出
    「房屋貸款」相關 candidate，dedupe 把跨 region chunks 全併入單一 candidate，
    產生 25-chunk mega candidate (region 邊界外的 chunk_0119, 0120 也被吸進來)。
    cap 與 [[reducer-chunk-cap]] / MAX_MERGED_OUTCOME_CHUNKS 一致，避免超大 stage 過載 Teacher。
    """
    merged: list[dict] = []
    for c in candidates:
        placed = False
        incoming_ids = c.get("source_chunk_ids") or []
        for m in merged:
            if concepts_match(
                [str(x) for x in m.get("key_concepts") or []],
                [str(x) for x in c.get("key_concepts") or []],
                threshold=threshold,
            ):
                m_ids = m.setdefault("source_chunk_ids", [])
                projected_total = len({*m_ids, *incoming_ids})
                if projected_total > MAX_MERGED_OUTCOME_CHUNKS:
                    _log.warning(
                        "dedupe reject merge: chunks=%d > cap=%d  m_region=%s  c_region=%s  m_title=%s  c_title=%s",
                        projected_total,
                        MAX_MERGED_OUTCOME_CHUNKS,
                        m.get("region_id"),
                        c.get("region_id"),
                        m.get("title"),
                        c.get("title"),
                    )
                    continue
                for cid in incoming_ids:
                    if cid not in m_ids:
                        m_ids.append(cid)
                placed = True
                break
        if not placed:
            merged.append(dict(c))
    return merged


def _build_follow_up_stages(
    *,
    stages: list[dict],
    source_chunks: list[dict],
    missing_options: list[str],
    orphan_chunk_ids: list[str],
    max_total_stages: int,
) -> list[dict]:
    """P1 (b)：global verifier fail 後補建 follow-up stages 救 missing case + orphan chunk。

    - missing_options 內每個 named case 嘗試找含 case 名的 chunk，補 1 stage
    - orphan_chunk_ids 全部歸到 1 個「章節總結」stage
    - 受 max_total_stages 硬上限保護避免爆 stage 數
    """
    if not stages:
        return []
    next_stage_id = max((s.get("stage_id") or 0) for s in stages) + 1
    next_node_chapter = max(int(str(s.get("node_id", "0.0")).split(".")[0] or 0) for s in stages) + 1
    chunks_by_id = {c["chunk_id"]: c for c in source_chunks if isinstance(c, dict) and c.get("chunk_id")}
    covered_chunks: set[str] = set()
    for s in stages:
        covered_chunks.update(s.get("source_chunk_ids") or [])

    new_stages: list[dict] = []

    for case_name in missing_options:
        if len(stages) + len(new_stages) >= max_total_stages:
            break
        case_main = normalize_case_name(case_name)
        if not case_main:
            continue
        if not filter_missing_named_cases([case_name], stages + new_stages, source_chunks):
            continue
        matched_chunk_id = best_chunk_for_case(
            case_name,
            chunks_by_id,
            prefer_unclaimed=set(covered_chunks),
            intro_chunk_id=(
                sorted(source_chunks, key=lambda c: c.get("order_index", 0))[0].get("chunk_id")
                if source_chunks else None
            ),
        )
        if not matched_chunk_id:
            continue
        chunk = chunks_by_id[matched_chunk_id]
        new_stages.append({
            "stage_id": next_stage_id,
            "node_id": f"{next_node_chapter}.{len(new_stages) + 1}",
            "title": f"案例：{case_main}",
            "key_concepts": [case_main],
            "source_chunk_ids": [matched_chunk_id],
            "source_chunks": [{
                "chunk_id": matched_chunk_id,
                "quote": chunk.get("text") or "",
                "note": chunk.get("source_id") or "",
            }],
            "prerequisites": [],
            "estimated_questions": 2,
            "teaching_goal": f"補建案例：{case_main} 的核心設計考量與應用情境",
            "kind": "follow_up_case",
        })
        next_stage_id += 1

    # bug fix (sess_live_e106b1a4)：covered_chunks 只反映 input stages，
    # 不含本次 follow_up_case stages 已用的 chunks。
    # 若 missing_options 補建的 case stage 用到的 chunk_id 也出現在 orphan_chunk_ids，
    # 會被重複包進 follow_up_orphan stage（造成同 chunk 兩 stage 重複講解）。
    for s in new_stages:
        covered_chunks.update(s.get("source_chunk_ids") or [])

    remaining_orphans = [cid for cid in orphan_chunk_ids if cid not in covered_chunks and cid in chunks_by_id]
    # 大量 orphan 留給 finalize_small_file_stages 分散到鄰近 stage，避免單一「垃圾 stage」
    if remaining_orphans and len(remaining_orphans) <= 3 and len(stages) + len(new_stages) < max_total_stages:
        orphan_meta = [
            {
                "chunk_id": cid,
                "quote": chunks_by_id[cid].get("text") or "",
                "note": chunks_by_id[cid].get("source_id") or "",
            }
            for cid in remaining_orphans
        ]
        new_stages.append({
            "stage_id": next_stage_id,
            "node_id": f"{next_node_chapter}.{len(new_stages) + 1}",
            "title": "章節總結與補充內容",
            "key_concepts": ["章節總結", "補充內容"],
            "source_chunk_ids": remaining_orphans,
            "source_chunks": orphan_meta,
            "prerequisites": [],
            "estimated_questions": 2,
            "teaching_goal": "補建：未被前面節點覆蓋的章節總結、面試話術與補充內容",
            "kind": "follow_up_orphan",
        })

    return new_stages


def _build_plan_b_stages(
    all_candidates: list[dict],
    source_chunks: list[dict],
    sources_manifest: list[dict],
    chunks_lookup: dict[str, str],
) -> tuple[list[dict], dict]:
    """Plan B: one region candidate → one stage; skip reducer LLM merge."""
    primary_source = sources_manifest[0]["source_id"] if sources_manifest else "src_0"
    primary_candidates = [c for c in all_candidates if c.get("source_id") == primary_source]
    if not primary_candidates:
        primary_candidates = list(all_candidates)
        if primary_candidates:
            primary_source = str(primary_candidates[0].get("source_id") or "src_0")
    other_chunks = [
        c for c in source_chunks
        if c.get("source_id") != primary_source
    ]
    stages = outcomes_to_stages([
        {
            "outcome_id": f"lo_{i + 1:03d}",
            "title": c.get("title", ""),
            "teaching_goal": c.get("teaching_goal", ""),
            "key_concepts": c.get("key_concepts") or [],
            "primary_evidence": {
                "source_id": primary_source,
                "chunk_ids": c.get("source_chunk_ids") or [],
            },
            "supporting_evidence": [],
            "merge_decision": "split",
            "merge_confidence": 1.0,
        }
        for i, c in enumerate(primary_candidates)
    ], chunks_lookup=chunks_lookup)
    stages = attach_supporting_by_fuzzy_match(stages, other_chunks)
    return stages, {"plan_b_active": True, "primary_source_id": primary_source}


def _splitter_stages_to_candidates(stages: list[dict], region: dict) -> list[dict]:
    """Convert splitter stages → reducer candidates，過濾掉 region 邊界外的 chunk_id。

    splitter 收到的 region_chunks 含上下文 overlap（前後 region 的 chunk），
    但 stage.source_chunk_ids 只能包含 region 本身的 chunk_ids，
    否則同一 chunk 會被前後兩個 region 的 stage 同時宣告為「正主」，
    導致 reducer 出 duplicate candidate（claude session region_000/001 都涵蓋 chunk_0000-2 的 root cause）。
    """
    region_chunk_set = set(region.get("chunk_ids") or [])
    out: list[dict] = []
    for s in stages:
        raw_ids = s.get("source_chunk_ids") or []
        if region_chunk_set:
            filtered_ids = [cid for cid in raw_ids if cid in region_chunk_set]
        else:
            filtered_ids = list(raw_ids)
        # raw_ids 非空但 filter 後全變空 = splitter 把 chunks 全分到 region 外
        # → skip 此 stage 避免 region_001 重切 region_000 的 chunks。
        # raw_ids 本來就空 = stage 沒 chunks（測試 mock 或邊界），保留候選不削減。
        if raw_ids and not filtered_ids:
            continue
        out.append({
            "region_id": region.get("region_id"),
            "source_id": region.get("source_id"),
            "title": s.get("title"),
            "teaching_goal": s.get("teaching_goal", ""),
            "key_concepts": s.get("key_concepts") or [],
            "source_chunk_ids": filtered_ids,
            "confidence": 0.9,
        })
    return out


async def _run_single_split(
    *,
    orch: "LearningOrchestrator",
    session_id: str,
    user_id: str,
    source_chunks: list[dict],
    target_depth: str,
    max_stages: int,
    required_outline: dict | None,
    emit,
    meter: CurriculumLlmMeter | None = None,
) -> tuple[list[dict], str]:
    """small_file path：一次 splitter call 處理整檔（bypass MacroRegion + per-region loop）。

    對 ≤ small_file_chunk_threshold (default 50) 的教材：MacroRegionPlanner 切的
    region 數 × per-region splitter+verifier+reroll 加總 LLM 用量極不合理
    （sess_live_e106b1a4 Rate Limiter 20 chunks 跑了 23 次 splitter+verifier）。

    替代流程：1 splitter + 1 verifier + 最多 1 次 reroll = 2-3 次 LLM 即可，
    省 ~80% splitter 階段 LLM 用量。
    """
    splitter_ctx_payload: dict = {
        "source_chunks": source_chunks,
        "max_stages": max_stages,
        "target_depth": target_depth,
    }
    if required_outline:
        splitter_ctx_payload["required_outline"] = required_outline
    ctx = AgentContext(
        session_id=session_id, user_id=user_id, task_payload=splitter_ctx_payload,
    )
    try:
        split_result = await orch.splitter.run(ctx)
        if meter:
            meter.record("ContentSplitterAgent")
    except Exception as e:
        _log.warning("v2 small_file split failed  session=%s  err=%s", session_id, e)
        return [], ""
    stages = split_result.get("stages") or []
    summary = split_result.get("summary") or ""

    verify_ctx = AgentContext(
        session_id=session_id, user_id=user_id,
        task_payload={"source_chunks": source_chunks, "stages": stages},
    )
    vresult: dict | None = None
    try:
        vresult = await orch.splitter_verifier.run(verify_ctx)
        if meter:
            meter.record("SplitterVerifierAgent")
    except Exception as e:
        _log.warning("v2 small_file verifier error  session=%s  err=%s", session_id, e)

    if vresult and not vresult.get("aligned"):
        filtered = filter_false_verifier_misses(
            vresult.get("missing_options") or [], stages, source_chunks,
        )
        if not filtered:
            enum_left = pending_enum_label_misses(
                vresult.get("missing_options") or [], stages,
            )
            if enum_left:
                filtered = enum_left
                _log.warning(
                    "v2 small_file verifier enum gap reroll  session=%s  misses=%s",
                    session_id, enum_left,
                )
            else:
                _log.info("v2 small_file verifier false positive filtered  session=%s", session_id)
        else:
            _log.warning(
                "v2 small_file verifier failed  session=%s  missing=%s",
                session_id, filtered,
            )
            repair_struct = vresult.get("repair_plan_struct") or {}
            if not repair_struct.get("required_stage_titles") and required_outline:
                repair_struct = {
                    **repair_struct,
                    "required_stage_titles": (
                        required_outline.get("required_stage_titles") or []
                    ),
                }
            reroll_ctx = AgentContext(
                session_id=session_id, user_id=user_id,
                task_payload={
                    **splitter_ctx_payload,
                    "previous_attempt_missed": filtered,
                    "issue_chunk_ids": vresult.get("issue_chunk_ids") or [],
                    "verifier_reason": vresult.get("reason", ""),
                    "repair_plan_struct": repair_struct,
                },
            )
            try:
                rerolled = await orch.splitter.run(reroll_ctx)
                if meter:
                    meter.record("ContentSplitterAgent")
                rerolled_stages = rerolled.get("stages") or []
                if rerolled_stages:
                    stages = rerolled_stages
                    _log.info(
                        "v2 small_file reroll done  session=%s  stages=%d",
                        session_id, len(stages),
                    )
            except Exception as e:
                _log.warning(
                    "v2 small_file reroll failed  session=%s  err=%s", session_id, e,
                )

    # 單一 pseudo region 涵蓋全 chunks（_splitter_stages_to_candidates 用 chunk_ids 過濾）
    primary_source_id = (
        source_chunks[0].get("source_id") if source_chunks else ""
    ) or "src_0"
    pseudo_region = {
        "region_id": "region_single",
        "source_id": primary_source_id,
        "chunk_ids": [
            c["chunk_id"] for c in source_chunks if c.get("chunk_id")
        ],
    }
    candidates = _splitter_stages_to_candidates(stages, pseudo_region)
    await emit({
        "type": "region_done",
        "payload": {
            "session_id": session_id,
            "region_id": "region_single",
            "stage_count": len(stages),
        },
    })
    return candidates, summary


async def _run_per_source_split(
    *,
    orch: "LearningOrchestrator",
    session_id: str,
    user_id: str,
    source_chunks: list[dict],
    sources_manifest: list[dict],
    target_depth: str,
    max_stages: int,
    required_outline: dict | None,
    emit,
    meter: CurriculumLlmMeter | None = None,
) -> tuple[list[dict], str]:
    """multi-source small_file：每 source 各跑 single-split，再合併 candidates。"""
    n_sources = len(sources_manifest) or 1
    per_source_max = max(3, max_stages // n_sources)
    all_candidates: list[dict] = []
    summary_parts: list[str] = []

    await emit({
        "type": "region_done",
        "payload": {"session_id": session_id, "region_count": n_sources},
    })

    for src in sources_manifest:
        sid = src.get("source_id") or f"src_{src.get('source_index', 0)}"
        idx = src.get("source_index", 0)
        subset = [
            c for c in source_chunks
            if isinstance(c, dict)
            and (
                (c.get("source_id") or f"src_{c.get('source_index', 0)}") == sid
                or c.get("source_index") == idx
            )
        ]
        if not subset:
            continue
        candidates, summary = await _run_single_split(
            orch=orch,
            session_id=session_id,
            user_id=user_id,
            source_chunks=subset,
            target_depth=target_depth,
            max_stages=per_source_max,
            required_outline=required_outline,
            emit=emit,
            meter=meter,
        )
        all_candidates.extend(candidates)
        if summary:
            summary_parts.append(summary)

    combined = " ".join(s for s in summary_parts if s).strip()
    return all_candidates, combined


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
    content_hash = compute_content_hash(source_chunks)
    sources_manifest = _build_sources_manifest(source_chunks)
    meter = CurriculumLlmMeter()

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

    # early detect: small_file 跳過 ContentOutline（C）+ MacroRegionPlanner（A）
    single_split = use_single_split_path(source_chunks)
    per_source_split = use_per_source_split_path(source_chunks)
    small_file = single_split or per_source_split

    required_outline: dict | None = None
    force_outline = os.getenv("SMALL_FILE_FORCE_OUTLINE", "0").strip().lower() in (
        "1", "true", "yes",
    )
    if source_chunks and (not small_file or force_outline):
        outline_ctx = AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload={"source_chunks": source_chunks},
        )
        try:
            required_outline = await orch.content_outliner.run(outline_ctx)
            meter.record("ContentOutlineAgent")
            _log.info(
                "v2 outline done  session=%s  cases=%d  titles=%d",
                session_id,
                len(required_outline.get("named_cases") or []),
                len(required_outline.get("required_stage_titles") or []),
            )
        except Exception as e:
            _log.warning("v2 content_outline failed  session=%s  err=%s", session_id, e)

    # small_file 已在前面 early-detect — 不重複偵測
    n_sources = count_sources(source_chunks)
    max_stages = compute_dynamic_max_stages(
        source_chunks, source_count=n_sources, required_outline=required_outline,
    )

    all_candidates: list[dict] = []
    summary_parts: list[str] = []

    if single_split:
        # small_file single-source：bypass MacroRegionPlanner + per-region loop
        _log.info(
            "v2 small_file path (single-split)  session=%s  chunks=%d",
            session_id, len(source_chunks),
        )
        await emit({
            "type": "region_done",
            "payload": {"session_id": session_id, "region_count": 1},
        })
        single_candidates, single_summary = await _run_single_split(
            orch=orch,
            session_id=session_id,
            user_id=user_id,
            source_chunks=source_chunks,
            target_depth=target_depth,
            max_stages=max_stages,
            required_outline=required_outline,
            emit=emit,
            meter=meter,
        )
        all_candidates.extend(single_candidates)
        if single_summary:
            summary_parts.append(single_summary)
        regions = []  # 下游 region_done loop 不需執行
    elif per_source_split:
        _log.info(
            "v2 small_file path (per-source-split)  session=%s  chunks=%d  sources=%d",
            session_id, len(source_chunks), n_sources,
        )
        per_candidates, per_summary = await _run_per_source_split(
            orch=orch,
            session_id=session_id,
            user_id=user_id,
            source_chunks=source_chunks,
            sources_manifest=sources_manifest,
            target_depth=target_depth,
            max_stages=max_stages,
            required_outline=required_outline,
            emit=emit,
            meter=meter,
        )
        all_candidates.extend(per_candidates)
        if per_summary:
            summary_parts.append(per_summary)
        regions = []
    else:
        regions_result = await MacroRegionPlannerAgent(
            orch.splitter.llm, orch.splitter.token_counter
        ).run(
            AgentContext(
                session_id=session_id,
                user_id=user_id,
                task_payload={"source_chunks": source_chunks},
            )
        )
        meter.record("MacroRegionPlannerAgent")
        regions = regions_result.get("regions") or []
        regions = enrich_regions_with_outline_topics(
            regions, required_outline, source_chunks,
        )
        await emit({
            "type": "region_done",
            "payload": {"session_id": session_id, "region_count": len(regions)},
        })

    per_region_max = max(3, max_stages // max(len(regions), 1)) if regions else max_stages

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
        if region.get("must_cover_topics"):
            splitter_ctx_payload["must_cover_topics"] = region["must_cover_topics"]
        ctx = AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload=splitter_ctx_payload,
        )
        try:
            split_result = await orch.splitter.run(ctx)
            meter.record("ContentSplitterAgent")
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
        vresult: dict | None = None
        try:
            vresult = await orch.splitter_verifier.run(verify_ctx)
            meter.record("SplitterVerifierAgent")
        except Exception as e:
            _log.warning("v2 region verifier error  region=%s  err=%s", region.get("region_id"), e)

        # P1 (a)：V2 per-region splitter_verifier fail 時 reroll 1 次
        # （V1 path 有 2 次 reroll；V2 短期至少給 1 次以救 named case 漏切）
        if vresult and not vresult.get("aligned"):
            filtered_missing = filter_false_verifier_misses(
                vresult.get("missing_options") or [],
                region_stages,
                region_chunks,
            )
            if not filtered_missing:
                _log.info(
                    "v2 region verifier false positive filtered  region=%s",
                    region.get("region_id"),
                )
                vresult = {**vresult, "aligned": True, "missing_options": []}
            else:
                vresult = {**vresult, "missing_options": filtered_missing}
        if vresult and not vresult.get("aligned"):
            _log.warning(
                "v2 region verifier failed  region=%s  missing=%s",
                region.get("region_id"), vresult.get("missing_options"),
            )
            repair_struct = vresult.get("repair_plan_struct") or {}
            if not repair_struct.get("required_stage_titles") and required_outline:
                repair_struct = {
                    **repair_struct,
                    "required_stage_titles": (
                        required_outline.get("required_stage_titles") or []
                    ),
                }
            reroll_payload = {
                **splitter_ctx_payload,
                "previous_attempt_missed": vresult.get("missing_options") or [],
                "issue_chunk_ids": vresult.get("issue_chunk_ids") or [],
                "verifier_reason": vresult.get("reason", ""),
                "repair_plan_struct": repair_struct,
            }
            reroll_ctx = AgentContext(
                session_id=session_id, user_id=user_id, task_payload=reroll_payload,
            )
            try:
                reroll_result = await orch.splitter.run(reroll_ctx)
                meter.record("ContentSplitterAgent")
                rerolled_stages = reroll_result.get("stages") or []
                if rerolled_stages:
                    region_stages = rerolled_stages
                    _log.info(
                        "v2 region reroll done  region=%s  stages=%d",
                        region.get("region_id"), len(region_stages),
                    )
            except Exception as e:
                _log.warning(
                    "v2 region reroll failed (keeping initial stages)  region=%s  err=%s",
                    region.get("region_id"), e,
                )

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

    chunks_lookup = {
        c["chunk_id"]: c.get("text", "")
        for c in source_chunks
        if isinstance(c, dict) and c.get("chunk_id")
    }

    use_plan_b = os.getenv("CURRICULUM_V2_PLAN_B") == "1"
    plan_b_active = False
    quality_warnings: dict | None = None
    stages: list[dict]
    reduce_metrics: dict = {
        "candidate_count": len(all_candidates),
        "outcome_count": 0,
        "unsure_pair_count": 0,
        "llm_outcome_count": 0,
    }

    if small_file:
        stages = candidates_to_stages_flat(all_candidates, chunks_lookup)
        quality_warnings = {"small_file_path": True, "reducer_skipped": True}
        if per_source_split:
            quality_warnings["multi_source_split"] = True
            quality_warnings["source_count"] = n_sources
        reduce_metrics["outcome_count"] = len(all_candidates)
    elif use_plan_b:
        stages, plan_b_qw = _build_plan_b_stages(
            all_candidates, source_chunks, sources_manifest, chunks_lookup,
        )
        quality_warnings = plan_b_qw
        reduce_metrics["outcome_count"] = len(all_candidates)
        plan_b_active = True
    else:
        reducer = GlobalCurriculumReducerAgent(orch.splitter.llm, orch.splitter.token_counter)
        reducer_ctx = AgentContext(
            session_id=session_id,
            user_id=user_id,
            task_payload={"candidate_stages": all_candidates, "use_llm": True},
        )
        try:
            reduce_result = await reducer.run(reducer_ctx)
            meter.record("GlobalCurriculumReducerAgent")
            outcomes = reduce_result.get("outcomes") or []
            reduce_metrics["outcome_count"] = len(outcomes)
            reduce_metrics["unsure_pair_count"] = reduce_result.get("unsure_pair_count") or 0
            reduce_metrics["llm_outcome_count"] = reduce_result.get("llm_outcome_count") or 0
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
                ], chunks_lookup=chunks_lookup)
                quality_warnings = {"reducer_fallback_flat": True}
            else:
                await session_memory.abandon_generating_stub(session_id)
                raise RuntimeError("GlobalCurriculumReducer 未產出任何 unified outcomes")
        else:
            composer = StageComposerAgent()
            stages = composer.compose(outcomes, chunks_lookup=chunks_lookup)

        from ..utils.curriculum_health import assess_reducer_health, should_auto_plan_b

        pre_health = assess_reducer_health(
            session_id=session_id,
            candidate_count=reduce_metrics["candidate_count"],
            outcome_count=reduce_metrics["outcome_count"],
            stage_count=len(stages),
            unsure_pair_count=reduce_metrics["unsure_pair_count"],
            llm_outcome_count=reduce_metrics["llm_outcome_count"],
            quality_warnings=quality_warnings,
            plan_b_active=False,
        )
        if should_auto_plan_b() and pre_health.get("plan_b_recommended"):
            _log.info(
                "v2 auto Plan B fallback  session=%s  signals=%s",
                session_id, pre_health.get("signals"),
            )
            stages, plan_b_qw = _build_plan_b_stages(
                all_candidates, source_chunks, sources_manifest, chunks_lookup,
            )
            quality_warnings = {
                **(quality_warnings or {}),
                **plan_b_qw,
                "plan_b_auto_fallback": True,
                "plan_b_fallback_signals": pre_health.get("signals") or [],
            }
            reduce_metrics["outcome_count"] = len(all_candidates)
            plan_b_active = True

    stages = normalize_stages_pre_verify(stages, source_chunks)

    from ..utils.curriculum_health import assess_reducer_health

    health = assess_reducer_health(
        session_id=session_id,
        candidate_count=reduce_metrics["candidate_count"],
        outcome_count=reduce_metrics["outcome_count"],
        stage_count=len(stages),
        unsure_pair_count=reduce_metrics["unsure_pair_count"],
        llm_outcome_count=reduce_metrics["llm_outcome_count"],
        quality_warnings=quality_warnings,
        plan_b_active=plan_b_active,
    )
    if health["signals"]:
        quality_warnings = {
            **(quality_warnings or {}),
            "health_signals": health["signals"],
            "plan_b_recommended": health["plan_b_recommended"],
        }

    await emit({
        "type": "reduce_done",
        "payload": {
            "session_id": session_id,
            "candidate_count": len(all_candidates),
            "stage_count": len(stages),
            "health": health,
        },
    })

    gverify = verify_global_coverage(stages, source_chunks, required_outline)
    initial_gverify = gverify
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

        # P1 (b)：補建 follow-up stages 救 missing named case + orphan chunk
        truly_missing = filter_missing_named_cases(
            gverify.get("missing_options") or [],
            stages,
            source_chunks,
        )
        follow_up = _build_follow_up_stages(
            stages=stages,
            source_chunks=source_chunks,
            missing_options=truly_missing,
            orphan_chunk_ids=gverify.get("orphan_chunk_ids") or [],
            max_total_stages=int(len(stages) * 1.5) + 2,
        )
        if follow_up:
            stages.extend(follow_up)
            stages = merge_duplicate_topic_stages(stages)
            _log.info(
                "v2 global verifier post-process  session=%s  added_stages=%d  total=%d",
                session_id, len(follow_up), len(stages),
            )
            quality_warnings = {
                **(quality_warnings or {}),
                "post_process_added_stages": len(follow_up),
            }
            gverify_after = verify_global_coverage(stages, source_chunks, required_outline)
            if gverify_after.get("aligned"):
                _log.info(
                    "v2 global verifier post-process succeeded  session=%s", session_id,
                )

    if is_compact_curriculum(source_chunks):
        stages = finalize_small_file_stages(stages, source_chunks)
        gverify = verify_global_coverage(stages, source_chunks, required_outline)
        if gverify.get("aligned") and not initial_gverify.get("aligned"):
            _log.info(
                "v2 compact recovered after finalize  session=%s  orphans_before=%d",
                session_id,
                len(initial_gverify.get("orphan_chunk_ids") or []),
            )
        elif not gverify.get("aligned"):
            _log.warning(
                "v2 compact still misaligned after finalize  session=%s  %s",
                session_id,
                gverify,
            )
    elif not initial_gverify.get("aligned"):
        stages = normalize_stages_pre_verify(stages, source_chunks)
        orphan_check = verify_global_coverage(stages, source_chunks, required_outline)
        if orphan_check.get("orphan_chunk_ids"):
            stages = ensure_orphan_chunks_attached(stages, source_chunks)
            stages = split_oversized_stages(stages, source_chunks)
            stages = dedupe_key_concept_aliases(stages)
            stages = prune_phantom_key_concepts(stages, source_chunks)
            stages = trim_stage_key_concepts(stages)
            _log.info(
                "v2 full path orphan attach  session=%s  orphans=%d",
                session_id, len(orphan_check.get("orphan_chunk_ids") or []),
            )

    if is_listicle_source(source_chunks):
        stages = prune_toc_listicle_chunks(stages, source_chunks)
        orphan_after = verify_global_coverage(stages, source_chunks, required_outline)
        if orphan_after.get("orphan_chunk_ids"):
            stages = ensure_orphan_chunks_attached(stages, source_chunks)
            stages = split_oversized_stages(stages, source_chunks)
            stages = trim_stage_key_concepts(stages)

    stages = finalize_curriculum_stages(stages, source_chunks)

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
            meter.record("ConceptCanonicalizeAgent")
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

    cost_qw = assess_curriculum_cost(
        session_id=session_id, meter=meter, source_chunks=source_chunks,
    )
    quality_warnings = {**(quality_warnings or {}), **cost_qw}

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
