"""email_whitelist schema 回歸（PostgreSQL）：表存在 + role CHECK 約束。

註：fresh-start 不再自動 seed（舊的 _seed_whitelist_from_users 已移除），
故僅驗 schema 本身；角色行為由 auth 測試（test_role_lookup / test_register_whitelist）覆蓋。
"""
import os
import unittest

import asyncpg

from backend.db.database import close_db, get_db, init_db


class TestEmailWhitelistSchema(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await init_db(os.environ["DATABASE_URL"], reset=True)

    async def asyncTearDown(self) -> None:
        await close_db()

    async def test_table_exists_and_insert(self):
        db = await get_db()
        await db.execute(
            "INSERT INTO email_whitelist (email, role) VALUES ($1, $2)",
            "a@example.com", "admin",
        )
        row = await db.fetchrow(
            "SELECT role FROM email_whitelist WHERE email = $1", "a@example.com"
        )
        self.assertEqual(row["role"], "admin")

    async def test_role_check_rejects_invalid(self):
        db = await get_db()
        with self.assertRaises(asyncpg.PostgresError):
            await db.execute(
                "INSERT INTO email_whitelist (email, role) VALUES ($1, $2)",
                "b@example.com", "superuser",
            )


if __name__ == "__main__":
    unittest.main()
