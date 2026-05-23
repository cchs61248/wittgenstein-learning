"""Part N / 第N堂 epub chunking."""
import unittest
from pathlib import Path

from backend.utils.chunker import (
    _chunk_by_part_sections,
    _extract_section_title,
    _has_part_sections,
    build_source_chunks,
)


PART_BOOK_SAMPLE = (
    "# 長期買進\n\n"
    "推薦序\n兼具理論及實務的投資書。\n\n"
    "Part 1 重新認識經濟學裡的有限理性\n"
    "第1堂 高報酬必然伴隨高風險\n"
    + ("風險與報酬的關係說明。" * 80)
    + "\n\n"
    "第2堂 人不如自己想像中的理性\n"
    + ("沉沒成本與機會成本。" * 80)
    + "\n\n"
    "Part 2 進場前一定要知道的投資觀念\n"
    "第1堂 效率市場假說不足以解釋股價變化\n"
    + ("效率市場與異象。" * 80)
    + "\n\n"
    "Part 3 影響投資成果的10個行為偏誤\n"
    "第1堂 本質主義：價格由投資人認定的價值而定\n"
    + ("本質主義行銷案例。" * 80)
)


class TestPartSectionChunker(unittest.TestCase):
    def test_detects_part_sections(self):
        self.assertTrue(_has_part_sections(PART_BOOK_SAMPLE))

    def test_splits_on_part_boundaries(self):
        segments = _chunk_by_part_sections(PART_BOOK_SAMPLE)
        self.assertGreaterEqual(len(segments), 4)
        titles = [_extract_section_title(s) or "" for s in segments]
        self.assertTrue(any("Part 1" in t for t in titles))
        self.assertTrue(any("Part 2" in t for t in titles))

    def test_build_source_chunks_assigns_section_titles(self):
        chunks = build_source_chunks(PART_BOOK_SAMPLE)
        titled = [c for c in chunks if (c.get("section_title") or "").strip()]
        self.assertGreaterEqual(len(titled), 3)
        titles = " ".join(c.get("section_title") or "" for c in chunks)
        self.assertTrue("Part 1" in titles or "第1堂" in titles)

    def test_live_changqimaijin_epub_if_present(self):
        epub = Path(r"C:\Users\dqaiot\Documents\aaron\epub\book\長期買進.epub")
        if not epub.exists():
            self.skipTest("長期買進.epub not on disk")
        from backend.utils.text_extractor import extract_text

        text = extract_text(epub.name, epub.read_bytes())
        chunks = build_source_chunks(text)
        titled = sum(1 for c in chunks if (c.get("section_title") or "").strip())
        lesson_titles = [
            c.get("section_title") for c in chunks
            if c.get("section_title") and "堂" in c["section_title"]
        ]
        self.assertGreater(titled, 20, f"titled={titled} total={len(chunks)}")
        self.assertGreaterEqual(len(lesson_titles), 15)


if __name__ == "__main__":
    unittest.main()
