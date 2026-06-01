"""Phase 4 — Cross-material pedagogical planner utilities.

This module is intentionally **pure and deterministic**. It currently contains:

- T1: StageCard normalization with heuristic role / difficulty classification.
- T2: prerequisite graph construction with sparse high-confidence edges,
  cycle detection, and unrelated cluster diagnostics.
- T3: warn-only ordering plan construction with deterministic topological
  recommendations and current-order diagnostics.
- T4a: pedagogical plan schema parsing, pure stage-move application, and
  safety verifiers.

Scope guard (see ``docs/phase4_implementation_tickets.md``):
- No LLM calls, no prompt, no external dependency.
- No persisted stage reordering, no merge, no chunk reassignment, no DB migration.
- Never mutates input stages or cards; output is fully deterministic.

T4b/T4c add the LLM planner and feature-flagged pipeline integration.
"""
from __future__ import annotations

import copy
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


def _stage_identity(stage: Mapping[str, Any], index: int) -> str:
    """Stable identity for a stage: its ``stage_id`` field, else ``stage_<index>``."""
    raw_stage_id = stage.get("stage_id")
    stage_id = str(raw_stage_id).strip() if raw_stage_id is not None else f"stage_{index}"
    return stage_id or f"stage_{index}"


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

        stage_id = _stage_identity(stage, index)

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


# ===========================================================================
# T3 — Ordering scorer (warn-only)
# ===========================================================================
# Produces a deterministic recommended stage order + ordering diagnostics.
# The diagnostics always describe the CURRENT order's problems, never the
# recommended order. Nothing here reorders persisted stages or mutates input —
# the recommendation is advisory until T4 (flagged pipeline integration).

# Pedagogical role rank for ordering. reference sits after summary (side
# material / appendix); unknown sits last so weak metadata never drives order.
ROLE_ORDER_RANK: dict[str, int] = {
    "overview": 0,
    "foundation": 1,
    "core": 2,
    "application": 3,
    "advanced": 4,
    "summary": 5,
    "reference": 6,
    "unknown": 7,
}

# Roles excluded from "main teaching" reasoning (side material / weak-signal).
_NON_TEACHING_ROLES = frozenset({"reference", "unknown"})
# Additionally excludes summary (positional closure): used where a trailing
# summary should not count as "teaching content that still follows".
_POSITIONAL_OR_WEAK_ROLES = frozenset({"summary", "reference", "unknown"})
# Difficulty drop (prev - curr) at/above this between adjacent main teaching
# stages is flagged as a regression.
_DIFFICULTY_REGRESSION_DROP = 2


@dataclass(frozen=True)
class OrderingPlan:
    current_stage_ids: tuple[str, ...]
    recommended_stage_ids: tuple[str, ...]
    order_changed: bool
    diagnostics: tuple[dict[str, Any], ...]


def _role_rank(role: str) -> int:
    return ROLE_ORDER_RANK.get(role, ROLE_ORDER_RANK["unknown"])


def _topo_sort(cards: Sequence[StageCard], graph: PrerequisiteGraph) -> tuple[str, ...]:
    """Deterministic Kahn topological sort.

    Among prerequisite-satisfied candidates, prefer earlier pedagogical role,
    then lower difficulty, then original stage_index. Equal-key ties preserve
    input order via Python's stable sort. Edges referencing absent stage ids
    are ignored.
    """
    card_by_id: dict[str, StageCard] = {}
    for c in cards:
        card_by_id.setdefault(c.stage_id, c)
    ids = list(card_by_id)

    adj: dict[str, list[str]] = {sid: [] for sid in ids}
    indeg: dict[str, int] = {sid: 0 for sid in ids}
    seen_edges: set[tuple[str, str]] = set()
    for edge in graph.edges:
        b, a = edge.before_stage_id, edge.after_stage_id
        if b in card_by_id and a in card_by_id and b != a and (b, a) not in seen_edges:
            seen_edges.add((b, a))
            adj[b].append(a)
            indeg[a] += 1

    def priority(sid: str) -> tuple[int, int, int]:
        # Stable sort + ids preserving input order keeps equal-key ties
        # deterministic without letting stage_id influence pedagogical order.
        c = card_by_id[sid]
        return (_role_rank(c.role), c.difficulty, c.stage_index)

    available = [sid for sid in ids if indeg[sid] == 0]
    result: list[str] = []
    while available:
        available.sort(key=priority)
        sid = available.pop(0)
        result.append(sid)
        for nxt in adj[sid]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                available.append(nxt)

    if len(result) != len(ids):  # defensive: residual cycle among present edges
        return tuple(ids)
    return tuple(result)


