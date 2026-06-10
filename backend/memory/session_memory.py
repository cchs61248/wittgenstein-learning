import json
from datetime import datetime, timezone
from typing import Optional
from ..db.database import get_db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

DECISION_HISTORY_MAX_PER_SESSION = 200


async def store_stages(session_id: str, stages: list[dict]) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE sessions SET stages_json = $1 WHERE session_id = $2",
        json.dumps(stages, ensure_ascii=False), session_id,
    )


async def get_session(session_id: str) -> Optional[dict]:
    db = await get_db()
    row = await db.fetchrow(
        "SELECT * FROM sessions WHERE session_id = $1", session_id
    )
    return dict(row) if row else None


async def get_source_signature(session_id: str) -> str | None:
    """回傳 session 的教材出處 signature，用於跨教材 mastery 隔離。

    優先使用 content_hash（同教材重傳 file_id 變了仍穩定）；
    若無 content_hash 則 fallback 至 sorted(source_file_ids) join '|'（legacy）。
    皆無 → None（QG 退回不過濾的 legacy 行為）。
    """
    db = await get_db()
    row = await db.fetchrow(
        "SELECT content_hash, source_file_ids_json FROM sessions WHERE session_id = $1",
        session_id,
    )
    if not row:
        return None
    content_hash = (row[0] or "").strip() if row[0] is not None else ""
    if content_hash:
        return content_hash
    raw_ids = row[1] if len(row) > 1 else None
    if not raw_ids:
        return None
    try:
        file_ids = json.loads(raw_ids)
    except Exception:
        return None
    if not isinstance(file_ids, list) or not file_ids:
        return None
    cleaned = sorted(str(fid) for fid in file_ids if fid)
    return "|".join(cleaned) if cleaned else None


