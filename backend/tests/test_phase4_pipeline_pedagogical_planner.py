"""Phase 4 / T4c: pipeline wiring + feature flag + activation gate.

T4c introduces the first seam that can touch the live curriculum pipeline. The
contract is deliberately conservative:

- flag default off → the planner never runs, stages are returned unchanged,
  and no Phase 4 utilities are called (bit-for-bit equivalence);
- flag on but activation gate fails → diagnostics-only, no LLM call, no reorder;
- flag on + gate pass → the planner agent may be called, and its plan applied
  only if every T4a safety verifier accepts it;
- any failure (LLM error, invalid plan, verifier rejection, utility exception)
  falls back to the original stage order and records warn-only diagnostics.

All inputs/agents are faked; no real network calls, no real pipeline run.
"""
import asyncio
import copy
import os
import unittest
from unittest import mock

from backend.orchestrator.curriculum_pipeline_v2 import (
    _is_cross_material_pedagogical_planner_enabled,
    _pedagogical_planner_gate_reasons,
    _maybe_apply_cross_material_pedagogical_planner,
)
from backend.agents.pedagogical_planner import PedagogicalPlannerAgentResult
from backend.utils.pedagogical_planner import (
    OrderingPlan,
    PrerequisiteGraph,
    PedagogicalPlan,
    PedagogicalPlanMove,
    build_stage_cards,
    build_prerequisite_graph,
    build_ordering_plan,
)


_FLAG = "CROSS_MATERIAL_PEDAGOGICAL_PLANNER"


def _stage(stage_id, title="T", summary="S", key_concepts=("k",),
           source_ids=(), source_stage_ids=()):
    return {
        "stage_id": stage_id,
        "title": title,
        "summary": summary,
        "key_concepts": list(key_concepts),
        "source_ids": list(source_ids),
        "source_stage_ids": list(source_stage_ids),
        "source_chunk_ids": ["chunk_0000"],
    }


def _reorderable_stages():
    """Six stages whose input order is the reverse of pedagogical order, so the
    deterministic ordering plan recommends a change (order_changed=True)."""
    titles = ["全書總結", "實戰應用", "進階主題", "核心概念", "基礎入門", "概論導讀"]
    return [_stage(f"s{i}", title=t) for i, t in enumerate(titles)]


def _chunks(n=30, sources=3):
    return [
        {"chunk_id": f"chunk_{i:04d}", "source_id": f"src_{i % sources}", "text": "x"}
        for i in range(n)
    ]


def _inputs(stages):
    cards, _ = build_stage_cards(stages)
    graph, _ = build_prerequisite_graph(cards)
    ordering = build_ordering_plan(cards, graph)
    return cards, graph, ordering


class _FakePlanner:
    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc
        self.calls = []

    async def propose_plan(self, *, stages, cards, graph, ordering_plan):
        self.calls.append({"stages": stages, "cards": cards,
                           "graph": graph, "ordering_plan": ordering_plan})
        if self._exc is not None:
            raise self._exc
        return self._result


class _FakeMeter:
    def __init__(self):
        self.calls = []

    def record(self, name):
        self.calls.append(name)


def _run(stages, chunks, *, same_material, planner, qw,
         session_id="sess_t4d", meter=None):
    return asyncio.run(
        _maybe_apply_cross_material_pedagogical_planner(
            session_id=session_id, stages=stages, chunks=chunks,
            same_material=same_material, planner_agent=planner,
            quality_warnings=qw, meter=meter,
        )
    )


class TestFlagHelper(unittest.TestCase):
    def test_default_off_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(_FLAG, None)
            self.assertFalse(_is_cross_material_pedagogical_planner_enabled())

    def test_accepts_truthy_values(self):
        for val in ("1", "true", "TRUE", "yes", "on", " On "):
            with mock.patch.dict(os.environ, {_FLAG: val}):
                self.assertTrue(_is_cross_material_pedagogical_planner_enabled(), val)

    def test_rejects_other_values(self):
        for val in ("0", "false", "no", "off", "", "enable"):
            with mock.patch.dict(os.environ, {_FLAG: val}):
                self.assertFalse(_is_cross_material_pedagogical_planner_enabled(), val)


