"""Curriculum pipeline V2 — small-file unified path (single-split / per-source-split)."""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from ..utils.content_hash import compute_content_hash

from ..agents.base_agent import AgentContext
from ..agents.global_curriculum_verifier import verify_global_coverage
from ..memory import session_memory
from ..memory import curriculum_checkpoint as ckpt_mem
from ..utils.canonicalize_apply import apply_canonical_mappings
from ..utils.fuzzy_match import concepts_match
from ..utils.reducer_constants import MAX_MERGED_OUTCOME_CHUNKS
from ..utils.region_planning import is_listicle_source
from ..utils.small_curriculum import (
    best_chunk_for_case,
    candidates_to_stages_flat,
    choose_postprocess_mode,
    cleanup_orphan_enumerator_titles,
    collect_key_concept_hygiene_warnings,
    enforce_stage_ordering,
    merge_by_concept_overlap,
    merge_singleton_chunk_stages,
    dedupe_key_concept_aliases,
    ensure_orphan_chunks_attached,
    finalize_curriculum_stages,
    finalize_small_file_stages,
    fold_interior_orphan_chunks,
    filter_false_verifier_misses,
    filter_missing_named_cases,
    is_compact_curriculum,
    merge_duplicate_topic_stages,
    normalize_case_name,
    normalize_stages_pre_verify,
    pending_enum_label_misses,
    prune_phantom_key_concepts,
    _renumber_stages,
    source_count as count_sources,
    prune_toc_listicle_chunks,
    split_oversized_stages,
    split_kc_heavy_stages,
    trim_stage_key_concepts,
    filter_epub_nav_junk_chunks,
)
from ..utils.stage_budget import compute_dynamic_max_stages
from ..utils.curriculum_llm_meter import CurriculumLlmMeter, assess_curriculum_cost
from ..utils.pedagogical_planner import (
    apply_pedagogical_plan,
    build_ordering_plan,
    build_prerequisite_graph,
    build_stage_cards,
    _stage_identity,
)

if TYPE_CHECKING:
    from .learning_orchestrator import LearningOrchestrator

_log = logging.getLogger("wl.orchestrator.v2")

_KM_SUMMARY_MAX_LEN = 180


def _merge_key_concept_hygiene_warnings(
    stages: list[dict], quality_warnings: dict | None
) -> dict | None:
    """Warn-only glue: audit final stage key_concepts and append findings to
    quality_warnings["key_concept_hygiene"] (a list). Never mutates stages, never
    overwrites existing hygiene entries, and leaves quality_warnings untouched
    (possibly None) when the build is clean.
    """
    kc_warnings = collect_key_concept_hygiene_warnings(stages)
    if not kc_warnings:
        return quality_warnings
    existing = list((quality_warnings or {}).get("key_concept_hygiene") or [])
    return {
        **(quality_warnings or {}),
        "key_concept_hygiene": existing + kc_warnings,
    }


def _canonicalize_enabled() -> bool:
    raw = os.getenv("CONCEPT_CANONICALIZE", "0").strip().lower()
    return raw in ("1", "true", "yes")


def _verifier_reroll_min_misses() -> int:
    """P2a: minimum missing-options count that triggers a Splitter reroll.

    Default 2 — verifier reporting just 1 miss is usually a minor naming
    drift; the downstream follow-up + orphan attach can recover it without
    paying for another Splitter+Verifier round trip.
    """
    raw = os.getenv("SPLITTER_VERIFIER_MIN_MISSES", "2").strip()
    try:
        v = int(raw)
        if v >= 1:
            return v
    except ValueError:
        pass
    return 2


def _build_knowledge_map_summary(summary_parts: list[str], stage_count: int) -> str:
    """產生單段知識地圖摘要，避免拼接全部 region summary 成無法閱讀的牆文。"""
    parts = [s.strip() for s in summary_parts if s and s.strip()]
    if not parts:
        return f"共 {stage_count} 個學習節點" if stage_count else "V2 課程路徑"
    head = parts[0]
    if len(head) > _KM_SUMMARY_MAX_LEN:
        cut = head[:_KM_SUMMARY_MAX_LEN]
        for sep in ("。", "！", "？", ".", "!"):
            idx = cut.rfind(sep)
            if idx >= 40:
                head = cut[: idx + 1]
                break
        else:
            head = cut.rstrip("，,、 ") + "…"
    if stage_count > 1:
        return f"{head}（共 {stage_count} 個學習節點）"
    return head


