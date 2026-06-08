"""Generating watchdog 偵測/標記測試（PostgreSQL / testcontainers）。"""
import os
import unittest

from backend.db.database import init_db, close_db, get_db
from backend.db import inflight_lock
from backend.memory import session_memory
from backend.memory import curriculum_checkpoint as ckpt
from backend.memory import curriculum_watchdog as wd


async def _ensure_user(user_id: str = "u1") -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO users (user_id, email, password_hash) VALUES ($1, $2, $3)"
        " ON CONFLICT (user_id) DO NOTHING",
        user_id, f"{user_id}@test.local", "hash",
    )


async def _age_session(session_id: str, seconds: float) -> None:
    """把 sessions.updated_at 往前推 seconds（模擬已生成一段時間）。

    僅動 sessions.updated_at；不影響 curriculum_checkpoints.updated_at。
    """
    db = await get_db()
    await db.execute(
        "UPDATE sessions SET updated_at = now() - make_interval(secs => $2) "
        "WHERE session_id = $1",
        session_id, float(seconds),
    )


class TestFindDead(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db(os.environ["DATABASE_URL"], reset=True)
        await _ensure_user()

    async def asyncTearDown(self):
        await close_db()

    async def _mk(self, sid: str, age_s: float) -> None:
        await session_memory.create_generating_stub(sid, "u1", "h")
        await _age_session(sid, age_s)

    async def test_fresh_no_lock_not_dead(self):
        await self._mk("s_fresh", 10)
        dead = await wd.find_dead_generating_sessions(stale_s=600, hardcap_s=3600)
        self.assertEqual(dead, [])

    async def test_old_no_lock_is_stale_dead(self):
        await self._mk("s_old", 700)
        dead = await wd.find_dead_generating_sessions(stale_s=600, hardcap_s=3600)
        self.assertEqual([d["session_id"] for d in dead], ["s_old"])
        self.assertEqual(dead[0]["reason"], "stale_no_lock")

    async def test_old_with_active_lock_not_dead(self):
        await self._mk("s_lock", 700)
        await inflight_lock.acquire("s_lock:start", session_id="s_lock", kind="start_session")
        dead = await wd.find_dead_generating_sessions(stale_s=600, hardcap_s=3600)
        self.assertEqual(dead, [])

    async def test_fresh_checkpoint_keeps_alive(self):
        await self._mk("s_ckpt", 700)
        # checkpoint 於 ageing 之後 upsert → cp.updated_at = now()，idle 很新；
        # 即使 session.updated_at 老，GREATEST 取 checkpoint 心跳故不算 stale。
        await ckpt.upsert_checkpoint("s_ckpt", content_hash="h")
        dead = await wd.find_dead_generating_sessions(stale_s=600, hardcap_s=3600)
        self.assertEqual(dead, [])

    async def test_within_stale_window_not_dead(self):
        # age 落在 stale 窗內（599 < 600）→ SQL 算術不應跨越邊界
        await self._mk("s_edge", 599)
        dead = await wd.find_dead_generating_sessions(stale_s=600, hardcap_s=3600)
        self.assertEqual(dead, [])

    async def test_lock_but_over_hardcap_dead(self):
        await self._mk("s_hard", 4000)
        await inflight_lock.acquire("s_hard:start", session_id="s_hard", kind="start_session")
        dead = await wd.find_dead_generating_sessions(stale_s=600, hardcap_s=3600)
        self.assertEqual([d["session_id"] for d in dead], ["s_hard"])
        self.assertEqual(dead[0]["reason"], "hardcap_timeout")

    async def test_null_checkpoint_no_crash(self):
        await self._mk("s_null", 700)
        dead = await wd.find_dead_generating_sessions(stale_s=600, hardcap_s=3600)
        self.assertEqual([d["session_id"] for d in dead], ["s_null"])

    async def test_query_only_lock_treated_as_no_lock(self):
        await self._mk("s_qonly", 700)
        await inflight_lock.acquire("s_qonly:resume", session_id="s_qonly", kind="resume_session")
        dead = await wd.find_dead_generating_sessions(stale_s=600, hardcap_s=3600)
        self.assertEqual([d["session_id"] for d in dead], ["s_qonly"])

    async def test_non_generating_ignored(self):
        await session_memory.create_generating_stub("s_active", "u1", "h")
        db = await get_db()
        await db.execute("UPDATE sessions SET status='active' WHERE session_id='s_active'")
        await _age_session("s_active", 5000)
        dead = await wd.find_dead_generating_sessions(stale_s=600, hardcap_s=3600)
        self.assertEqual(dead, [])

    async def test_valid_lock_overrides_query_only(self):
        await self._mk("s_both", 700)
        await inflight_lock.acquire("s_both:resume", session_id="s_both", kind="resume_session")
        await inflight_lock.acquire("s_both:start", session_id="s_both", kind="start_session")
        dead = await wd.find_dead_generating_sessions(stale_s=600, hardcap_s=3600)
        self.assertEqual(dead, [])


class TestClassify(unittest.TestCase):
    """純判定函式邊界（不碰 DB，邊界精確且無時序脆弱）。spec：嚴格 `>`。"""

    def test_stale_boundary_exclusive(self):
        self.assertIsNone(wd._classify_dead(
            age_s=100, idle_s=600.0, has_lock=False, stale_s=600, hardcap_s=3600))
        self.assertEqual(wd._classify_dead(
            age_s=100, idle_s=600.01, has_lock=False, stale_s=600, hardcap_s=3600),
            "stale_no_lock")

    def test_hardcap_boundary_exclusive(self):
        self.assertIsNone(wd._classify_dead(
            age_s=3600.0, idle_s=0, has_lock=True, stale_s=600, hardcap_s=3600))
        self.assertEqual(wd._classify_dead(
            age_s=3600.01, idle_s=0, has_lock=True, stale_s=600, hardcap_s=3600),
            "hardcap_timeout")

    def test_hardcap_precedence_over_stale(self):
        self.assertEqual(wd._classify_dead(
            age_s=4000, idle_s=700, has_lock=False, stale_s=600, hardcap_s=3600),
            "hardcap_timeout")

    def test_stale_requires_no_lock(self):
        self.assertIsNone(wd._classify_dead(
            age_s=100, idle_s=700, has_lock=True, stale_s=600, hardcap_s=3600))


class TestSweep(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db(os.environ["DATABASE_URL"], reset=True)
        await _ensure_user()

    async def asyncTearDown(self):
        await close_db()

    async def _mk_old(self, sid: str) -> None:
        await session_memory.create_generating_stub(sid, "u1", "h")
        await _age_session(sid, 700)

    async def test_sweep_marks_failed(self):
        await self._mk_old("s1")
        n = await wd.sweep_dead_generating(stale_s=600, hardcap_s=3600)
        self.assertEqual(n, 1)
        row = await session_memory.get_session("s1")
        self.assertEqual(row["status"], "failed")

    async def test_sweep_preserves_source_chunks(self):
        await self._mk_old("s2")
        await session_memory.insert_source_chunks(
            "s2", [{"chunk_id": "c1", "text": "hi", "order_index": 0}]
        )
        await wd.sweep_dead_generating(stale_s=600, hardcap_s=3600)
        chunks = await session_memory.get_source_chunks("s2")
        self.assertEqual(len(chunks), 1)

    async def test_sweep_idempotent(self):
        await self._mk_old("s3")
        first = await wd.sweep_dead_generating(stale_s=600, hardcap_s=3600)
        second = await wd.sweep_dead_generating(stale_s=600, hardcap_s=3600)
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)

    async def test_sweep_does_not_touch_abandoned(self):
        await self._mk_old("s4")
        db = await get_db()
        await db.execute("UPDATE sessions SET status='abandoned' WHERE session_id='s4'")
        n = await wd.sweep_dead_generating(stale_s=600, hardcap_s=3600)
        self.assertEqual(n, 0)
        row = await session_memory.get_session("s4")
        self.assertEqual(row["status"], "abandoned")