class TestGateReasons(unittest.TestCase):
    def _ok(self, **over):
        base = dict(
            same_material=False, chunk_count=30, stage_count=6, source_count=3,
            ordering_plan=OrderingPlan((), (), True, ()),
            graph=PrerequisiteGraph((), (), (), False),
        )
        base.update(over)
        return _pedagogical_planner_gate_reasons(**base)

    def test_all_criteria_met_passes(self):
        self.assertEqual(self._ok(), [])

    def test_rejects_same_material(self):
        self.assertIn("same_material", self._ok(same_material=True))

    def test_rejects_insufficient_chunks(self):
        self.assertIn("insufficient_chunks", self._ok(chunk_count=29))

    def test_rejects_insufficient_stages(self):
        self.assertIn("insufficient_stages", self._ok(stage_count=5))

    def test_rejects_insufficient_sources(self):
        self.assertIn("insufficient_sources", self._ok(source_count=2))

    def test_rejects_no_order_change(self):
        self.assertIn("no_order_change_recommended",
                      self._ok(ordering_plan=OrderingPlan((), (), False, ())))

    def test_rejects_prerequisite_cycle(self):
        self.assertIn("prerequisite_cycle",
                      self._ok(graph=PrerequisiteGraph((), (), (), True)))


class TestFixtureSanity(unittest.TestCase):
    def test_reorderable_fixture_recommends_change(self):
        _, _, ordering = _inputs(_reorderable_stages())
        self.assertTrue(ordering.order_changed)

    def test_reorderable_fixture_has_no_cycle(self):
        _, graph, _ = _inputs(_reorderable_stages())
        self.assertFalse(graph.has_cycle)

    def test_gate_passes_for_full_fixture(self):
        stages = _reorderable_stages()
        _, graph, ordering = _inputs(stages)
        reasons = _pedagogical_planner_gate_reasons(
            same_material=False, chunk_count=30, stage_count=len(stages),
            source_count=3, ordering_plan=ordering, graph=graph,
        )
        self.assertEqual(reasons, [])


class TestFlagOff(unittest.TestCase):
    def test_returns_stages_unchanged(self):
        stages = _reorderable_stages()
        before = copy.deepcopy(stages)
        planner = _FakePlanner()
        qw = {}
        with mock.patch.dict(os.environ, {_FLAG: "0"}):
            out = _run(stages, _chunks(), same_material=False, planner=planner, qw=qw)
        self.assertEqual([s["stage_id"] for s in out],
                         [s["stage_id"] for s in before])

    def test_writes_no_warning(self):
        qw = {}
        planner = _FakePlanner()
        with mock.patch.dict(os.environ, {_FLAG: "0"}):
            _run(_reorderable_stages(), _chunks(), same_material=False,
                 planner=planner, qw=qw)
        self.assertEqual(qw, {})

    def test_does_not_call_planner(self):
        planner = _FakePlanner()
        with mock.patch.dict(os.environ, {_FLAG: "0"}):
            _run(_reorderable_stages(), _chunks(), same_material=False,
                 planner=planner, qw={})
        self.assertEqual(planner.calls, [])

    def test_flag_off_does_not_call_phase4_utilities(self):
        """bit-for-bit guard: the short-circuit must precede every Phase 4
        utility call, so nobody can later hoist a build_* call above the flag
        check without this test going red."""
        planner = _FakePlanner()
        mod = "backend.orchestrator.curriculum_pipeline_v2"
        with mock.patch.dict(os.environ, {_FLAG: "0"}), \
             mock.patch(f"{mod}.build_stage_cards",
                        side_effect=AssertionError("build_stage_cards ran while flag off")), \
             mock.patch(f"{mod}.build_prerequisite_graph",
                        side_effect=AssertionError("build_prerequisite_graph ran while flag off")), \
             mock.patch(f"{mod}.build_ordering_plan",
                        side_effect=AssertionError("build_ordering_plan ran while flag off")):
            out = _run(_reorderable_stages(), _chunks(), same_material=False,
                       planner=planner, qw={})
        self.assertEqual([s["stage_id"] for s in out],
                         [s["stage_id"] for s in _reorderable_stages()])
        self.assertEqual(planner.calls, [])


