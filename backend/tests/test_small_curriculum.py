"""Small-file curriculum helpers — API Design.pdf regression."""
import os
import unittest
from unittest.mock import patch

from backend.agents.global_curriculum_verifier import verify_global_coverage
from backend.orchestrator.curriculum_pipeline_v2 import _build_follow_up_stages
from backend.utils.small_curriculum import (
    case_covered_in_stages,
    dedupe_key_concept_aliases,
    ensure_key_concept_chunk_coverage,
    ensure_orphan_chunks_attached,
    filter_false_verifier_misses,
    filter_missing_named_cases,
    finalize_small_file_stages,
    ensure_empty_key_concepts,
    finalize_curriculum_stages,
    sort_stages_by_chunk_order,
    is_compact_curriculum,
    is_small_file,
    merge_duplicate_topic_stages,
    merge_empty_chunk_stages,
    normalize_stages_pre_verify,
    prune_intro_chunk_sharing,
    prune_phantom_key_concepts,
    split_oversized_stages,
    trim_stage_key_concepts,
    zero_region_overlaps,
    ORPHAN_STAGE_MAX_CHUNKS,
    STAGE_MAX_KEY_CONCEPTS,
)


def _api_design_chunks() -> list[dict]:
    return [
        {
            "chunk_id": "chunk_0001",
            "text": "API 風格選型框架 REST GraphQL RPC Webhook 比較",
            "order_index": 0,
            "source_id": "src_0",
        },
        {
            "chunk_id": "chunk_0002",
            "text": "案例 QR Code Generator REST API 設計 Airbnb Booking GraphQL schema",
            "order_index": 1,
            "source_id": "src_0",
        },
        {
            "chunk_id": "chunk_0003",
            "text": "Webhook Platform 事件訂閱 ChatGPT Tasks RPC 風格 Checklist 面試應答技巧",
            "order_index": 2,
            "source_id": "src_0",
        },
        {
            "chunk_id": "chunk_0004",
            "text": "本章重點整理 面試話術 常見問題",
            "order_index": 3,
            "source_id": "src_0",
        },
    ]


def _api_design_reroll_stages() -> list[dict]:
    """Splitter reroll output — cases present but outline names differ."""
    return [
        {
            "stage_id": 1,
            "title": "API 風格選型框架",
            "key_concepts": ["REST", "GraphQL"],
            "source_chunk_ids": ["chunk_0001"],
        },
        {
            "stage_id": 2,
            "title": "案例實務：QR Code Generator",
            "key_concepts": ["REST API"],
            "source_chunk_ids": ["chunk_0001", "chunk_0002"],
        },
        {
            "stage_id": 3,
            "title": "案例實務：Airbnb Booking",
            "key_concepts": ["GraphQL"],
            "source_chunk_ids": ["chunk_0001", "chunk_0002"],
        },
        {
            "stage_id": 4,
            "title": "案例實務：Webhook Platform",
            "key_concepts": ["Webhook"],
            "source_chunk_ids": ["chunk_0002", "chunk_0003"],
        },
    ]


class TestSmallFileDetection(unittest.TestCase):
    def setUp(self):
        self._env_patch = patch.dict(
            os.environ, {"SMALL_FILE_CHUNK_THRESHOLD": "50"}, clear=False,
        )
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
    def test_four_chunks_is_small(self):
        self.assertTrue(is_small_file(_api_design_chunks()))

    def test_fifty_chunks_is_small(self):
        chunks = [{"chunk_id": f"c{i}", "text": "x"} for i in range(50)]
        self.assertTrue(is_small_file(chunks))

    def test_fifty_one_chunks_not_small(self):
        chunks = [{"chunk_id": f"c{i}", "text": "x" * 800} for i in range(51)]
        self.assertFalse(is_small_file(chunks))
        regions = [{"region_id": "r0", "overlap_before": 2, "overlap_after": 2}]
        zero_region_overlaps(regions)
        self.assertEqual(regions[0]["overlap_before"], 0)
        self.assertEqual(regions[0]["overlap_after"], 0)

    def test_compact_curriculum_when_full_v2_forces_threshold_zero(self):
        chunks = [{"chunk_id": f"chunk_{i:04d}", "text": "x"} for i in range(23)]
        with patch.dict(os.environ, {"SMALL_FILE_CHUNK_THRESHOLD": "0"}, clear=False):
            self.assertFalse(is_small_file(chunks))
            self.assertTrue(is_compact_curriculum(chunks))


