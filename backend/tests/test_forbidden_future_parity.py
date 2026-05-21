import unittest

from backend.orchestrator.stage_boundary import FORBIDDEN_FUTURE_LIMIT, compute_stage_boundary_lists


class TestForbiddenFutureParity(unittest.TestCase):
    def test_limit_constant_is_ten(self):
        self.assertEqual(FORBIDDEN_FUTURE_LIMIT, 10)

    def test_compute_caps_at_ten(self):
        stages = [
            {"stage_id": 0, "key_concepts": ["A"]},
        ]
        for i in range(1, 15):
            stages.append({"stage_id": i, "key_concepts": [f"C{i}"]})
        _, forbidden = compute_stage_boundary_lists(stages[0], stages)
        self.assertLessEqual(len(forbidden), 10)

    def test_teacher_prompt_uses_ten_not_five(self):
        from backend.agents.teacher import TeacherAgent
        agent = TeacherAgent.__new__(TeacherAgent)
        params = agent._build_prompt_params({
            "adaptive_context": {
                "learner_state": {},
                "next_lesson_requirements": {
                    "must_reinforce": [],
                    "forbidden_future_concepts": [f"F{i}" for i in range(12)],
                    "next_stage_concepts": [],
                },
            },
        })
        shown = params["forbidden_future_text"].split("、")
        self.assertLessEqual(len(shown), 10)


if __name__ == "__main__":
    unittest.main()
