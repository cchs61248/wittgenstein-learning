"""Global curriculum coverage verifier."""
from __future__ import annotations

from typing import Any

from ..utils.fuzzy_match import similarity
from ..utils.small_curriculum import (
    filter_missing_named_cases,
    is_small_file,
)


def _duplicate_titles(stages: list[dict], threshold: float = 0.92) -> list[str]:
    dupes: list[str] = []
    titles = [(s.get("title") or "").strip() for s in stages]
    for i in range(len(titles)):
        if not titles[i]:
            continue
        for j in range(i + 1, len(titles)):
            if titles[j] and similarity(titles[i], titles[j]) >= threshold:
                dupes.append(f"{titles[i]} ~ {titles[j]}")
    return dupes


def verify_global_coverage(
    stages: list[dict],
    source_chunks: list[dict],
    required_outline: dict | None = None,
) -> dict[str, Any]:
    referenced: set[str] = set()
    for s in stages:
        for cid in s.get("source_chunk_ids") or []:
            referenced.add(cid)
    all_ids = {c["chunk_id"] for c in source_chunks}
    orphans = sorted(all_ids - referenced)
    outline = required_outline or {}
    named_cases = [str(c) for c in outline.get("named_cases") or []]
    missing_cases = filter_missing_named_cases(named_cases, stages, source_chunks)
    duplicate_titles = _duplicate_titles(stages)
    orphan_limit = 0 if is_small_file(source_chunks) else max(5, len(all_ids) // 20)
    aligned = (
        not missing_cases
        and not duplicate_titles
        and len(orphans) <= orphan_limit
    )
    issues: list[str] = []
    if missing_cases:
        issues.append(f"missing named_cases: {missing_cases}")
    if duplicate_titles:
        issues.append(f"duplicate titles: {duplicate_titles[:5]}")
    if len(orphans) > orphan_limit:
        issues.append(f"orphan chunks: {len(orphans)} > {orphan_limit}")
    return {
        "aligned": aligned,
        "missing_options": missing_cases,
        "duplicate_titles": duplicate_titles,
        "orphan_chunk_ids": orphans[:20],
        "reason": "; ".join(issues) or "global coverage check",
    }
