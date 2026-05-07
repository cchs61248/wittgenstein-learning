import json
from datetime import datetime
from typing import Optional
from ..db.database import get_db

DECISION_HISTORY_MAX_PER_SESSION = 200


async def create_session(
    session_id: str,
    user_id: str,
    content_hash: str,
    total_stages: int,
    raw_content_summary: str = "",
) -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO sessions
           (session_id, user_id, content_hash, total_stages, raw_content_summary)
           VALUES (?, ?, ?, ?, ?)""",
        (session_id, user_id, content_hash, total_stages, raw_content_summary),
    )
    await db.commit()


async def store_stages(session_id: str, stages: list[dict]) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE sessions SET stages_json = ? WHERE session_id = ?",
        (json.dumps(stages, ensure_ascii=False), session_id),
    )
    await db.commit()


async def get_session(session_id: str) -> Optional[dict]:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def create_generating_stub(
    session_id: str, user_id: str, content_hash: str
) -> None:
    """ContentSplitter 執行前建立佔位記錄，讓書櫃在 LLM 呼叫期間持久顯示「生成中」。"""
    db = await get_db()
    await db.execute(
        """INSERT OR IGNORE INTO sessions
           (session_id, user_id, content_hash, total_stages, status, title)
           VALUES (?, ?, ?, 0, 'generating', '生成中…')""",
        (session_id, user_id, content_hash),
    )
    await db.commit()


async def abandon_generating_stub(session_id: str) -> None:
    """ContentSplitter 失敗時，將 generating 佔位標記為 abandoned。"""
    db = await get_db()
    await db.execute(
        "UPDATE sessions SET status = 'abandoned' WHERE session_id = ? AND status = 'generating'",
        (session_id,),
    )
    await db.commit()


async def create_pending_session(
    session_id: str,
    user_id: str,
    content_hash: str,
    summary: str,
    stages: list[dict],
    nodes: list[dict],
    provider_name: str | None = None,
    model_name: str | None = None,
    question_mode: str = "short_answer",
) -> None:
    db = await get_db()
    pending_map = {"nodes": nodes, "summary": summary}
    # UPSERT：若 session 已以 generating stub 存在，直接更新為 pending_confirmation
    await db.execute(
        """INSERT INTO sessions
           (session_id, user_id, content_hash, total_stages, raw_content_summary,
            status, stages_json, pending_map_json, provider_name, model_name, question_mode, title)
           VALUES (?, ?, ?, ?, ?, 'pending_confirmation', ?, ?, ?, ?, ?, ?)
           ON CONFLICT(session_id) DO UPDATE SET
             content_hash=excluded.content_hash,
             total_stages=excluded.total_stages,
             raw_content_summary=excluded.raw_content_summary,
             status='pending_confirmation',
             stages_json=excluded.stages_json,
             pending_map_json=excluded.pending_map_json,
             provider_name=excluded.provider_name,
             model_name=excluded.model_name,
             question_mode=excluded.question_mode,
             title=excluded.title,
             updated_at=CURRENT_TIMESTAMP""",
        (
            session_id, user_id, content_hash, len(stages),
            summary,
            json.dumps(stages, ensure_ascii=False),
            json.dumps(pending_map, ensure_ascii=False),
            provider_name,
            model_name,
            question_mode,
            summary,
        ),
    )
    await db.commit()


async def activate_pending_session(session_id: str) -> None:
    db = await get_db()
    await db.execute(
        """UPDATE sessions
           SET status = 'active', pending_map_json = NULL, updated_at = ?
           WHERE session_id = ?""",
        (datetime.utcnow(), session_id),
    )
    await db.commit()


async def get_user_active_session(user_id: str) -> Optional[dict]:
    db = await get_db()
    async with db.execute(
        """SELECT * FROM sessions
           WHERE user_id = ? AND status IN ('active', 'pending_confirmation')
           ORDER BY updated_at DESC LIMIT 1""",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def complete_session(session_id: str) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE sessions SET status = 'completed', updated_at = ? WHERE session_id = ?",
        (datetime.utcnow(), session_id),
    )
    await db.commit()


async def get_stage_statuses(session_id: str) -> dict[int, str]:
    db = await get_db()
    async with db.execute(
        "SELECT stage_id, status FROM stage_progress WHERE session_id = ?",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    return {row["stage_id"]: row["status"] for row in rows}


async def store_stage_explanation(session_id: str, stage_id: int, full_explanation: str) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE stage_progress SET full_explanation = ? WHERE session_id = ? AND stage_id = ?",
        (full_explanation, session_id, stage_id),
    )
    await db.commit()


async def get_stage_explanation(session_id: str, stage_id: int) -> str:
    db = await get_db()
    async with db.execute(
        "SELECT full_explanation FROM stage_progress WHERE session_id = ? AND stage_id = ?",
        (session_id, stage_id),
    ) as cur:
        row = await cur.fetchone()
    return (row["full_explanation"] or "") if row else ""


async def store_stage_questions(session_id: str, stage_id: int, questions: list[dict]) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE stage_progress SET questions_json = ? WHERE session_id = ? AND stage_id = ?",
        (json.dumps(questions, ensure_ascii=False), session_id, stage_id),
    )
    await db.commit()


async def get_stage_questions(session_id: str, stage_id: int) -> list[dict]:
    db = await get_db()
    async with db.execute(
        "SELECT questions_json FROM stage_progress WHERE session_id = ? AND stage_id = ?",
        (session_id, stage_id),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return []
    return json.loads(row["questions_json"] or "[]")


async def update_current_stage(session_id: str, stage_id: int) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE sessions SET current_stage_id = ?, updated_at = ? WHERE session_id = ?",
        (stage_id, datetime.utcnow(), session_id),
    )
    await db.commit()


async def upsert_stage_progress(
    session_id: str,
    stage_id: int,
    status: str,
    attempts: int,
    best_score: float,
    understanding_notes: dict,
    completed_at: Optional[datetime] = None,
) -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO stage_progress
           (session_id, stage_id, status, attempts, best_score, understanding_notes, completed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(session_id, stage_id) DO UPDATE SET
             status=excluded.status,
             attempts=excluded.attempts,
             best_score=excluded.best_score,
             understanding_notes=excluded.understanding_notes,
             completed_at=excluded.completed_at""",
        (
            session_id,
            stage_id,
            status,
            attempts,
            best_score,
            json.dumps(understanding_notes, ensure_ascii=False),
            completed_at,
        ),
    )
    await db.commit()