async def create_generating_stub(
    session_id: str,
    user_id: str,
    content_hash: str,
    source_file_ids: list[str] | None = None,
    sources_json: list[dict] | None = None,
    provider_name: str | None = None,
    model_name: str | None = None,
    question_mode: str | None = None,
    target_depth: str | None = None,
    same_material: bool | None = None,
) -> None:
    """ContentSplitter 執行前建立佔位記錄，讓書櫃在 LLM 呼叫期間持久顯示「生成中」。

    `same_material`：使用者「是否同教材」的選擇，供 resume 流程還原。
    None = legacy 未紀錄；True/False = 使用者明確選擇。
    """
    db = await get_db()
    file_ids_json = json.dumps(source_file_ids or [], ensure_ascii=False)
    sources_json_str = json.dumps(sources_json or [], ensure_ascii=False)
    # 1/0/NULL：None 代表呼叫端沒提供（保留 NULL）
    same_material_val = None if same_material is None else (1 if same_material else 0)
    async with db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO sessions
                   (session_id, user_id, content_hash, total_stages, status, title,
                    source_file_ids_json, sources_json, provider_name, model_name,
                    question_mode, target_depth, same_material)
                   VALUES ($1, $2, $3, 0, 'generating', '生成中…', $4, $5, $6, $7, $8, $9, $10)
                   ON CONFLICT (session_id) DO NOTHING""",
                session_id, user_id, content_hash, file_ids_json, sources_json_str,
                provider_name, model_name, question_mode, target_depth, same_material_val,
            )
            # 若 stub 已存在（重試），補寫 file_ids 與 start 參數
            await conn.execute(
                """UPDATE sessions
                   SET source_file_ids_json = $1,
                       sources_json = COALESCE($2, sources_json),
                       provider_name = COALESCE($3, provider_name),
                       model_name = COALESCE($4, model_name),
                       question_mode = COALESCE($5, question_mode),
                       target_depth = COALESCE($6, target_depth),
                       same_material = COALESCE($7, same_material)
                   WHERE session_id = $8 AND status = 'generating'""",
                file_ids_json,
                sources_json_str if sources_json else None,
                provider_name,
                model_name,
                question_mode,
                target_depth,
                same_material_val,
                session_id,
            )


async def _delete_session_upload_blobs(session_id: str) -> list[str]:
    """讀取 session 的 file_ids 並刪除磁碟 blob；回傳已刪除的 id 列表。"""
    from ..files.upload_store import delete_upload

    db = await get_db()
    row = await db.fetchrow(
        "SELECT source_file_ids_json FROM sessions WHERE session_id = $1",
        session_id,
    )
    if not row:
        return []
    try:
        file_ids = json.loads(row[0] or "[]")
    except Exception:
        file_ids = []
    deleted: list[str] = []
    for fid in file_ids if isinstance(file_ids, list) else []:
        if isinstance(fid, str) and delete_upload(fid):
            deleted.append(fid)
    return deleted


async def abandon_generating_stub(session_id: str) -> None:
    """ContentSplitter 失敗或取消時，標記 abandoned 並 GC 關聯 upload。"""
    db = await get_db()
    row = await db.fetchrow(
        "SELECT status FROM sessions WHERE session_id = $1",
        session_id,
    )
    if not row or row[0] != "generating":
        return

    await _delete_session_upload_blobs(session_id)
    from . import curriculum_checkpoint as ckpt
    await ckpt.delete_checkpoint(session_id)

    async with db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM source_chunks WHERE session_id = $1", session_id
            )
            await conn.execute(
                """UPDATE sessions
                   SET status = 'abandoned', source_file_ids_json = '[]'
                   WHERE session_id = $1 AND status = 'generating'""",
                session_id,
            )


async def abandon_failed_session(session_id: str) -> None:
    """使用者移除一個 watchdog-failed session：清理並標 abandoned。"""
    db = await get_db()
    row = await db.fetchrow(
        "SELECT status FROM sessions WHERE session_id = $1", session_id
    )
    if not row or row["status"] != "failed":
        return
    await _delete_session_upload_blobs(session_id)
    from . import curriculum_checkpoint as ckpt
    await ckpt.delete_checkpoint(session_id)

    async with db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM source_chunks WHERE session_id = $1", session_id
            )
            await conn.execute(
                """UPDATE sessions
                   SET status = 'abandoned', source_file_ids_json = '[]'
                   WHERE session_id = $1 AND status = 'failed'""",
                session_id,
            )


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
    source_file_ids: list[str] | None = None,
    quality_warnings: dict | None = None,
) -> None:
    db = await get_db()
    pending_map: dict = {"nodes": nodes, "summary": summary}
    if quality_warnings:
        pending_map["quality_warnings"] = quality_warnings
    file_ids_json = json.dumps(source_file_ids or [], ensure_ascii=False)
    # UPSERT：若 session 已以 generating stub 存在，直接更新為 pending_confirmation
    await db.execute(
        """INSERT INTO sessions
           (session_id, user_id, content_hash, total_stages, raw_content_summary,
            status, stages_json, pending_map_json, provider_name, model_name,
            question_mode, title, source_file_ids_json)
           VALUES ($1, $2, $3, $4, $5, 'pending_confirmation', $6, $7, $8, $9, $10, $11, $12)
           ON CONFLICT(session_id) DO UPDATE SET
             content_hash=EXCLUDED.content_hash,
             total_stages=EXCLUDED.total_stages,
             raw_content_summary=EXCLUDED.raw_content_summary,
             status='pending_confirmation',
             stages_json=EXCLUDED.stages_json,
             pending_map_json=EXCLUDED.pending_map_json,
             provider_name=EXCLUDED.provider_name,
             model_name=EXCLUDED.model_name,
             question_mode=EXCLUDED.question_mode,
             title=EXCLUDED.title,
             source_file_ids_json=EXCLUDED.source_file_ids_json,
             updated_at=CURRENT_TIMESTAMP""",
        session_id, user_id, content_hash, len(stages),
        summary,
        json.dumps(stages, ensure_ascii=False),
        json.dumps(pending_map, ensure_ascii=False),
        provider_name,
        model_name,
        question_mode,
        summary,
        file_ids_json,
    )


async def activate_pending_session(session_id: str) -> None:
    db = await get_db()
    await db.execute(
        """UPDATE sessions
           SET status = 'active', pending_map_json = NULL, updated_at = $1
           WHERE session_id = $2""",
        _utcnow(), session_id,
    )


async def get_user_active_session(user_id: str) -> Optional[dict]:
    db = await get_db()
    row = await db.fetchrow(
        """SELECT * FROM sessions
           WHERE user_id = $1 AND status IN ('active', 'pending_confirmation')
           ORDER BY updated_at DESC LIMIT 1""",
        user_id,
    )
    return dict(row) if row else None


async def complete_session(session_id: str) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE sessions SET status = 'completed', updated_at = $1 WHERE session_id = $2",
        _utcnow(), session_id,
    )


async def get_stage_statuses(session_id: str) -> dict[int, str]:
    db = await get_db()
    rows = await db.fetch(
        "SELECT stage_id, status FROM stage_progress WHERE session_id = $1",
        session_id,
    )
    return {row["stage_id"]: row["status"] for row in rows}


async def store_stage_explanation(session_id: str, stage_id: int, full_explanation: str) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE stage_progress SET full_explanation = $1 WHERE session_id = $2 AND stage_id = $3",
        full_explanation, session_id, stage_id,
    )


async def get_stage_explanation(session_id: str, stage_id: int) -> str:
    db = await get_db()
    row = await db.fetchrow(
        "SELECT full_explanation FROM stage_progress WHERE session_id = $1 AND stage_id = $2",
        session_id, stage_id,
    )
    return (row["full_explanation"] or "") if row else ""


async def store_stage_questions(session_id: str, stage_id: int, questions: list[dict]) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE stage_progress SET questions_json = $1 WHERE session_id = $2 AND stage_id = $3",
        json.dumps(questions, ensure_ascii=False), session_id, stage_id,
    )


async def get_stage_questions(session_id: str, stage_id: int) -> list[dict]:
    db = await get_db()
    row = await db.fetchrow(
        "SELECT questions_json FROM stage_progress WHERE session_id = $1 AND stage_id = $2",
        session_id, stage_id,
    )
    if not row:
        return []
    return json.loads(row["questions_json"] or "[]")


async def update_current_stage(session_id: str, stage_id: int) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE sessions SET current_stage_id = $1, updated_at = $2 WHERE session_id = $3",
        stage_id, _utcnow(), session_id,
    )


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
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           ON CONFLICT(session_id, stage_id) DO UPDATE SET
             status=EXCLUDED.status,
             attempts=EXCLUDED.attempts,
             best_score=EXCLUDED.best_score,
             understanding_notes=EXCLUDED.understanding_notes,
             completed_at=EXCLUDED.completed_at""",
        session_id,
        stage_id,
        status,
        attempts,
        best_score,
        json.dumps(understanding_notes, ensure_ascii=False),
        completed_at,
    )


