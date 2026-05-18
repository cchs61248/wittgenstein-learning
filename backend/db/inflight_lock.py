"""DB-backed inflight lock — 跨 worker dedup 的 acquire/release/is_active CRUD。"""
import os
import time
from typing import Optional

from .database import get_db


async def acquire(
    key: str,
    *,
    session_id: str,
    kind: str,
    meta_json: Optional[str] = None,
) -> bool:
    """嘗試取得 lock；已存在則回 False（呼叫端應走 wait/cache 路徑）。"""
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO inflight_locks "
            "(key, session_id, kind, started_at, worker_pid, meta_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (key, session_id, kind, time.time(), os.getpid(), meta_json),
        )
        await db.commit()
        return True
    except Exception:
        # UNIQUE PRIMARY KEY 衝突 — 已有人 hold
        return False


async def release(key: str) -> None:
    db = await get_db()
    await db.execute("DELETE FROM inflight_locks WHERE key = ?", (key,))
    await db.commit()


async def is_active(key: str) -> bool:
    db = await get_db()
    cur = await db.execute(
        "SELECT 1 FROM inflight_locks WHERE key = ? LIMIT 1", (key,)
    )
    row = await cur.fetchone()
    return row is not None


async def cleanup_stale(max_age_s: float = 600) -> int:
    """清掉 started_at 過老的孤兒（worker 強制關閉時殘留）。回傳清掉的數量。"""
    cutoff = time.time() - max_age_s
    db = await get_db()
    cur = await db.execute(
        "DELETE FROM inflight_locks WHERE started_at < ?", (cutoff,)
    )
    await db.commit()
    return cur.rowcount or 0