def _dedupe_within(candidates: list[dict], threshold: float = 0.85) -> list[dict]:
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


def _dedupe_candidates(
    candidates: list[dict],
    threshold: float = 0.85,
    same_material: bool = False,
) -> list[dict]:
    """kc 相似 candidate 去重入口。

    same_material=True（同教材多章）時按 source_id 分組、組內各自去重、不跨組 union，
    避免貫穿主題的 candidate 跨章合併打亂 reading order（Phase 2.5，sess_85qxyltir
    「星期三郵局」橫跨兩章被誤併的 root case）。
    same_material=False（cross_material / 預設）維持原跨 source 去重行為。
    """
    if not same_material:
        return _dedupe_within(candidates, threshold)
    # dict 保留各組首現順序，避免擾動下游（enforce_stage_ordering 之後仍會依 chunk 重排）
    groups: dict = {}
    for c in candidates:
        groups.setdefault(c.get("source_id"), []).append(c)
    result: list[dict] = []
    for group in groups.values():
        result.extend(_dedupe_within(group, threshold))
    return result


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


def _apply_deterministic_cleanup(
    stages: list[dict],
    source_chunks: list[dict],
    required_outline: dict | None,
    quality_warnings: dict,
    session_id: str,
) -> list[dict]:
    """確定性結構收尾（層 1）：orphan attach + 過量 kc 修剪 + oversized 拆分。

    與 global verify 的 aligned 結果無關，一律執行。`verify_global_coverage` 對
    orphan 數有容忍上限（compact_orphan_limit），aligned=True 不代表零 orphan——
    舊版把這段綁在 `not aligned` 分支，導致容忍上限內的 orphan 被靜默丟棄、kc 過量
    的 stage 不被修剪（live sess_81ihihq27：5 個尾 chunk 連同「利益衝突」整堂遺失、
    stage kc=10 未修）。這些都是確定性結構修正，非語意合併——語意合併（jaccard /
    consolidator）仍受 allow_merge 閘門控制，不在此函式。
    """
    orphan_check = verify_global_coverage(stages, source_chunks, required_outline)
    orphans_before = len(orphan_check.get("orphan_chunk_ids") or [])
    if orphans_before:
        # 先把「夾在閱讀區間內」的 interior orphan 折進鄰近 content stage，
        # 避免它被 ensure_orphan_chunks_attached 變成中段假「章節總結與補充內容」
        # stage（live sess_dra3xubdr：chunk_0049 夾在 stage 間卻成了第 15/20 的假總結）。
        # fold 後一定要重新計算 orphan，剩下的（真尾段 orphan）才走 fallback attach。
        folded = fold_interior_orphan_chunks(stages, source_chunks)
        if folded is not stages:
            after_fold = verify_global_coverage(folded, source_chunks, required_outline)
            folded_count = orphans_before - len(after_fold.get("orphan_chunk_ids") or [])
            stages = folded
            if folded_count > 0:
                _log.info(
                    "v2 deterministic cleanup folded interior orphans  session=%s  count=%d",
                    session_id, folded_count,
                )
        if (verify_global_coverage(stages, source_chunks, required_outline)
                .get("orphan_chunk_ids")):
            stages = ensure_orphan_chunks_attached(stages, source_chunks)
        stages = split_oversized_stages(stages, source_chunks)
    # kc 修剪 / 拆分 / 去重不依賴 orphan，永遠跑（避免 aligned 課綱殘留 kc 過量 stage）
    stages = split_kc_heavy_stages(stages, source_chunks)
    stages = dedupe_key_concept_aliases(stages)
    stages = prune_phantom_key_concepts(stages, source_chunks)
    stages = trim_stage_key_concepts(stages)
    if orphans_before:
        final_check = verify_global_coverage(stages, source_chunks, required_outline)
        orphans_after = len(final_check.get("orphan_chunk_ids") or [])
        _log.info(
            "v2 deterministic cleanup  session=%s  orphans before=%d  after=%d",
            session_id, orphans_before, orphans_after,
        )
        if orphans_after > 0:
            _log.warning(
                "v2 orphan attach incomplete  session=%s  remaining=%d  ids=%s",
                session_id, orphans_after,
                (final_check.get("orphan_chunk_ids") or [])[:10],
            )
            quality_warnings["orphan_attach_incomplete"] = orphans_after
    return stages


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
    source_hint: dict | None = None,
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
    if source_hint:
        splitter_ctx_payload["source_hint"] = source_hint
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
        # P2a: cutoff — if only 1 missing item, accept and let downstream
        # follow-up / orphan attach handle it. Avoids burning a reroll for
        # marginal misses (and reroll has its own ~30% failure rate).
        if filtered and len(filtered) <= _verifier_reroll_min_misses() - 1:
            _log.info(
                "v2 small_file verifier soft-pass  session=%s  misses=%d (<%d)",
                session_id, len(filtered), _verifier_reroll_min_misses(),
            )
            filtered = []
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
        # P1b: derive source_hint for this batch so Splitter knows which chapter
        # / file this is — naming stays consistent across per-source splits.
        first = subset[0]
        source_hint = {
            "source_label": first.get("source_label") or src.get("source_label"),
            "epub_filename": first.get("epub_filename"),
            "chapter_index": first.get("chapter_index"),
            "chapter_title": first.get("chapter_title"),
        }
        source_hint = {k: v for k, v in source_hint.items() if v is not None}
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
            source_hint=source_hint or None,
        )
        all_candidates.extend(candidates)
        if summary:
            summary_parts.append(summary)

    combined = _build_knowledge_map_summary(summary_parts, max(len(all_candidates), 1))
    return all_candidates, combined


