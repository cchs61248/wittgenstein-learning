"""P0b-1: Cross-source stage merge by key_concepts jaccard."""
import os
import unittest
from unittest.mock import patch

from backend.utils.fuzzy_match import concept_jaccard
from backend.utils.small_curriculum import (
    concept_overlap_threshold,
    merge_by_concept_overlap,
)


class TestConceptJaccard(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(concept_jaccard([], []), 0.0)
        self.assertEqual(concept_jaccard(["a"], []), 0.0)

    def test_full_overlap(self):
        self.assertEqual(concept_jaccard(["a", "b"], ["a", "b"]), 1.0)

    def test_case_insensitive(self):
        self.assertEqual(concept_jaccard(["Alpha"], ["alpha"]), 1.0)

    def test_partial_overlap_two_of_three(self):
        # |a ∩ b| = 2, |a ∪ b| = 4 → 0.5
        v = concept_jaccard(["a", "b", "c"], ["b", "c", "d", "e"])
        self.assertAlmostEqual(v, 2 / 5)  # 5 union

    def test_three_of_five_jaccard(self):
        # |inter|=3, |union|=5 → 0.6
        v = concept_jaccard(["a", "b", "c", "d"], ["a", "b", "c", "e"])
        self.assertAlmostEqual(v, 3 / 5)


class TestConceptOverlapThreshold(unittest.TestCase):
    def test_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STAGE_CONCEPT_OVERLAP_THRESHOLD", None)
            self.assertEqual(concept_overlap_threshold(), 0.6)

    def test_env_override(self):
        with patch.dict(os.environ, {"STAGE_CONCEPT_OVERLAP_THRESHOLD": "0.7"}, clear=False):
            self.assertEqual(concept_overlap_threshold(), 0.7)

    def test_invalid_falls_back(self):
        for bad in ("", "abc", "-0.1", "0", "1.5"):
            with patch.dict(os.environ, {"STAGE_CONCEPT_OVERLAP_THRESHOLD": bad}, clear=False):
                self.assertEqual(concept_overlap_threshold(), 0.6)


class TestMergeByConceptOverlap(unittest.TestCase):
    def _stage(self, title: str, kc: list[str], chunks: list[str] | None = None) -> dict:
        return {
            "title": title,
            "key_concepts": kc,
            "source_chunk_ids": chunks or [],
        }

    def test_empty_or_single_stage_passthrough(self):
        self.assertEqual(merge_by_concept_overlap([]), [])
        s = [self._stage("X", ["a"])]
        self.assertEqual(merge_by_concept_overlap(s), s)

    def test_cross_source_high_overlap_merges(self):
        """Different chapters / titles but heavily overlapping kc should merge."""
        a = self._stage("借錢外掛（一）：信用貸款", ["信用貸款", "波浪操作", "撥款時間"], ["c1"])
        b = self._stage("借錢工具：信用貸款細節", ["信用貸款", "波浪操作", "額度"], ["c5"])
        # jaccard = 2/4 = 0.5 < 0.6 default; bump threshold lower to force merge
        merged = merge_by_concept_overlap([a, b], threshold=0.4)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["title"], "借錢外掛（一）：信用貸款")  # earlier wins
        # chunks combined
        self.assertEqual(set(merged[0]["source_chunk_ids"]), {"c1", "c5"})

    def test_unrelated_stages_not_merged(self):
        a = self._stage("金融股篩選", ["金融股", "S級", "A級"], ["c1"])
        b = self._stage("房屋貸款", ["房貸", "利率", "撥款"], ["c2"])
        merged = merge_by_concept_overlap([a, b])
        self.assertEqual(len(merged), 2)

    def test_threshold_boundary(self):
        """jaccard == threshold should merge (>= comparison)."""
        a = self._stage("X", ["a", "b", "c"], ["c1"])
        b = self._stage("Y", ["a", "b", "d", "e"], ["c2"])
        # jaccard = 2/5 = 0.4
        self.assertEqual(len(merge_by_concept_overlap([a, b], threshold=0.4)), 1)
        self.assertEqual(len(merge_by_concept_overlap([a, b], threshold=0.5)), 2)

    def test_stages_with_no_kc_not_merged(self):
        a = self._stage("X", [], ["c1"])
        b = self._stage("Y", [], ["c2"])
        merged = merge_by_concept_overlap([a, b])
        self.assertEqual(len(merged), 2)

    def test_uses_env_threshold_when_none_passed(self):
        a = self._stage("X", ["a", "b", "c"], ["c1"])
        b = self._stage("Y", ["a", "b", "c"], ["c2"])  # jaccard = 1.0
        with patch.dict(os.environ, {"STAGE_CONCEPT_OVERLAP_THRESHOLD": "0.99"}, clear=False):
            merged = merge_by_concept_overlap([a, b])
            self.assertEqual(len(merged), 1)


if __name__ == "__main__":
    unittest.main()
