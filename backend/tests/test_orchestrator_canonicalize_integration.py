"""Pure-helper tests for _apply_canonical_mappings.

對應 spec: docs/superpowers/specs/2026-05-21-canonicalize-agent-design.md § 6

註：原本的 start_session 整合測試（pin CURRICULUM_PIPELINE_V2=0）已隨 D3
（V1 pipeline 移除）一併刪除。V2 對 canonicalize 的整合覆蓋見
test_curriculum_pipeline_v2.py。
"""
import unittest

from backend.orchestrator.learning_orchestrator import _apply_canonical_mappings


class TestApplyCanonicalMappings(unittest.TestCase):
    def test_mapped_rewrites_key_concept(self):
        stages = [
            {"stage_id": 1, "key_concepts": ["巴菲特家世背景", "賠得起的優雅"]},
        ]
        mappings = [
            {"new_name": "巴菲特家世背景", "decision": "mapped",
             "canonical": "巴菲特神話", "reason": ""},
            {"new_name": "賠得起的優雅", "decision": "new",
             "canonical": None, "reason": ""},
        ]
        result = _apply_canonical_mappings(stages, mappings)
        self.assertEqual(result[0]["key_concepts"], ["巴菲特神話", "賠得起的優雅"])

    def test_new_keeps_original_name(self):
        stages = [{"stage_id": 1, "key_concepts": ["概念A"]}]
        mappings = [{"new_name": "概念A", "decision": "new",
                     "canonical": None, "reason": ""}]
        result = _apply_canonical_mappings(stages, mappings)
        self.assertEqual(result[0]["key_concepts"], ["概念A"])

    def test_unsure_keeps_original_name(self):
        stages = [{"stage_id": 1, "key_concepts": ["概念B"]}]
        mappings = [{"new_name": "概念B", "decision": "unsure",
                     "canonical": None, "reason": ""}]
        result = _apply_canonical_mappings(stages, mappings)
        self.assertEqual(result[0]["key_concepts"], ["概念B"])

    def test_same_concept_across_stages_consistent_mapping(self):
        stages = [
            {"stage_id": 1, "key_concepts": ["X", "Y"]},
            {"stage_id": 5, "key_concepts": ["X"]},
        ]
        mappings = [
            {"new_name": "X", "decision": "mapped",
             "canonical": "X_canonical", "reason": ""},
            {"new_name": "Y", "decision": "new",
             "canonical": None, "reason": ""},
        ]
        result = _apply_canonical_mappings(stages, mappings)
        self.assertEqual(result[0]["key_concepts"], ["X_canonical", "Y"])
        self.assertEqual(result[1]["key_concepts"], ["X_canonical"])

    def test_missing_mapping_keeps_original(self):
        stages = [{"stage_id": 1, "key_concepts": ["概念A", "概念B"]}]
        mappings = [{"new_name": "概念A", "decision": "mapped",
                     "canonical": "X", "reason": ""}]
        result = _apply_canonical_mappings(stages, mappings)
        self.assertEqual(result[0]["key_concepts"], ["X", "概念B"])

    def test_empty_stages_returns_empty(self):
        result = _apply_canonical_mappings([], [])
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
