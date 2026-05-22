"""Global curriculum coverage verifier."""
from __future__ import annotations

from typing import Any


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
    missing_cases: list[str] = []
    outline = required_outline or {}
    for case in outline.get("named_cases") or []:
        case_str = str(case)
        if not any(case_str in (s.get("title") or "") for s in stages):
            missing_cases.append(case_str)
    aligned = not missing_cases and len(orphans) <= max(5, len(all_ids) // 20)
    return {
        "aligned": aligned,
        "missing_options": missing_cases,
        "orphan_chunk_ids": orphans[:20],
        "reason": "global coverage check",
    }
