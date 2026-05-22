"""CI validation for Go/No-Go live fixture structure and unsure-path coverage."""
import unittest

from backend.tests.go_nogo_fixture import (
    load_cases,
    validate_fixture,
)
from backend.utils.curriculum_reducer import rule_merge_candidates


class TestGoNoGoFixtureValidation(unittest.TestCase):
    def test_fixture_has_no_validation_errors(self):
        errors = validate_fixture()
        self.assertEqual(errors, [], msg="\n".join(errors))

    def test_merge_cases_cover_drift_patterns(self):
        for baseline in ("same_source", "multi_source"):
            patterns = {c["drift_pattern"] for c in load_cases(baseline, "merge")}
            self.assertIn("literal_tweak", patterns, msg=baseline)
            self.assertIn("synonym", patterns, msg=baseline)

    def test_negative_cases_exist(self):
        for baseline in ("same_source", "multi_source"):
            negs = load_cases(baseline, "negative")
            self.assertGreaterEqual(len(negs), 3, msg=baseline)

    def test_negative_cases_should_not_auto_merge_in_step_a(self):
        for baseline in ("same_source", "multi_source"):
            for case in load_cases(baseline, "negative"):
                candidates = case["candidates"]
                groups, _ = rule_merge_candidates(candidates)
                merged = any(len(g) > 1 for g in groups)
                self.assertFalse(
                    merged,
                    msg=f"{baseline}/{case['id']} should not Step-A merge",
                )


if __name__ == "__main__":
    unittest.main()
