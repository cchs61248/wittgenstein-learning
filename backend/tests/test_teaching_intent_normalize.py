import unittest

from backend.utils.teaching_intent import normalize_teaching_intent


class TestTeachingIntentNormalize(unittest.TestCase):
    def test_inline_key_concepts_maps_to_reinforced(self):
        raw = {
            "key_concepts": ["利差交易", "滾雪球"],
            "expected_misunderstandings": ["以為房貸本金會減少"],
            "evidence_chunk_ids": ["chunk_0012"],
        }
        out = normalize_teaching_intent(raw, {"key_concepts": ["利差交易", "滾雪球", "其他"]})
        self.assertEqual(out["reinforced_concepts"], ["利差交易", "滾雪球"])
        self.assertIn("房貸", out["repair_target"] or "")
        self.assertEqual(out["main_chunk_ids"], ["chunk_0012"])

    def test_extract_schema_passthrough(self):
        raw = {
            "reinforced_concepts": ["A"],
            "analogies_used": ["像滾雪球"],
            "repair_target": None,
            "main_chunk_ids": ["chunk_0001"],
        }
        out = normalize_teaching_intent(raw)
        self.assertEqual(out["reinforced_concepts"], ["A"])
        self.assertEqual(out["analogies_used"], ["像滾雪球"])

    def test_empty_raw_uses_stage_fallback(self):
        out = normalize_teaching_intent(None, {"key_concepts": ["X", "Y", "Z"]})
        self.assertEqual(out["reinforced_concepts"], ["X", "Y"])


if __name__ == "__main__":
    unittest.main()
