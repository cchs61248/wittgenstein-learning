"""跨 stage 邊界清單（next_stage / forbidden_future）的單一來源。"""

FORBIDDEN_FUTURE_LIMIT = 10


def compute_stage_boundary_lists(
    stage: dict,
    stages: list[dict],
    *,
    limit: int = FORBIDDEN_FUTURE_LIMIT,
) -> tuple[list[str], list[str]]:
    """回傳 (next_stage_concepts, forbidden_future_concepts)。

    與 context_builder / DriftVerifier 共用，避免 Teacher 前 5、Drift 前 10 不一致。
    """
    key_concepts_here = set(stage.get("key_concepts") or [])
    current_idx = next(
        (i for i, s in enumerate(stages) if s.get("stage_id") == stage.get("stage_id")),
        -1,
    )
    next_stage_concepts: list[str] = []
    if 0 <= current_idx < len(stages) - 1:
        next_stage_concepts = [
            c for c in (stages[current_idx + 1].get("key_concepts") or [])
            if c not in key_concepts_here
        ]
    forbidden_future_concepts: list[str] = []
    if 0 <= current_idx < len(stages) - 2:
        next_stage_set = set(next_stage_concepts)
        forbidden_future_concepts = list(dict.fromkeys([
            c
            for s in stages[current_idx + 2:]
            for c in s.get("key_concepts", [])
            if c not in key_concepts_here and c not in next_stage_set
        ]))[:limit]
    return next_stage_concepts, forbidden_future_concepts
