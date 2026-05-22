"""Tests for MacroRegionPlannerAgent tier-3 LLM refinement."""
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.agents.base_agent import AgentContext
from backend.agents.macro_region_planner import MacroRegionPlannerAgent


def _make_chunks(n: int = 60, section: str = "長期買進") -> list[dict]:
    return [
        {
            "chunk_id": f"chunk_{i:04d}",
            "order_index": i,
            "source_id": "s1",
            "section_title": section,
            "text": f"段落 {i} 內容：行為財務學介紹與案例 {i}",
        }
        for i in range(n)
    ]


def _llm_mock(refinements: list[dict]) -> MagicMock:
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value=MagicMock(
            content=json.dumps({"refinements": refinements}, ensure_ascii=False)
        )
    )
    return llm


class TestMacroRegionRefiner(unittest.IsolatedAsyncioTestCase):
    async def test_default_no_llm_call(self):
        """Without MACRO_REGION_USE_LLM, agent skips LLM and returns tier-1/2 regions."""
        llm = MagicMock()
        llm.chat = AsyncMock()
        agent = MacroRegionPlannerAgent(llm, MagicMock())
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={"source_chunks": _make_chunks(60)},
        )
        with patch.dict(os.environ, {"MACRO_REGION_USE_LLM": "0"}, clear=False):
            result = await agent.run(ctx)
        llm.chat.assert_not_awaited()
        self.assertGreater(len(result["regions"]), 1)

    async def test_env_flag_enables_llm_refinement(self):
        refinements = [
            {
                "region_id": "region_000",
                "title": "行為財務學基礎",
                "expected_stage_count": 4,
                "must_cover_topics": ["有限理性", "心理偏誤"],
            }
        ]
        llm = _llm_mock(refinements)
        agent = MacroRegionPlannerAgent(llm, MagicMock())
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={"source_chunks": _make_chunks(60)},
        )
        with patch.dict(os.environ, {"MACRO_REGION_USE_LLM": "1"}, clear=False):
            result = await agent.run(ctx)

        llm.chat.assert_awaited_once()
        r0 = next(r for r in result["regions"] if r["region_id"] == "region_000")
        self.assertEqual(r0["title"], "行為財務學基礎")
        self.assertEqual(r0["expected_stage_count"], 4)
        self.assertEqual(r0["must_cover_topics"], ["有限理性", "心理偏誤"])

    async def test_payload_flag_overrides_env(self):
        llm = _llm_mock([])
        agent = MacroRegionPlannerAgent(llm, MagicMock())
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "source_chunks": _make_chunks(60),
                "use_llm_refinement": True,
            },
        )
        with patch.dict(os.environ, {"MACRO_REGION_USE_LLM": "0"}, clear=False):
            await agent.run(ctx)
        llm.chat.assert_awaited_once()

    async def test_llm_failure_falls_back_to_tier12(self):
        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=RuntimeError("llm down"))
        agent = MacroRegionPlannerAgent(llm, MagicMock())
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "source_chunks": _make_chunks(60),
                "use_llm_refinement": True,
            },
        )
        result = await agent.run(ctx)
        self.assertGreater(len(result["regions"]), 1)
        # original placeholder title 保留
        self.assertTrue(
            all(r.get("title") for r in result["regions"]),
            msg="regions should still have titles after LLM fallback",
        )

    async def test_clamps_invalid_refinement_values(self):
        refinements = [{
            "region_id": "region_000",
            "title": "x" * 100,
            "expected_stage_count": "999",
            "must_cover_topics": ["a"] * 20,
        }]
        llm = _llm_mock(refinements)
        agent = MacroRegionPlannerAgent(llm, MagicMock())
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "source_chunks": _make_chunks(60),
                "use_llm_refinement": True,
            },
        )
        result = await agent.run(ctx)
        r0 = next(r for r in result["regions"] if r["region_id"] == "region_000")
        self.assertLessEqual(len(r0["title"]), 30)
        self.assertLessEqual(r0["expected_stage_count"], 8)
        self.assertLessEqual(len(r0["must_cover_topics"]), 5)

    async def test_unknown_region_id_in_refinement_is_ignored(self):
        refinements = [{"region_id": "region_999", "title": "wrong"}]
        llm = _llm_mock(refinements)
        agent = MacroRegionPlannerAgent(llm, MagicMock())
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "source_chunks": _make_chunks(60),
                "use_llm_refinement": True,
            },
        )
        result = await agent.run(ctx)
        titles = [r["title"] for r in result["regions"]]
        self.assertNotIn("wrong", titles)


if __name__ == "__main__":
    unittest.main()
