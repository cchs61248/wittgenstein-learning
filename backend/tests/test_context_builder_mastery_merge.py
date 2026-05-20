"""build_adaptive_context mastery_map 合併 regression。

新行為：
- mastery_map 應合併「整個 user 跨 stage 已掌握的概念（≥0.8）」與
  「當前 stage.key_concepts 的 mastery（含 < 0.8）」
- 同名 concept 以當前 stage 的值為準（覆蓋）
- 不影響 must_reinforce 判斷（仍以當前 stage concept 的 mastery 為主）
- get_user_mastery_map 必須帶 source_signature（跨教材污染隔離）
"""
import unittest
from unittest.mock import AsyncMock, patch

from backend.orchestrator.context_builder import build_adaptive_context


class TestMasteryMerge(unittest.IsolatedAsyncioTestCase):
    async def _run_with_mocks(
        self,
        stage: dict,
        user_mastery: dict[str, float],
        stage_mastery: dict[str, float],
        misconceptions: list[dict] | None = None,
        source_signature: str | None = "sig_test",
    ):
        misconceptions = misconceptions or []
        user_mastery_mock = AsyncMock(return_value=user_mastery)
        with patch(
            "backend.orchestrator.context_builder.session_memory.get_source_chunks",
            new=AsyncMock(return_value=[]),
        ), patch(
            "backend.orchestrator.context_builder.session_memory.get_recent_qa_summary",
            new=AsyncMock(return_value=[]),
        ), patch(
            "backend.orchestrator.context_builder.session_memory.get_last_decision_record",
            new=AsyncMock(return_value=None),
        ), patch(
            "backend.orchestrator.context_builder.session_memory.get_source_signature",
            new=AsyncMock(return_value=source_signature),
        ), patch(
            "backend.orchestrator.context_builder.longterm_memory.get_user_mastery_map",
            new=user_mastery_mock,
        ), patch(
            "backend.orchestrator.context_builder.longterm_memory.get_concept_mastery_map",
            new=AsyncMock(return_value=stage_mastery),
        ), patch(
            "backend.orchestrator.context_builder.longterm_memory.get_misconceptions",
            new=AsyncMock(return_value=misconceptions),
        ):
            ctx = await build_adaptive_context(
                session_id="s1",
                user_id="u1",
                stage=stage,
                current_attempt=1,
                stages=[stage],
            )
            return ctx, user_mastery_mock

    async def test_merges_user_high_mastery_with_stage_low(self):
        """跨 stage 已掌握的概念（user_mastery≥0.8）+ 當前 stage 低 mastery 概念
        都應出現在最終 mastery_map 中，QG 才能同時做個人化過濾與補強判斷。"""
        ctx, _ = await self._run_with_mocks(
            stage={"stage_id": 3, "key_concepts": ["新概念C"], "source_chunk_ids": []},
            user_mastery={"前置概念A": 1.0, "前置概念B": 0.9},
            stage_mastery={"新概念C": 0.0},
        )
        mastery_map = ctx["learner_state"]["mastery_map"]
        self.assertEqual(mastery_map["前置概念A"], 1.0)
        self.assertEqual(mastery_map["前置概念B"], 0.9)
        self.assertEqual(mastery_map["新概念C"], 0.0)

    async def test_stage_mastery_overrides_user_mastery_for_same_concept(self):
        """若同個 concept 兩邊都有，以當前 stage 為準（語意：當前回合最新值優先）。"""
        ctx, _ = await self._run_with_mocks(
            stage={"stage_id": 5, "key_concepts": ["重複概念X"], "source_chunk_ids": []},
            user_mastery={"重複概念X": 0.95},  # user 全表撈到的
            stage_mastery={"重複概念X": 0.6},   # 當前 stage 撈到的（較新）
        )
        self.assertEqual(ctx["learner_state"]["mastery_map"]["重複概念X"], 0.6)

    async def test_must_reinforce_unaffected_by_user_mastery(self):
        """user_mastery 中的「跨 stage 已掌握」概念不該被列入 must_reinforce。
        只有當前 stage.key_concepts 中 < 0.75 或有 misconception 的才列入。"""
        ctx, _ = await self._run_with_mocks(
            stage={
                "stage_id": 4,
                "key_concepts": ["當前低概念", "當前高概念"],
                "source_chunk_ids": [],
            },
            user_mastery={"無關高概念": 1.0},  # 不在 key_concepts 中，不影響
            stage_mastery={"當前低概念": 0.5, "當前高概念": 0.9},
        )
        must_reinforce = ctx["next_lesson_requirements"]["must_reinforce"]
        self.assertIn("當前低概念", must_reinforce)
        self.assertNotIn("當前高概念", must_reinforce)
        self.assertNotIn("無關高概念", must_reinforce)

    async def test_first_time_stage_no_record_falls_back_to_must_reinforce(self):
        """第一次學的 stage：stage_mastery 撈不到 record（空 dict），
        get(concept, 0.5) fallback → 0.5 < 0.75 → 仍列 must_reinforce（保留原行為）。"""
        ctx, _ = await self._run_with_mocks(
            stage={"stage_id": 1, "key_concepts": ["全新概念"], "source_chunk_ids": []},
            user_mastery={},
            stage_mastery={},  # 第一次學，DB 沒 record
        )
        self.assertIn("全新概念", ctx["next_lesson_requirements"]["must_reinforce"])

    async def test_source_signature_passed_to_user_mastery_query(self):
        """build_adaptive_context 必須把當前 session 的 source_signature 傳給
        get_user_mastery_map，否則跨教材的高 mastery 概念會污染當前教材的 prompt。"""
        _, user_mastery_mock = await self._run_with_mocks(
            stage={"stage_id": 2, "key_concepts": ["新概念"], "source_chunk_ids": []},
            user_mastery={},
            stage_mastery={},
            source_signature="book_A.pdf|book_B.pdf",
        )
        user_mastery_mock.assert_awaited_once()
        call_kwargs = user_mastery_mock.await_args.kwargs
        self.assertEqual(call_kwargs.get("source_signature"), "book_A.pdf|book_B.pdf")

    async def test_legacy_session_without_signature_still_works(self):
        """舊 session 沒有 source_file_ids → signature=None；
        get_user_mastery_map 應收到 None 並退回不過濾的 legacy 行為。"""
        _, user_mastery_mock = await self._run_with_mocks(
            stage={"stage_id": 1, "key_concepts": ["x"], "source_chunk_ids": []},
            user_mastery={},
            stage_mastery={},
            source_signature=None,
        )
        call_kwargs = user_mastery_mock.await_args.kwargs
        self.assertIsNone(call_kwargs.get("source_signature"))


if __name__ == "__main__":
    unittest.main()