async def _load_resume_checkpoint(
    session_id: str, content_hash: str,
) -> tuple[dict | None, bool]:
    checkpoint = await ckpt_mem.load_checkpoint(session_id)
    if not checkpoint:
        return None, False
    if checkpoint["content_hash"] != content_hash:
        await ckpt_mem.delete_checkpoint(session_id)
        return None, False
    return checkpoint, True


def _meter_from_checkpoint(checkpoint: dict | None) -> CurriculumLlmMeter:
    meter = CurriculumLlmMeter()
    if checkpoint:
        meter.breakdown = dict(checkpoint.get("meter_breakdown") or {})
    return meter


async def _save_checkpoint(
    session_id: str,
    content_hash: str,
    *,
    pipeline_meta: dict | None = None,
    required_outline: dict | None = None,
    regions: list | None = None,
    completed_region_ids: list | None = None,
    all_candidates: list | None = None,
    summary_parts: list | None = None,
    meter: CurriculumLlmMeter | None = None,
    last_region_id: str | None = None,
) -> None:
    await ckpt_mem.upsert_checkpoint(
        session_id,
        content_hash=content_hash,
        pipeline_meta=pipeline_meta,
        required_outline=required_outline,
        regions=regions,
        completed_region_ids=completed_region_ids,
        all_candidates=all_candidates,
        summary_parts=summary_parts,
        meter_breakdown=dict(meter.breakdown) if meter else None,
        last_region_id=last_region_id,
    )


