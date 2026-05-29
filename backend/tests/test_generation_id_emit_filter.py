import unittest

from backend.orchestrator.learning_orchestrator import make_generation_scoped_emit


class TestGenerationScopedEmit(unittest.IsolatedAsyncioTestCase):
    async def test_emit_when_generation_matches(self):
        sent = []
        async def base_emit(msg): sent.append(msg)

        wm_holder = {"id": "gen_A"}
        emit = make_generation_scoped_emit(
            base_emit, generation_id="gen_A", get_current=lambda: wm_holder["id"]
        )
        await emit({"type": "explanation_chunk", "payload": {"chunk": "x"}})
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["payload"]["generation_id"], "gen_A")

    async def test_emit_dropped_when_generation_stale(self):
        sent = []
        async def base_emit(msg): sent.append(msg)

        wm_holder = {"id": "gen_B"}  # 已經換成 gen_B
        emit = make_generation_scoped_emit(
            base_emit, generation_id="gen_A", get_current=lambda: wm_holder["id"]
        )
        await emit({"type": "explanation_chunk", "payload": {"chunk": "x"}})
        self.assertEqual(sent, [])  # 被丟棄

    async def test_non_chunk_messages_pass_through_without_id(self):
        """error / kicked 等系統訊息不該被過濾"""
        sent = []
        async def base_emit(msg): sent.append(msg)

        wm_holder = {"id": "gen_B"}
        emit = make_generation_scoped_emit(
            base_emit, generation_id="gen_A", get_current=lambda: wm_holder["id"]
        )
        await emit({"type": "error", "payload": {"message": "fatal"}})
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["type"], "error")


if __name__ == "__main__":
    unittest.main()
