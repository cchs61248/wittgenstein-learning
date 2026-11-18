"""P3b: deterministic stage ordering after LLM consolidator.

Real motivation: sess_t641psl54 produced "借錢工具（二）：股票質押" BEFORE
"借錢工具（一）：信貸與房貸" and case-stages scattered across the map.
"""
import unittest

from backend.utils.small_curriculum import (
    _extract_followup,
    _extract_ordinal_group,
    _stage_min_chunk,
    enforce_stage_ordering,
    ensure_empty_key_concepts,
    merge_singleton_chunk_stages,
)


def _stage(
    title: str,
    cids: list[str],
    kc: list[str] | None = None,
    kind: str | None = None,
) -> dict:
    s = {
        "title": title,
        "source_chunk_ids": cids,
        "key_concepts": kc or [],
    }
    if kind:
        s["kind"] = kind
    return s


class TestExtractOrdinalGroup(unittest.TestCase):
    def test_chinese_ordinal(self):
        self.assertEqual(_extract_ordinal_group("借錢工具（一）：信貸"), ("借錢工具", 1))
        self.assertEqual(_extract_ordinal_group("借錢工具（二）：房貸"), ("借錢工具", 2))

    def test_arabic_ordinal(self):
        self.assertEqual(_extract_ordinal_group("方案 (3)：股票"), ("方案", 3))

    def test_no_ordinal(self):
        self.assertIsNone(_extract_ordinal_group("案例：新光金"))
        self.assertIsNone(_extract_ordinal_group("致富框架"))
        self.assertIsNone(_extract_ordinal_group(""))

    def test_ordinal_without_prefix(self):
        # "（一）" alone has no group → returns None
        self.assertIsNone(_extract_ordinal_group("（一）"))


class TestStageMinChunk(unittest.TestCase):
    def test_normal(self):
        s = _stage("X", ["chunk_0014", "chunk_0012", "chunk_0099"])
        self.assertEqual(_stage_min_chunk(s), "chunk_0012")

    def test_empty(self):
        self.assertEqual(_stage_min_chunk(_stage("X", [])), "chunk_zzzz")


