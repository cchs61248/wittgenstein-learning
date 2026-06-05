"""Tests for curriculum session prepare and Arq enqueue."""
import os
import unittest
from unittest.mock import AsyncMock, MagicMock

from backend.db.database import init_db, close_db, get_db
from backend.memory import curriculum_checkpoint as ckpt
from backend.memory import session_memory
from backend.jobs.session_prepare import prepare_curriculum_session
from backend.jobs.enqueue import enqueue_curriculum_job
from backend.utils.content_hash import compute_content_hash


async def _ensure_user(user_id: str = "u1") -> None:
    db = await get_db()
    await db.execute(
        "INSERT INTO users (user_id, email, password_hash) VALUES ($1, $2, $3) ON CONFLICT (user_id) DO NOTHING",
        user_id, f"{user_id}@test.local", "hash",
    )


class TestSessionPrepare(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db(os.environ["DATABASE_URL"], reset=True)
        await _ensure_user()

    async def asyncTearDown(self):
        await close_db()

    async def test_prepare_persists_stub_chunks_and_checkpoint_meta(self):
        chunks = [{"chunk_id": "c0", "order_index": 0, "text": "hello"}]
        content_hash = compute_content_hash(chunks)
        await prepare_curriculum_session(
            session_id="sess_prep",
            user_id="u1",
            source_chunks=chunks,
            content_hash=content_hash,
            target_depth="intermediate",
            question_mode="short_answer",
            provider_name="claude",
            model_name="m",
            source_file_ids=["upl_1"],
            sources_json=[{"source_id": "src_a", "source_index": 0, "source_label": "A"}],
            same_material=True,
        )
        row = await session_memory.get_session("sess_prep")
        assert row is not None
        self.assertEqual(row["status"], "generating")
        db_chunks = await session_memory.get_source_chunks("sess_prep")
        self.assertEqual(len(db_chunks), 1)
        loaded = await ckpt.load_checkpoint("sess_prep")
        assert loaded is not None
        self.assertEqual(loaded["pipeline_meta"]["target_depth"], "intermediate")


class TestEnqueueCurriculumJob(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db(os.environ["DATABASE_URL"], reset=True)
        await _ensure_user()

    async def asyncTearDown(self):
        await close_db()

    async def test_enqueue_acquires_lock_and_enqueues(self):
        redis = AsyncMock()
        job = MagicMock()
        job.job_id = "job-1"
        redis.enqueue_job = AsyncMock(return_value=job)
        job_id = await enqueue_curriculum_job(redis, "sess_q")
        self.assertEqual(job_id, "job-1")
        redis.enqueue_job.assert_awaited_once()

    async def test_enqueue_skips_when_lock_held(self):
        redis = AsyncMock()
        await enqueue_curriculum_job(redis, "sess_a")
        job_id = await enqueue_curriculum_job(redis, "sess_a")
        self.assertIsNone(job_id)
        redis.enqueue_job.assert_awaited_once()
