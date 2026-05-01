import tempfile
import unittest
from pathlib import Path

from backend.auth.utils import create_token, decode_token_active
from backend.db.database import close_db, get_db, init_db


class TestAuthSingleLogin(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        db_path = str(Path(self._tmp_dir.name) / "test.db")
        await init_db(db_path)

    async def asyncTearDown(self) -> None:
        await close_db()
        self._tmp_dir.cleanup()

    async def test_old_token_invalid_after_session_version_rotates(self):
        db = await get_db()
        await db.execute(
            "INSERT INTO users (user_id, email, password_hash, session_version) VALUES (?, ?, ?, ?)",
            ("u1", "u1@example.com", "hash", 1),
        )
        await db.commit()

        token_v1 = create_token("u1", "u1@example.com", session_version=1)
        self.assertIsNotNone(await decode_token_active(token_v1))

        await db.execute(
            "UPDATE users SET session_version = 2 WHERE user_id = ?",
            ("u1",),
        )
        await db.commit()

        self.assertIsNone(await decode_token_active(token_v1))


if __name__ == "__main__":
    unittest.main()
