import os
import unittest

from backend.auth.utils import get_role_by_email, is_email_whitelisted
from backend.db.database import close_db, get_db, init_db


class TestRoleLookup(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await init_db(os.environ["DATABASE_URL"], reset=True)
        db = await get_db()
        await db.execute(
            "INSERT INTO email_whitelist (email, role) VALUES ($1, $2)",
            "admin@example.com", "admin",
        )
        await db.execute(
            "INSERT INTO email_whitelist (email, role) VALUES ($1, $2)",
            "learner@example.com", "user",
        )

    async def asyncTearDown(self) -> None:
        await close_db()

    async def test_role_admin(self):
        self.assertEqual(await get_role_by_email("admin@example.com"), "admin")

    async def test_role_user(self):
        self.assertEqual(await get_role_by_email("learner@example.com"), "user")

    async def test_role_unknown_defaults_user(self):
        self.assertEqual(await get_role_by_email("ghost@example.com"), "user")

    async def test_is_whitelisted(self):
        self.assertTrue(await is_email_whitelisted("admin@example.com"))
        self.assertFalse(await is_email_whitelisted("ghost@example.com"))


if __name__ == "__main__":
    unittest.main()
