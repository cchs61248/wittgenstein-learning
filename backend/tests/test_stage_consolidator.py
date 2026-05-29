"""P0b-2: StageConsolidatorAgent unit tests."""
import json
import unittest
from unittest.mock import AsyncMock, MagicMock

from backend.agents.base_agent import AgentContext
from backend.agents.stage_consolidator import (
    StageConsolidatorAgent,
    _is_valid_consolidation,
    _stage_chunks_set,
)


def _mk_agent(llm_response: str) -> StageConsolidatorAgent:
    llm = MagicMock()
    response = MagicMock()
    response.content = llm_response
    llm.chat = AsyncMock(return_value=response)
    tc = MagicMock()
    return StageConsolidatorAgent(llm, tc)


def _ctx(stages: list[dict]) -> AgentContext:
    return AgentContext(
        session_id="sess_x", user_id="u",
        task_payload={"stages": stages, "sources_manifest": [], "required_outline": None},
    )


class TestStageChunksSet(unittest.TestCase):
    def test_collects_all_chunk_ids(self):
        stages = [
            {"source_chunk_ids": ["c1", "c2"]},
            {"source_chunk_ids": ["c3"]},
        ]
        self.assertEqual(_stage_chunks_set(stages), {"c1", "c2", "c3"})


class TestValidConsolidation(unittest.TestCase):
    def test_drop_chunk_invalid(self):
        original = [{"source_chunk_ids": ["c1", "c2"]}]
        new = [{"title": "X", "source_chunk_ids": ["c1"]}]
        ok, reason = _is_valid_consolidation(original, new)
        self.assertFalse(ok)
        self.assertIn("dropped", reason)

    def test_fabricated_chunk_invalid(self):
        original = [{"source_chunk_ids": ["c1"]}]
        new = [{"title": "X", "source_chunk_ids": ["c1", "c99"]}]
        ok, reason = _is_valid_consolidation(original, new)
        self.assertFalse(ok)
        self.assertIn("fabricated", reason)

    def test_empty_invalid(self):
        ok, _ = _is_valid_consolidation([{"source_chunk_ids": ["c1"]}], [])
        self.assertFalse(ok)

    def test_missing_title_invalid(self):
        original = [{"source_chunk_ids": ["c1"]}]
        new = [{"source_chunk_ids": ["c1"]}]
        ok, _ = _is_valid_consolidation(original, new)
        self.assertFalse(ok)

    def test_valid_consolidation_passes(self):
        original = [
            {"source_chunk_ids": ["c1", "c2"]},
            {"source_chunk_ids": ["c3"]},
        ]
        new = [
            {"title": "Merged", "source_chunk_ids": ["c1", "c2", "c3"]},
        ]
        ok, _ = _is_valid_consolidation(original, new)
        self.assertTrue(ok)


class TestStageConsolidatorRun(unittest.IsolatedAsyncioTestCase):
    async def test_single_stage_skipped(self):
        agent = _mk_agent("")
        stages = [{"title": "X", "source_chunk_ids": ["c1"], "key_concepts": ["a"]}]
        result = await agent.run(_ctx(stages))
        self.assertEqual(result["stages"], stages)
        self.assertTrue(result.get("skipped"))
        agent.llm.chat.assert_not_called()

    async def test_invalid_json_fallback(self):
        agent = _mk_agent("not json")
        stages = [
            {"title": "A", "source_chunk_ids": ["c1"], "key_concepts": ["a"]},
            {"title": "B", "source_chunk_ids": ["c2"], "key_concepts": ["b"]},
        ]
        result = await agent.run(_ctx(stages))
        self.assertEqual(result["stages"], stages)
        self.assertTrue(result.get("fallback"))

    async def test_chunk_drop_triggers_fallback(self):
        bad = json.dumps({"consolidated_stages": [
            {"title": "Merged", "source_chunk_ids": ["c1"], "key_concepts": ["a"]},  # drops c2
        ]})
        agent = _mk_agent(bad)
        stages = [
            {"title": "A", "source_chunk_ids": ["c1"], "key_concepts": ["a"]},
            {"title": "B", "source_chunk_ids": ["c2"], "key_concepts": ["b"]},
        ]
        result = await agent.run(_ctx(stages))
        self.assertTrue(result.get("fallback"))
        self.assertIn("dropped", result.get("reason", ""))

    async def test_valid_response_accepted(self):
        good = json.dumps({
            "consolidated_stages": [
                {
                    "title": "整理後標題",
                    "node_id": "1.1",
                    "source_chunk_ids": ["c1", "c2"],
                    "key_concepts": ["a", "b"],
                    "teaching_goal": "教學 a 與 b",
                }
            ]
        })
        agent = _mk_agent(good)
        stages = [
            {"title": "A", "source_chunk_ids": ["c1"], "key_concepts": ["a"]},
            {"title": "B", "source_chunk_ids": ["c2"], "key_concepts": ["b"]},
        ]
        result = await agent.run(_ctx(stages))
        self.assertFalse(result.get("fallback"))
        self.assertFalse(result.get("skipped"))
        self.assertEqual(len(result["stages"]), 1)
        self.assertEqual(result["stages"][0]["title"], "整理後標題")
        # estimated_questions / prerequisites filled in
        self.assertEqual(result["stages"][0]["estimated_questions"], 3)
        self.assertEqual(result["stages"][0]["prerequisites"], [])


if __name__ == "__main__":
    unittest.main()
