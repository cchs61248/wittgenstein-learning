"""Macro region planner agent (optional LLM; defaults to programmatic planning)."""
from __future__ import annotations

from typing import Any

from .base_agent import BaseAgent, AgentContext
from ..utils.region_planning import plan_macro_regions


class MacroRegionPlannerAgent(BaseAgent):
    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        self._reset()
        payload = ctx.task_payload
        source_chunks: list[dict] = payload.get("source_chunks") or []
        t0 = self._log_start(ctx, chunks=len(source_chunks))
        regions = plan_macro_regions(source_chunks)
        result = {"regions": regions}
        self._log_end(ctx, t0, {"region_count": len(regions)})
        return result
