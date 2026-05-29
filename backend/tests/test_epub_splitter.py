from pathlib import Path

import pytest

from backend.utils.epub_splitter import split_epub_by_toc


FIXTURE = Path(__file__).parent / "fixtures" / "sample_toc.epub"


def test_returns_chapter_list():
    raw = FIXTURE.read_bytes()
    chapters = split_epub_by_toc(raw)
    assert len(chapters) == 3
    titles = [t for t, _ in chapters]
    assert titles == ["Chapter 1", "Chapter 2", "Chapter 3"]


def test_chapter_text_nonempty():
    raw = FIXTURE.read_bytes()
    chapters = split_epub_by_toc(raw)
    for title, text in chapters:
        assert title in text or text.strip()


def test_empty_bytes_raises():
    with pytest.raises(Exception):
        split_epub_by_toc(b"")


def test_no_toc_falls_back_to_spine(tmp_path):
    """When EPUB has no TOC, fall back to one chapter per spine document."""
    from ebooklib import epub as ebooklib_epub
    book = ebooklib_epub.EpubBook()
    book.set_identifier("notoc-test")
    book.set_title("No TOC Book")
    book.set_language("zh")

    spine_items = []
    for i in range(2):
        item = ebooklib_epub.EpubHtml(
            title=f"Spine {i+1}", file_name=f"s{i+1}.xhtml", lang="zh"
        )
        item.content = f"<h1>Spine {i+1}</h1><p>body {i+1}</p>"
        book.add_item(item)
        spine_items.append(item)

    book.toc = []  # explicitly no TOC
    book.spine = spine_items  # no nav
    book.add_item(ebooklib_epub.EpubNcx())
    book.add_item(ebooklib_epub.EpubNav())

    epub_path = tmp_path / "notoc.epub"
    ebooklib_epub.write_epub(str(epub_path), book)

    raw = epub_path.read_bytes()
    chapters = split_epub_by_toc(raw)

    # fallback: each spine doc becomes a chapter (title from filename stem)
    assert len(chapters) == 2
    for _, text in chapters:
        assert text.strip()
