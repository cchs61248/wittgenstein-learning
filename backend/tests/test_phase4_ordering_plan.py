"""Phase 4 / T3: deterministic warn-only ordering scorer.

Combines StageCard metadata + PrerequisiteGraph constraints into a recommended
stage order plus ordering diagnostics — all describing the *current* order.

Pure / deterministic / warn-only: no LLM, no pipeline wiring, no real reorder,
no mutation of stages or cards.
"""
import copy
import unittest

from backend.utils.pedagogical_planner import (
    StageCard,
    PrerequisiteEdge,
    PrerequisiteGraph,
    OrderingPlan,
    build_ordering_plan,
)


def _card(stage_id, role="core", difficulty=3, stage_index=0,
          title="", summary="", key_concepts=()):
    return StageCard(
        stage_id=str(stage_id),
        stage_index=stage_index,
        source_ids=(),
        source_stage_ids=(),
        title=title,
        summary=summary,
        key_concepts=tuple(key_concepts),
        role=role,
        difficulty=difficulty,
        role_reason="t",
        difficulty_reason="t",
    )


def _graph(stage_ids, edges=(), has_cycle=False):
    return PrerequisiteGraph(
        stage_ids=tuple(stage_ids), edges=tuple(edges), clusters=(), has_cycle=has_cycle
    )


def _types(plan):
    return [d["type"] for d in plan.diagnostics]


class TestBasics(unittest.TestCase):
    def test_empty_cards_returns_empty_plan(self):
        plan = build_ordering_plan([], _graph([]))
        self.assertEqual(plan.current_stage_ids, ())
        self.assertEqual(plan.recommended_stage_ids, ())
        self.assertFalse(plan.order_changed)
        self.assertEqual(plan.diagnostics, ())

    def test_current_preserves_input_order(self):
        cards = [_card("c"), _card("a"), _card("b")]
        plan = build_ordering_plan(cards, _graph(["c", "a", "b"]))
        self.assertEqual(plan.current_stage_ids, ("c", "a", "b"))

    def test_recommended_deterministic(self):
        cards = [_card("s", role="summary", stage_index=0), _card("o", role="overview", stage_index=1)]
        g = _graph(["s", "o"])
        self.assertEqual(build_ordering_plan(cards, g), build_ordering_plan(cards, g))

    def test_diagnostics_is_tuple(self):
        plan = build_ordering_plan([_card("a")], _graph(["a"]))
        self.assertIsInstance(plan.diagnostics, tuple)
        self.assertIsInstance(plan, OrderingPlan)


class TestTopologicalConstraints(unittest.TestCase):
    def test_prerequisite_edge_reorders_reversed_current(self):
        cards = [_card("b", stage_index=0), _card("a", stage_index=1)]
        edge = PrerequisiteEdge("a", "b", "keyword_seed:retrieval->rag", "high")
        plan = build_ordering_plan(cards, _graph(["b", "a"], edges=[edge]))
        self.assertEqual(plan.recommended_stage_ids, ("a", "b"))
        self.assertTrue(plan.order_changed)

    def test_already_valid_order_stays(self):
        cards = [_card("a", stage_index=0), _card("b", stage_index=1)]
        edge = PrerequisiteEdge("a", "b", "r", "high")
        plan = build_ordering_plan(cards, _graph(["a", "b"], edges=[edge]))
        self.assertEqual(plan.recommended_stage_ids, ("a", "b"))
        self.assertFalse(plan.order_changed)

    def test_topo_ignores_edges_for_missing_stage_ids(self):
        cards = [_card("a", stage_index=0), _card("b", stage_index=1)]
        ghost = PrerequisiteEdge("ghost", "a", "r", "high")
        plan = build_ordering_plan(cards, _graph(["a", "b"], edges=[ghost]))
        self.assertEqual(plan.recommended_stage_ids, ("a", "b"))

    def test_graph_cycle_blocks_reorder(self):
        cards = [_card("b", stage_index=0), _card("a", stage_index=1)]
        edge = PrerequisiteEdge("a", "b", "r", "high")
        plan = build_ordering_plan(cards, _graph(["b", "a"], edges=[edge], has_cycle=True))
        self.assertEqual(plan.recommended_stage_ids, plan.current_stage_ids)
        self.assertFalse(plan.order_changed)
        self.assertIn("prerequisite_cycle_blocks_ordering", _types(plan))


class TestPriorityTieBreaks(unittest.TestCase):
    def test_role_rank_orders_when_no_edges(self):
        cards = [
            _card("s", role="summary", stage_index=0),
            _card("o", role="overview", stage_index=1),
            _card("a", role="advanced", stage_index=2),
            _card("f", role="foundation", stage_index=3),
        ]
        plan = build_ordering_plan(cards, _graph(["s", "o", "a", "f"]))
        self.assertEqual(plan.recommended_stage_ids, ("o", "f", "a", "s"))

    def test_difficulty_orders_lower_before_higher_same_role(self):
        cards = [_card("hi", difficulty=4, stage_index=0), _card("lo", difficulty=2, stage_index=1)]
        plan = build_ordering_plan(cards, _graph(["hi", "lo"]))
        self.assertEqual(plan.recommended_stage_ids, ("lo", "hi"))

    def test_original_index_tie_break_stable(self):
        cards = [_card("b", difficulty=3, stage_index=1), _card("a", difficulty=3, stage_index=0)]
        plan = build_ordering_plan(cards, _graph(["b", "a"]))
        self.assertEqual(plan.recommended_stage_ids, ("a", "b"))

    def test_equal_priority_preserves_input_order(self):
        cards = [
            _card("b", role="core", difficulty=3, stage_index=0),
            _card("a", role="core", difficulty=3, stage_index=0),
        ]
        plan = build_ordering_plan(cards, _graph(["b", "a"]))
        self.assertEqual(plan.recommended_stage_ids, ("b", "a"))


