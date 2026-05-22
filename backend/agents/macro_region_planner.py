"""Macro region planner agent.

Tier (a)+(b) = programmatic plan_macro_regions (always).
Tier (c) = optional LLM metadata refinement (title / expected_stage_count /
must_cover_topics) when MACRO_REGION_USE_LLM=1 or payload.use_llm_refinement=True.
LLM does NOT re-split boundaries — only enriches per-region metadata using each
region's head/tail 300 chars (avoids lost-in-middle on full-book context).
"""
from __future__ import annotations

import json
import os
from typing import Any

from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole
from ..utils import extract_json
from ..utils.prompt_templates import SYSTEM_PROMPTS
from ..utils.region_planning import plan_macro_regions


class MacroRegionPlannerAgent(BaseAgent):
    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        self._reset()
        payload = ctx.task_payload
        source_chunks: list[dict] = payload.get("source_chunks") or []
        use_llm = bool(payload.get("use_llm_refinement")) or (
            os.getenv("MACRO_REGION_USE_LLM", "").strip() == "1"
        )
        t0 = self._log_start(ctx, chunks=len(source_chunks), use_llm=use_llm)

        regions = plan_macro_regions(source_chunks)

        refined = False
        if use_llm and regions and self.llm is not None:
            try:
                regions = await self._refine_with_llm(regions, source_chunks)
                refined = True
            except Exception as e:
                self._log.warning(
                    "macro region LLM refinement failed (using tier-1/2 regions): %s", e
                )

        result = {"regions": regions}
        self._log_end(
            ctx, t0,
            {"region_count": len(regions), "llm_refined": refined},
        )
        return result

    async def _refine_with_llm(
        self, regions: list[dict], source_chunks: list[dict]
    ) -> list[dict]:
        by_id = {c["chunk_id"]: c for c in source_chunks}
        snippets: list[dict] = []
        for r in regions:
            ids = r.get("chunk_ids") or []
            if not ids:
                continue
            head = (by_id.get(ids[0], {}).get("text") or "")[:300]
            tail = (by_id.get(ids[-1], {}).get("text") or "")[:300] if len(ids) > 1 else ""
            snippets.append({
                "region_id": r["region_id"],
                "current_title": r.get("title", ""),
                "chunk_count": len(ids),
                "head_300": head,
                "tail_300": tail,
            })

        if not snippets:
            return regions

        self._add_message(
            MessageRole.USER,
            json.dumps({"regions": snippets}, ensure_ascii=False),
        )
        response = await self.llm.chat(
            self._messages, system_prompt=SYSTEM_PROMPTS["macro_region_refiner"]
        )
        data = json.loads(extract_json(response.content))
        refinements = data.get("refinements") or []
        ref_by_id: dict[str, dict] = {
            r.get("region_id"): r for r in refinements if isinstance(r, dict) and r.get("region_id")
        }

        for r in regions:
            ref = ref_by_id.get(r["region_id"])
            if not ref:
                continue
            title = ref.get("title")
            if isinstance(title, str) and title.strip():
                r["title"] = title.strip()[:30]
            esc = ref.get("expected_stage_count")
            if esc is not None:
                try:
                    r["expected_stage_count"] = max(1, min(8, int(esc)))
                except (ValueError, TypeError):
                    pass
            topics = ref.get("must_cover_topics")
            if isinstance(topics, list):
                r["must_cover_topics"] = [
                    str(t).strip()[:20] for t in topics[:5] if str(t).strip()
                ]
        return regions