class TestGateFailDiagnosticsOnly(unittest.TestCase):
    def _run_same_material(self):
        qw = {}
        planner = _FakePlanner()
        with mock.patch.dict(os.environ, {_FLAG: "1"}):
            out = _run(_reorderable_stages(), _chunks(), same_material=True,
                       planner=planner, qw=qw)
        return out, planner, qw

    def test_writes_single_warning(self):
        _, _, qw = self._run_same_material()
        self.assertIn("cross_material_pedagogical_planner", qw)

    def test_planner_mode_diagnostics_only(self):
        _, _, qw = self._run_same_material()
        w = qw["cross_material_pedagogical_planner"]
        self.assertEqual(w["planner_mode"], "diagnostics_only")
        self.assertFalse(w["gate_passed"])
        self.assertIn("same_material", w["gate_reasons"])

    def test_does_not_call_llm(self):
        _, planner, _ = self._run_same_material()
        self.assertEqual(planner.calls, [])

    def test_warning_includes_diagnostic_sections(self):
        _, _, qw = self._run_same_material()
        w = qw["cross_material_pedagogical_planner"]
        for key in ("stage_card_diagnostics", "graph_diagnostics",
                    "ordering_diagnostics", "current_stage_ids",
                    "recommended_stage_ids", "order_changed"):
            self.assertIn(key, w)

    def test_stage_order_unchanged(self):
        out, _, _ = self._run_same_material()
        self.assertEqual([s["stage_id"] for s in out],
                         [s["stage_id"] for s in _reorderable_stages()])


class TestGatePassApplied(unittest.TestCase):
    def _run_applied(self, move_stage="s5"):
        plan = PedagogicalPlan(
            moves=(PedagogicalPlanMove(stage_id=move_stage, after_stage_id=None,
                                       reason="lead with overview"),),
            rationale="overview first",
        )
        planner = _FakePlanner(
            result=PedagogicalPlannerAgentResult(plan=plan, diagnostics=())
        )
        qw = {}
        with mock.patch.dict(os.environ, {_FLAG: "1"}):
            out = _run(_reorderable_stages(), _chunks(), same_material=False,
                       planner=planner, qw=qw)
        return out, planner, qw

    def test_calls_llm_once(self):
        _, planner, _ = self._run_applied()
        self.assertEqual(len(planner.calls), 1)

    def test_planner_mode_applied(self):
        _, _, qw = self._run_applied()
        self.assertEqual(qw["cross_material_pedagogical_planner"]["planner_mode"],
                         "applied")

    def test_stages_reordered(self):
        out, _, _ = self._run_applied(move_stage="s5")
        self.assertEqual(out[0]["stage_id"], "s5")

    def test_warning_has_applied_stage_ids(self):
        out, _, qw = self._run_applied()
        w = qw["cross_material_pedagogical_planner"]
        self.assertEqual(w["applied_stage_ids"], [s["stage_id"] for s in out])

    def test_no_fallback_reason_when_applied(self):
        _, _, qw = self._run_applied()
        self.assertNotIn("fallback_reason", qw["cross_material_pedagogical_planner"])


class TestGatePassAgentFailure(unittest.TestCase):
    def test_plan_none_falls_back(self):
        planner = _FakePlanner(result=PedagogicalPlannerAgentResult(
            plan=None, diagnostics=({"type": "pedagogical_planner_invalid_json"},)))
        qw = {}
        with mock.patch.dict(os.environ, {_FLAG: "1"}):
            out = _run(_reorderable_stages(), _chunks(), same_material=False,
                       planner=planner, qw=qw)
        w = qw["cross_material_pedagogical_planner"]
        self.assertEqual(w["planner_mode"], "fallback")
        self.assertEqual(w["fallback_reason"], "planner_agent_failed")
        self.assertEqual([s["stage_id"] for s in out],
                         [s["stage_id"] for s in _reorderable_stages()])

    def test_agent_diagnostics_preserved(self):
        planner = _FakePlanner(result=PedagogicalPlannerAgentResult(
            plan=None, diagnostics=({"type": "pedagogical_planner_llm_error"},)))
        qw = {}
        with mock.patch.dict(os.environ, {_FLAG: "1"}):
            _run(_reorderable_stages(), _chunks(), same_material=False,
                 planner=planner, qw=qw)
        w = qw["cross_material_pedagogical_planner"]
        self.assertEqual(w["agent_diagnostics"][0]["type"],
                         "pedagogical_planner_llm_error")


