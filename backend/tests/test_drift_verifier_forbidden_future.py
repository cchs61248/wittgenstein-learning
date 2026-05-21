"""DriftVerifier 遠期章節（forbidden_future_concepts）豁免（A 方案）regression tests。

設計目的：解決 Teacher 規則 11（遠期 stage 案例略過）與 DriftVerifier 規則 4b
（反向 coverage 只豁免 next_stage_concepts）的衝突。

對應 spec: docs/superpowers/specs/2026-05-21-driftverifier-forbidden-future-exemption-design.md
"""
import json
import unittest
from unittest.mock import AsyncMock, MagicMock

from backend.agents.base_agent import AgentContext
from backend.agents.drift_verifier import DriftVerifierAgent
from backend.utils.prompt_templates import SYSTEM_PROMPTS


# ── L1: prompt sanity ──────────────────────────────────────────

class TestDriftVerifierPromptHasForbiddenFutureExemption(unittest.TestCase):
    def test_prompt_has_forbidden_future_exemption_rule(self):
        prompt = SYSTEM_PROMPTS["drift_verifier"]
        # 規則段標題
        self.assertIn("遠期章節 chunk 豁免", prompt)
        self.assertIn("forbidden_future_concepts", prompt)
        # 必須說明「LLM 語意判定」
        self.assertIn("語意判定", prompt)
        # 必須說明 4 類教學必要元素全部豁免
        self.assertIn("並列方案", prompt)
        self.assertIn("4 類教學必要元素", prompt)

    def test_prompt_has_example_h_with_aligned_true(self):
        prompt = SYSTEM_PROMPTS["drift_verifier"]
        # 範例 H 內含 stage 7 chunk_0021 場景
        self.assertIn("範例 H", prompt)
        self.assertIn("永豐軍公教信貸", prompt)
        self.assertIn("元大證金質押", prompt)
        # 必須有 aligned=true 結論
        self.assertIn("aligned=true", prompt)


if __name__ == "__main__":
    unittest.main()
