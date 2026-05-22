"""Unit tests for V2 curriculum health monitoring."""
import unittest

from backend.utils.curriculum_health import assess_reducer_health


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


if __name__ == "__main__":
    unittest.main()
