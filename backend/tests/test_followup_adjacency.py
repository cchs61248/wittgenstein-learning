"""T-FOLLOWUP-ADJACENCY: deterministic follow-up stage adjacency invariant.

Follow-up ("（續 N）") stages must sit immediately after their base stage, after any
earlier-batch siblings. The reorder is follow-up-only: base/non-follow-up stages keep
their received relative order (so it is safe to run after a pedagogical reorder without
clobbering the planner's base order).
"""
import copy
import unittest

from backend.utils.small_curriculum import (
    enforce_followup_adjacency_only,
    followup_adjacency_violations,
    finalize_curriculum_stages,
)
from backend.orchestrator.curriculum_pipeline_v2 import (
    _renumber_finalized_stages_after_pedagogical_reorder,
)


def _stage(title, chunks=None, kc=None, **extra):
    s = {
        "title": title,
        "source_chunk_ids": list(chunks or []),
        "key_concepts": list(kc or []),
    }
    s.update(extra)
    return s


def _titles(stages):
    return [s["title"] for s in stages]


class TestEnforceFollowupAdjacencyOnly(unittest.TestCase):
    def test_pulls_followups_adjacent_preserving_base_order(self):
        stages = [
            _stage("A"),
            _stage("B"),
            _stage("A（續 2）"),
            _stage("B（續 2）"),
            _stage("A（續 3）"),
        ]
        out = enforce_followup_adjacency_only(stages)
        self.assertEqual(
            _titles(out),
            ["A", "A（續 2）", "A（續 3）", "B", "B（續 2）"],
        )

    def test_sibling_followups_sorted_by_batch(self):
        stages = [_stage("A"), _stage("A（續 3）"), _stage("A（續 2）")]
        out = enforce_followup_adjacency_only(stages)
        self.assertEqual(_titles(out), ["A", "A（續 2）", "A（續 3）"])

    def test_base_relative_order_is_not_resorted(self):
        # base order received as B, A must stay B, A (NOT alphabetised / chunk-sorted)
        stages = [_stage("B"), _stage("A"), _stage("A（續 2）"), _stage("B（續 2）")]
        out = enforce_followup_adjacency_only(stages)
        self.assertEqual(_titles(out), ["B", "B（續 2）", "A", "A（續 2）"])

    def test_unmatched_followup_degrades_to_tail(self):
        stages = [_stage("A"), _stage("X（續 2）"), _stage("B")]
        out = enforce_followup_adjacency_only(stages)
        self.assertEqual(_titles(out), ["A", "B", "X（續 2）"])

    def test_no_followups_is_noop(self):
        stages = [_stage("A"), _stage("B"), _stage("C")]
        out = enforce_followup_adjacency_only(stages)
        self.assertEqual(_titles(out), ["A", "B", "C"])

    def test_does_not_mutate_content_or_coverage(self):
        stages = [
            _stage("A", chunks=["chunk_0000"], kc=["k1"]),
            _stage("B", chunks=["chunk_0003"], kc=["k2"]),
            _stage("A（續 2）", chunks=["chunk_0001"], kc=["k1"]),
        ]
        before = copy.deepcopy(stages)
        out = enforce_followup_adjacency_only(stages)
        # same multiset of stages, same chunk coverage, content untouched
        self.assertEqual(len(out), 3)
        cov_in = {c for s in stages for c in s["source_chunk_ids"]}
        cov_out = {c for s in out for c in s["source_chunk_ids"]}
        self.assertEqual(cov_in, cov_out)
        self.assertEqual(stages, before)  # input list dicts untouched
        a_follow = next(s for s in out if s["title"] == "A（續 2）")
        self.assertEqual(a_follow["source_chunk_ids"], ["chunk_0001"])
        self.assertEqual(a_follow["key_concepts"], ["k1"])


