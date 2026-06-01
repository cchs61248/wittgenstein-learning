"""Phase 4 — Cross-material pedagogical planner (T1: Stage Cards).

This module is intentionally **pure and deterministic**. It normalizes finalized
curriculum stages into immutable :class:`StageCard` objects with heuristic
``role`` / ``difficulty`` classification, plus warn-only diagnostics for weak or
missing metadata.

Scope guard (see ``docs/phase4_implementation_tickets.md`` T1):
- No LLM calls, no prompt, no external dependency.
- No stage reordering, no merge, no chunk reassignment, no DB migration.
- Never mutates the input stages; classification is fully deterministic.

T2 (prerequisite graph) and T3 (ordering scorer) build on these cards.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# Role classification
# ---------------------------------------------------------------------------
# Roles are matched by keyword against the stage's combined text
# (title + summary + key_concepts). Precedence is highest-first: summary and
# reference must be recognised before the graded content roles so positional
# stages are never mislabelled as a teaching step.
ROLE_PRECEDENCE = (
    "summary",
    "reference",
    "overview",
    "advanced",
    "application",
    "foundation",
)

ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "summary": ("綜合總結", "總結", "回顧", "結語", "summary", "review", "recap", "conclusion"),
    "reference": ("參考", "附錄", "術語", "名詞解釋", "reference", "appendix", "glossary"),
    "overview": ("概述", "概論", "導論", "簡介", "入門", "overview", "introduction", "intro"),
    "advanced": (
        "進階", "深入", "優化", "評估", "架構",
        "advanced", "deep dive", "optimization", "evaluation", "architecture",
    ),
    "application": (
        "應用", "實作", "案例", "範例", "部署",
        "workflow", "use case", "implementation", "example", "deployment",
    ),
    "foundation": (
        "基礎", "原理", "核心概念", "定義",
        "foundation", "basics", "principle", "concept", "definition",
    ),
}

# Default role when there IS text but no keyword matched.
DEFAULT_ROLE = "core"
# Role when the stage has no usable text signal at all.
UNKNOWN_ROLE = "unknown"

# ---------------------------------------------------------------------------
# Difficulty classification (integer 1..5; T3 scorer compares progression)
# ---------------------------------------------------------------------------
ROLE_BASE_DIFFICULTY: dict[str, int] = {
    "overview": 1,
    "reference": 1,
    "foundation": 2,
    "core": 3,
    "unknown": 3,
    "application": 4,
    "advanced": 5,
    "summary": 5,
}

# Roles whose difficulty is graded content (clamps may adjust). summary /
# reference / unknown keep their positional/default value.
GRADED_ROLES = frozenset({"overview", "foundation", "core", "application", "advanced"})

# Title-level difficulty modifiers. A high signal dominates a low signal.
DIFFICULTY_HIGH_KEYWORDS = ("進階", "advanced", "optimization", "evaluation")
DIFFICULTY_LOW_KEYWORDS = ("入門", "basics", "introduction")


@dataclass(frozen=True)
class StageCard:
    """Immutable normalized representation of a finalized curriculum stage."""

    stage_id: str
    stage_index: int
    source_ids: tuple[str, ...]
    source_stage_ids: tuple[str, ...]
    title: str
    summary: str
    key_concepts: tuple[str, ...]
    role: str
    difficulty: int
    role_reason: str
    difficulty_reason: str


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        s = value.strip()
        return (s,) if s else ()
    if not value:
        return ()
    out: list[str] = []
    for item in value:
        s = str(item).strip()
        if s:
            out.append(s)
    return tuple(out)


def _classify_role(title: str, summary: str, key_concepts: tuple[str, ...]) -> tuple[str, str]:
    text = " ".join([title, summary, " ".join(key_concepts)]).strip().lower()
    if not text:
        return UNKNOWN_ROLE, "no_text_signal"
    for role in ROLE_PRECEDENCE:
        for kw in ROLE_KEYWORDS[role]:
            if kw.lower() in text:
                return role, f"keyword:{kw}"
    return DEFAULT_ROLE, "default_core"


def _classify_difficulty(role: str, title: str) -> tuple[int, str]:
    base = ROLE_BASE_DIFFICULTY.get(role, 3)
    reason = f"base:{role}"
    if role not in GRADED_ROLES:
        return base, reason
    title_l = title.lower()
    if any(kw.lower() in title_l for kw in DIFFICULTY_HIGH_KEYWORDS):
        if base < 4:
            return 4, reason + ";clamp_high"
        return base, reason
    if any(kw.lower() in title_l for kw in DIFFICULTY_LOW_KEYWORDS):
        if base > 2:
            return 2, reason + ";clamp_low"
        return base, reason
    return base, reason


def build_stage_cards(
    stages: Sequence[Mapping[str, Any]],
) -> tuple[list[StageCard], list[dict[str, Any]]]:
    """Normalize finalized stages into stage cards + warn-only diagnostics.

    Returns ``(cards, diagnostics)``. Diagnostics are plain dicts (warn-only) and
    are *not* attached to ``quality_warnings`` here — wiring is deferred to T4.
    """
    cards: list[StageCard] = []
    diagnostics: list[dict[str, Any]] = []

    for index, stage in enumerate(stages):
        title = str(stage.get("title") or "").strip()
        summary = str(stage.get("summary") or "").strip()
        key_concepts = _as_str_tuple(stage.get("key_concepts"))
        role, role_reason = _classify_role(title, summary, key_concepts)
        difficulty, difficulty_reason = _classify_difficulty(role, title)

        raw_stage_id = stage.get("stage_id")
        stage_id = str(raw_stage_id).strip() if raw_stage_id is not None else f"stage_{index}"
        if not stage_id:
            stage_id = f"stage_{index}"

        cards.append(
            StageCard(
                stage_id=stage_id,
                stage_index=index,
                source_ids=_as_str_tuple(stage.get("source_ids")),
                source_stage_ids=_as_str_tuple(stage.get("source_stage_ids")),
                title=title,
                summary=summary,
                key_concepts=key_concepts,
                role=role,
                difficulty=difficulty,
                role_reason=role_reason,
                difficulty_reason=difficulty_reason,
            )
        )

        if not title:
            diagnostics.append(
                {"type": "missing_stage_title", "stage_index": index, "reason": "empty_title"}
            )
        if not key_concepts:
            diagnostics.append(
                {
                    "type": "missing_key_concepts",
                    "stage_index": index,
                    "stage_title": title,
                    "reason": "empty_key_concepts",
                }
            )

    return cards, diagnostics
