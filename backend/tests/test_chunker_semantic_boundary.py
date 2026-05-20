"""chunker semantic-aware boundary — Commit 2 regression。

行為驗證：
- 偵測「短行 + 無句尾標點 + 後接段落」的 inline heading 作為強制邊界
- chunk 不會把 inline heading 跟其後續段落分到不同 chunks
- 若教材無明顯標題（純散文），維持既有段落切分行為
- 不影響既有 Markdown / Wittgenstein 命題切分
"""
import unittest

from backend.utils.chunker import (
    build_source_chunks,
    _detect_inline_headings,
)


class TestDetectInlineHeadings(unittest.TestCase):
    def test_short_line_followed_by_paragraph_detected_as_heading(self):
        text = (
            "前一個概念的說明文字佔據兩三行，這是上一段的內容收尾。\n"
            "\n"
            "Consistent Hashing 的核心想法\n"
            "\n"
            "Consistent Hashing 不把 key 對節點數取模，而是把 key 和節點都放到同一個 hash space。\n"
        )
        offsets = _detect_inline_headings(text)
        # 應該抓到「Consistent Hashing 的核心想法」這行的起始 offset
        heading_line_start = text.index("Consistent Hashing 的核心想法")
        self.assertIn(heading_line_start, offsets)

    def test_long_line_with_period_not_a_heading(self):
        """普通段落（長、有句尾標點）不該誤判為 heading。"""
        text = (
            "這是一段比較長的段落，內含一個完整句子，不應該被當作 heading。\n"
            "\n"
            "後續段落內容。\n"
        )
        offsets = _detect_inline_headings(text)
        self.assertEqual(offsets, set())

    def test_heading_at_text_start_detected(self):
        text = (
            "雜湊環的核心想法\n"
            "\n"
            "把 key 和節點放到同一個 hash space 上，找順時針方向第一個節點當 owner。\n"
        )
        offsets = _detect_inline_headings(text)
        self.assertIn(0, offsets)


class TestChunkBoundaryRespectsHeading(unittest.TestCase):
    def test_long_text_split_avoids_breaking_at_heading(self):
        """長文本切分時，inline heading 與其後段落不能被分到不同 chunk。"""
        # 模擬一致性雜湊教材：前半 N=3/N=4 取模案例、後半 hash ring 規則
        # 兩段中間有「Consistent Hashing 的核心想法」這行 inline heading
        prefix = "傳統取模法的失效案例：\n"
        front = (
            "假設 N=3，9 個 key 分配到 Node 0/1/2。"
            "當擴容成 N=4 時，9 個值有 7 個換了位置。"
            "這是真實系統裡幾百萬個 key 的資料搬遷災難。"
            "重分配比例極高，會造成 cache miss 與流量尖峰。"
        ) * 3  # 拉長到 ~600 字
        heading = "\n\nConsistent Hashing 的核心想法\n\n"
        back = (
            "Consistent Hashing 不把 key 對節點數取模，而是把 key 和節點都放到同一個 hash space。"
            "規則一：每個節點 hash 到 ring 上的位置。"
            "規則二：key 也 hash 到 ring 上的位置。"
            "規則三：從 key 的位置順時針走，遇到的第一個節點就是 owner。"
        ) * 3  # 拉長到 ~600 字
        text = prefix + front + heading + back

        chunks = build_source_chunks(text)

        # 重點：找出包含「Consistent Hashing 的核心想法」這行的 chunk，
        # 該 chunk 應該也包含後續「規則一」「規則二」「規則三」（heading 不該被孤立到上一個 chunk）
        heading_chunks = [c for c in chunks if "Consistent Hashing 的核心想法" in c["text"]]
        self.assertEqual(
            len(heading_chunks), 1,
            f"heading 應在恰好一個 chunk，實際 = {len(heading_chunks)}"
        )
        heading_chunk = heading_chunks[0]
        self.assertIn(
            "規則一", heading_chunk["text"],
            "heading 與後續段落（規則一/二/三）應在同個 chunk，不該被切到不同 chunk"
        )

    def test_pure_paragraph_text_still_chunks_normally(self):
        """純散文（無 inline heading）仍走原段落切分，行為不變。"""
        text = "\n\n".join(["這是第一段普通內容，沒有任何標題。" * 5] * 3)
        chunks = build_source_chunks(text)
        # 至少有切出 chunk
        self.assertGreaterEqual(len(chunks), 1)
        for c in chunks:
            self.assertGreater(len(c["text"]), 0)


if __name__ == "__main__":
    unittest.main()
