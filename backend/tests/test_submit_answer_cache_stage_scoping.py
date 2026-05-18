"""Regression test: cross-stage question_id reuse must not collapse to cache hit.

Bug context (2026-05-18): questioner agent can issue the same question_id
(e.g. "q_cb_1") in the original stage and a later dynamic remediation stage.
The dedup cache for submit_answer used `get_all_stage_qa_records` and matched
on question_id alone, so the new stage's submission was emitted the OLD stage's
cached feedback, bypassing handle_answer entirely (no next question, no
stage_decision). UI froze at "評估進度中，請稍候...".

Fix: `_lookup_answer_cache` filters by current session.current_stage_id.
"""

import json
import tempfile
import unittest
from pathlib import Path

from backend.db.database import close_db, get_db, init_db
from backend.main import _lookup_answer_cache
from backend.memory import session_memory


class TestSubmitAnswerCacheStageScoping(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        await init_db(str(Path(self._tmp.name) / "test.db"))

        self.sid = "sess_x"
        self.uid = "u_x"

        db = await get_db()
        await db.execute(
            "INSERT INTO users (user_id, email, password_hash, session_version) VALUES (?,?,?,?)",
            (self.uid, "u@x.com", "h", 1),
        )
        await db.execute(
            """INSERT INTO sessions (session_id, user_id, content_hash, total_stages,
                raw_content_summary, status, stages_json, current_stage_id,
                provider_name, model_name, question_mode, title, source_file_ids_json)
               VALUES (?,?,?,?,?,'active',?,?,?,?,?,?,?)""",
            (self.sid, self.uid, "h", 2, "", json.dumps([]), 8,
             "monica", "g", "multiple_choice", "t", json.dumps([])),
        )
        await db.commit()

        # 種 stage 8 已答過的 q_cb_1
        await session_memory.insert_qa_record(
            self.sid, 8, "q_cb_1", "Q stage 8", "understand", "B", 1.0, "ok"
        )

    async def asyncTearDown(self) -> None:
        await close_db()
        self._tmp.cleanup()

    async def test_same_stage_hits_cache(self):
        """同 stage 同 question_id：應命中 cache（Bug F 修補保留）。"""
        result = await _lookup_answer_cache(self.sid, "q_cb_1")
        self.assertIsNotNone(result)
        self.assertEqual(result["question_id"], "q_cb_1")
        self.assertEqual(result["stage_id"], 8)
        self.assertEqual(result["score"], 1.0)

    async def test_different_stage_misses_cache(self):
        """跨 stage 同名 question_id：必須 cache miss，避免 handle_answer 被跳過。

        場景重現：stage 8 已答過 q_cb_1（DB 留紀錄）；session.current_stage_id
        切到 11（補強章節 advance / remediate 後）；補強章節 questioner 重出
        同名 q_cb_1。若 cache miss 失敗（回到舊行為），user submit 後會被
        emit cached feedback 並 continue，handle_answer 不跑 → UI 卡死。
        """
        await session_memory.update_current_stage(self.sid, 11)
        result = await _lookup_answer_cache(self.sid, "q_cb_1")
        self.assertIsNone(result)

    async def test_no_session_returns_none(self):
        """session 不存在：return None。"""
        result = await _lookup_answer_cache("sess_nonexistent", "q_cb_1")
        self.assertIsNone(result)

    async def test_unknown_question_id_in_current_stage(self):
        """同 stage 但 question_id 未見過：cache miss。"""
        result = await _lookup_answer_cache(self.sid, "q_unknown")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