class TestFuzzyNamedCase(unittest.TestCase):
    def test_outline_case_matches_case_prefix_title(self):
        chunks = _api_design_chunks()
        stages = _api_design_reroll_stages()
        missing = filter_missing_named_cases(
            ["QR Code Generator", "Airbnb Booking", "Webhook Platform", "ChatGPT Tasks"],
            stages,
            chunks,
        )
        self.assertNotIn("QR Code Generator", missing)
        self.assertNotIn("Airbnb Booking", missing)
        self.assertNotIn("Webhook Platform", missing)
        self.assertIn("ChatGPT Tasks", missing)

    def test_rate_limiter_via_chunk_and_chinese_title(self):
        chunks = [{
            "chunk_id": "chunk_0006",
            "text": "Rate Limiter uses consistent hashing for Redis cluster",
            "order_index": 6,
        }]
        stages = [{
            "title": "案例：Redis 與限流器應用",
            "key_concepts": ["限流器權屬"],
            "source_chunk_ids": ["chunk_0006"],
        }]
        missing = filter_missing_named_cases(["Rate Limiter"], stages, chunks)
        self.assertEqual(missing, [])
        chunks = _api_design_chunks()
        merged_stages = [
            {"title": "案例實務：QR Code 與 Airbnb (REST vs GraphQL)", "key_concepts": [], "source_chunk_ids": ["chunk_0002"]},
            {"title": "案例實務：Webhook 與 ChatGPT (可靠傳遞)", "key_concepts": [], "source_chunk_ids": ["chunk_0003"]},
        ]
        missing = filter_missing_named_cases(
            ["QR Code Generator", "Airbnb Booking", "Webhook Platform", "ChatGPT Tasks"],
            merged_stages,
            chunks,
        )
        self.assertEqual(missing, [])

    def test_parenthetical_outline_case_matches(self):
        chunks = _api_design_chunks()
        stages = _api_design_reroll_stages()
        missing = filter_missing_named_cases(
            ["Airbnb Booking (GraphQL/BFF 案例)", "ChatGPT Tasks (混合架構案例)"],
            stages,
            chunks,
        )
        self.assertNotIn("Airbnb Booking (GraphQL/BFF 案例)", missing)
        self.assertIn("ChatGPT Tasks (混合架構案例)", missing)

    def test_chinese_compound_case_matches_short_title(self):
        chunks = [{"chunk_id": "chunk_0005", "text": "小雅與小蝶的抗壓性對比案例", "order_index": 5}]
        stages = [{
            "title": "案例：小雅與小蝶的抗壓性對比",
            "key_concepts": ["抗壓性"],
            "source_chunk_ids": ["chunk_0005"],
        }]
        missing = filter_missing_named_cases(
            ["粉絲小雅與作家小蝶"],
            stages,
            chunks,
        )
        self.assertEqual(missing, [])

    def test_topic_alias_credit_loan(self):
        stages = [{
            "title": "借錢方案（二）：信貸與房貸壓力",
            "key_concepts": ["還債壓力"],
            "source_chunk_ids": ["chunk_0008"],
        }]
        filtered = filter_false_verifier_misses(
            ["股票質押", "信用貸款", "房屋貸款"],
            stages,
            [],
        )
        self.assertNotIn("信用貸款", filtered)
        self.assertNotIn("房屋貸款", filtered)
        self.assertIn("股票質押", filtered)

    def test_slash_topic_covered(self):
        stages = [
            {
                "title": "借錢方案（二）：信貸與房貸壓力",
                "key_concepts": ["信用貸款"],
                "source_chunk_ids": ["chunk_0017"],
            },
            {
                "title": "零支付與風林火山節奏總結",
                "key_concepts": ["零支付手法"],
                "source_chunk_ids": ["chunk_0009"],
            },
        ]
        filtered = filter_false_verifier_misses(
            ["房貸/信貸無本分期策略"],
            stages,
            [],
        )
        self.assertEqual(filtered, [])

    def test_numbered_rule_range_in_stage_title(self):
        stages = [{
            "title": "法則 3-7：心態與市場規律",
            "key_concepts": ["均值回歸"],
            "source_chunk_ids": ["chunk_0008"],
        }]
        filtered = filter_false_verifier_misses(
            ["法則 5：利息是武器"],
            stages,
            [],
        )
        self.assertEqual(filtered, [])

    def test_colon_suffix_numbered_buy_method(self):
        stages = [{
            "title": "現金買進的三種方案對比",
            "key_concepts": ["一次全買", "定期定額買進", "肥羊派流買法"],
            "source_chunk_ids": ["chunk_0093"],
        }]
        misses = [
            "炒股方式 1：一次全買",
            "炒股方式 2：定期定額買進",
            "炒股方式 3：肥羊派流買法",
        ]
        filtered = filter_false_verifier_misses(misses, stages, [])
        self.assertEqual(filtered, [])

    def test_grade_bucket_s_tier_banks(self):
        stages = [{
            "title": "金控評比（一）：S 級龍頭標的",
            "key_concepts": ["中信金參考價", "玉山金教訓", "富邦金龍頭"],
            "source_chunk_ids": ["chunk_0056"],
        }]
        filtered = filter_false_verifier_misses(
            ["S級金控（中信金、玉山金、富邦金）"],
            stages,
            [],
        )
        self.assertEqual(filtered, [])

    def test_paren_enumeration_cash_buy_methods(self):
        stages = [{
            "title": "現金買進的三種方案對比",
            "key_concepts": ["一次全買", "定期定額買進", "肥羊派流買法"],
            "source_chunk_ids": ["chunk_0093"],
        }]
        filtered = filter_false_verifier_misses(
            ["現金買進 3 種方式（一次全買、定期定額、肥羊派流買法）"],
            stages,
            [],
        )
        self.assertEqual(filtered, [])

    def test_paren_alias_fenglinhuoshan(self):
        stages = [{
            "title": "肥羊派波浪理論與操作實務",
            "key_concepts": ["肥羊波浪理論", "蛛網交易"],
            "source_chunk_ids": ["chunk_0083"],
        }]
        filtered = filter_false_verifier_misses(
            ["夏、秋、冬、春四大戰術（風林火山四戰術）"],
            stages,
            [],
        )
        self.assertEqual(filtered, [])

    def test_ltcm_case_prefix_merge(self):
        stages = [
            {
                "stage_id": 1,
                "title": "案例：長期資本管理公司 (LTCM)",
                "key_concepts": ["LTCM"],
                "source_chunk_ids": ["chunk_0001", "chunk_0002"],
            },
            {
                "stage_id": 2,
                "title": "長期資本管理公司",
                "key_concepts": ["低成本ETF"],
                "source_chunk_ids": ["chunk_0001"],
            },
        ]
        merged = merge_duplicate_topic_stages(stages)
        self.assertEqual(len(merged), 1)
        self.assertIn("chunk_0002", merged[0]["source_chunk_ids"])

    def test_merge_empty_chunk_stage_into_neighbor(self):
        stages = [
            {
                "stage_id": 1,
                "title": "行為財務學",
                "key_concepts": ["行為財務學"],
                "source_chunk_ids": ["chunk_0000"],
            },
            {
                "stage_id": 2,
                "title": "複利優勢與長期投資",
                "key_concepts": ["複利", "長期投資"],
                "source_chunk_ids": [],
            },
        ]
        merged = merge_empty_chunk_stages(stages)
        self.assertEqual(len(merged), 1)
        self.assertIn("複利", merged[0]["key_concepts"])

    def test_case_entities_dutch_shell_covered(self):
        stages = [{
            "title": "案例：荷蘭皇家石油與殼牌的連體嬰股價",
            "key_concepts": ["連體嬰股價"],
            "source_chunk_ids": ["chunk_0017"],
        }]
        filtered = filter_false_verifier_misses(
            ["荷蘭皇家石油與殼牌石油 (連體嬰公司案例)"],
            stages,
            [],
        )
        self.assertEqual(filtered, [])

    def test_vs_parallel_asset_classes(self):
        stages = [
            {
                "title": "資產配置概論",
                "key_concepts": ["無風險資產", "有風險資產"],
                "source_chunk_ids": ["chunk_0100"],
            },
            {
                "title": "區域配置",
                "key_concepts": ["國內資產", "國外資產"],
                "source_chunk_ids": ["chunk_0101"],
            },
        ]
        filtered = filter_false_verifier_misses(
            [
                "無風險資產 vs 有風險資產 (資產大類並列)",
                "國內資產 vs 國外資產 (配置比例並列)",
            ],
            stages,
            [],
        )
        self.assertEqual(filtered, [])

    def test_product_list_enumeration(self):
        stages = [
            {"title": "股票與債券", "key_concepts": ["股票", "債券"], "source_chunk_ids": ["c1"]},
            {"title": "主題式與高股息", "key_concepts": ["主題式基金", "高股息"], "source_chunk_ids": ["c2"]},
            {"title": "衍生商品", "key_concepts": ["期貨與選擇權"], "source_chunk_ids": ["c3"]},
            {"title": "房地產", "key_concepts": ["房地產"], "source_chunk_ids": ["c4"]},
        ]
        filtered = filter_false_verifier_misses(
            ["股票", "債券", "主題式基金與 ETF", "高股息及高收益商品", "期貨與選擇權", "房地產"],
            stages,
            [],
        )
        self.assertEqual(filtered, [])

    def test_global_verifier_aligned_after_fuzzy(self):
        chunks = _api_design_chunks()
        stages = _api_design_reroll_stages()
        outline = {
            "named_cases": [
                "QR Code Generator",
                "Airbnb Booking",
                "Webhook Platform",
                "ChatGPT Tasks",
            ],
        }
        result = verify_global_coverage(stages, chunks, outline)
        self.assertNotIn("QR Code Generator", result["missing_options"])
        self.assertNotIn("Airbnb Booking", result["missing_options"])


