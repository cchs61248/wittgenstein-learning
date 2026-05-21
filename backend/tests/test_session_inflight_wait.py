"""Session-level inflight wait：resume 應等待 :answer: 等子 key 的 run_stage。"""
import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from backend.db.database import init_db, close_db
from backend.db.inflight_lock import acquire, release, has_session_inflight, active_keys_for_session
from backend.ws import generation_handle as gh


class TestSessionInflightDb(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test.db")
        await init_db(self._db_path)

    async def asyncTearDown(self):
        await close_db()
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)

    async def test_has_session_inflight_any_key(self):
        await acquire("sess_a:answer:q1", session_id="sess_a", kind="submit_answer")
        self.assertTrue(await has_session_inflight("sess_a"))
        keys = await active_keys_for_session("sess_a")
        self.assertEqual(keys, ["sess_a:answer:q1"])
        await release("sess_a:answer:q1")
        self.assertFalse(await has_session_inflight("sess_a"))


class TestWaitForSessionIdle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test.db")
        await init_db(self._db_path)
        gh._registry.clear()

    async def asyncTearDown(self):
        gh._registry.clear()
        await close_db()
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)

    async def test_waits_for_answer_subkey_task(self):
        done = asyncio.Event()

        async def _slow_answer():
            await done.wait()

        task = asyncio.create_task(_slow_answer())
        handle = await gh.register_async(
            "sess_w:answer:q_x",
            task,
            session_id="sess_w",
            kind="submit_answer",
        )
        self.assertIsNotNone(handle)

        wait_task = asyncio.create_task(gh.wait_for_session_idle("sess_w", timeout_s=5))
        await asyncio.sleep(0.05)
        self.assertFalse(wait_task.done())
        done.set()
        idle = await wait_task
        self.assertTrue(idle)
        await gh.finish_async("sess_w:answer:q_x")


class TestResumeFromStoredInflightGuard(unittest.IsolatedAsyncioTestCase):
    async def test_skips_replay_when_session_still_generating(self):
        from backend.orchestrator.learning_orchestrator import LearningOrchestrator

        orch = LearningOrchestrator(AsyncMock())
        emitted: list[dict] = []

        async def emit(msg: dict) -> None:
            emitted.append(msg)

        stage = {
            "stage_id": 17,
            "node_id": "6.2",
            "title": "測試節",
            "content": "x",
            "key_concepts": [],
            "source_chunks": [],
        }
        stored = "---\n\n📖\n\n已有部分講解"

        with patch(
            "backend.orchestrator.learning_orchestrator.session_memory.get_stage_progress",
            new_callable=AsyncMock,
            return_value={"status": "in_progress", "attempts": 1, "best_score": 0.0},
        ), patch(
            "backend.orchestrator.learning_orchestrator.session_memory.update_current_stage",
            new_callable=AsyncMock,
        ), patch(
            "backend.orchestrator.learning_orchestrator.session_memory.upsert_stage_progress",
            new_callable=AsyncMock,
        ), patch(
            "backend.orchestrator.learning_orchestrator.session_memory.get_stage_questions",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "backend.orchestrator.learning_orchestrator.has_session_inflight",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "backend.orchestrator.learning_orchestrator.get_working_memory",
        ) as mock_wm:
            wm = mock_wm.return_value
            wm.reset_for_new_stage = lambda _sid: None
            wm.current_attempt = 1
            wm.question_mode = "multiple_choice"

            await orch._resume_from_stored(
                "sess_x", "user", [stage], 0, stored, emit
            )

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["type"], "session_generating")


if __name__ == "__main__":
    unittest.main()
