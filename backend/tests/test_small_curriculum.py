"""Small-file curriculum helpers — API Design.pdf regression."""
import os
import unittest
from unittest.mock import patch

from backend.agents.global_curriculum_verifier import verify_global_coverage
from backend.orchestrator.curriculum_pipeline_v2 import _build_follow_up_stages
from backend.utils.small_curriculum import (
    case_covered_in_stages,
    ensure_orphan_chunks_attached,
    filter_false_verifier_misses,
    filter_missing_named_cases,
    finalize_small_file_stages,
    is_small_file,
    merge_duplicate_topic_stages,
    prune_intro_chunk_sharing,
    zero_region_overlaps,
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


if __name__ == "__main__":
    unittest.main()
