"""DB-backed inflight lock — 跨 worker dedup 的 acquire/release/is_active CRUD。"""
import os
import sqlite3
import time
from typing import Optional

from .database import get_db
from ..utils.logger import ws_logger


def _pid_alive(pid: int) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


async def acquire(
    key: str,
    *,
    session_id: str,
    kind: str,
    meta_json: Optional[str] = None,
) -> bool:
    """嘗試取得 lock；已存在則回 False（呼叫端應走 wait/cache 路徑）。"""
    log = ws_logger()
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO inflight_locks "
            "(key, session_id, kind, started_at, worker_pid, meta_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (key, session_id, kind, time.time(), os.getpid(), meta_json),
        )
        await db.commit()
        log.debug(
            "inflight_lock acquire ok  key=%s  session=%s  kind=%s  pid=%d",
            key, session_id, kind, os.getpid(),
        )
        return True
    except sqlite3.IntegrityError:
        # UNIQUE PRIMARY KEY 衝突 — 已有人 hold（預期路徑）
        log.debug(
            "inflight_lock acquire race_lost  key=%s  session=%s  kind=%s",
            key, session_id, kind,
        )
        return False
    except Exception:
        # 非預期錯誤（DB 連線壞 / schema 壞 / etc）— 留 trace 但保留 False 行為
        log.warning(
            "inflight_lock acquire unexpected_error → treating as race_lost  "
            "key=%s  session=%s  kind=%s",
            key, session_id, kind,
            exc_info=True,
        )
        return False


async def release(key: str) -> None:
    log = ws_logger()
    db = await get_db()
    cur = await db.execute("DELETE FROM inflight_locks WHERE key = ?", (key,))
    await db.commit()
    log.debug(
        "inflight_lock release  key=%s  deleted=%d",
        key, cur.rowcount or 0,
    )


async def is_active(key: str) -> bool:
    db = await get_db()
    cur = await db.execute(
        "SELECT 1 FROM inflight_locks WHERE key = ? LIMIT 1", (key,)
    )
    row = await cur.fetchone()
    return row is not None


async def has_session_inflight(
    session_id: str,
    *,
    exclude_kinds: tuple[str, ...] = (),
) -> bool:
    """此 session 是否有任一 inflight 任務（含 submit_answer → run_stage 等子 key）。

    exclude_kinds：resume_session 等「查詢用」lock 不應視為 stage 仍在生成。
    """
    db = await get_db()
    if exclude_kinds:
        placeholders = ",".join("?" * len(exclude_kinds))
        sql = (
            f"SELECT 1 FROM inflight_locks "
            f"WHERE session_id = ? AND kind NOT IN ({placeholders}) LIMIT 1"
        )
        params: tuple = (session_id, *exclude_kinds)
    else:
        sql = "SELECT 1 FROM inflight_locks WHERE session_id = ? LIMIT 1"
        params = (session_id,)
    cur = await db.execute(sql, params)
    return (await cur.fetchone()) is not None


async def active_keys_for_session(session_id: str) -> list[str]:
    db = await get_db()
    cur = await db.execute(
        "SELECT key FROM inflight_locks WHERE session_id = ?",
        (session_id,),
    )
    rows = await cur.fetchall()
    return [r[0] for r in rows]


async def cleanup_dead_worker_locks() -> int:
    """清掉 worker_pid 已不存在（reload / crash）的孤兒 lock。"""
    log = ws_logger()
    db = await get_db()
    async with db.execute(
        "SELECT key, session_id, kind, worker_pid FROM inflight_locks"
    ) as cur:
        rows = await cur.fetchall()
    dead = [r for r in rows if not _pid_alive(r[3])]
    if dead:
        log.info(
            "inflight_lock cleanup_dead_worker targets  count=%d  entries=%s",
            len(dead),
            [(r[0], r[1], r[2], r[3]) for r in dead],
        )
    n = 0
    for row in dead:
        await release(row[0])
        n += 1
    return n


async def cleanup_stale(max_age_s: float = 600) -> int:
    """清掉 started_at 過老的孤兒（worker 強制關閉時殘留）。回傳清掉的數量。"""
    log = ws_logger()
    cutoff = time.time() - max_age_s
    db = await get_db()
    # 先撈出要被清的細節，方便 debug
    async with db.execute(
        "SELECT key, session_id, kind, worker_pid, started_at "
        "FROM inflight_locks WHERE started_at < ?",
        (cutoff,),
    ) as cur:
        stale = await cur.fetchall()
    if stale:
        log.debug(
            "inflight_lock cleanup_stale targets  cutoff=%.0f  count=%d  entries=%s",
            cutoff, len(stale),
            [(r[0], r[1], r[2], r[3]) for r in stale],
        )
    cur = await db.execute(
        "DELETE FROM inflight_locks WHERE started_at < ?", (cutoff,)
    )
    await db.commit()
    return cur.rowcount or 0
