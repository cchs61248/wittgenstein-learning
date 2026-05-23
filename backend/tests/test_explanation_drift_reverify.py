"""Teacher rewrite 後二次 explanation DriftVerifier 閉環回歸。"""
import inspect
import unittest

from backend.orchestrator.learning_orchestrator import LearningOrchestrator


class TestExplanationDriftReverify(unittest.TestCase):
    def test_run_stage_invokes_post_rewrite_explanation_verify(self):
        src = inspect.getsource(LearningOrchestrator.run_stage)
        self.assertIn("explanation_rewritten = True", src)
        self.assertIn("explain_verify = await self._verify_grounding", src)
        self.assertIn('content_type="explanation"', src)
        idx_rewrite = src.index("explanation_rewritten = True")
        idx_first_verify = src.index("explain_verify = await self._verify_grounding")
        self.assertGreater(idx_rewrite, idx_first_verify)

    def test_run_stage_supports_multiple_rewrites(self):
        src = inspect.getsource(LearningOrchestrator.run_stage)
        self.assertIn("max_explanation_rewrites = 2", src)
        self.assertIn("while (", src)
        self.assertIn("rewrite_attempt < max_explanation_rewrites", src)

    def test_run_stage_blocks_qg_when_grounding_fails(self):
        src = inspect.getsource(LearningOrchestrator.run_stage)
        self.assertIn("explanation_grounded", src)
        self.assertIn("if explanation_grounded:", src)
        self.assertIn("Question generation blocked", src)
        self.assertIn("blocking_qg=true", src)


if __name__ == "__main__":
    unittest.main()
