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
    else:
        raw_chunks = _chunk_by_paragraphs(text)

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


def _has_wittgenstein_numbering(text: str) -> bool:
    matches = _WITTGENSTEIN_RE.findall(text)
    return len(matches) >= 3


def _has_markdown_headers(text: str) -> bool:
    matches = _MARKDOWN_HEADER_RE.findall(text)
    return len(matches) >= 2


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
    """從 chunk 第一行抽取標題（Markdown 標題或命題編號）。"""
    first_line = text.split("\n")[0].strip()
    if re.match(r"^#{1,4}\s+", first_line):
        return re.sub(r"^#{1,4}\s+", "", first_line).strip()
    if re.match(r"^\d+(\.\d+)+", first_line):
        return first_line[:80]
    return None


def _find_char_start(full_text: str, chunk_text: str) -> Optional[int]:
    """在原始全文中找到 chunk 的起始位置。"""
    # 用前 60 字元定位（避免重複段落的歧義）
    probe = chunk_text[:60]
    idx = full_text.find(probe)
    return idx if idx >= 0 else None