class TestDiagnostics(unittest.TestCase):
    def test_prerequisite_order_violation_emitted(self):
        cards = [_card("b", stage_index=0), _card("a", stage_index=1)]
        edge = PrerequisiteEdge("a", "b", "keyword_seed:retrieval->rag", "high")
        plan = build_ordering_plan(cards, _graph(["b", "a"], edges=[edge]))
        d = [x for x in plan.diagnostics if x["type"] == "prerequisite_order_violation"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["before_stage_id"], "a")
        self.assertEqual(d[0]["after_stage_id"], "b")
        self.assertEqual(d[0]["reason"], "keyword_seed:retrieval->rag")
        self.assertEqual(d[0]["confidence"], "high")

    def test_overview_after_advanced_emitted(self):
        cards = [_card("adv", role="advanced", stage_index=0), _card("ov", role="overview", stage_index=1)]
        plan = build_ordering_plan(cards, _graph(["adv", "ov"]))
        d = [x for x in plan.diagnostics if x["type"] == "overview_after_advanced"]
        self.assertTrue(d)
        self.assertEqual(d[0]["stage_id"], "ov")
        self.assertEqual(d[0]["advanced_stage_id"], "adv")

    def test_summary_in_middle_emitted(self):
        cards = [_card("sum", role="summary", stage_index=0), _card("c", role="core", stage_index=1)]
        plan = build_ordering_plan(cards, _graph(["sum", "c"]))
        d = [x for x in plan.diagnostics if x["type"] == "summary_in_middle"]
        self.assertTrue(d)
        self.assertEqual(d[0]["stage_id"], "sum")

    def test_reference_as_main_stage_emitted(self):
        cards = [_card("ref", role="reference", stage_index=0), _card("c", role="core", stage_index=1)]
        plan = build_ordering_plan(cards, _graph(["ref", "c"]))
        d = [x for x in plan.diagnostics if x["type"] == "reference_as_main_stage"]
        self.assertTrue(d)
        self.assertEqual(d[0]["stage_id"], "ref")

    def test_difficulty_regression_emitted(self):
        cards = [_card("hard", role="core", difficulty=5, stage_index=0),
                 _card("easy", role="core", difficulty=2, stage_index=1)]
        plan = build_ordering_plan(cards, _graph(["hard", "easy"]))
        d = [x for x in plan.diagnostics if x["type"] == "difficulty_regression"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["before_stage_id"], "hard")
        self.assertEqual(d[0]["after_stage_id"], "easy")
        self.assertEqual(d[0]["before_difficulty"], 5)
        self.assertEqual(d[0]["after_difficulty"], 2)

    def test_cycle_emits_blocks_ordering(self):
        cards = [_card("a", stage_index=0), _card("b", stage_index=1)]
        plan = build_ordering_plan(cards, _graph(["a", "b"], has_cycle=True))
        self.assertIn("prerequisite_cycle_blocks_ordering", _types(plan))

    def test_good_order_emits_no_positional_warnings(self):
        cards = [
            _card("o", role="overview", difficulty=1, stage_index=0),
            _card("c", role="core", difficulty=3, stage_index=1),
            _card("sum", role="summary", difficulty=5, stage_index=2),
            _card("ref", role="reference", difficulty=1, stage_index=3),
        ]
        plan = build_ordering_plan(cards, _graph(["o", "c", "sum", "ref"]))
        for t in ("overview_after_advanced", "summary_in_middle", "reference_as_main_stage",
                  "difficulty_regression", "prerequisite_order_violation",
                  "prerequisite_cycle_blocks_ordering"):
            self.assertNotIn(t, _types(plan))

    def test_summary_followed_only_by_summary_reference_unknown_is_not_middle(self):
        cards = [
            _card("s1", role="summary", stage_index=0),
            _card("s2", role="summary", stage_index=1),
            _card("ref", role="reference", stage_index=2),
            _card("u", role="unknown", stage_index=3),
        ]
        plan = build_ordering_plan(cards, _graph(["s1", "s2", "ref", "u"]))
        self.assertNotIn("summary_in_middle", _types(plan))

    def test_difficulty_regression_ignores_summary(self):
        # core(diff5) -> summary(diff5) -> nothing: summary excluded from regression seq
        cards = [_card("c", role="core", difficulty=3, stage_index=0),
                 _card("sum", role="summary", difficulty=5, stage_index=1)]
        plan = build_ordering_plan(cards, _graph(["c", "sum"]))
        self.assertNotIn("difficulty_regression", _types(plan))


class TestPurity(unittest.TestCase):
    def test_does_not_mutate_cards(self):
        cards = [_card("adv", role="advanced", stage_index=0), _card("ov", role="overview", stage_index=1)]
        before = copy.deepcopy(cards)
        build_ordering_plan(cards, _graph(["adv", "ov"]))
        self.assertEqual(cards, before)


if __name__ == "__main__":
    unittest.main()
