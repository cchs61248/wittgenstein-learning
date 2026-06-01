"""Phase 4 / T4a: pedagogical plan schema, applier, and safety verifiers.

Pure functions only: parse a plan payload, apply stage-move reordering, and
verify the result preserves stage identity / coverage / content. Invalid moves
or verifier failures fall back to the original order with warn-only diagnostics.

No LLM, no prompt, no pipeline wiring, no feature flag.
"""
import copy
import unittest
from unittest import mock

from backend.utils.pedagogical_planner import (
    PedagogicalPlan,
    PedagogicalPlanMove,
    PedagogicalPlanResult,
    parse_pedagogical_plan,
    apply_pedagogical_plan,
    _verify_stage_id_set,
    _verify_stage_coverage,
    _verify_stage_content,
)


def _stage(stage_id, title="T", summary="S", key_concepts=("k",),
           source_ids=(), source_stage_ids=(), source_chunk_ids=("chunk_0",)):
    d = {
        "stage_id": stage_id,
        "title": title,
        "summary": summary,
        "key_concepts": list(key_concepts),
        "source_chunk_ids": list(source_chunk_ids),
    }
    if source_ids:
        d["source_ids"] = list(source_ids)
    if source_stage_ids:
        d["source_stage_ids"] = list(source_stage_ids)
    return d


def _move(stage_id, after=None, reason="r"):
    return PedagogicalPlanMove(stage_id=str(stage_id), after_stage_id=after, reason=reason)


def _plan(*moves, rationale="r"):
    return PedagogicalPlan(moves=tuple(moves), rationale=rationale)


def _ids(stages):
    return [s["stage_id"] for s in stages]


class TestParser(unittest.TestCase):
    def test_valid_payload_parses(self):
        payload = {"moves": [{"stage_id": "s3", "after_stage_id": "s1", "reason": "x"}], "rationale": "R"}
        plan, diags = parse_pedagogical_plan(payload)
        self.assertEqual(diags, [])
        self.assertEqual(plan.rationale, "R")
        self.assertEqual(len(plan.moves), 1)
        self.assertEqual(plan.moves[0].stage_id, "s3")
        self.assertEqual(plan.moves[0].after_stage_id, "s1")
        self.assertEqual(plan.moves[0].reason, "x")

    def test_missing_moves_invalid(self):
        plan, diags = parse_pedagogical_plan({"rationale": "x"})
        self.assertIsNone(plan)
        self.assertEqual(diags[0]["type"], "invalid_plan_payload")

    def test_moves_not_list_invalid(self):
        plan, diags = parse_pedagogical_plan({"moves": "nope"})
        self.assertIsNone(plan)
        self.assertEqual(diags[0]["type"], "invalid_plan_payload")

    def test_invalid_move_missing_stage_id(self):
        plan, diags = parse_pedagogical_plan({"moves": [{"after_stage_id": None, "reason": "x"}]})
        self.assertIsNone(plan)
        self.assertEqual(diags[0]["type"], "invalid_plan_move")

    def test_after_stage_id_none_allowed(self):
        plan, diags = parse_pedagogical_plan(
            {"moves": [{"stage_id": "s1", "after_stage_id": None, "reason": "x"}], "rationale": ""}
        )
        self.assertIsNotNone(plan)
        self.assertIsNone(plan.moves[0].after_stage_id)

    def test_empty_after_stage_id_invalid(self):
        plan, diags = parse_pedagogical_plan(
            {"moves": [{"stage_id": "s1", "after_stage_id": "  ", "reason": "x"}]}
        )
        self.assertIsNone(plan)
        self.assertEqual(diags[0]["type"], "invalid_plan_move")

    def test_rationale_normalized_to_str(self):
        plan1, _ = parse_pedagogical_plan({"moves": []})
        self.assertEqual(plan1.rationale, "")
        plan2, _ = parse_pedagogical_plan({"moves": [], "rationale": 123})
        self.assertEqual(plan2.rationale, "123")

    def test_non_mapping_payload_invalid(self):
        plan, diags = parse_pedagogical_plan(["not", "a", "mapping"])
        self.assertIsNone(plan)
        self.assertEqual(diags[0]["type"], "invalid_plan_payload")


