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


async def close_db() -> None:
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None
