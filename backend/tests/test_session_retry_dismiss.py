"""retry / dismiss endpoint 與 abandon_failed_session（直接驗證 service 層）。"""
import os
import unittest

from backend.db.database import init_db, close_db, get_db
from backend.memory import session_memory


async def _ensure_user(user_id: str = "u1") -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO users (user_id, email, password_hash) VALUES ($1, $2, $3)"
        " ON CONFLICT (user_id) DO NOTHING",
        user_id, f"{user_id}@test.local", "hash",
    )


class TestDismiss(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db(os.environ["DATABASE_URL"], reset=True)
        await _ensure_user()

    async def asyncTearDown(self):
        await close_db()

    async def test_dismiss_failed_marks_abandoned(self):
        await session_memory.create_generating_stub("s_d", "u1", "h")
        db = await get_db()
        await db.execute("UPDATE sessions SET status='failed' WHERE session_id='s_d'")
        await session_memory.abandon_failed_session("s_d")
        row = await session_memory.get_session("s_d")
        self.assertEqual(row["status"], "abandoned")

    async def test_dismiss_ignores_non_failed(self):
        await session_memory.create_generating_stub("s_g", "u1", "h")
        await session_memory.abandon_failed_session("s_g")  # 仍 generating，不動
        row = await session_memory.get_session("s_g")
        self.assertEqual(row["status"], "generating")
