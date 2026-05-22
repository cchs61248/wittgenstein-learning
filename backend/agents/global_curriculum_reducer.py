"""Global curriculum reducer agent — Step B LLM for unsure pairs."""
from __future__ import annotations

import json
from typing import Any

from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS
from ..utils.curriculum_reducer import (
    build_outcome_from_group,
    parse_reducer_llm_output,
    rule_merge_candidates,
)


class GlobalCurriculumReducerAgent(BaseAgent):
    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        self._reset()
        payload = ctx.task_payload
        candidates: list[dict] = payload.get("candidate_stages") or []
        use_llm: bool = payload.get("use_llm", True)
        t0 = self._log_start(ctx, candidates=len(candidates))

        merged_groups, unsure_pairs = rule_merge_candidates(candidates)
        outcomes: list[dict] = []
        for gi, indices in enumerate(merged_groups):
            outcomes.append(
                build_outcome_from_group(candidates, indices, f"lo_{gi + 1:03d}")
            )

        if use_llm and unsure_pairs:
            subset = []
            seen: set[int] = set()
            for i, j in unsure_pairs[:20]:
                for idx in (i, j):
                    if idx not in seen:
                        seen.add(idx)
                        subset.append(candidates[idx])
            self._add_message(
                MessageRole.USER,
                json.dumps({"candidate_stages": subset, "unsure_pairs": unsure_pairs[:20]}, ensure_ascii=False),
            )
            try:
                response = await self.llm.chat(
                    self._messages, system_prompt=SYSTEM_PROMPTS["global_curriculum_reducer"]
                )
                llm_outcomes = parse_reducer_llm_output(response.content)
                for o in llm_outcomes:
                    if float(o.get("merge_confidence") or 0) >= 0.8:
                        outcomes.append(o)
            except Exception as e:
                self._log.warning("GlobalCurriculumReducer LLM failed: %s", e)

        result = {"outcomes": outcomes, "unsure_pair_count": len(unsure_pairs)}
        self._log_end(ctx, t0, {"outcome_count": len(outcomes), "unsure": len(unsure_pairs)})
        return result