class TestIntroChunkPrune(unittest.TestCase):
    def test_intro_chunk_kept_only_on_framework_stage(self):
        chunks = _api_design_chunks()
        stages = _api_design_reroll_stages()
        pruned = prune_intro_chunk_sharing(stages, chunks)
        intro_refs = [
            s for s in pruned
            if "chunk_0001" in (s.get("source_chunk_ids") or [])
        ]
        self.assertEqual(len(intro_refs), 1)
        self.assertIn("框架", intro_refs[0]["title"])


def _data_pipeline_chunks() -> list[dict]:
    return [
        {
            "chunk_id": "chunk_0000",
            "text": "Data Pipeline 批量處理 ETL 選型框架 離線數倉",
            "order_index": 0,
            "source_id": "src_0",
        },
        {
            "chunk_id": "chunk_0001",
            "text": "串流處理 Kafka Flink 即時管線 低延遲",
            "order_index": 1,
            "source_id": "src_0",
        },
        {
            "chunk_id": "chunk_0002",
            "text": "Spark 批次調度 MapReduce 分散式運算 YARN",
            "order_index": 2,
            "source_id": "src_0",
        },
    ]


class TestIntroStageKeyConceptCoverage(unittest.TestCase):
    def test_intro_expands_contiguous_chunks_for_spark_kc(self):
        """Data Pipeline regression: intro 選型 stage 須涵蓋 Spark/MapReduce chunk。"""
        chunks = _data_pipeline_chunks()
        stages = [{
            "stage_id": 1,
            "title": "Data Pipeline 核心選型：批量與串流",
            "key_concepts": ["批量 ETL", "Spark", "MapReduce"],
            "source_chunk_ids": ["chunk_0000", "chunk_0001", "chunk_0002"],
        }]
        normalized = normalize_stages_pre_verify(stages, chunks)
        ids = normalized[0]["source_chunk_ids"]
        self.assertIn("chunk_0000", ids)
        self.assertIn("chunk_0002", ids)

    def test_intro_stays_single_chunk_when_kc_covered(self):
        chunks = [
            {
                "chunk_id": "chunk_0000",
                "text": "API 風格選型框架 REST GraphQL",
                "order_index": 0,
            },
            {
                "chunk_id": "chunk_0001",
                "text": "Webhook 案例實務",
                "order_index": 1,
            },
        ]
        stages = [{
            "title": "API 風格選型框架",
            "key_concepts": ["REST", "GraphQL"],
            "source_chunk_ids": ["chunk_0000", "chunk_0001"],
        }]
        normalized = normalize_stages_pre_verify(stages, chunks)
        self.assertEqual(normalized[0]["source_chunk_ids"], ["chunk_0000"])

    def test_ensure_key_concept_chunk_coverage_attaches_neighbor(self):
        chunks = _data_pipeline_chunks()
        stages = [{
            "title": "Spark 運算基礎",
            "key_concepts": ["MapReduce"],
            "source_chunk_ids": ["chunk_0001"],
        }]
        fixed = ensure_key_concept_chunk_coverage(stages, chunks)
        self.assertIn("chunk_0002", fixed[0]["source_chunk_ids"])


