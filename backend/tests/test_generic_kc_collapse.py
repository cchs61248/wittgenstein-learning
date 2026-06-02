"""generic_kc_collapse: warn-only detector for umbrella/generic key_concept degradation.

When the splitter degrades specific concepts into broad umbrella terms (e.g. '基本概念',
'核心內容', '相關知識'), the curriculum's key_concepts stop being teachable/specific. This
is a cross-stage kc-quality signal distinct from the stage-local hygiene audits
(malformed_key_concept / meta_only_key_concepts). Detector is pure / deterministic / no
LLM / no mutation / warn-only. Live root: sess_tkfe20227 (kc 退化成傘狀詞).
"""
import unittest

from backend.utils.small_curriculum import detect_generic_kc_collapse


def _stage(stage_id, title, kc=None, **extra):
    s = {
        "stage_id": stage_id,
        "title": title,
        "key_concepts": list(kc or []),
        "source_chunk_ids": [],
    }
    s.update(extra)
    return s


class TestDetectGenericKcCollapse(unittest.TestCase):
    def test_returns_none_for_clean_curriculum(self):
        stages = [
            _stage(1, "融資斷頭", kc=["融資斷頭效應", "籌碼結構分析", "散戶追高陷阱"]),
            _stage(2, "核心衛星配置", kc=["核心衛星配置法", "防禦性資產", "停損停利紀律"]),
        ]
        self.assertIsNone(detect_generic_kc_collapse(stages))

    def test_returns_none_for_empty_input(self):
        self.assertIsNone(detect_generic_kc_collapse([]))

    def test_returns_none_when_single_generic_among_specific(self):
        # one umbrella kc in a stage of specific ones -> below stage ratio + below
        # curriculum ratio -> no warning noise
        stages = [
            _stage(1, "A", kc=["融資斷頭效應", "籌碼結構分析", "基本概念"]),
            _stage(2, "B", kc=["核心衛星配置法", "防禦性資產", "停損停利紀律"]),
        ]
        self.assertIsNone(detect_generic_kc_collapse(stages))

    def test_fires_rule_a_on_generic_dominated_stage(self):
        stages = [
            _stage(1, "投資概論", kc=["基本概念", "核心內容", "相關知識"]),
            _stage(2, "B", kc=["核心衛星配置法", "防禦性資產", "停損停利紀律"]),
            _stage(3, "C", kc=["填息率", "成分股集中風險", "產業分散"]),
        ]
        w = detect_generic_kc_collapse(stages)
        self.assertIsNotNone(w)
        self.assertEqual(w["type"], "generic_kc_collapse")
        collapsed = {c["stage_id"]: c for c in w["collapsed_stages"]}
        self.assertIn(1, collapsed)
        self.assertNotIn(2, collapsed)
        self.assertEqual(
            sorted(collapsed[1]["generic_key_concepts"]),
            ["基本概念", "核心內容", "相關知識"],
        )
        self.assertEqual(collapsed[1]["kc_count"], 3)
        self.assertEqual(collapsed[1]["generic_ratio"], 1.0)

    def test_stage_with_two_generic_of_three_is_collapsed(self):
        stages = [
            _stage(1, "X", kc=["基本概念", "相關知識", "填息率"]),
            _stage(2, "Y", kc=["核心衛星配置法", "防禦性資產", "停損停利紀律"]),
            _stage(3, "Z", kc=["三問決策框架", "長期趨勢誤區"]),
        ]
        w = detect_generic_kc_collapse(stages)
        self.assertIsNotNone(w)
        ids = {c["stage_id"] for c in w["collapsed_stages"]}
        self.assertEqual(ids, {1})

    def test_fires_rule_b_curriculum_wide(self):
        # each stage has 1 generic of 2 (50% per stage but <2 generic so no Rule A);
        # curriculum-wide ratio 4/8 = 0.5 >= 0.3 -> curriculum_collapse
        stages = [
            _stage(1, "A", kc=["基本概念", "融資斷頭效應"]),
            _stage(2, "B", kc=["核心內容", "防禦性資產"]),
            _stage(3, "C", kc=["相關知識", "填息率"]),
            _stage(4, "D", kc=["重點", "三問決策框架"]),
        ]
        w = detect_generic_kc_collapse(stages)
        self.assertIsNotNone(w)
        self.assertTrue(w["curriculum_collapse"])
        self.assertEqual(w["generic_kc_total"], 4)
        self.assertEqual(w["total_kc"], 8)
        self.assertEqual(w["generic_ratio"], 0.5)
        # no single stage has >=2 generic -> Rule A empty
        self.assertEqual(w["collapsed_stages"], [])

    def test_followups_excluded_from_scan(self):
        # follow-up copies base kc; must not double-count or be flagged itself
        stages = [
            _stage(1, "投資策略", kc=["核心衛星配置法", "防禦性資產", "停損停利紀律"]),
            _stage(2, "投資策略（續 2）", kc=["核心衛星配置法", "防禦性資產", "停損停利紀律"],
                   kind="follow_up_orphan"),
        ]
        # all-specific -> None even though follow-up present
        self.assertIsNone(detect_generic_kc_collapse(stages))

    def test_followup_generic_not_counted(self):
        base_only_generic = [
            _stage(1, "概論", kc=["基本概念", "核心內容", "相關知識"]),
            _stage(2, "概論（續 2）", kc=["基本概念", "核心內容", "相關知識"],
                   kind="follow_up_orphan"),
            _stage(3, "B", kc=["核心衛星配置法", "防禦性資產", "停損停利紀律"]),
            _stage(4, "C", kc=["填息率", "成分股集中風險", "產業分散"]),
        ]
        w = detect_generic_kc_collapse(base_only_generic)
        self.assertIsNotNone(w)
        # only the base (stage 1) counted, follow-up (stage 2) excluded
        ids = {c["stage_id"] for c in w["collapsed_stages"]}
        self.assertEqual(ids, {1})
        self.assertEqual(w["generic_kc_total"], 3)  # not 6
        self.assertEqual(w["total_kc"], 9)          # 3 + 3 + 3, follow-up excluded

    def test_stage_with_one_kc_not_collapsed(self):
        # a single-kc stage can't meet generic_count>=2; only curriculum ratio applies
        stages = [
            _stage(1, "案例：萊納斯", kc=["萊納斯稀土公司"]),
            _stage(2, "概念", kc=["基本概念"]),
            _stage(3, "B", kc=["核心衛星配置法", "防禦性資產", "停損停利紀律"]),
        ]
        # generic 1/5 = 0.2 < 0.3 -> None
        self.assertIsNone(detect_generic_kc_collapse(stages))

    def test_payload_shape(self):
        stages = [
            _stage(1, "投資概論", kc=["基本概念", "核心內容", "相關知識"]),
            _stage(2, "B", kc=["核心衛星配置法", "防禦性資產"]),
        ]
        w = detect_generic_kc_collapse(stages)
        self.assertEqual(
            set(w.keys()),
            {"type", "stage_count", "total_kc", "generic_kc_total",
             "generic_ratio", "collapsed_stages", "curriculum_collapse"},
        )
        self.assertEqual(w["stage_count"], 2)  # total stages, like medium gap


if __name__ == "__main__":
    unittest.main()
