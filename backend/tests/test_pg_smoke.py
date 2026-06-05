import os
import unittest

from backend.db.database import close_db, get_db, init_db


class TestPgSmoke(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await init_db(os.environ["DATABASE_URL"], reset=True)

    async def asyncTearDown(self) -> None:
        await close_db()

    async def test_insert_and_read(self):
        db = await get_db()
        await db.execute(
            "INSERT INTO users (user_id, email, password_hash) VALUES ($1,$2,$3)",
            "u1", "u1@x", "h",
        )
        row = await db.fetchrow("SELECT email FROM users WHERE user_id=$1", "u1")
        self.assertEqual(row["email"], "u1@x")

    async def test_reset_isolates(self):
        db = await get_db()
        n = await db.fetchval("SELECT count(*) FROM users")
        self.assertEqual(n, 0)  # 上個測試的 u1 已被 reset 清掉
