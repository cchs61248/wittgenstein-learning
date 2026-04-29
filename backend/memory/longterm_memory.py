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
    confused_concepts: list[str],
    successful_analogies: list[str],
) -> None:
    db = await get_db()
    async with db.execute(
        "SELECT mastery_score, total_exposures, confusion_patterns FROM concept_mastery WHERE user_id = ? AND concept_name = ?",
        (user_id, concept_name),
    ) as cur:
        row = await cur.fetchone()

    if row:
        # 指數移動平均（EMA）更新分數
        old_score = row[0]
        exposures = row[1] + 1
        ema_score = 0.7 * old_score + 0.3 * new_score
        existing_confusion = json.loads(row[2] or "[]")
        existing_confusion = list(dict.fromkeys(existing_confusion + confused_concepts))[:10]

        await db.execute(
            """UPDATE concept_mastery
               SET mastery_score = ?, total_exposures = ?, confusion_patterns = ?, last_tested = ?
               WHERE user_id = ? AND concept_name = ?""",
            (ema_score, exposures, json.dumps(existing_confusion, ensure_ascii=False),
             datetime.utcnow(), user_id, concept_name),
        )
    else:
        await db.execute(
            """INSERT INTO concept_mastery
               (user_id, concept_name, mastery_score, total_exposures, confusion_patterns, successful_analogies, last_tested)
               VALUES (?, ?, ?, 1, ?, ?, ?)""",
            (
                user_id, concept_name, new_score,
                json.dumps(confused_concepts[:10], ensure_ascii=False),
                json.dumps(successful_analogies[:5], ensure_ascii=False),
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
