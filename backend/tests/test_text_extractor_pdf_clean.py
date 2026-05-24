"""PDF text extraction cleanup — vertical watermark glyphs & inline noise."""
import unittest

from backend.utils.text_extractor import _clean_pdf_text


# 取自 Consistent Hashing.pdf 實際 chunk 噪音樣本
CH_SAMPLE = """\
N = 3
， ，
3 6 9 -> Node 0
g
r
o
.
t
a
o
9 個值裡有 7 個換了位置。真實系統裡 key 可能是幾百萬、幾十億個
owner = hash(key) % number_of_nodes
hash(ukey) % N
這個 hashd space 通常會被想像成 hash ring
將 key 也 hbash 到 ring 上
Consistent hashinog 可以讓擴容只影響一部分 key
naive modurlo 就好
"""


class TestCleanPdfText(unittest.TestCase):
    def test_removes_vertical_watermark_glyphs(self):
        out = _clean_pdf_text(CH_SAMPLE)
        self.assertNotIn("\ng\n", out)
        self.assertNotIn("\nr\n", out)
        self.assertNotRegex(out, r"(?m)^g$")
        self.assertIn("9 個值裡有 7 個換了位置", out)

    def test_preserves_formula_lines(self):
        out = _clean_pdf_text("N = 3\n\nowner = hash(key) % 3\n\nN = 4")
        self.assertIn("N = 3", out)
        self.assertIn("owner = hash(key) % 3", out)
        self.assertIn("N = 4", out)

    def test_fixes_inline_watermark_corruption(self):
        out = _clean_pdf_text(CH_SAMPLE)
        self.assertIn("hash(key) % N", out)
        self.assertNotIn("hash(ukey)", out)
        self.assertIn("hash space", out)
        self.assertNotIn("hashd space", out)
        self.assertIn("hash 到 ring", out)
        self.assertNotIn("hbash", out)
        self.assertIn("Consistent hashing", out)
        self.assertIn("naive modulo", out)

    def test_preserves_uppercase_single_letter_lines(self):
        out = _clean_pdf_text("Some text\nN\nMore text")
        self.assertIn("\nN\n", out)


if __name__ == "__main__":
    unittest.main()
