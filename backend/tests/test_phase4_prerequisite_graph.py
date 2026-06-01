"""Phase 4 / T2: deterministic prerequisite graph builder.

Builds sparse high-confidence prerequisite edges from keyword seed rules and
role-based nearest-prior rules, computes weakly connected clusters, and emits
warn-only diagnostics for cycles and unrelated stage clusters.

Pure / deterministic / warn-only: no LLM, no pipeline wiring, no reorder.
"""
import copy
import unittest

from backend.utils.pedagogical_planner import (
    StageCard,
    PrerequisiteEdge,
    PrerequisiteGraph,
    build_prerequisite_graph,
    _detect_cycle,
)


def _card(stage_id, role="core", title="", summary="", key_concepts=(), difficulty=3):
    return StageCard(
        stage_id=str(stage_id),
        stage_index=0,
        source_ids=(),
        source_stage_ids=(),
        title=title,
        summary=summary,
        key_concepts=tuple(key_concepts),
        role=role,
        difficulty=difficulty,
        role_reason="test",
        difficulty_reason="test",
    )


def _pairs(graph):
    return [(e.before_stage_id, e.after_stage_id) for e in graph.edges]


def _edge(before, after):
    return PrerequisiteEdge(before, after, "test", "high")


class TestGraphBasics(unittest.TestCase):
    def test_builds_graph_with_all_stage_ids(self):
        g, _ = build_prerequisite_graph([_card("a"), _card("b"), _card("c")])
        self.assertEqual(g.stage_ids, ("a", "b", "c"))

    def test_returns_tuple_structures(self):
        g, _ = build_prerequisite_graph([_card("a"), _card("b")])
        self.assertIsInstance(g.stage_ids, tuple)
        self.assertIsInstance(g.edges, tuple)
        self.assertIsInstance(g.clusters, tuple)

    def test_no_cards_empty_graph(self):
        g, diags = build_prerequisite_graph([])
        self.assertEqual(g.stage_ids, ())
        self.assertEqual(g.edges, ())
        self.assertEqual(g.clusters, ())
        self.assertFalse(g.has_cycle)
        self.assertEqual(diags, [])

    def test_deterministic_output(self):
        cards = [
            _card("p", title="提示詞工程", key_concepts=("提示工程",)),
            _card("cot", title="思維鏈 CoT"),
            _card("s", role="summary", title="總結"),
        ]
        self.assertEqual(build_prerequisite_graph(cards), build_prerequisite_graph(cards))


class TestKeywordEdges(unittest.TestCase):
    def test_embedding_before_vector_database(self):
        g, _ = build_prerequisite_graph(
            [_card("emb", title="嵌入 Embedding 表示"), _card("vdb", title="向量資料庫 Vector Database")]
        )
        self.assertIn(("emb", "vdb"), _pairs(g))
        e = [x for x in g.edges if (x.before_stage_id, x.after_stage_id) == ("emb", "vdb")][0]
        self.assertEqual(e.reason, "keyword_seed:embedding->vector_database")
        self.assertEqual(e.confidence, "high")

    def test_vector_database_before_retrieval(self):
        g, _ = build_prerequisite_graph(
            [_card("vdb", title="向量資料庫"), _card("ret", title="檢索 Retrieval")]
        )
        self.assertIn(("vdb", "ret"), _pairs(g))

    def test_retrieval_before_rag(self):
        g, _ = build_prerequisite_graph(
            [_card("ret", title="檢索 Retrieval"), _card("rag", title="檢索增強生成 RAG")]
        )
        self.assertIn(("ret", "rag"), _pairs(g))

    def test_prompting_before_cot(self):
        g, _ = build_prerequisite_graph(
            [_card("p", title="提示詞工程", key_concepts=("提示工程",)), _card("cot", title="思維鏈 CoT")]
        )
        self.assertIn(("p", "cot"), _pairs(g))

    def test_keyword_matching_reads_summary_and_key_concepts(self):
        g, _ = build_prerequisite_graph(
            [_card("a", summary="本節介紹 embedding 與向量表示"), _card("b", key_concepts=("向量資料庫",))]
        )
        self.assertIn(("a", "b"), _pairs(g))