class TestEnforceStageOrdering(unittest.TestCase):
    def test_empty_or_single_passes_through(self):
        self.assertEqual(enforce_stage_ordering([]), [])
        single = [_stage("A", ["chunk_0001"])]
        self.assertEqual(len(enforce_stage_ordering(single)), 1)

    def test_ordinal_group_reordered(self):
        """sess_t641psl54 actual bug: (二) had smaller first chunk than (一)."""
        stages = [
            _stage("借錢工具（二）：股票質押", ["chunk_0012", "chunk_0144"]),
            _stage("借錢工具（一）：信貸與房貸", ["chunk_0014", "chunk_0141"]),
        ]
        out = enforce_stage_ordering(stages)
        self.assertEqual(out[0]["title"], "借錢工具（一）：信貸與房貸")
        self.assertEqual(out[1]["title"], "借錢工具（二）：股票質押")

    def test_group_contiguous_with_other_stages(self):
        """Group stays adjacent even if a non-group stage falls in between by chunk_id."""
        stages = [
            _stage("致富框架：背景", ["chunk_0000"]),
            _stage("借錢工具（二）：股票質押", ["chunk_0012"]),
            _stage("投資心理", ["chunk_0013"]),  # would naturally fall between (二) and (一)
            _stage("借錢工具（一）：信貸", ["chunk_0014"]),
            _stage("總結", ["chunk_0200"]),
        ]
        out = enforce_stage_ordering(stages)
        titles = [s["title"] for s in out]
        # Group must be contiguous at position min(0012, 0014) = 0012
        i1 = titles.index("借錢工具（一）：信貸")
        i2 = titles.index("借錢工具（二）：股票質押")
        self.assertEqual(i2 - i1, 1, f"group not contiguous: {titles}")
        self.assertLess(i1, titles.index("投資心理"))

    def test_singleton_ordinal_degrades_to_single(self):
        """If only （一） exists with no （二）, treat as non-group."""
        stages = [
            _stage("金融股紅利", ["chunk_0050"]),
            _stage("方案（一）：唯一選項", ["chunk_0010"]),
        ]
        out = enforce_stage_ordering(stages)
        self.assertEqual(out[0]["title"], "方案（一）：唯一選項")

    def test_non_group_sorted_by_chunk_id(self):
        stages = [
            _stage("C", ["chunk_0050"]),
            _stage("A", ["chunk_0001"]),
            _stage("B", ["chunk_0020"]),
        ]
        titles = [s["title"] for s in enforce_stage_ordering(stages)]
        self.assertEqual(titles, ["A", "B", "C"])

    def test_shared_boundary_chunk_does_not_drag_stage_forward(self):
        """sess_rykux8gxa：「處理路徑」與「中繼資料路徑」都含共用 chunk_0002，
        舊版 min() 把處理路徑排到中繼資料路徑之前。應依獨佔 chunk 排序。"""
        stages = [
            _stage("大型二進位檔", ["chunk_0000", "chunk_0001"]),
            _stage("設計診斷", ["chunk_0002", "chunk_0001"]),
            _stage("中繼資料路徑", ["chunk_0003", "chunk_0004"]),
            _stage("上傳路徑", ["chunk_0005", "chunk_0006", "chunk_0011"]),
            _stage("狀態同步", ["chunk_0008", "chunk_0005"]),
            _stage("處理路徑", ["chunk_0007", "chunk_0002"]),
            _stage("下載路徑", ["chunk_0009", "chunk_0002"]),
            _stage("清理路徑", ["chunk_0010", "chunk_0008"]),
        ]
        titles = [s["title"] for s in enforce_stage_ordering(stages)]
        self.assertLess(titles.index("中繼資料路徑"), titles.index("上傳路徑"))
        self.assertLess(titles.index("上傳路徑"), titles.index("處理路徑"))
        self.assertLess(titles.index("處理路徑"), titles.index("下載路徑"))
        self.assertLess(titles.index("下載路徑"), titles.index("清理路徑"))
        self.assertLess(titles.index("設計診斷"), titles.index("中繼資料路徑"))

    def test_fully_shared_stage_positioned_by_last_chunk(self):
        """全部 chunk 都被共用的 stage 以最後一個 chunk 定位，不被最小值拉前。"""
        stages = [
            _stage("A", ["chunk_0000", "chunk_0001"]),
            _stage("B", ["chunk_0002", "chunk_0003"]),
            _stage("C", ["chunk_0003", "chunk_0004"]),
            _stage("D", ["chunk_0004", "chunk_0005"]),
        ]
        titles = [s["title"] for s in enforce_stage_ordering(stages)]
        self.assertEqual(titles, ["A", "B", "C", "D"])

    def test_node_id_reassigned(self):
        stages = [
            _stage("B", ["chunk_0020"]),
            _stage("A", ["chunk_0001"]),
        ]
        out = enforce_stage_ordering(stages)
        self.assertEqual(out[0]["node_id"], "1.1")
        self.assertEqual(out[1]["node_id"], "1.2")

    def test_real_session_t641psl54_partial(self):
        """Subset of sess_t641psl54 actual stages — verify ordering matches expected."""
        stages = [
            _stage("致富框架：背景與本金陷阱", ["chunk_0000"]),
            _stage("投資心理：輸家性格與對策", ["chunk_0001"]),
            _stage("致富框架：傳統思維批判", ["chunk_0006"]),
            _stage("借錢工具（二）：股票質押", ["chunk_0012", "chunk_0144"]),
            _stage("借錢工具（一）：信貸與房貸", ["chunk_0014", "chunk_0141"]),
            _stage("借錢炒股：迷思拆解與對比", ["chunk_0032"]),
            _stage("案例：新光金與特許壟斷", ["chunk_0045"]),
        ]
        out = enforce_stage_ordering(stages)
        titles = [s["title"] for s in out]
        # （一）must precede（二）
        self.assertLess(
            titles.index("借錢工具（一）：信貸與房貸"),
            titles.index("借錢工具（二）：股票質押"),
        )
        # Group「借錢工具」must be contiguous
        i1 = titles.index("借錢工具（一）：信貸與房貸")
        i2 = titles.index("借錢工具（二）：股票質押")
        self.assertEqual(i2 - i1, 1)
        # First stage stays first (chunk_0000)
        self.assertEqual(titles[0], "致富框架：背景與本金陷阱")