def _is_cross_material_pedagogical_planner_enabled() -> bool:
    """Phase 4 / T4c feature flag. Default off — any value other than an explicit
    truthy token leaves the planner disabled (and the pipeline bit-for-bit)."""
    return str(os.getenv("CROSS_MATERIAL_PEDAGOGICAL_PLANNER", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _pedagogical_planner_gate_reasons(
    *,
    same_material: bool,
    chunk_count: int,
    stage_count: int,
    source_count: int,
    ordering_plan,
    graph,
) -> list[str]:
    """Conservative activation gate. Returns the (possibly empty) list of reasons
    the LLM planner must NOT be invoked; empty list ⇒ gate passes."""
    reasons: list[str] = []
    if same_material:
        reasons.append("same_material")
    if chunk_count < 30:
        reasons.append("insufficient_chunks")
    if stage_count < 6:
        reasons.append("insufficient_stages")
    if source_count < 3:
        reasons.append("insufficient_sources")
    if not ordering_plan.order_changed:
        reasons.append("no_order_change_recommended")
    if graph.has_cycle:
        reasons.append("prerequisite_cycle")
    return reasons


_PEDAGOGICAL_PLANNER_WARNING_SCHEMA_VERSION = 1


def _pedagogical_planner_order(stages: list[dict]) -> list[str]:
    return [_stage_identity(s, i) for i, s in enumerate(stages)]


def _renumber_finalized_stages_after_pedagogical_reorder(
    stages: list[dict],
) -> list[dict]:
    """T4e: after the planner reorders already-finalized stages, reapply the
    canonical finalize numbering so persisted stage_id / node_id match the new
    teaching order. Delegates to ``_renumber_stages`` to stay bit-identical to
    finalize's own convention (stage_id 1..N int, node_id "chapter.section").
    Copies each stage so the input list's dicts are not mutated.
    """
    return _renumber_stages([dict(s) for s in stages])


async def _maybe_apply_cross_material_pedagogical_planner(
    *,
    session_id: str,
    stages: list[dict],
    chunks: list[dict],
    same_material: bool,
    planner_agent,
    quality_warnings: dict,
    meter: "CurriculumLlmMeter | None" = None,
) -> list[dict]:
    """T4c seam — the only place Phase 4 touches the live pipeline.

    Flag off → returns ``stages`` unchanged, writes no warning, and calls no
    Phase 4 utility (bit-for-bit equivalence). Flag on → builds deterministic
    diagnostics, applies the activation gate, and only calls the LLM planner for
    large cross-material curricula. Planner / parse / verifier failures fall back
    to the original stage order. The (possibly reordered) stage list is returned;
    everything else is recorded warn-only under
    ``quality_warnings["cross_material_pedagogical_planner"]`` (schema v1).

    T4d hardens observability only: it adds a versioned warning schema with a
    run id, explicit agent/apply attempt flags, before/after stage-order
    snapshots, redacted move summaries, planner meter accounting, and stable log
    lines. It changes no planner decision, gate criterion, or apply semantics.
    """
    if not _is_cross_material_pedagogical_planner_enabled():
        return list(stages)

    # Computed before any utility call so the exception path can still report
    # them (no user id; cheap, never raises).
    run_id = f"phase4_{session_id}_{len(stages)}st_{len(chunks)}ch"
    stage_order_before = _pedagogical_planner_order(stages)
    agent_called = False
    apply_attempted = False

    try:
        cards, card_diags = build_stage_cards(stages)
        graph, graph_diags = build_prerequisite_graph(cards)
        ordering_plan = build_ordering_plan(cards, graph)

        source_count = count_sources(chunks)
        gate_reasons = _pedagogical_planner_gate_reasons(
            same_material=same_material,
            chunk_count=len(chunks),
            stage_count=len(stages),
            source_count=source_count,
            ordering_plan=ordering_plan,
            graph=graph,
        )
        gate_passed = not gate_reasons

        planner_mode = "diagnostics_only"
        agent_diags: list[dict] = []
        applier_diags: list[dict] = []
        fallback_reason: str | None = None
        applied_stage_ids: list[str] | None = None
        plan_move_count = 0
        plan_moves_redacted: list[dict] = []
        result_stages = stages

        if gate_passed:
            agent_called = True
            agent_result = await planner_agent.propose_plan(
                stages=stages, cards=cards, graph=graph, ordering_plan=ordering_plan,
            )
            if meter:
                meter.record("PedagogicalPlannerAgent")
            agent_diags = list(agent_result.diagnostics)
            if agent_result.plan is None:
                planner_mode = "fallback"
                fallback_reason = "planner_agent_failed"
            else:
                plan_move_count = len(agent_result.plan.moves)
                plan_moves_redacted = [
                    {"stage_id": m.stage_id, "after_stage_id": m.after_stage_id}
                    for m in agent_result.plan.moves
                ]
                apply_attempted = True
                apply_result = apply_pedagogical_plan(stages, agent_result.plan)
                applier_diags = list(apply_result.diagnostics)
                if apply_result.applied:
                    result_stages = list(apply_result.stages)
                    planner_mode = "applied"
                    applied_stage_ids = _pedagogical_planner_order(result_stages)
                else:
                    planner_mode = "fallback"
                    fallback_reason = apply_result.fallback_reason

        stage_order_after = _pedagogical_planner_order(result_stages)

        warning: dict = {
            "type": "cross_material_pedagogical_planner",
            "schema_version": _PEDAGOGICAL_PLANNER_WARNING_SCHEMA_VERSION,
            "run_id": run_id,
            "planner_mode": planner_mode,
            "enabled": True,
            "gate_passed": gate_passed,
            "gate_reasons": gate_reasons,
            "agent_called": agent_called,
            "apply_attempted": apply_attempted,
            "source_count": source_count,
            "stage_count": len(stages),
            "chunk_count": len(chunks),
            "stage_order_before": stage_order_before,
            "stage_order_after": stage_order_after,
            "current_stage_ids": list(ordering_plan.current_stage_ids),
            "recommended_stage_ids": list(ordering_plan.recommended_stage_ids),
            "order_changed": ordering_plan.order_changed,
            "plan_move_count": plan_move_count,
            "plan_moves_redacted": plan_moves_redacted,
            "stage_card_diagnostics": list(card_diags),
            "graph_diagnostics": list(graph_diags),
            "ordering_diagnostics": [dict(d) for d in ordering_plan.diagnostics],
            "agent_diagnostics": agent_diags,
            "applier_diagnostics": applier_diags,
        }
        if fallback_reason is not None:
            warning["fallback_reason"] = fallback_reason
        if applied_stage_ids is not None:
            warning["applied_stage_ids"] = applied_stage_ids

        quality_warnings["cross_material_pedagogical_planner"] = warning

        if planner_mode == "applied":
            _log.info(
                "v2 pedagogical planner applied  session=%s  run_id=%s  moves=%d  stages=%d",
                session_id, run_id, plan_move_count, len(result_stages),
            )
        elif planner_mode == "fallback":
            _log.warning(
                "v2 pedagogical planner fallback  session=%s  run_id=%s  reason=%s  "
                "agent_diags=%d  applier_diags=%d",
                session_id, run_id, fallback_reason, len(agent_diags), len(applier_diags),
            )
        else:  # diagnostics_only
            _log.info(
                "v2 pedagogical planner diagnostics_only  session=%s  run_id=%s  "
                "reasons=%s  stages=%d  chunks=%d  sources=%d",
                session_id, run_id, gate_reasons, len(stages), len(chunks), source_count,
            )
        return list(result_stages)

    except Exception as exc:  # noqa: BLE001 — planner must never abort the build
        _log.warning(
            "v2 pedagogical planner error_fallback  session=%s  run_id=%s  err=%s",
            session_id, run_id, exc,
        )
        quality_warnings["cross_material_pedagogical_planner"] = {
            "type": "cross_material_pedagogical_planner",
            "schema_version": _PEDAGOGICAL_PLANNER_WARNING_SCHEMA_VERSION,
            "run_id": run_id,
            "planner_mode": "error_fallback",
            "enabled": True,
            "gate_passed": False,
            "gate_reasons": ["planner_exception"],
            "agent_called": agent_called,
            "apply_attempted": apply_attempted,
            "stage_count": len(stages),
            "chunk_count": len(chunks),
            "stage_order_before": stage_order_before,
            "stage_order_after": stage_order_before,
            "diagnostics": [
                {
                    "type": "cross_material_pedagogical_planner_error",
                    "reason": type(exc).__name__,
                }
            ],
        }
        return list(stages)


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
    same_material: bool = True,
    order_decision: dict | None = None,
) -> None:
    source_chunks = filter_epub_nav_junk_chunks(source_chunks)
    content_hash = compute_content_hash(source_chunks)
    sources_manifest = _build_sources_manifest(source_chunks)

    checkpoint, resuming = await _load_resume_checkpoint(session_id, content_hash)
    if resuming and checkpoint:
        _log.info(
            "v2 checkpoint resume  session=%s  skip_regions=%d",
            session_id,
            len(checkpoint.get("completed_region_ids") or []),
        )

    meter = _meter_from_checkpoint(checkpoint)
    pipeline_meta: dict = {
        "user_id": user_id,
        "target_depth": target_depth,
        "question_mode": question_mode,
        "provider_name": provider_name,
        "model_name": model_name,
        "same_material": same_material,
    }
    if checkpoint:
        pipeline_meta = {**checkpoint.get("pipeline_meta", {}), **pipeline_meta}

    _log.info(
        "start_session_v2  session=%s  chunks=%d  sources=%d  resuming=%s",
        session_id, len(source_chunks), len(sources_manifest), resuming,
    )

    db_chunks = await session_memory.get_source_chunks(session_id) if resuming else []
    if resuming and db_chunks:
        if not source_chunks:
            source_chunks = db_chunks
    else:
        await session_memory.create_generating_stub(
            session_id, user_id, content_hash,
            source_file_ids=source_file_ids or [],
            sources_json=sources_manifest,
            provider_name=provider_name,
            model_name=model_name,
            question_mode=question_mode,
            target_depth=target_depth,
        )
        await session_memory.insert_source_chunks(session_id, source_chunks)
        if source_file_ids:
            await session_memory.purge_source_uploads(session_id, source_file_ids)
        await _save_checkpoint(session_id, content_hash, pipeline_meta=pipeline_meta)

    await emit({"type": "session_generating", "payload": {"session_id": session_id}})

    # D1: pipeline is unified to small-file paths; route by source count alone.
    # single_split + per_source_split partition all valid inputs — no large-file branch.
    n_sources = count_sources(source_chunks)
    single_split = (n_sources <= 1)
    per_source_split = (n_sources > 1)

    required_outline: dict | None = None
    if resuming and checkpoint and checkpoint.get("required_outline") is not None:
        required_outline = checkpoint["required_outline"]
    # P0a: ContentOutline trigger
    # - same_material=False → 不同教材一定跑 Outline 取全局骨架
    # - same_material=True → 一律不跑 Outline（含 ≥3 章）。
    #   Phase 3：原本 n_sources>=3 也跑 Outline，但全局 named_cases 是跨章主題桶，
    #   被各 per-source splitter 共用 → 同主題的不同章 chunk 併進同一 stage
    #   （live sess_f9qt8rac9：7.1=第6+8章）。章節順序已由 SourceOrderResolver
    #   處理，outline 對同教材的唯一價值已被取代，故對齊「保章節邊界」原則不跑。
    run_outline = not same_material
    if required_outline is None and source_chunks and run_outline:
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
            await _save_checkpoint(
                session_id, content_hash,
                pipeline_meta=pipeline_meta,
                required_outline=required_outline,
                meter=meter,
            )
        except Exception as e:
            _log.warning("v2 content_outline failed  session=%s  err=%s", session_id, e)

    max_stages = compute_dynamic_max_stages(
        source_chunks, source_count=n_sources, required_outline=required_outline,
    )

    all_candidates: list[dict] = (
        list(checkpoint.get("all_candidates") or []) if resuming and checkpoint else []
    )
    summary_parts: list[str] = (
        list(checkpoint.get("summary_parts") or []) if resuming and checkpoint else []
    )
    saved_path = (checkpoint or {}).get("pipeline_meta", {}).get("pipeline_path")

    if single_split:
        if resuming and saved_path == "single_split" and all_candidates:
            _log.info("v2 checkpoint skip single_split  session=%s", session_id)
        else:
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
            await _save_checkpoint(
                session_id, content_hash,
                pipeline_meta={**pipeline_meta, "pipeline_path": "single_split"},
                required_outline=required_outline,
                all_candidates=all_candidates,
                summary_parts=summary_parts,
                completed_region_ids=["__single_split__"],
                meter=meter,
            )
    elif per_source_split:
        if resuming and saved_path == "per_source_split" and all_candidates:
            _log.info("v2 checkpoint skip per_source_split  session=%s", session_id)
        else:
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
            await _save_checkpoint(
                session_id, content_hash,
                pipeline_meta={**pipeline_meta, "pipeline_path": "per_source_split"},
                required_outline=required_outline,
                all_candidates=all_candidates,
                summary_parts=summary_parts,
                completed_region_ids=["__per_source_split__"],
                meter=meter,
            )
    # D1: single_split + per_source_split partition all valid inputs.
    # The previous large-file else-branch (MacroRegionPlanner + per-region loop
    # + GlobalCurriculumReducer + Plan B) has been removed.

    all_candidates = _dedupe_candidates(all_candidates, same_material=same_material)

    chunks_lookup = {
        c["chunk_id"]: c.get("text", "")
        for c in source_chunks
        if isinstance(c, dict) and c.get("chunk_id")
    }

    quality_warnings: dict | None = None
    stages: list[dict]
    reduce_metrics: dict = {
        "candidate_count": len(all_candidates),
        "outcome_count": len(all_candidates),
        "unsure_pair_count": 0,
        "llm_outcome_count": 0,
    }

    # D1: small-file unified path — flatten candidates directly, no reducer.
    stages = candidates_to_stages_flat(all_candidates, chunks_lookup)
    quality_warnings = {"small_file_path": True, "reducer_skipped": True}
    if per_source_split:
        quality_warnings["multi_source_split"] = True
        quality_warnings["source_count"] = n_sources

    # Phase 1: mode-aware postprocessing. single source / same_material=True
    # preserve splitter boundaries — only cross-material (multi source +
    # same_material=False) runs the stage-merge passes + LLM consolidator.
    # Deterministic ordering still runs in every mode (it reorders, never merges).
    postprocess_mode = choose_postprocess_mode(n_sources, same_material)
    quality_warnings["postprocess_mode"] = postprocess_mode
    if order_decision is not None:
        quality_warnings["source_order"] = order_decision
    # Phase 3：透明化「同教材多章刻意不跑 Outline」的保守決策（見 P0a 註解）。
    if same_material and n_sources >= 3:
        quality_warnings["outline_skipped_same_material"] = {
            "reason": "same_material_multi_chapter",
            "n_sources": n_sources,
            "note": "chapter order handled by SourceOrderResolver",
        }
    allow_merge = postprocess_mode == "cross_material_merge_and_coordinate"

    stages = normalize_stages_pre_verify(stages, source_chunks)

    # P0b-1: cross-source merge by key_concepts jaccard (default ≥ 0.6).
    # Catches per-source splitter naming drift across chapters where titles
    # diverge but concepts overlap heavily. Cross-material only.
    if allow_merge:
        stages_before_jaccard = len(stages)
        stages = merge_by_concept_overlap(stages)
        if len(stages) < stages_before_jaccard:
            merged_count = stages_before_jaccard - len(stages)
            quality_warnings["concept_overlap_merged"] = merged_count
            _log.info(
                "v2 jaccard merge  session=%s  before=%d  after=%d  merged=%d",
                session_id, stages_before_jaccard, len(stages), merged_count,
            )

    # P3b: deterministic ordering pre-consolidator. Ensures even when
    # consolidator is skipped (chunks < 30 or non-cross mode) reading order
    # is enforced. Runs in every mode — it reorders, never merges.
    stages = enforce_stage_ordering(stages)
    # P4c: middle singleton stages folded into previous neighbour. Runs in every
    # mode — this is thin-stage cleanup (a 1-chunk stage is too thin for a
    # teaching round), not a semantic merge, so it does not erase splitter intent.
    stages = merge_singleton_chunk_stages(stages)

    # P0b-2: LLM Stage Consolidator for long materials (≥ 30 chunks).
    # Per-source splitter has no global view; this pass does cross-chapter
    # rename + reorder + semantic merge that jaccard can't reach. Cross-material
    # only — same-material coordination is deferred to Phase 3's coordinator.
    if allow_merge and len(source_chunks) >= 30 and len(stages) >= 2:
        try:
            consolidator_ctx = AgentContext(
                session_id=session_id, user_id=user_id,
                task_payload={
                    "stages": stages,
                    "required_outline": required_outline,
                    "sources_manifest": sources_manifest,
                },
            )
            cons_result = await orch.stage_consolidator.run(consolidator_ctx)
            meter.record("StageConsolidatorAgent")
            if not cons_result.get("fallback") and not cons_result.get("skipped"):
                stages_before_cons = len(stages)
                stages = cons_result["stages"]
                # P3b: re-enforce ordering after LLM (rule F not always honoured)
                stages = enforce_stage_ordering(stages)
                # P4c: collapse middle 1-chunk stages produced by consolidator
                stages = merge_singleton_chunk_stages(stages)
                quality_warnings["stage_consolidator_ran"] = True
                _log.info(
                    "v2 stage consolidator  session=%s  before=%d  after=%d",
                    session_id, stages_before_cons, len(stages),
                )
            elif cons_result.get("fallback"):
                quality_warnings["stage_consolidator_fallback"] = cons_result.get("reason") or "unknown"
        except Exception as e:
            _log.warning(
                "v2 stage consolidator failed  session=%s  err=%s",
                session_id, e,
            )
            quality_warnings["stage_consolidator_error"] = str(e)

    from ..utils.curriculum_health import assess_reducer_health

    health = assess_reducer_health(
        session_id=session_id,
        candidate_count=reduce_metrics["candidate_count"],
        outcome_count=reduce_metrics["outcome_count"],
        stage_count=len(stages),
        unsure_pair_count=reduce_metrics["unsure_pair_count"],
        llm_outcome_count=reduce_metrics["llm_outcome_count"],
        quality_warnings=quality_warnings,
        plan_b_active=False,
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

        # Phase 1 refinement: fold interior orphans (chunks sitting inside the
        # reading span) into the neighbouring stage first, so only genuinely
        # trailing orphans reach the generic summary path below.
        folded = fold_interior_orphan_chunks(stages, source_chunks)
        if folded is not stages:
            stages = folded
            gverify = verify_global_coverage(stages, source_chunks, required_outline)
            quality_warnings = {
                **(quality_warnings or {}),
                "interior_orphans_folded": True,
            }
            _log.info(
                "v2 interior orphan fold  session=%s  aligned_after=%s",
                session_id, gverify.get("aligned"),
            )

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
    else:
        # 非 compact 路徑：確定性收尾一律跑（不再綁 `not aligned`）。aligned-within-
        # tolerance 的課綱也會有 orphan / kc 過量，舊版在此靜默漏掉。語意合併不在此。
        stages = normalize_stages_pre_verify(stages, source_chunks)
        stages = _apply_deterministic_cleanup(
            stages, source_chunks, required_outline, quality_warnings, session_id,
        )

    if is_listicle_source(source_chunks):
        stages = prune_toc_listicle_chunks(stages, source_chunks)
        orphan_after = verify_global_coverage(stages, source_chunks, required_outline)
        if orphan_after.get("orphan_chunk_ids"):
            stages = ensure_orphan_chunks_attached(stages, source_chunks)
            stages = split_oversized_stages(stages, source_chunks)
            stages = split_kc_heavy_stages(stages, source_chunks)
            stages = trim_stage_key_concepts(stages)

    # Issue B: title-only orphan-enumerator cleanup. Runs at the single finalize
    # convergence point (after both compact and non-compact branches + listicle
    # pruning) so every same_material path — including the compact small-file path
    # that bypasses _apply_deterministic_cleanup — receives it. same_material only:
    # cross_material gets global naming coordination elsewhere (Phase 4/5).
    if same_material:
        stages, title_cleanup_warnings = cleanup_orphan_enumerator_titles(stages)
        if title_cleanup_warnings:
            quality_warnings = {
                **(quality_warnings or {}),
                "title_cleanup_removed_orphan_enumerators": len(title_cleanup_warnings),
            }
            _log.info(
                "v2 title cleanup removed orphan enumerators  session=%s  count=%d",
                session_id, len(title_cleanup_warnings),
            )
            for w in title_cleanup_warnings:
                _log.debug(
                    "v2 title cleanup detail  session=%s  stage=%s  old=%r  new=%r  pattern=%s",
                    session_id, w.get("stage_id"), w.get("old_title"),
                    w.get("new_title"), w.get("pattern"),
                )

    stages = finalize_curriculum_stages(stages, source_chunks)

    # Phase 4 / T4c+T4e: cross-material pedagogical reorder behind a default-off
    # feature flag. Flag off → no-op (bit-for-bit). T4e: runs AFTER finalization
    # so finalize's reading-order sort (sort_stages_by_chunk_order) cannot clobber
    # an applied pedagogical order. When a plan is applied, renumber stage_id /
    # node_id to the new order before persistence; off / diagnostics_only /
    # fallback / error_fallback leave the finalized order untouched.
    stages = await _maybe_apply_cross_material_pedagogical_planner(
        session_id=session_id,
        stages=stages,
        chunks=source_chunks,
        same_material=same_material,
        planner_agent=orch.pedagogical_planner,
        quality_warnings=quality_warnings,
        meter=meter,
    )
    _planner_warning = (quality_warnings or {}).get(
        "cross_material_pedagogical_planner"
    ) or {}
    if _planner_warning.get("planner_mode") == "applied":
        stages = _renumber_finalized_stages_after_pedagogical_reorder(stages)
        _planner_warning["renumbered_after_apply"] = True

    new_concepts = sorted({c for s in stages for c in s.get("key_concepts", [])})
    if _canonicalize_enabled() and content_hash and new_concepts:
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

    # PR1b: warn-only audit of the final (post-canonicalize) key_concepts that
    # will be persisted / used by QG. Never mutates stages; only appends to
    # quality_warnings["key_concept_hygiene"].
    quality_warnings = _merge_key_concept_hygiene_warnings(stages, quality_warnings)
    if quality_warnings and quality_warnings.get("key_concept_hygiene"):
        _log.info(
            "v2 key concept hygiene  session=%s  warnings=%d",
            session_id, len(quality_warnings["key_concept_hygiene"]),
        )

    nodes = [
        {"node_id": s["node_id"], "stage_id": s["stage_id"], "title": s["title"]}
        for s in stages
    ]
    summary = _build_knowledge_map_summary(summary_parts, len(stages))
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

    await ckpt_mem.delete_checkpoint(session_id)

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
