"""Live LLM Go/No-Go gate — manual pre-release validation.

Run (from wittgenstein-learning repo root, with .venv + API keys in backend/.env):

    $env:RUN_LLM_TESTS="1"
    .\\backend\\.venv\\Scripts\\python.exe -m pytest backend/tests/test_reducer_go_nogo_live.py -m llm_live -v

Each baseline averages merge accuracy over >=5 independent pair cases
(see fixtures/reducer_go_nogo_live_pairs.json). CI excludes via pytest.ini.
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
    GO_NOGO_LIVE_MIN_PAIRS,
    GO_NOGO_MULTI_SOURCE_MERGE_MIN,
    GO_NOGO_SAME_SOURCE_MERGE_MIN,
)
from backend.utils.token_counter import TokenCounter

FIXTURES = Path(__file__).parent / "fixtures"
LIVE_PAIRS = FIXTURES / "reducer_go_nogo_live_pairs.json"

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


def _load_baseline_cases(baseline: str) -> list[dict]:
    data = json.loads(LIVE_PAIRS.read_text(encoding="utf-8"))
    return data[baseline]


@pytest.mark.llm_live
class TestReducerGoNoGoLive(unittest.IsolatedAsyncioTestCase):
    async def _run_live_pair(self, pair_case: dict, llm, baseline: str) -> float | None:
        candidates = pair_case["candidates"]
        expected_pairs = [tuple(p) for p in pair_case["expected_merge_pairs"]]
        _, unsure = rule_merge_candidates(candidates)
        if not unsure:
            return None

        agent = GlobalCurriculumReducerAgent(llm, TokenCounter())
        ctx = AgentContext(
            session_id=f"go_nogo_live_{baseline}_{pair_case['id']}",
            user_id="u1",
            task_payload={
                "candidate_stages": candidates,
                "use_llm": True,
                "keep_internal_fields": True,
            },
        )
        result = await agent.run(ctx)
        return measure_merge_accuracy(candidates, result["outcomes"], expected_pairs)

    async def _run_live_baseline(self, baseline: str) -> tuple[float, int]:
        if not _llm_configured():
            self.skipTest("Set RUN_LLM_TESTS=1 and configure LLM API keys")

        cases = _load_baseline_cases(baseline)
        provider_name = os.getenv("GO_NOGO_LLM_PROVIDER") or DEFAULT_PROVIDER
        llm = create_provider(provider_name)

        scores: list[float] = []
        skipped = 0
        for case in cases:
            acc = await self._run_live_pair(case, llm, baseline)
            if acc is None:
                skipped += 1
                continue
            scores.append(acc)

        if len(scores) < GO_NOGO_LIVE_MIN_PAIRS:
            self.skipTest(
                f"{baseline}: only {len(scores)} valid pairs "
                f"(need {GO_NOGO_LIVE_MIN_PAIRS}, skipped {skipped})"
            )
        avg = sum(scores) / len(scores)
        return avg, len(scores)

    async def test_live_multi_source_merge_baseline(self):
        accuracy, n = await self._run_live_baseline("multi_source")
        self.assertGreaterEqual(
            accuracy,
            GO_NOGO_MULTI_SOURCE_MERGE_MIN,
            msg=f"live multi-source avg merge accuracy {accuracy:.2f} over {n} pairs",
        )

    async def test_live_same_source_merge_baseline(self):
        accuracy, n = await self._run_live_baseline("same_source")
        self.assertGreaterEqual(
            accuracy,
            GO_NOGO_SAME_SOURCE_MERGE_MIN,
            msg=f"live same-source avg merge accuracy {accuracy:.2f} over {n} pairs",
        )


if __name__ == "__main__":
    unittest.main()
