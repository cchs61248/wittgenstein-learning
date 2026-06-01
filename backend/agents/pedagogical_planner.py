"""PedagogicalPlannerAgent — Phase 4 / T4b.

Asks an LLM to propose a *stage-move plan* (reorder existing stages only) and
parses it against the T4a ``PedagogicalPlan`` schema. The agent:

- builds a compact, stage-level planning payload (no chunk text, no coverage);
- requests a JSON plan via the ``pedagogical_planner`` system prompt;
- extracts + validates the JSON, returning warn-only diagnostics on failure.

Scope guard: this agent does NOT apply moves, wire into the pipeline, add a
feature flag, or change curriculum output. Applying / gating is T4c.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .base_agent import BaseAgent
from ..llm.base_provider import MessageRole
from ..llm.cache_context import llm_cache_context
from ..utils import extract_json
from ..utils.prompt_templates import SYSTEM_PROMPTS
from ..utils.pedagogical_planner import (
    StageCard,
    PrerequisiteGraph,
    OrderingPlan,
    PedagogicalPlan,
    parse_pedagogical_plan,
    _stage_identity,
    _as_str_tuple,
)


@dataclass(frozen=True)
class PedagogicalPlannerAgentResult:
    plan: PedagogicalPlan | None
    diagnostics: tuple[dict[str, Any], ...] = ()
    raw_response: str | None = None


def _build_planner_payload(
    stages: Sequence[Mapping[str, Any]],
    cards: Sequence[StageCard],
    graph: PrerequisiteGraph,
    ordering_plan: OrderingPlan,
) -> dict[str, Any]:
    """Compact, stage-level planning payload. No chunk text, no coverage claims."""
    stage_entries = [
        {
            "stage_id": _stage_identity(s, i),
            "stage_index": i,
            "title": str(s.get("title") or ""),
            "summary": str(s.get("summary") or ""),
            "key_concepts": list(_as_str_tuple(s.get("key_concepts"))),
            "source_ids": list(_as_str_tuple(s.get("source_ids"))),
            "source_stage_ids": list(_as_str_tuple(s.get("source_stage_ids"))),
        }
        for i, s in enumerate(stages)
    ]
    card_entries = [
        {
            "stage_id": c.stage_id,
            "role": c.role,
            "difficulty": c.difficulty,
            "role_reason": c.role_reason,
            "difficulty_reason": c.difficulty_reason,
        }
        for c in cards
    ]
    edge_entries = [
        {
            "before_stage_id": e.before_stage_id,
            "after_stage_id": e.after_stage_id,
            "reason": e.reason,
            "confidence": e.confidence,
        }
        for e in graph.edges
    ]
    ordering = {
        "current_stage_ids": list(ordering_plan.current_stage_ids),
        "recommended_stage_ids": list(ordering_plan.recommended_stage_ids),
        "order_changed": ordering_plan.order_changed,
        "diagnostics": [dict(d) for d in ordering_plan.diagnostics],
    }
    return {
        "stages": stage_entries,
        "stage_cards": card_entries,
        "prerequisite_edges": edge_entries,
        "ordering_plan": ordering,
    }


def _extract_json_object(text: Any) -> dict[str, Any] | None:
    """Return a JSON object from an LLM response, or None.

    Reuses the shared ``extract_json`` (fenced-block / first-object aware), then
    requires the decoded value to be a JSON object — lists / scalars / prose are
    rejected.
    """
    if not isinstance(text, str):
        return None
    try:
        data = json.loads(extract_json(text))
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


class PedagogicalPlannerAgent(BaseAgent):
    """Propose a stage-move plan (reorder only) from Phase 4 planning inputs."""

    async def propose_plan(
        self,
        *,
        stages: Sequence[Mapping[str, Any]],
        cards: Sequence[StageCard],
        graph: PrerequisiteGraph,
        ordering_plan: OrderingPlan,
    ) -> PedagogicalPlannerAgentResult:
        payload = _build_planner_payload(stages, cards, graph, ordering_plan)
        self._reset()
        self._add_message(
            MessageRole.USER,
            "planner_input=" + json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )
        try:
            with llm_cache_context(agent_name="PedagogicalPlannerAgent"):
                response = await self.llm.chat(
                    self._messages, system_prompt=SYSTEM_PROMPTS["pedagogical_planner"]
                )
        except Exception as exc:  # noqa: BLE001 — any LLM failure → safe fallback
            self._reset()
            return PedagogicalPlannerAgentResult(
                plan=None,
                diagnostics=(
                    {"type": "pedagogical_planner_llm_error", "reason": type(exc).__name__},
                ),
                raw_response=None,
            )
        self._reset()

        raw = response.content
        obj = _extract_json_object(raw)
        if obj is None:
            return PedagogicalPlannerAgentResult(
                plan=None,
                diagnostics=(
                    {"type": "pedagogical_planner_invalid_json",
                     "reason": "response_must_be_json_object"},
                ),
                raw_response=raw,
            )

        plan, parse_diags = parse_pedagogical_plan(obj)
        if plan is None:
            return PedagogicalPlannerAgentResult(
                plan=None, diagnostics=tuple(parse_diags), raw_response=raw
            )
        return PedagogicalPlannerAgentResult(plan=plan, diagnostics=(), raw_response=raw)
