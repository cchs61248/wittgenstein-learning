"""Global curriculum verifier unit tests."""
import unittest

from backend.agents.global_curriculum_verifier import verify_global_coverage


def _chunk(cid: str, text: str = "x") -> dict:
    return {"chunk_id": cid, "text": text, "order_index": int(cid.split("_")[-1])}


class TestGlobalCurriculumVerifier(unittest.TestCase):
    def test_aligned_when_cases_and_chunks_covered(self):
        stages = [
            {"title": "案例：房貸", "key_concepts": ["房貸"], "source_chunk_ids": ["chunk_0001"]},
            {"title": "其他", "key_concepts": ["其他"], "source_chunk_ids": ["chunk_0002"]},
        ]
        chunks = [_chunk("chunk_0001"), _chunk("chunk_0002")]
        result = verify_global_coverage(
            stages, chunks, {"named_cases": ["房貸"]},
        )
        self.assertTrue(result["aligned"])

    def test_missing_named_case_fails(self):
        stages = [{"title": "無關", "key_concepts": [], "source_chunk_ids": ["chunk_0001"]}]
        result = verify_global_coverage(
            stages, [_chunk("chunk_0001")], {"named_cases": ["房貸"]},
        )
        self.assertFalse(result["aligned"])
        self.assertIn("房貸", result["missing_options"])

    def test_duplicate_titles_fail(self):
        stages = [
            {"title": "巴菲特家世", "key_concepts": ["巴菲特"], "source_chunk_ids": ["chunk_0001"]},
            {"title": "巴菲特家世", "key_concepts": ["家世"], "source_chunk_ids": ["chunk_0002"]},
        ]
        result = verify_global_coverage(
            stages, [_chunk("chunk_0001"), _chunk("chunk_0002")],
        )
        self.assertFalse(result["aligned"])
        self.assertTrue(result["duplicate_titles"])

    def test_compact_source_zero_orphan_tolerance(self):
        """23-chunk IT PDF: 5 orphans must fail global verify (full V2 regression)."""
        stages = [
            {
                "title": "框架",
                "key_concepts": ["HTTP"],
                "source_chunk_ids": [f"chunk_{i:04d}" for i in range(18)],
            },
        ]
        chunks = [_chunk(f"chunk_{i:04d}") for i in range(23)]
        result = verify_global_coverage(stages, chunks)
        self.assertFalse(result["aligned"])
        self.assertEqual(len(result["orphan_chunk_ids"]), 5)

    def test_large_source_allows_orphan_budget(self):
        stages = [
            {
                "title": "章節",
                "key_concepts": ["x"],
                "source_chunk_ids": [f"chunk_{i:04d}" for i in range(95)],
            },
        ]
        chunks = [_chunk(f"chunk_{i:04d}") for i in range(100)]
        result = verify_global_coverage(stages, chunks)
        self.assertTrue(result["aligned"])
        self.assertEqual(len(result["orphan_chunk_ids"]), 5)


if __name__ == "__main__":
    unittest.main()
