"""T-LARGE-SINGLE Phase 1: warn-only `large_single_source_risk` detector.

Pure / read-only / deterministic — flags a single-source curriculum whose entire
chunk set is fed into ONE splitter LLM call with no batching and no upper bound.
Phase 0 (docs/T-LARGE-SINGLE.md §3.1/§3.2) established the two facts this detector
makes observable: the provider output cap (default 4096) is the primary truncation
wall, and a truncated splitter output silently persists a 0-stage curriculum.

This detector NEVER mutates stages and NEVER blocks — observability only.
"""
import copy
import unittest

from backend.utils.small_curriculum import (
    detect_large_single_source_risk,
    LARGE_SINGLE_SOURCE_CHUNK_THRESHOLD,
)


def _single_source_chunks(n: int, *, source_id: str = "src_a") -> list[dict]:
    return [
        {
            "chunk_id": f"chunk_{i:04d}",
            "text": "段落內容 alpha beta gamma delta",
            "source_id": source_id,
            "source_index": 0,
            "order_index": i,
        }
        for i in range(n)
    ]


def _multi_source_chunks(n_per_source: int, n_sources: int = 2) -> list[dict]:
    chunks: list[dict] = []
    order = 0
    for sidx in range(n_sources):
        for i in range(n_per_source):
            chunks.append({
                "chunk_id": f"chunk_{sidx}_{i:04d}",
                "text": "段落內容 alpha beta gamma delta",
                "source_id": f"src_{sidx}",
                "source_index": sidx,
                "order_index": order,
            })
            order += 1
    return chunks


class TestDetectLargeSingleSourceRisk(unittest.TestCase):
    def test_threshold_is_small_file_boundary(self):
        # warn-only floor aligns with the small-file design boundary (50)
        self.assertEqual(LARGE_SINGLE_SOURCE_CHUNK_THRESHOLD, 50)

    def test_positive_observe_at_threshold(self):
        # single source, exactly at the 50-chunk floor, modest max_stages →
        # output estimate stays under 0.8*cap → severity=observe
        out = detect_large_single_source_risk(
            _single_source_chunks(50), max_stages=8,
        )
        self.assertIsNotNone(out)
        self.assertEqual(out["source_count"], 1)
        self.assertEqual(out["chunk_count"], 50)
        self.assertEqual(out["severity"], "observe")
        self.assertEqual(out["risk"], "single_source_all_chunks_single_splitter_call")

    def test_positive_risk_by_chunk_count(self):
        out = detect_large_single_source_risk(
            _single_source_chunks(100), max_stages=8,
        )
        self.assertIsNotNone(out)
        self.assertEqual(out["chunk_count"], 100)
        self.assertEqual(out["severity"], "risk")

    def test_positive_high_risk_by_chunk_count(self):
        out = detect_large_single_source_risk(
            _single_source_chunks(150), max_stages=8,
        )
        self.assertIsNotNone(out)
        self.assertEqual(out["chunk_count"], 150)
        self.assertEqual(out["severity"], "high_risk")

    def test_severity_is_output_aware(self):
        # Phase 0 ②: output cap is the primary wall. Even at the chunk floor (50,
        # which alone would be "observe"), a large max_stages pushes the estimated
        # output past the cap → severity escalates to high_risk.
        out = detect_large_single_source_risk(
            _single_source_chunks(50), max_stages=40,
            provider_max_output_tokens=4096,
        )
        self.assertIsNotNone(out)
        self.assertEqual(out["chunk_count"], 50)
        self.assertGreaterEqual(out["estimated_output_tokens"], 4096)
        self.assertEqual(out["severity"], "high_risk")

    def test_negative_multi_source_large(self):
        # 75 * 2 = 150 chunks but TWO sources → not the single-source failure mode
        out = detect_large_single_source_risk(
            _multi_source_chunks(75, n_sources=2), max_stages=8,
        )
        self.assertIsNone(out)

    def test_negative_single_small(self):
        out = detect_large_single_source_risk(
            _single_source_chunks(49), max_stages=8,
        )
        self.assertIsNone(out)

    def test_negative_empty(self):
        self.assertIsNone(detect_large_single_source_risk([], max_stages=8))

    def test_payload_includes_phase0_facts(self):
        chunks = _single_source_chunks(120)
        out = detect_large_single_source_risk(
            chunks, max_stages=30,
            provider_max_output_tokens=4096,
            explicit_token_budget=False,
        )
        self.assertIsNotNone(out)
        # Phase 0 ② facts surfaced for calibration
        self.assertEqual(out["provider_max_output_tokens"], 4096)
        self.assertFalse(out["explicit_token_budget"])
        # Phase 0 ① fact: empty curriculum silently persists
        self.assertTrue(out["empty_curriculum_fallback_risk"])
        # calibration metrics present
        self.assertEqual(out["max_stages"], 30)
        self.assertEqual(out["threshold"], LARGE_SINGLE_SOURCE_CHUNK_THRESHOLD)
        self.assertIn("estimated_input_tokens", out)
        self.assertIn("estimated_output_tokens", out)
        self.assertIn("total_chars", out)
        self.assertGreater(out["total_chars"], 0)
        self.assertGreater(out["estimated_input_tokens"], 0)
        self.assertGreater(out["estimated_output_tokens"], 0)

    def test_explicit_token_budget_passthrough(self):
        out = detect_large_single_source_risk(
            _single_source_chunks(60), max_stages=8,
            explicit_token_budget=True,
        )
        self.assertIsNotNone(out)
        self.assertTrue(out["explicit_token_budget"])

    def test_custom_provider_cap_changes_severity(self):
        # deepseek-class 32768 output cap → same input no longer output-bound;
        # chunk_count=60 (<100) → severity falls back to observe
        out = detect_large_single_source_risk(
            _single_source_chunks(60), max_stages=8,
            provider_max_output_tokens=32768,
        )
        self.assertIsNotNone(out)
        self.assertEqual(out["severity"], "observe")

    def test_does_not_mutate_input(self):
        chunks = _single_source_chunks(60)
        before = copy.deepcopy(chunks)
        detect_large_single_source_risk(chunks, max_stages=8)
        self.assertEqual(chunks, before)


if __name__ == "__main__":
    unittest.main()
