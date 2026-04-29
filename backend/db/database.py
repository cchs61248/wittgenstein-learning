import aiosqlite
import os
from pathlib import Path

_DB_PATH: str | None = None
_connection: aiosqlite.Connection | None = None


def set_db_path(path: str) -> None:
    global _DB_PATH
    _DB_PATH = path


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
    await _connection.execute("PRAGMA journal_mode=WAL")
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


async def close_db() -> None:
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None
