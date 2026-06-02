"""T-MID-XMAT Phase 1: warn-only detector for the medium cross-material gap.

Cross-material curricula with several short sources but < 30 chunks trigger neither
the global StageConsolidator (needs chunks>=30) nor the Phase 4 planner reorder (gated
on chunks>=30), so the sources are never cross-organised. This detector flags that gap
deterministically (no LLM, no mutation, warn-only).
"""
import unittest

from backend.utils.small_curriculum import detect_medium_cross_material_gap


def _stage(stage_id, title, kc=None, chunks=None):
    return {
        "stage_id": stage_id,
        "title": title,
        "key_concepts": list(kc or []),
        "source_chunk_ids": list(chunks or []),
    }


def _medium_stages():
    # shape mirrors live sess_r7we4jmbe: same topic from 2 sources never merged
    return [
        _stage(1, "台股散戶的致命傷"),
        _stage(2, "出場策略四條鐵律：與人生週期同步調整"),
        _stage(3, "稅務攻略：股利所得"),
        _stage(4, "出場策略四條鐵律：建立投資憲法"),
    ]


class TestDetectMediumCrossMaterialGap(unittest.TestCase):
    def test_fires_on_medium_cross_material(self):
        w = detect_medium_cross_material_gap(
            same_material=False, source_count=4, chunk_count=26,
            stages=_medium_stages(),
        )
        self.assertIsNotNone(w)
        self.assertEqual(w["type"], "medium_cross_material_gap")
        self.assertEqual(w["source_count"], 4)
        self.assertEqual(w["chunk_count"], 26)
        self.assertEqual(w["stage_count"], 4)
        self.assertTrue(w["consolidator_skipped"])
        self.assertTrue(w["planner_reorder_skipped"])

    def test_does_not_fire_for_same_material(self):
        self.assertIsNone(detect_medium_cross_material_gap(
            same_material=True, source_count=4, chunk_count=26,
            stages=_medium_stages(),
        ))

    def test_does_not_fire_when_chunks_ge_30(self):
        self.assertIsNone(detect_medium_cross_material_gap(
            same_material=False, source_count=4, chunk_count=30,
            stages=_medium_stages(),
        ))

    def test_does_not_fire_when_sources_lt_3(self):
        self.assertIsNone(detect_medium_cross_material_gap(
            same_material=False, source_count=2, chunk_count=26,
            stages=_medium_stages(),
        ))

    def test_detects_duplicate_theme_across_distinct_stages(self):
        w = detect_medium_cross_material_gap(
            same_material=False, source_count=4, chunk_count=26,
            stages=_medium_stages(),
        )
        groups = {g["theme"]: g["stage_ids"] for g in w["duplicate_theme_groups"]}
        self.assertIn("出場策略四條鐵律", groups)
        self.assertEqual(sorted(groups["出場策略四條鐵律"]), [2, 4])

    def test_followup_siblings_not_counted_as_duplicate(self):
        stages = [
            _stage(1, "概論"),
            _stage(2, "投資策略"),
            _stage(3, "投資策略（續 2）"),
        ]
        w = detect_medium_cross_material_gap(
            same_material=False, source_count=3, chunk_count=20, stages=stages,
        )
        self.assertEqual(w["duplicate_theme_groups"], [])

    def test_no_duplicates_gives_empty_groups(self):
        stages = [_stage(1, "甲"), _stage(2, "乙"), _stage(3, "丙")]
        w = detect_medium_cross_material_gap(
            same_material=False, source_count=3, chunk_count=18, stages=stages,
        )
        self.assertEqual(w["duplicate_theme_groups"], [])
        self.assertEqual(w["stage_per_source"], round(3 / 3, 2))


if __name__ == "__main__":
    unittest.main()
