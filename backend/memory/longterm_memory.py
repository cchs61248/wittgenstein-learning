import json
from datetime import datetime, timezone
import asyncpg
from ..db.database import get_db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_user_profile_summary(user_id: str) -> str:
    db = await get_db()
    row = await db.fetchrow(
        "SELECT * FROM user_learning_profile WHERE user_id = $1", user_id
    )

    if not row:
        return "尚無學習記錄"

    profile = dict(row)
    return (
        f"偏好{profile.get('preferred_style', 'concrete')}式解釋，"
        f"平均需要{profile.get('avg_attempts_per_stage', 1.5):.1f}次嘗試通過。"
    )


async def get_weak_concepts(user_id: str, limit: int = 5) -> str:
    db = await get_db()
    rows = await db.fetch(
        """SELECT concept_name FROM concept_mastery
           WHERE user_id = $1 AND mastery_score < 0.6
           ORDER BY last_tested DESC LIMIT $2""",
        user_id, limit,
    )

    if not rows:
        return "無"
    return "、".join(row[0] for row in rows)


async def get_misconceptions(user_id: str, concepts: list[str]) -> list[dict]:
    """取得指定概念的混淆模式（供 ContextBuilder 使用）。

    confusion_patterns 欄位可能混入兩種格式：
    - dict：結構化 misconception_pattern（concept + pattern + severity 等完整資訊）
    - str：早期格式或從 confused_concepts 累積進來的「跨概念干擾」名稱
           （學生在這題混淆到另一個 concept），不適合當 pattern 文字輸出。

    本函式優先回傳結構化條目，並依 (concept, pattern) 去重，避免 teacher
    prompt 出現「『X』：Y、『X』：Y」這種重複或語意不明的條目。
    """
    if not concepts:
        return []
    db = await get_db()
    placeholders = ",".join(f"${i+2}" for i in range(len(concepts)))
    rows = await db.fetch(
        f"""SELECT concept_name, confusion_patterns FROM concept_mastery
            WHERE user_id = $1 AND concept_name IN ({placeholders})
              AND confusion_patterns IS NOT NULL AND confusion_patterns != '[]'""",
        user_id, *concepts,
    )

    result: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        concept = row["concept_name"]
        patterns = json.loads(row["confusion_patterns"] or "[]")
        for p in patterns:
            if isinstance(p, dict) and p.get("pattern"):
                # 結構化 misconception：保留 concept/pattern/severity/student_evidence 等完整欄位
                norm_concept = p.get("concept", concept) or concept
                pattern_text = p["pattern"]
                key = (norm_concept, pattern_text)
                if key in seen:
                    continue
                seen.add(key)
                result.append({**p, "concept": norm_concept})
            # str 條目（跨概念干擾、舊格式）跳過：這些不是真正的 misconception pattern，
            # 累積到「容易混淆的概念」欄位反而會造成「『X』：Y」式的怪格式條目。
            # 真正有診斷價值的細節都應該在 dict 條目的 pattern 文字裡。
    return result


async def get_concept_mastery_map(
    user_id: str,
    concepts: list[str],
    source_signature: str | None = None,
) -> dict[str, float]:
    if not concepts:
        return {}
    db = await get_db()
    placeholders = ",".join(f"${i+2}" for i in range(len(concepts)))
    params: list = [user_id, *concepts]
    sig_clause = ""
    if source_signature is not None:
        sig_clause = f" AND COALESCE(source_signature, '') = ${len(params)+1}"
        params.append(source_signature)
    rows = await db.fetch(
        f"""SELECT concept_name, mastery_score FROM concept_mastery
            WHERE user_id = $1 AND concept_name IN ({placeholders}){sig_clause}""",
        *params,
    )
    return {row["concept_name"]: float(row["mastery_score"]) for row in rows}


async def get_concept_canonical_pool(
    user_id: str,
    source_signature: str,
    limit: int | None = None,
) -> list[dict]:
    """canonicalize agent 專用：回傳同 source_signature 的概念名與排序所需欄位。

    回傳 [{"concept_name": str, "total_exposures": int, "last_tested": str}, ...]
    按 total_exposures DESC, last_tested DESC 排序。
    跨 source_signature 隔離（migration 017 延續行為）：NULL signature 與其他
    signature 一律排除，避免 canonicalize 把不同教材的概念視為候選 canonical。
    """
    db = await get_db()
    sql = """SELECT concept_name, total_exposures, last_tested
             FROM concept_mastery
             WHERE user_id = $1 AND source_signature = $2
             ORDER BY total_exposures DESC, last_tested DESC"""
    params: list = [user_id, source_signature]
    if limit is not None:
        sql += f" LIMIT ${len(params)+1}"
        params.append(limit)
    rows = await db.fetch(sql, *params)
    return [
        {
            "concept_name": row["concept_name"],
            "total_exposures": int(row["total_exposures"] or 0),
            # asyncpg returns datetime for TIMESTAMPTZ; convert for JSON serialization
            "last_tested": row["last_tested"].isoformat() if row["last_tested"] is not None else "",
        }
        for row in rows
    ]


