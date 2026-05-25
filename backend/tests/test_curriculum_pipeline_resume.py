"""Resume test: checkpoint skips completed regions (mocked LLM)."""
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.db.database import init_db, close_db, get_db
from backend.memory import curriculum_checkpoint as ckpt
from backend.memory import session_memory
from backend.orchestrator.curriculum_pipeline_v2 import run_start_session_v2
from backend.orchestrator.learning_orchestrator import LearningOrchestrator
from backend.utils.content_hash import compute_content_hash


def _chunks(n: int = 30) -> list[dict]:
    return [
        {
            "chunk_id": f"chunk_{i:04d}",
            "order_index": i,
            "text": f"段落 {i} 關於概念 alpha beta gamma",
            "source_id": "src_a",
            "source_index": 0,
            "source_label": "書A",
            "section_title": f"第 {i // 10 + 1} 章",
        }
        for i in range(n)
    ]


def _regions() -> list[dict]:
    return [
        {
            "region_id": "region_000",
            "chunk_ids": [f"chunk_{i:04d}" for i in range(10)],
        },
        {
            "region_id": "region_001",
            "chunk_ids": [f"chunk_{i:04d}" for i in range(10, 20)],
        },
        {
            "region_id": "region_002",
            "chunk_ids": [f"chunk_{i:04d}" for i in range(20, 30)],
        },
    ]


def _mk_orch() -> LearningOrchestrator:
    orch = LearningOrchestrator.__new__(LearningOrchestrator)
    orch.content_outliner = MagicMock()
    orch.content_outliner.run = AsyncMock(return_value={
        "required_stage_titles": ["導論"],
        "named_cases": [],
        "framework_sections": [],
        "summary_sections": [],
    })
    orch.splitter = MagicMock()
    orch.splitter.llm = MagicMock()
    orch.splitter.token_counter = MagicMock()
    call_count = {"n": 0}

    async def _splitter_run(_ctx):
        call_count["n"] += 1
        return {
            "stages": [{
                "stage_id": call_count["n"],
                "node_id": f"1.{call_count['n']}",
                "title": f"Stage {call_count['n']}",
                "teaching_goal": "理解 alpha",
                "key_concepts": ["alpha"],
                "source_chunk_ids": ["chunk_0000"],
            }],
            "summary": "摘要",
        }

    orch.splitter.run = AsyncMock(side_effect=_splitter_run)
    orch.splitter_verifier = MagicMock()
    orch.splitter_verifier.run = AsyncMock(return_value={
        "aligned": True,
        "missing_options": [],
        "issue_chunk_ids": [],
        "reason": "ok",
    })
    orch.canonicalizer = MagicMock()
    orch.canonicalizer.run = AsyncMock(return_value={"mappings": []})
    orch._pending_stages = None
    orch._pending_start_args = None
    orch._check_stage_quality = MagicMock(return_value=[])
    orch._splitter_call_count = call_count
    return orch


class TestCurriculumPipelineResume(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test.db")
        await init_db(self._db_path)
        db = await get_db()
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, email, password_hash) VALUES (?, ?, ?)",
            ("u1", "u1@test.local", "hash"),
        )
        await db.commit()

    async def asyncTearDown(self):
        await close_db()
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)

    async def test_resume_skips_completed_regions(self):
        chunks = _chunks()
        content_hash = compute_content_hash(chunks)
        session_id = "sess_resume"

        await session_memory.create_generating_stub(session_id, "u1", content_hash)
        await session_memory.insert_source_chunks(session_id, chunks)
        await ckpt.upsert_checkpoint(
            session_id,
            content_hash=content_hash,
            pipeline_meta={
                "user_id": "u1",
                "target_depth": "standard",
                "question_mode": "short_answer",
                "provider_name": "claude",
                "model_name": "m",
                "pipeline_path": "region_loop",
            },
            required_outline={"required_stage_titles": ["導論"], "named_cases": []},
            regions=_regions(),
            completed_region_ids=["region_000", "region_001"],
            all_candidates=[{"title": "prior", "key_concepts": ["alpha"], "source_chunk_ids": ["c0"]}],
            summary_parts=["part0", "part1"],
        )

        orch = _mk_orch()
        reducer_mock = MagicMock()
        reducer_mock.run = AsyncMock(return_value={
            "outcomes": [{
                "outcome_id": "lo_001",
                "title": "Stage 1",
                "teaching_goal": "理解 alpha",
                "key_concepts": ["alpha"],
                "primary_evidence": {"source_id": "src_a", "chunk_ids": ["chunk_0000"]},
                "supporting_evidence": [],
                "merge_decision": "merged",
                "merge_confidence": 0.9,
            }],
        })

        async def _emit(_msg):
            pass

        env_patch = {
            "CURRICULUM_PIPELINE_V2": "1",
            "CURRICULUM_V2_PLAN_B": "0",
            "REDUCER_FAIL_MODE": "hard",
            "SPLITTER_FAIL_MODE": "hard",
            "SMALL_FILE_CHUNK_THRESHOLD": "0",
        }

        with patch(
            "backend.orchestrator.curriculum_pipeline_v2.GlobalCurriculumReducerAgent",
            return_value=reducer_mock,
        ), patch(
            "backend.orchestrator.curriculum_pipeline_v2.session_memory.create_pending_session",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.curriculum_pipeline_v2.session_memory.purge_source_uploads",
            new=AsyncMock(),
        ), patch.dict("os.environ", env_patch, clear=False):
            await run_start_session_v2(
                orch,
                session_id=session_id,
                user_id="u1",
                source_chunks=chunks,
                target_depth="standard",
                question_mode="short_answer",
                provider_name="claude",
                model_name="m",
                emit=_emit,
            )

        self.assertEqual(orch._splitter_call_count["n"], 1)
        self.assertIsNone(await ckpt.load_checkpoint(session_id))
