"""Verify ContentOutline trigger rules in V2 pipeline.

Phase 3 (2026-05-29): Outline triggers ONLY when same_material is False
(different material → needs global skeleton). same_material=True 一律跳過，
含 ≥3 章——原 P0a 讓 n_sources>=3 也跑 Outline，但全局 named_cases 是跨章主題
桶，會把不同章的同主題 chunk 併進同一 stage（live sess_f9qt8rac9：7.1=第6+8
章）。章節順序已由 SourceOrderResolver 處理，故對齊「保章節邊界」原則不跑。
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.orchestrator.curriculum_pipeline_v2 import run_start_session_v2
from backend.orchestrator.learning_orchestrator import LearningOrchestrator


def _multi_source_chunks(
    n_per_source: int = 10, n_sources: int = 2,
) -> list[dict]:
    chunks: list[dict] = []
    source_ids = [f"src_{chr(ord('a') + i)}" for i in range(n_sources)]
    for src_idx, src_id in enumerate(source_ids):
        for i in range(n_per_source):
            chunks.append({
                "chunk_id": f"chunk_{src_id}_{i:04d}",
                "text": f"段落 {i} 關於概念 alpha beta gamma",
                "source_id": src_id,
                "source_index": src_idx,
                "source_label": f"書{src_id}",
                "section_title": f"第 {i // 5 + 1} 章",
            })
    return chunks


def _mk_orch_v2() -> LearningOrchestrator:
    orch = LearningOrchestrator.__new__(LearningOrchestrator)
    orch.content_outliner = MagicMock()
    orch.content_outliner.run = AsyncMock(return_value={
        "required_stage_titles": ["導論"],
        "named_cases": ["案例A"],
        "framework_sections": [],
        "summary_sections": [],
    })
    orch.splitter = MagicMock()
    orch.splitter.llm = MagicMock()
    orch.splitter.token_counter = MagicMock()
    orch.splitter.run = AsyncMock(return_value={
        "stages": [{
            "stage_id": 1,
            "node_id": "1.1",
            "title": "Stage 1",
            "teaching_goal": "理解 alpha",
            "key_concepts": ["alpha"],
            "source_chunk_ids": ["chunk_0000"],
        }],
        "summary": "V2 摘要",
    })
    orch.splitter_verifier = MagicMock()
    orch.splitter_verifier.run = AsyncMock(return_value={
        "aligned": True,
        "missing_options": [],
        "issue_chunk_ids": [],
        "reason": "ok",
    })
    orch.canonicalizer = MagicMock()
    orch.canonicalizer.run = AsyncMock(return_value={"mappings": []})
    orch.pedagogical_planner = MagicMock()
    orch._pending_stages = None
    orch._pending_start_args = None
    orch._check_stage_quality = MagicMock(return_value=[])
    return orch


class TestOutlineTrigger(unittest.IsolatedAsyncioTestCase):
    async def _run(self, *, same_material: bool, n_sources: int = 2):
        orch = _mk_orch_v2()
        events: list[str] = []

        async def _emit(msg):
            events.append(msg.get("type", ""))

        async def _capture_pending(**kwargs):
            pass

        env_patch = {"SPLITTER_FAIL_MODE": "hard"}

        with patch(
            "backend.orchestrator.curriculum_pipeline_v2.session_memory.create_generating_stub",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.curriculum_pipeline_v2.session_memory.insert_source_chunks",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.curriculum_pipeline_v2.session_memory.get_source_chunks",
            new=AsyncMock(return_value=[]),
        ), patch(
            "backend.orchestrator.curriculum_pipeline_v2.ckpt_mem.load_checkpoint",
            new=AsyncMock(return_value=None),
        ), patch(
            "backend.orchestrator.curriculum_pipeline_v2.ckpt_mem.upsert_checkpoint",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.curriculum_pipeline_v2.ckpt_mem.delete_checkpoint",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.curriculum_pipeline_v2.session_memory.create_pending_session",
            new=AsyncMock(side_effect=_capture_pending),
        ), patch(
            "backend.orchestrator.curriculum_pipeline_v2.session_memory.purge_source_uploads",
            new=AsyncMock(),
        ), patch.dict("os.environ", env_patch, clear=False):
            await run_start_session_v2(
                orch,
                session_id="sess_sm",
                user_id="u1",
                source_chunks=_multi_source_chunks(10, n_sources=n_sources),
                target_depth="standard",
                question_mode="multiple_choice",
                provider_name="claude",
                model_name="m",
                emit=_emit,
                source_file_ids=["upl_1"],
                same_material=same_material,
            )
        return orch

    async def test_two_sources_same_material_skips_outline(self):
        """2 sources + same_material=True → skip Outline (1-2 sources 同教材跳過)."""
        orch = await self._run(same_material=True, n_sources=2)
        orch.content_outliner.run.assert_not_called()

    async def test_two_sources_different_material_runs_outline(self):
        """2 sources + same_material=False → run Outline (不同教材一定跑)."""
        orch = await self._run(same_material=False, n_sources=2)
        orch.content_outliner.run.assert_called_once()

    async def test_three_sources_same_material_skips_outline(self):
        """Phase 3: 3+ sources 但 same_material=True → 不跑 Outline（保章節邊界）."""
        orch = await self._run(same_material=True, n_sources=3)
        orch.content_outliner.run.assert_not_called()

    async def test_three_sources_different_material_runs_outline(self):
        """3+ sources + same_material=False → run Outline."""
        orch = await self._run(same_material=False, n_sources=3)
        orch.content_outliner.run.assert_called_once()

    async def test_single_source_same_material_skips_outline(self):
        """1 source + same_material=True → skip Outline."""
        orch = await self._run(same_material=True, n_sources=1)
        orch.content_outliner.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