class TestOrphanAttach(unittest.TestCase):
    def test_summary_chunk_attached_to_last_stage(self):
        chunks = _api_design_chunks()
        stages = _api_design_reroll_stages()
        stages = prune_intro_chunk_sharing(stages, chunks)
        fixed = ensure_orphan_chunks_attached(stages, chunks)
        referenced = {
            cid
            for s in fixed
            for cid in (s.get("source_chunk_ids") or [])
        }
        self.assertIn("chunk_0004", referenced)
        self.assertLessEqual(len(fixed), len(stages) + 1)

    def test_finalize_covers_all_chunks(self):
        chunks = _api_design_chunks()
        stages = finalize_small_file_stages(_api_design_reroll_stages(), chunks)
        referenced = {
            cid
            for s in stages
            for cid in (s.get("source_chunk_ids") or [])
        }
        self.assertEqual(referenced, {c["chunk_id"] for c in chunks})

    def test_bulk_orphan_caps_per_stage_and_overflow(self):
        chunks = [
            {"chunk_id": f"chunk_{i:04d}", "text": f"p{i}", "order_index": i}
            for i in range(20)
        ]
        stages = [{
            "stage_id": 1,
            "title": "主節",
            "key_concepts": [f"kc{k}" for k in range(12)],
            "source_chunk_ids": ["chunk_0000"],
        }]
        fixed = ensure_orphan_chunks_attached(stages, chunks)
        max_chunks = max(len(s.get("source_chunk_ids") or []) for s in fixed)
        self.assertLessEqual(max_chunks, ORPHAN_STAGE_MAX_CHUNKS)
        overflow = [s for s in fixed if s.get("kind") == "follow_up_orphan"]
        self.assertTrue(overflow)
        for s in fixed:
            self.assertLessEqual(len(s.get("key_concepts") or []), STAGE_MAX_KEY_CONCEPTS)

    def test_trim_stage_key_concepts(self):
        stages = [{"key_concepts": [f"c{i}" for i in range(12)]}]
        trimmed = trim_stage_key_concepts(stages, max_kc=8)
        self.assertEqual(len(trimmed[0]["key_concepts"]), 8)


