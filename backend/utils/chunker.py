"""
本地 evidence chunking 模組：將純文字切成可引用的 source_chunks。
目標：穩定、可定位、可引用——不要求教學完整性（那是 LLM 的工作）。

chunk_id 格式：chunk_NNNN（文件層級命名，不綁定 stage）
"""
import re
from typing import Optional


def build_source_chunks(text: str) -> list[dict]:
    """
    將文字切成 evidence chunks。
    優先策略：
      1. Wittgenstein 命題編號（如 1.1、1.2.1）
      2. Markdown 標題（## / ###）
      3. 段落切分 + 大小控制
    """
    text = text.strip()
    if not text:
        return []

    # 嘗試按結構切
    if _has_wittgenstein_numbering(text):
        raw_chunks = _chunk_by_proposition(text)
    elif _has_markdown_headers(text):
        raw_chunks = _chunk_by_headers(text)
    elif _has_numbered_rules(text):
        raw_chunks = _chunk_by_numbered_rules(text)
    else:
        raw_chunks = _chunk_by_paragraphs(text)
        # 純散文走 paragraph 切分時，把 inline heading（短行、無句尾標點）
        # 黏到後一段，避免標題與其段落被切到不同 chunk
        raw_chunks = _glue_inline_headings_to_next(raw_chunks)

    # 合併過短 chunk、切分過長 chunk
    raw_chunks = _normalize_chunk_sizes(raw_chunks, target=600, max_chars=1000)

    # 組裝最終格式
    chunks = []
    for i, chunk_text in enumerate(raw_chunks):
        chunk_text = chunk_text.strip()
        if not chunk_text:
            continue
        chunks.append({
            "chunk_id": f"chunk_{i:04d}",
            "text": chunk_text,
            "order_index": i,
            "section_title": _extract_section_title(chunk_text),
            "char_start": _find_char_start(text, chunk_text),
            "char_end": None,  # 由 char_start + len 推導
        })

    # 回填 char_end
    for c in chunks:
        if c["char_start"] is not None:
            c["char_end"] = c["char_start"] + len(c["text"])

    return chunks


# ── 結構偵測 ────────────────────────────────────────────────────

_WITTGENSTEIN_RE = re.compile(r"^\s*(\d+(\.\d+)+)\s", re.MULTILINE)
_MARKDOWN_HEADER_RE = re.compile(r"^#{1,4}\s+\S", re.MULTILINE)
# 法則 1　標題 / 法則1 + 下一行標題（epub 常見 listicle 結構）
_RULE_LINE_RE = re.compile(
    r"^\s*法則\s*(\d+)\s*(?:[　：:\s]\s*(.*))?\s*$",
    re.MULTILINE,
)
_RULE_STANDALONE_RE = re.compile(r"^\s*法則\s*(\d+)\s*$", re.MULTILINE)


def _has_wittgenstein_numbering(text: str) -> bool:
    matches = _WITTGENSTEIN_RE.findall(text)
    return len(matches) >= 3


def _has_markdown_headers(text: str) -> bool:
    matches = _MARKDOWN_HEADER_RE.findall(text)
    return len(matches) >= 2


def _has_numbered_rules(text: str) -> bool:
    """Detect 法則 N listicle structure (≥10 rule markers in body)."""
    numbers: set[str] = set()
    for m in _RULE_LINE_RE.finditer(text):
        numbers.add(m.group(1))
    for m in _RULE_STANDALONE_RE.finditer(text):
        numbers.add(m.group(1))
    return len(numbers) >= 10


def _rule_boundary_starts(text: str) -> list[int]:
    """Return char offsets where a numbered 法則 section begins (skip TOC-only lines)."""
    starts: set[int] = set()
    lines = text.splitlines(keepends=True)
    cursor = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if _RULE_STANDALONE_RE.match(stripped):
            starts.add(cursor)
        else:
            m_inline = _RULE_LINE_RE.match(stripped)
            if m_inline and m_inline.group(2):
                rest = "".join(lines[i + 1 : i + 4])
                if len(rest.strip()) > 120:
                    starts.add(cursor)
        cursor += len(line)
    return sorted(starts)


def _chunk_by_numbered_rules(text: str) -> list[str]:
    """Split on 法則 N boundaries; keep intro before first rule intact."""
    starts = _rule_boundary_starts(text)
    if not starts:
        return _chunk_by_paragraphs(text)
    segments: list[str] = []
    if starts[0] > 0:
        intro = text[: starts[0]].strip()
        if intro:
            segments.append(intro)
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        seg = text[start:end].strip()
        if seg:
            segments.append(seg)
    return segments if segments else [text]


# ── 切分策略 ────────────────────────────────────────────────────

def _chunk_by_proposition(text: str) -> list[str]:
    """按 Wittgenstein 命題編號（如 1.1、2.1.1）切分。"""
    parts = _WITTGENSTEIN_RE.split(text)
    # split 會包含 group capture，需清理
    chunks = []
    i = 0
    while i < len(parts):
        part = parts[i].strip()
        if part and not re.match(r"^\d+(\.\d+)*$", part):
            chunks.append(part)
        i += 1
    # 重新以命題邊界切
    boundaries = [m.start() for m in _WITTGENSTEIN_RE.finditer(text)]
    if not boundaries:
        return [text]
    segments = []
    for j, start in enumerate(boundaries):
        end = boundaries[j + 1] if j + 1 < len(boundaries) else len(text)
        segments.append(text[start:end].strip())
    return [s for s in segments if s]


