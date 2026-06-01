"""Phase 4 — Cross-material pedagogical planner utilities.

This module is intentionally **pure and deterministic**. It currently contains:

- T1: StageCard normalization with heuristic role / difficulty classification.
- T2: prerequisite graph construction with sparse high-confidence edges,
  cycle detection, and unrelated cluster diagnostics.

Scope guard (see ``docs/phase4_implementation_tickets.md``):
- No LLM calls, no prompt, no external dependency.
- No stage reordering, no merge, no chunk reassignment, no DB migration.
- Never mutates input stages or cards; output is fully deterministic.

T3 (ordering scorer) and T4 (flagged pipeline integration) build on these.
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


# ===========================================================================
# T2 — Prerequisite graph
# ===========================================================================
# Sparse, high-confidence edges only. Two deterministic sources:
#   1. keyword seed edges (semantic; may run against the original order)
#   2. role-based "nearest prior" edges (strictly backward in list order, so
#      they can never introduce a cycle on their own)
# No LLM, no embeddings, no full N×N role fan-out (that would drown the T3 scorer).

# (earlier_terms, later_terms, slug). Matching is case-insensitive substring on
# the combined card text (title + summary + key_concepts). Keep this table tiny
# and high-confidence — broad seeds create noise and false cycles.
PREREQUISITE_KEYWORD_SEEDS: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    (("embedding", "嵌入", "向量表示"), ("vector database", "向量資料庫", "vector store"),
     "embedding->vector_database"),
    (("vector database", "向量資料庫", "vector store"), ("retrieval", "檢索"),
     "vector_database->retrieval"),
    (("retrieval", "檢索"), ("rag", "檢索增強生成"),
     "retrieval->rag"),
    (("prompt", "提示詞", "提示工程"), ("chain-of-thought", "cot", "思維鏈"),
     "prompting->chain_of_thought"),
)

# role -> (eligible prior roles, reason slug). reference / unknown never drive a
# role edge and are never selected as a prior (they are not in any prior set).
ROLE_PRIOR_RULES: dict[str, tuple[tuple[str, ...], str]] = {
    "application": (("foundation", "core"), "role:foundation_core_before_application"),
    "advanced": (("foundation", "core", "application"),
                 "role:foundation_core_application_before_advanced"),
    "summary": (("overview", "foundation", "core", "application", "advanced"),
                "role:teaching_before_summary"),
}


@dataclass(frozen=True)
class PrerequisiteEdge:
    before_stage_id: str
    after_stage_id: str
    reason: str
    confidence: str  # "high" for the T2 MVP


@dataclass(frozen=True)
class PrerequisiteGraph:
    stage_ids: tuple[str, ...]
    edges: tuple[PrerequisiteEdge, ...]
    clusters: tuple[tuple[str, ...], ...]
    has_cycle: bool


def _card_text(card: StageCard) -> str:
    return " ".join([card.title, card.summary, " ".join(card.key_concepts)]).lower()


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term.lower() in text for term in terms)


def _nearest_prior_index(cards: Sequence[StageCard], index: int, roles: tuple[str, ...]) -> int | None:
    for k in range(index - 1, -1, -1):
        if cards[k].role in roles:
            return k
    return None


def _build_edges(cards: Sequence[StageCard]) -> tuple[PrerequisiteEdge, ...]:
    texts = [_card_text(c) for c in cards]
    keyword_edges: list[PrerequisiteEdge] = []
    for earlier, later, slug in PREREQUISITE_KEYWORD_SEEDS:
        reason = f"keyword_seed:{slug}"
        for i, card_a in enumerate(cards):
            if not _contains_any(texts[i], earlier):
                continue
            for j, card_b in enumerate(cards):
                if i == j:
                    continue
                if _contains_any(texts[j], later):
                    keyword_edges.append(
                        PrerequisiteEdge(card_a.stage_id, card_b.stage_id, reason, "high")
                    )

    role_edges: list[PrerequisiteEdge] = []
    for index, card in enumerate(cards):
        rule = ROLE_PRIOR_RULES.get(card.role)
        if rule is None:
            continue
        prior_roles, reason = rule
        k = _nearest_prior_index(cards, index, prior_roles)
        if k is not None:
            role_edges.append(PrerequisiteEdge(cards[k].stage_id, card.stage_id, reason, "high"))

    # Dedupe by (before, after) pair. Keyword edges come first so a pair that is
    # both a semantic seed and a role edge keeps the semantic reason.
    seen: set[tuple[str, str]] = set()
    edges: list[PrerequisiteEdge] = []
    for edge in keyword_edges + role_edges:
        if edge.before_stage_id == edge.after_stage_id:
            continue
        pair = (edge.before_stage_id, edge.after_stage_id)
        if pair in seen:
            continue
        seen.add(pair)
        edges.append(edge)
    return tuple(edges)


def _detect_cycle(
    stage_ids: tuple[str, ...], edges: Sequence[PrerequisiteEdge]
) -> list[str]:
    """Return one cycle path (deterministic) or [] when acyclic."""
    adj: dict[str, list[str]] = {sid: [] for sid in stage_ids}
    for edge in edges:
        if edge.before_stage_id in adj and edge.after_stage_id in adj:
            adj[edge.before_stage_id].append(edge.after_stage_id)

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {sid: WHITE for sid in stage_ids}
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = GRAY
        path.append(node)
        for nxt in adj[node]:
            if color.get(nxt) == GRAY:
                return path[path.index(nxt):]
            if color.get(nxt) == WHITE:
                found = visit(nxt)
                if found is not None:
                    return found
        color[node] = BLACK
        path.pop()
        return None

    for sid in stage_ids:
        if color[sid] == WHITE:
            found = visit(sid)
            if found is not None:
                return found
    return []


def _weakly_connected_clusters(
    stage_ids: tuple[str, ...], edges: Sequence[PrerequisiteEdge]
) -> tuple[tuple[str, ...], ...]:
    index_of = {sid: i for i, sid in enumerate(stage_ids)}
    adj: dict[str, set[str]] = {sid: set() for sid in stage_ids}
    for edge in edges:
        if edge.before_stage_id in adj and edge.after_stage_id in adj:
            adj[edge.before_stage_id].add(edge.after_stage_id)
            adj[edge.after_stage_id].add(edge.before_stage_id)

    visited: set[str] = set()
    clusters: list[tuple[str, ...]] = []
    for sid in stage_ids:  # original order → cluster order by first stage
        if sid in visited:
            continue
        component: list[str] = []
        stack = [sid]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            component.append(node)
            for neighbour in adj[node]:
                if neighbour not in visited:
                    stack.append(neighbour)
        component.sort(key=lambda s: index_of[s])
        clusters.append(tuple(component))
    return tuple(clusters)


def build_prerequisite_graph(
    cards: Sequence[StageCard],
) -> tuple[PrerequisiteGraph, list[dict[str, Any]]]:
    """Build a sparse prerequisite graph + warn-only diagnostics from stage cards.

    Returns ``(graph, diagnostics)``. Diagnostics are warn-only dicts and are
    *not* attached to ``quality_warnings`` here — wiring is deferred to T4.
    """
    cards = list(cards)
    stage_ids = tuple(c.stage_id for c in cards)
    edges = _build_edges(cards)
    cycle_path = _detect_cycle(stage_ids, edges)
    has_cycle = bool(cycle_path)
    clusters = _weakly_connected_clusters(stage_ids, edges)

    diagnostics: list[dict[str, Any]] = []
    if has_cycle:
        diagnostics.append(
            {
                "type": "prerequisite_cycle_detected",
                "stage_ids": list(cycle_path),
                "reason": "cycle_in_prerequisite_graph",
            }
        )
    if len(clusters) > 1:
        diagnostics.append(
            {
                "type": "unrelated_source_clusters",
                "cluster_count": len(clusters),
                "clusters": [list(c) for c in clusters],
                "reason": "multiple_weakly_connected_stage_clusters",
            }
        )

    graph = PrerequisiteGraph(
        stage_ids=stage_ids, edges=edges, clusters=clusters, has_cycle=has_cycle
    )
    return graph, diagnostics
