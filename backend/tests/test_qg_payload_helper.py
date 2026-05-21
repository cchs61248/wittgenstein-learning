import unittest

from backend.orchestrator.qg_payload import build_qg_task_payload


class TestQgPayloadHelper(unittest.TestCase):
    def test_includes_teaching_intent_and_must_reinforce(self):
        payload = build_qg_task_payload(
            stage={"stage_id": 1, "title": "T", "key_concepts": ["A"], "content": "c"},
            full_explanation="講解全文",
            teaching_intent={"reinforced_concepts": ["A"]},
            adaptive_ctx={
                "allowed_evidence": [{"chunk_id": "c1"}],
                "learner_state": {"mastery_map": {"A": 0.5}},
                "next_lesson_requirements": {"must_reinforce": ["A"]},
            },
            question_mode="short_answer",
            attempt_number=2,
            previous_question_ids=["q1"],
            previous_question_texts=["舊題"],
        )
        self.assertEqual(payload["teaching_intent"]["reinforced_concepts"], ["A"])
        self.assertEqual(payload["must_reinforce"], ["A"])
        self.assertEqual(payload["attempt_number"], 2)
        self.assertEqual(payload["previous_question_ids"], ["q1"])
        self.assertIn("allowed_evidence", payload)


if __name__ == "__main__":
    unittest.main()
