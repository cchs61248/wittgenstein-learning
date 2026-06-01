"""V2 curriculum pipeline E2E tests (mocked LLM).

D1: pipeline is unified to small-file paths (single_split + per_source_split).
The large-file branch (MacroRegionPlanner + GlobalCurriculumReducer + Plan B)
has been removed; tests targeting those paths are deleted accordingly.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.orchestrator.curriculum_pipeline_v2 import (
    _build_knowledge_map_summary,
    _dedupe_candidates,
    run_start_session_v2,
)
from backend.orchestrator.learning_orchestrator import LearningOrchestrator
from backend.agents.pedagogical_planner import PedagogicalPlannerAgentResult
from backend.utils.pedagogical_planner import (
    PedagogicalPlan,
    PedagogicalPlanMove,
    _stage_identity,
)


def _chunks(n: int = 30, source_id: str = "src_a") -> list[dict]:
    return [
        {
            "chunk_id": f"chunk_{i:04d}",
            "text": f"段落 {i} 關於概念 alpha beta gamma",
            "source_id": source_id,
            "source_index": 0,
            "order_index": i,
            "source_label": "書A",
            "section_title": f"第 {i // 10 + 1} 章",
        }
        for i in range(n)
    ]


def _multi_source_chunks(n_per_source: int = 10) -> list[dict]:
    chunks: list[dict] = []
    order = 0
    for src_idx, src_id in enumerate(["src_a", "src_b"]):
        for i in range(n_per_source):
            chunks.append({
                "chunk_id": f"chunk_{src_id}_{i:04d}",
                "text": f"段落 {i} 關於概念 alpha beta gamma",
                "source_id": src_id,
                "source_index": src_idx,
                "order_index": order,
                "source_label": f"書{src_id}",
                "section_title": f"第 {i // 5 + 1} 章",
            })
            order += 1
    return chunks


def _n_source_chunks(n_sources: int, n_per_source: int = 8) -> list[dict]:
    """n 個 source（每個視為一章）的 source_chunks，用於 ≥3 章 outline 閘門測試。"""
    chunks: list[dict] = []
    order = 0
    for src_idx in range(n_sources):
        src_id = f"src_{src_idx}"
        for i in range(n_per_source):
            chunks.append({
                "chunk_id": f"chunk_{src_id}_{i:04d}",
                "text": f"段落 {i} 關於概念 alpha beta gamma",
                "source_id": src_id,
                "source_index": src_idx,
                "order_index": order,
                "source_label": f"第{src_idx + 1}章",
                "section_title": f"第 {src_idx + 1} 章",
            })
            order += 1
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

    def test_dedupe_same_material_no_cross_source(self):
        # Phase 2.5：same_material 時，kc 相同但 source_id 不同 → 不跨章合併
        # （sess_85qxyltir「星期三郵局」貫穿兩章被誤併的 root case）
        candidates = [
            {"source_id": "src_a", "title": "星期三郵局（1章）",
             "key_concepts": ["星期三郵局", "文字淨化"],
             "source_chunk_ids": ["chunk_0001"]},
            {"source_id": "src_b", "title": "星期三郵局（2章）",
             "key_concepts": ["星期三郵局", "文字淨化"],
             "source_chunk_ids": ["chunk_0034"]},
        ]
        merged = _dedupe_candidates(candidates, same_material=True)
        self.assertEqual(len(merged), 2, "不同 source 不應跨章合併")
        for m in merged:
            self.assertEqual(len(m["source_chunk_ids"]), 1, "chunk 不應被 union")

    def test_dedupe_same_material_within_source_still_merges(self):
        # same_material 時，同一 source_id 內 kc 相似 → 仍去重合併
        candidates = [
            {"source_id": "src_a", "title": "A",
             "key_concepts": ["星期三郵局", "文字淨化"],
             "source_chunk_ids": ["chunk_0001"]},
            {"source_id": "src_a", "title": "B",
             "key_concepts": ["星期三郵局", "文字淨化"],
             "source_chunk_ids": ["chunk_0002"]},
        ]
        merged = _dedupe_candidates(candidates, same_material=True)
        self.assertEqual(len(merged), 1, "同 source 內 kc 相似仍應合併")
        self.assertIn("chunk_0001", merged[0]["source_chunk_ids"])
        self.assertIn("chunk_0002", merged[0]["source_chunk_ids"])

    def test_dedupe_cross_material_still_merges(self):
        # same_material=False（cross_material / 預設）：跨 source kc 相似 → 仍合併（原行為）
        candidates = [
            {"source_id": "src_a", "title": "A",
             "key_concepts": ["alpha", "beta"],
             "source_chunk_ids": ["chunk_0001"]},
            {"source_id": "src_b", "title": "B",
             "key_concepts": ["alpha", "beta"],
             "source_chunk_ids": ["chunk_0002"]},
        ]
        merged = _dedupe_candidates(candidates, same_material=False)
        self.assertEqual(len(merged), 1, "cross_material 跨 source 仍應合併")
        self.assertIn("chunk_0001", merged[0]["source_chunk_ids"])
        self.assertIn("chunk_0002", merged[0]["source_chunk_ids"])


class TestCurriculumPipelineV2(unittest.IsolatedAsyncioTestCase):
    async def _run_v2(
        self,
        orch,
        *,
        source_chunks: list[dict] | None = None,
        env: dict | None = None,
        same_material: bool = True,
    ):
        captured = {"stages": None, "quality_warnings": None}
        events: list[str] = []

        async def _emit(msg):
            events.append(msg.get("type", ""))

        async def _capture_pending(**kwargs):
            captured["stages"] = kwargs["stages"]
            captured["quality_warnings"] = kwargs.get("quality_warnings")

        env_patch = {"SPLITTER_FAIL_MODE": "hard"}
        if env:
            env_patch.update(env)

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
                session_id="sess_v2",
                user_id="u1",
                source_chunks=source_chunks or _chunks(),
                target_depth="standard",
                question_mode="multiple_choice",
                provider_name="claude",
                model_name="m",
                emit=_emit,
                source_file_ids=["upl_1"],
                same_material=same_material,
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

    async def test_v2_single_source_uses_single_split(self):
        """D1: single-source input → single_split path. 1 splitter call, no outline."""
        orch = _mk_orch_v2()
        captured, events = await self._run_v2(orch)

        # splitter called exactly once (verifier always aligned → no reroll)
        self.assertEqual(
            orch.splitter.run.await_count, 1,
            "single_split should call splitter exactly once",
        )
        # outline skipped because default same_material=True
        self.assertEqual(
            orch.content_outliner.run.await_count, 0,
            "ContentOutline should be skipped when same_material=True",
        )
        self.assertIn("region_done", events)
        qw = captured["quality_warnings"] or {}
        self.assertTrue(qw.get("small_file_path"))
        self.assertTrue(qw.get("reducer_skipped"))
        # multi_source flag should NOT be set for single source
        self.assertFalse(qw.get("multi_source_split"))

    async def test_v2_multi_source_uses_per_source_split(self):
        """D1: multi-source input → per_source_split path. 1 splitter per source."""
        orch = _mk_orch_v2()
        captured, events = await self._run_v2(
            orch,
            source_chunks=_multi_source_chunks(10),
        )

        self.assertEqual(orch.splitter.run.await_count, 2)
        self.assertEqual(orch.content_outliner.run.await_count, 0)
        qw = captured["quality_warnings"] or {}
        self.assertTrue(qw.get("small_file_path"))
        self.assertTrue(qw.get("multi_source_split"))
        self.assertEqual(qw.get("source_count"), 2)
        self.assertTrue(qw.get("reducer_skipped"))
        self.assertIn("region_done", events)

    async def test_t9_compact_same_material_cleans_orphan_enumerator_title(self):
        """A1 鎖：compact same_material 路徑（finalize_small_file_stages）也必須經過
        title cleanup 匯流點。若 hook 被掛回 _apply_deterministic_cleanup，compact
        path 會 bypass → 此測試會抓到未清理的「模式二」。"""
        orch = _mk_orch_v2()
        chunks = _chunks(6)
        orch.splitter.run = AsyncMock(return_value={
            "stages": [{
                "stage_id": 1,
                "node_id": "1.1",
                "title": "模式二：受託責任夥伴",
                "teaching_goal": "理解受託責任",
                "key_concepts": ["受託責任"],
                "source_chunk_ids": [c["chunk_id"] for c in chunks],
            }],
            "summary": "V2 摘要",
        })
        captured, _ = await self._run_v2(orch, source_chunks=chunks, same_material=True)
        titles = [s["title"] for s in captured["stages"]]
        self.assertIn("受託責任夥伴", titles)
        self.assertNotIn("模式二：受託責任夥伴", titles)
        qw = captured["quality_warnings"] or {}
        self.assertEqual(qw.get("title_cleanup_removed_orphan_enumerators"), 1)

    async def test_t9b_non_same_material_preserves_enumerator_title(self):
        """same_material gate：cross_material / 非同教材不跑 title cleanup。"""
        orch = _mk_orch_v2()
        chunks = _chunks(6)
        orch.splitter.run = AsyncMock(return_value={
            "stages": [{
                "stage_id": 1,
                "node_id": "1.1",
                "title": "模式二：受託責任夥伴",
                "teaching_goal": "理解受託責任",
                "key_concepts": ["受託責任"],
                "source_chunk_ids": [c["chunk_id"] for c in chunks],
            }],
            "summary": "V2 摘要",
        })
        captured, _ = await self._run_v2(orch, source_chunks=chunks, same_material=False)
        titles = [s["title"] for s in captured["stages"]]
        self.assertIn("模式二：受託責任夥伴", titles)
        qw = captured["quality_warnings"] or {}
        self.assertNotIn("title_cleanup_removed_orphan_enumerators", qw)

    async def test_start_session_routes_to_v2(self):
        orch = _mk_orch_v2()
        with patch(
            "backend.orchestrator.curriculum_pipeline_v2.run_start_session_v2",
            new=AsyncMock(),
        ) as v2_mock:
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

    async def test_order_decision_written_to_quality_warnings(self):
        orch = _mk_orch_v2()
        decision = {"applied": True, "certain": True, "signal": ["filename_regex"],
                    "order": ["第一章.txt", "第二章.txt"], "reason": None}
        captured = {"qw": None}

        async def _capture_pending(**kwargs):
            captured["qw"] = kwargs.get("quality_warnings")

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
        ):
            await run_start_session_v2(
                orch, session_id="s", user_id="u",
                source_chunks=_chunks(5), target_depth="standard",
                question_mode="multiple_choice", provider_name="claude",
                model_name="m", emit=AsyncMock(), source_file_ids=[],
                order_decision=decision,
            )
        self.assertEqual((captured["qw"] or {}).get("source_order"), decision)


def _mk_orch_v2_gating() -> LearningOrchestrator:
    """orch whose splitter returns 2 distinct-concept stages per call and that
    has a stage_consolidator mock — so the ≥30-chunk consolidator gate is
    reachable and we can assert whether it ran per postprocess mode."""
    orch = _mk_orch_v2()

    async def _split(ctx):
        chunks = ctx.task_payload.get("source_chunks") or []
        ids = [c["chunk_id"] for c in chunks[:2]] or ["chunk_0000"]
        src = (chunks[0].get("source_id") if chunks else "x") or "x"
        stages = [
            {
                "stage_id": i + 1,
                "node_id": f"1.{i + 1}",
                "title": f"Stage {src} {i}",
                "teaching_goal": "理解概念",
                "key_concepts": [f"c_{src}_{i}"],
                "source_chunk_ids": [cid],
            }
            for i, cid in enumerate(ids)
        ]
        return {"stages": stages, "summary": "V2 摘要"}

    orch.splitter.run = AsyncMock(side_effect=_split)
    orch.stage_consolidator = MagicMock()
    orch.stage_consolidator.run = AsyncMock(return_value={"skipped": True})
    return orch


class TestPostprocessModeGating(TestCurriculumPipelineV2):
    """Phase 1: single source / same_material=True 跳過合併與 consolidator；
    only cross_material（多 source + same_material=False）維持合併流程。"""

    def _spy_merges(self):
        import backend.orchestrator.curriculum_pipeline_v2 as v2mod
        return (
            patch.object(
                v2mod, "merge_by_concept_overlap",
                wraps=v2mod.merge_by_concept_overlap,
            ),
            patch.object(
                v2mod, "merge_singleton_chunk_stages",
                wraps=v2mod.merge_singleton_chunk_stages,
            ),
        )

    async def test_single_source_skips_semantic_merges_and_consolidator(self):
        # Refinement: singleton cleanup runs in every mode (it's thin-stage
        # cleanup, not a semantic merge); only jaccard + consolidator are gated.
        orch = _mk_orch_v2_gating()
        p_jaccard, p_singleton = self._spy_merges()
        with p_jaccard as mj, p_singleton as ms:
            captured, _ = await self._run_v2(orch, source_chunks=_chunks(30))
        qw = captured["quality_warnings"] or {}
        self.assertEqual(qw.get("postprocess_mode"), "single_source_finalize_only")
        self.assertEqual(orch.stage_consolidator.run.await_count, 0)
        self.assertEqual(mj.call_count, 0, "jaccard merge must not run for single source")
        self.assertGreaterEqual(ms.call_count, 1, "singleton cleanup still runs")

    async def test_same_material_multi_skips_semantic_merges_and_consolidator(self):
        orch = _mk_orch_v2_gating()
        p_jaccard, p_singleton = self._spy_merges()
        with p_jaccard as mj, p_singleton as ms:
            captured, _ = await self._run_v2(
                orch, source_chunks=_multi_source_chunks(15), same_material=True,
            )
        qw = captured["quality_warnings"] or {}
        self.assertEqual(qw.get("postprocess_mode"), "same_material_coordinate_only")
        self.assertEqual(orch.stage_consolidator.run.await_count, 0)
        self.assertEqual(mj.call_count, 0)
        self.assertGreaterEqual(ms.call_count, 1, "singleton cleanup still runs")

    async def test_cross_material_multi_runs_merges_and_consolidator(self):
        orch = _mk_orch_v2_gating()
        p_jaccard, p_singleton = self._spy_merges()
        with p_jaccard as mj, p_singleton as ms:
            captured, _ = await self._run_v2(
                orch, source_chunks=_multi_source_chunks(15), same_material=False,
            )
        qw = captured["quality_warnings"] or {}
        self.assertEqual(
            qw.get("postprocess_mode"), "cross_material_merge_and_coordinate"
        )
        self.assertGreaterEqual(orch.stage_consolidator.run.await_count, 1)
        self.assertGreaterEqual(mj.call_count, 1)
        self.assertGreaterEqual(ms.call_count, 1)

    async def test_interior_orphan_folds_instead_of_filler_stage(self):
        # splitter leaves chunk_0002 uncovered between [0,1] and [3,4]; the interior
        # orphan must fold into the preceding stage, not spawn a「章節總結」filler.
        orch = _mk_orch_v2()
        orch.stage_consolidator = MagicMock()
        orch.stage_consolidator.run = AsyncMock(return_value={"skipped": True})

        async def _split(ctx):
            return {
                "stages": [
                    {"stage_id": 1, "node_id": "1.1", "title": "前半",
                     "teaching_goal": "g", "key_concepts": ["a"],
                     "source_chunk_ids": ["chunk_0000", "chunk_0001"]},
                    {"stage_id": 2, "node_id": "1.2", "title": "後半",
                     "teaching_goal": "g", "key_concepts": ["b"],
                     "source_chunk_ids": ["chunk_0003", "chunk_0004"]},
                ],
                "summary": "s",
            }

        orch.splitter.run = AsyncMock(side_effect=_split)
        captured, _ = await self._run_v2(orch, source_chunks=_chunks(5))
        stages = captured["stages"]
        kinds = [s.get("kind") for s in stages]
        titles = [s.get("title") or "" for s in stages]
        self.assertNotIn("follow_up_orphan", kinds,
                         "interior orphan should fold, not spawn a filler stage")
        self.assertFalse(any("章節總結" in t for t in titles))
        covered = {c for s in stages for c in (s.get("source_chunk_ids") or [])}
        self.assertIn("chunk_0002", covered)


class TestOutlineGating(TestCurriculumPipelineV2):
    """Phase 3: same_material 永不跑 ContentOutline（含 ≥3 章）。

    根因（live sess_f9qt8rac9，8 章理財書）：run_outline 在 n_sources>=3 時
    即使 same_material 也跑全局 outline，named_cases 變成跨章主題桶，被各
    per-source splitter 共用 → 同主題的不同章 chunk 併進同一 stage（7.1=第6+8章）。
    對齊 same_material 保章節邊界原則：same_material 一律不跑 outline；
    cross_material 仍跑（取全局骨架）。"""

    async def test_same_material_three_sources_skips_outline(self):
        orch = _mk_orch_v2()
        await self._run_v2(
            orch, source_chunks=_n_source_chunks(3), same_material=True,
        )
        self.assertEqual(
            orch.content_outliner.run.await_count, 0,
            "same_material 不論章數都不應跑 ContentOutline",
        )

    async def test_same_material_three_sources_records_skip_warning(self):
        orch = _mk_orch_v2()
        captured, _ = await self._run_v2(
            orch, source_chunks=_n_source_chunks(3), same_material=True,
        )
        qw = captured["quality_warnings"] or {}
        self.assertIn("outline_skipped_same_material", qw)
        self.assertEqual(qw["outline_skipped_same_material"]["n_sources"], 3)

    async def test_cross_material_three_sources_still_runs_outline(self):
        orch = _mk_orch_v2_gating()
        await self._run_v2(
            orch, source_chunks=_n_source_chunks(3), same_material=False,
        )
        self.assertEqual(
            orch.content_outliner.run.await_count, 1,
            "cross_material 仍應跑 ContentOutline 取全局骨架",
        )


# --- T4e: planner-applied order must survive finalization to persistence ---

# reading order (by chunk order_index) is reverse-pedagogical so the deterministic
# T3 ordering plan recommends a change (order_changed=True ⇒ gate can pass).
_T4E_SOURCES = {
    "src_0": [("全書總結與展望", "結語"), ("進階整合應用", "整合應用")],
    "src_1": [("核心機制：思維鏈 CoT", "CoT機制"), ("核心機制：RAG 檢索", "RAG機制")],
    "src_2": [("課程總覽與導讀", "課程總覽"), ("基礎概念入門", "基礎概念")],
}


def _t4e_chunks(n_per_source: int = 12) -> list[dict]:
    chunks: list[dict] = []
    order = 0
    for src_idx, src_id in enumerate(["src_0", "src_1", "src_2"]):
        for i in range(n_per_source):
            chunks.append({
                "chunk_id": f"chunk_{src_id}_{i:04d}",
                "text": f"段落 {i}",
                "source_id": src_id,
                "source_index": src_idx,
                "order_index": order,
                "source_label": f"書{src_id}",
                "section_title": "第 1 章",
            })
            order += 1
    return chunks


class _RecordingPlanner:
    """Fake planner agent: records the (finalized) stage order it receives and
    proposes moving the last stage to the very front — a real, verifiable reorder."""

    def __init__(self):
        self.input_titles: list[str] | None = None
        self.calls = 0

    async def propose_plan(self, *, stages, cards, graph, ordering_plan):
        self.calls += 1
        self.input_titles = [s.get("title") for s in stages]
        last_id = _stage_identity(stages[-1], len(stages) - 1)
        plan = PedagogicalPlan(
            moves=(PedagogicalPlanMove(stage_id=last_id, after_stage_id=None,
                                       reason="t4e move last to front"),),
            rationale="t4e",
        )
        return PedagogicalPlannerAgentResult(plan=plan, diagnostics=())


def _mk_orch_v2_planner(planner) -> LearningOrchestrator:
    """Cross-material orch: 3 sources × 2 distinct stages, consolidator skipped,
    distinct key_concepts (no jaccard merge), ≥2 chunks/stage (no singleton fold)."""
    orch = _mk_orch_v2()
    orch.stage_consolidator = MagicMock()
    orch.stage_consolidator.run = AsyncMock(return_value={"skipped": True})
    orch.pedagogical_planner = planner

    async def _split(ctx):
        chunks = ctx.task_payload.get("source_chunks") or []
        sid = chunks[0]["source_id"] if chunks else "src_0"
        cids = [c["chunk_id"] for c in chunks]
        half = max(1, len(cids) // 2)
        (t1, kc1), (t2, kc2) = _T4E_SOURCES[sid]
        return {
            "stages": [
                {"stage_id": 1, "node_id": "1.1", "title": t1, "teaching_goal": "g",
                 "key_concepts": [kc1], "source_chunk_ids": cids[:half]},
                {"stage_id": 2, "node_id": "1.2", "title": t2, "teaching_goal": "g",
                 "key_concepts": [kc2], "source_chunk_ids": cids[half:]},
            ],
            "summary": "s",
        }

    orch.splitter.run = AsyncMock(side_effect=_split)
    return orch


class TestPlannerAppliedOrderPersisted(TestCurriculumPipelineV2):
    """T4e: when the planner applies a reorder, the FINAL persisted stage order
    must equal the applied order — finalize's reading-order sort must not clobber
    it, and stage_id/node_id must be renumbered to the new order."""

    _FLAG = {"CROSS_MATERIAL_PEDAGOGICAL_PLANNER": "1"}

    async def _run(self, planner, *, same_material=False, env=None):
        e = dict(self._FLAG)
        if env:
            e.update(env)
        return await self._run_v2(
            _mk_orch_v2_planner(planner),
            source_chunks=_t4e_chunks(12),
            same_material=same_material,
            env=e,
        )

    async def test_planner_was_called_gate_passed(self):
        planner = _RecordingPlanner()
        captured, _ = await self._run(planner)
        self.assertEqual(planner.calls, 1, "gate should pass for this fixture")
        w = (captured["quality_warnings"] or {}).get("cross_material_pedagogical_planner") or {}
        self.assertEqual(w.get("planner_mode"), "applied")

    async def test_persisted_order_equals_planner_applied_order(self):
        planner = _RecordingPlanner()
        captured, _ = await self._run(planner)
        persisted_titles = [s["title"] for s in captured["stages"]]
        # planner moved the last finalized stage to the front
        expected = [planner.input_titles[-1]] + planner.input_titles[:-1]
        self.assertEqual(persisted_titles, expected)

    async def test_persisted_stage_ids_renumbered_to_new_order(self):
        planner = _RecordingPlanner()
        captured, _ = await self._run(planner)
        stages = captured["stages"]
        n = len(stages)
        self.assertEqual([s["stage_id"] for s in stages], list(range(1, n + 1)))
        # node_id follows finalize's chapter.section convention (_renumber_stages)
        expected_nodes = [f"{(j // 3) + 1}.{(j % 3) + 1}" for j in range(n)]
        self.assertEqual([s["node_id"] for s in stages], expected_nodes)

    async def test_warning_marks_renumbered_after_apply(self):
        planner = _RecordingPlanner()
        captured, _ = await self._run(planner)
        w = captured["quality_warnings"]["cross_material_pedagogical_planner"]
        self.assertTrue(w.get("renumbered_after_apply"))

    async def test_flag_off_keeps_finalize_reading_order(self):
        planner = _RecordingPlanner()
        captured, _ = await self._run_v2(
            _mk_orch_v2_planner(planner),
            source_chunks=_t4e_chunks(12),
            same_material=False,
            env={"CROSS_MATERIAL_PEDAGOGICAL_PLANNER": "0"},
        )
        self.assertEqual(planner.calls, 0)
        self.assertNotIn("cross_material_pedagogical_planner",
                         captured["quality_warnings"] or {})
        # reading order: src_0 stages first (lowest order_index)
        self.assertEqual(captured["stages"][0]["title"], "全書總結與展望")


class TestBuildKnowledgeMapSummary(unittest.TestCase):
    def test_uses_first_part_only_with_stage_count(self):
        parts = [
            "本段材料說明長期被動投資的核心主張。",
            "本段教材說明有限理性如何影響決策。",
        ]
        out = _build_knowledge_map_summary(parts, 33)
        self.assertIn("長期被動投資", out)
        self.assertNotIn("有限理性", out)
        self.assertIn("33", out)

    def test_truncates_long_first_part(self):
        long = "本段" + "很長" * 120 + "。"
        out = _build_knowledge_map_summary([long], 5)
        self.assertLessEqual(len(out), 200)
        self.assertIn("5", out)


class TestDeterministicCleanup(unittest.TestCase):
    """Regression: deterministic structural cleanup (orphan attach + kc trim) must
    run even when global verify is aligned-within-tolerance. Previously gated on
    `not aligned`, so tolerated orphans (≤ compact_orphan_limit) were silently
    dropped and kc-heavy stages left untrimmed (live sess_81ihihq27: 5 chunks +
    a whole '利益衝突' lesson lost; stage kc=10).
    """

    def test_tolerated_orphans_still_attached_when_aligned(self):
        from backend.orchestrator.curriculum_pipeline_v2 import (
            _apply_deterministic_cleanup,
        )
        chunks = _chunks(8)  # chunk_0000..chunk_0007
        stages = [{
            "stage_id": 1, "node_id": "1.1", "title": "S1",
            "key_concepts": ["alpha"],
            "source_chunk_ids": ["chunk_0000", "chunk_0001", "chunk_0002"],
        }]
        # chunks 3-7 orphaned (5 ≤ tolerance → verify reports aligned=True)
        out = _apply_deterministic_cleanup(stages, chunks, None, {}, "sess_t")
        covered = {cid for s in out for cid in (s.get("source_chunk_ids") or [])}
        self.assertEqual(covered, {c["chunk_id"] for c in chunks})

    def test_kc_heavy_stage_trimmed_when_aligned(self):
        from backend.orchestrator.curriculum_pipeline_v2 import (
            _apply_deterministic_cleanup,
        )
        from backend.utils.small_curriculum import STAGE_MAX_KEY_CONCEPTS
        chunks = _chunks(3)
        stages = [{
            "stage_id": 1, "node_id": "1.1", "title": "S1",
            "key_concepts": [f"概念{i}" for i in range(10)],
            "source_chunk_ids": ["chunk_0000", "chunk_0001", "chunk_0002"],
        }]
        out = _apply_deterministic_cleanup(stages, chunks, None, {}, "sess_t")
        for s in out:
            self.assertLessEqual(
                len(s.get("key_concepts") or []), STAGE_MAX_KEY_CONCEPTS,
            )

    @staticmethod
    def _chunk_range(ids):
        return [{
            "chunk_id": f"chunk_{i:04d}",
            "text": f"段落 {i} 內容",
            "source_id": "src_a", "source_index": 0, "order_index": i,
            "source_label": "書A", "section_title": f"法則 {i}",
        } for i in ids]

    def test_interior_orphan_folded_not_summary_stage(self):
        """aligned path 的 interior orphan 應被 fold 進鄰近 stage，
        不可變成中段的「章節總結與補充內容」fallback stage。"""
        from backend.orchestrator.curriculum_pipeline_v2 import (
            _apply_deterministic_cleanup,
        )
        chunks = self._chunk_range([47, 48, 49, 50, 51])
        stages = [
            {"stage_id": 1, "node_id": "1.1", "title": "前節",
             "key_concepts": ["a"],
             "source_chunk_ids": ["chunk_0047", "chunk_0048"]},
            {"stage_id": 2, "node_id": "1.2", "title": "後節",
             "key_concepts": ["b"],
             "source_chunk_ids": ["chunk_0050", "chunk_0051"]},
        ]
        # chunk_0049 是 interior orphan（夾在 0048 與 0050 之間）
        out = _apply_deterministic_cleanup(stages, chunks, None, {}, "sess_t")
        covered = {cid for s in out for cid in (s.get("source_chunk_ids") or [])}
        self.assertIn("chunk_0049", covered)
        titles = [s.get("title") or "" for s in out]
        self.assertNotIn("章節總結與補充內容", titles)

    def test_tail_orphan_still_attached(self):
        """真尾段 orphan（後面沒有任何 stage chunk）仍須被 attach，不可漏掉。"""
        from backend.orchestrator.curriculum_pipeline_v2 import (
            _apply_deterministic_cleanup,
        )
        chunks = self._chunk_range([47, 48, 49, 50, 51, 52])
        stages = [
            {"stage_id": 1, "node_id": "1.1", "title": "前節",
             "key_concepts": ["a"],
             "source_chunk_ids": ["chunk_0047", "chunk_0048", "chunk_0049"]},
            {"stage_id": 2, "node_id": "1.2", "title": "後節",
             "key_concepts": ["b"],
             "source_chunk_ids": ["chunk_0050", "chunk_0051"]},
        ]
        # chunk_0052 是尾段 orphan（後面沒有 stage chunk）
        out = _apply_deterministic_cleanup(stages, chunks, None, {}, "sess_t")
        covered = {cid for s in out for cid in (s.get("source_chunk_ids") or [])}
        self.assertIn("chunk_0052", covered)


if __name__ == "__main__":
    unittest.main()
