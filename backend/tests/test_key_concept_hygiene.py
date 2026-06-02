"""PR1b: build-time key-concept hygiene warnings (warn-only, stage-local).

Two detectors, both audit-only — they never mutate stages / key_concepts:
- malformed_key_concept:  a kc that looks hard-truncated from its stage title
                          (root cause of the live `提升 LLM 正` regression).
- meta_only_key_concepts: a stage whose key_concepts are ALL meta/summary labels.
"""
import copy
import unittest

from backend.utils.small_curriculum import (
    collect_key_concept_hygiene_warnings,
    _is_likely_malformed_key_concept,
    _is_meta_only_key_concepts,
    _META_ONLY_KEY_CONCEPTS,
)


class TestIsLikelyMalformedKeyConcept(unittest.TestCase):
    def test_live_truncated_prefix_warns(self):
        self.assertTrue(
            _is_likely_malformed_key_concept("提升 LLM 正確率的綜合總結", "提升 LLM 正")
        )

    def test_complete_phrase_does_not_warn(self):
        # diff == 0 → not a truncation
        self.assertFalse(
            _is_likely_malformed_key_concept("提升 LLM 正確率的綜合總結", "提升 LLM 正確率")
        )

    def test_short_cjk_term_does_not_warn(self):
        # len(kc) < 6 → below the [:8] truncation band
        self.assertFalse(_is_likely_malformed_key_concept("RAG原理與檢索增強", "RAG原理"))

    def test_ascii_only_hyphenated_term_does_not_warn(self):
        # no CJK in kc → protected (Auto-CoT must survive)
        self.assertFalse(_is_likely_malformed_key_concept("Auto-CoT 自動化思維鏈", "Auto-CoT"))

    def test_ascii_only_spaced_term_does_not_warn(self):
        self.assertFalse(
            _is_likely_malformed_key_concept("Zero-shot CoT 應用", "Zero-shot CoT")
        )

    def test_kc_not_prefix_of_title_does_not_warn(self):
        self.assertFalse(_is_likely_malformed_key_concept("完全不同的標題", "提升 LLM 正"))


class TestIsMetaOnlyKeyConcepts(unittest.TestCase):
    def test_single_meta_warns(self):
        self.assertTrue(_is_meta_only_key_concepts(["章節總結"]))

    def test_overview_meta_warns(self):
        self.assertTrue(_is_meta_only_key_concepts(["概述"]))

    def test_all_meta_multi_warns(self):
        self.assertTrue(_is_meta_only_key_concepts(["總結", "導論"]))

    def test_mixed_with_real_concept_does_not_warn(self):
        self.assertFalse(_is_meta_only_key_concepts(["RAG原理", "章節總結"]))

    def test_real_concept_does_not_warn(self):
        self.assertFalse(_is_meta_only_key_concepts(["提升 LLM 正確率"]))

    def test_empty_does_not_warn(self):
        self.assertFalse(_is_meta_only_key_concepts([]))

    # T-META-KC-SUPPLEMENT: chapter-supplement filler labels are meta-only too.
    # Live evidence: sess_k73w3v6ah stage "章節補充" slipped through this set while
    # sess_u2ccjo94t "章節總結" was caught — same filler class,詞表 coverage hole.
    def test_chapter_supplement_warns(self):
        self.assertTrue(_is_meta_only_key_concepts(["章節補充"]))

    def test_supplement_note_warns(self):
        self.assertTrue(_is_meta_only_key_concepts(["補充說明"]))

    def test_supplement_content_warns(self):
        self.assertTrue(_is_meta_only_key_concepts(["補充內容"]))

    def test_all_meta_mix_old_and_new_warns(self):
        self.assertTrue(_is_meta_only_key_concepts(["章節總結", "章節補充"]))

    def test_supplement_substring_real_concept_does_not_warn(self):
        # exact-match guard: real concepts that merely CONTAIN 補充 must not be flagged
        for kc in ["補充保費", "營養補充品", "補充醫療保險", "補充資料分析"]:
            self.assertFalse(_is_meta_only_key_concepts([kc]), kc)

    def test_meta_set_contents(self):
        self.assertEqual(
            _META_ONLY_KEY_CONCEPTS,
            {"章節總結", "綜合總結", "總結", "概述", "導論",
             "章節補充", "補充說明", "補充內容"},
        )