async def get_stage_progress(session_id: str, stage_id: int) -> dict | None:
    db = await get_db()
    row = await db.fetchrow(
        """SELECT status, attempts, best_score, understanding_notes
           FROM stage_progress WHERE session_id = $1 AND stage_id = $2""",
        session_id, stage_id,
    )
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
        "UPDATE stage_progress SET attempts = $1 WHERE session_id = $2 AND stage_id = $3",
        attempt, session_id, stage_id,
    )


async def get_stage_qa_records(session_id: str, stage_id: int) -> list[dict]:
    db = await get_db()
    rows = await db.fetch(
        """SELECT question_id, question_text, question_type, user_answer, score, feedback
           FROM qa_records WHERE session_id = $1 AND stage_id = $2
           ORDER BY id""",
        session_id, stage_id,
    )
    return [dict(row) for row in rows]


async def get_all_stage_qa_records(session_id: str) -> dict[int, list[dict]]:
    db = await get_db()
    rows = await db.fetch(
        """SELECT stage_id, question_id, question_text, question_type, user_answer, score, feedback
           FROM qa_records WHERE session_id = $1
           ORDER BY stage_id, id""",
        session_id,
    )
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        r = dict(row)
        sid = int(r["stage_id"])
        grouped.setdefault(sid, []).append(r)
    return grouped


async def get_all_stage_explanations(session_id: str) -> dict[int, str]:
    db = await get_db()
    rows = await db.fetch(
        """SELECT stage_id, full_explanation FROM stage_progress
           WHERE session_id = $1 AND full_explanation IS NOT NULL AND full_explanation != ''""",
        session_id,
    )
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
    async with db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO decision_records
                   (session_id, stage_id, decision, best_score, next_stage_id, next_stage_score,
                    reason_lines_json, strategy_snapshot_json)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                session_id,
                stage_id,
                decision,
                best_score,
                next_stage_id,
                next_stage_score,
                json.dumps(reason_lines, ensure_ascii=False),
                json.dumps(strategy_snapshot, ensure_ascii=False),
            )
            # 每個 session 只保留最近 N 筆決策歷史，避免表無限成長
            await conn.execute(
                """DELETE FROM decision_records
                   WHERE session_id = $1
                     AND id NOT IN (
                       SELECT id FROM decision_records
                       WHERE session_id = $2
                       ORDER BY id DESC
                       LIMIT $3
                     )""",
                session_id, session_id, DECISION_HISTORY_MAX_PER_SESSION,
            )


