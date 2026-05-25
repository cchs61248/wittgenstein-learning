"""Curriculum checkpoint CRUD tests."""
import os
import tempfile
import unittest

from backend.db.database import init_db, close_db, get_db
from backend.memory import curriculum_checkpoint as ckpt
from backend.memory import session_memory


async def _ensure_user(user_id: str = "u1") -> None:
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO users (user_id, email, password_hash) VALUES (?, ?, ?)",
        (user_id, f"{user_id}@test.local", "hash"),
    )
    await db.commit()


class TestCurriculumCheckpoint(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test.db")
        await init_db(self._db_path)
        await _ensure_user()

    async def asyncTearDown(self):
        await close_db()
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)

    async def test_upsert_and_load_roundtrip(self):
        await session_memory.create_generating_stub("sess_x", "u1", "abc")
        await ckpt.upsert_checkpoint(
            "sess_x",
            content_hash="abc",
            pipeline_meta={"target_depth": "intermediate", "user_id": "u1"},
        )
        loaded = await ckpt.load_checkpoint("sess_x")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded["content_hash"], "abc")
        self.assertEqual(loaded["completed_region_ids"], [])
        self.assertEqual(loaded["pipeline_meta"]["target_depth"], "intermediate")

    async def test_partial_update_merges(self):
        await session_memory.create_generating_stub("sess_y", "u1", "h1")
        await ckpt.upsert_checkpoint(
            "sess_y",
            content_hash="h1",
            pipeline_meta={"user_id": "u1"},
        )
        await ckpt.upsert_checkpoint(
            "sess_y",
            completed_region_ids=["region_000"],
            all_candidates=[{"title": "A"}],
        )
        loaded = await ckpt.load_checkpoint("sess_y")
        assert loaded is not None
        self.assertEqual(loaded["content_hash"], "h1")
        self.assertEqual(loaded["completed_region_ids"], ["region_000"])
        self.assertEqual(len(loaded["all_candidates"]), 1)

    async def test_delete_checkpoint(self):
        await session_memory.create_generating_stub("sess_z", "u1", "h")
        await ckpt.upsert_checkpoint("sess_z", content_hash="h")
        await ckpt.delete_checkpoint("sess_z")
        self.assertIsNone(await ckpt.load_checkpoint("sess_z"))

    async def test_list_resumable_sessions(self):
        await session_memory.create_generating_stub(
            "sess_r", "u1", "hash1",
        )
        await session_memory.insert_source_chunks(
            "sess_r",
            [{"chunk_id": "c1", "text": "hello", "order_index": 0}],
        )
        await ckpt.upsert_checkpoint("sess_r", content_hash="hash1")

        resumable = await ckpt.list_resumable_sessions()
        self.assertIn("sess_r", resumable)

        await ckpt.delete_checkpoint("sess_r")
        self.assertEqual(await ckpt.list_resumable_sessions(), [])
