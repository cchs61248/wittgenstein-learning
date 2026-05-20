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
    # 合併兩部分：
    #   (a) 當前 stage.key_concepts 的 mastery（即使低於 threshold 也要，
    #       供 must_reinforce 判斷使用）
    #   (b) 整個 user 跨 stage 已掌握的高 mastery 概念（≥0.8）
    #       供 QG 個人化過濾使用，避免重出已掌握概念當主要考點
    # stage_mastery 覆蓋 user_mastery 中同名 concept（兩者撈到同個 concept 時數值相同，安全）
    #
    # user_mastery 用 source_signature 過濾：跨教材污染防護。
    # 若 session 沒有 source_file_ids（legacy session），signature=None
    # → get_user_mastery_map 退回 legacy 不過濾行為（保留向後相容）。
    key_concepts: list[str] = stage.get("key_concepts", [])
    source_signature = await session_memory.get_source_signature(session_id)
    user_mastery_map = await longterm_memory.get_user_mastery_map(
        user_id, threshold=0.8, source_signature=source_signature
    )
    stage_mastery_map = await longterm_memory.get_concept_mastery_map(user_id, key_concepts)
    mastery_map = {**user_mastery_map, **stage_mastery_map}

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

    # 6b. 下一節即將教的概念（next_stage_concepts）—— 給 Teacher 跨章節邊界
    #     感知用：source_chunks 可能跨主題（chunker 切到一半含當前 + 下節內容），
    #     Teacher 看到屬於 next_stage_concepts 的段落只能一句帶過、禁止完整展開，
    #     把詳細留給下一節，避免相鄰章節 50%+ 重疊。
    #     DriftVerifier 也吃這個欄位，作為反向 coverage 檢查的豁免清單。
    next_stage_concepts: list[str] = []
    if current_idx + 1 < len(stages):
        next_stage_concepts = [
            c for c in (stages[current_idx + 1].get("key_concepts") or [])
            if c not in key_concepts
        ]

    # 7. 計算本節必須補強的概念。觸發條件（任一即可）：
    #    a) 掌握度 < 0.75（基準閾值，初學或答錯多次）
    #    b) 有未消除的 confusion pattern（即使 mastery>=0.75 也要補，
    #       因為 misconception 累積在 DB 表示學生對該概念仍有理解錯誤）
    #    c) 上一輪 remediation_focus 中的概念（current_attempt > 1）
    must_reinforce: list[str] = []
    misconception_concepts = {m.get("concept") for m in misconceptions if m.get("concept")}
    for c in key_concepts:
        if mastery_map.get(c, 0.5) < 0.75:
            must_reinforce.append(c)
        elif c in misconception_concepts:
            must_reinforce.append(c)
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
            "next_stage_concepts": next_stage_concepts,
            "selection_reason": selection_reason,
        },
        "source_constraints": {
            "must_cite_chunks": True,
            "no_external_claims": True,
            "forbidden_future_concepts": future_concepts,
        },
    }
