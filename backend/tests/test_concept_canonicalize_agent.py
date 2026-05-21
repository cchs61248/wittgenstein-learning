"""ConceptCanonicalizeAgent 行為測試（L2 payload + L3 行為 + fallback）。

對應 spec: docs/superpowers/specs/2026-05-21-canonicalize-agent-design.md § 4
"""
import json
import unittest

from backend.agents.base_agent import AgentContext
from backend.agents.concept_canonicalize import ConceptCanonicalizeAgent


def _capture_llm():
    captured = {"messages": None, "system_prompt": None}

    class _Resp:
        content = '{"mappings": []}'

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
    agent = ConceptCanonicalizeAgent.__new__(ConceptCanonicalizeAgent)
    agent.llm = llm
    agent._messages = []
    agent.token_counter = None
    return agent


class TestCanonicalizeAgentPayloadShape(unittest.IsolatedAsyncioTestCase):
    async def test_user_message_contains_new_concepts_and_historical_pool(self):
        llm, captured = _capture_llm()
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "new_concepts": ["A", "B", "C"],
                "historical_pool": [
                    {"concept_name": "X", "total_exposures": 5, "last_tested": "..."},
                ],
            },
        )
        await agent.run(ctx)
        user_msg = "\n".join(m.content for m in captured["messages"])
        self.assertIn("new_concepts", user_msg)
        self.assertIn('"A"', user_msg)
        self.assertIn('"C"', user_msg)
        self.assertIn("historical_pool", user_msg)
        self.assertIn("X", user_msg)
        self.assertIn("total_exposures", user_msg)


class TestCanonicalizeAgentDecisionBehavior(unittest.IsolatedAsyncioTestCase):
    async def test_mapped_decision_keeps_canonical(self):
        llm = _fake_llm({
            "mappings": [
                {"new_name": "巴菲特家世背景", "decision": "mapped",
                 "canonical": "巴菲特神話", "reason": "..."},
            ],
        })
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "new_concepts": ["巴菲特家世背景"],
                "historical_pool": [{"concept_name": "巴菲特神話",
                                     "total_exposures": 6, "last_tested": ""}],
            },
        )
        result = await agent.run(ctx)
        self.assertEqual(len(result["mappings"]), 1)
        self.assertEqual(result["mappings"][0]["decision"], "mapped")
        self.assertEqual(result["mappings"][0]["canonical"], "巴菲特神話")

    async def test_new_decision_keeps_null_canonical(self):
        llm = _fake_llm({
            "mappings": [
                {"new_name": "新概念", "decision": "new",
                 "canonical": None, "reason": "..."},
            ],
        })
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={"new_concepts": ["新概念"], "historical_pool": []},
        )
        result = await agent.run(ctx)
        self.assertEqual(result["mappings"][0]["decision"], "new")
        self.assertIsNone(result["mappings"][0]["canonical"])

    async def test_unsure_decision_keeps_null_canonical(self):
        llm = _fake_llm({
            "mappings": [
                {"new_name": "醫師年薪天花板", "decision": "unsure",
                 "canonical": None, "reason": "角度不同"},
            ],
        })
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "new_concepts": ["醫師年薪天花板"],
                "historical_pool": [{"concept_name": "醫師執照的保障",
                                     "total_exposures": 4, "last_tested": ""}],
            },
        )
        result = await agent.run(ctx)
        self.assertEqual(result["mappings"][0]["decision"], "unsure")
        self.assertIsNone(result["mappings"][0]["canonical"])


class TestCanonicalizeAgentFallback(unittest.IsolatedAsyncioTestCase):
    async def test_omitted_concept_falls_back_to_unsure(self):
        llm = _fake_llm({
            "mappings": [
                {"new_name": "A", "decision": "new", "canonical": None, "reason": ""},
            ],
        })
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={"new_concepts": ["A", "B"], "historical_pool": []},
        )
        result = await agent.run(ctx)
        decisions = {m["new_name"]: m["decision"] for m in result["mappings"]}
        self.assertEqual(decisions["A"], "new")
        self.assertEqual(decisions["B"], "unsure")

    async def test_mapped_with_empty_canonical_degrades_to_unsure(self):
        llm = _fake_llm({
            "mappings": [
                {"new_name": "A", "decision": "mapped",
                 "canonical": None, "reason": ""},
            ],
        })
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "new_concepts": ["A"],
                "historical_pool": [{"concept_name": "X",
                                     "total_exposures": 1, "last_tested": ""}],
            },
        )
        result = await agent.run(ctx)
        self.assertEqual(result["mappings"][0]["decision"], "unsure")
        self.assertIsNone(result["mappings"][0]["canonical"])

    async def test_mapped_with_invalid_canonical_degrades_to_unsure(self):
        llm = _fake_llm({
            "mappings": [
                {"new_name": "A", "decision": "mapped",
                 "canonical": "幻覺概念", "reason": ""},
            ],
        })
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "new_concepts": ["A"],
                "historical_pool": [{"concept_name": "X",
                                     "total_exposures": 1, "last_tested": ""}],
            },
        )
        result = await agent.run(ctx)
        self.assertEqual(result["mappings"][0]["decision"], "unsure")

    async def test_invalid_decision_degrades_to_unsure(self):
        llm = _fake_llm({
            "mappings": [
                {"new_name": "A", "decision": "weird_value",
                 "canonical": None, "reason": ""},
            ],
        })
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={"new_concepts": ["A"], "historical_pool": []},
        )
        result = await agent.run(ctx)
        self.assertEqual(result["mappings"][0]["decision"], "unsure")

    async def test_malformed_json_raises(self):
        class _BadLLM:
            async def chat(self, messages, system_prompt=None):
                class _R:
                    content = "not a json"
                return _R()

        agent = _make_agent(_BadLLM())
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={"new_concepts": ["A"], "historical_pool": []},
        )
        with self.assertRaises(Exception):
            await agent.run(ctx)


if __name__ == "__main__":
    unittest.main()
