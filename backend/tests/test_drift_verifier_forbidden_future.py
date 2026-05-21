"""DriftVerifier 遠期章節（forbidden_future_concepts）豁免（A 方案）regression tests。

設計目的：解決 Teacher 規則 11（遠期 stage 案例略過）與 DriftVerifier 規則 4b
（反向 coverage 只豁免 next_stage_concepts）的衝突。

對應 spec: docs/superpowers/specs/2026-05-21-driftverifier-forbidden-future-exemption-design.md
"""
import json
import unittest
from unittest.mock import AsyncMock, MagicMock

from backend.agents.base_agent import AgentContext
from backend.agents.drift_verifier import DriftVerifierAgent
from backend.utils.prompt_templates import SYSTEM_PROMPTS


# ── L1: prompt sanity ──────────────────────────────────────────

class TestDriftVerifierPromptHasForbiddenFutureExemption(unittest.TestCase):
    def test_prompt_has_forbidden_future_exemption_rule(self):
        prompt = SYSTEM_PROMPTS["drift_verifier"]
        # 規則段標題
        self.assertIn("遠期章節 chunk 豁免", prompt)
        self.assertIn("forbidden_future_concepts", prompt)
        # 必須說明「LLM 語意判定」
        self.assertIn("語意判定", prompt)
        # 必須說明 4 類教學必要元素全部豁免
        self.assertIn("並列方案", prompt)
        self.assertIn("4 類教學必要元素", prompt)

    def test_prompt_has_example_h_with_aligned_true(self):
        prompt = SYSTEM_PROMPTS["drift_verifier"]
        # 範例 H 內含 stage 7 chunk_0021 場景
        self.assertIn("範例 H", prompt)
        self.assertIn("永豐軍公教信貸", prompt)
        self.assertIn("元大證金質押", prompt)
        # 必須有 aligned=true 結論
        self.assertIn("aligned=true", prompt)


# ── L2: user message 注入 ──────────────────────────────────────

def _capture_llm():
    """製造一個 mock LLM、記錄收到的 messages 供斷言。"""
    captured = {"messages": None}

    class _Resp:
        content = '{"aligned": true, "claim_checks": [], "issues": []}'

    class _LLM:
        async def chat(self, messages, system_prompt=None):
            captured["messages"] = messages
            return _Resp()

    return _LLM(), captured


def _make_agent(llm):
    agent = DriftVerifierAgent.__new__(DriftVerifierAgent)
    agent.llm = llm
    agent._messages = []
    agent.token_counter = None
    return agent


class TestDriftVerifierUserMsgContainsForbiddenFuture(unittest.IsolatedAsyncioTestCase):
    async def test_user_msg_contains_forbidden_future_when_provided(self):
        llm, captured = _capture_llm()
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "content_type": "explanation",
                "source_chunks": [{"chunk_id": "chunk_0021", "text": "..."}],
                "candidate_text": "test",
                "full_explanation": "",
                "next_stage_concepts": ["中信融資型房貸"],
                "forbidden_future_concepts": ["元大證金質押", "維持率與斷頭線"],
            },
        )
        await agent.run(ctx)
        user_msg = "\n".join(m.content for m in captured["messages"])
        # 必須含 forbidden_future_concepts 段
        self.assertIn("forbidden_future_concepts", user_msg)
        # 必須含具體清單字面（JSON 形式）
        self.assertIn("元大證金質押", user_msg)
        self.assertIn("維持率與斷頭線", user_msg)
        # 既有 next_stage_concepts 段不能丟
        self.assertIn("next_stage_concepts", user_msg)
        self.assertIn("中信融資型房貸", user_msg)

    async def test_user_msg_skips_forbidden_future_when_empty(self):
        """forbidden_future_concepts=[] 時不應出現該段（保持 prompt 精簡）。"""
        llm, captured = _capture_llm()
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "content_type": "explanation",
                "source_chunks": [],
                "candidate_text": "test",
                "full_explanation": "",
                "next_stage_concepts": [],
                "forbidden_future_concepts": [],
            },
        )
        await agent.run(ctx)
        user_msg = "\n".join(m.content for m in captured["messages"])
        # 空清單不應注入該段
        self.assertNotIn("forbidden_future_concepts（", user_msg)

    async def test_user_msg_skips_forbidden_future_when_not_in_payload(self):
        """payload 完全沒此 key（既有 caller 不傳）時、不應拋錯也不應注入該段。"""
        llm, captured = _capture_llm()
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "content_type": "explanation",
                "source_chunks": [],
                "candidate_text": "test",
                "full_explanation": "",
                # 故意不傳 forbidden_future_concepts、next_stage_concepts
            },
        )
        # 不應 raise
        await agent.run(ctx)
        user_msg = "\n".join(m.content for m in captured["messages"])
        self.assertNotIn("forbidden_future_concepts（", user_msg)


if __name__ == "__main__":
    unittest.main()