class TestGatePassApplierFailure(unittest.TestCase):
    def test_invalid_move_falls_back(self):
        plan = PedagogicalPlan(
            moves=(PedagogicalPlanMove(stage_id="does_not_exist",
                                       after_stage_id=None, reason="r"),),
            rationale="bad",
        )
        planner = _FakePlanner(
            result=PedagogicalPlannerAgentResult(plan=plan, diagnostics=()))
        qw = {}
        with mock.patch.dict(os.environ, {_FLAG: "1"}):
            out = _run(_reorderable_stages(), _chunks(), same_material=False,
                       planner=planner, qw=qw)
        w = qw["cross_material_pedagogical_planner"]
        self.assertEqual(w["planner_mode"], "fallback")
        self.assertIsNotNone(w.get("fallback_reason"))
        self.assertEqual([s["stage_id"] for s in out],
                         [s["stage_id"] for s in _reorderable_stages()])


class TestExceptionSafety(unittest.TestCase):
    def test_planner_exception_error_fallback(self):
        planner = _FakePlanner(exc=RuntimeError("boom"))
        qw = {}
        with mock.patch.dict(os.environ, {_FLAG: "1"}):
            out = _run(_reorderable_stages(), _chunks(), same_material=False,
                       planner=planner, qw=qw)
        w = qw["cross_material_pedagogical_planner"]
        self.assertEqual(w["planner_mode"], "error_fallback")
        self.assertEqual([s["stage_id"] for s in out],
                         [s["stage_id"] for s in _reorderable_stages()])


def _applied_planner(move_stage="s5"):
    plan = PedagogicalPlan(
        moves=(PedagogicalPlanMove(stage_id=move_stage, after_stage_id=None,
                                   reason="lead with overview"),),
        rationale="overview first",
    )
    return _FakePlanner(result=PedagogicalPlannerAgentResult(plan=plan, diagnostics=()))


def _run_on(stages, *, same_material=False, planner=None, meter=None,
            session_id="sess_t4d"):
    qw = {}
    planner = planner if planner is not None else _FakePlanner()
    with mock.patch.dict(os.environ, {_FLAG: "1"}):
        out = _run(stages, _chunks(), same_material=same_material,
                   planner=planner, qw=qw, meter=meter, session_id=session_id)
    return out, qw["cross_material_pedagogical_planner"]


class TestObservabilitySchema(unittest.TestCase):
    def test_warning_has_schema_version_and_run_id(self):
        _, w = _run_on(_reorderable_stages(), planner=_applied_planner())
        self.assertEqual(w["schema_version"], 1)
        self.assertTrue(w["run_id"].startswith("phase4_sess_t4d_"))

    def test_run_id_omits_user_id(self):
        _, w = _run_on(_reorderable_stages(), planner=_applied_planner())
        self.assertIn("6st", w["run_id"])
        self.assertIn("30ch", w["run_id"])


class TestObservabilityAttemptFlags(unittest.TestCase):
    def test_diagnostics_only_flags_false(self):
        _, w = _run_on(_reorderable_stages(), same_material=True)
        self.assertFalse(w["agent_called"])
        self.assertFalse(w["apply_attempted"])

    def test_applied_flags_true(self):
        _, w = _run_on(_reorderable_stages(), planner=_applied_planner())
        self.assertTrue(w["agent_called"])
        self.assertTrue(w["apply_attempted"])

    def test_plan_none_agent_true_apply_false(self):
        planner = _FakePlanner(result=PedagogicalPlannerAgentResult(
            plan=None, diagnostics=({"type": "pedagogical_planner_invalid_json"},)))
        _, w = _run_on(_reorderable_stages(), planner=planner)
        self.assertTrue(w["agent_called"])
        self.assertFalse(w["apply_attempted"])

    def test_applier_failure_both_true(self):
        plan = PedagogicalPlan(
            moves=(PedagogicalPlanMove(stage_id="does_not_exist",
                                       after_stage_id=None, reason="r"),),
            rationale="bad")
        planner = _FakePlanner(
            result=PedagogicalPlannerAgentResult(plan=plan, diagnostics=()))
        _, w = _run_on(_reorderable_stages(), planner=planner)
        self.assertTrue(w["agent_called"])
        self.assertTrue(w["apply_attempted"])


