"""concept_normalize utility + 整合到 evaluator/progress_manager 的 regression tests。

目的：確保跨 agent 的 concept 命名都對齊到 stage.key_concepts，
避免 concept_mastery 表跨章節碎片化（同一概念在不同 stage 用不同命名累積成兩筆）。
"""
import json
import unittest

from backend.agents.base_agent import AgentContext
from backend.agents.evaluator import EvaluatorAgent
from backend.agents.progress_manager import _unique_confused_concepts
from backend.utils.concept_normalize import (
    normalize_concept,
    normalize_concepts,
    normalize_misconception_patterns,
)


class TestNormalizeConcept(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(
            normalize_concept("融資型房貸", ["融資型房貸", "股票質押"]),
            "融資型房貸",
        )

    def test_raw_in_canonical_returns_canonical(self):
        """raw 較簡略、canonical 較具體：對到 canonical。"""
        self.assertEqual(
            normalize_concept("房貸", ["融資型房貸", "信用貸款"]),
            "融資型房貸",
        )

    def test_canonical_in_raw_returns_canonical(self):
        """raw 較具體、canonical 較簡略：對到 canonical。"""
        self.assertEqual(
            normalize_concept(
                "融資型房貸的零支付機制", ["融資型房貸", "股票質押"]
            ),
            "融資型房貸",
        )

    def test_longest_match_preferred(self):
        """有多個候選時，選最長匹配。"""
        # raw="房貸" 同時含於「房貸」和「融資型房貸」，應選後者（更具體、長度更長）
        self.assertEqual(
            normalize_concept("房貸", ["房貸利率", "融資型房貸"]),
            "融資型房貸",
        )

    def test_no_match_returns_none(self):
        self.assertIsNone(
            normalize_concept("polling 機制", ["融資型房貸", "股票質押"])
        )

    def test_empty_canonical_passes_through(self):
        """無規範清單時，直接回傳 raw（穩健 fallback）。"""
        self.assertEqual(normalize_concept("任意概念", []), "任意概念")

    def test_empty_raw_returns_none(self):
        self.assertIsNone(normalize_concept("", ["融資型房貸"]))
        self.assertIsNone(normalize_concept(None, ["融資型房貸"]))


class TestNormalizeConcepts(unittest.TestCase):
    def test_filters_unmatched_preserves_order(self):
        out = normalize_concepts(
            ["房貸", "polling", "股票質押"],
            ["融資型房貸", "股票質押", "信用貸款"],
        )
        self.assertEqual(out, ["融資型房貸", "股票質押"])

    def test_dedupes_aligned(self):
        """raw 兩個不同字串對到同一 canonical 應只保留一份。"""
        out = normalize_concepts(
            ["房貸", "融資型房貸"], ["融資型房貸"]
        )
        self.assertEqual(out, ["融資型房貸"])


class TestNormalizeMisconceptionPatterns(unittest.TestCase):
    def test_aligns_concept_preserves_pattern_fields(self):
        raw = [
            {
                "concept": "房貸",
                "pattern": "把房貸利率當成決定性因素",
                "severity": "medium",
            }
        ]
        out = normalize_misconception_patterns(raw, ["融資型房貸"])
        self.assertEqual(out[0]["concept"], "融資型房貸")
        self.assertEqual(out[0]["pattern"], "把房貸利率當成決定性因素")
        self.assertEqual(out[0]["severity"], "medium")

    def test_unalignable_concept_set_to_none(self):
        raw = [{"concept": "polling", "pattern": "誤解輪詢機制"}]
        out = normalize_misconception_patterns(raw, ["融資型房貸"])
        self.assertIsNone(out[0]["concept"])
        self.assertEqual(out[0]["pattern"], "誤解輪詢機制")


def _make_evaluator() -> EvaluatorAgent:
    agent = EvaluatorAgent.__new__(EvaluatorAgent)
    agent.llm = None
    agent._messages = []
    return agent


class TestEvaluatorMCCorrectNormalize(unittest.IsolatedAsyncioTestCase):
    """MC 答對路徑（line 99 那段不呼叫 LLM）也要 normalize key_concepts_tested。"""

    async def test_mc_correct_aligns_understood_to_stage_concepts(self):
        agent = _make_evaluator()
        ctx = AgentContext(
            session_id="s1",
            user_id="u1",
            task_payload={
                "question": {
                    "question_id": "q1",
                    "text": "Q",
                    "answer_mode": "multiple_choice",
                    "correct_option_id": "A",
                    "options": [{"id": "A", "text": "正確答案"}],
                    # QG 自創命名（不在 stage.key_concepts 內），但是 stage 有「融資型房貸」
                    "key_concepts_tested": ["房貸"],
                },
                "user_answer": "A",
                "stage_key_concepts": ["融資型房貸", "股票質押"],
            },
        )
        result = await agent.run(ctx)
        self.assertEqual(result["score"], 1.0)
        # MC correct 路徑：「房貸」應 normalize 成「融資型房貸」
        self.assertEqual(result["understood_concepts"], ["融資型房貸"])

    async def test_mc_correct_drops_unalignable_concepts(self):
        """QG 命名完全不在 stage 範圍 → understood_concepts 變空（避免污染 DB）。"""
        agent = _make_evaluator()
        ctx = AgentContext(
            session_id="s1",
            user_id="u1",
            task_payload={
                "question": {
                    "question_id": "q1",
                    "text": "Q",
                    "answer_mode": "multiple_choice",
                    "correct_option_id": "A",
                    "options": [{"id": "A", "text": "正確"}],
                    "key_concepts_tested": ["利息與金額的取捨"],
                },
                "user_answer": "A",
                "stage_key_concepts": ["安全第一原則", "現金股利支付利息"],
            },
        )
        result = await agent.run(ctx)
        self.assertEqual(result["understood_concepts"], [])


class TestEvaluatorLLMNormalize(unittest.IsolatedAsyncioTestCase):
    """LLM 評分後的輸出 normalize。"""

    async def test_llm_output_normalize_three_fields(self):
        """understood / confused / misconception_patterns[].concept 三欄都要 normalize。"""
        # 模擬 LLM 用稍微偏離的命名回應
        llm_response = {
            "score": 0.5,
            "understood_concepts": ["房貸"],
            "confused_concepts": ["質押"],
            "misconception_patterns": [
                {
                    "concept": "質押",
                    "pattern": "誤以為借滿 60% 才划算",
                    "severity": "medium",
                }
            ],
            "feedback": "feedback text",
        }

        class _Resp:
            def __init__(self, content):
                self.content = content

        class _LLM:
            async def chat(self, messages, system_prompt=None):
                return _Resp(json.dumps(llm_response, ensure_ascii=False))

        agent = EvaluatorAgent.__new__(EvaluatorAgent)
        agent.llm = _LLM()
        agent._messages = []
        ctx = AgentContext(
            session_id="s1",
            user_id="u1",
            task_payload={
                "question": {
                    "question_id": "q1",
                    "text": "Q",
                    "answer_mode": "short_answer",
                    "key_concepts_tested": ["融資型房貸"],
                },
                "user_answer": "...",
                "source_chunks": [],
                "stage_key_concepts": ["融資型房貸", "股票質押"],
            },
        )
        result = await agent.run(ctx)
        self.assertEqual(result["understood_concepts"], ["融資型房貸"])
        self.assertEqual(result["confused_concepts"], ["股票質押"])
        self.assertEqual(len(result["misconception_patterns"]), 1)
        self.assertEqual(
            result["misconception_patterns"][0]["concept"], "股票質押"
        )
        # pattern 文字必須保留（細節觀察的價值不丟失）
        self.assertIn("60%", result["misconception_patterns"][0]["pattern"])

    async def test_misconception_with_unalignable_concept_dropped(self):
        """LLM 給出完全對不上 stage 的 concept → 該筆 mp 被丟棄（concept=None 後 filter）。"""
        llm_response = {
            "score": 0.3,
            "understood_concepts": [],
            "confused_concepts": [],
            "misconception_patterns": [
                {
                    "concept": "polling 機制",  # 完全對不上
                    "pattern": "誤解輪詢",
                    "severity": "high",
                }
            ],
            "feedback": "f",
        }

        class _Resp:
            def __init__(self, c):
                self.content = c

        class _LLM:
            async def chat(self, messages, system_prompt=None):
                return _Resp(json.dumps(llm_response, ensure_ascii=False))

        agent = EvaluatorAgent.__new__(EvaluatorAgent)
        agent.llm = _LLM()
        agent._messages = []
        ctx = AgentContext(
            session_id="s1",
            user_id="u1",
            task_payload={
                "question": {
                    "question_id": "q1",
                    "text": "Q",
                    "answer_mode": "short_answer",
                    "key_concepts_tested": [],
                },
                "user_answer": "a",
                "source_chunks": [],
                "stage_key_concepts": ["融資型房貸"],
            },
        )
        result = await agent.run(ctx)
        self.assertEqual(result["misconception_patterns"], [])


class TestProgressManagerNormalizesConfused(unittest.TestCase):
    """ProgressManager unique_confused 也要 normalize（防護網）。"""

    def test_normalize_when_stage_key_concepts_given(self):
        evaluations = [
            {"confused_concepts": ["房貸", "polling"]},
            {"confused_concepts": ["股票質押的維持率"]},
        ]
        out = _unique_confused_concepts(
            evaluations, stage_key_concepts=["融資型房貸", "股票質押"]
        )
        self.assertEqual(out, ["融資型房貸", "股票質押"])

    def test_passthrough_when_no_canonical(self):
        """無 canonical 時保留原行為。"""
        evaluations = [{"confused_concepts": ["A", "B"]}]
        self.assertEqual(_unique_confused_concepts(evaluations), ["A", "B"])


if __name__ == "__main__":
    unittest.main()
