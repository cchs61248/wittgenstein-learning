"""V2 curriculum pipeline health signals (monitoring; no auto Plan B switch)."""
from __future__ import annotations

import logging
from typing import Any

from .reducer_constants import OUTCOME_RATIO_WARN

_log = logging.getLogger("wl.orchestrator.v2.health")


def assess_reducer_health(
    *,
    session_id: str,
    candidate_count: int,
    outcome_count: int,
    stage_count: int,
    unsure_pair_count: int = 0,
    llm_outcome_count: int = 0,
    quality_warnings: dict | None = None,
    plan_b_active: bool = False,
) -> dict[str, Any]:
    """
    Emit structured health signals for ops monitoring.
    Does NOT auto-enable Plan B — operators review logs / quality_warnings.
    """
    signals: list[str] = []
    qw = quality_warnings or {}

    if qw.get("reducer_fallback_flat"):
        signals.append("reducer_fallback_flat")
    if qw.get("plan_b_active"):
        signals.append("plan_b_active")
    if qw.get("splitter_verifier_failed"):
        signals.append("splitter_verifier_failed")

    if candidate_count > 0 and outcome_count < candidate_count * OUTCOME_RATIO_WARN:
        signals.append("reducer_outcome_ratio_low")

    if candidate_count > 0 and stage_count == 0:
        signals.append("zero_stages")

    if unsure_pair_count > 0 and llm_outcome_count == 0 and not plan_b_active:
        signals.append("llm_reducer_no_accepted_outcomes")

    healthy = len(signals) == 0
    report: dict[str, Any] = {
        "healthy": healthy,
        "signals": signals,
        "metrics": {
            "candidate_count": candidate_count,
            "outcome_count": outcome_count,
            "stage_count": stage_count,
            "unsure_pair_count": unsure_pair_count,
            "llm_outcome_count": llm_outcome_count,
        },
        "plan_b_recommended": (
            "reducer_outcome_ratio_low" in signals
            or "reducer_fallback_flat" in signals
            or "llm_reducer_no_accepted_outcomes" in signals
        ),
    }

    if signals:
        _log.warning(
            "curriculum_health_alert  session=%s  signals=%s  metrics=%s  "
            "plan_b_recommended=%s  (manual: CURRICULUM_V2_PLAN_B=1)",
            session_id,
            signals,
            report["metrics"],
            report["plan_b_recommended"],
        )

    return report
