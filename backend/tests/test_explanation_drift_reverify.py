"""Teacher rewrite 後二次 explanation DriftVerifier 閉環回歸。"""
import inspect
import unittest

from backend.orchestrator.learning_orchestrator import LearningOrchestrator


class TestExplanationDriftReverify(unittest.TestCase):
    def test_run_stage_invokes_post_rewrite_explanation_verify(self):
        src = inspect.getsource(LearningOrchestrator.run_stage)
        self.assertIn("explanation_rewritten = True", src)
        self.assertIn("post_rewrite_verify = await self._verify_grounding", src)
        self.assertIn('content_type="explanation"', src)
        idx_rewrite = src.index("post_rewrite_verify")
        idx_first_verify = src.index("explain_verify = await self._verify_grounding")
        self.assertGreater(idx_rewrite, idx_first_verify)


if __name__ == "__main__":
    unittest.main()
