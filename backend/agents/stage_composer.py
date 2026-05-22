"""Compose unified stage path from learning outcomes."""
from __future__ import annotations

from typing import Any

from ..utils.fuzzy_match import concept_overlap_score
from ..utils.curriculum_reducer import outcomes_to_stages


class StageComposerAgent:
    """Order outcomes and infer prerequisites from key_concepts overlap."""

    def compose(self, outcomes: list[dict]) -> list[dict]:
        if not outcomes:
            return []
        ordered = list(outcomes)
        stages = outcomes_to_stages(ordered)
        id_by_outcome = {o["outcome_id"]: i for i, o in enumerate(ordered)}
        for i, stage in enumerate(stages):
            prereq_ids: list[str] = []
            concepts = [str(c) for c in stage.get("key_concepts") or []]
            for j in range(i):
                prev = ordered[j]
                prev_concepts = [str(c) for c in prev.get("key_concepts") or []]
                if concept_overlap_score(concepts, prev_concepts) >= 0.5:
                    prereq_ids.append(prev["outcome_id"])
            stage["prerequisites"] = prereq_ids
            stage["stage_id"] = i + 1
            chapter = (i // 3) + 1
            section = (i % 3) + 1
            stage["node_id"] = f"{chapter}.{section}"
        return stages