def _stage(title, kcs):
    return {"title": title, "key_concepts": list(kcs), "source_chunk_ids": ["chunk_0000"]}


class TestCollectKeyConceptHygieneWarnings(unittest.TestCase):
    def test_clean_returns_empty(self):
        stages = [
            _stage("檢索增強生成 (RAG)", ["RAG原理"]),
            _stage("自動化思維鏈", ["Auto-CoT"]),
        ]
        self.assertEqual(collect_key_concept_hygiene_warnings(stages), [])

    def test_malformed_emitted(self):
        stages = [_stage("提升 LLM 正確率的綜合總結", ["提升 LLM 正"])]
        out = collect_key_concept_hygiene_warnings(stages)
        self.assertEqual(len(out), 1)
        w = out[0]
        self.assertEqual(w["type"], "malformed_key_concept")
        self.assertEqual(w["stage_index"], 0)
        self.assertEqual(w["stage_title"], "提升 LLM 正確率的綜合總結")
        self.assertEqual(w["key_concept"], "提升 LLM 正")
        self.assertEqual(w["reason"], "likely_hard_truncated_title_prefix")

    def test_meta_only_emitted(self):
        stages = [_stage("課程總結", ["章節總結"])]
        out = collect_key_concept_hygiene_warnings(stages)
        self.assertEqual(len(out), 1)
        w = out[0]
        self.assertEqual(w["type"], "meta_only_key_concepts")
        self.assertEqual(w["stage_index"], 0)
        self.assertEqual(w["stage_title"], "課程總結")
        self.assertEqual(w["key_concepts"], ["章節總結"])
        self.assertEqual(w["reason"], "all_key_concepts_are_meta_labels")

    def test_meta_only_chapter_supplement_emitted(self):
        # live shape: sess_k73w3v6ah stage "台股交易隱形成本與稅務盲區" kc=["章節補充"]
        stages = [_stage("台股交易隱形成本與稅務盲區", ["章節補充"])]
        out = collect_key_concept_hygiene_warnings(stages)
        self.assertEqual(len(out), 1)
        w = out[0]
        self.assertEqual(w["type"], "meta_only_key_concepts")
        self.assertEqual(w["key_concepts"], ["章節補充"])
        self.assertEqual(w["reason"], "all_key_concepts_are_meta_labels")

    def test_both_types_can_appear(self):
        stages = [
            _stage("提升 LLM 正確率的綜合總結", ["提升 LLM 正"]),
            _stage("檢索增強生成 (RAG)", ["RAG原理"]),
            _stage("課程總結", ["章節總結"]),
        ]
        out = collect_key_concept_hygiene_warnings(stages)
        types = [w["type"] for w in out]
        self.assertIn("malformed_key_concept", types)
        self.assertIn("meta_only_key_concepts", types)
        self.assertEqual(len(out), 2)

    def test_does_not_mutate_stages(self):
        stages = [_stage("提升 LLM 正確率的綜合總結", ["提升 LLM 正"])]
        before = copy.deepcopy(stages)
        collect_key_concept_hygiene_warnings(stages)
        self.assertEqual(stages, before)

    def test_deterministic_ordering(self):
        stages = [
            _stage("提升 LLM 正確率的綜合總結", ["提升 LLM 正"]),
            _stage("課程總結", ["章節總結"]),
        ]
        out1 = collect_key_concept_hygiene_warnings(stages)
        out2 = collect_key_concept_hygiene_warnings(stages)
        self.assertEqual(out1, out2)
        self.assertEqual([w["stage_index"] for w in out1], [0, 1])


if __name__ == "__main__":
    unittest.main()
