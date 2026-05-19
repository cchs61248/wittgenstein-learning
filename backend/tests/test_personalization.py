"""個人化教學/出題的單元測試：
- Teacher 根據 stage.kind 切換 lesson_mode_text（標準/補強/重教）
- QuestionGenerator 從 mastery_map 過濾出已掌握概念並嵌入 user message
"""
import unittest

from backend.agents.question_generator import QuestionGeneratorAgent
from backend.agents.teacher import TeacherAgent


def _make_teacher() -> TeacherAgent:
    agent = TeacherAgent.__new__(TeacherAgent)
    agent.llm = None
    agent._messages = []
    return agent


def _make_questioner() -> QuestionGeneratorAgent:
    agent = QuestionGeneratorAgent.__new__(QuestionGeneratorAgent)
    agent.llm = None
    agent._messages = []
    return agent


class TestTeacherLessonMode(unittest.TestCase):
    """stage.kind 不同時，Teacher 應產出不同的 lesson_mode_text。"""

    def test_standard_mode_when_no_kind(self):
        agent = _make_teacher()
        params = agent._build_prompt_params({
            "stage": {"key_concepts": ["A", "B"]},
            "adaptive_context": {},
        })
        text = params["lesson_mode_text"]
        self.assertIn("標準教學模式", text)
        self.assertIn("詳盡", text)

    def test_remediation_mode_signals_partial_coverage(self):
        """補強模式必須明確告訴 LLM：只展開 must_reinforce，已掌握的不要重講。"""
        agent = _make_teacher()
        params = agent._build_prompt_params({
            "stage": {"key_concepts": ["polling"], "kind": "remediation"},
            "adaptive_context": {},
        })
        text = params["lesson_mode_text"]
        self.assertIn("補強模式", text)
        self.assertIn("must_reinforce", text)
        # 補強模式必須明確說「不要重講已掌握」
        self.assertTrue(
            any(s in text for s in ["不要重講", "已掌握的概念請不要"]),
            f"補強模式應明確要求不重講已掌握；實際：{text}",
        )

    def test_reteach_mode_full_re_explain_different_angle(self):
        agent = _make_teacher()
        params = agent._build_prompt_params({
            "stage": {"key_concepts": ["X"], "kind": "reteach"},
            "adaptive_context": {},
        })
        text = params["lesson_mode_text"]
        self.assertIn("重教模式", text)
        # 必須要求不同切入點/類比角度
        self.assertTrue(
            any(s in text for s in ["不同切入點", "不同的類比", "換不同"]),
            f"重教應要求不同角度；實際：{text}",
        )


class TestQuestionGeneratorMasteredFilter(unittest.TestCase):
    """QG 從 mastery_map 過濾已掌握概念（>=0.8），加進 user message。"""

    def test_empty_map_returns_empty_string(self):
        agent = _make_questioner()
        self.assertEqual(agent._format_mastered_concepts({}, ["A", "B"]), "")

    def test_below_threshold_omitted(self):
        agent = _make_questioner()
        out = agent._format_mastered_concepts(
            {"A": 0.5, "B": 0.79, "C": 0.0}, ["A", "B", "C"]
        )
        # 全部 <0.8 → 沒有已掌握概念，回傳空
        self.assertEqual(out, "")

    def test_at_or_above_threshold_listed(self):
        agent = _make_questioner()
        out = agent._format_mastered_concepts(
            {"A": 0.8, "B": 0.95, "C": 0.7}, ["A", "B", "C"]
        )
        self.assertIn("已掌握概念清單", out)
        self.assertIn("A", out)
        self.assertIn("B", out)
        self.assertNotIn("C", out)
        # 必須明確告訴 LLM 不要當主要 key_concepts_tested
        self.assertIn("key_concepts_tested", out)

    def test_custom_threshold(self):
        agent = _make_questioner()
        out = agent._format_mastered_concepts(
            {"A": 0.6, "B": 0.7}, ["A", "B"], threshold=0.55
        )
        self.assertIn("A", out)
        self.assertIn("B", out)


if __name__ == "__main__":
    unittest.main()
