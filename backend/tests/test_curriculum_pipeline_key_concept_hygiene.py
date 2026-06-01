"""PR1b commit 2: pipeline wiring for key-concept hygiene warnings.

Proves the thin glue the curriculum pipeline calls after finalize/canonicalize:
warn-only, list under quality_warnings["key_concept_hygiene"], append (never
overwrite), no mutation of input warnings, and clean builds stay key-free.
"""
import copy
import unittest

from backend.orchestrator.curriculum_pipeline_v2 import (
    _merge_key_concept_hygiene_warnings,
)


def _stage(title, kcs):
    return {"title": title, "key_concepts": list(kcs), "source_chunk_ids": ["chunk_0000"]}


class TestPipelineKeyConceptHygieneWiring(unittest.TestCase):
    def test_clean_leaves_warnings_untouched(self):
        stages = [_stage("檢索增強生成 (RAG)", ["RAG原理"])]
        qw = {"small_file_path": True}
        out = _merge_key_concept_hygiene_warnings(stages, qw)
        self.assertNotIn("key_concept_hygiene", out or {})
        self.assertEqual(out, qw)

    def test_clean_with_none_stays_none(self):
        stages = [_stage("檢索增強生成 (RAG)", ["RAG原理"])]
        self.assertIsNone(_merge_key_concept_hygiene_warnings(stages, None))

    def test_dirty_attaches_list(self):
        stages = [_stage("提升 LLM 正確率的綜合總結", ["提升 LLM 正"])]
        out = _merge_key_concept_hygiene_warnings(stages, {"small_file_path": True})
        self.assertEqual(out["small_file_path"], True)
        self.assertEqual(len(out["key_concept_hygiene"]), 1)
        self.assertEqual(out["key_concept_hygiene"][0]["type"], "malformed_key_concept")

    def test_dirty_with_none_quality_warnings(self):
        stages = [_stage("課程總結", ["章節總結"])]
        out = _merge_key_concept_hygiene_warnings(stages, None)
        self.assertEqual(len(out["key_concept_hygiene"]), 1)
        self.assertEqual(out["key_concept_hygiene"][0]["type"], "meta_only_key_concepts")

    def test_appends_to_existing_hygiene_list(self):
        stages = [_stage("提升 LLM 正確率的綜合總結", ["提升 LLM 正"])]
        prior = {"key_concept_hygiene": [{"type": "preexisting"}]}
        out = _merge_key_concept_hygiene_warnings(stages, prior)
        self.assertEqual(len(out["key_concept_hygiene"]), 2)
        self.assertEqual(out["key_concept_hygiene"][0]["type"], "preexisting")

    def test_does_not_mutate_input_warnings(self):
        stages = [_stage("提升 LLM 正確率的綜合總結", ["提升 LLM 正"])]
        qw = {"small_file_path": True}
        before = copy.deepcopy(qw)
        _merge_key_concept_hygiene_warnings(stages, qw)
        self.assertEqual(qw, before)


if __name__ == "__main__":
    unittest.main()