class TestObservabilityStageOrder(unittest.TestCase):
    def test_applied_before_after_differ(self):
        _, w = _run_on(_reorderable_stages(), planner=_applied_planner("s5"))
        self.assertEqual(w["stage_order_before"][0], "s0")
        self.assertEqual(w["stage_order_after"][0], "s5")

    def test_fallback_after_equals_before(self):
        planner = _FakePlanner(result=PedagogicalPlannerAgentResult(
            plan=None, diagnostics=()))
        _, w = _run_on(_reorderable_stages(), planner=planner)
        self.assertEqual(w["stage_order_after"], w["stage_order_before"])

    def test_diagnostics_only_after_equals_before(self):
        _, w = _run_on(_reorderable_stages(), same_material=True)
        self.assertEqual(w["stage_order_after"], w["stage_order_before"])


class TestObservabilityRedactedPlan(unittest.TestCase):
    def test_applied_plan_move_count_and_redacted(self):
        _, w = _run_on(_reorderable_stages(), planner=_applied_planner("s5"))
        self.assertEqual(w["plan_move_count"], 1)
        self.assertEqual(w["plan_moves_redacted"],
                         [{"stage_id": "s5", "after_stage_id": None}])

    def test_redacted_plan_has_no_reason(self):
        _, w = _run_on(_reorderable_stages(), planner=_applied_planner("s5"))
        self.assertNotIn("reason", w["plan_moves_redacted"][0])

    def test_no_plan_move_count_zero(self):
        _, w = _run_on(_reorderable_stages(), same_material=True)
        self.assertEqual(w["plan_move_count"], 0)
        self.assertEqual(w["plan_moves_redacted"], [])


class TestObservabilityMeter(unittest.TestCase):
    def test_records_planner_on_success(self):
        meter = _FakeMeter()
        _run_on(_reorderable_stages(), planner=_applied_planner(), meter=meter)
        self.assertEqual(meter.calls, ["PedagogicalPlannerAgent"])

    def test_records_planner_when_plan_none(self):
        meter = _FakeMeter()
        planner = _FakePlanner(result=PedagogicalPlannerAgentResult(
            plan=None, diagnostics=()))
        _run_on(_reorderable_stages(), planner=planner, meter=meter)
        self.assertEqual(meter.calls, ["PedagogicalPlannerAgent"])

    def test_does_not_record_when_gate_fails(self):
        meter = _FakeMeter()
        _run_on(_reorderable_stages(), same_material=True, meter=meter)
        self.assertEqual(meter.calls, [])

    def test_does_not_record_when_agent_raises(self):
        meter = _FakeMeter()
        planner = _FakePlanner(exc=RuntimeError("boom"))
        _run_on(_reorderable_stages(), planner=planner, meter=meter)
        self.assertEqual(meter.calls, [])

    def test_meter_none_is_safe(self):
        # default meter=None must not raise on the success path
        _run_on(_reorderable_stages(), planner=_applied_planner(), meter=None)


class TestObservabilityErrorFallback(unittest.TestCase):
    def test_error_fallback_keeps_schema_and_order(self):
        planner = _FakePlanner(exc=RuntimeError("boom"))
        _, w = _run_on(_reorderable_stages(), planner=planner)
        self.assertEqual(w["schema_version"], 1)
        self.assertTrue(w["run_id"].startswith("phase4_sess_t4d_"))
        self.assertEqual(w["planner_mode"], "error_fallback")
        self.assertEqual(w["stage_order_after"], w["stage_order_before"])

    def test_error_fallback_reports_attempt_flags(self):
        planner = _FakePlanner(exc=RuntimeError("boom"))
        _, w = _run_on(_reorderable_stages(), planner=planner)
        # exception is raised inside the agent await → agent_called True,
        # apply never reached → apply_attempted False
        self.assertTrue(w["agent_called"])
        self.assertFalse(w["apply_attempted"])


if __name__ == "__main__":
    unittest.main()