class TestApplierBasics(unittest.TestCase):
    def setUp(self):
        self.stages = [_stage("a"), _stage("b"), _stage("c")]

    def test_move_stage_to_beginning(self):
        res = apply_pedagogical_plan(self.stages, _plan(_move("c", after=None)))
        self.assertTrue(res.applied)
        self.assertEqual(_ids(res.stages), ["c", "a", "b"])

    def test_move_stage_after_another(self):
        res = apply_pedagogical_plan(self.stages, _plan(_move("a", after="b")))
        self.assertEqual(_ids(res.stages), ["b", "a", "c"])

    def test_multiple_moves_sequential(self):
        res = apply_pedagogical_plan(self.stages, _plan(_move("c", after=None), _move("b", after="c")))
        self.assertEqual(_ids(res.stages), ["c", "b", "a"])

    def test_original_stages_not_mutated(self):
        stages = [_stage("a"), _stage("b")]
        before = copy.deepcopy(stages)
        apply_pedagogical_plan(stages, _plan(_move("b", after=None)))
        self.assertEqual(stages, before)

    def test_result_stages_are_tuple_and_deep_copied(self):
        stages = [_stage("a"), _stage("b")]
        res = apply_pedagogical_plan(stages, _plan())
        self.assertIsInstance(res.stages, tuple)
        res.stages[0]["title"] = "MUT"
        self.assertNotEqual(stages[0]["title"], "MUT")

    def test_no_moves_returns_same_order_applied(self):
        res = apply_pedagogical_plan([_stage("a"), _stage("b")], _plan())
        self.assertTrue(res.applied)
        self.assertIsNone(res.fallback_reason)
        self.assertEqual(_ids(res.stages), ["a", "b"])


class TestInvalidMoves(unittest.TestCase):
    def test_moving_missing_stage_fails(self):
        res = apply_pedagogical_plan([_stage("a")], _plan(_move("ghost", after=None)))
        self.assertFalse(res.applied)
        self.assertEqual(res.fallback_reason, "invalid_plan_move")
        self.assertEqual(_ids(res.stages), ["a"])

    def test_after_stage_id_missing_fails(self):
        res = apply_pedagogical_plan([_stage("a"), _stage("b")], _plan(_move("a", after="ghost")))
        self.assertFalse(res.applied)
        self.assertEqual(res.diagnostics[0]["reason"], "after_stage_id_not_found")

    def test_self_move_fails(self):
        res = apply_pedagogical_plan([_stage("a")], _plan(_move("a", after="a")))
        self.assertFalse(res.applied)
        self.assertEqual(res.diagnostics[0]["reason"], "self_move_not_allowed")

    def test_duplicate_moved_stage_fails(self):
        res = apply_pedagogical_plan(
            [_stage("a"), _stage("b")], _plan(_move("a", after=None), _move("a", after="b"))
        )
        self.assertFalse(res.applied)
        self.assertEqual(res.diagnostics[0]["reason"], "duplicate_moved_stage_id")

    def test_invalid_move_returns_original_order_copy(self):
        stages = [_stage("a"), _stage("b"), _stage("c")]
        res = apply_pedagogical_plan(stages, _plan(_move("ghost")))
        self.assertEqual(_ids(res.stages), ["a", "b", "c"])

    def test_duplicate_stage_identity_blocks_apply(self):
        stages = [_stage("a", title="A1"), _stage("a", title="A2")]
        res = apply_pedagogical_plan(stages, _plan())
        self.assertFalse(res.applied)
        self.assertEqual(res.fallback_reason, "duplicate_stage_identity")
        self.assertEqual(res.diagnostics[0]["type"], "duplicate_stage_identity")
        self.assertEqual(res.diagnostics[0]["stage_ids"], ["a"])
        self.assertEqual([s["title"] for s in res.stages], ["A1", "A2"])

    def test_empty_stage_ids_use_unique_index_fallback_identities(self):
        stages = [{"stage_id": " ", "title": "A"}, {"stage_id": " ", "title": "B"}]
        res = apply_pedagogical_plan(stages, _plan(_move("stage_1", after=None)))
        self.assertTrue(res.applied)
        self.assertEqual([s["title"] for s in res.stages], ["B", "A"])


