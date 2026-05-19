"""把 LLM agent 自由生成的 concept 名稱對齊到 stage.key_concepts canonical 命名。

背景：ContentSplitter、QuestionGenerator、Evaluator、Teacher 各自會用不同字串
命名同一個概念，導致 concept_mastery 表跨 stage 碎片化、QG 個人化過濾失效。
解法：以 stage.key_concepts 為 source of truth，所有下游 agent 的 concept 輸出
都過這層 normalize 對齊。
"""
from __future__ import annotations


def normalize_concept(raw: str, canonical: list[str]) -> str | None:
    """把 raw concept 名稱對齊到 canonical 列表中最相近的元素。

    匹配規則（依序嘗試，先匹配先回傳）：
    1. 完全相符（去掉前後空白後）
    2. canonical 元素是 raw 的 substring（例「房貸」⊆「融資型房貸」→ 對到「融資型房貸」）
    3. raw 是 canonical 元素的 substring
    4. 都不匹配 → 回傳 None（呼叫端丟棄）

    若 canonical 為空，視為「沒有規範」，直接回傳 raw（穩健 fallback）。
    """
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None
    if not canonical:
        return raw  # 無規範，pass-through

    # Rule 1: 完全相符
    for c in canonical:
        if not isinstance(c, str):
            continue
        if c.strip() == raw:
            return c

    # Rule 2 / 3: substring 互含——並列時優先選最具體（最長）的 canonical
    best: tuple[int, str] | None = None  # (canonical length, canonical text)
    for c in canonical:
        if not isinstance(c, str):
            continue
        c_stripped = c.strip()
        if not c_stripped:
            continue
        if raw in c_stripped or c_stripped in raw:
            # 一律用 canonical 長度當 score，避免短 canonical 蓋過具體 canonical
            score = len(c_stripped)
            if best is None or score > best[0]:
                best = (score, c_stripped)
    if best is not None:
        return best[1]

    return None


def normalize_concepts(raw_list: list[str], canonical: list[str]) -> list[str]:
    """批次 normalize，過濾 None 與重複（保留順序）。"""
    if not raw_list:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_list:
        norm = normalize_concept(raw, canonical)
        if norm is not None and norm not in seen:
            result.append(norm)
            seen.add(norm)
    return result


def normalize_misconception_patterns(
    patterns: list[dict], canonical: list[str]
) -> list[dict]:
    """normalize misconception_patterns 列表中每筆的 concept 欄位。

    無法對齊（normalize_concept 回 None）的 pattern 仍保留，但 concept 設為 None
    以便上層判斷是否丟棄或合併（pattern 文字本身仍有診斷價值）。
    """
    if not patterns:
        return []
    out: list[dict] = []
    for p in patterns:
        if not isinstance(p, dict):
            continue
        raw_concept = p.get("concept", "")
        norm = normalize_concept(raw_concept, canonical) if raw_concept else None
        new_p = {**p, "concept": norm} if norm else {**p, "concept": None}
        out.append(new_p)
    return out
