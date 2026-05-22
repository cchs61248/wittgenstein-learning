"""Go/No-Go gate tests — mock LLM Step B + Step C split fallback."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from backend.agents.base_agent import AgentContext
from backend.agents.global_curriculum_reducer import (
    GlobalCurriculumReducerAgent,
    GlobalCurriculumReducerError,
)
from backend.utils.curriculum_reducer import (
    CONFLICT_SUPPORTED,
    build_step_a_outcomes,
    ensure_candidate_coverage,
    integrate_llm_outcomes,
    measure_merge_accuracy,
    rule_merge_candidates,
)
from backend.utils.reducer_constants import (
    GO_NOGO_MULTI_SOURCE_MERGE_MIN,
    GO_NOGO_SAME_SOURCE_MERGE_MIN,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestReducerStepC(unittest.TestCase):
    def test_low_confidence_llm_keeps_step_a_splits(self):
        candidates = [
            {"title": "A", "teaching_goal": "goal X", "key_concepts": ["a1"], "source_chunk_ids": ["c1"]},
            {"title": "B", "teaching_goal": "goal X", "key_concepts": ["b1"], "source_chunk_ids": ["c2"]},
        ]
        groups, unsure = rule_merge_candidates(candidates)
        step_a = build_step_a_outcomes(candidates, groups)
        llm_low = [{
            "merge_decision": "merged",
            "merge_confidence": 0.65,
            "primary_evidence": {"source_id": "s", "chunk_ids": ["c1", "c2"]},
        }]
        final = integrate_llm_outcomes(candidates, step_a, llm_low, unsure)
        self.assertEqual(len(final), 2)
        self.assertTrue(all(o.get("merge_decision") == "split" for o in final))

    def test_ensure_candidate_coverage_never_drops_stages(self):
        candidates = [
            {"title": "A", "teaching_goal": "g", "key_concepts": ["a"], "source_chunk_ids": ["c1"]},
            {"title": "B", "teaching_goal": "h", "key_concepts": ["b"], "source_chunk_ids": ["c2"]},
        ]
        covered = ensure_candidate_coverage(candidates, [])
        self.assertEqual(len(covered), 2)

    def test_conflict_downgraded_to_split_when_unsupported(self):
        self.assertFalse(CONFLICT_SUPPORTED)
        candidates = [
            {"title": "A", "teaching_goal": "g", "key_concepts": ["a"], "source_chunk_ids": ["c1"]},
            {"title": "B", "teaching_goal": "h", "key_concepts": ["b"], "source_chunk_ids": ["c2"]},
        ]
        groups, unsure = rule_merge_candidates(candidates)
        step_a = build_step_a_outcomes(candidates, groups)
        llm_conflict = [{
            "merge_decision": "conflict",
            "merge_confidence": 0.95,
            "primary_evidence": {"source_id": "s", "chunk_ids": ["c1"]},
            "supporting_evidence": [{"source_id": "s", "chunk_ids": ["c2"]}],
        }]
        final = integrate_llm_outcomes(candidates, step_a, llm_conflict, unsure)
        self.assertFalse(any(o.get("merge_decision") == "conflict" for o in final))


class TestReducerGoNoGo(unittest.IsolatedAsyncioTestCase):
    async def _run_mock_llm_scenario(self, scenario: dict) -> float:
        candidates = scenario["candidates"]
        n = len(candidates)
        forced_unsure = scenario.get("forced_unsure_pair", [0, 1])
        pair = (forced_unsure[0], forced_unsure[1])

        llm = MagicMock()
        llm.chat = AsyncMock(return_value=MagicMock(
            content=json.dumps(scenario["llm_outcomes"], ensure_ascii=False)
        ))
        agent = GlobalCurriculumReducerAgent(llm, MagicMock())
        ctx = AgentContext(
            session_id="go_nogo",
            user_id="u1",
            task_payload={
                "candidate_stages": candidates,
                "use_llm": True,
                "keep_internal_fields": True,
            },
        )
        with unittest.mock.patch(
            "backend.agents.global_curriculum_reducer.rule_merge_candidates",
            return_value=([[i] for i in range(n)], [pair]),
        ):
            result = await agent.run(ctx)
        pairs = [tuple(p) for p in scenario.get("expected_merge_pairs") or []]
        return measure_merge_accuracy(candidates, result["outcomes"], pairs)

    async def test_go_nogo_mock_llm_baselines(self):
        data = json.loads((FIXTURES / "reducer_go_nogo_unsure.json").read_text(encoding="utf-8"))
        same_source_scores: list[float] = []
        multi_source_scores: list[float] = []

        for scenario in data["scenarios"]:
            accuracy = await self._run_mock_llm_scenario(scenario)
            if scenario["baseline"] == "same_source":
                same_source_scores.append(accuracy)
            elif scenario["baseline"] == "multi_source":
                multi_source_scores.append(accuracy)
            elif scenario["baseline"] == "step_c":
                self.assertEqual(accuracy, 1.0)

        if same_source_scores:
            avg = sum(same_source_scores) / len(same_source_scores)
            self.assertGreaterEqual(
                avg, GO_NOGO_SAME_SOURCE_MERGE_MIN,
                msg=f"same-source merge accuracy {avg:.2f}",
            )
        if multi_source_scores:
            avg = sum(multi_source_scores) / len(multi_source_scores)
            self.assertGreaterEqual(
                avg, GO_NOGO_MULTI_SOURCE_MERGE_MIN,
                msg=f"multi-source merge accuracy {avg:.2f}",
            )

    async def test_reducer_llm_fail_raises_in_hard_mode(self):
        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=RuntimeError("llm down"))
        agent = GlobalCurriculumReducerAgent(llm, MagicMock())
        ctx = AgentContext(
            session_id="s1",
            user_id="u1",
            task_payload={
                "candidate_stages": [
                    {"title": "A", "teaching_goal": "same goal", "key_concepts": ["a"], "source_chunk_ids": ["c1"]},
                    {"title": "B", "teaching_goal": "same goal", "key_concepts": ["b"], "source_chunk_ids": ["c2"]},
                ],
                "use_llm": True,
            },
        )
        with patch(
            "backend.agents.global_curriculum_reducer.rule_merge_candidates",
            return_value=([[0], [1]], [(0, 1)]),
        ), patch.dict(os.environ, {"REDUCER_FAIL_MODE": "hard"}, clear=False):
            with self.assertRaises(GlobalCurriculumReducerError):
                await agent.run(ctx)


if __name__ == "__main__":
    unittest.main()