def _chunk_by_headers(text: str) -> list[str]:
    """按 Markdown 標題切分。"""
    boundaries = [m.start() for m in _MARKDOWN_HEADER_RE.finditer(text)]
    if not boundaries:
        return [text]
    # 如果開頭沒有標題，把前面的文字也當一段
    segments = []
    if boundaries[0] > 0:
        intro = text[:boundaries[0]].strip()
        if intro:
            segments.append(intro)
    for j, start in enumerate(boundaries):
        end = boundaries[j + 1] if j + 1 < len(boundaries) else len(text)
        segments.append(text[start:end].strip())
    return [s for s in segments if s]


def _chunk_by_paragraphs(text: str) -> list[str]:
    """按段落（連續換行）切分。"""
    paragraphs = re.split(r"\n{2,}", text)
    return [p.strip() for p in paragraphs if p.strip()]


_HEADING_MAX_CHARS = 30  # inline heading 字數上限
_SENTENCE_END_RE = re.compile(r"[。！？.!?:：]\s*$")


def _looks_like_inline_heading(paragraph: str) -> bool:
    """判斷一個段落是否像 inline heading：短、無句尾標點、單行。"""
    stripped = paragraph.strip()
    if not stripped or "\n" in stripped:
        return False
    if len(stripped) > _HEADING_MAX_CHARS:
        return False
    return not _SENTENCE_END_RE.search(stripped)


def _glue_inline_headings_to_next(paragraphs: list[str]) -> list[str]:
    """把 inline heading（短、無句尾標點的單行段落）黏到後一段，
    確保 heading 與其後續段落留在同一個 chunk。

    若 heading 是最後一段（無後續），維持獨立段落。
    """
    if not paragraphs:
        return paragraphs
    result: list[str] = []
    i = 0
    while i < len(paragraphs):
        cur = paragraphs[i]
        if _looks_like_inline_heading(cur) and i + 1 < len(paragraphs):
            result.append(cur + "\n\n" + paragraphs[i + 1])
            i += 2
        else:
            result.append(cur)
            i += 1
    return result


def _detect_inline_headings(text: str) -> set[int]:
    """回傳 text 中所有 inline heading 的起始 char offset。

    判定條件：以 \\n\\n 隔開、短、無句尾標點、後續有段落內容。
    """
    paragraphs = re.split(r"\n{2,}", text)
    offsets: set[int] = set()
    cursor = 0
    for i, para in enumerate(paragraphs):
        # 找出該段落實際在 text 中的位置（跳過 leading whitespace）
        idx = text.find(para, cursor)
        if idx < 0:
            cursor += len(para)
            continue
        if _looks_like_inline_heading(para) and i + 1 < len(paragraphs):
            # 確認後續還有段落內容
            nxt = paragraphs[i + 1].strip()
            if nxt:
                offsets.add(idx)
        cursor = idx + len(para)
    return offsets


# ── 大小正規化 ────────────────────────────────────────────────

def _normalize_chunk_sizes(
    chunks: list[str],
    target: int = 600,
    max_chars: int = 1000,
    min_chars: int = 80,
) -> list[str]:
    """
    1. 合併過短的相鄰 chunk（< min_chars）到前一個 chunk
    2. 切分過長的 chunk（> max_chars）
    """
    # 步驟一：合併過短
    merged: list[str] = []
    for chunk in chunks:
        if merged and len(merged[-1]) < min_chars:
            merged[-1] = merged[-1] + "\n\n" + chunk
        elif len(chunk) < min_chars and merged:
            merged[-1] = merged[-1] + "\n\n" + chunk
        else:
            merged.append(chunk)

    # 步驟二：切分過長
    result: list[str] = []
    for chunk in merged:
        if len(chunk) <= max_chars:
            result.append(chunk)
        else:
            result.extend(_split_long_chunk(chunk, max_chars))
    return result


def _split_long_chunk(text: str, max_chars: int) -> list[str]:
    """在句子邊界切分過長的 chunk。"""
    sentences = re.split(r"(?<=[。！？.!?])\s*", text)
    parts: list[str] = []
    current = ""
    for sent in sentences:
        if len(current) + len(sent) > max_chars and current:
            parts.append(current.strip())
            current = sent
        else:
            current = current + sent if not current else current + " " + sent
    if current.strip():
        parts.append(current.strip())
    return parts if parts else [text]


# ── 工具函式 ─────────────────────────────────────────────────

def _extract_section_title(text: str) -> Optional[str]:
    """從 chunk 第一行抽取標題（Markdown 標題、命題編號、法則 N）。"""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return None
    first_line = lines[0]
    if re.match(r"^#{1,4}\s+", first_line):
        return re.sub(r"^#{1,4}\s+", "", first_line).strip()
    if re.match(r"^\d+(\.\d+)+", first_line):
        return first_line[:80]
    m_inline = _RULE_LINE_RE.match(first_line)
    if m_inline and m_inline.group(2):
        return f"法則 {m_inline.group(1)}：{m_inline.group(2).strip()}"
    m_standalone = _RULE_STANDALONE_RE.match(first_line)
    if m_standalone and len(lines) >= 2:
        subtitle = lines[1]
        if len(subtitle) <= 40 and not _SENTENCE_END_RE.search(subtitle):
            return f"法則 {m_standalone.group(1)}：{subtitle}"
        return f"法則 {m_standalone.group(1)}"
    return None


def _find_char_start(full_text: str, chunk_text: str) -> Optional[int]:
    """在原始全文中找到 chunk 的起始位置。"""
    # 用前 60 字元定位（避免重複段落的歧義）
    probe = chunk_text[:60]
    idx = full_text.find(probe)
    return idx if idx >= 0 else None
