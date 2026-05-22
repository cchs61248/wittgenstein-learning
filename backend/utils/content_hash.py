"""Stable content_hash for source_chunks.

跨次上傳同檔案必須產生相同 hash — 否則 concept_mastery.source_signature
無法跨 session 累積，ConceptCanonicalize 的 historical_pool 永遠為空。

歷史 bug（驗收 sess_ybvdvp5uf 觀察）：同一本 epub 5 次 session
在 DB 出現 a109c47bdd9a613e + bc5eefa567e576ec 兩個 hash bucket，
因 (a) chunks 未排序、(b) 前 80 字易受切點與 whitespace 影響。
"""
from __future__ import annotations

import hashlib


def compute_content_hash(source_chunks: list[dict], prefix_chars: int = 200) -> str:
    """Return stable 16-hex hash from source_chunks.

    排序 by (source_id, order_index, chunk_id) 確保 chunks 順序穩定；
    每 chunk text 先 normalize whitespace（任意空白 → 單一空格），
    再取前 prefix_chars 字，用 \\n 分隔避免邊界黏接歧義。
    """
    sorted_chunks = sorted(
        source_chunks,
        key=lambda c: (
            str(c.get("source_id", "")),
            int(c.get("order_index", 0) or 0),
            str(c.get("chunk_id", "")),
        ),
    )
    parts: list[str] = []
    for c in sorted_chunks:
        text = " ".join(str(c.get("text", "")).split())
        parts.append(text[:prefix_chars])
    seed = "\n".join(parts)
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
