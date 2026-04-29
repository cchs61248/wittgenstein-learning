from ..memory import session_memory, longterm_memory


async def build_adaptive_context(
    session_id: str,
    user_id: str,
    stage: dict,
    current_attempt: int,
    stages: list[dict],
) -> dict:
    """
    組裝 AdaptiveLessonContext，在每次 TeacherAgent 呼叫前執行。
    包含：allowed_evidence、learner_state、next_lesson_requirements、source_constraints。
    """
    # 1. 取此 stage 的 source_chunks（allowed_evidence）
    chunk_ids: list[str] = stage.get("source_chunk_ids") or []
    if not chunk_ids:
        chunk_ids = [
            c["chunk_id"]
            for c in (stage.get("source_chunks") or [])
            if isinstance(c, dict) and c.get("chunk_id")
        ]
    allowed_chunks = await session_memory.get_source_chunks(session_id, chunk_ids) if chunk_ids else []

    # 2. 取掌握度
    key_concepts: list[str] = stage.get("key_concepts", [])
    mastery_map = await longterm_memory.get_concept_mastery_map(user_id, key_concepts)

    # 3. 取結構化混淆模式
    misconceptions = await longterm_memory.get_misconceptions(user_id, key_concepts)

    # 4. 取最近問答摘要
    recent_qa = await session_memory.get_recent_qa_summary(session_id, max_items=5)

    # 5. 取最後決策記錄（含選課理由）
    last_decision = await session_memory.get_last_decision_record(session_id)

    # 6. 計算禁止提前教的概念（後續節點的概念，排除本節已有）
    current_idx = next(
        (i for i, s in enumerate(stages) if s["stage_id"] == stage["stage_id"]), 0
    )
    future_concepts = list(dict.fromkeys([
        c
        for s in stages[current_idx + 1:]
        for c in s.get("key_concepts", [])
        if c not in key_concepts
    ]))[:10]

    # 7. 計算本節必須補強的概念（掌握度 < 0.75 或本次嘗試 > 1）
    must_reinforce = [c for c in key_concepts if mastery_map.get(c, 0.5) < 0.75]
    if current_attempt > 1 and last_decision:
        focus = (last_decision.get("strategy_snapshot") or {}).get("remediation_focus") or []
        for c in focus:
            if c not in must_reinforce:
                must_reinforce.append(c)

    # 8. 讀取選課理由（Phase 4：由 Orchestrator 在選課後存入 strategy_snapshot）
    selection_reason = (last_decision.get("strategy_snapshot") or {}).get("selection_reason") if last_decision else None

    return {
        "stage": stage,
        "current_attempt": current_attempt,
        "allowed_evidence": allowed_chunks,
        "learner_state": {
            "mastery_map": mastery_map,
            "misconceptions": misconceptions,
            "recent_qa_summary": recent_qa,
        },
        "next_lesson_requirements": {
            "must_reinforce": must_reinforce,
            "forbidden_future_concepts": future_concepts,
            "selection_reason": selection_reason,
        },
        "source_constraints": {
            "must_cite_chunks": True,
            "no_external_claims": True,
            "forbidden_future_concepts": future_concepts,
        },
    }
