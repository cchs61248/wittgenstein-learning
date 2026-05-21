"""orchestrator splitter verify loop 整合測試（L3 reroll 路徑 + L4 回歸）。

對應 spec: docs/superpowers/specs/2026-05-21-splitter-verifier-agent-design.md § 3 + § 5-6
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.orchestrator.learning_orchestrator import (
    LearningOrchestrator,
    MAX_SPLITTER_VERIFY_RETRIES,
)


def _mk_orch():
    """建立 LearningOrchestrator instance、所有 agent 與 IO 都 mock。"""
    orch = LearningOrchestrator.__new__(LearningOrchestrator)
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


async def _run_start(orch, source_file_ids=None, source_chunks=None):
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
    ):
        await orch.start_session(
            session_id="s1", user_id="u1",
            source_chunks=source_chunks or [{"chunk_id": "c1", "text": "..."}],
            target_depth="standard",
            question_mode="multiple_choice",
            provider_name="claude", model_name="m",
            source_file_ids=source_file_ids or ["upl_A"],
            emit=AsyncMock(),
        )
    return captured["stages"]


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
        final_stages = await _run_start(orch)

        # splitter 只跑一次
        self.assertEqual(orch.splitter.run.await_count, 1)
        # verifier 跑一次
        self.assertEqual(orch.splitter_verifier.run.await_count, 1)
        # 用 splitter 第一輪結果
        self.assertEqual(final_stages, _stages_a())

    async def test_aligned_false_then_reroll_aligned(self):
        """verifier 第一輪 false、第二輪 true、splitter 跑兩次、用 reroll 結果。"""
        orch = _mk_orch()
        orch.splitter.run = AsyncMock(side_effect=[
            {"stages": _stages_a(), "summary": ""},
            {"stages": _stages_b(), "summary": ""},
        ])
        orch.splitter_verifier.run = AsyncMock(side_effect=[
            {"aligned": False, "missing_options": ["X"],
             "issue_chunk_ids": ["c1"], "reason": "missing X"},
            {"aligned": True, "missing_options": [],
             "issue_chunk_ids": [], "reason": "ok after reroll"},
        ])
        final_stages = await _run_start(orch)

        self.assertEqual(orch.splitter.run.await_count, 2)
        self.assertEqual(orch.splitter_verifier.run.await_count, 2)
        # 用 reroll 結果（stages_b）
        self.assertEqual(final_stages, _stages_b())

        # 第二次 splitter 呼叫應該有 previous_attempt_missed hint
        second_call_kwargs = orch.splitter.run.await_args_list[1]
        second_payload = second_call_kwargs.args[0].task_payload
        self.assertEqual(second_payload["previous_attempt_missed"], ["X"])

    async def test_aligned_false_after_retry_uses_reroll_stages(self):
        """verifier 兩輪都 false、用 reroll 結果繼續、log warning。"""
        orch = _mk_orch()
        orch.splitter.run = AsyncMock(side_effect=[
            {"stages": _stages_a(), "summary": ""},
            {"stages": _stages_b(), "summary": ""},
        ])
        orch.splitter_verifier.run = AsyncMock(return_value={
            "aligned": False, "missing_options": ["Y"],
            "issue_chunk_ids": ["c1"], "reason": "still missing",
        })
        final_stages = await _run_start(orch)

        # splitter 跑 1+1=2 次（MAX_SPLITTER_VERIFY_RETRIES=1）
        self.assertEqual(orch.splitter.run.await_count, 2)
        # verifier 跑 2 次（first run + reroll 後 verify）
        self.assertEqual(orch.splitter_verifier.run.await_count, 2)
        # 用 reroll 結果（stages_b、最後一次 splitter 輸出）
        self.assertEqual(final_stages, _stages_b())


# ── L3: fallback 路徑 ─────────────────────────────────────────

class TestSplitterVerifyFallback(unittest.IsolatedAsyncioTestCase):
    async def test_verifier_exception_falls_back_to_splitter_stages(self):
        """verifier 拋 exception、用 splitter 第一輪結果、不 reroll。"""
        orch = _mk_orch()
        orch.splitter.run = AsyncMock(return_value={
            "stages": _stages_a(), "summary": "",
        })
        orch.splitter_verifier.run = AsyncMock(side_effect=RuntimeError("LLM down"))
        final_stages = await _run_start(orch)

        # splitter 只跑一次（verifier 失敗、不 reroll）
        self.assertEqual(orch.splitter.run.await_count, 1)
        # 用 splitter 第一輪結果
        self.assertEqual(final_stages, _stages_a())

    async def test_reroll_splitter_exception_keeps_first_run_stages(self):
        """reroll splitter 拋 exception、保留第一輪 stages。"""
        orch = _mk_orch()
        orch.splitter.run = AsyncMock(side_effect=[
            {"stages": _stages_a(), "summary": ""},
            RuntimeError("reroll fail"),
        ])
        orch.splitter_verifier.run = AsyncMock(return_value={
            "aligned": False, "missing_options": ["X"],
            "issue_chunk_ids": ["c1"], "reason": "missing X",
        })
        final_stages = await _run_start(orch)

        self.assertEqual(orch.splitter.run.await_count, 2)
        # verifier 跑一次（reroll 後沒再 verify、因 reroll 失敗）
        self.assertEqual(orch.splitter_verifier.run.await_count, 1)
        # 保留第一輪 splitter 結果
        self.assertEqual(final_stages, _stages_a())


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
        final_stages = await _run_start(orch)
        self.assertEqual(final_stages, _stages_a())

    def test_max_retries_constant(self):
        self.assertEqual(MAX_SPLITTER_VERIFY_RETRIES, 1)


if __name__ == "__main__":
    unittest.main()