def _ordering_diagnostics(
    cards: Sequence[StageCard], graph: PrerequisiteGraph
) -> list[dict[str, Any]]:
    pos = {c.stage_id: i for i, c in enumerate(cards)}
    diags: list[dict[str, Any]] = []

    if graph.has_cycle:
        diags.append(
            {
                "type": "prerequisite_cycle_blocks_ordering",
                "reason": "cycle_detected_in_prerequisite_graph",
            }
        )

    for edge in graph.edges:
        b, a = edge.before_stage_id, edge.after_stage_id
        if b in pos and a in pos and pos[b] > pos[a]:
            diags.append(
                {
                    "type": "prerequisite_order_violation",
                    "before_stage_id": b,
                    "after_stage_id": a,
                    "reason": edge.reason,
                    "confidence": edge.confidence,
                }
            )

    advanced = [(c.stage_id, pos[c.stage_id]) for c in cards if c.role == "advanced"]
    for c in cards:
        if c.role == "overview":
            oi = pos[c.stage_id]
            for adv_id, ai in advanced:
                if ai < oi:
                    diags.append(
                        {
                            "type": "overview_after_advanced",
                            "stage_id": c.stage_id,
                            "advanced_stage_id": adv_id,
                            "reason": "overview_should_precede_advanced_content",
                        }
                    )

    # A summary is "in the middle" only if real teaching content follows it.
    # Another summary after it is positional closure, not teaching content.
    for i, c in enumerate(cards):
        if c.role == "summary" and any(
            later.role not in _POSITIONAL_OR_WEAK_ROLES for later in cards[i + 1:]
        ):
            diags.append(
                {
                    "type": "summary_in_middle",
                    "stage_id": c.stage_id,
                    "reason": "summary_should_follow_main_teaching_stages",
                }
            )

    for i, c in enumerate(cards):
        if c.role == "reference" and any(
            later.role not in _NON_TEACHING_ROLES for later in cards[i + 1:]
        ):
            diags.append(
                {
                    "type": "reference_as_main_stage",
                    "stage_id": c.stage_id,
                    "reason": "reference_should_be_appendix_or_side_material",
                }
            )

    main_seq = [c for c in cards if c.role not in _POSITIONAL_OR_WEAK_ROLES]
    for prev, curr in zip(main_seq, main_seq[1:]):
        if prev.difficulty - curr.difficulty >= _DIFFICULTY_REGRESSION_DROP:
            diags.append(
                {
                    "type": "difficulty_regression",
                    "before_stage_id": prev.stage_id,
                    "after_stage_id": curr.stage_id,
                    "before_difficulty": prev.difficulty,
                    "after_difficulty": curr.difficulty,
                    "reason": "difficulty_should_progress_gradually",
                }
            )

    return diags


def build_ordering_plan(
    cards: Sequence[StageCard], graph: PrerequisiteGraph
) -> OrderingPlan:
    """Build a deterministic warn-only ordering plan from cards + graph.

    Diagnostics describe the *current* order. The recommendation is advisory:
    a prerequisite cycle blocks reorder (keeps current order). Nothing is
    persisted or mutated here — wiring is deferred to T4.
    """
    cards = list(cards)
    current = tuple(c.stage_id for c in cards)
    diagnostics = _ordering_diagnostics(cards, graph)

    if graph.has_cycle:
        recommended = current
    else:
        recommended = _topo_sort(cards, graph)

    return OrderingPlan(
        current_stage_ids=current,
        recommended_stage_ids=recommended,
        order_changed=recommended != current,
        diagnostics=tuple(diagnostics),
    )


