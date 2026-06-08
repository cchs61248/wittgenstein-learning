import os
import unittest

from backend.auth.utils import create_token, decode_token_active
from backend.db.database import close_db, get_db, init_db


class TestAuthSingleLogin(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await init_db(os.environ["DATABASE_URL"], reset=True)

    async def asyncTearDown(self) -> None:
        await close_db()

    async def test_old_token_invalid_after_session_version_rotates(self):
        db = await get_db()
        await db.execute(
            "INSERT INTO users (user_id, email, password_hash, session_version) VALUES ($1, $2, $3, $4)",
            "u1", "u1@example.com", "hash", 1,
        )

        token_v1 = create_token("u1", "u1@example.com", session_version=1)
        self.assertIsNotNone(await decode_token_active(token_v1))

        await db.execute(
            "UPDATE users SET session_version = 2 WHERE user_id = $1",
            "u1",
        )

        self.assertIsNone(await decode_token_active(token_v1))


if __name__ == "__main__":
    unittest.main()