async def get_decision_records(
    session_id: str,
    limit: int = DECISION_HISTORY_MAX_PER_SESSION,
) -> list[dict]:
    db = await get_db()
    rows = await db.fetch(
        """SELECT stage_id, decision, best_score, next_stage_id, next_stage_score,
                  reason_lines_json, strategy_snapshot_json, created_at
           FROM decision_records
           WHERE session_id = $1
           ORDER BY id ASC
           LIMIT $2""",
        session_id, limit,
    )
    records: list[dict] = []
    for row in rows:
        created_at = row["created_at"]
        records.append(
            {
                "stage_id": row["stage_id"],
                "decision": row["decision"],
                "best_score": row["best_score"],
                "next_stage_id": row["next_stage_id"],
                "next_stage_score": row["next_stage_score"],
                "reason_lines": json.loads(row["reason_lines_json"] or "[]"),
                "strategy_snapshot": json.loads(row["strategy_snapshot_json"] or "{}"),
                "created_at": created_at.isoformat() if created_at is not None else None,
            }
        )
    return records


async def insert_source_chunks(session_id: str, chunks: list[dict]) -> None:
    """將本地切好的 source_chunks 存入 DB（教材 source truth）。"""
    db = await get_db()
    rows = [
        (
            c["chunk_id"],
            session_id,
            c["order_index"],
            c["text"],
            c.get("section_title"),
            c.get("char_start"),
            c.get("char_end"),
            c.get("source_id"),
            c.get("source_index"),
            c.get("source_label"),
        )
        for c in chunks
    ]
    await db.executemany(
        """INSERT INTO source_chunks
           (chunk_id, session_id, order_index, text, section_title, char_start, char_end,
            source_id, source_index, source_label)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
           ON CONFLICT (session_id, chunk_id) DO UPDATE SET
             order_index   = EXCLUDED.order_index,
             text          = EXCLUDED.text,
             section_title = EXCLUDED.section_title,
             char_start    = EXCLUDED.char_start,
             char_end      = EXCLUDED.char_end,
             source_id     = EXCLUDED.source_id,
             source_index  = EXCLUDED.source_index,
             source_label  = EXCLUDED.source_label""",
        rows,
    )


async def purge_source_uploads(session_id: str, file_ids: list[str]) -> None:
    """chunk 入庫後刪除磁碟 upload 並清空 session 的 file_ids 引用。"""
    from ..files.upload_store import purge_upload_files

    for fid in file_ids:
        if isinstance(fid, str) and fid:
            purge_upload_files(fid)
    db = await get_db()
    await db.execute(
        "UPDATE sessions SET source_file_ids_json = '[]' WHERE session_id = $1",
        session_id,
    )


def _json_safe_source_chunk(row) -> dict:
    """asyncpg Record → dict，並把 created_at（timestamptz → datetime）轉成 ISO 字串。

    DB 回的 raw chunk dict 帶 datetime 型 created_at；下游 build-phase agent
    （SplitterVerifier / ContentOutline）會把整個 chunk dict 丟進 json.dumps 組 LLM
    payload，datetime 不可序列化會讓 verifier fail-open（見 docs §9 P1）。在 loader
    邊界把 created_at 轉成 ISO 字串即可單點根治，沿用本檔 get_decision_records 的慣例。
    """
    chunk = dict(row)
    created = chunk.get("created_at")
    if isinstance(created, datetime):
        chunk["created_at"] = created.isoformat()
    return chunk


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
        rows = await db.fetch(
            "SELECT * FROM source_chunks WHERE session_id = $1 ORDER BY order_index",
            session_id,
        )
    else:
        if not chunk_ids:
            return []
        placeholders = ", ".join(f"${i + 2}" for i in range(len(chunk_ids)))
        rows = await db.fetch(
            f"SELECT * FROM source_chunks WHERE session_id = $1 AND chunk_id IN ({placeholders}) ORDER BY order_index",
            session_id, *chunk_ids,
        )
    return [_json_safe_source_chunk(row) for row in rows]


async def get_recent_qa_summary(session_id: str, max_items: int = 5) -> list[dict]:
    """取得最近 N 筆問答記錄摘要（供 ContextBuilder 使用）。"""
    db = await get_db()
    rows = await db.fetch(
        """SELECT stage_id, question_text, user_answer, score
           FROM qa_records WHERE session_id = $1
           ORDER BY id DESC LIMIT $2""",
        session_id, max_items,
    )
    return [dict(row) for row in reversed(rows)]


async def get_last_decision_record(session_id: str) -> Optional[dict]:
    """取得最後一筆決策記錄（含 strategy_snapshot）。"""
    db = await get_db()
    row = await db.fetchrow(
        """SELECT stage_id, decision, best_score, strategy_snapshot_json
           FROM decision_records WHERE session_id = $1
           ORDER BY id DESC LIMIT 1""",
        session_id,
    )
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
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
        session_id, stage_id, question_id, question_text, question_type,
        user_answer, score, feedback,
    )


