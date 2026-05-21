"""Teacher inline INTENT 與 QG / extract_teaching_intent 的統一 schema。"""

from typing import Any


def normalize_teaching_intent(
    raw: dict[str, Any] | None,
    stage: dict | None = None,
) -> dict[str, Any]:
    """將 Teacher inline JSON 或 extract 輸出正規為 QG 使用的欄位。

    支援兩種來源：
    - inline INTENT：key_concepts / expected_misunderstandings / evidence_chunk_ids
    - extract：reinforced_concepts / analogies_used / repair_target / main_chunk_ids
    """
    stage = stage or {}
    key_concepts = [str(c) for c in (stage.get("key_concepts") or []) if c]
    if not raw:
        return {
            "reinforced_concepts": key_concepts[:2],
            "analogies_used": [],
            "repair_target": None,
            "main_chunk_ids": [],
        }

    reinforced = [
        str(c) for c in (
            raw.get("reinforced_concepts")
            or raw.get("key_concepts")
            or []
        )
        if c
    ]
    if not reinforced and key_concepts:
        reinforced = key_concepts[:2]

    analogies = [str(a) for a in (raw.get("analogies_used") or []) if a]

    repair = raw.get("repair_target")
    if repair is None and raw.get("expected_misunderstandings"):
        parts = [str(x) for x in raw["expected_misunderstandings"] if x]
        repair = "；".join(parts[:3]) if parts else None
    elif repair is not None:
        repair = str(repair).strip() or None

    main_chunks = [
        str(c) for c in (
            raw.get("main_chunk_ids")
            or raw.get("evidence_chunk_ids")
            or []
        )
        if c
    ]

    return {
        "reinforced_concepts": reinforced,
        "analogies_used": analogies,
        "repair_target": repair,
        "main_chunk_ids": main_chunks,
    }
