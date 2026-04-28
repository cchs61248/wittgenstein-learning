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
    # 清除同用戶舊的 pending session（避免累積）
    await db.execute(
        "UPDATE sessions SET status = 'abandoned' WHERE user_id = ? AND status = 'pending_confirmation'",
        (user_id,),
    )
    pending_map = {"nodes": nodes, "summary": summary}
    await db.execute(
        """INSERT INTO sessions
           (session_id, user_id, content_hash, total_stages, raw_content_summary,
            status, stages_json, pending_map_json, provider_name, model_name, question_mode)
           VALUES (?, ?, ?, ?, ?, 'pending_confirmation', ?, ?, ?, ?, ?)""",
        (
            session_id, user_id, content_hash, len(stages),
            summary,
            json.dumps(stages, ensure_ascii=False),
            json.dumps(pending_map, ensure_ascii=False),
            provider_name,
            model_name,
            question_mode,
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
