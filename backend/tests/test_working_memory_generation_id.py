import unittest

from backend.memory.working_memory import WorkingMemory


class TestWorkingMemoryGenerationId(unittest.TestCase):
    def test_default_is_none(self):
        wm = WorkingMemory("sess_x")
        self.assertIsNone(wm.current_generation_id)

    def test_can_be_set(self):
        wm = WorkingMemory("sess_x")
        wm.current_generation_id = "gen_abc"
        self.assertEqual(wm.current_generation_id, "gen_abc")

    def test_reset_for_new_stage_does_not_clear_generation_id(self):
        wm = WorkingMemory("sess_x")
        wm.current_generation_id = "gen_abc"
        wm.reset_for_new_stage(stage_id=2)
        self.assertEqual(wm.current_generation_id, "gen_abc",
                         "reset_for_new_stage 不應重置 current_generation_id — 它由 run_stage 入口管理")


if __name__ == "__main__":
    unittest.main()