class TestExtractFollowup(unittest.TestCase):
    def test_followup_with_batch(self):
        s = _stage("案例：肥羊與診所護士實戰（續 2）", ["chunk_0064"])
        self.assertEqual(_extract_followup(s), ("案例：肥羊與診所護士實戰", 2))

    def test_followup_no_batch(self):
        s = _stage("借錢炒股（續）", ["chunk_0010"])
        self.assertEqual(_extract_followup(s), ("借錢炒股", 1))

    def test_not_followup(self):
        self.assertIsNone(_extract_followup(_stage("致富框架", ["chunk_0001"])))
        # （續 is required to be at end with closing 括弧
        self.assertIsNone(_extract_followup(_stage("續集電影", ["chunk_0001"])))


class TestFollowupGluedToBase(unittest.TestCase):
    def test_followup_immediately_after_base(self):
        """P4a real bug from sess_pmulzyche: 續 2 was 6 stages away from base."""
        stages = [
            _stage("致富框架", ["chunk_0000"]),
            _stage("案例：肥羊與護士", ["chunk_0022"]),  # base, min=0022
            _stage("借錢工具", ["chunk_0026"]),          # min=0026, would slot between
            _stage("借錢炒股", ["chunk_0036"]),          # min=0036
            _stage(
                "案例：肥羊與護士（續 2）",
                ["chunk_0064"], kind="follow_up_orphan",
            ),
        ]
        out = enforce_stage_ordering(stages)
        titles = [s["title"] for s in out]
        i_base = titles.index("案例：肥羊與護士")
        i_follow = titles.index("案例：肥羊與護士（續 2）")
        self.assertEqual(
            i_follow - i_base, 1,
            f"followup must be adjacent to base; got titles={titles}",
        )

    def test_multiple_followups_sorted_by_batch(self):
        stages = [
            _stage("A", ["chunk_0001"]),
            _stage("A（續 3）", ["chunk_0030"]),
            _stage("A（續 1）", ["chunk_0010"]),
            _stage("A（續 2）", ["chunk_0020"]),
        ]
        out = enforce_stage_ordering(stages)
        titles = [s["title"] for s in out]
        self.assertEqual(titles, ["A", "A（續 1）", "A（續 2）", "A（續 3）"])

    def test_unmatched_followup_falls_back_to_tail(self):
        """If base title was renamed by consolidator, followup falls to tail by chunk_id."""
        stages = [
            _stage("致富框架", ["chunk_0000"]),
            _stage("借錢炒股", ["chunk_0010"]),
            _stage(
                "OldBaseName（續 1）",
                ["chunk_0050"], kind="follow_up_orphan",
            ),
        ]
        out = enforce_stage_ordering(stages)
        titles = [s["title"] for s in out]
        # Unmatched followup goes to end
        self.assertEqual(titles[-1], "OldBaseName（續 1）")


