"""ContentOutlineAgent 行為測試。"""
import json
import unittest

from backend.agents.base_agent import AgentContext
from backend.agents.content_outline import ContentOutlineAgent, normalize_outline


def _fake_llm(response_dict: dict):
    response_json = json.dumps(response_dict, ensure_ascii=False)

    class _Resp:
        def __init__(self, content):
            self.content = content

    class _LLM:
        async def chat(self, messages, system_prompt=None):
            return _Resp(response_json)

    return _LLM()


def _make_agent(llm):
    agent = ContentOutlineAgent.__new__(ContentOutlineAgent)
    agent.llm = llm
    agent._messages = []
    agent.token_counter = None
    return agent


class TestNormalizeOutline(unittest.TestCase):
    def test_normalize_fills_lists(self):
        out = normalize_outline({"named_cases": ["A"]})
        self.assertEqual(out["named_cases"], ["A"])
        self.assertEqual(out["required_stage_titles"], [])


class TestContentOutlineAgent(unittest.IsolatedAsyncioTestCase):
    async def test_output_shape(self):
        llm = _fake_llm({
            "required_stage_titles": ["案例：QR Code Generator"],
            "named_cases": ["QR Code Generator", "Airbnb Booking"],
            "framework_sections": ["框架"],
            "summary_sections": ["總結"],
            "must_cover_chunks": ["chunk_0000"],
        })
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={"source_chunks": [{"chunk_id": "chunk_0000", "text": "..."}]},
        )
        result = await agent.run(ctx)
        self.assertIn("QR Code Generator", result["named_cases"])
        self.assertIn("Airbnb Booking", result["named_cases"])


if __name__ == "__main__":
    unittest.main()
