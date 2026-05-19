"""LearningOrchestrator._build_question_retry_guidance helper 測試。

對應 spec：2026-05-19-question-explanation-grounding-design.md §6.2
"""
import unittest

from backend.orchestrator.learning_orchestrator import LearningOrchestrator


class TestBuildQuestionRetryGuidance(unittest.TestCase):
    def setUp(self):
        # 跳過 __init__（會要 LLM/agents），只測 helper 是 pure function
        self.orchestrator = LearningOrchestrator.__new__(LearningOrchestrator)

    def test_guidance_with_unsupported_claims(self):
        verify_result = {
            "aligned": False,
            "unsupported_claims": [
                "polling 機制與 push 機制的差異（漂移）",
                "Kubernetes pod 排程（教材外）",
            ],
        }
        guidance = self.orchestrator._build_question_retry_guidance(verify_result)
        self.assertIn("polling", guidance)
        self.assertIn("Kubernetes", guidance)
        self.assertIn("講解全文", guidance)  # 確認包含「對齊講解」提示

    def test_guidance_without_unsupported_claims(self):
        verify_result = {"aligned": False, "unsupported_claims": []}
        guidance = self.orchestrator._build_question_retry_guidance(verify_result)
        # 退回預設 hint（source_chunks 提醒）
        self.assertIn("source_chunks", guidance)

    def test_guidance_truncates_to_5_claims(self):
        verify_result = {
            "aligned": False,
            "unsupported_claims": [f"claim_{i}" for i in range(10)],
        }
        guidance = self.orchestrator._build_question_retry_guidance(verify_result)
        # 前 5 條都要出現
        for i in range(5):
            self.assertIn(f"claim_{i}", guidance)
        # 後 5 條不應出現（避免 prompt 過長）
        for i in range(5, 10):
            self.assertNotIn(f"claim_{i}", guidance)


if __name__ == "__main__":
    unittest.main()
