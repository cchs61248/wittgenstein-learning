"""Live LLM Go/No-Go gate — manual pre-release validation.

Run (from wittgenstein-learning repo root, with .venv + API keys in backend/.env):

    $env:RUN_LLM_TESTS="1"
    .\\backend\\.venv\\Scripts\\python.exe -m pytest backend/tests/test_reducer_go_nogo_live.py -m llm_live -v

Metrics per baseline (avg over >=5 merge + >=3 negative cases):
  - merge accuracy
  - split accuracy (no false merge on negative cases)
  - unsure abstain rate (LLM had unsure pairs but zero accepted outcomes)
"""
from __future__ import annotations

import os
import unittest
from dataclasses import dataclass

import pytest

from backend.agents.base_agent import AgentContext
from backend.agents.global_curriculum_reducer import GlobalCurriculumReducerAgent
from backend.config import DEFAULT_PROVIDER
from backend.llm.provider_factory import create_provider
from backend.tests.go_nogo_fixture import expected_pairs, load_cases
from backend.utils.curriculum_reducer import (
    measure_merge_accuracy,
    measure_split_accuracy,
    measure_unsure_abstain_rate,
    rule_merge_candidates,
)
from backend.utils.reducer_constants import (
    GO_NOGO_LIVE_MIN_NEGATIVE,
    GO_NOGO_LIVE_MIN_PAIRS,
    GO_NOGO_MULTI_SOURCE_MERGE_MIN,
    GO_NOGO_MULTI_SOURCE_UNSURE_MAX,
    GO_NOGO_SAME_SOURCE_MERGE_MIN,
    GO_NOGO_SAME_SOURCE_UNSURE_MAX,
    GO_NOGO_SPLIT_ACCURACY_MIN,
)
from backend.utils.token_counter import TokenCounter

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


@dataclass
class BaselineMetrics:
    merge_accuracy: float
    split_accuracy: float
    unsure_rate: float
    merge_n: int
    negative_n: int


@pytest.mark.llm_live
class TestReducerGoNoGoLive(unittest.IsolatedAsyncioTestCase):
    async def _run_case(
        self,
        case: dict,
        llm,
        baseline: str,
        *,
        kind: str,
    ) -> dict | None:
        candidates = case["candidates"]
        _, unsure = rule_merge_candidates(candidates)
        if kind == "merge" and not unsure:
            return None

        agent = GlobalCurriculumReducerAgent(llm, TokenCounter())
        ctx = AgentContext(
            session_id=f"go_nogo_live_{baseline}_{case['id']}",
            user_id="u1",
            task_payload={
                "candidate_stages": candidates,
                "use_llm": True,
                "keep_internal_fields": True,
            },
        )
        result = await agent.run(ctx)
        outcomes = result["outcomes"]
        expected_merge = kind == "merge"
        pairs = expected_pairs(case, kind=kind)

        if kind == "merge":
            score = measure_merge_accuracy(candidates, outcomes, pairs)
            unsure_rate = measure_unsure_abstain_rate(result, expected_merge=True)
            return {"merge": score, "unsure": unsure_rate}
        split_score = measure_split_accuracy(outcomes, pairs)
        return {"split": split_score}

    async def _run_baseline(self, baseline: str) -> BaselineMetrics:
        if not _llm_configured():
            self.skipTest("Set RUN_LLM_TESTS=1 and configure LLM API keys")

        provider_name = os.getenv("GO_NOGO_LLM_PROVIDER") or DEFAULT_PROVIDER
        llm = create_provider(provider_name)

        merge_scores: list[float] = []
        unsure_scores: list[float] = []
        split_scores: list[float] = []
        merge_skipped = 0

        for case in load_cases(baseline, "merge"):
            row = await self._run_case(case, llm, baseline, kind="merge")
            if row is None:
                merge_skipped += 1
                continue
            merge_scores.append(row["merge"])
            unsure_scores.append(row["unsure"])

        for case in load_cases(baseline, "negative"):
            row = await self._run_case(case, llm, baseline, kind="negative")
            if row is None:
                continue
            split_scores.append(row["split"])

        if len(merge_scores) < GO_NOGO_LIVE_MIN_PAIRS:
            self.skipTest(
                f"{baseline}: only {len(merge_scores)} merge cases "
                f"(need {GO_NOGO_LIVE_MIN_PAIRS}, skipped {merge_skipped})"
            )
        if len(split_scores) < GO_NOGO_LIVE_MIN_NEGATIVE:
            self.skipTest(
                f"{baseline}: only {len(split_scores)} negative cases "
                f"(need {GO_NOGO_LIVE_MIN_NEGATIVE})"
            )

        return BaselineMetrics(
            merge_accuracy=sum(merge_scores) / len(merge_scores),
            split_accuracy=sum(split_scores) / len(split_scores),
            unsure_rate=sum(unsure_scores) / len(unsure_scores),
            merge_n=len(merge_scores),
            negative_n=len(split_scores),
        )

    async def test_live_multi_source_baselines(self):
        m = await self._run_baseline("multi_source")
        self.assertGreaterEqual(
            m.merge_accuracy, GO_NOGO_MULTI_SOURCE_MERGE_MIN,
            msg=f"multi merge {m.merge_accuracy:.2f} n={m.merge_n}",
        )
        self.assertGreaterEqual(
            m.split_accuracy, GO_NOGO_SPLIT_ACCURACY_MIN,
            msg=f"multi split {m.split_accuracy:.2f} n={m.negative_n}",
        )
        self.assertLessEqual(
            m.unsure_rate, GO_NOGO_MULTI_SOURCE_UNSURE_MAX,
            msg=f"multi unsure {m.unsure_rate:.2f}",
        )

    async def test_live_same_source_baselines(self):
        m = await self._run_baseline("same_source")
        self.assertGreaterEqual(
            m.merge_accuracy, GO_NOGO_SAME_SOURCE_MERGE_MIN,
            msg=f"same merge {m.merge_accuracy:.2f} n={m.merge_n}",
        )
        self.assertGreaterEqual(
            m.split_accuracy, GO_NOGO_SPLIT_ACCURACY_MIN,
            msg=f"same split {m.split_accuracy:.2f} n={m.negative_n}",
        )
        self.assertLessEqual(
            m.unsure_rate, GO_NOGO_SAME_SOURCE_UNSURE_MAX,
            msg=f"same unsure {m.unsure_rate:.2f}",
        )


if __name__ == "__main__":
    unittest.main()
