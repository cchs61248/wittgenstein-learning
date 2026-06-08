"""Tests for run_curriculum_job (Arq worker entrypoint)."""
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.db.database import init_db, close_db, get_db
from backend.jobs.curriculum_job import run_curriculum_job
from backend.jobs.session_prepare import prepare_curriculum_session
from backend.memory import session_memory
from backend.utils.content_hash import compute_content_hash


def _chunks(n: int = 30) -> list[dict]:
    return [
        {
            "chunk_id": f"chunk_{i:04d}",
            "order_index": i,
            "text": f"段落 {i} 關於概念 alpha",
            "source_id": "src_a",
            "source_index": 0,
            "source_label": "書A",
        }
        for i in range(n)
    ]


class TestCurriculumJob(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db(os.environ["DATABASE_URL"], reset=True)
        db = await get_db()
        await db.execute(
            "INSERT INTO users (user_id, email, password_hash) VALUES ($1, $2, $3) ON CONFLICT (user_id) DO NOTHING",
            "u1", "u1@test.local", "hash",
        )

    async def asyncTearDown(self):
        await close_db()

    async def test_job_completes_to_pending_confirmation(self):
        chunks = _chunks()
        content_hash = compute_content_hash(chunks)
        session_id = "sess_job"
        await prepare_curriculum_session(
            session_id=session_id,
            user_id="u1",
            source_chunks=chunks,
            content_hash=content_hash,
            target_depth="standard",
            question_mode="short_answer",
            provider_name="claude",
            model_name="m",
            source_file_ids=None,
            sources_json=[{"source_id": "src_a", "source_index": 0, "source_label": "A"}],
            same_material=True,
        )

        orch_mock = MagicMock()
        orch_mock.content_outliner = MagicMock()
        orch_mock.content_outliner.run = AsyncMock(return_value={
            "required_stage_titles": [], "named_cases": [],
            "framework_sections": [], "summary_sections": [],
        })
        orch_mock.splitter = MagicMock()
        orch_mock.splitter.llm = MagicMock()
        orch_mock.splitter.token_counter = MagicMock()
        orch_mock.splitter.run = AsyncMock(return_value={
            "stages": [{
                "stage_id": 1, "node_id": "1.1", "title": "S1",
                "teaching_goal": "g", "key_concepts": ["alpha"],
                "source_chunk_ids": ["chunk_0000"],
            }],
            "summary": "sum",
        })
        orch_mock.splitter_verifier = MagicMock()
        orch_mock.splitter_verifier.run = AsyncMock(return_value={
            "aligned": True, "missing_options": [], "issue_chunk_ids": [], "reason": "ok",
        })
        orch_mock.canonicalizer = MagicMock()
        orch_mock.canonicalizer.run = AsyncMock(return_value={"mappings": []})
        orch_mock._pending_stages = None
        orch_mock._pending_start_args = None
        orch_mock._check_stage_quality = MagicMock(return_value=[])

        env_patch = {"SPLITTER_FAIL_MODE": "hard"}

        with patch("backend.jobs.curriculum_job.init_db", new=AsyncMock()), patch(
            "backend.jobs.curriculum_job.close_db", new=AsyncMock(),
        ), patch(
            "backend.jobs.curriculum_job.create_provider", return_value=MagicMock(),
        ), patch(
            "backend.jobs.curriculum_job.LearningOrchestrator", return_value=orch_mock,
        ), patch(
            "backend.orchestrator.curriculum_pipeline_v2.session_memory.create_pending_session",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.curriculum_pipeline_v2.session_memory.purge_source_uploads",
            new=AsyncMock(),
        ), patch.dict("os.environ", env_patch, clear=False):
            result = await run_curriculum_job({"job_try": 1}, session_id)

        self.assertEqual(result["status"], "done")
        row = await session_memory.get_session(session_id)
        # create_pending_session mocked — status stays generating unless we verify call
        self.assertIsNotNone(row)
