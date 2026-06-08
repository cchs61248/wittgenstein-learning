"""TDD step 1: failing test proving resume of a fully completed session
corrupts state.

Background (Phase 1 stability):
- `_resume_from_stored` in learning_orchestrator.py (around lines 1547-1553)
  unconditionally calls `upsert_stage_progress(..., "in_progress", ...)`
  even when the stage is already `completed`. This silently downgrades
  the persisted status.
- Subsequently, the `elif qa_records:` branch (around line 1741) treats the
  fully-answered session as a "normal completion" and invokes
  `_make_progress_decision`, which can cascade into a fresh `run_stage`
  → another `questioner` LLM call → emitting a new `question` event.

This test seeds 3 stages all in `completed` status with full QA history,
calls `LearningOrchestrator.resume_session(...)` with a mock LLM, and
asserts the following four invariants. ALL of these are expected to FAIL
against the current (buggy) implementation. Task 2 will fix the bugs
and turn this test green.
"""

import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock

from backend.db.database import close_db, get_db, init_db
from backend.memory import session_memory


async def _empty_async_gen():
    """Empty async generator so MagicMock(stream_chat) returns something awaitable."""
    if False:
        yield ""


class TestResumeCompletedSession(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await init_db(os.environ["DATABASE_URL"], reset=True)

        self.session_id = "sess_test_completed"
        self.user_id = "u_test"

        db = await get_db()
        # users row needed because sessions.user_id is a FK
        await db.execute(
            "INSERT INTO users (user_id, email, password_hash, session_version) VALUES ($1, $2, $3, $4)",
            self.user_id, "u_test@example.com", "hash", 1,
        )

        # 3 stages, all completed, full QA history
        stages_data = [
            {
                "stage_id": 1,
                "node_id": "1.1",
                "title": "stage 1",
                "key_concepts": ["c1"],
                "source_chunks": [],
                "content": "stage 1 content",
            },
            {
                "stage_id": 2,
                "node_id": "1.2",
                "title": "stage 2",
                "key_concepts": ["c2"],
                "source_chunks": [],
                "content": "stage 2 content",
            },
            {
                "stage_id": 3,
                "node_id": "1.3",
                "title": "stage 3",
                "key_concepts": ["c3"],
                "source_chunks": [],
                "content": "stage 3 content",
            },
        ]

        await db.execute(
            """INSERT INTO sessions
               (session_id, user_id, content_hash, total_stages, raw_content_summary,
                status, stages_json, current_stage_id,
                provider_name, model_name, question_mode, title, source_file_ids_json)
               VALUES ($1, $2, $3, $4, $5, 'active', $6, $7, $8, $9, $10, $11, $12)""",
            self.session_id,
            self.user_id,
            "hash_x",
            3,
            "test summary",
            json.dumps(stages_data, ensure_ascii=False),
            3,  # current_stage_id = 3 (last stage)
            "monica",
            "gemini-3-flash",
            "multiple_choice",
            "Test",
            json.dumps([], ensure_ascii=False),
        )

        # Seed each stage: completed status + stored explanation + stored
        # question(s) + matching qa_records (so the resume path lands in
        # `_resume_from_stored` -> elif qa_records branch).
        for sid in (1, 2, 3):
            await session_memory.upsert_stage_progress(
                self.session_id, sid, "completed", 1, 1.0, {}
            )
            await session_memory.store_stage_explanation(
                self.session_id, sid, f"已存講解 stage {sid}"
            )
            qid = f"q_{sid}_1"
            await session_memory.store_stage_questions(
                self.session_id,
                sid,
                [
                    {
                        "question_id": qid,
                        "text": f"Q1 of {sid}",
                        "type": "understand",
                        "answer_mode": "multiple_choice",
                        "options": [],
                        "evidence_chunk_ids": [],
                    }
                ],
            )
            await session_memory.insert_qa_record(
                self.session_id,
                sid,
                qid,
                f"Q1 of {sid}",
                "understand",
                "ans",
                1.0,
                "good",
            )

    async def asyncTearDown(self) -> None:
        await close_db()

    async def test_resume_completed_session_does_not_mutate_status(self) -> None:
        # Late import: orchestrator pulls in many transitive deps, so we
        # only import after init_db so the db is wired.
        from backend.orchestrator.learning_orchestrator import LearningOrchestrator

        statuses_before = await session_memory.get_stage_statuses(self.session_id)
        self.assertEqual(
            statuses_before,
            {1: "completed", 2: "completed", 3: "completed"},
            "前置條件：3 章皆 completed",
        )

        emitted: list[dict] = []

        async def emit(message: dict) -> None:
            emitted.append(message)

        # Fake LLM: any LLM call would mean the resume path did NOT honor
        # the completed status (and would also raise because chat/stream_chat
        # are not configured to return anything meaningful).
        fake_llm = MagicMock()
        fake_llm.chat = AsyncMock()
        fake_llm.stream_chat = MagicMock(return_value=_empty_async_gen())

        orch = LearningOrchestrator(fake_llm)
        await orch.resume_session(
            session_id=self.session_id,
            user_id=self.user_id,
            emit=emit,
        )

        statuses_after = await session_memory.get_stage_statuses(self.session_id)

        # Assertion 1 ─ stage statuses must be unchanged (no downgrade).
        self.assertEqual(
            statuses_before,
            statuses_after,
            "resume_session 不應修改已完成章節的 status；"
            f"before={statuses_before}, after={statuses_after}",
        )

        # Assertion 2 ─ no LLM calls when resuming a fully completed session.
        self.assertEqual(
            fake_llm.chat.call_count,
            0,
            f"已完成 session resume 不應呼叫 LLM chat (got {fake_llm.chat.call_count})",
        )
        self.assertEqual(
            fake_llm.stream_chat.call_count,
            0,
            "已完成 session resume 不應呼叫 LLM stream_chat "
            f"(got {fake_llm.stream_chat.call_count})",
        )

        types = [m["type"] for m in emitted]

        # Assertion 3 ─ no new `question` events in pure-review mode.
        self.assertNotIn(
            "question",
            types,
            f"已完成 session 純複習模式不應 emit question；emitted types={types}",
        )

        # Assertion 4 ─ no `stage_decision` events (would indicate
        # `_make_progress_decision` was triggered).
        self.assertNotIn(
            "stage_decision",
            types,
            f"resume 不應觸發 _make_progress_decision；emitted types={types}",
        )


if __name__ == "__main__":
    unittest.main()
