"""Lightweight string similarity helpers (no embedding dependency)."""
from __future__ import annotations

import difflib


def similarity(a: str, b: str) -> float:
    a = (a or "").strip().lower()
    b = (b or "").strip().lower()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def concept_overlap_score(concepts_a: list[str], concepts_b: list[str]) -> float:
    if not concepts_a or not concepts_b:
        return 0.0
    scores: list[float] = []
    for ca in concepts_a:
        for cb in concepts_b:
            scores.append(similarity(str(ca), str(cb)))
    return max(scores) if scores else 0.0


def concepts_match(concepts_a: list[str], concepts_b: list[str], threshold: float = 0.85) -> bool:
    return concept_overlap_score(concepts_a, concepts_b) >= threshold


def _normalize_concept(c: str) -> str:
    return str(c or "").strip().lower()


def concept_jaccard(concepts_a: list[str], concepts_b: list[str]) -> float:
    """Set jaccard with case-insensitive exact match for cross-source stage merge.

    Used for P0b-1: when two stages from different sources share ≥ THRESHOLD
    of their key_concepts after lowercase+strip, they describe the same topic
    and should merge.
    """
    set_a = {_normalize_concept(c) for c in (concepts_a or []) if c}
    set_b = {_normalize_concept(c) for c in (concepts_b or []) if c}
    set_a.discard("")
    set_b.discard("")
    if not set_a or not set_b:
        return 0.0
    inter = set_a & set_b
    union = set_a | set_b
    return len(inter) / len(union)