class TestStageChunkCapAndKcHygiene(unittest.TestCase):
    def test_split_oversized_stages(self):
        chunks = [
            {"chunk_id": f"chunk_{i:04d}", "text": f"p{i}", "order_index": i}
            for i in range(20)
        ]
        stages = [{
            "stage_id": 17,
            "node_id": "6.1",
            "title": "肥羊派波浪理論",
            "key_concepts": [f"kc{k}" for k in range(6)],
            "source_chunk_ids": [f"chunk_{i:04d}" for i in range(20)],
        }]
        fixed = split_oversized_stages(stages, chunks)
        max_chunks = max(len(s.get("source_chunk_ids") or []) for s in fixed)
        self.assertLessEqual(max_chunks, ORPHAN_STAGE_MAX_CHUNKS)
        self.assertGreater(len(fixed), 1)
        all_ids = {cid for s in fixed for cid in (s.get("source_chunk_ids") or [])}
        self.assertEqual(len(all_ids), 20)

    def test_dedupe_key_concept_aliases(self):
        stages = [{
            "key_concepts": [
                "巴菲特",
                "巴菲特 (Warren Buffett)",
                "中信金",
                "中信金 (2891)",
                "風林火山",
            ],
        }]
        out = dedupe_key_concept_aliases(stages)
        kcs = out[0]["key_concepts"]
        self.assertEqual(kcs, ["巴菲特", "中信金", "風林火山"])

    def test_prune_phantom_key_concepts(self):
        chunks = [
            {"chunk_id": "chunk_0001", "text": "投資心法與富人思維", "order_index": 1},
        ]
        stages = [{
            "title": "投資心法",
            "key_concepts": ["投資心法", "台塑四寶案例", "富人思維"],
            "source_chunk_ids": ["chunk_0001"],
        }]
        out = prune_phantom_key_concepts(stages, chunks)
        kcs = out[0]["key_concepts"]
        self.assertNotIn("台塑四寶案例", kcs)
        self.assertIn("投資心法", kcs)
        self.assertIn("富人思維", kcs)


