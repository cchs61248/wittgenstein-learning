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
