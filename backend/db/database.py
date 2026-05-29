import aiosqlite
import logging
import os
import sys
from pathlib import Path

_log = logging.getLogger(__name__)

_DB_PATH: str | None = None
_connection: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    global _connection
    if _connection is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _connection


async def init_db(db_path: str) -> None:
    global _connection, _DB_PATH
    _DB_PATH = db_path

    db_dir = Path(db_path).parent
    db_dir.mkdir(parents=True, exist_ok=True)

    _connection = await aiosqlite.connect(db_path)
    _connection.row_factory = aiosqlite.Row
    journal = os.getenv("SQLITE_JOURNAL_MODE", "").strip().upper()
    if not journal:
        # Windows host + Docker bind-mount: WAL 常導致 disk I/O error
        journal = "DELETE" if sys.platform == "win32" else "WAL"
    try:
        await _connection.execute(f"PRAGMA journal_mode={journal}")
    except Exception as exc:
        _log.warning(
            "PRAGMA journal_mode=%s failed (%s); continuing with existing journal mode",
            journal,
            exc,
        )
    await _connection.execute("PRAGMA busy_timeout=30000")
    await _connection.execute("PRAGMA foreign_keys=ON")

    migration_path = Path(__file__).parent / "migrations" / "001_initial.sql"
    sql = migration_path.read_text(encoding="utf-8")
    await _connection.executescript(sql)
    await _connection.commit()

    # Migration 002：加入 stages_json 欄位（已存在則忽略）
    try:
        await _connection.execute("ALTER TABLE sessions ADD COLUMN stages_json TEXT DEFAULT '[]'")
        await _connection.commit()
    except Exception:
        pass  # 欄位已存在，忽略

    # Migration 003：加入 full_explanation 欄位至 stage_progress（已存在則忽略）
    try:
        await _connection.execute("ALTER TABLE stage_progress ADD COLUMN full_explanation TEXT DEFAULT ''")
        await _connection.commit()
    except Exception:
        pass  # 欄位已存在，忽略

    # Migration 004：加入 questions_json 欄位至 stage_progress（已存在則忽略）
    try:
        await _connection.execute("ALTER TABLE stage_progress ADD COLUMN questions_json TEXT DEFAULT '[]'")
        await _connection.commit()
    except Exception:
        pass  # 欄位已存在，忽略

    # Migration 005：加入 pending_map_json 欄位至 sessions（已存在則忽略）
    try:
        await _connection.execute("ALTER TABLE sessions ADD COLUMN pending_map_json TEXT DEFAULT NULL")
        await _connection.commit()
    except Exception:
        pass  # 欄位已存在，忽略

    # Migration 007：記錄每個 session 使用的 provider/model
    try:
        await _connection.execute("ALTER TABLE sessions ADD COLUMN provider_name TEXT DEFAULT NULL")
        await _connection.commit()
    except Exception:
        pass
    try:
        await _connection.execute("ALTER TABLE sessions ADD COLUMN model_name TEXT DEFAULT NULL")
        await _connection.commit()
    except Exception:
        pass

    # Migration 008：記錄 session 使用的題目模式
    try:
        await _connection.execute("ALTER TABLE sessions ADD COLUMN question_mode TEXT DEFAULT 'short_answer'")
        await _connection.commit()
    except Exception:
        pass

    # Migration 009：建立 source_chunks 表（後端掌控教材 source truth）
    await _connection.execute(
        """CREATE TABLE IF NOT EXISTS source_chunks (
            chunk_id      TEXT NOT NULL,
            session_id    TEXT NOT NULL REFERENCES sessions(session_id),
            order_index   INTEGER NOT NULL,
            text          TEXT NOT NULL,
            section_title TEXT,
            char_start    INTEGER,
            char_end      INTEGER,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chunk_id, session_id)
        )"""
    )
    await _connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_source_chunks_session ON source_chunks(session_id)"
    )
    await _connection.commit()

    # Migration 010：為 sessions 加入可自訂標題欄位
    try:
        await _connection.execute("ALTER TABLE sessions ADD COLUMN title TEXT DEFAULT NULL")
        await _connection.commit()
    except Exception:
        pass

    # Migration 011：users 加入 session_version，支援單一裝置登入
    try:
        await _connection.execute("ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0")
        await _connection.commit()
    except Exception:
        pass

    # Migration 012：user_learning_profile 儲存跨裝置 UI 狀態
    try:
        await _connection.execute(
            "ALTER TABLE user_learning_profile ADD COLUMN ui_state_json TEXT DEFAULT '{}'"
        )
        await _connection.commit()
    except Exception:
        pass

    # Migration 006：建立決策歷史表（跨裝置恢復教練趨勢）
    await _connection.execute(
        """CREATE TABLE IF NOT EXISTS decision_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            stage_id INTEGER NOT NULL,
            decision TEXT NOT NULL,
            best_score REAL NOT NULL,
            next_stage_id INTEGER NULL,
            next_stage_score REAL NULL,
            reason_lines_json TEXT DEFAULT '[]',
            strategy_snapshot_json TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    await _connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_decision_records_session_id ON decision_records(session_id)"
    )
    await _connection.commit()

    # Migration 013：建立 tutor_records 表（ask_tutor 問答按章節持久化）
    await _connection.execute(
        """CREATE TABLE IF NOT EXISTS tutor_records (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            stage_id    INTEGER NOT NULL,
            question    TEXT NOT NULL,
            answer      TEXT NOT NULL,
            in_scope    INTEGER NOT NULL DEFAULT 1,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    await _connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_tutor_records_session "
        "ON tutor_records(session_id, stage_id)"
    )
    await _connection.commit()

    # Migration 014：tutor_records 新增 scope TEXT 欄位（三態邊界判定）
    try:
        await _connection.execute(
            "ALTER TABLE tutor_records ADD COLUMN scope TEXT DEFAULT NULL"
        )
        await _connection.commit()
    except Exception:
        pass  # 欄位已存在，冪等跳過

    # Migration 015：sessions 記錄該 session 使用的 upload file_ids，供刪除時 GC
    try:
        await _connection.execute(
            "ALTER TABLE sessions ADD COLUMN source_file_ids_json TEXT DEFAULT '[]'"
        )
        await _connection.commit()
    except Exception:
        pass

    # Migration 016：inflight_locks 跨 worker dedup
    # key 用 ws layer 的 generation key（例如 sess_X、sess_X:tutor、sess_X:answer:q_1_0）；
    # started_at = time.time() Unix timestamp；worker 啟動清理 stale lock 防止孤兒
    await _connection.execute(
        """CREATE TABLE IF NOT EXISTS inflight_locks (
            key TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            started_at REAL NOT NULL,
            worker_pid INTEGER,
            meta_json TEXT
        )"""
    )
    await _connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_inflight_session ON inflight_locks(session_id)"
    )
    await _connection.commit()

    # Migration 017：concept_mastery 加 source_signature（教材出處標記）
    # 目的：跨教材 mastery 隔離。同一 user 跑多本書時，QG 個人化過濾的
    # 「已掌握概念清單」只取「與當前 session 同 source」的概念，
    # 避免上本書的高 mastery 概念污染下本書的 prompt。
    # signature = sorted(source_file_ids) join '|'；舊 record 保持 NULL（legacy）。
    try:
        await _connection.execute(
            "ALTER TABLE concept_mastery ADD COLUMN source_signature TEXT DEFAULT NULL"
        )
        await _connection.commit()
    except Exception:
        pass

    # Migration 018：concept_mastery 唯一鍵改為 (user_id, source_signature, concept_name)
    async with _connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='concept_mastery_scoped'"
    ) as cur:
        already = await cur.fetchone()
    if not already:
        await _connection.execute(
            """CREATE TABLE concept_mastery_scoped (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES users(user_id),
                concept_name TEXT NOT NULL,
                mastery_score REAL DEFAULT 0.0,
                total_exposures INTEGER DEFAULT 0,
                confusion_patterns TEXT DEFAULT '[]',
                successful_analogies TEXT DEFAULT '[]',
                last_tested TIMESTAMP,
                source_signature TEXT,
                UNIQUE(user_id, source_signature, concept_name)
            )"""
        )
        async with _connection.execute("SELECT * FROM concept_mastery") as cur:
            old_rows = await cur.fetchall()
        merged: dict[tuple, dict] = {}
        for row in old_rows:
            sig = row["source_signature"] if "source_signature" in row.keys() else None
            key = (row["user_id"], sig, row["concept_name"])
            if key not in merged:
                merged[key] = dict(row)
                continue
            prev = merged[key]
            prev_score = float(prev["mastery_score"] or 0)
            new_score = float(row["mastery_score"] or 0)
            prev["mastery_score"] = 0.7 * prev_score + 0.3 * new_score
            prev["total_exposures"] = int(prev["total_exposures"] or 0) + int(
                row["total_exposures"] or 0
            )
            if str(row["last_tested"] or "") > str(prev["last_tested"] or ""):
                prev["last_tested"] = row["last_tested"]
        for rec in merged.values():
            await _connection.execute(
                """INSERT INTO concept_mastery_scoped
                   (user_id, concept_name, mastery_score, total_exposures, confusion_patterns,
                    successful_analogies, last_tested, source_signature)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rec["user_id"],
                    rec["concept_name"],
                    rec["mastery_score"],
                    rec["total_exposures"],
                    rec["confusion_patterns"],
                    rec["successful_analogies"],
                    rec["last_tested"],
                    rec.get("source_signature"),
                ),
            )
        await _connection.execute("DROP TABLE concept_mastery")
        await _connection.execute(
            "ALTER TABLE concept_mastery_scoped RENAME TO concept_mastery"
        )
        await _connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_concept_mastery_user ON concept_mastery(user_id)"
        )
        await _connection.commit()

    # Migration 019：source_chunks 來源 metadata + sessions.sources_json
    for col in ("source_id TEXT", "source_index INTEGER", "source_label TEXT"):
        try:
            await _connection.execute(f"ALTER TABLE source_chunks ADD COLUMN {col}")
            await _connection.commit()
        except Exception:
            pass
    try:
        await _connection.execute(
            "ALTER TABLE sessions ADD COLUMN sources_json TEXT DEFAULT '[]'"
        )
        await _connection.commit()
    except Exception:
        pass

    # Migration 022：curriculum pipeline region checkpoint（重啟續跑）
    await _connection.execute(
        """CREATE TABLE IF NOT EXISTS curriculum_checkpoints (
            session_id            TEXT PRIMARY KEY REFERENCES sessions(session_id),
            content_hash          TEXT NOT NULL,
            pipeline_version      TEXT NOT NULL DEFAULT 'v2',
            pipeline_meta_json    TEXT NOT NULL DEFAULT '{}',
            required_outline_json TEXT,
            regions_json          TEXT,
            completed_region_ids_json TEXT NOT NULL DEFAULT '[]',
            all_candidates_json   TEXT NOT NULL DEFAULT '[]',
            summary_parts_json    TEXT NOT NULL DEFAULT '[]',
            meter_json            TEXT NOT NULL DEFAULT '{}',
            last_region_id        TEXT,
            updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    await _connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_curriculum_ckpt_updated "
        "ON curriculum_checkpoints(updated_at)"
    )
    await _connection.commit()

    # Migration 023：generating 期間持久化 target_depth
    try:
        await _connection.execute(
            "ALTER TABLE sessions ADD COLUMN target_depth TEXT DEFAULT NULL"
        )
        await _connection.commit()
    except Exception:
        pass

    # Migration 025：sessions 加入 same_material 欄位（0/1/NULL）
    # 紀錄使用者「是否同教材」選擇，供 resume 流程還原；
    # NULL = legacy 未紀錄（resume 時視為 True）；1 = 同教材；0 = 換教材
    try:
        await _connection.execute(
            "ALTER TABLE sessions ADD COLUMN same_material INTEGER"
        )
        await _connection.commit()
    except Exception:
        pass

    # Migration 024：curriculum LLM result cache
    await _connection.execute(
        """CREATE TABLE IF NOT EXISTS llm_result_cache (
            cache_key       TEXT PRIMARY KEY,
            scope           TEXT NOT NULL DEFAULT 'curriculum',
            content_hash    TEXT,
            agent_name      TEXT NOT NULL,
            region_id       TEXT,
            prompt_version  TEXT NOT NULL,
            model_name      TEXT NOT NULL,
            result_json     TEXT NOT NULL,
            input_tokens    INTEGER DEFAULT 0,
            output_tokens   INTEGER DEFAULT 0,
            hit_count       INTEGER NOT NULL DEFAULT 0,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_hit_at     TIMESTAMP
        )"""
    )
    await _connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_cache_content_hash "
        "ON llm_result_cache(content_hash)"
    )
    await _connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_cache_scope_agent "
        "ON llm_result_cache(scope, agent_name)"
    )
    await _connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_cache_created "
        "ON llm_result_cache(created_at)"
    )
    await _connection.commit()


async def close_db() -> None:
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None
