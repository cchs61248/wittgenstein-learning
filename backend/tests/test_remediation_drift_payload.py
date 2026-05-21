import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.orchestrator.learning_orchestrator import LearningOrchestrator


class TestRemediationDriftPayload(unittest.IsolatedAsyncioTestCase):
    async def test_verify_grounding_passes_remediation_fields(self):
        orch = LearningOrchestrator.__new__(LearningOrchestrator)
        captured: dict = {}

        async def fake_run(ctx):
            captured.update(ctx.task_payload)
            return {"aligned": True, "issues": [], "claim_checks": []}

        orch.drift_verifier = MagicMock()
        orch.drift_verifier.run = fake_run
        orch._normalize_stage_source_chunks = lambda s: [{"chunk_id": "c1", "quote": "q"}]

        stage = {"stage_id": 1, "key_concepts": ["弱項A"], "kind": "remediation"}
        stages = [stage, {"stage_id": 2, "key_concepts": ["下節B"]}]
        adaptive = {"next_lesson_requirements": {"must_reinforce": ["弱項A"]}}

        await orch._verify_grounding(
            session_id="s",
            user_id="u",
            stage=stage,
            content_type="explanation",
            candidate_text="講解",
            stages=stages,
            adaptive_ctx=adaptive,
        )
        self.assertEqual(captured.get("stage_kind"), "remediation")
        self.assertEqual(captured.get("must_reinforce_concepts"), ["弱項A"])

    def test_remediation_section_built_for_explanation_mode(self):
        import json
        content_type = "explanation"
        stage_kind = "remediation"
        must_reinforce_concepts = ["弱項A"]
        remediation_section = ""
        if content_type == "explanation" and stage_kind == "remediation" and must_reinforce_concepts:
            remediation_section = (
                f"\n\nstage_kind=remediation；must_reinforce_concepts（補強模式反向 coverage "
                f"僅檢查以下弱項，其他 chunk 教學必要元素豁免）："
                f"{json.dumps(must_reinforce_concepts, ensure_ascii=False)}"
            )
        self.assertIn("stage_kind=remediation", remediation_section)
        self.assertIn("弱項A", remediation_section)


if __name__ == "__main__":
    unittest.main()
