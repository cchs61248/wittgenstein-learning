"""Curriculum pipeline LLM call counter and tier budget alerts."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .small_curriculum import is_small_file, source_count

_log = logging.getLogger("wl.orchestrator.v2.health")

TIER_LLM_BUDGET: dict[str, int] = {
    "small": 8,
    "small_multi": 20,
    "mid": 15,
    "large": 30,
}


def tier_llm_budget(source_chunks: list[dict]) -> int:
    """Tier base budget; mid/large full-V2 paths scale with chunk count (~3 calls/chunk)."""
    tier = curriculum_tier(source_chunks)
    base = TIER_LLM_BUDGET.get(tier, TIER_LLM_BUDGET["large"])
    n = len(source_chunks)
    if tier in ("mid", "large"):
        return max(base, n * 3)
    return base


def curriculum_tier(source_chunks: list[dict]) -> str:
    n = len(source_chunks)
    if is_small_file(source_chunks):
        if source_count(source_chunks) > 1:
            return "small_multi"
        return "small"
    if n <= 100:
        return "mid"
    return "large"


@dataclass
class CurriculumLlmMeter:
    breakdown: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.breakdown.values())

    def record(self, agent_name: str, count: int = 1) -> None:
        if count <= 0:
            return
        self.breakdown[agent_name] = self.breakdown.get(agent_name, 0) + count

    def to_quality_warnings(self, source_chunks: list[dict]) -> dict[str, Any]:
        tier = curriculum_tier(source_chunks)
        budget = tier_llm_budget(source_chunks)
        total = self.total
        return {
            "curriculum_llm_calls": total,
            "curriculum_llm_breakdown": dict(self.breakdown),
            "curriculum_tier": tier,
            "curriculum_llm_budget": budget,
            "curriculum_llm_over_budget": total > budget,
        }


def assess_curriculum_cost(
    *,
    session_id: str,
    meter: CurriculumLlmMeter,
    source_chunks: list[dict],
) -> dict[str, Any]:
    """Log WARNING when curriculum LLM calls exceed tier budget."""
    qw = meter.to_quality_warnings(source_chunks)
    if qw.get("curriculum_llm_over_budget"):
        _log.warning(
            "curriculum_cost_alert  session=%s  tier=%s  calls=%d  budget=%d  breakdown=%s",
            session_id,
            qw.get("curriculum_tier"),
            qw.get("curriculum_llm_calls"),
            qw.get("curriculum_llm_budget"),
            qw.get("curriculum_llm_breakdown"),
        )
    return qw
