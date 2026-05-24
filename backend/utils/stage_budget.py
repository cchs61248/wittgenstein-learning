"""Dynamic max_stages budget from chunk count and outline metadata."""
from __future__ import annotations

import math
from typing import Any

from .region_planning import count_listicle_rules, is_listicle_source


def compute_dynamic_max_stages(
    source_chunks: list[dict],
    source_count: int = 1,
    required_outline: dict[str, Any] | None = None,
) -> int:
    """Compute stage budget; named_cases drive parallel-option headroom."""
    _ = source_count  # reserved for future multi-source scaling
    chunk_based = math.ceil(len(source_chunks) / 4.5) if source_chunks else 30
    outline = required_outline or {}
    titles = outline.get("required_stage_titles") or []
    named_cases = outline.get("named_cases") or []
    outline_demand = len(titles) + len(named_cases) + 5
    case_budget = len(named_cases) * 4
    budget = max(30, chunk_based, outline_demand, case_budget)
    if is_listicle_source(source_chunks):
        rule_count = count_listicle_rules(source_chunks)
        if rule_count >= 10:
            budget = max(budget, min(rule_count, 50))
    return budget
