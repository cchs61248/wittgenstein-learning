import unittest

from backend.agents.teacher import TeacherAgent


class TestIntentInline(unittest.IsolatedAsyncioTestCase):
    async def test_chunks_before_marker_emitted(self):
        async def fake_stream(messages, system_prompt):
            for c in ["講解第一段。", "講解第二段。", "<<INTENT_JSON>>"]:
                yield c
            for c in ['{"key_concepts": ["x"]}', "<<END_INTENT>>"]:
                yield c

        llm = type("L", (), {"stream_chat": staticmethod(fake_stream)})()
        agent = TeacherAgent.__new__(TeacherAgent)
        agent.llm = llm
        agent._messages = []
        agent._system = "x"

        emitted = []
        async for c in agent.stream_explanation_with_intent(ctx=None):
            emitted.append(c)

        self.assertEqual("".join(emitted), "講解第一段。講解第二段。")
        self.assertEqual(agent.last_intent, {"key_concepts": ["x"]})

    async def test_marker_split_across_chunks(self):
        async def fake_stream(messages, system_prompt):
            yield "內容。<<INTENT"
            yield "_JSON>>"
            yield '{"key_concepts": []}<<END_INTENT>>'

        llm = type("L", (), {"stream_chat": staticmethod(fake_stream)})()
        agent = TeacherAgent.__new__(TeacherAgent)
        agent.llm = llm
        agent._messages = []
        agent._system = "x"

        emitted = []
        async for c in agent.stream_explanation_with_intent(ctx=None):
            emitted.append(c)

        self.assertEqual("".join(emitted), "內容。")
        self.assertEqual(agent.last_intent, {"key_concepts": []})

    async def test_missing_intent_falls_back_gracefully(self):
        async def fake_stream(messages, system_prompt):
            for c in ["講解第一段。", "結束。"]:
                yield c

        llm = type("L", (), {"stream_chat": staticmethod(fake_stream)})()
        agent = TeacherAgent.__new__(TeacherAgent)
        agent.llm = llm
        agent._messages = []
        agent._system = "x"

        emitted = []
        async for c in agent.stream_explanation_with_intent(ctx=None):
            emitted.append(c)

        self.assertEqual("".join(emitted), "講解第一段。結束。")
        self.assertIsNone(agent.last_intent)

    async def test_emitted_chunks_never_contain_marker_substring(self):
        """v2 新增：無論標記如何被切斷，emit 出去的 chunk 串接後絕不含 INTENT 標記字串。"""
        async def fake_stream(messages, system_prompt):
            for c in ["前段。", "<<INTE", "NT_JSON", ">>", '{"k": 1}', "<<END_", "INTENT>>"]:
                yield c

        llm = type("L", (), {"stream_chat": staticmethod(fake_stream)})()
        agent = TeacherAgent.__new__(TeacherAgent)
        agent.llm = llm
        agent._messages = []
        agent._system = "x"

        emitted = []
        async for c in agent.stream_explanation_with_intent(ctx=None):
            emitted.append(c)

        full = "".join(emitted)
        self.assertNotIn("<<INTENT_JSON>>", full)
        self.assertNotIn("<<END_INTENT>>", full)
        self.assertEqual(full, "前段。")
        self.assertEqual(agent.last_intent, {"k": 1})


if __name__ == "__main__":
    unittest.main()
