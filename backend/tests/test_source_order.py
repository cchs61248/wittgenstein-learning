"""Phase 2: SourceOrderResolver 單元測試。"""
import unittest

from backend.utils.source_order import (
    _cn_chapter_no,
    _detect_chapter_no,
    _detect_from_filename,
    _detect_from_heading,
    resolve_source_order,
)


def _info(label, text="內文", chapter_hint=None, index=0):
    return {"label": label, "text": text, "index": index,
            "chapter_hint": chapter_hint or {}}


class TestCnChapterNo(unittest.TestCase):
    def test_single_digits(self):
        self.assertEqual(_cn_chapter_no("一"), 1)
        self.assertEqual(_cn_chapter_no("九"), 9)

    def test_ten_and_teens(self):
        self.assertEqual(_cn_chapter_no("十"), 10)
        self.assertEqual(_cn_chapter_no("十一"), 11)
        self.assertEqual(_cn_chapter_no("十九"), 19)

    def test_twenty(self):
        self.assertEqual(_cn_chapter_no("二十"), 20)

    def test_out_of_range_is_none(self):
        self.assertIsNone(_cn_chapter_no("二十一"))
        self.assertIsNone(_cn_chapter_no("三十"))
        self.assertIsNone(_cn_chapter_no("一百"))
        self.assertIsNone(_cn_chapter_no(""))


class TestDetectFromFilename(unittest.TestCase):
    def test_cn_chapter(self):
        self.assertEqual(_detect_from_filename("第二章.pdf"), (2, "filename_regex"))

    def test_english_chapter(self):
        self.assertEqual(_detect_from_filename("chapter_2.pdf"), (2, "filename_regex"))
        self.assertEqual(_detect_from_filename("ch2.txt"), (2, "filename_regex"))

    def test_part(self):
        self.assertEqual(_detect_from_filename("Part 3 intro.md"), (3, "filename_regex"))

    def test_paren_number_beats_tail(self):
        self.assertEqual(_detect_from_filename("Consistent Hashing (1).pdf"), (1, "filename_regex"))

    def test_paren_wins_over_tail_number(self):
        self.assertEqual(_detect_from_filename("Hashing (1) v2.pdf"), (1, "filename_regex"))

    def test_tail_number(self):
        self.assertEqual(_detect_from_filename("報告2.pdf"), (2, "filename_regex"))

    def test_no_number(self):
        self.assertIsNone(_detect_from_filename("導論.pdf"))
        self.assertIsNone(_detect_from_filename(""))


class TestDetectFromHeading(unittest.TestCase):
    def test_cn_chapter_heading_in_first_500(self):
        self.assertEqual(_detect_from_heading("第三章 分散式系統\n內文..."), (3, "content_heading"))

    def test_section_heading(self):
        self.assertEqual(_detect_from_heading("第十二節 快取\n內文"), (12, "content_heading"))

    def test_no_heading(self):
        self.assertIsNone(_detect_from_heading("這是一段沒有章節標題的內文。"))


class TestDetectChapterNoCascade(unittest.TestCase):
    def test_epub_chapter_index_wins(self):
        info = {"label": "chapter_9.txt", "text": "第一章 開頭",
                "chapter_hint": {"chapter_index": 2}}
        self.assertEqual(_detect_chapter_no(info), (2, "epub_chapter_index"))

    def test_filename_when_no_epub(self):
        info = {"label": "chapter_5.pdf", "text": "無標題內文", "chapter_hint": {}}
        self.assertEqual(_detect_chapter_no(info), (5, "filename_regex"))

    def test_heading_when_no_filename(self):
        info = {"label": "untitled.pdf", "text": "第七章 內容", "chapter_hint": {}}
        self.assertEqual(_detect_chapter_no(info), (7, "content_heading"))

    def test_none_when_no_signal(self):
        info = {"label": "untitled.pdf", "text": "純內文沒有號", "chapter_hint": {}}
        self.assertEqual(_detect_chapter_no(info), (None, None))


