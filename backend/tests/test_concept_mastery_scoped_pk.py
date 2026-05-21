"""Migration 018：同 user 不同 source_signature 可各有同名 concept。"""
import os
import tempfile
import unittest

from backend.db.database import init_db, close_db
from backend.memory import longterm_memory


class TestConceptMasteryScopedPk(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test.db")
        await init_db(self._db_path)
        from backend.db.database import get_db
        db = await get_db()
        await db.execute(
            "INSERT INTO users (user_id, email, password_hash) VALUES (?, ?, ?)",
            ("u1", "u1@test", "x"),
        )
        await db.commit()

    async def asyncTearDown(self):
        await close_db()
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)

    async def test_same_concept_two_signatures_coexist(self):
        await longterm_memory.update_concept_mastery(
            "u1", "槓桿", 0.9, source_signature="hash_book_a"
        )
        await longterm_memory.update_concept_mastery(
            "u1", "槓桿", 0.4, source_signature="hash_book_b"
        )
        map_a = await longterm_memory.get_user_mastery_map(
            "u1", threshold=0.0, source_signature="hash_book_a"
        )
        map_b = await longterm_memory.get_user_mastery_map(
            "u1", threshold=0.0, source_signature="hash_book_b"
        )
        self.assertAlmostEqual(map_a["槓桿"], 0.9, places=2)
        self.assertAlmostEqual(map_b["槓桿"], 0.4, places=2)


if __name__ == "__main__":
    unittest.main()
