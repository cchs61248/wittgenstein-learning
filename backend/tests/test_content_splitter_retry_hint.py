"""content_splitter retry hint 行為測試（L1 prompt + L2 user msg 注入）。

對應 spec: docs/superpowers/specs/2026-05-21-splitter-verifier-agent-design.md § 3.4 + § 4.4-4.5
"""
import unittest

from backend.agents.base_agent import AgentContext
from backend.agents.content_splitter import ContentSplitterAgent
from backend.utils.prompt_templates import SYSTEM_PROMPTS


# ── L1: prompt sanity ──────────────────────────────────────────

class TestContentSplitterPromptHasRetryHintRule(unittest.TestCase):
    def test_prompt_has_retry_hint_rule(self):
        prompt = SYSTEM_PROMPTS["content_splitter"]
        # 規則段標題
        self.assertIn("重試提示", prompt)
        self.assertIn("previous_attempt_missed", prompt)
        # 必須要求「不可 mash-up」與「各自獨立 stage」
        self.assertIn("獨立 stage", prompt)
        self.assertIn("mash-up", prompt)

    def test_prompt_has_required_outline_rule(self):
        prompt = SYSTEM_PROMPTS["content_splitter"]
        self.assertIn("required_outline", prompt)
        self.assertIn("named_cases", prompt)

    def test_prompt_has_repair_plan_struct_rule(self):
        prompt = SYSTEM_PROMPTS["content_splitter"]
        self.assertIn("repair_plan", prompt)
        self.assertIn("forbidden_mixes", prompt)


# ── L2: user message 注入 ──────────────────────────────────────

def _capture_llm():
    captured = {"messages": None}

    class _Resp:
        content = ('{"stages": [{"stage_id": 1, "node_id": "1.1", "title": "t", '
                   '"source_chunk_ids": ["c1"], "key_concepts": ["k"], '
                   '"prerequisites": [], "estimated_questions": 2, "teaching_goal": "g"}], '
                   '"chunk_roles": {"c1": "core"}, "summary": "test"}')

    class _LLM:
        async def chat(self, messages, system_prompt=None):
            captured["messages"] = messages
            return _Resp()

    return _LLM(), captured


def _make_agent(llm):
    agent = ContentSplitterAgent.__new__(ContentSplitterAgent)
    agent.llm = llm
    agent._messages = []
    agent.token_counter = None
    return agent


class TestContentSplitterRetryHintInjection(unittest.IsolatedAsyncioTestCase):
    async def test_injects_retry_hint_when_provided(self):
        llm, captured = _capture_llm()
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "source_chunks": [{"chunk_id": "c1", "text": "...", "order_index": 0}],
                "max_stages": 5,
                "target_depth": "standard",
                "previous_attempt_missed": ["房屋貸款"],
                "issue_chunk_ids": ["chunk_0021"],
            },
        )
        await agent.run(ctx)
        user_msg = "\n".join(m.content for m in captured["messages"])
        # 必須注入重試提示段
        self.assertIn("重試提示", user_msg)
        self.assertIn("previous_attempt_missed", user_msg)
        self.assertIn("房屋貸款", user_msg)
        self.assertIn("chunk_0021", user_msg)

    async def test_injects_retry_hint_when_only_verifier_reason(self):
        llm, captured = _capture_llm()
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "source_chunks": [{"chunk_id": "c1", "text": "...", "order_index": 0}],
                "max_stages": 5,
                "target_depth": "standard",
                "verifier_reason": "stage 2 標題 Webhook 但 key_concepts 為 GraphQL",
            },
        )
        await agent.run(ctx)
        user_msg = "\n".join(m.content for m in captured["messages"])
        self.assertIn("重試提示", user_msg)
        self.assertIn("verifier_reason", user_msg)
        self.assertIn("GraphQL", user_msg)

    async def test_injects_required_outline_when_provided(self):
        llm, captured = _capture_llm()
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "source_chunks": [{"chunk_id": "c1", "text": "...", "order_index": 0}],
                "required_outline": {
                    "named_cases": ["QR Code Generator"],
                    "required_stage_titles": ["案例：QR Code Generator"],
                },
            },
        )
        await agent.run(ctx)
        user_msg = "\n".join(m.content for m in captured["messages"])
        self.assertIn("教材骨架", user_msg)
        self.assertIn("QR Code Generator", user_msg)
        self.assertNotIn("重試提示", user_msg)

    async def test_injects_repair_plan_struct_on_reroll(self):
        llm, captured = _capture_llm()
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "source_chunks": [{"chunk_id": "c1", "text": "...", "order_index": 0}],
                "repair_plan_struct": {
                    "required_stage_titles": ["案例：Airbnb Booking"],
                    "forbidden_mixes": [{
                        "stage_title_hint": "Webhook",
                        "forbidden_concepts": ["GraphQL"],
                    }],
                },
            },
        )
        await agent.run(ctx)
        user_msg = "\n".join(m.content for m in captured["messages"])
        self.assertIn("repair_plan_struct", user_msg)
        self.assertIn("Airbnb Booking", user_msg)
        self.assertIn("forbidden_mixes", user_msg)

    async def test_skips_retry_hint_when_not_provided(self):
        """既有 caller 不傳 previous_attempt_missed、不應注入該段（向後相容）。"""
        llm, captured = _capture_llm()
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "source_chunks": [{"chunk_id": "c1", "text": "...", "order_index": 0}],
                "max_stages": 5,
                "target_depth": "standard",
                # 故意不傳 previous_attempt_missed
            },
        )
        await agent.run(ctx)
        user_msg = "\n".join(m.content for m in captured["messages"])
        self.assertNotIn("重試提示", user_msg)
        self.assertNotIn("previous_attempt_missed", user_msg)


if __name__ == "__main__":
    unittest.main()
