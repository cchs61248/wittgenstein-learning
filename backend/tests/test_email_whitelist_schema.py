import tempfile
import unittest
from pathlib import Path

from backend.db.database import (
    close_db,
    get_db,
    init_db,
    _seed_whitelist_from_users,
)


class TestEmailWhitelistSchema(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        db_path = str(Path(self._tmp_dir.name) / "test.db")
        await init_db(db_path)

    async def asyncTearDown(self) -> None:
        await close_db()
        self._tmp_dir.cleanup()

    async def test_table_exists_and_role_check(self):
        db = await get_db()
        await db.execute(
            "INSERT INTO email_whitelist (email, role) VALUES (?, ?)",
            ("a@example.com", "admin"),
        )
        await db.commit()
        async with db.execute(
            "SELECT role FROM email_whitelist WHERE email = ?", ("a@example.com",)
        ) as cur:
            row = await cur.fetchone()
        self.assertEqual(row[0], "admin")
        with self.assertRaises(Exception):
            await db.execute(
                "INSERT INTO email_whitelist (email, role) VALUES (?, ?)",
                ("b@example.com", "superuser"),
            )
            await db.commit()

    async def test_seed_only_when_empty(self):
        db = await get_db()
        await db.execute(
            "INSERT INTO users (user_id, email, password_hash, session_version) VALUES (?,?,?,?)",
            ("u1", "u1@example.com", "h", 1),
        )
        await db.execute(
            "INSERT INTO users (user_id, email, password_hash, session_version) VALUES (?,?,?,?)",
            ("u2", "u2@example.com", "h", 1),
        )
        await db.commit()

        added = await _seed_whitelist_from_users(db)
        self.assertEqual(added, 2)
        async with db.execute(
            "SELECT role FROM email_whitelist WHERE email = ?", ("u1@example.com",)
        ) as cur:
            row = await cur.fetchone()
        self.assertEqual(row[0], "admin")

        await db.execute(
            "UPDATE email_whitelist SET role='user' WHERE email='u1@example.com'"
        )
        await db.commit()
        added2 = await _seed_whitelist_from_users(db)
        self.assertEqual(added2, 0)
        async with db.execute(
            "SELECT role FROM email_whitelist WHERE email = ?", ("u1@example.com",)
        ) as cur:
            row = await cur.fetchone()
        self.assertEqual(row[0], "user")


if __name__ == "__main__":
    unittest.main()