# ===========================================================================
# T4a — Pedagogical plan schema, applier, and safety verifiers (pure)
# ===========================================================================
# The LLM planner (T4b) proposes a plan of stage *moves*; this layer parses,
# validates, and applies it programmatically while preserving stage identity,
# source coverage metadata, and stage content. Anything suspicious falls back
# to the original order (conservative). No LLM, no prompt, no pipeline wiring.

# Coverage fields that a pure reorder must never change.
_PLAN_COVERAGE_FIELDS = ("source_ids", "source_stage_ids", "source_chunk_ids")
# Content fields that a pure reorder must never change.
_PLAN_CONTENT_FIELDS = ("title", "summary", "key_concepts")


@dataclass(frozen=True)
class PedagogicalPlanMove:
    stage_id: str
    after_stage_id: str | None  # None ⇒ move to the beginning
    reason: str


@dataclass(frozen=True)
class PedagogicalPlan:
    moves: tuple[PedagogicalPlanMove, ...]
    rationale: str


@dataclass(frozen=True)
class PedagogicalPlanResult:
    stages: tuple[Mapping[str, Any], ...]
    applied: bool
    fallback_reason: str | None
    diagnostics: tuple[dict[str, Any], ...]


def parse_pedagogical_plan(
    payload: Any,
) -> tuple[PedagogicalPlan | None, list[dict[str, Any]]]:
    """Validate and parse a raw plan payload (e.g. decoded LLM JSON).

    Existence of referenced stages is *not* checked here — that is the
    applier's job. Returns ``(plan, [])`` on success or ``(None, diagnostics)``.
    """
    if not isinstance(payload, Mapping):
        return None, [{"type": "invalid_plan_payload", "reason": "payload_must_be_mapping"}]

    raw_moves = payload.get("moves")
    if not isinstance(raw_moves, list):
        return None, [{"type": "invalid_plan_payload", "reason": "moves_must_be_list"}]

    moves: list[PedagogicalPlanMove] = []
    for i, raw in enumerate(raw_moves):
        if not isinstance(raw, Mapping):
            return None, [{"type": "invalid_plan_move", "move_index": i, "reason": "move_must_be_mapping"}]
        sid = raw.get("stage_id")
        if not isinstance(sid, str) or not sid.strip():
            return None, [{"type": "invalid_plan_move", "move_index": i, "reason": "stage_id_must_be_nonempty_str"}]
        after = raw.get("after_stage_id")
        if after is not None:
            if not isinstance(after, str) or not after.strip():
                return None, [{"type": "invalid_plan_move", "move_index": i,
                               "reason": "after_stage_id_must_be_null_or_nonempty_str"}]
            after = after.strip()
        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return None, [{"type": "invalid_plan_move", "move_index": i, "reason": "reason_must_be_nonempty_str"}]
        moves.append(PedagogicalPlanMove(stage_id=sid.strip(), after_stage_id=after, reason=reason.strip()))

    raw_rationale = payload.get("rationale")
    rationale = str(raw_rationale).strip() if raw_rationale is not None else ""
    return PedagogicalPlan(moves=tuple(moves), rationale=rationale), []


def _explicit_stage_id(stage: Mapping[str, Any]) -> str | None:
    raw = stage.get("stage_id")
    if raw is None:
        return None
    sid = str(raw).strip()
    return sid or None


