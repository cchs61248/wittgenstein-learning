"""ContentSplitter _sanitize_key_concepts unit tests.

對應 sess_live_bf8b1ff2「長期買進」epub 觀察到的 R10 title-leak 漏洞：
- stage 1 kc 第 1 項 = 「投資框架：理性經濟學與行為財務學」（= stage title）
- stage 38 同時有「Palm 與 3Com 定價錯誤」+「案例：Palm 與 3Com 的定價錯誤」
- stage 39 kc 含「投資實務：資產配置與自制力準則」（= stage 30 的 title）
"""
import unittest

from backend.agents.content_splitter import _sanitize_key_concepts


class TestSanitizeKeyConcepts(unittest.TestCase):
    def test_drops_kc_equal_to_stage_title(self):
        title = "投資框架：理性經濟學與行為財務學"
        kc = [title, "效用理論", "邊際效用遞減", "風險趨避"]
        out = _sanitize_key_concepts(kc, title)
        self.assertNotIn(title, out)
        self.assertEqual(out, ["效用理論", "邊際效用遞減", "風險趨避"])

    def test_drops_kc_with_chinese_separator(self):
        title = "案例分析：市場定價錯誤"
        kc = ["套利限制", "投資實務：資產配置與自制力準則", "掛牌市場情緒"]
        out = _sanitize_key_concepts(kc, title)
        # 含中文「：」的 title-like kc 被 drop
        self.assertNotIn("投資實務：資產配置與自制力準則", out)
        self.assertEqual(out, ["套利限制", "掛牌市場情緒"])

    def test_dedupes_case_prefix_variant(self):
        title = "案例分析：Palm 與 3Com"
        # 「案例：Palm 與 3Com 定價錯誤」與「Palm 與 3Com 定價錯誤」是同概念
        kc = ["Palm 與 3Com 定價錯誤", "案例：Palm 與 3Com 定價錯誤", "套利限制"]
        out = _sanitize_key_concepts(kc, title)
        # 第二個（前綴版）被 dedupe
        self.assertEqual(out, ["Palm 與 3Com 定價錯誤", "套利限制"])

    def test_allows_english_punct_kc(self):
        # 含英文連字號的固定術語（如「00631L 案例」「ETF-N 機制」）不應誤殺
        # 只 drop 含中文「：」或全形「—」的 title-like kc
        title = "槓桿型 ETF 風險分析"
        kc = ["00631L 案例", "每日目標報酬", "淨值侵蝕"]
        out = _sanitize_key_concepts(kc, title)
        self.assertEqual(out, ["00631L 案例", "每日目標報酬", "淨值侵蝕"])

    def test_filters_empty_and_non_string(self):
        title = "X"
        kc = ["valid", "", None, 42, "  ", "another"]
        out = _sanitize_key_concepts(kc, title)
        self.assertEqual(out, ["valid", "42", "another"])

    def test_preserves_order(self):
        title = "T"
        kc = ["a", "b", "c", "d"]
        out = _sanitize_key_concepts(kc, title)
        self.assertEqual(out, ["a", "b", "c", "d"])


if __name__ == "__main__":
    unittest.main()