async def get_user_mastery_map(
    user_id: str,
    threshold: float = 0.8,
    source_signature: str | None = None,
) -> dict[str, float]:
    """撈整個 user 的高 mastery 概念（跨 stage 已掌握），用於 QG 個人化過濾。

    與 get_concept_mastery_map 不同：不限定 concepts 列表，回傳所有 mastery_score
    >= threshold 的概念，讓 QG 知道學生已穩定掌握哪些（含過去 stage 學過的），
    避免在新 stage 出題時把這些概念當作主要 key_concepts_tested。

    threshold 預設 0.8 對齊 QuestionGenerator._format_mastered_concepts 的判定標準。

    source_signature 行為（跨教材隔離）：
      - 非 None：只回傳「source_signature 相同」的概念，跨教材污染被切斷。
      - None：保留 legacy 行為（不過濾出處），舊資料 / 測試 path 仍能運作。
    """
    db = await get_db()
    if source_signature is not None:
        rows = await db.fetch(
            """SELECT concept_name, mastery_score FROM concept_mastery
               WHERE user_id = $1 AND mastery_score >= $2 AND source_signature = $3""",
            user_id, threshold, source_signature,
        )
    else:
        rows = await db.fetch(
            """SELECT concept_name, mastery_score FROM concept_mastery
               WHERE user_id = $1 AND mastery_score >= $2""",
            user_id, threshold,
        )
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
    source_signature: str | None = None,
) -> None:
    db = await get_db()
    sig_key = (source_signature or "").strip()

    # update_concept_mastery does a lookup + conditional write; wrap atomically
    async with db.acquire() as conn:
        async with conn.transaction():
            if sig_key:
                lookup_sql = (
                    """SELECT mastery_score, total_exposures, confusion_patterns, successful_analogies,
                              source_signature
                       FROM concept_mastery
                       WHERE user_id = $1 AND concept_name = $2 AND COALESCE(source_signature, '') = $3"""
                )
                row = await conn.fetchrow(lookup_sql, user_id, concept_name, sig_key)
            else:
                # Legacy caller 未傳 signature：以 (user, concept) 找第一筆，不覆蓋既有出處標記
                lookup_sql = (
                    """SELECT mastery_score, total_exposures, confusion_patterns, successful_analogies,
                              source_signature
                       FROM concept_mastery
                       WHERE user_id = $1 AND concept_name = $2
                       ORDER BY last_tested DESC LIMIT 1"""
                )
                row = await conn.fetchrow(lookup_sql, user_id, concept_name)
            if row and not sig_key:
                sig_key = (row["source_signature"] or "").strip()

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
                if sig_key:
                    await conn.execute(
                        """UPDATE concept_mastery
                           SET mastery_score = $1, total_exposures = $2, confusion_patterns = $3,
                               successful_analogies = $4, last_tested = $5
                           WHERE user_id = $6 AND concept_name = $7 AND COALESCE(source_signature, '') = $8""",
                        ema_score, exposures,
                        json.dumps(existing_confusion, ensure_ascii=False),
                        json.dumps(existing_analogies, ensure_ascii=False),
                        _utcnow(), user_id, concept_name, sig_key,
                    )
                else:
                    await conn.execute(
                        """UPDATE concept_mastery
                           SET mastery_score = $1, total_exposures = $2, confusion_patterns = $3,
                               successful_analogies = $4, last_tested = $5
                           WHERE user_id = $6 AND concept_name = $7""",
                        ema_score, exposures,
                        json.dumps(existing_confusion, ensure_ascii=False),
                        json.dumps(existing_analogies, ensure_ascii=False),
                        _utcnow(), user_id, concept_name,
                    )
            else:
                insert_sig = sig_key or (source_signature if source_signature else None)
                await conn.execute(
                    """INSERT INTO concept_mastery
                       (user_id, concept_name, mastery_score, total_exposures, confusion_patterns,
                        successful_analogies, last_tested, source_signature)
                       VALUES ($1, $2, $3, 1, $4, $5, $6, $7)""",
                    user_id, concept_name, new_score,
                    json.dumps(existing_confusion, ensure_ascii=False),
                    json.dumps(existing_analogies, ensure_ascii=False),
                    _utcnow(), insert_sig,
                )


async def update_user_profile(user_id: str, attempts_this_session: float) -> None:
    db = await get_db()
    row = await db.fetchrow(
        "SELECT avg_attempts_per_stage FROM user_learning_profile WHERE user_id = $1",
        user_id,
    )

    if row:
        new_avg = 0.8 * row[0] + 0.2 * attempts_this_session
        await db.execute(
            "UPDATE user_learning_profile SET avg_attempts_per_stage = $1, updated_at = $2 WHERE user_id = $3",
            new_avg, _utcnow(), user_id,
        )
    else:
        await db.execute(
            "INSERT INTO user_learning_profile (user_id, avg_attempts_per_stage) VALUES ($1, $2)",
            user_id, attempts_this_session,
        )


async def get_all_concept_mastery(user_id: str) -> list[dict]:
    """回傳該用戶所有概念的掌握度記錄（按掌握度升序）。"""
    db = await get_db()
    rows = await db.fetch(
        """SELECT concept_name, mastery_score, total_exposures,
                  confusion_patterns, last_tested
           FROM concept_mastery
           WHERE user_id = $1
           ORDER BY mastery_score ASC""",
        user_id,
    )

    result = []
    for row in rows:
        result.append({
            "concept_name": row["concept_name"],
            "mastery_score": float(row["mastery_score"]),
            "total_exposures": int(row["total_exposures"]),
            "confusion_patterns": json.loads(row["confusion_patterns"] or "[]"),
            # asyncpg returns datetime for TIMESTAMPTZ; use isoformat for serialization
            "last_tested": row["last_tested"].isoformat() if row["last_tested"] is not None else None,
        })
    return result
