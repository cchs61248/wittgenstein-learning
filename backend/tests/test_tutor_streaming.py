import unittest
from backend.orchestrator.learning_orchestrator import LearningOrchestrator


class TestTutorStreaming(unittest.IsolatedAsyncioTestCase):
    async def test_tutor_emits_chunks_then_complete(self):
        emitted = []
        async def emit(m): emitted.append(m)

        orch = LearningOrchestrator.__new__(LearningOrchestrator)

        async def fake_stream_chat(messages, system_prompt=None):
            for c in ["你", "好", "嗎"]:
                yield c

        llm = type("L", (), {"stream_chat": staticmethod(fake_stream_chat)})()
        orch.teacher = type("T", (), {"llm": llm})()

        full = await orch._stream_tutor_answer(
            messages=[], system_prompt="x", emit=emit,
            stage_id=1, question="?",
        )
        self.assertEqual(full, "你好嗎")

        types = [m["type"] for m in emitted]
        self.assertIn("tutor_chunk", types)
        self.assertEqual(types.count("tutor_chunk"), 3)

        chunk_msgs = [m for m in emitted if m["type"] == "tutor_chunk"]
        self.assertEqual([m["payload"]["chunk"] for m in chunk_msgs], ["你", "好", "嗎"])
        self.assertTrue(all(m["payload"]["stage_id"] == 1 for m in chunk_msgs))


if __name__ == "__main__":
    unittest.main()
