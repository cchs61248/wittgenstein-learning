"""Phase 4 / T1: deterministic StageCard builder.

Pure adapter that normalizes finalized curriculum stages into immutable stage
cards with deterministic role / difficulty classification and warn-only
diagnostics. No LLM, no prompt, no reorder, no pipeline mutation.
"""
import copy
import unittest

from backend.utils.pedagogical_planner import StageCard, build_stage_cards


def _stage(stage_id=1, title="", key_concepts=(), summary="", **extra):
    s = {
        "stage_id": stage_id,
        "title": title,
        "key_concepts": list(key_concepts),
        "source_chunk_ids": ["chunk_0000"],
    }
    if summary:
        s["summary"] = summary
    s.update(extra)
    return s


class TestBuildStageCards(unittest.TestCase):
    def test_builds_card_from_minimal_stage(self):
        cards, _ = build_stage_cards([_stage(1, "檢索增強生成 (RAG)", ["RAG原理"])])
        self.assertEqual(len(cards), 1)
        c = cards[0]
        self.assertIsInstance(c, StageCard)
        self.assertEqual(c.stage_id, "1")
        self.assertEqual(c.title, "檢索增強生成 (RAG)")
        self.assertEqual(c.key_concepts, ("RAG原理",))

    def test_preserves_order_and_index(self):
        cards, _ = build_stage_cards(
            [_stage(1, "概論導論"), _stage(2, "進階優化"), _stage(3, "課程總結")]
        )
        self.assertEqual([c.stage_index for c in cards], [0, 1, 2])
        self.assertEqual([c.stage_id for c in cards], ["1", "2", "3"])

    def test_extracts_source_metadata_when_present(self):
        cards, _ = build_stage_cards(
            [_stage(1, "X", source_ids=["src_a", "src_b"], source_stage_ids=["candidate_01"])]
        )
        self.assertEqual(cards[0].source_ids, ("src_a", "src_b"))
        self.assertEqual(cards[0].source_stage_ids, ("candidate_01",))

    def test_normalizes_missing_fields_safely(self):
        cards, _ = build_stage_cards([{"stage_id": 7}])
        c = cards[0]
        self.assertEqual(c.title, "")
        self.assertEqual(c.summary, "")
        self.assertEqual(c.key_concepts, ())
        self.assertEqual(c.source_ids, ())
        self.assertEqual(c.source_stage_ids, ())
        self.assertEqual(c.role, "unknown")

    def test_normalizes_key_concepts_strips_and_drops_empty(self):
        cards, _ = build_stage_cards([_stage(1, "X", ["  RAG原理 ", "", "  "])])
        self.assertEqual(cards[0].key_concepts, ("RAG原理",))

    def test_missing_stage_id_falls_back_to_stage_index(self):
        cards, _ = build_stage_cards([{"title": "RAG", "key_concepts": ["RAG原理"]}])
        self.assertEqual(cards[0].stage_id, "stage_0")

    def test_empty_stage_id_falls_back_to_stage_index(self):
        cards, _ = build_stage_cards(
            [{"stage_id": " ", "title": "RAG", "key_concepts": ["RAG原理"]}]
        )
        self.assertEqual(cards[0].stage_id, "stage_0")

    def test_scalar_string_key_concepts_not_split_into_characters(self):
        cards, _ = build_stage_cards([{"stage_id": 1, "title": "RAG", "key_concepts": "RAG原理"}])
        self.assertEqual(cards[0].key_concepts, ("RAG原理",))

    def test_scalar_string_source_ids_not_split_into_characters(self):
        cards, _ = build_stage_cards(
            [{"stage_id": 1, "title": "RAG", "key_concepts": ["RAG原理"], "source_ids": "src_a"}]
        )
        self.assertEqual(cards[0].source_ids, ("src_a",))


