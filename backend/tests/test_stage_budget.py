"""Tests for compute_dynamic_max_stages."""
import unittest

from backend.utils.stage_budget import compute_dynamic_max_stages


class TestStageBudget(unittest.TestCase):
    def test_sess_c2pzru21e_fixture_at_least_32(self):
        chunks = [{"chunk_id": f"chunk_{i:04d}"} for i in range(134)]
        outline = {
            "required_stage_titles": [f"t{i}" for i in range(9)],
            "named_cases": [f"case_{i}" for i in range(8)],
        }
        result = compute_dynamic_max_stages(chunks, source_count=1, required_outline=outline)
        self.assertGreaterEqual(result, 32)

    def test_no_outline_falls_back_to_chunk_based(self):
        chunks = [{"chunk_id": f"chunk_{i:04d}"} for i in range(50)]
        self.assertEqual(compute_dynamic_max_stages(chunks), 30)

    def test_many_named_cases_raises_budget(self):
        chunks = [{"chunk_id": f"chunk_{i:04d}"} for i in range(134)]
        outline = {"required_stage_titles": [], "named_cases": [f"c{i}" for i in range(12)]}
        self.assertGreaterEqual(compute_dynamic_max_stages(chunks, required_outline=outline), 48)


if __name__ == "__main__":
    unittest.main()
