"""DB-backed inflight lock CRUD test。"""
import asyncio
import os
import unittest

from backend.db.database import init_db, close_db, get_db
from backend.db.inflight_lock import acquire, release, is_active, cleanup_stale, cleanup_dead_worker_locks


class TestInflightLockDb(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db(os.environ["DATABASE_URL"], reset=True)

    async def asyncTearDown(self):
        await close_db()

    async def test_acquire_and_release(self):
        ok = await acquire("k1", session_id="s", kind="tutor")
        self.assertTrue(ok)
        self.assertTrue(await is_active("k1"))
        await release("k1")
        self.assertFalse(await is_active("k1"))

    async def test_acquire_when_existing_returns_false(self):
        await acquire("k2", session_id="s", kind="tutor")
        ok = await acquire("k2", session_id="s", kind="tutor")
        self.assertFalse(ok)  # 已有人 hold

    async def test_cleanup_removes_stale_but_keeps_recent(self):
        await acquire("old", session_id="s", kind="x")
        await acquire("fresh", session_id="s", kind="x")
        # 把 "old" 的 started_at 改成 700 秒前
        db = await get_db()
        await db.execute(
            "UPDATE inflight_locks SET started_at = started_at - 700 WHERE key = $1",
            "old",
        )

        n = await cleanup_stale(max_age_s=600)
        self.assertEqual(n, 1)
        self.assertFalse(await is_active("old"))
        self.assertTrue(await is_active("fresh"))

    async def test_release_nonexistent_is_noop(self):
        # 不該拋例外
        await release("nonexistent_key")
        self.assertFalse(await is_active("nonexistent_key"))

    async def test_cleanup_dead_worker_locks(self):
        await acquire("dead_k", session_id="s", kind="resume_session")
        db = await get_db()
        await db.execute(
            "UPDATE inflight_locks SET worker_pid = $1 WHERE key = $2",
            99999999, "dead_k",
        )
        n = await cleanup_dead_worker_locks()
        self.assertEqual(n, 1)
        self.assertFalse(await is_active("dead_k"))

    async def test_meta_json_persisted(self):
        await acquire("k_meta", session_id="s", kind="tutor", meta_json='{"q": "hi"}')
        db = await get_db()
        row = await db.fetchrow("SELECT meta_json FROM inflight_locks WHERE key = $1", "k_meta")
        self.assertEqual(row[0], '{"q": "hi"}')


class TestInflightLockConcurrent(unittest.IsolatedAsyncioTestCase):
    """v2 plan B2 Step 7：cleanup_stale 與 acquire 並行不應 deadlock。"""

    async def asyncSetUp(self):
        await init_db(os.environ["DATABASE_URL"], reset=True)

    async def asyncTearDown(self):
        await close_db()

    async def test_cleanup_stale_concurrent_with_acquire_no_deadlock(self):
        async def _run():
            await asyncio.gather(
                cleanup_stale(max_age_s=0.001),
                acquire("k1", session_id="s", kind="tutor"),
                cleanup_stale(max_age_s=0.001),
                acquire("k2", session_id="s", kind="tutor"),
            )

        await asyncio.wait_for(_run(), timeout=5.0)


if __name__ == "__main__":
    unittest.main()