class TestRoleEdges(unittest.TestCase):
    def test_foundation_core_before_application(self):
        g, _ = build_prerequisite_graph([_card("f", role="foundation"), _card("app", role="application")])
        self.assertIn(("f", "app"), _pairs(g))
        e = [x for x in g.edges if (x.before_stage_id, x.after_stage_id) == ("f", "app")][0]
        self.assertEqual(e.reason, "role:foundation_core_before_application")

    def test_core_before_summary(self):
        g, _ = build_prerequisite_graph([_card("c", role="core"), _card("s", role="summary")])
        self.assertIn(("c", "s"), _pairs(g))
        e = [x for x in g.edges if (x.before_stage_id, x.after_stage_id) == ("c", "s")][0]
        self.assertEqual(e.reason, "role:teaching_before_summary")

    def test_application_advanced_before_advanced(self):
        g, _ = build_prerequisite_graph([_card("app", role="application"), _card("adv", role="advanced")])
        self.assertIn(("app", "adv"), _pairs(g))

    def test_reference_creates_no_edges(self):
        g, _ = build_prerequisite_graph([_card("r", role="reference"), _card("app", role="application")])
        self.assertEqual(g.edges, ())

    def test_unknown_creates_no_edges(self):
        g, _ = build_prerequisite_graph([_card("u", role="unknown"), _card("app", role="application")])
        self.assertEqual(g.edges, ())


class TestDedupAndSparsity(unittest.TestCase):
    def test_duplicate_edges_deduped_keeps_keyword_reason(self):
        g, _ = build_prerequisite_graph(
            [_card("c", role="core", title="檢索 retrieval"),
             _card("s", role="summary", title="檢索增強生成 RAG 總結")]
        )
        matched = [x for x in g.edges if (x.before_stage_id, x.after_stage_id) == ("c", "s")]
        self.assertEqual(len(matched), 1)
        self.assertTrue(matched[0].reason.startswith("keyword_seed:retrieval->rag"))

    def test_role_edges_are_nearest_prior_not_complete(self):
        g, _ = build_prerequisite_graph(
            [_card("f1", role="foundation"), _card("f2", role="foundation"), _card("app", role="application")]
        )
        self.assertIn(("f2", "app"), _pairs(g))
        self.assertNotIn(("f1", "app"), _pairs(g))


class TestCycleDetection(unittest.TestCase):
    def test_detect_cycle_finds_cycle(self):
        path = _detect_cycle(("a", "b", "c"), [_edge("a", "b"), _edge("b", "c"), _edge("c", "a")])
        self.assertTrue(path)

    def test_detect_cycle_acyclic_returns_empty(self):
        path = _detect_cycle(("a", "b", "c"), [_edge("a", "b"), _edge("b", "c")])
        self.assertEqual(path, [])

    def test_build_detects_cycle_from_symmetric_keywords(self):
        g, diags = build_prerequisite_graph(
            [_card("x", title="embedding 向量資料庫"), _card("y", title="embedding 向量資料庫")]
        )
        self.assertTrue(g.has_cycle)
        self.assertIn("prerequisite_cycle_detected", [d["type"] for d in diags])

    def test_acyclic_build_has_no_cycle(self):
        g, _ = build_prerequisite_graph([_card("f", role="foundation"), _card("app", role="application")])
        self.assertFalse(g.has_cycle)


class TestClusters(unittest.TestCase):
    def test_connected_stages_form_one_cluster(self):
        g, _ = build_prerequisite_graph([_card("f", role="foundation"), _card("app", role="application")])
        self.assertEqual(g.clusters, (("f", "app"),))

    def test_disconnected_stages_form_multiple_clusters(self):
        g, _ = build_prerequisite_graph([_card("a", role="core"), _card("b", role="core")])
        self.assertEqual(len(g.clusters), 2)

    def test_unrelated_clusters_diagnostic_emitted(self):
        _, diags = build_prerequisite_graph([_card("a", role="core"), _card("b", role="core")])
        d = [x for x in diags if x["type"] == "unrelated_source_clusters"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["cluster_count"], 2)
        self.assertEqual(d[0]["clusters"], [["a"], ["b"]])
        self.assertEqual(d[0]["reason"], "multiple_weakly_connected_stage_clusters")

    def test_cluster_ordering_deterministic(self):
        g, _ = build_prerequisite_graph(
            [_card("a", title="檢索"), _card("b", role="core"), _card("c", title="檢索增強生成 RAG")]
        )
        self.assertEqual(g.clusters, (("a", "c"), ("b",)))

    def test_single_cluster_emits_no_unrelated_diagnostic(self):
        _, diags = build_prerequisite_graph([_card("f", role="foundation"), _card("app", role="application")])
        self.assertNotIn("unrelated_source_clusters", [d["type"] for d in diags])


class TestPurity(unittest.TestCase):
    def test_does_not_mutate_cards(self):
        cards = [_card("f", role="foundation"), _card("app", role="application")]
        before = copy.deepcopy(cards)
        build_prerequisite_graph(cards)
        self.assertEqual(cards, before)


if __name__ == "__main__":
    unittest.main()
