import unittest
from unittest.mock import AsyncMock, patch

from backend.jobs.session_prepare import prepare_curriculum_session


class TestPrepareStoresOrderDecision(unittest.IsolatedAsyncioTestCase):
    async def test_order_decision_in_pipeline_meta(self):
        captured = {"meta": None}

        async def _upsert(session_id, **kwargs):
            captured["meta"] = kwargs.get("pipeline_meta")

        decision = {"applied": True, "certain": True, "signal": ["filename_regex"],
                    "order": ["第一章.txt", "第二章.txt"], "reason": None}
        with patch("backend.jobs.session_prepare.session_memory.create_generating_stub", new=AsyncMock()), \
             patch("backend.jobs.session_prepare.session_memory.insert_source_chunks", new=AsyncMock()), \
             patch("backend.jobs.session_prepare.session_memory.purge_source_uploads", new=AsyncMock()), \
             patch("backend.jobs.session_prepare.ckpt.upsert_checkpoint", new=AsyncMock(side_effect=_upsert)):
            await prepare_curriculum_session(
                session_id="s", user_id="u", source_chunks=[{"chunk_id": "chunk_0000"}],
                content_hash="h", target_depth="standard", question_mode="multiple_choice",
                provider_name="claude", model_name="m", source_file_ids=[],
                sources_json=[], same_material=True, order_decision=decision,
            )
        self.assertEqual((captured["meta"] or {}).get("order_decision"), decision)


if __name__ == "__main__":
    unittest.main()
