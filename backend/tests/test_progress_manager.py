"""
ProgressManager 單元測試

重點驗證：attempts 來源必須是 current_attempt（第幾輪嘗試），
不可用 len(evaluations)（當輪已答題目數）。

防止回歸場景：
- 簡答題每輪 2 題，attempts 永不觸發 max_attempts
- 選擇題每輪 4 題，第一輪誤判為 reteach/remediate
"""
import asyncio
import unittest
from unittest.mock import MagicMock

from backend.agents.base_agent import AgentContext
from backend.agents.progress_manager import ProgressManagerAgent, correct_mc_score
from backend.utils.token_counter import TokenCounter


def _make_agent() -> ProgressManagerAgent:
    llm = MagicMock()
    tc = TokenCounter()
    return ProgressManagerAgent(llm, tc)


def _ctx(evaluations: list[dict], current_attempt: int, max_attempts: int = 3,
         question_mode: str = "short_answer", total_stages: int = 5,
         current_stage_id: int = 1) -> AgentContext:
    return AgentContext(
        session_id="test-session",
        user_id="test-user",
        task_payload={
            "evaluations": evaluations,
            "current_attempt": current_attempt,
            "pass_threshold": 0.75,
            "max_attempts": max_attempts,
            "question_mode": question_mode,
            "total_stages": total_stages,
            "current_stage_id": current_stage_id,
        },
    )


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestProgressManagerAttempts(unittest.TestCase):

    def test_short_answer_attempt1_should_retry(self):
        """簡答題第 1 輪 2 題未達門檻 → retry（修前因 len=2<3 也是 retry，但理由正確）。"""
        agent = _make_agent()
        evals = [
            {"score": 0.4, "misconception_patterns": [], "confused_concepts": []},
            {"score": 0.5, "misconception_patterns": [], "confused_concepts": []},
        ]
        result = run(agent.run(_ctx(evals, current_attempt=1)))
        self.assertEqual(result["decision"], "retry")

    def test_short_answer_attempt3_low_score_should_reteach(self):
        """簡答題第 3 輪 2 題，latest < 0.5 → reteach。修前 len=2<3 誤判為 retry。"""
        agent = _make_agent()
        evals = [
            {"score": 0.40, "misconception_patterns": [], "confused_concepts": []},
            {"score": 0.45, "misconception_patterns": [], "confused_concepts": []},
        ]
        result = run(agent.run(_ctx(evals, current_attempt=3)))
        self.assertEqual(result["decision"], "reteach",
                         "第 3 輪 latest_score < 0.5 應觸發 reteach，而非繼續 retry")

    def test_multiple_choice_attempt1_should_retry_not_remediate(self):
        """選擇題第 1 輪 4 題未達門檻 → retry。修前 len=4>=3 誤判 reteach/remediate。"""
        agent = _make_agent()
        evals = [
            {"score": 0.2, "misconception_patterns": [], "confused_concepts": []},
            {"score": 0.4, "misconception_patterns": [], "confused_concepts": []},
            {"score": 0.3, "misconception_patterns": [], "confused_concepts": []},
            {"score": 0.5, "misconception_patterns": [], "confused_concepts": []},
        ]
        result = run(agent.run(_ctx(evals, current_attempt=1, question_mode="multiple_choice")))
        self.assertEqual(result["decision"], "retry",
                         "選擇題第 1 輪 4 題未達門檻應為 retry，不應因題目多而誤判")

    def test_high_score_should_advance_regardless_of_question_count(self):
        """best_score 達標 → advance，無論題目數為何。"""
        agent = _make_agent()
        evals = [
            {"score": 0.8, "misconception_patterns": [], "confused_concepts": []},
            {"score": 0.6, "misconception_patterns": [], "confused_concepts": []},
        ]
        result = run(agent.run(_ctx(evals, current_attempt=1)))
        self.assertEqual(result["decision"], "advance")
        self.assertAlmostEqual(result["best_score"], 0.8)

    def test_high_severity_triggers_reteach_immediately(self):
        """high severity misconception → 立即 reteach，即使 attempts 未達上限。"""
        agent = _make_agent()
        evals = [
            {
                "score": 0.5,
                "misconception_patterns": [
                    {"concept": "條件機率", "pattern": "混淆方向", "severity": "high",
                     "repair_strategy": "換框架"}
                ],
                "confused_concepts": ["條件機率"],
            },
        ]
        result = run(agent.run(_ctx(evals, current_attempt=1)))
        self.assertEqual(result["decision"], "reteach")

    def test_repeated_pattern_triggers_reteach(self):
        """同一 pattern 出現 >= 2 次 → reteach，即使 attempts 未達上限。"""
        agent = _make_agent()
        evals = [
            {
                "score": 0.5,
                "misconception_patterns": [
                    {"concept": "X", "pattern": "把因果搞反", "severity": "medium",
                     "repair_strategy": "換例子"}
                ],
                "confused_concepts": [],
            },
            {
                "score": 0.5,
                "misconception_patterns": [
                    {"concept": "X", "pattern": "把因果搞反", "severity": "medium",
                     "repair_strategy": "換例子"}
                ],
                "confused_concepts": [],
            },
        ]
        result = run(agent.run(_ctx(evals, current_attempt=2)))
        self.assertEqual(result["decision"], "reteach")

    def test_fallback_when_current_attempt_missing(self):
        """未傳 current_attempt 時 fallback 使用 len(evaluations)，不應崩潰。"""
        agent = _make_agent()
        ctx = AgentContext(
            session_id="s", user_id="u",
            task_payload={
                "evaluations": [
                    {"score": 0.4, "misconception_patterns": [], "confused_concepts": []},
                    {"score": 0.5, "misconception_patterns": [], "confused_concepts": []},
                ],
                "pass_threshold": 0.75, "max_attempts": 3,
                "question_mode": "short_answer", "total_stages": 5, "current_stage_id": 1,
            },
        )
        result = run(agent.run(ctx))
        self.assertIn(result["decision"], ("retry", "remediate", "reteach", "advance"))

    def test_correct_mc_score_formula(self):
        """選擇題猜測校正公式驗證：正確率 0.25（全猜）應校正為 0.0。"""
        self.assertAlmostEqual(correct_mc_score(0.25), 0.0)
        self.assertAlmostEqual(correct_mc_score(1.0), 1.0)
        self.assertAlmostEqual(correct_mc_score(0.0), 0.0)  # 下限 0.0


if __name__ == "__main__":
    unittest.main()