class TestSortStagesByChunkOrder(unittest.TestCase):
    def test_large_path_disordered_stages_reordered(self):
        """Reducer/composer 可能把 intro chunks 排到後段 stage — sort 應還原文本順序。"""
        chunks = [
            {"chunk_id": f"chunk_{i:04d}", "text": f"c{i}", "order_index": i}
            for i in range(5)
        ]
        stages = [
            {
                "stage_id": 13,
                "node_id": "5.1",
                "title": "錯位 intro",
                "key_concepts": ["intro"],
                "source_chunk_ids": ["chunk_0000", "chunk_0001", "chunk_0002"],
            },
            {
                "stage_id": 1,
                "node_id": "1.1",
                "title": "後段主題",
                "key_concepts": ["later"],
                "source_chunk_ids": ["chunk_0004"],
            },
            {
                "stage_id": 2,
                "node_id": "1.2",
                "title": "中段",
                "key_concepts": ["mid"],
                "source_chunk_ids": ["chunk_0003"],
            },
        ]
        out = sort_stages_by_chunk_order(stages, chunks)
        self.assertEqual(out[0]["source_chunk_ids"][0], "chunk_0000")
        self.assertEqual(out[0]["stage_id"], 1)
        self.assertEqual(out[0]["node_id"], "1.1")
        self.assertEqual(out[-1]["source_chunk_ids"], ["chunk_0004"])
        self.assertEqual(out[-1]["stage_id"], 3)

    def test_finalize_curriculum_stages_applies_sort_and_kc_fallback(self):
        chunks = [
            {"chunk_id": f"chunk_{i:04d}", "text": "正文段落", "order_index": i}
            for i in range(20)
        ]
        stages = [
            {
                "stage_id": 9,
                "node_id": "3.3",
                "title": "章節總結與補充內容",
                "key_concepts": [],
                "source_chunk_ids": ["chunk_0017", "chunk_0018", "chunk_0019"],
                "kind": "follow_up_orphan",
            },
            {
                "stage_id": 1,
                "node_id": "1.1",
                "title": "開頭",
                "key_concepts": ["限流器"],
                "source_chunk_ids": ["chunk_0000"],
            },
        ]
        out = finalize_curriculum_stages(stages, chunks)
        self.assertEqual(out[0]["source_chunk_ids"], ["chunk_0000"])
        self.assertEqual(out[1]["key_concepts"], ["章節總結"])


