"""依 EPUB 目錄結構切分章節，回傳 [(章節標題, 純文字內容), ...]。

純函式模組：不寫檔、不處理 CLI、不負責檔名清洗（檔名清洗交給呼叫端）。
邏輯來源：aaron/epub/split_epub.py。
"""

from __future__ import annotations

import logging
import re
import tempfile
import warnings
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup, NavigableString, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
from ebooklib import epub, ITEM_DOCUMENT  # noqa: E402

_log = logging.getLogger("wl.ingest.epub_splitter")

BLOCK_TAGS = frozenset(
    {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "div"}
)


def normalize_href(href: str) -> str:
    return unquote(href.split("#")[0]).lstrip("/")


def parse_href(href: str) -> tuple[str, str | None]:
    """回傳 (檔案路徑, 錨點 id)；無錨點時 fragment 為 None。"""
    if "#" in href:
        path, frag = href.split("#", 1)
        return normalize_href(path), frag or None
    return normalize_href(href), None


def decode_content(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()

    lines: list[str] = []

    def walk(node) -> None:
        if isinstance(node, NavigableString):
            text = str(node).strip()
            if text:
                lines.append(text)
            return
        if not hasattr(node, "name") or node.name is None:
            return
        if node.name == "br":
            lines.append("")
            return
        if node.name in BLOCK_TAGS:
            block_lines: list[str] = []
            for child in node.children:
                if isinstance(child, NavigableString):
                    t = str(child).strip()
                    if t:
                        block_lines.append(t)
                elif getattr(child, "name", None) == "br":
                    block_lines.append("")
                else:
                    sub: list[str] = []

                    def collect(n) -> None:
                        if isinstance(n, NavigableString):
                            t = str(n).strip()
                            if t:
                                sub.append(t)
                        elif getattr(n, "name", None) == "br":
                            sub.append("")
                        elif hasattr(n, "children"):
                            for c in n.children:
                                collect(c)

                    collect(child)
                    block_lines.extend(sub)
            paragraph = "\n".join(block_lines).strip()
            if paragraph:
                lines.append(paragraph)
            return
        for child in node.children:
            walk(child)

    body = soup.body or soup
    walk(body)

    text = "\n\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _block_has_anchor(block, anchor_id: str) -> bool:
    return block.get("id") == anchor_id or block.find(id=anchor_id) is not None


def _body_blocks(soup: BeautifulSoup) -> list:
    body = soup.body or soup
    return [c for c in body.children if getattr(c, "name", None)]


def extract_html_fragment(
    html: str, start_id: str | None, end_id: str | None
) -> str:
    """依錨點 id 擷取 HTML 區段（同一檔案內的目錄子章）。"""
    if not start_id and not end_id:
        return html

    soup = BeautifulSoup(html, "lxml")
    blocks = _body_blocks(soup)
    if not blocks:
        return html

    start_idx = 0
    end_idx = len(blocks)

    if start_id:
        for i, block in enumerate(blocks):
            if _block_has_anchor(block, start_id):
                start_idx = i
                break

    if end_id:
        for i, block in enumerate(blocks):
            if _block_has_anchor(block, end_id):
                end_idx = i
                break

    if start_idx >= end_idx and end_id:
        end_idx = len(blocks)

    inner = "".join(str(block) for block in blocks[start_idx:end_idx])
    return f"<body>{inner}</body>"


def spine_documents(book: epub.EpubBook) -> list[epub.EpubItem]:
    docs: list[epub.EpubItem] = []
    for spine_id, _linear in book.spine:
        item = book.get_item_with_id(spine_id)
        if item is not None and item.get_type() == ITEM_DOCUMENT:
            docs.append(item)
    return docs


def find_spine_index(docs: list[epub.EpubItem], href: str) -> int | None:
    key = normalize_href(href)
    basename = Path(key).name
    for i, item in enumerate(docs):
        name = normalize_href(item.get_name() or "")
        if name == key or name.endswith(key) or Path(name).name == basename:
            return i
    return None


def collect_toc_boundaries(book: epub.EpubBook) -> list[tuple[str, str]]:
    """依目錄頂層項目建立切分點（大章節合併子節）。"""
    boundaries: list[tuple[str, str]] = []
    for entry in book.toc:
        if isinstance(entry, tuple):
            section, _children = entry
            title = getattr(section, "title", None) or "untitled"
            href = getattr(section, "href", None)
        else:
            title = getattr(entry, "title", None) or "untitled"
            href = getattr(entry, "href", None)
        if href:
            boundaries.append((title, href))
    return boundaries


# (spine 項目, 起始錨點, 結束錨點)；錨點為 None 表示檔案開頭或結尾
ChapterSegment = tuple[epub.EpubItem, str | None, str | None]


def group_by_toc(
    book: epub.EpubBook,
) -> list[tuple[str, list[ChapterSegment]]]:
    boundaries = collect_toc_boundaries(book)
    if not boundaries:
        docs = spine_documents(book)
        return [
            (Path(d.get_name() or "untitled").stem, [(d, None, None)]) for d in docs
        ]

    docs = spine_documents(book)
    groups: list[tuple[str, list[ChapterSegment]]] = []

    for i, (title, href) in enumerate(boundaries):
        start = find_spine_index(docs, href)
        if start is None:
            _log.warning("略過 TOC 項目：找不到「%s」(%s)", title, href)
            continue

        _, start_frag = parse_href(href)
        if i + 1 < len(boundaries):
            next_href = boundaries[i + 1][1]
            end = find_spine_index(docs, next_href)
            _, end_frag = parse_href(next_href)
            if end is None:
                end = start
        else:
            end = len(docs)
            end_frag = None

        segments: list[ChapterSegment] = []
        if end is None or end <= start:
            segments.append((docs[start], start_frag, end_frag))
        else:
            # 跨檔：等同 docs[start:end]，不含下一章所在檔
            segments.append((docs[start], start_frag, None))
            for j in range(start + 1, end):
                segments.append((docs[j], None, None))

        groups.append((title, segments))

    return groups


def segment_to_text(
    item: epub.EpubItem, start_frag: str | None, end_frag: str | None
) -> str:
    html = decode_content(item.get_content())
    if start_frag or end_frag:
        html = extract_html_fragment(html, start_frag, end_frag)
    return html_to_text(html)


def merge_segments_text(segments: list[ChapterSegment]) -> str:
    parts: list[str] = []
    for item, start_frag, end_frag in segments:
        text = segment_to_text(item, start_frag, end_frag)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def split_epub_by_toc(raw: bytes) -> list[tuple[str, str]]:
    """按 TOC 切 EPUB 章節，回傳 [(章節標題, 純文字內容), ...]。

    無 TOC → fallback 每個 spine document 一章；
    切出 0 章 → raise ValueError。
    """
    with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name
    try:
        try:
            book = epub.read_epub(tmp_path)
        except Exception as e:
            # ebooklib wraps zipfile.BadZipFile in its own EpubException; translate
            # any read-time failure to ValueError so the router maps it to 422
            # without needing to import ebooklib internals.
            raise ValueError(f"EPUB 解析失敗：{e}") from e
        groups = group_by_toc(book)
        if not groups:
            raise ValueError("EPUB 無可解析的章節")
        chapters: list[tuple[str, str]] = []
        for title, segments in groups:
            content = merge_segments_text(segments)
            if content.strip():
                chapters.append((title, content))
        if not chapters:
            raise ValueError("EPUB 章節內容皆為空")
        return chapters
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass
