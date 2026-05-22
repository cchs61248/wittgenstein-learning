"""Global curriculum reducer agent — Step B LLM for unsure pairs."""
from __future__ import annotations

import json
import os
from typing import Any

from .base_agent import BaseAgent, AgentContext
from ..llm.base_provider import MessageRole
from ..utils.prompt_templates import SYSTEM_PROMPTS
from ..utils.reducer_constants import MAX_UNSURE_PAIRS_LLM
from ..utils.curriculum_reducer import (
    build_step_a_outcomes,
    integrate_llm_outcomes,
    parse_reducer_llm_output,
    rule_merge_candidates,
    strip_internal_fields,
)


class GlobalCurriculumReducerError(RuntimeError):
    """Reducer failed under REDUCER_FAIL_MODE=hard."""


class GlobalCurriculumReducerAgent(BaseAgent):
    async def run(self, ctx: AgentContext) -> dict[str, Any]:
        self._reset()
        payload = ctx.task_payload
        candidates: list[dict] = payload.get("candidate_stages") or []
        use_llm: bool = payload.get("use_llm", True)
        fail_mode = os.getenv("REDUCER_FAIL_MODE", "hard").strip().lower()
        t0 = self._log_start(ctx, candidates=len(candidates))

        merged_groups, unsure_pairs = rule_merge_candidates(candidates)
        outcomes = build_step_a_outcomes(candidates, merged_groups)
        llm_outcomes: list[dict] = []

        if use_llm and unsure_pairs:
            subset: list[dict] = []
            seen: set[int] = set()
            for i, j in unsure_pairs[:MAX_UNSURE_PAIRS_LLM]:
                for idx in (i, j):
                    if idx not in seen:
                        seen.add(idx)
                        subset.append(candidates[idx])
            self._add_message(
                MessageRole.USER,
                json.dumps(
                    {
                        "candidate_stages": subset,
                        "unsure_pairs": unsure_pairs[:MAX_UNSURE_PAIRS_LLM],
                    },
                    ensure_ascii=False,
                ),
            )
            try:
                response = await self.llm.chat(
                    self._messages, system_prompt=SYSTEM_PROMPTS["global_curriculum_reducer"]
                )
                llm_outcomes = parse_reducer_llm_output(response.content)
            except Exception as e:
                self._log.warning("GlobalCurriculumReducer LLM failed: %s", e)
                if fail_mode == "hard":
                    raise GlobalCurriculumReducerError(
                        f"GlobalCurriculumReducer LLM failed: {e}"
                    ) from e

        outcomes = integrate_llm_outcomes(
            candidates,
            outcomes,
            llm_outcomes,
            unsure_pairs[:MAX_UNSURE_PAIRS_LLM],
        )
        if payload.get("keep_internal_fields"):
            final = outcomes
        else:
            final = strip_internal_fields(outcomes)

        result = {
            "outcomes": final,
            "unsure_pair_count": len(unsure_pairs),
            "llm_outcome_count": len(llm_outcomes),
        }
        self._log_end(ctx, t0, {"outcome_count": len(final), "unsure": len(unsure_pairs)})
        return result