async def get_user_sessions(user_id: str) -> list[dict]:
    """回傳用戶所有書本（排除 abandoned），含完成進度統計。"""
    db = await get_db()
    rows = await db.fetch(
        """SELECT session_id, title, raw_content_summary, status,
                  total_stages, updated_at
           FROM sessions
           WHERE user_id = $1 AND status != 'abandoned'
           ORDER BY updated_at DESC""",
        user_id,
    )
    result = []
    for row in rows:
        r = dict(row)
        cnt_row = await db.fetchrow(
            "SELECT COUNT(*) AS cnt FROM stage_progress WHERE session_id = $1 AND status = 'completed'",
            r["session_id"],
        )
        updated_at = r["updated_at"]
        result.append({
            "session_id": r["session_id"],
            "title": r["title"] or r["raw_content_summary"] or "未命名學習材料",
            "status": r["status"],
            "total_stages": int(r["total_stages"] or 0),
            "completed_stages": int(cnt_row["cnt"] if cnt_row else 0),
            "updated_at": updated_at.isoformat() if updated_at is not None else None,
        })
    return result


async def update_session_title(session_id: str, user_id: str, title: str) -> bool:
    db = await get_db()
    await db.execute(
        "UPDATE sessions SET title = $1 WHERE session_id = $2 AND user_id = $3",
        title.strip(), session_id, user_id,
    )
    return True


async def delete_session(session_id: str, user_id: str) -> bool:
    """刪除 session 及相關學習記錄；upload 通常已在 chunk 入庫後 purge，此處為冪等保險。"""
    from ..files.upload_store import delete_upload

    db = await get_db()
    row = await db.fetchrow(
        "SELECT source_file_ids_json FROM sessions WHERE session_id = $1 AND user_id = $2",
        session_id, user_id,
    )
    if not row:
        return False

    try:
        file_ids = json.loads(row[0] or "[]")
    except Exception:
        file_ids = []

    async with db.acquire() as conn:
        async with conn.transaction():
            for tbl in ("qa_records", "stage_progress", "source_chunks", "decision_records", "tutor_records"):
                await conn.execute(f"DELETE FROM {tbl} WHERE session_id = $1", session_id)
            await conn.execute(
                "DELETE FROM sessions WHERE session_id = $1 AND user_id = $2",
                session_id, user_id,
            )

    # 清理磁碟上的 upload blob（失敗不阻擋 session 刪除）
    for fid in file_ids:
        if isinstance(fid, str):
            delete_upload(fid)
    return True


async def insert_tutor_record(
    session_id: str,
    stage_id: int,
    question: str,
    answer: str,
    in_scope: bool,
    scope: str = "current_chapter",
) -> int:
    db = await get_db()
    new_id = await db.fetchval(
        """INSERT INTO tutor_records (session_id, stage_id, question, answer, in_scope, scope)
           VALUES ($1, $2, $3, $4, $5, $6)
           RETURNING id""",
        session_id, stage_id, question, answer, 1 if in_scope else 0, scope,
    )
    return new_id


async def get_all_tutor_records(session_id: str) -> dict[int, list[dict]]:
    """回傳 session 所有 tutor 問答，以 stage_id 分組，按插入順序排列。"""
    db = await get_db()
    rows = await db.fetch(
        """SELECT id, stage_id, question, answer, in_scope, scope
           FROM tutor_records WHERE session_id = $1
           ORDER BY id ASC""",
        session_id,
    )
    result: dict[int, list[dict]] = {}
    for row in rows:
        sid = row["stage_id"]
        if sid not in result:
            result[sid] = []
        raw_scope = row["scope"]
        # 舊資料 scope=NULL，從 in_scope 反推
        resolved_scope = raw_scope if raw_scope else (
            "current_chapter" if row["in_scope"] else "out_of_scope"
        )
        result[sid].append({
            "id": row["id"],
            "question": row["question"],
            "answer": row["answer"],
            "in_scope": bool(row["in_scope"]),
            "scope": resolved_scope,
        })
    return result


async def delete_tutor_record(record_id: int, session_id: str) -> bool:
    db = await get_db()
    row = await db.fetchrow(
        "DELETE FROM tutor_records WHERE id = $1 AND session_id = $2 RETURNING id",
        record_id, session_id,
    )
    return row is not None
