"""Unit tests for V2 curriculum health monitoring."""
import unittest
from unittest.mock import patch

from backend.utils.curriculum_health import assess_reducer_health
from backend.utils.curriculum_llm_meter import (
    CurriculumLlmMeter,
    assess_curriculum_cost,
    curriculum_tier,
    tier_llm_budget,
)


class TestCurriculumHealth(unittest.TestCase):
    def test_healthy_when_no_signals(self):
        r = assess_reducer_health(
            session_id="s1",
            candidate_count=10,
            outcome_count=8,
            stage_count=8,
        )
        self.assertTrue(r["healthy"])
        self.assertFalse(r["plan_b_recommended"])

    def test_fallback_flat_triggers_plan_b_recommended(self):
        r = assess_reducer_health(
            session_id="s1",
            candidate_count=10,
            outcome_count=10,
            stage_count=10,
            quality_warnings={"reducer_fallback_flat": True},
        )
        self.assertIn("reducer_fallback_flat", r["signals"])
        self.assertTrue(r["plan_b_recommended"])

    def test_low_outcome_ratio(self):
        r = assess_reducer_health(
            session_id="s1",
            candidate_count=20,
            outcome_count=5,
            stage_count=5,
        )
        self.assertIn("reducer_outcome_ratio_low", r["signals"])
        self.assertTrue(r["plan_b_recommended"])

    def test_zero_stages_fires_when_candidates_present(self):
        """P1-1 regression: reducer-health zero_stages 仍在 candidate>0 且 stage=0 觸發。
        新增的 pipeline-level empty_curriculum guard 不得改動此既有語意（Case A 仍可觀測）。"""
        r = assess_reducer_health(
            session_id="s1",
            candidate_count=5,
            outcome_count=5,
            stage_count=0,
        )
        self.assertIn("zero_stages", r["signals"])
        self.assertFalse(r["healthy"])

    def test_zero_stages_absent_when_no_candidates(self):
        """candidate=0 且 stage=0（splitter 回空子案）reducer-health 結構上不報 zero_stages
        —— 這正是 pipeline 端 empty_curriculum warn-only guard 要補的盲區（Case B）。"""
        r = assess_reducer_health(
            session_id="s1",
            candidate_count=0,
            outcome_count=0,
            stage_count=0,
        )
        self.assertNotIn("zero_stages", r["signals"])

class TestCurriculumLlmMeter(unittest.TestCase):
    def _chunks(self, n: int, source_id: str = "s1") -> list[dict]:
        return [{"chunk_id": f"c{i}", "source_id": source_id} for i in range(n)]

    def test_tier_small_single(self):
        self.assertEqual(curriculum_tier(self._chunks(10)), "small")

    def test_tier_small_multi(self):
        chunks = [
            {"chunk_id": "a", "source_id": "s1"},
            {"chunk_id": "b", "source_id": "s2"},
        ]
        self.assertEqual(curriculum_tier(chunks), "small_multi")

    def test_over_budget_triggers_cost_alert(self):
        meter = CurriculumLlmMeter()
        for _ in range(5):
            meter.record("ContentSplitterAgent")
            meter.record("SplitterVerifierAgent")
        chunks = [{"chunk_id": "c0", "source_id": "only"}]
        with patch("backend.utils.curriculum_llm_meter._log") as mock_log:
            qw = assess_curriculum_cost(
                session_id="s1", meter=meter, source_chunks=chunks,
            )
        self.assertTrue(qw["curriculum_llm_over_budget"])
        mock_log.warning.assert_called_once()

    def test_under_budget_no_alert(self):
        meter = CurriculumLlmMeter()
        meter.record("ContentSplitterAgent")
        meter.record("SplitterVerifierAgent")
        chunks = [{"chunk_id": "c0", "source_id": "only"}]
        with patch("backend.utils.curriculum_llm_meter._log") as mock_log:
            qw = assess_curriculum_cost(
                session_id="s1", meter=meter, source_chunks=chunks,
            )
        self.assertFalse(qw["curriculum_llm_over_budget"])
        mock_log.warning.assert_not_called()

    def test_mid_tier_budget_scales_with_chunk_count(self):
        chunks = [{"chunk_id": f"c{i}"} for i in range(86)]
        self.assertEqual(tier_llm_budget(chunks), 258)

    def test_mid_session_224_calls_under_scaled_budget(self):
        meter = CurriculumLlmMeter()
        for _ in range(139):
            meter.record("ContentSplitterAgent")
        for _ in range(82):
            meter.record("SplitterVerifierAgent")
        meter.record("ContentOutlineAgent")
        chunks = [{"chunk_id": f"c{i}"} for i in range(86)]
        with patch("backend.utils.curriculum_llm_meter._log") as mock_log:
            qw = assess_curriculum_cost(
                session_id="meng_zi", meter=meter, source_chunks=chunks,
            )
        self.assertEqual(qw["curriculum_llm_budget"], 258)
        self.assertFalse(qw["curriculum_llm_over_budget"])
        mock_log.warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
