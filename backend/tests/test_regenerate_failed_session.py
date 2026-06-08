"""regenerate_failed_session 測試（in-process 路徑，避免依賴 Redis）。"""
import os
import unittest
from unittest.mock import patch, AsyncMock

from backend.db.database import init_db, close_db, get_db
from backend.memory import session_memory
from backend.memory import curriculum_checkpoint as ckpt
from backend.jobs.regenerate import regenerate_failed_session, RegenerateError


async def _ensure_user(user_id: str = "u1") -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO users (user_id, email, password_hash) VALUES ($1, $2, $3)"
        " ON CONFLICT (user_id) DO NOTHING",
        user_id, f"{user_id}@test.local", "hash",
    )


async def _mk_failed(sid: str, *, with_chunks: bool = True) -> None:
    await session_memory.create_generating_stub(
        sid, "u1", "h", target_depth="intermediate", question_mode="short_answer",
    )
    if with_chunks:
        await session_memory.insert_source_chunks(
            sid, [{"chunk_id": "c1", "text": "hi", "order_index": 0}]
        )
    db = await get_db()
    await db.execute("UPDATE sessions SET status='failed' WHERE session_id=$1", sid)


class TestRegenerate(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db(os.environ["DATABASE_URL"], reset=True)
        await _ensure_user()

    async def asyncTearDown(self):
        await close_db()

    async def test_rejects_non_failed(self):
        await session_memory.create_generating_stub("s_gen", "u1", "h")
        with self.assertRaises(RegenerateError):
            await regenerate_failed_session("s_gen", use_arq=False)

    async def test_rejects_missing_chunks(self):
        await _mk_failed("s_nochunk", with_chunks=False)
        with self.assertRaises(RegenerateError):
            await regenerate_failed_session("s_nochunk", use_arq=False)

    async def test_flips_back_to_generating_and_rebuilds_checkpoint(self):
        await _mk_failed("s_ok")
        with patch(
            "backend.jobs.regenerate.resume_generating_session_background",
            new=AsyncMock(),
        ):
            result = await regenerate_failed_session("s_ok", use_arq=False)
        self.assertEqual(result["status"], "generating")
        row = await session_memory.get_session("s_ok")
        self.assertEqual(row["status"], "generating")
        cp = await ckpt.load_checkpoint("s_ok")
        self.assertIsNotNone(cp)
        self.assertEqual(cp["completed_region_ids"], [])
