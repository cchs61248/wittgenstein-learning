"""orchestrator canonicalize 整合測試（_apply_canonical_mappings helper + start_session）。

對應 spec: docs/superpowers/specs/2026-05-21-canonicalize-agent-design.md § 6
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.orchestrator.learning_orchestrator import (
    LearningOrchestrator,
    _apply_canonical_mappings,
)


class TestApplyCanonicalMappings(unittest.TestCase):
    def test_mapped_rewrites_key_concept(self):
        stages = [
            {"stage_id": 1, "key_concepts": ["巴菲特家世背景", "賠得起的優雅"]},
        ]
        mappings = [
            {"new_name": "巴菲特家世背景", "decision": "mapped",
             "canonical": "巴菲特神話", "reason": ""},
            {"new_name": "賠得起的優雅", "decision": "new",
             "canonical": None, "reason": ""},
        ]
        result = _apply_canonical_mappings(stages, mappings)
        self.assertEqual(result[0]["key_concepts"], ["巴菲特神話", "賠得起的優雅"])

    def test_new_keeps_original_name(self):
        stages = [{"stage_id": 1, "key_concepts": ["概念A"]}]
        mappings = [{"new_name": "概念A", "decision": "new",
                     "canonical": None, "reason": ""}]
        result = _apply_canonical_mappings(stages, mappings)
        self.assertEqual(result[0]["key_concepts"], ["概念A"])

    def test_unsure_keeps_original_name(self):
        stages = [{"stage_id": 1, "key_concepts": ["概念B"]}]
        mappings = [{"new_name": "概念B", "decision": "unsure",
                     "canonical": None, "reason": ""}]
        result = _apply_canonical_mappings(stages, mappings)
        self.assertEqual(result[0]["key_concepts"], ["概念B"])

    def test_same_concept_across_stages_consistent_mapping(self):
        stages = [
            {"stage_id": 1, "key_concepts": ["X", "Y"]},
            {"stage_id": 5, "key_concepts": ["X"]},
        ]
        mappings = [
            {"new_name": "X", "decision": "mapped",
             "canonical": "X_canonical", "reason": ""},
            {"new_name": "Y", "decision": "new",
             "canonical": None, "reason": ""},
        ]
        result = _apply_canonical_mappings(stages, mappings)
        self.assertEqual(result[0]["key_concepts"], ["X_canonical", "Y"])
        self.assertEqual(result[1]["key_concepts"], ["X_canonical"])

    def test_missing_mapping_keeps_original(self):
        stages = [{"stage_id": 1, "key_concepts": ["概念A", "概念B"]}]
        mappings = [{"new_name": "概念A", "decision": "mapped",
                     "canonical": "X", "reason": ""}]
        result = _apply_canonical_mappings(stages, mappings)
        self.assertEqual(result[0]["key_concepts"], ["X", "概念B"])

    def test_empty_stages_returns_empty(self):
        result = _apply_canonical_mappings([], [])
        self.assertEqual(result, [])


def _mk_orch():
    orch = LearningOrchestrator.__new__(LearningOrchestrator)
    orch.splitter = MagicMock()
    orch.splitter_verifier = MagicMock()
    orch.splitter_verifier.run = AsyncMock(return_value={
        "aligned": True, "missing_options": [],
        "issue_chunk_ids": [], "reason": "ok",
    })
    orch.canonicalizer = MagicMock()
    orch.drift_verifier = MagicMock()
    orch._pending_stages = None
    orch._pending_start_args = None
    orch._check_stage_quality = MagicMock(return_value=[])
    return orch


class TestOrchestratorCanonicalizeIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_canonicalize_called_after_splitter_writes_back_stages(self):
        orch = _mk_orch()
        orch.splitter.run = AsyncMock(return_value={
            "stages": [
                {"stage_id": 1, "key_concepts": ["巴菲特家世背景"],
                 "node_id": "1.1", "title": "..."},
            ],
            "summary": "",
        })
        orch.canonicalizer.run = AsyncMock(return_value={
            "mappings": [
                {"new_name": "巴菲特家世背景", "decision": "mapped",
                 "canonical": "巴菲特神話", "reason": ""},
            ],
        })

        captured_stages = {"value": None}

        async def _capture_create_pending(**kwargs):
            captured_stages["value"] = kwargs["stages"]

        with patch(
            "backend.orchestrator.learning_orchestrator.session_memory.create_generating_stub",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.learning_orchestrator.session_memory.insert_source_chunks",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.learning_orchestrator.session_memory.create_pending_session",
            new=AsyncMock(side_effect=_capture_create_pending),
        ), patch(
            "backend.orchestrator.learning_orchestrator.longterm_memory.get_concept_canonical_pool",
            new=AsyncMock(return_value=[
                {"concept_name": "巴菲特神話", "total_exposures": 6, "last_tested": ""},
            ]),
        ):
            await orch.start_session(
                session_id="s1", user_id="u1",
                source_chunks=[{"chunk_id": "c1", "text": "..."}],
                target_depth="standard",
                question_mode="multiple_choice",
                provider_name="claude", model_name="m",
                source_file_ids=["upl_A"],
                emit=AsyncMock(),
            )

        orch.canonicalizer.run.assert_awaited_once()
        self.assertEqual(
            captured_stages["value"][0]["key_concepts"],
            ["巴菲特神話"],
        )

    async def test_canonicalize_skipped_when_no_source_signature(self):
        orch = _mk_orch()
        orch.splitter.run = AsyncMock(return_value={
            "stages": [
                {"stage_id": 1, "key_concepts": ["A"],
                 "node_id": "1.1", "title": "..."},
            ],
            "summary": "",
        })
        orch.canonicalizer.run = AsyncMock()

        with patch(
            "backend.orchestrator.learning_orchestrator.session_memory.create_generating_stub",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.learning_orchestrator.session_memory.insert_source_chunks",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.learning_orchestrator.session_memory.create_pending_session",
            new=AsyncMock(),
        ):
            await orch.start_session(
                session_id="s1", user_id="u1",
                source_chunks=[{"chunk_id": "c1", "text": "..."}],
                target_depth="standard",
                question_mode="multiple_choice",
                provider_name="claude", model_name="m",
                source_file_ids=[],
                emit=AsyncMock(),
            )

        orch.canonicalizer.run.assert_not_awaited()

    async def test_canonicalize_failure_falls_back_to_splitter_stages(self):
        orch = _mk_orch()
        orch.splitter.run = AsyncMock(return_value={
            "stages": [
                {"stage_id": 1, "key_concepts": ["原始名"],
                 "node_id": "1.1", "title": "..."},
            ],
            "summary": "",
        })
        orch.canonicalizer.run = AsyncMock(side_effect=RuntimeError("LLM down"))

        captured_stages = {"value": None}

        async def _capture_create_pending(**kwargs):
            captured_stages["value"] = kwargs["stages"]

        with patch(
            "backend.orchestrator.learning_orchestrator.session_memory.create_generating_stub",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.learning_orchestrator.session_memory.insert_source_chunks",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.learning_orchestrator.session_memory.create_pending_session",
            new=AsyncMock(side_effect=_capture_create_pending),
        ), patch(
            "backend.orchestrator.learning_orchestrator.longterm_memory.get_concept_canonical_pool",
            new=AsyncMock(return_value=[]),
        ):
            await orch.start_session(
                session_id="s1", user_id="u1",
                source_chunks=[{"chunk_id": "c1", "text": "..."}],
                target_depth="standard",
                question_mode="multiple_choice",
                provider_name="claude", model_name="m",
                source_file_ids=["upl_A"],
                emit=AsyncMock(),
            )

        self.assertEqual(
            captured_stages["value"][0]["key_concepts"],
            ["原始名"],
        )

    async def test_canonicalize_skipped_when_stages_have_no_concepts(self):
        orch = _mk_orch()
        orch.splitter.run = AsyncMock(return_value={
            "stages": [
                {"stage_id": 1, "key_concepts": [],
                 "node_id": "1.1", "title": "..."},
            ],
            "summary": "",
        })
        orch.canonicalizer.run = AsyncMock()

        with patch(
            "backend.orchestrator.learning_orchestrator.session_memory.create_generating_stub",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.learning_orchestrator.session_memory.insert_source_chunks",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.learning_orchestrator.session_memory.create_pending_session",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.learning_orchestrator.longterm_memory.get_concept_canonical_pool",
            new=AsyncMock(return_value=[]),
        ):
            await orch.start_session(
                session_id="s1", user_id="u1",
                source_chunks=[{"chunk_id": "c1", "text": "..."}],
                target_depth="standard",
                question_mode="multiple_choice",
                provider_name="claude", model_name="m",
                source_file_ids=["upl_A"],
                emit=AsyncMock(),
            )

        orch.canonicalizer.run.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
