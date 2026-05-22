"""Reducer spike tests — rule-based Step A + fixture baselines."""
import json
import unittest
from pathlib import Path

from backend.utils.curriculum_reducer import rule_merge_candidates
from backend.utils.prompt_templates import SYSTEM_PROMPTS

FIXTURES = Path(__file__).parent / "fixtures"


class TestReducerSpike(unittest.TestCase):
    def test_prompt_exists(self):
        self.assertIn("global_curriculum_reducer", SYSTEM_PROMPTS)
        self.assertIn("merge_confidence", SYSTEM_PROMPTS["global_curriculum_reducer"])

    def test_same_source_drift_rule_merge(self):
        data = json.loads((FIXTURES / "reducer_same_source_drift.json").read_text(encoding="utf-8"))
        candidates = data["candidates_a"] + data["candidates_b"]
        groups, unsure = rule_merge_candidates(candidates)
        self.assertGreaterEqual(len(groups), 2)
        merged_titles = []
        for indices in groups:
            if len(indices) > 1:
                merged_titles.append([candidates[i]["title"] for i in indices])
        self.assertTrue(any("巴菲特" in t[0] or "巴菲特" in t[1] for g in merged_titles for t in [g]))

    def test_multi_source_pairs_merge_signal(self):
        data = json.loads((FIXTURES / "reducer_multi_source_pairs.json").read_text(encoding="utf-8"))
        for pair in data["pairs"]:
            candidates = [pair["a"], pair["b"]]
            groups, _ = rule_merge_candidates(candidates)
            merged = any(len(g) > 1 for g in groups)
            if pair["should_merge"]:
                self.assertTrue(merged, msg=pair["a"]["title"])
            else:
                self.assertFalse(merged, msg=pair["a"]["title"])

    def test_c2pzru21e_fixture_loads(self):
        data = json.loads((FIXTURES / "reducer_c2pzru21e_mashup.json").read_text(encoding="utf-8"))
        self.assertEqual(len(data["candidate_stages"]), 3)
        self.assertEqual(len(data["verify_missing_attempt_3"]), 8)


if __name__ == "__main__":
    unittest.main()
