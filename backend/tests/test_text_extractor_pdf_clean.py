"""PDF markdown postprocess — vertical watermark glyphs & inline noise."""
import unittest

from backend.utils.text_extractor import postprocess_pdf_markdown

_clean_pdf_text = postprocess_pdf_markdown


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

CH_INLINE_SAMPLE = """\
這個⽅法在節點數固定時很簡單m。
key 可能是.幾百萬、幾⼗億個
規則很簡單： l
1. 新節點加入時u會被 hash 到 ring 上
加一台機器i就會讓
l user_id
Consistent hashing 常被i講
cache ring t
不l均
hotu key
Redis Cluster r就是
16384 t
分散i式
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

    def test_fixes_cjk_inline_watermark_chars(self):
        out = _clean_pdf_text(CH_INLINE_SAMPLE)
        self.assertIn("很簡單。", out)
        self.assertNotIn("簡單m", out)
        self.assertIn("可能是幾百萬", out)
        self.assertNotIn("可能是.幾", out)
        self.assertIn("規則很簡單：", out)
        self.assertNotRegex(out, r"規則很簡單：\s*l")
        self.assertIn("加入時會被", out)
        self.assertNotIn("加入時u", out)
        self.assertIn("加一台機器就會", out)
        self.assertIn("user_id", out)
        self.assertNotRegex(out, r"(?m)^l user_id")
        self.assertIn("常被講", out)
        self.assertIn("cache ring", out)
        self.assertNotRegex(out, r"cache ring t\b")
        self.assertIn("不均", out)
        self.assertNotIn("不l均", out)
        self.assertIn("hot key", out)
        self.assertIn("Cluster 就是", out)
        self.assertIn("16384", out)
        self.assertNotRegex(out, r"16384 t\b")
        self.assertIn("分散式", out)

    def test_strips_markdown_image_only_lines(self):
        sample = "## Section\n\n![image 1](<foo_images/a.png>)\n\nBody text.\n"
        out = postprocess_pdf_markdown(sample)
        self.assertNotIn("![image", out)
        self.assertIn("## Section", out)
        self.assertIn("Body text.", out)

    def test_removes_watermark_domain_lines(self):
        sample = "Content\nmoat.org\nbuildmoat.org\nbiuld\nMore"
        out = postprocess_pdf_markdown(sample)
        self.assertNotRegex(out, r"(?m)^moat\.org")
        self.assertIn("Content", out)
        self.assertIn("More", out)


if __name__ == "__main__":
    unittest.main()