def _explicit_id_map(stages: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for stage in stages:
        eid = _explicit_stage_id(stage)
        if eid is not None:
            out.setdefault(eid, stage)
    return out


def _verify_stage_id_set(
    before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Order-independent: the multiset of stage identities must be preserved."""
    b = sorted(_stage_identity(s, i) for i, s in enumerate(before))
    a = sorted(_stage_identity(s, i) for i, s in enumerate(after))
    if b != a:
        return [{"type": "stage_id_set_changed", "reason": "applied_plan_must_preserve_stage_ids"}]
    return []


def _verify_stage_coverage(
    before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    bmap, amap = _explicit_id_map(before), _explicit_id_map(after)
    diags: list[dict[str, Any]] = []
    for sid, bstage in bmap.items():
        astage = amap.get(sid)
        if astage is None:
            continue
        for field in _PLAN_COVERAGE_FIELDS:
            if _as_str_tuple(bstage.get(field)) != _as_str_tuple(astage.get(field)):
                diags.append(
                    {
                        "type": "stage_coverage_changed",
                        "stage_id": sid,
                        "field": field,
                        "reason": "applied_plan_must_preserve_stage_coverage",
                    }
                )
    return diags


def _verify_stage_content(
    before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    bmap, amap = _explicit_id_map(before), _explicit_id_map(after)
    diags: list[dict[str, Any]] = []
    for sid, bstage in bmap.items():
        astage = amap.get(sid)
        if astage is None:
            continue
        for field in _PLAN_CONTENT_FIELDS:
            bv, av = bstage.get(field), astage.get(field)
            if field == "key_concepts":
                changed = _as_str_tuple(bv) != _as_str_tuple(av)
            else:
                changed = str(bv or "") != str(av or "")
            if changed:
                diags.append(
                    {
                        "type": "stage_content_changed",
                        "stage_id": sid,
                        "field": field,
                        "reason": "applied_plan_must_preserve_stage_content",
                    }
                )
    return diags


def apply_pedagogical_plan(
    stages: Sequence[Mapping[str, Any]], plan: PedagogicalPlan
) -> PedagogicalPlanResult:
    """Apply plan moves to reorder stages, with conservative fallback.

    Each move pulls ``stage_id`` out of the working order and reinserts it
    immediately after ``after_stage_id`` (or at the front when None). Any
    invalid move or safety-verifier diagnostic returns the original order.
    Input stages and plan are never mutated; returned stages are deep copies.
    """
    original = [copy.deepcopy(dict(s)) for s in stages]
    identities = [_stage_identity(s, i) for i, s in enumerate(original)]

    # Preflight: duplicate identities would let a later stage be silently
    # dropped (collapsed onto the first occurrence). Refuse to apply.
    counts: dict[str, int] = {}
    for ident in identities:
        counts[ident] = counts.get(ident, 0) + 1
    dupes = sorted(sid for sid, n in counts.items() if n > 1)
    if dupes:
        return PedagogicalPlanResult(
            stages=tuple(original),
            applied=False,
            fallback_reason="duplicate_stage_identity",
            diagnostics=(
                {
                    "type": "duplicate_stage_identity",
                    "stage_ids": dupes,
                    "reason": "stage_identities_must_be_unique_to_apply_plan",
                },
            ),
        )

    stage_by_id: dict[str, Mapping[str, Any]] = {}
    for ident, stage in zip(identities, original):
        stage_by_id.setdefault(ident, stage)

    work = list(identities)
    moved: set[str] = set()
    for move in plan.moves:
        sid, after = move.stage_id, move.after_stage_id
        diag: dict[str, Any] | None = None
        if sid not in stage_by_id:
            diag = {"type": "invalid_plan_move", "stage_id": sid, "reason": "stage_id_not_found"}
        elif sid in moved:
            diag = {"type": "invalid_plan_move", "stage_id": sid, "reason": "duplicate_moved_stage_id"}
        elif after is not None and after == sid:
            diag = {"type": "invalid_plan_move", "stage_id": sid, "after_stage_id": after,
                    "reason": "self_move_not_allowed"}
        elif after is not None and after not in stage_by_id:
            diag = {"type": "invalid_plan_move", "stage_id": sid, "after_stage_id": after,
                    "reason": "after_stage_id_not_found"}
        if diag is not None:
            return PedagogicalPlanResult(
                stages=tuple(original), applied=False,
                fallback_reason="invalid_plan_move", diagnostics=(diag,),
            )
        moved.add(sid)
        work.remove(sid)
        if after is None:
            work.insert(0, sid)
        else:
            work.insert(work.index(after) + 1, sid)

    reordered = [stage_by_id[ident] for ident in work]
    vdiags = (
        _verify_stage_id_set(original, reordered)
        + _verify_stage_coverage(original, reordered)
        + _verify_stage_content(original, reordered)
    )
    if vdiags:
        return PedagogicalPlanResult(
            stages=tuple(original), applied=False,
            fallback_reason="safety_verifier_failed", diagnostics=tuple(vdiags),
        )
    return PedagogicalPlanResult(
        stages=tuple(reordered), applied=True, fallback_reason=None, diagnostics=(),
    )
