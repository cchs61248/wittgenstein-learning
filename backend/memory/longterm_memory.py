import json
from datetime import datetime
from typing import Optional
from ..db.database import get_db


async def get_user_profile_summary(user_id: str) -> str:
    db = await get_db()
    async with db.execute(
        "SELECT * FROM user_learning_profile WHERE user_id = ?", (user_id,)
    ) as cur:
        row = await cur.fetchone()

    if not row:
        return "尚無學習記錄"

    profile = dict(row)
    return (
        f"偏好{profile.get('preferred_style', 'concrete')}式解釋，"
        f"平均需要{profile.get('avg_attempts_per_stage', 1.5):.1f}次嘗試通過。"
    )


async def get_weak_concepts(user_id: str, limit: int = 5) -> str:
    db = await get_db()
    async with db.execute(
        """SELECT concept_name FROM concept_mastery
           WHERE user_id = ? AND mastery_score < 0.6
           ORDER BY last_tested DESC LIMIT ?""",
        (user_id, limit),
    ) as cur:
        rows = await cur.fetchall()

    if not rows:
        return "無"
    return "、".join(row[0] for row in rows)


async def get_misconceptions(user_id: str, concepts: list[str]) -> list[dict]:
    """取得指定概念的混淆模式（供 ContextBuilder 使用）。"""
    if not concepts:
        return []
    db = await get_db()
    placeholders = ",".join("?" for _ in concepts)
    async with db.execute(
        f"""SELECT concept_name, confusion_patterns FROM concept_mastery
            WHERE user_id = ? AND concept_name IN ({placeholders})
              AND confusion_patterns IS NOT NULL AND confusion_patterns != '[]'""",
        [user_id, *concepts],
    ) as cur:
        rows = await cur.fetchall()

    result: list[dict] = []
    for row in rows:
        concept = row["concept_name"]
        patterns = json.loads(row["confusion_patterns"] or "[]")
        for p in patterns:
            if isinstance(p, str):
                result.append({"concept": concept, "pattern": p, "severity": "medium"})
            elif isinstance(p, dict):
                result.append({**p, "concept": p.get("concept", concept)})
    return result


async def get_concept_mastery_map(user_id: str, concepts: list[str]) -> dict[str, float]:
    if not concepts:
        return {}
    db = await get_db()
    placeholders = ",".join("?" for _ in concepts)
    params = [user_id, *concepts]
    async with db.execute(
        f"""SELECT concept_name, mastery_score FROM concept_mastery
            WHERE user_id = ? AND concept_name IN ({placeholders})""",
        params,
    ) as cur:
        rows = await cur.fetchall()
    return {row["concept_name"]: float(row["mastery_score"]) for row in rows}


async def update_concept_mastery(
    user_id: str,
    concept_name: str,
    new_score: float,
    confused_concepts: list[str] | None = None,
    successful_analogies: list[str] | None = None,
    misconception_pattern: dict | None = None,
    analogy_used: str | None = None,
    lesson_was_effective: bool = False,
) -> None:
    db = await get_db()
    async with db.execute(
        """SELECT mastery_score, total_exposures, confusion_patterns, successful_analogies
           FROM concept_mastery WHERE user_id = ? AND concept_name = ?""",
        (user_id, concept_name),
    ) as cur:
        row = await cur.fetchone()

    # ── 組裝 confusion_patterns ──────────────────────────────────
    existing_confusion: list = json.loads((row["confusion_patterns"] if row else None) or "[]")
    if misconception_pattern and isinstance(misconception_pattern, dict):
        # 結構化 misconception（Phase 3+）：去除相同 pattern 的舊記錄後 append
        existing_confusion = [
            p for p in existing_confusion
            if not (isinstance(p, dict) and p.get("pattern") == misconception_pattern.get("pattern"))
        ]
        existing_confusion.append(misconception_pattern)
    elif confused_concepts:
        # 舊格式字串列表（相容）
        # dict.fromkeys 要求 hashable，existing_confusion 可能含 Phase 3+ 的 dict，需分開處理
        existing_dicts = [p for p in existing_confusion if isinstance(p, dict)]
        existing_strings = [p for p in existing_confusion if isinstance(p, str)]
        new_strings = [c for c in confused_concepts if isinstance(c, str) and c not in existing_strings]
        existing_confusion = existing_dicts + existing_strings + new_strings
    existing_confusion = existing_confusion[-5:]

    # ── 組裝 successful_analogies ────────────────────────────────
    existing_analogies: list = json.loads((row["successful_analogies"] if row else None) or "[]")
    if successful_analogies:
        for a in successful_analogies:
            if a and a not in existing_analogies:
                existing_analogies.append(a)
    if lesson_was_effective and analogy_used and analogy_used not in existing_analogies:
        existing_analogies.append(analogy_used)
    existing_analogies = existing_analogies[-5:]

    if row:
        ema_score = 0.7 * float(row["mastery_score"]) + 0.3 * new_score
        exposures = row["total_exposures"] + 1
        await db.execute(
            """UPDATE concept_mastery
               SET mastery_score = ?, total_exposures = ?, confusion_patterns = ?,
                   successful_analogies = ?, last_tested = ?
               WHERE user_id = ? AND concept_name = ?""",
            (
                ema_score, exposures,
                json.dumps(existing_confusion, ensure_ascii=False),
                json.dumps(existing_analogies, ensure_ascii=False),
                datetime.utcnow(), user_id, concept_name,
            ),
        )
    else:
        await db.execute(
            """INSERT INTO concept_mastery
               (user_id, concept_name, mastery_score, total_exposures, confusion_patterns, successful_analogies, last_tested)
               VALUES (?, ?, ?, 1, ?, ?, ?)""",
            (
                user_id, concept_name, new_score,
                json.dumps(existing_confusion, ensure_ascii=False),
                json.dumps(existing_analogies, ensure_ascii=False),
                datetime.utcnow(),
            ),
        )
    await db.commit()


async def update_user_profile(user_id: str, attempts_this_session: float) -> None:
    db = await get_db()
    async with db.execute(
        "SELECT avg_attempts_per_stage FROM user_learning_profile WHERE user_id = ?",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()

    if row:
        new_avg = 0.8 * row[0] + 0.2 * attempts_this_session
        await db.execute(
            "UPDATE user_learning_profile SET avg_attempts_per_stage = ?, updated_at = ? WHERE user_id = ?",
            (new_avg, datetime.utcnow(), user_id),
        )
    else:
        await db.execute(
            "INSERT INTO user_learning_profile (user_id, avg_attempts_per_stage) VALUES (?, ?)",
            (user_id, attempts_this_session),
        )
    await db.commit()
