"""orchestrator splitter verify loop 整合測試（L3 reroll 路徑 + L4 回歸）。

對應 spec: docs/superpowers/specs/2026-05-21-splitter-verifier-agent-design.md § 3 + § 5-6
方案 C：outline 引導 + repair_plan reroll + MAX_SPLITTER_VERIFY_RETRIES=2。
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.orchestrator.learning_orchestrator import (
    LearningOrchestrator,
    MAX_SPLITTER_VERIFY_RETRIES,
    SplitterVerificationRejected,
)


def _default_outline():
    return {
        "required_stage_titles": ["案例：A"],
        "named_cases": ["A"],
        "framework_sections": [],
        "summary_sections": [],
        "must_cover_chunks": ["c1"],
    }


def _mk_orch():
    """建立 LearningOrchestrator instance、所有 agent 與 IO 都 mock。"""
    orch = LearningOrchestrator.__new__(LearningOrchestrator)
    orch.content_outliner = MagicMock()
    orch.content_outliner.run = AsyncMock(return_value=_default_outline())
    orch.splitter = MagicMock()
    orch.splitter_verifier = MagicMock()
    orch.splitter_verifier.run = AsyncMock(return_value={
        "aligned": True, "missing_options": [],
        "issue_chunk_ids": [], "reason": "ok",
    })
    orch.canonicalizer = MagicMock()
    orch.canonicalizer.run = AsyncMock(return_value={"mappings": []})
    orch.drift_verifier = MagicMock()
    orch._pending_stages = None
    orch._pending_start_args = None
    orch._check_stage_quality = MagicMock(return_value=[])
    return orch


def _stages_a():
    return [{"stage_id": 1, "key_concepts": ["A"], "node_id": "1.1",
             "title": "stage A", "source_chunk_ids": ["c1"]}]


def _stages_b():
    return [{"stage_id": 1, "key_concepts": ["B"], "node_id": "1.1",
             "title": "stage B (reroll)", "source_chunk_ids": ["c1"]}]


async def _run_start(orch, source_file_ids=None, source_chunks=None, *, expect_reject=False):
    """共用：跑 start_session、回傳 captured stages（最終寫入 DB 的）。"""
    captured = {"stages": None}

    async def _capture_create_pending(**kwargs):
        captured["stages"] = kwargs["stages"]

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
        "backend.orchestrator.learning_orchestrator.session_memory.abandon_generating_stub",
        new=AsyncMock(),
    ) as abandon_mock:
        try:
            await orch.start_session(
                session_id="s1", user_id="u1",
                source_chunks=source_chunks or [{"chunk_id": "c1", "text": "..."}],
                target_depth="standard",
                question_mode="multiple_choice",
                provider_name="claude", model_name="m",
                source_file_ids=source_file_ids or ["upl_A"],
                emit=AsyncMock(),
            )
        except SplitterVerificationRejected:
            if expect_reject:
                return None, abandon_mock
            raise
    return captured["stages"], abandon_mock


# ── L3: reroll 行為路徑 ──────────────────────────────────────

class TestSplitterVerifyLoop(unittest.IsolatedAsyncioTestCase):
    async def test_aligned_true_first_run_no_reroll(self):
        """verifier 第一輪通過、splitter 只跑一次。"""
        orch = _mk_orch()
        orch.splitter.run = AsyncMock(return_value={
            "stages": _stages_a(), "summary": "",
        })
        orch.splitter_verifier.run = AsyncMock(return_value={
            "aligned": True, "missing_options": [],
            "issue_chunk_ids": [], "reason": "ok",
        })
        final_stages, _ = await _run_start(orch)

        self.assertEqual(orch.splitter.run.await_count, 1)
        self.assertEqual(orch.splitter_verifier.run.await_count, 1)
        self.assertEqual(final_stages, _stages_a())

    async def test_aligned_false_then_reroll_aligned(self):
        """verifier 第一輪 false、第二輪 true、splitter 跑兩次、用 reroll 結果。"""
        orch = _mk_orch()
        orch.splitter.run = AsyncMock(side_effect=[
            {"stages": _stages_a(), "summary": ""},
            {"stages": _stages_b(), "summary": ""},
        ])
        orch.splitter_verifier.run = AsyncMock(side_effect=[
            {
                "aligned": False, "missing_options": ["X"],
                "issue_chunk_ids": ["c1"], "reason": "missing X",
                "repair_plan_struct": {
                    "required_stage_titles": ["案例：X"],
                    "missing_stage_specs": [],
                    "forbidden_mixes": [],
                    "summary": "add X stage",
                },
            },
            {"aligned": True, "missing_options": [],
             "issue_chunk_ids": [], "reason": "ok after reroll",
             "repair_plan_struct": {}},
        ])
        final_stages, _ = await _run_start(orch)

        self.assertEqual(orch.content_outliner.run.await_count, 1)
        self.assertEqual(orch.splitter.run.await_count, 2)
        self.assertEqual(orch.splitter_verifier.run.await_count, 2)
        self.assertEqual(final_stages, _stages_b())

        first_payload = orch.splitter.run.await_args_list[0].args[0].task_payload
        self.assertIn("required_outline", first_payload)

        second_payload = orch.splitter.run.await_args_list[1].args[0].task_payload
        self.assertEqual(second_payload["previous_attempt_missed"], ["X"])
        self.assertEqual(second_payload["verifier_reason"], "missing X")
        self.assertEqual(
            second_payload["repair_plan_struct"]["required_stage_titles"],
            ["案例：X"],
        )

    async def test_raises_when_still_failed_after_max_retries(self):
        """verifier 用盡重試仍 false → 拒絕、不寫 pending、abandon stub。"""
        orch = _mk_orch()
        orch.splitter.run = AsyncMock(side_effect=[
            {"stages": _stages_a(), "summary": ""},
            {"stages": _stages_b(), "summary": ""},
            {"stages": _stages_b(), "summary": ""},
        ])
        orch.splitter_verifier.run = AsyncMock(return_value={
            "aligned": False, "missing_options": ["GraphQL 案例"],
            "issue_chunk_ids": ["c1"], "reason": "mash-up",
        })
        final_stages, abandon_mock = await _run_start(orch, expect_reject=True)
        self.assertIsNone(final_stages)
        self.assertEqual(orch.splitter.run.await_count, 3)
        self.assertEqual(orch.splitter_verifier.run.await_count, 3)
        abandon_mock.assert_awaited_once()


# ── L3: fallback 路徑 ─────────────────────────────────────────

class TestSplitterVerifyFallback(unittest.IsolatedAsyncioTestCase):
    async def test_verifier_exception_retries_then_rejects(self):
        """verifier 連續 exception → reroll 至上限 → 拒絕（不再靜默接受第一輪）。"""
        orch = _mk_orch()
        orch.splitter.run = AsyncMock(return_value={
            "stages": _stages_a(), "summary": "",
        })
        orch.splitter_verifier.run = AsyncMock(side_effect=RuntimeError("LLM down"))
        final_stages, abandon_mock = await _run_start(orch, expect_reject=True)
        self.assertIsNone(final_stages)
        self.assertEqual(orch.splitter.run.await_count, 3)
        abandon_mock.assert_awaited_once()

    async def test_reroll_splitter_exception_then_rejects(self):
        """reroll splitter 拋 exception → 仍視為未通過 → 拒絕。"""
        orch = _mk_orch()
        orch.splitter.run = AsyncMock(side_effect=[
            {"stages": _stages_a(), "summary": ""},
            RuntimeError("reroll fail"),
        ])
        orch.splitter_verifier.run = AsyncMock(return_value={
            "aligned": False, "missing_options": ["X"],
            "issue_chunk_ids": ["c1"], "reason": "missing X",
        })
        final_stages, abandon_mock = await _run_start(orch, expect_reject=True)
        self.assertIsNone(final_stages)
        abandon_mock.assert_awaited_once()


# ── L4: 回歸（既有 start_session 流程不破） ────────────────────

class TestStartSessionFlowUnaffected(unittest.IsolatedAsyncioTestCase):
    async def test_existing_flow_when_verifier_passes(self):
        """verifier 永遠 aligned=true、create_pending_session 介面與既有相同。"""
        orch = _mk_orch()
        orch.splitter.run = AsyncMock(return_value={
            "stages": _stages_a(), "summary": "test summary",
        })
        orch.splitter_verifier.run = AsyncMock(return_value={
            "aligned": True, "missing_options": [],
            "issue_chunk_ids": [], "reason": "ok",
        })
        final_stages, _ = await _run_start(orch)
        self.assertEqual(final_stages, _stages_a())

    def test_max_retries_constant_plan_b(self):
        self.assertEqual(MAX_SPLITTER_VERIFY_RETRIES, 2)


if __name__ == "__main__":
    unittest.main()
