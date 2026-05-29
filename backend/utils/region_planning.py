"""Listicle (numbered 法則) source detection used by the small-file curriculum path."""
from __future__ import annotations

import re

_RULE_SECTION_RE = re.compile(r"^法則\s*\d+", re.IGNORECASE)


def _is_numbered_rule_title(title: str | None) -> bool:
    return bool(_RULE_SECTION_RE.match((title or "").strip()))


def _listicle_rule_ratio(chunks: list[dict]) -> float:
    if not chunks:
        return 0.0
    rule_count = sum(
        1 for c in chunks if _is_numbered_rule_title(c.get("section_title"))
    )
    return rule_count / len(chunks)


def is_listicle_source(source_chunks: list[dict]) -> bool:
    """True when ≥40% chunks carry 法則 N section titles (numbered-rule books)."""
    return _listicle_rule_ratio(source_chunks) >= 0.4


def count_listicle_rules(source_chunks: list[dict]) -> int:
    """Count chunks whose section_title is a numbered 法則 heading."""
    return sum(
        1 for c in source_chunks if _is_numbered_rule_title(c.get("section_title"))
    )
