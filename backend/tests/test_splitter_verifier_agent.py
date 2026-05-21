"""SplitterVerifierAgent 行為測試（L2 payload + L3 行為）。

對應 spec: docs/superpowers/specs/2026-05-21-splitter-verifier-agent-design.md § 3-4
"""
import json
import unittest

from backend.agents.base_agent import AgentContext
from backend.agents.splitter_verifier import SplitterVerifierAgent


def _capture_llm():
    """記錄收到 messages 的 mock LLM、回固定 aligned=true 回應。"""
    captured = {"messages": None, "system_prompt": None}

    class _Resp:
        content = '{"aligned": true, "missing_options": [], "issue_chunk_ids": [], "reason": "ok"}'

    class _LLM:
        async def chat(self, messages, system_prompt=None):
            captured["messages"] = messages
            captured["system_prompt"] = system_prompt
            return _Resp()

    return _LLM(), captured


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
    agent = SplitterVerifierAgent.__new__(SplitterVerifierAgent)
    agent.llm = llm
    agent._messages = []
    agent.token_counter = None
    return agent


# ── L2: payload 介面 ──────────────────────────────────────────

class TestVerifierAgentPayloadShape(unittest.IsolatedAsyncioTestCase):
    async def test_user_message_contains_source_chunks_and_stages(self):
        llm, captured = _capture_llm()
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "source_chunks": [{"chunk_id": "chunk_0021", "text": "..."}],
                "stages": [
                    {"stage_id": 1, "title": "test", "key_concepts": ["A"]},
                ],
            },
        )
        await agent.run(ctx)
        user_msg = "\n".join(m.content for m in captured["messages"])
        # 必須注入 source_chunks 與 stages
        self.assertIn("source_chunks", user_msg)
        self.assertIn("chunk_0021", user_msg)
        self.assertIn("stages", user_msg)
        self.assertIn("test", user_msg)


class TestVerifierAgentOutputShape(unittest.IsolatedAsyncioTestCase):
    async def test_output_has_four_fields(self):
        llm = _fake_llm({
            "aligned": False,
            "missing_options": ["房屋貸款"],
            "issue_chunk_ids": ["chunk_0021"],
            "reason": "test reason",
        })
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "source_chunks": [{"chunk_id": "chunk_0021", "text": "..."}],
                "stages": [],
            },
        )
        result = await agent.run(ctx)
        self.assertFalse(result["aligned"])
        self.assertEqual(result["missing_options"], ["房屋貸款"])
        self.assertEqual(result["issue_chunk_ids"], ["chunk_0021"])
        self.assertEqual(result["reason"], "test reason")


# ── L3: 行為驗證 ──────────────────────────────────────────────

class TestVerifierAgentBehavior(unittest.IsolatedAsyncioTestCase):
    async def test_aligned_true_propagates(self):
        llm = _fake_llm({
            "aligned": True,
            "missing_options": [],
            "issue_chunk_ids": [],
            "reason": "all 3 stages covered",
        })
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={"source_chunks": [], "stages": []},
        )
        result = await agent.run(ctx)
        self.assertTrue(result["aligned"])
        self.assertEqual(result["missing_options"], [])

    async def test_aligned_false_with_missing_options(self):
        """spec § 4.2 範例 B：bug case 場景。"""
        llm = _fake_llm({
            "aligned": False,
            "missing_options": ["房屋貸款"],
            "issue_chunk_ids": ["chunk_0021"],
            "reason": "stages 只切 2 個、缺（二）房貸",
        })
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "source_chunks": [{"chunk_id": "chunk_0021",
                                    "text": "借錢外掛分為 3 種：信貸、房貸、股票質押..."}],
                "stages": [
                    {"stage_id": 9, "title": "借錢外掛（一）：信用貸款",
                     "key_concepts": ["軍公教信貸"]},
                    {"stage_id": 10, "title": "借錢外掛（三）：股票質押",
                     "key_concepts": ["融資型房貸", "元大證金質押"]},
                ],
            },
        )
        result = await agent.run(ctx)
        self.assertFalse(result["aligned"])
        self.assertIn("房屋貸款", result["missing_options"])
        self.assertIn("chunk_0021", result["issue_chunk_ids"])

    async def test_malformed_json_raises(self):
        """LLM 回非 JSON、agent 拋 exception（讓 orchestrator catch 走 fail-safe）。"""
        class _BadLLM:
            async def chat(self, messages, system_prompt=None):
                class _R:
                    content = "not a json"
                return _R()

        agent = _make_agent(_BadLLM())
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={"source_chunks": [], "stages": []},
        )
        with self.assertRaises(Exception):
            await agent.run(ctx)


if __name__ == "__main__":
    unittest.main()
