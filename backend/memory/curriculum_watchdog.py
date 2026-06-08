"""Stuck-session fail-safe：偵測卡死的 generating session 並標記 failed。

責任很窄：只認「看起來不會再前進」的 generating session，標 failed（UI 可重試）。
不做 resume、不做 worker recovery。
"""
from __future__ import annotations

import logging

from ..db.database import get_db

_log = logging.getLogger("wl.watchdog")

# 不代表「正在生成」的 lock kind（查詢用），視為無有效鎖
QUERY_ONLY_KINDS: tuple[str, ...] = ("resume_session",)

# 不變式：curriculum_checkpoints.session_id 是 PRIMARY KEY（schema.sql），
# 故一個 session 至多一列 checkpoint，下方 LEFT JOIN 不會讓 session 重複。
_FIND_SQL = """
SELECT s.session_id,
       EXISTS (
         SELECT 1 FROM inflight_locks l
         WHERE l.session_id = s.session_id
           AND NOT (l.kind = ANY($1::text[]))
       ) AS has_lock,
       EXTRACT(EPOCH FROM (now() - s.updated_at)) AS age_seconds,
       EXTRACT(EPOCH FROM (now() -
           GREATEST(s.updated_at, COALESCE(cp.updated_at, s.updated_at)))) AS idle_seconds
FROM sessions s
LEFT JOIN curriculum_checkpoints cp ON cp.session_id = s.session_id
WHERE s.status = 'generating'
ORDER BY s.session_id
"""


def _classify_dead(
    *, age_s: float, idle_s: float, has_lock: bool, stale_s: float, hardcap_s: float
) -> str | None:
    """純判定：回傳 reason 或 None。邊界用嚴格 `>`（age==hardcap / idle==stale 不算死）。

    hardcap 優先於 stale。抽成純函式以便精確、無時序脆弱地測邊界。
    """
    if age_s > hardcap_s:
        return "hardcap_timeout"
    if idle_s > stale_s and not has_lock:
        return "stale_no_lock"
    return None


async def find_dead_generating_sessions(*, stale_s: float, hardcap_s: float) -> list[dict]:
    """回傳判定為死亡的 generating session（含 reason），依 session_id 排序。

    死亡 = age > hardcap（不論鎖）或（idle > stale 且 無有效鎖）。

    注意（競態）：本函式與呼叫端的 UPDATE 是兩次獨立 pool 獲取；呼叫端在標記前
    必須以 `WHERE status='generating'` 做 CAS，避免兩次之間狀態已改變而誤覆寫。
    """
    db = await get_db()
    rows = await db.fetch(_FIND_SQL, list(QUERY_ONLY_KINDS))
    dead: list[dict] = []
    for r in rows:
        age = float(r["age_seconds"])
        idle = float(r["idle_seconds"])
        has_lock = bool(r["has_lock"])
        reason = _classify_dead(
            age_s=age, idle_s=idle, has_lock=has_lock,
            stale_s=stale_s, hardcap_s=hardcap_s,
        )
        if reason is None:
            continue
        dead.append({
            "session_id": r["session_id"],
            "age_seconds": age,
            "idle_seconds": idle,
            "has_lock": has_lock,
            "reason": reason,
        })
    return dead


async def sweep_dead_generating(*, stale_s: float, hardcap_s: float) -> int:
    """標記死亡 session 為 failed（冪等），逐筆 log reason，回傳實際標記數量。

    以 `WHERE status='generating'` 做 CAS：兩次 pool 獲取之間若狀態已被改成
    abandoned/active 等，RETURNING 不會回傳該列，故不會誤覆寫。
    """
    dead = await find_dead_generating_sessions(stale_s=stale_s, hardcap_s=hardcap_s)
    if not dead:
        return 0
    ids = [d["session_id"] for d in dead]
    db = await get_db()
    rows = await db.fetch(
        "UPDATE sessions SET status = 'failed', updated_at = now() "
        "WHERE session_id = ANY($1::text[]) AND status = 'generating' "
        "RETURNING session_id",
        ids,
    )
    marked = {r["session_id"] for r in rows}
    for d in dead:
        if d["session_id"] not in marked:
            continue
        if d["reason"] == "hardcap_timeout":
            _log.warning(
                "watchdog marked session failed: reason=hardcap_timeout "
                "session_id=%s age_seconds=%.0f",
                d["session_id"], d["age_seconds"],
            )
        else:
            _log.warning(
                "watchdog marked session failed: reason=stale_no_lock "
                "session_id=%s idle_seconds=%.0f",
                d["session_id"], d["idle_seconds"],
            )
    return len(marked)
