"""第X節 / 第X章 Chinese epub chunking."""
import unittest
from pathlib import Path

from backend.utils.chunker import (
    _cn_boundary_starts,
    _cn_section_line_starts,
    _has_cn_sections,
    build_source_chunks,
)


CN_PARENTING_SAMPLE = (
    "# 親子英語，玩出來\n\n"
    "目錄\n"
    "第一節 學習第二外語的幾個準則：和女兒共同學習西班牙語的心得\n"
    "第二節 英語啟蒙如何聽：對話倫敦雅思考試官\n\n"
    "第一節 學習第二外語的幾個準則：和女兒共同學習西班牙語的心得\n"
    + ("我很喜歡學習語言，在考取教師資格證前，我申請的博士學位就是語言研究學。" * 15)
    + "\n\n"
    "第二節 英語啟蒙如何聽：對話倫敦雅思考試官\n"
    + ("讓孩子從小多聽原版磁帶和音頻，必須要作為一個主要的學習方式。" * 15)
    + "\n\n"
    "第三節 如何讓孩子開口說英語：英國老師如何幫助EAL的孩子學習英語\n"
    + ("「說」在英語學習基本步驟「聽、說、讀、寫」中排行第二，我們學習英語，在很大程度上要與人溝通交流。" * 15)
    + "\n\n"
    "第四節 大聲朗讀對孩子很重要：從今天開始為孩子大聲朗讀\n"
    + ("「讀」作為英語學習基本步驟之一，近年來得到越來越多家長的重視。" * 15)
    + "\n\n"
    "第五節 不容忽視的幼兒寫作培養\n"
    + ("「寫」是英語學習的最後一步，但絕不是最不重要的一步。" * 15)
    + "\n\n"
    "第一節 大自然的呼喚——和孩子出去走走也能學英語\n"
    + ("大自然是最好的教室，帶著孩子到戶外，聽鳥叫、風聲、水流聲。" * 15)
    + "\n\n"
    "第二節 不要說它們吵，我們就是喜歡——喧鬧的樂器\n"
    + ("讓孩子用身體和簡單的樂器感受節奏，是培養聽音辨音能力的好方法。" * 15)
    + "\n\n"
    "第三節 身體發音的奧秘——讓我們一起拍拍手，頓頓足\n"
    + ("身體動作能幫助孩子記憶音素，拍手、跺腳都是有效的輔助。" * 15)
    + "\n\n"
    "第四節 從小培養「小詩人」——韻律和押韻\n"
    + ("韻律和押韻是英語啟蒙的重要環節，童謠是最好的載體。" * 15)
)


class TestCnSectionChunker(unittest.TestCase):
    def test_detects_cn_sections(self):
        self.assertTrue(_has_cn_sections(CN_PARENTING_SAMPLE))

    def test_skips_toc_only_headings(self):
        hits = _cn_section_line_starts(CN_PARENTING_SAMPLE)
        starts = _cn_boundary_starts(hits, CN_PARENTING_SAMPLE)
        # TOC has 2 stub sections; body has 9 → expect 9 valid boundaries
        self.assertGreaterEqual(len(starts), 8)

    def test_extracts_section_titles(self):
        chunks = build_source_chunks(CN_PARENTING_SAMPLE)
        titles = [c.get("section_title") or "" for c in chunks]
        joined = " ".join(titles)
        self.assertIn("西班牙語", joined)
        self.assertIn("朗讀", joined)

    def test_live_qinzi_epub_if_present(self):
        epub = Path(r"C:\Users\dqaiot\Downloads\apk.tw_親子英語，玩出來.epub")
        if not epub.exists():
            self.skipTest("親子英語 epub not on disk")
        from backend.utils.text_extractor import extract_text

        text = extract_text(epub.name, epub.read_bytes())
        chunks = build_source_chunks(text)
        titled = sum(1 for c in chunks if (c.get("section_title") or "").strip())
        toc_like = sum(
            1 for c in chunks
            if "目錄" in (c.get("text") or "")[:800]
            and sum(1 for ln in (c.get("text") or "").splitlines()
                    if ln.strip().startswith("第") and "節" in ln) >= 8
        )
        self.assertEqual(toc_like, 0, f"TOC chunks should be stripped, got {toc_like}")
        self.assertGreater(titled, 20, f"titled={titled} total={len(chunks)}")
        self.assertGreaterEqual(len(chunks), 30)


if __name__ == "__main__":
    unittest.main()