class TestMergeSingletonChunkStages(unittest.TestCase):
    def test_middle_singleton_folded_into_previous(self):
        stages = [
            _stage("Intro", ["chunk_0000", "chunk_0001"]),
            _stage("Solo", ["chunk_0002"], kc=["x"]),  # middle singleton -> merge into Intro
            _stage("Body", ["chunk_0003", "chunk_0004"]),
            _stage("End", ["chunk_0005", "chunk_0006"]),
        ]
        out = merge_singleton_chunk_stages(stages)
        titles = [s["title"] for s in out]
        self.assertEqual(titles, ["Intro", "Body", "End"])
        # chunk merged in
        self.assertIn("chunk_0002", out[0]["source_chunk_ids"])
        self.assertIn("x", out[0]["key_concepts"])

    def test_head_singleton_preserved(self):
        """Head singleton is often a 序章, keep it."""
        stages = [
            _stage("Intro", ["chunk_0000"]),
            _stage("Body", ["chunk_0001", "chunk_0002"]),
            _stage("End", ["chunk_0003", "chunk_0004"]),
        ]
        out = merge_singleton_chunk_stages(stages)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0]["title"], "Intro")

    def test_tail_singleton_preserved(self):
        """Tail singleton is often a 結語, keep it."""
        stages = [
            _stage("Intro", ["chunk_0000", "chunk_0001"]),
            _stage("Body", ["chunk_0002", "chunk_0003"]),
            _stage("End", ["chunk_0004"]),
        ]
        out = merge_singleton_chunk_stages(stages)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[-1]["title"], "End")

    def test_followup_singleton_preserved(self):
        """follow_up_orphan kind never merged even if 1 chunk."""
        stages = [
            _stage("Intro", ["chunk_0000", "chunk_0001"]),
            _stage("A（續 1）", ["chunk_0002"], kind="follow_up_orphan"),
            _stage("Body", ["chunk_0003", "chunk_0004"]),
            _stage("End", ["chunk_0005", "chunk_0006"]),
        ]
        out = merge_singleton_chunk_stages(stages)
        titles = [s["title"] for s in out]
        self.assertIn("A（續 1）", titles)

    def test_short_list_passthrough(self):
        self.assertEqual(merge_singleton_chunk_stages([]), [])
        single = [_stage("X", ["chunk_0001"])]
        self.assertEqual(merge_singleton_chunk_stages(single), single)

    def test_parallel_ordinal_siblings_not_cascaded(self):
        """sess_7r5prsw3c real bug: reroll correctly split 道德經「四樣核心功夫」
        into 4 independent 1-chunk stages（不爭/虛中/樸實/自然）, each declared
        via an explicit ordinal marker（一）〜（四）. The old cascading merge
        folded all 4 into a single stage (losing the SplitterVerifier-mandated
        parallel split). Same-base-different-ordinal siblings must be preserved
        even though each is a middle singleton.
        """
        stages = [
            _stage("Intro", ["chunk_0000", "chunk_0001"]),
            _stage("道德經功夫（一）：不爭的功夫", ["chunk_0008"], kc=["上善若水"]),
            _stage("道德經功夫（二）：虛中的功夫", ["chunk_0009"], kc=["跂者不立"]),
            _stage("道德經功夫（三）：樸實的功夫", ["chunk_0010"], kc=["豫兮若冬涉川"]),
            _stage("道德經功夫（四）：自然的功夫", ["chunk_0011"], kc=["道法自然"]),
            _stage("End", ["chunk_0023"]),
        ]
        out = merge_singleton_chunk_stages(stages)
        titles = [s["title"] for s in out]
        self.assertEqual(
            titles,
            [
                "Intro",
                "道德經功夫（一）：不爭的功夫",
                "道德經功夫（二）：虛中的功夫",
                "道德經功夫（三）：樸實的功夫",
                "道德經功夫（四）：自然的功夫",
                "End",
            ],
        )

    def test_non_ordinal_singletons_still_cascade_merged(self):
        """Regression guard: the ordinal-sibling exception must not disable the
        original P4c behaviour for genuinely thin, unrelated 1-chunk stages."""
        stages = [
            _stage("Intro", ["chunk_0000", "chunk_0001"]),
            _stage("Solo A", ["chunk_0002"], kc=["x"]),
            _stage("Solo B", ["chunk_0003"], kc=["y"]),
            _stage("End", ["chunk_0004", "chunk_0005"]),
        ]
        out = merge_singleton_chunk_stages(stages)
        titles = [s["title"] for s in out]
        self.assertEqual(titles, ["Intro", "End"])
        self.assertIn("chunk_0002", out[0]["source_chunk_ids"])
        self.assertIn("chunk_0003", out[0]["source_chunk_ids"])

    def test_dissimilar_titles_without_ordinal_marker_not_merged(self):
        """Extend protection beyond explicit ordinal markers: singletons with
        clearly unrelated substantive titles (no shared lexical overlap) must
        also stay independent, even without a "（N）" marker. Calibrated so
        generic filler titles ("Intro"/"Solo"-style) still cascade-merge as
        before — see `_SINGLETON_MERGE_TITLE_SIM_FLOOR` calibration comment."""
        stages = [
            _stage("Intro", ["chunk_0000", "chunk_0001"]),
            _stage("蘋果的種植方法", ["chunk_0002"], kc=["嫁接", "施肥週期"]),
            _stage("銀行放款風控流程", ["chunk_0003"], kc=["信用評分"]),
            _stage("End", ["chunk_0004", "chunk_0005"]),
        ]
        out = merge_singleton_chunk_stages(stages)
        titles = [s["title"] for s in out]
        self.assertIn("蘋果的種植方法", titles, f"unrelated topic must stay separate: {titles}")
        self.assertIn("銀行放款風控流程", titles, f"unrelated topic must stay separate: {titles}")

    def test_merge_skipped_when_key_concepts_would_exceed_cap(self):
        """Safety net: even without an ordinal marker, don't silently cascade-merge
        singletons into a mega-stage that blows past STAGE_MAX_KEY_CONCEPTS."""
        from backend.utils.small_curriculum import STAGE_MAX_KEY_CONCEPTS

        prev_kc = [f"concept_{i}" for i in range(STAGE_MAX_KEY_CONCEPTS)]
        stages = [
            _stage("Intro", ["chunk_0000", "chunk_0001"]),
            _stage("Body", ["chunk_0002", "chunk_0003"], kc=prev_kc),
            _stage("Solo", ["chunk_0004"], kc=["one_more_concept"]),
            _stage("End", ["chunk_0005", "chunk_0006"]),
        ]
        out = merge_singleton_chunk_stages(stages)
        titles = [s["title"] for s in out]
        self.assertIn("Solo", titles, f"Solo should stay separate to avoid kc overflow: {titles}")
        body = next(s for s in out if s["title"] == "Body")
        self.assertEqual(len(body["key_concepts"]), STAGE_MAX_KEY_CONCEPTS)


class TestEnsureEmptyKeyConceptsFallback(unittest.TestCase):
    def test_colon_title_not_truncated_naively(self):
        """P4b: '借錢炒股：致富框架與心態' should NOT become '借錢炒股：致富框'."""
        stages = [_stage("借錢炒股：致富框架與心態", ["chunk_0000"])]
        out = ensure_empty_key_concepts(stages)
        kc = out[0]["key_concepts"]
        self.assertEqual(len(kc), 1)
        # _summary_kc_from_title splits on '：' -> '借錢炒股'
        self.assertEqual(kc[0], "借錢炒股")

    def test_plain_title_not_hard_truncated(self):
        """PR1 Mode 1: a separator-less title is kept whole, NOT bare title[:8].

        Hard-truncating to a fixed char count mangled mixed ASCII+CJK concepts
        (e.g. '提升 LLM 正確率...' -> '提升 LLM 正'), so the fallback now returns
        the complete phrase.
        """
        stages = [_stage("簡單標題僅有八字符這麼長", ["chunk_0000"])]
        out = ensure_empty_key_concepts(stages)
        self.assertEqual(out[0]["key_concepts"][0], "簡單標題僅有八字符這麼長")


if __name__ == "__main__":
    unittest.main()