class TestRoleClassification(unittest.TestCase):
    def _role(self, title, kcs=(), summary=""):
        cards, _ = build_stage_cards([_stage(1, title, kcs, summary)])
        return cards[0].role

    def test_classifies_summary(self):
        self.assertEqual(self._role("提升 LLM 正確率的綜合總結"), "summary")

    def test_classifies_overview(self):
        self.assertEqual(self._role("提示詞工程概論"), "overview")

    def test_classifies_reference(self):
        self.assertEqual(self._role("參考資料與附錄"), "reference")

    def test_classifies_advanced(self):
        self.assertEqual(self._role("進階提示詞技術：PAL 與 Meta Prompting"), "advanced")

    def test_classifies_application(self):
        self.assertEqual(self._role("RAG 部署實作案例"), "application")

    def test_classifies_foundation(self):
        self.assertEqual(self._role("Transformer 基礎原理"), "foundation")

    def test_classifies_core_default(self):
        self.assertEqual(self._role("檢索增強生成"), "core")

    def test_unknown_when_no_text_signal(self):
        self.assertEqual(self._role(""), "unknown")

    def test_precedence_summary_over_overview(self):
        self.assertEqual(self._role("概論總結"), "summary")

    def test_role_uses_summary_and_key_concepts_text(self):
        # signal lives in summary, not title
        self.assertEqual(self._role("第三節", summary="本節為全課程的總結與回顧"), "summary")


class TestDifficultyClassification(unittest.TestCase):
    def _diff(self, title, kcs=()):
        cards, _ = build_stage_cards([_stage(1, title, kcs)])
        return cards[0].difficulty

    def test_overview_is_low(self):
        self.assertEqual(self._diff("提示詞工程概論"), 1)

    def test_foundation(self):
        self.assertEqual(self._diff("Transformer 基礎原理"), 2)

    def test_core_default(self):
        self.assertEqual(self._diff("檢索增強生成"), 3)

    def test_application(self):
        self.assertEqual(self._diff("RAG 部署實作案例"), 4)

    def test_advanced_is_high(self):
        self.assertEqual(self._diff("進階優化技術"), 5)

    def test_summary_is_high(self):
        self.assertEqual(self._diff("課程總結"), 5)

    def test_reference_is_low(self):
        self.assertEqual(self._diff("參考資料與附錄"), 1)

    def test_difficulty_is_integer(self):
        self.assertIsInstance(self._diff("檢索增強生成"), int)

    def test_advanced_keyword_guarantees_high_difficulty(self):
        self.assertGreaterEqual(self._diff("評估方法"), 4)

    def test_beginner_keyword_guarantees_low_difficulty(self):
        self.assertLessEqual(self._diff("入門簡介"), 2)


class TestDiagnostics(unittest.TestCase):
    def test_emits_missing_title_diagnostic(self):
        _, diags = build_stage_cards([_stage(1, title="", key_concepts=["X"])])
        d = [x for x in diags if x["type"] == "missing_stage_title"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["stage_index"], 0)
        self.assertEqual(d[0]["reason"], "empty_title")

    def test_emits_missing_key_concepts_diagnostic(self):
        _, diags = build_stage_cards([_stage(1, title="RAG", key_concepts=[])])
        d = [x for x in diags if x["type"] == "missing_key_concepts"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["stage_index"], 0)
        self.assertEqual(d[0]["stage_title"], "RAG")
        self.assertEqual(d[0]["reason"], "empty_key_concepts")

    def test_clean_stage_emits_no_diagnostics(self):
        _, diags = build_stage_cards([_stage(1, "RAG 概論", ["RAG原理"])])
        self.assertEqual(diags, [])


class TestPurityAndDeterminism(unittest.TestCase):
    def test_does_not_mutate_input(self):
        stages = [_stage(1, "概論", ["X"], source_ids=["a"])]
        before = copy.deepcopy(stages)
        build_stage_cards(stages)
        self.assertEqual(stages, before)

    def test_deterministic_output(self):
        stages = [_stage(1, "概論", ["A"]), _stage(2, "進階", ["B"]), _stage(3, "總結", ["C"])]
        self.assertEqual(build_stage_cards(stages), build_stage_cards(stages))

    def test_stage_card_is_frozen(self):
        cards, _ = build_stage_cards([_stage(1, "概論", ["A"])])
        with self.assertRaises(Exception):
            cards[0].role = "mutated"


if __name__ == "__main__":
    unittest.main()
