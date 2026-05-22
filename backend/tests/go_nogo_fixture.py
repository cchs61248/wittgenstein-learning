"""Shared Go/No-Go fixture loader and validation."""
from __future__ import annotations

import json
from pathlib import Path

from backend.utils.curriculum_reducer import rule_merge_candidates

FIXTURES = Path(__file__).parent / "fixtures"
LIVE_PAIRS_PATH = FIXTURES / "reducer_go_nogo_live_pairs.json"

REQUIRED_DRIFT_PATTERNS = {
    "literal_tweak",
    "word_order",
    "synonym",
    "added_words",
    "translation",
    "complementary_angle",
    "false_similar_title",
    "false_similar_topic",
    "different_goal",
}


def load_fixture() -> dict:
    return json.loads(LIVE_PAIRS_PATH.read_text(encoding="utf-8"))


def load_cases(baseline: str, kind: str) -> list[dict]:
    """kind: 'merge' | 'negative'"""
    data = load_fixture()
    section = data.get(baseline) or {}
    if isinstance(section, list):
        # legacy v1 flat list → treat as merge-only
        return section if kind == "merge" else []
    return section.get(kind) or []


def expected_pairs(case: dict, *, kind: str) -> list[tuple[int, int]]:
    if kind == "negative":
        raw = case.get("expected_split_pairs") or case.get("expected_merge_pairs") or [[0, 1]]
    else:
        raw = case.get("expected_merge_pairs") or [[0, 1]]
    return [tuple(p) for p in raw]


def validate_fixture() -> list[str]:
    """Return list of validation errors (empty = OK)."""
    errors: list[str] = []
    data = load_fixture()
    for baseline in ("same_source", "multi_source"):
        section = data.get(baseline)
        if not isinstance(section, dict):
            errors.append(f"{baseline}: expected {{merge, negative}} object")
            continue
        merge_cases = section.get("merge") or []
        neg_cases = section.get("negative") or []
        if len(merge_cases) < 5:
            errors.append(f"{baseline}.merge: need >=5 cases, got {len(merge_cases)}")
        if len(neg_cases) < 3:
            errors.append(f"{baseline}.negative: need >=3 cases, got {len(neg_cases)}")

        patterns_seen: set[str] = set()
        for kind, cases in (("merge", merge_cases), ("negative", neg_cases)):
            for case in cases:
                pid = case.get("id", "?")
                pattern = case.get("drift_pattern")
                if not pattern:
                    errors.append(f"{baseline}.{kind}.{pid}: missing drift_pattern")
                else:
                    patterns_seen.add(pattern)
                candidates = case.get("candidates") or []
                if len(candidates) < 2:
                    errors.append(f"{baseline}.{kind}.{pid}: need 2 candidates")
                    continue
                _, unsure = rule_merge_candidates(candidates)
                if kind == "merge" and not unsure:
                    errors.append(f"{baseline}.{kind}.{pid}: should hit unsure path")

        for required in ("literal_tweak", "synonym", "different_goal"):
            if required not in patterns_seen:
                errors.append(f"{baseline}: missing drift_pattern {required}")

    return errors