class TestEnsureEmptyKeyConcepts(unittest.TestCase):
    def test_orphan_summary_stage_gets_fallback_kc(self):
        stages = [{
            "title": "章節總結與補充內容",
            "key_concepts": [],
            "source_chunk_ids": ["chunk_0017", "chunk_0018", "chunk_0019"],
            "kind": "follow_up_orphan",
        }]
        out = ensure_empty_key_concepts(stages)
        self.assertEqual(out[0]["key_concepts"], ["章節總結"])
        self.assertEqual(out[0]["kind"], "follow_up_orphan")

    def test_prune_then_ensure_restores_summary_kc(self):
        chunks = [
            {"chunk_id": f"chunk_{i:04d}", "text": "正文", "order_index": i}
            for i in range(17, 20)
        ]
        stages = [{
            "title": "章節總結與補充內容",
            "key_concepts": ["章節總結", "補充內容"],
            "source_chunk_ids": ["chunk_0017", "chunk_0018", "chunk_0019"],
            "kind": "follow_up_orphan",
        }]
        pruned = prune_phantom_key_concepts(stages, chunks)
        self.assertEqual(pruned[0]["key_concepts"], [])
        restored = ensure_empty_key_concepts(pruned)
        self.assertEqual(restored[0]["key_concepts"], ["章節總結"])


class TestMergeDuplicateStages(unittest.TestCase):
    def test_exact_duplicate_titles_merge(self):
        stages = [
            {
                "stage_id": 1,
                "node_id": "1.1",
                "title": "投資心法與富人思維",
                "key_concepts": ["心法"],
                "source_chunk_ids": ["chunk_0000"],
                "source_chunks": [{"chunk_id": "chunk_0000", "quote": "a"}],
            },
            {
                "stage_id": 12,
                "node_id": "4.3",
                "title": "投資心法與富人思維",
                "key_concepts": ["富人思維"],
                "source_chunk_ids": ["chunk_0010"],
                "source_chunks": [{"chunk_id": "chunk_0010", "quote": "b"}],
            },
        ]
        merged = merge_duplicate_topic_stages(stages)
        self.assertEqual(len(merged), 1)
        self.assertIn("chunk_0000", merged[0]["source_chunk_ids"])
        self.assertIn("chunk_0010", merged[0]["source_chunk_ids"])
        self.assertEqual(merged[0]["stage_id"], 1)

    def test_global_verifier_passes_after_merge(self):
        chunks = [
            {"chunk_id": "chunk_0000", "text": "x", "order_index": 0},
            {"chunk_id": "chunk_0010", "text": "y", "order_index": 10},
        ]
        stages = merge_duplicate_topic_stages([
            {
                "title": "投資心法與富人思維",
                "key_concepts": ["心法"],
                "source_chunk_ids": ["chunk_0000"],
            },
            {
                "title": "投資心法與富人思維",
                "key_concepts": ["富人思維"],
                "source_chunk_ids": ["chunk_0010"],
            },
        ])
        result = verify_global_coverage(stages, chunks)
        self.assertFalse(result["duplicate_titles"])
        self.assertTrue(result["aligned"])