async def get_stage_progress(session_id: str, stage_id: int) -> dict | None:
    db = await get_db()
    async with db.execute(
        """SELECT status, attempts, best_score, understanding_notes
           FROM stage_progress WHERE session_id = ? AND stage_id = ?""",
        (session_id, stage_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    d = dict(row)
    try:
        d["understanding_notes"] = json.loads(d["understanding_notes"] or "{}")
    except Exception:
        d["understanding_notes"] = {}
    return d


async def update_stage_attempt(session_id: str, stage_id: int, attempt: int) -> None:
    """retry 決策後立即更新輪次，讓 resume 能還原正確 current_attempt。"""
    db = await get_db()
    await db.execute(
        "UPDATE stage_progress SET attempts = ? WHERE session_id = ? AND stage_id = ?",
        (attempt, session_id, stage_id),
    )
    await db.commit()


async def get_stage_qa_records(session_id: str, stage_id: int) -> list[dict]:
    db = await get_db()
    async with db.execute(
        """SELECT question_id, question_text, question_type, user_answer, score, feedback
           FROM qa_records WHERE session_id = ? AND stage_id = ?
           ORDER BY id""",
        (session_id, stage_id),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_all_stage_qa_records(session_id: str) -> dict[int, list[dict]]:
    db = await get_db()
    async with db.execute(
        """SELECT stage_id, question_id, question_text, question_type, user_answer, score, feedback
           FROM qa_records WHERE session_id = ?
           ORDER BY stage_id, id""",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        r = dict(row)
        sid = int(r["stage_id"])
        grouped.setdefault(sid, []).append(r)
    return grouped


async def get_all_stage_explanations(session_id: str) -> dict[int, str]:
    db = await get_db()
    async with db.execute(
        """SELECT stage_id, full_explanation FROM stage_progress
           WHERE session_id = ? AND full_explanation IS NOT NULL AND full_explanation != ''""",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    return {int(row["stage_id"]): row["full_explanation"] for row in rows}


async def insert_decision_record(
    session_id: str,
    stage_id: int,
    decision: str,
    best_score: float,
    next_stage_id: int | None,
    next_stage_score: float | None,
    reason_lines: list[str],
    strategy_snapshot: dict,
) -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO decision_records
           (session_id, stage_id, decision, best_score, next_stage_id, next_stage_score,
            reason_lines_json, strategy_snapshot_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            stage_id,
            decision,
            best_score,
            next_stage_id,
            next_stage_score,
            json.dumps(reason_lines, ensure_ascii=False),
            json.dumps(strategy_snapshot, ensure_ascii=False),
        ),
    )
    # 每個 session 只保留最近 N 筆決策歷史，避免表無限成長
    await db.execute(
        """DELETE FROM decision_records
           WHERE session_id = ?
             AND id NOT IN (
               SELECT id FROM decision_records
               WHERE session_id = ?
               ORDER BY id DESC
               LIMIT ?
             )""",
        (session_id, session_id, DECISION_HISTORY_MAX_PER_SESSION),
    )
    await db.commit()


async def get_decision_records(
    session_id: str,
    limit: int = DECISION_HISTORY_MAX_PER_SESSION,
) -> list[dict]:
    db = await get_db()
    async with db.execute(
        """SELECT stage_id, decision, best_score, next_stage_id, next_stage_score,
                  reason_lines_json, strategy_snapshot_json, created_at
           FROM decision_records
           WHERE session_id = ?
           ORDER BY id ASC
           LIMIT ?""",
        (session_id, limit),
    ) as cur:
        rows = await cur.fetchall()
    records: list[dict] = []
    for row in rows:
        records.append(
            {
                "stage_id": row["stage_id"],
                "decision": row["decision"],
                "best_score": row["best_score"],
                "next_stage_id": row["next_stage_id"],
                "next_stage_score": row["next_stage_score"],
                "reason_lines": json.loads(row["reason_lines_json"] or "[]"),
                "strategy_snapshot": json.loads(row["strategy_snapshot_json"] or "{}"),
                "created_at": row["created_at"],
            }
        )
    return records


async def insert_source_chunks(session_id: str, chunks: list[dict]) -> None:
    """將本地切好的 source_chunks 存入 DB（教材 source truth）。"""
    db = await get_db()
    await db.executemany(
        """INSERT OR REPLACE INTO source_chunks
           (chunk_id, session_id, order_index, text, section_title, char_start, char_end)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                c["chunk_id"],
                session_id,
                c["order_index"],
                c["text"],
                c.get("section_title"),
                c.get("char_start"),
                c.get("char_end"),
            )
            for c in chunks
        ],
    )
    await db.commit()


async def get_source_chunks(
    session_id: str,
    chunk_ids: list[str] | None = None,
) -> list[dict]:
    """
    取得 session 的 source_chunks。
    chunk_ids 為 None 時回傳全部；指定時只回傳對應的 chunks。
    """
    db = await get_db()
    if chunk_ids is None:
        async with db.execute(
            "SELECT * FROM source_chunks WHERE session_id = ? ORDER BY order_index",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
    else:
        if not chunk_ids:
            return []
        placeholders = ",".join("?" * len(chunk_ids))
        async with db.execute(
            f"SELECT * FROM source_chunks WHERE session_id = ? AND chunk_id IN ({placeholders}) ORDER BY order_index",
            (session_id, *chunk_ids),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def get_recent_qa_summary(session_id: str, max_items: int = 5) -> list[dict]:
    """取得最近 N 筆問答記錄摘要（供 ContextBuilder 使用）。"""
    db = await get_db()
    async with db.execute(
        """SELECT stage_id, question_text, user_answer, score
           FROM qa_records WHERE session_id = ?
           ORDER BY id DESC LIMIT ?""",
        (session_id, max_items),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(row) for row in reversed(rows)]


async def get_last_decision_record(session_id: str) -> Optional[dict]:
    """取得最後一筆決策記錄（含 strategy_snapshot）。"""
    db = await get_db()
    async with db.execute(
        """SELECT stage_id, decision, best_score, strategy_snapshot_json
           FROM decision_records WHERE session_id = ?
           ORDER BY id DESC LIMIT 1""",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    return {
        "stage_id": row["stage_id"],
        "decision": row["decision"],
        "best_score": row["best_score"],
        "strategy_snapshot": json.loads(row["strategy_snapshot_json"] or "{}"),
    }


async def insert_qa_record(
    session_id: str,
    stage_id: int,
    question_id: str,
    question_text: str,
    question_type: str,
    user_answer: str,
    score: float,
    feedback: str,
) -> None:
    db = await get_db()
    await db.execute(
        """INSERT INTO qa_records
           (session_id, stage_id, question_id, question_text, question_type,
            user_answer, score, feedback)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (session_id, stage_id, question_id, question_text, question_type,
         user_answer, score, feedback),
    )
    await db.commit()


async def get_user_sessions(user_id: str) -> list[dict]:
    """回傳用戶所有書本（排除 abandoned），含完成進度統計。"""
    db = await get_db()
    async with db.execute(
        """SELECT session_id, title, raw_content_summary, status,
                  total_stages, updated_at
           FROM sessions
           WHERE user_id = ? AND status != 'abandoned'
           ORDER BY updated_at DESC""",
        (user_id,),
    ) as cur:
        rows = await cur.fetchall()
    result = []
    for row in rows:
        r = dict(row)
        async with db.execute(
            "SELECT COUNT(*) AS cnt FROM stage_progress WHERE session_id = ? AND status = 'completed'",
            (r["session_id"],),
        ) as cnt_cur:
            cnt_row = await cnt_cur.fetchone()
        result.append({
            "session_id": r["session_id"],
            "title": r["title"] or r["raw_content_summary"] or "未命名學習材料",
            "status": r["status"],
            "total_stages": int(r["total_stages"] or 0),
            "completed_stages": int(cnt_row["cnt"] if cnt_row else 0),
            "updated_at": str(r["updated_at"]) if r["updated_at"] else None,
        })
    return result


async def update_session_title(session_id: str, user_id: str, title: str) -> bool:
    db = await get_db()
    await db.execute(
        "UPDATE sessions SET title = ? WHERE session_id = ? AND user_id = ?",
        (title.strip(), session_id, user_id),
    )
    await db.commit()
    return True


async def delete_session(session_id: str, user_id: str) -> bool:
    """刪除 session 及相關學習記錄，但保留 concept_mastery（學習成效不刪）。"""
    db = await get_db()
    async with db.execute(
        "SELECT session_id FROM sessions WHERE session_id = ? AND user_id = ?",
        (session_id, user_id),
    ) as cur:
        if not await cur.fetchone():
            return False
    for tbl in ("qa_records", "stage_progress", "source_chunks", "decision_records", "tutor_records"):
        await db.execute(f"DELETE FROM {tbl} WHERE session_id = ?", (session_id,))
    await db.execute(
        "DELETE FROM sessions WHERE session_id = ? AND user_id = ?",
        (session_id, user_id),
    )
    await db.commit()
    return True


async def insert_tutor_record(
    session_id: str,
    stage_id: int,
    question: str,
    answer: str,
    in_scope: bool,
) -> int:
    db = await get_db()
    cur = await db.execute(
        """INSERT INTO tutor_records (session_id, stage_id, question, answer, in_scope)
           VALUES (?, ?, ?, ?, ?)""",
        (session_id, stage_id, question, answer, 1 if in_scope else 0),
    )
    await db.commit()
    return cur.lastrowid


async def get_all_tutor_records(session_id: str) -> dict[int, list[dict]]:
    """回傳 session 所有 tutor 問答，以 stage_id 分組，按插入順序排列。"""
    db = await get_db()
    async with db.execute(
        """SELECT id, stage_id, question, answer, in_scope
           FROM tutor_records WHERE session_id = ?
           ORDER BY id ASC""",
        (session_id,),
    ) as cur:
        rows = await cur.fetchall()
    result: dict[int, list[dict]] = {}
    for row in rows:
        sid = row["stage_id"]
        if sid not in result:
            result[sid] = []
        result[sid].append({
            "id": row["id"],
            "question": row["question"],
            "answer": row["answer"],
            "in_scope": bool(row["in_scope"]),
        })
    return result


async def delete_tutor_record(record_id: int, session_id: str) -> bool:
    db = await get_db()
    cur = await db.execute(
        "DELETE FROM tutor_records WHERE id = ? AND session_id = ?",
        (record_id, session_id),
    )
    await db.commit()
    return cur.rowcount > 0
