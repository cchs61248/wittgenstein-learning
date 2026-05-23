"""Numbered 法則 listicle chunking and region planning."""
import unittest

from backend.utils.chunker import build_source_chunks
from backend.utils.region_planning import plan_macro_regions


RULE_BOOK_SAMPLE = (
    "# 穩住投資心態\n\n"
    "目次\n法則 1　朋友比金錢可貴\n法則 2　投資他人\n\n"
    "前言\n這是一本投資心態書。\n\n"
    "法則1\n朋友比金錢可貴\n"
    "龍捲風掃過後，朋友比金錢更可贵。" + "正文補充。" * 40 + "\n\n"
    "法則2\n投資他人\n"
    "投資他人就是投資關係。" + "更多內容。" * 40 + "\n\n"
    "法則3\n知道合作夥伴\n"
    "合作夥伴至關重要。" + "延伸說明。" * 40
)


class TestNumberedRuleChunker(unittest.TestCase):
    def test_splits_on_standalone_rule_markers(self):
        chunks = build_source_chunks(RULE_BOOK_SAMPLE)
        # 樣本僅 3 則，不足 10 則門檻；直接驗證 rule 切分函式
        from backend.utils.chunker import _chunk_by_numbered_rules, _extract_section_title
        segments = _chunk_by_numbered_rules(RULE_BOOK_SAMPLE)
        titles = [_extract_section_title(s) or "" for s in segments]
        self.assertTrue(any("法則 1" in t for t in titles))
        self.assertTrue(any("法則 2" in t for t in titles))
        self.assertIn("龍捲風", RULE_BOOK_SAMPLE)
        self.assertGreaterEqual(len(chunks), 1)

    def test_toc_only_rule_lines_not_split(self):
        chunks = build_source_chunks(RULE_BOOK_SAMPLE)
        toc_only = [c for c in chunks if c["text"].strip() == "法則 1　朋友比金錢可貴"]
        self.assertEqual(toc_only, [])


class TestListicleRegionPlanning(unittest.TestCase):
    def test_rule_chunks_use_window_regions_not_one_per_rule(self):
        text = RULE_BOOK_SAMPLE
        for n in range(4, 12):
            text += f"\n\n法則{n}\n標題{n}\n" + ("內容。" * 50) + "\n"
        chunks = build_source_chunks(text)
        for i, c in enumerate(chunks):
            c["chunk_id"] = f"chunk_{i:04d}"
        regions = plan_macro_regions(chunks, chunks_per_region=3)
        self.assertLess(len(regions), len(chunks))


if __name__ == "__main__":
    unittest.main()