class TestGradeCaseTokens(unittest.TestCase):
    def test_yongfeng_grade_case_covered(self):
        chunks = [
            {
                "chunk_id": "chunk_0050",
                "text": "永豐金控 A級案例 零成本買股",
                "order_index": 50,
            },
        ]
        stages = [{
            "title": "案例：永豐金",
            "key_concepts": ["永豐金"],
            "source_chunk_ids": ["chunk_0050"],
        }]
        self.assertFalse(
            filter_missing_named_cases(["永豐金 (A級案例)"], stages, chunks)
        )


class TestPostProcessDedupe(unittest.TestCase):
    def test_no_duplicate_follow_up_when_cases_covered(self):
        chunks = _api_design_chunks()
        stages = list(_api_design_reroll_stages())
        outline_missing = ["QR Code Generator", "Airbnb Booking", "Webhook Platform", "ChatGPT Tasks"]
        truly_missing = filter_missing_named_cases(outline_missing, stages, chunks)
        follow_up = _build_follow_up_stages(
            stages=stages,
            source_chunks=chunks,
            missing_options=truly_missing,
            orphan_chunk_ids=["chunk_0004"],
            max_total_stages=12,
        )
        case_follow_ups = [s for s in follow_up if s.get("kind") == "follow_up_case"]
        self.assertEqual(len(case_follow_ups), 1)
        self.assertIn("ChatGPT", case_follow_ups[0]["title"])
        self.assertLessEqual(len(stages) + len(follow_up), 6)

    def test_orphan_excludes_chunks_used_by_follow_up_case(self):
        """sess_live_e106b1a4 (Rate Limiter md) 觸發：
        missing_options 補出的 case stage（如「滑動窗口計數器」）用到 chunk_0006，
        若 chunk_0006 同時在 orphan_chunk_ids，原本會被 follow_up_orphan stage 重複抓進去
        → 同 chunk 在 stage 16 與 stage 17 各被講解一次。
        Fix：build_follow_up_stages 計算 remaining_orphans 前要把 new_stages 用的
        chunks 也加進 covered_chunks。
        """
        chunks = [
            {"chunk_id": f"chunk_{i:04d}", "text": f"段落 {i}", "order_index": i,
             "source_id": "src_a", "source_index": 0}
            for i in range(10)
        ]
        # chunk_0005 含「滑動窗口日誌」、chunk_0006 含「滑動窗口計數器」
        chunks[5]["text"] = "滑動窗口日誌算法詳解"
        chunks[6]["text"] = "滑動窗口計數器演算法"
        # existing stages 沒覆蓋 chunk_0005/0006
        stages = [{
            "stage_id": 1, "node_id": "1.1", "title": "限流框架",
            "key_concepts": ["限流"], "source_chunk_ids": ["chunk_0000"],
        }]
        follow_up = _build_follow_up_stages(
            stages=stages,
            source_chunks=chunks,
            missing_options=["滑動窗口日誌", "滑動窗口計數器"],
            orphan_chunk_ids=["chunk_0005", "chunk_0006", "chunk_0009"],
            max_total_stages=10,
        )
        case_stages = [s for s in follow_up if s.get("kind") == "follow_up_case"]
        orphan_stages = [s for s in follow_up if s.get("kind") == "follow_up_orphan"]
        # 兩個 case stage 應該分別吃 chunk_0005、chunk_0006
        case_chunks = set()
        for s in case_stages:
            case_chunks.update(s.get("source_chunk_ids") or [])
        self.assertIn("chunk_0005", case_chunks)
        self.assertIn("chunk_0006", case_chunks)
        # orphan stage 不可重複抓 chunk_0005/0006
        for s in orphan_stages:
            stage_chunks = set(s.get("source_chunk_ids") or [])
            self.assertNotIn(
                "chunk_0005", stage_chunks,
                "chunk_0005 已被 follow_up_case 用，不該再進 follow_up_orphan",
            )
            self.assertNotIn(
                "chunk_0006", stage_chunks,
                "chunk_0006 已被 follow_up_case 用，不該再進 follow_up_orphan",
            )


if __name__ == "__main__":
    unittest.main()
