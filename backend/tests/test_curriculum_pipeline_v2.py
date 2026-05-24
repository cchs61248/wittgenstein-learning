"""V2 curriculum pipeline E2E tests (mocked LLM)."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.orchestrator.curriculum_pipeline_v2 import _dedupe_candidates, run_start_session_v2
from backend.orchestrator.learning_orchestrator import LearningOrchestrator


def _chunks(n: int = 30, source_id: str = "src_a") -> list[dict]:
    return [
        {
            "chunk_id": f"chunk_{i:04d}",
            "text": f"段落 {i} 關於概念 alpha beta gamma",
            "source_id": source_id,
            "source_index": 0,
            "source_label": "書A",
            "section_title": f"第 {i // 10 + 1} 章",
        }
        for i in range(n)
    ]


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
    orch._pending_stages = None
    orch._pending_start_args = None
    orch._check_stage_quality = MagicMock(return_value=[])
    return orch


class TestInterimDedup(unittest.TestCase):
    def test_dedupe_merges_similar_concepts(self):
        candidates = [
            {"title": "A", "key_concepts": ["alpha", "beta"], "source_chunk_ids": ["c1"]},
            {"title": "B", "key_concepts": ["alpha", "beta"], "source_chunk_ids": ["c2"]},
            {"title": "C", "key_concepts": ["gamma"], "source_chunk_ids": ["c3"]},
        ]
        merged = _dedupe_candidates(candidates)
        self.assertEqual(len(merged), 2)
        chunk_ids = merged[0].get("source_chunk_ids") or []
        self.assertIn("c1", chunk_ids)
        self.assertIn("c2", chunk_ids)

    def test_dedupe_rejects_merge_exceeding_chunk_cap(self):
        # sess_live_049d39ce 案例：region_001/002/005 splitter 各出
        # 「房屋貸款」相關 candidate，dedupe 將跨 region 25 chunks 全併入單一 candidate。
        # 預期：合併後超過 MAX_MERGED_OUTCOME_CHUNKS=14 → 拒絕合併，保留獨立 candidate。
        def chunks(prefix: str, n: int) -> list[str]:
            return [f"{prefix}_{i:03d}" for i in range(n)]
        candidates = [
            {"region_id": "region_001", "title": "房屋貸款（一）",
             "key_concepts": ["房屋貸款", "中信融資型房貸"],
             "source_chunk_ids": chunks("r1", 12)},
            {"region_id": "region_002", "title": "房屋貸款（二）",
             "key_concepts": ["房屋貸款", "理財型房貸"],
             "source_chunk_ids": chunks("r2", 12)},
        ]
        merged = _dedupe_candidates(candidates)
        # 12 + 12 = 24 > cap=14 → 拒絕合併，保留 2 個 candidate
        self.assertEqual(len(merged), 2)
        self.assertEqual(len(merged[0]["source_chunk_ids"]), 12)
        self.assertEqual(len(merged[1]["source_chunk_ids"]), 12)

    def test_dedupe_accepts_merge_within_chunk_cap(self):
        # 5 + 5 = 10 ≤ cap → 允許合併
        def chunks(prefix: str, n: int) -> list[str]:
            return [f"{prefix}_{i:03d}" for i in range(n)]
        candidates = [
            {"region_id": "region_001", "title": "A",
             "key_concepts": ["alpha", "beta"],
             "source_chunk_ids": chunks("r1", 5)},
            {"region_id": "region_002", "title": "B",
             "key_concepts": ["alpha", "beta"],
             "source_chunk_ids": chunks("r2", 5)},
        ]
        merged = _dedupe_candidates(candidates)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0]["source_chunk_ids"]), 10)


class TestCurriculumPipelineV2(unittest.IsolatedAsyncioTestCase):
    async def _run_v2(
        self,
        orch,
        *,
        env: dict | None = None,
        reducer_outcomes=None,
        reducer_result: dict | None = None,
    ):
        captured = {"stages": None, "quality_warnings": None}
        events: list[str] = []

        async def _emit(msg):
            events.append(msg.get("type", ""))

        async def _capture_pending(**kwargs):
            captured["stages"] = kwargs["stages"]
            captured["quality_warnings"] = kwargs.get("quality_warnings")

        if reducer_result is None:
            if reducer_outcomes is None:
                reducer_outcomes = [{
                    "outcome_id": "lo_001",
                    "title": "Stage 1",
                    "teaching_goal": "理解 alpha",
                    "key_concepts": ["alpha"],
                    "primary_evidence": {"source_id": "src_a", "chunk_ids": ["chunk_0000"]},
                    "supporting_evidence": [],
                    "merge_decision": "merged",
                    "merge_confidence": 0.9,
                }]
            reducer_result = {"outcomes": reducer_outcomes}
        reducer_mock = MagicMock()
        reducer_mock.run = AsyncMock(return_value=reducer_result)

        env_patch = {
            "CURRICULUM_PIPELINE_V2": "1",
            "CURRICULUM_V2_PLAN_B": "0",
            "REDUCER_FAIL_MODE": "hard",
            "SPLITTER_FAIL_MODE": "hard",
            "SMALL_FILE_CHUNK_THRESHOLD": "0",
        }
        if env:
            env_patch.update(env)

        with patch(
            "backend.orchestrator.curriculum_pipeline_v2.session_memory.create_generating_stub",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.curriculum_pipeline_v2.session_memory.insert_source_chunks",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.curriculum_pipeline_v2.session_memory.create_pending_session",
            new=AsyncMock(side_effect=_capture_pending),
        ), patch(
            "backend.orchestrator.curriculum_pipeline_v2.session_memory.purge_source_uploads",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.curriculum_pipeline_v2.GlobalCurriculumReducerAgent",
            return_value=reducer_mock,
        ), patch.dict("os.environ", env_patch, clear=False):
            await run_start_session_v2(
                orch,
                session_id="sess_v2",
                user_id="u1",
                source_chunks=_chunks(),
                target_depth="standard",
                question_mode="multiple_choice",
                provider_name="claude",
                model_name="m",
                emit=_emit,
                source_file_ids=["upl_1"],
            )
        return captured, events

    async def test_v2_emits_ws_events_and_writes_pending(self):
        orch = _mk_orch_v2()
        captured, events = await self._run_v2(orch)

        self.assertIn("session_generating", events)
        self.assertIn("region_done", events)
        self.assertIn("reduce_done", events)
        self.assertIn("composer_done", events)
        self.assertIn("knowledge_map", events)
        self.assertIsNotNone(captured["stages"])
        self.assertGreaterEqual(len(captured["stages"]), 1)
        self.assertIsNotNone(orch._pending_stages)

    async def test_v2_small_file_skips_macro_region_planner(self):
        """sess_live_e106b1a4 觀察：Rate Limiter 20 chunks 仍跑 MacroRegion + per-region
        splitter（13 splitter + 10 verify = 23 LLM）。優化後 small_file 應 bypass
        MacroRegionPlanner，直接 single split (1-2 splitter + 1-2 verify)。
        """
        orch = _mk_orch_v2()
        macro_called = {"count": 0}

        class _MacroSpy:
            def __init__(self, *a, **kw): pass
            async def run(self, ctx):
                macro_called["count"] += 1
                return {"regions": []}

        with patch(
            "backend.orchestrator.curriculum_pipeline_v2.MacroRegionPlannerAgent",
            _MacroSpy,
        ):
            # default _chunks(30) 屬 small_file (threshold 50)。
            # _run_v2 預設 SMALL_FILE_CHUNK_THRESHOLD=0 強制 large path，
            # 這裡 override 回 default 觸發 small_file branch。
            captured, events = await self._run_v2(
                orch, env={"SMALL_FILE_CHUNK_THRESHOLD": "50"},
            )

        self.assertEqual(
            macro_called["count"], 0,
            "small_file 應 bypass MacroRegionPlanner",
        )
        # 仍應 emit region_done（single pseudo region）
        self.assertIn("region_done", events)
        # splitter 應只被呼叫 1 次（無 per-region 多次，本 test mock verifier 永遠 aligned）
        self.assertEqual(
            orch.splitter.run.await_count, 1,
            "small_file 應只跑 1 次 splitter (mock verifier 永遠 aligned，無 reroll)",
        )
        # outline 也應被 skip（C）
        self.assertEqual(
            orch.content_outliner.run.await_count, 0,
            "small_file 應 bypass ContentOutline",
        )

    async def test_v2_plan_b_skips_reducer(self):
        orch = _mk_orch_v2()
        chunks = _chunks(20, "src_a") + [
            {
                "chunk_id": f"chunk_b{i:04d}",
                "text": f"補充 alpha beta {i}",
                "source_id": "src_b",
                "source_index": 1,
                "source_label": "書B",
                "section_title": "補充",
            }
            for i in range(5)
        ]
        captured = {"quality_warnings": None}
        events: list[str] = []

        async def _emit(msg):
            events.append(msg.get("type", ""))

        async def _capture_pending(**kwargs):
            captured["quality_warnings"] = kwargs.get("quality_warnings")

        with patch(
            "backend.orchestrator.curriculum_pipeline_v2.session_memory.create_generating_stub",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.curriculum_pipeline_v2.session_memory.insert_source_chunks",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.curriculum_pipeline_v2.session_memory.create_pending_session",
            new=AsyncMock(side_effect=_capture_pending),
        ), patch(
            "backend.orchestrator.curriculum_pipeline_v2.session_memory.purge_source_uploads",
            new=AsyncMock(),
        ), patch(
            "backend.orchestrator.curriculum_pipeline_v2.GlobalCurriculumReducerAgent",
        ) as reducer_cls, patch.dict(
            "os.environ",
            {
                "CURRICULUM_V2_PLAN_B": "1",
                "SPLITTER_FAIL_MODE": "hard",
                "SMALL_FILE_CHUNK_THRESHOLD": "0",
            },
            clear=False,
        ):
            await run_start_session_v2(
                orch,
                session_id="sess_planb",
                user_id="u1",
                source_chunks=chunks,
                target_depth="standard",
                question_mode="multiple_choice",
                provider_name=None,
                model_name=None,
                emit=_emit,
            )
        reducer_cls.assert_not_called()
        self.assertTrue(captured["quality_warnings"].get("plan_b_active"))
        self.assertIn("primary_source_id", captured["quality_warnings"])

    async def test_v2_reducer_fail_soft_flattens_candidates(self):
        orch = _mk_orch_v2()
        captured, _ = await self._run_v2(
            orch,
            env={"REDUCER_FAIL_MODE": "soft"},
            reducer_outcomes=[],
        )
        self.assertTrue(captured["quality_warnings"].get("reducer_fallback_flat"))
        self.assertGreaterEqual(len(captured["stages"]), 1)

    async def test_v2_auto_plan_b_on_reducer_no_llm_outcomes(self):
        orch = _mk_orch_v2()
        outcomes = [
            {
                "outcome_id": f"lo_{i + 1:03d}",
                "title": f"Stage {i + 1}",
                "teaching_goal": f"goal {i}",
                "key_concepts": [f"kc{i}"],
                "primary_evidence": {"source_id": "src_a", "chunk_ids": [f"chunk_{i:04d}"]},
                "supporting_evidence": [],
                "merge_decision": "split",
                "merge_confidence": 1.0,
            }
            for i in range(3)
        ]
        captured, _ = await self._run_v2(
            orch,
            env={"CURRICULUM_V2_PLAN_B_AUTO": "1"},
            reducer_result={
                "outcomes": outcomes,
                "unsure_pair_count": 4,
                "llm_outcome_count": 0,
            },
        )
        qw = captured["quality_warnings"] or {}
        self.assertTrue(qw.get("plan_b_auto_fallback"))
        self.assertTrue(qw.get("plan_b_active"))

    async def test_start_session_routes_to_v2(self):
        orch = _mk_orch_v2()
        with patch(
            "backend.orchestrator.curriculum_pipeline_v2.run_start_session_v2",
            new=AsyncMock(),
        ) as v2_mock, patch.dict(
            "os.environ", {"CURRICULUM_PIPELINE_V2": "1"}, clear=False
        ):
            await orch.start_session(
                session_id="s1",
                user_id="u1",
                source_chunks=_chunks(5),
                target_depth="standard",
                question_mode="multiple_choice",
                provider_name=None,
                model_name=None,
                emit=AsyncMock(),
            )
        v2_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