class TestFollowupAdjacencyViolations(unittest.TestCase):
    def test_detects_followup_split_from_base(self):
        stages = [_stage("A"), _stage("B"), _stage("A（續 2）")]
        v = followup_adjacency_violations(stages)
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0]["base_title"], "A")

    def test_no_violation_when_adjacent(self):
        stages = [_stage("A"), _stage("A（續 2）"), _stage("B")]
        self.assertEqual(followup_adjacency_violations(stages), [])

    def test_unmatched_followup_not_flagged(self):
        # no base "X" -> degraded, not a violation
        stages = [_stage("A"), _stage("X（續 2）")]
        self.assertEqual(followup_adjacency_violations(stages), [])

    def test_detects_out_of_order_siblings(self):
        # adjacent to base/sibling but batch descending -> violation
        stages = [_stage("A"), _stage("A（續 3）"), _stage("A（續 2）")]
        v = followup_adjacency_violations(stages)
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0]["reason"], "followup_batch_out_of_order")
        self.assertEqual(v[0]["base_title"], "A")

    def test_enforcer_output_has_no_violations(self):
        stages = [
            _stage("A"), _stage("B"),
            _stage("A（續 3）"), _stage("A（續 2）"), _stage("B（續 2）"),
        ]
        self.assertEqual(
            followup_adjacency_violations(enforce_followup_adjacency_only(stages)),
            [],
        )


class TestFinalizeRepairsFollowupScatter(unittest.TestCase):
    def test_finalize_keeps_followup_adjacent_after_chunk_sort(self):
        # base "維護" aggregates an early chunk (0000) + late chunk (0010);
        # its （續 2）only has a late chunk (0011); an unrelated base "前端" sits at 0003.
        # pure chunk-order sort would scatter: 維護(0000) 前端(0003) 維護續2(0011).
        chunks = [
            {"chunk_id": f"chunk_{i:04d}", "text": "正文", "order_index": i}
            for i in range(12)
        ]
        stages = [
            _stage("維護", chunks=["chunk_0000", "chunk_0010"], kc=["m"]),
            _stage("前端", chunks=["chunk_0003"], kc=["f"]),
            _stage("維護（續 2）", chunks=["chunk_0011"], kc=["m"],
                   kind="follow_up_orphan"),
        ]
        out = finalize_curriculum_stages(stages, chunks)
        self.assertEqual(_titles(out), ["維護", "維護（續 2）", "前端"])
        self.assertEqual(followup_adjacency_violations(out), [])
        # renumber still canonical
        self.assertEqual([s["stage_id"] for s in out], [1, 2, 3])

    def test_finalize_noop_order_when_no_followups(self):
        chunks = [
            {"chunk_id": f"chunk_{i:04d}", "text": "正文", "order_index": i}
            for i in range(3)
        ]
        stages = [
            _stage("第二", chunks=["chunk_0001"], kc=["b"]),
            _stage("第一", chunks=["chunk_0000"], kc=["a"]),
        ]
        out = finalize_curriculum_stages(stages, chunks)
        # pure chunk-order sort still applies (no follow-ups to protect)
        self.assertEqual(_titles(out), ["第一", "第二"])


class TestRenumberHelperEnforcesAdjacency(unittest.TestCase):
    def test_planner_applied_order_followups_pulled_to_base(self):
        # simulate a planner-applied order that scattered follow-ups:
        # B, A, A（續 2）, B（續 2） -> B, B（續 2）, A, A（續 2）
        stages = [
            _stage("B", chunks=["chunk_0002"]),
            _stage("A", chunks=["chunk_0000"]),
            _stage("A（續 2）", chunks=["chunk_0001"], kind="follow_up_orphan"),
            _stage("B（續 2）", chunks=["chunk_0003"], kind="follow_up_orphan"),
        ]
        out = _renumber_finalized_stages_after_pedagogical_reorder(stages)
        self.assertEqual(_titles(out), ["B", "B（續 2）", "A", "A（續 2）"])
        self.assertEqual([s["stage_id"] for s in out], [1, 2, 3, 4])
        self.assertEqual(followup_adjacency_violations(out), [])

    def test_does_not_mutate_input(self):
        stages = [_stage("Z", chunks=["c0"]), _stage("A", chunks=["c1"])]
        before = copy.deepcopy(stages)
        _renumber_finalized_stages_after_pedagogical_reorder(stages)
        self.assertEqual(stages, before)


if __name__ == "__main__":
    unittest.main()