class TestNoPrefixChapterMarkers(unittest.TestCase):
    """Live sess_j1ilhdohb：檔名與內文標題用「2章」「1章」（無「第」前綴）。

    「第」前綴在實務上常省略；偵測應同時支援「第二章」與「2章」/「二章」。
    """
    def test_filename_no_prefix_cn_unit(self):
        self.assertEqual(_detect_from_filename("005_2章 今井洋輝的燈塔.txt"), (2, "filename_regex"))
        self.assertEqual(_detect_from_filename("004_1章 井村直美的空想.txt"), (1, "filename_regex"))

    def test_heading_no_prefix(self):
        self.assertEqual(_detect_from_heading("2章　今井洋輝的燈塔\n五月的夜空"), (2, "content_heading"))

    def test_reorder_no_prefix_chapter_filenames(self):
        infos = [_info("005_2章 今井洋輝的燈塔.txt", index=0),
                 _info("004_1章 井村直美的空想.txt", index=1)]
        out, dec = resolve_source_order(infos)
        self.assertTrue(dec["applied"])
        self.assertTrue(dec["certain"])
        self.assertEqual(
            [i["label"] for i in out],
            ["004_1章 井村直美的空想.txt", "005_2章 今井洋輝的燈塔.txt"],
        )


class TestResolveSourceOrder(unittest.TestCase):
    def test_filename_reorders_upload_order(self):
        infos = [_info("第二章.pdf", index=0), _info("第一章.pdf", index=1)]
        out, dec = resolve_source_order(infos)
        self.assertEqual([i["label"] for i in out], ["第一章.pdf", "第二章.pdf"])
        self.assertTrue(dec["applied"])
        self.assertTrue(dec["certain"])
        self.assertEqual(dec["signal"], ["filename_regex"])
        self.assertEqual(dec["order"], ["第一章.pdf", "第二章.pdf"])

    def test_epub_chapter_index_reorders(self):
        infos = [_info("a.txt", chapter_hint={"chapter_index": 2}, index=0),
                 _info("b.txt", chapter_hint={"chapter_index": 1}, index=1)]
        out, dec = resolve_source_order(infos)
        self.assertEqual([i["label"] for i in out], ["b.txt", "a.txt"])
        self.assertEqual(dec["signal"], ["epub_chapter_index"])

    def test_mixed_signal_levels_still_certain(self):
        infos = [_info("x.txt", chapter_hint={"chapter_index": 1}, index=0),
                 _info("chapter_2.pdf", index=1)]
        out, dec = resolve_source_order(infos)
        self.assertTrue(dec["certain"])
        self.assertEqual(dec["signal"], ["epub_chapter_index", "filename_regex"])
        self.assertEqual([i["label"] for i in out], ["x.txt", "chapter_2.pdf"])

    def test_partial_missing_keeps_upload_order(self):
        infos = [_info("第一章.pdf", index=0), _info("導論.pdf", index=1)]
        out, dec = resolve_source_order(infos)
        self.assertEqual([i["label"] for i in out], ["第一章.pdf", "導論.pdf"])
        self.assertFalse(dec["applied"])
        self.assertFalse(dec["certain"])
        self.assertIsNone(dec["signal"])
        self.assertIn("missing", dec["reason"])

    def test_duplicate_numbers_keep_upload_order(self):
        infos = [_info("第一章.pdf", index=0), _info("第一章-v2.pdf", index=1)]
        out, dec = resolve_source_order(infos)
        self.assertEqual([i["label"] for i in out], ["第一章.pdf", "第一章-v2.pdf"])
        self.assertFalse(dec["applied"])
        self.assertIn("duplicate", dec["reason"])

    def test_no_signal_keeps_upload_order(self):
        infos = [_info("a.pdf", index=0), _info("b.pdf", index=1)]
        out, dec = resolve_source_order(infos)
        self.assertEqual([i["label"] for i in out], ["a.pdf", "b.pdf"])
        self.assertFalse(dec["applied"])


if __name__ == "__main__":
    unittest.main()
