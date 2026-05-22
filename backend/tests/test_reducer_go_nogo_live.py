"""Live LLM Go/No-Go gate — manual pre-release validation.

Run (from wittgenstein-learning repo root, with .venv + API keys in backend/.env):

    $env:RUN_LLM_TESTS="1"
    .\\backend\\.venv\\Scripts\\python.exe -m pytest backend/tests/test_reducer_go_nogo_live.py -m llm_live -v

CI default suite excludes these via pytest.ini addopts.
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

import pytest

from backend.agents.base_agent import AgentContext
from backend.agents.global_curriculum_reducer import GlobalCurriculumReducerAgent
from backend.config import DEFAULT_PROVIDER
from backend.llm.provider_factory import create_provider
from backend.utils.curriculum_reducer import measure_merge_accuracy, rule_merge_candidates
from backend.utils.reducer_constants import (
    GO_NOGO_MULTI_SOURCE_MERGE_MIN,
    GO_NOGO_SAME_SOURCE_MERGE_MIN,
)
from backend.utils.token_counter import TokenCounter

FIXTURES = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.llm_live


def _llm_configured() -> bool:
    if os.getenv("RUN_LLM_TESTS") != "1":
        return False
    provider = os.getenv("GO_NOGO_LLM_PROVIDER") or DEFAULT_PROVIDER
    if provider == "claude":
        return bool(os.getenv("ANTHROPIC_API_KEY"))
    if provider == "openai":
        return bool(os.getenv("OPENAI_API_KEY"))
    if provider == "gemini":
        return bool(os.getenv("GOOGLE_API_KEY"))
    return bool(os.getenv("MONICA_API_KEY") or os.getenv("DEEPSEEK_API_KEY"))


def _live_candidates(scenario_id: str) -> tuple[list[dict], list[tuple[int, int]], str]:
    """Candidates engineered to hit Step A unsure pairs (no mock LLM)."""
    if scenario_id == "multi_source":
        candidates = [
            {
                "source_id": "src_a",
                "title": "賭徒謬誤",
                "teaching_goal": "理解追損心理",
                "key_concepts": ["賭徒謬誤"],
                "source_chunk_ids": ["chunk_0012"],
            },
            {
                "source_id": "src_b",
                "title": "追高殺低",
                "teaching_goal": "理解追損心理",
                "key_concepts": ["追高"],
                "source_chunk_ids": ["chunk_0045"],
            },
        ]
        expected = [(0, 1)]
        baseline = "multi_source"
    else:
        candidates = [
            {
                "source_id": "src_a",
                "title": "巴菲特神話",
                "teaching_goal": "理解出身對投資視野的影響",
                "key_concepts": ["神話"],
                "source_chunk_ids": ["chunk_0001"],
            },
            {
                "source_id": "src_a",
                "title": "巴菲特流派家世",
                "teaching_goal": "理解出身對投資視野的影響",
                "key_concepts": ["流派"],
                "source_chunk_ids": ["chunk_0005"],
            },
        ]
        expected = [(0, 1)]
        baseline = "same_source"

    _, unsure = rule_merge_candidates(candidates)
    if not unsure:
        raise unittest.SkipTest(f"{scenario_id}: candidates did not produce unsure pairs")
    return candidates, expected, baseline


@pytest.mark.llm_live
class TestReducerGoNoGoLive(unittest.IsolatedAsyncioTestCase):
    async def _run_live(self, scenario_id: str) -> tuple[float, str]:
        if not _llm_configured():
            self.skipTest("Set RUN_LLM_TESTS=1 and configure LLM API keys")

        candidates, expected_pairs, baseline = _live_candidates(scenario_id)
        provider_name = os.getenv("GO_NOGO_LLM_PROVIDER") or DEFAULT_PROVIDER
        llm = create_provider(provider_name)
        agent = GlobalCurriculumReducerAgent(llm, TokenCounter())
        ctx = AgentContext(
            session_id=f"go_nogo_live_{scenario_id}",
            user_id="u1",
            task_payload={
                "candidate_stages": candidates,
                "use_llm": True,
                "keep_internal_fields": True,
            },
        )
        result = await agent.run(ctx)
        accuracy = measure_merge_accuracy(candidates, result["outcomes"], expected_pairs)
        return accuracy, baseline

    async def test_live_multi_source_merge_baseline(self):
        accuracy, baseline = await self._run_live("multi_source")
        self.assertEqual(baseline, "multi_source")
        self.assertGreaterEqual(
            accuracy,
            GO_NOGO_MULTI_SOURCE_MERGE_MIN,
            msg=f"live multi-source merge accuracy {accuracy:.2f}",
        )

    async def test_live_same_source_merge_baseline(self):
        accuracy, baseline = await self._run_live("same_source")
        self.assertEqual(baseline, "same_source")
        self.assertGreaterEqual(
            accuracy,
            GO_NOGO_SAME_SOURCE_MERGE_MIN,
            msg=f"live same-source merge accuracy {accuracy:.2f}",
        )


if __name__ == "__main__":
    unittest.main()