class TestVerifiers(unittest.TestCase):
    def test_stage_id_set_detects_missing(self):
        self.assertTrue(_verify_stage_id_set([_stage("a"), _stage("b")], [_stage("a")]))

    def test_stage_id_set_detects_extra(self):
        self.assertTrue(_verify_stage_id_set([_stage("a")], [_stage("a"), _stage("b")]))

    def test_stage_id_set_detects_duplicate(self):
        d = _verify_stage_id_set([_stage("a"), _stage("b")], [_stage("a"), _stage("a")])
        self.assertTrue(d)
        self.assertEqual(d[0]["type"], "stage_id_set_changed")

    def test_stage_id_set_passes_on_reorder(self):
        self.assertEqual(_verify_stage_id_set([_stage("a"), _stage("b")], [_stage("b"), _stage("a")]), [])

    def test_coverage_detects_source_ids_changed(self):
        d = _verify_stage_coverage([_stage("a", source_ids=("x",))], [_stage("a", source_ids=("y",))])
        self.assertTrue(any(x["field"] == "source_ids" for x in d))

    def test_coverage_detects_source_stage_ids_changed(self):
        d = _verify_stage_coverage(
            [_stage("a", source_stage_ids=("c1",))], [_stage("a", source_stage_ids=("c2",))]
        )
        self.assertTrue(any(x["field"] == "source_stage_ids" for x in d))

    def test_coverage_detects_source_chunk_ids_changed(self):
        d = _verify_stage_coverage(
            [_stage("a", source_chunk_ids=("c1",))], [_stage("a", source_chunk_ids=("c2",))]
        )
        self.assertTrue(any(x["field"] == "source_chunk_ids" for x in d))

    def test_content_detects_title_changed(self):
        d = _verify_stage_content([_stage("a", title="X")], [_stage("a", title="Y")])
        self.assertTrue(any(x["field"] == "title" for x in d))

    def test_content_detects_summary_changed(self):
        d = _verify_stage_content([_stage("a", summary="X")], [_stage("a", summary="Y")])
        self.assertTrue(any(x["field"] == "summary" for x in d))

    def test_content_detects_key_concepts_changed(self):
        d = _verify_stage_content([_stage("a", key_concepts=("k1",))], [_stage("a", key_concepts=("k2",))])
        self.assertTrue(any(x["field"] == "key_concepts" for x in d))

    def test_clean_reorder_passes_all_verifiers(self):
        before = [_stage("a"), _stage("b")]
        after = [_stage("b"), _stage("a")]
        self.assertEqual(_verify_stage_coverage(before, after), [])
        self.assertEqual(_verify_stage_content(before, after), [])


class TestVerifierFallbackIntegration(unittest.TestCase):
    def test_verifier_failure_falls_back_to_original(self):
        stages = [_stage("a"), _stage("b")]
        fake = [{"type": "stage_content_changed", "stage_id": "a", "field": "title", "reason": "x"}]
        with mock.patch(
            "backend.utils.pedagogical_planner._verify_stage_content", return_value=fake
        ):
            res = apply_pedagogical_plan(stages, _plan(_move("b", after=None)))
        self.assertFalse(res.applied)
        self.assertEqual(res.fallback_reason, "safety_verifier_failed")
        self.assertEqual(_ids(res.stages), ["a", "b"])
        self.assertTrue(any(d["type"] == "stage_content_changed" for d in res.diagnostics))

    def test_successful_apply_preserves_metadata_and_content(self):
        stages = [
            _stage("a", source_ids=("x",), source_chunk_ids=("c1",)),
            _stage("b", source_chunk_ids=("c2",)),
        ]
        res = apply_pedagogical_plan(stages, _plan(_move("b", after=None)))
        self.assertTrue(res.applied)
        self.assertEqual(_ids(res.stages), ["b", "a"])
        anew = [s for s in res.stages if s["stage_id"] == "a"][0]
        bnew = [s for s in res.stages if s["stage_id"] == "b"][0]
        self.assertEqual(anew["source_ids"], ["x"])
        self.assertEqual(bnew["source_chunk_ids"], ["c2"])


class TestStageIdFallback(unittest.TestCase):
    def test_missing_stage_id_uses_index_identity(self):
        stages = [{"title": "A"}, {"title": "B"}]
        res = apply_pedagogical_plan(stages, _plan(_move("stage_1", after=None)))
        self.assertTrue(res.applied)
        self.assertEqual(res.stages[0]["title"], "B")

    def test_empty_stage_id_uses_index_identity(self):
        stages = [{"stage_id": "  ", "title": "A"}, {"stage_id": "  ", "title": "B"}]
        res = apply_pedagogical_plan(stages, _plan(_move("stage_1", after=None)))
        self.assertTrue(res.applied)
        self.assertEqual(res.stages[0]["title"], "B")


class TestPurity(unittest.TestCase):
    def test_parse_does_not_mutate_payload(self):
        payload = {"moves": [{"stage_id": "a", "after_stage_id": None, "reason": "r"}], "rationale": "x"}
        before = copy.deepcopy(payload)
        parse_pedagogical_plan(payload)
        self.assertEqual(payload, before)

    def test_apply_does_not_mutate_plan(self):
        plan = _plan(_move("b", after=None))
        before = copy.deepcopy(plan)
        apply_pedagogical_plan([_stage("a"), _stage("b")], plan)
        self.assertEqual(plan, before)

    def test_result_type(self):
        res = apply_pedagogical_plan([_stage("a")], _plan())
        self.assertIsInstance(res, PedagogicalPlanResult)


if __name__ == "__main__":
    unittest.main()
