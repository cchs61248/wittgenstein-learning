"""Apply concept-canonicalize mappings to stage key_concepts."""


def apply_canonical_mappings(
    stages: list[dict],
    mappings: list[dict],
) -> list[dict]:
    """Rewrite stages[].key_concepts from canonicalize agent mappings."""
    mapping_by_name: dict[str, str | None] = {}
    for m in mappings:
        new_name = m.get("new_name")
        if not new_name:
            continue
        if m.get("decision") == "mapped" and m.get("canonical"):
            mapping_by_name[new_name] = m["canonical"]

    result: list[dict] = []
    for stage in stages:
        new_stage = dict(stage)
        original_concepts = stage.get("key_concepts") or []
        new_stage["key_concepts"] = [
            mapping_by_name.get(c, c) for c in original_concepts
        ]
        result.append(new_stage)
    return result
