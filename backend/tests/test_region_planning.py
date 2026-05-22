"""Tests for macro region planning."""
import unittest

from backend.utils.region_planning import overlap_chunk_count, plan_macro_regions, slice_region_chunks


class TestRegionPlanning(unittest.TestCase):
    def test_overlap_count_bounds(self):
        self.assertEqual(overlap_chunk_count(10), 2)
        self.assertLessEqual(overlap_chunk_count(100), 8)

    def test_plan_by_section_title(self):
        chunks = [
            {"chunk_id": "c0", "order_index": 0, "section_title": "Part 1", "source_id": "s1", "text": "a"},
            {"chunk_id": "c1", "order_index": 1, "section_title": "Part 1", "source_id": "s1", "text": "b"},
            {"chunk_id": "c2", "order_index": 2, "section_title": "Part 2", "source_id": "s1", "text": "c"},
        ]
        regions = plan_macro_regions(chunks)
        self.assertGreaterEqual(len(regions), 2)

    def test_slice_includes_overlap(self):
        chunks = [{"chunk_id": f"c{i}", "order_index": i, "source_id": "s1", "text": "x"} for i in range(10)]
        regions = plan_macro_regions(chunks, chunks_per_region=4)
        self.assertGreaterEqual(len(regions), 2)
        sliced = slice_region_chunks(chunks, regions[1], regions, 1)
        self.assertGreater(len(sliced), len(regions[1]["chunk_ids"]))


if __name__ == "__main__":
    unittest.main()
