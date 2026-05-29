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
        # 重構後措辭：【repair_plan 處理規則】（previous_attempt_missed 表示上一輪未過）
        self.assertIn("上一輪切分未通過", prompt)
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

    def test_prompt_has_summary_stage_kc_anchor_rule(self):
        prompt = SYSTEM_PROMPTS["content_splitter"]
        # 重構後措辭：「summary / checklist / 面試類 stage 規則」
        self.assertIn("面試類 stage", prompt)
        self.assertIn("章節總結", prompt)
        # 規則語意：summary stage 的 kc 必須能在原文找到 anchor，不可只用 meta 標籤
        self.assertIn("字面或同義 anchor", prompt)


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

    async def test_injects_must_cover_topics_from_tier3(self):
        """V2 tier-3 MacroRegionPlanner refinement → per-region splitter 必須收到 must_cover_topics 強約束段。"""
        llm, captured = _capture_llm()
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "source_chunks": [{"chunk_id": "c1", "text": "...", "order_index": 0}],
                "max_stages": 5,
                "must_cover_topics": ["賭徒謬誤", "過度自信", "錨定效應", "框架效應"],
            },
        )
        await agent.run(ctx)
        user_msg = "\n".join(m.content for m in captured["messages"])
        self.assertIn("強約束", user_msg)
        self.assertIn("MacroRegionPlanner tier-3", user_msg)
        self.assertIn("賭徒謬誤", user_msg)
        self.assertIn("過度自信", user_msg)
        self.assertIn("錨定效應", user_msg)
        self.assertIn("框架效應", user_msg)
        # 4+ 概念時要求獨立 stage
        self.assertIn("獨立 stage", user_msg)

    async def test_skips_must_cover_section_when_empty(self):
        llm, captured = _capture_llm()
        agent = _make_agent(llm)
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "source_chunks": [{"chunk_id": "c1", "text": "...", "order_index": 0}],
                "must_cover_topics": [],  # 空 list
            },
        )
        await agent.run(ctx)
        user_msg = "\n".join(m.content for m in captured["messages"])
        self.assertNotIn("MacroRegionPlanner tier-3", user_msg)


class TestContentSplitterPlanCParsingAndMerge(unittest.IsolatedAsyncioTestCase):
    async def test_artifact_json_preserves_outline_thin_stages(self):
        content = """````artifact
id: api-design-learning-stages
name: API 設計學習階段規劃
type: json
content: |-
  {
    "stages": [
      {"stage_id": 1, "node_id": "1.1", "title": "API 風格選型框架", "source_chunk_ids": ["c1"], "key_concepts": ["框架"], "prerequisites": [], "estimated_questions": 2, "teaching_goal": "g1"},
      {"stage_id": 2, "node_id": "2.1", "title": "案例：QR Code Generator", "source_chunk_ids": ["c2"], "key_concepts": ["REST"], "prerequisites": [], "estimated_questions": 2, "teaching_goal": "g2"},
      {"stage_id": 3, "node_id": "2.2", "title": "案例：Airbnb Booking", "source_chunk_ids": ["c2"], "key_concepts": ["GraphQL"], "prerequisites": [], "estimated_questions": 2, "teaching_goal": "g3"},
      {"stage_id": 4, "node_id": "2.3", "title": "案例：Webhook Platform", "source_chunk_ids": ["c3"], "key_concepts": ["Webhook"], "prerequisites": [], "estimated_questions": 2, "teaching_goal": "g4"},
      {"stage_id": 5, "node_id": "2.4", "title": "案例：ChatGPT Tasks", "source_chunk_ids": ["c3"], "key_concepts": ["RPC"], "prerequisites": [], "estimated_questions": 2, "teaching_goal": "g5"},
      {"stage_id": 6, "node_id": "3.1", "title": "面試應答與總結", "source_chunk_ids": ["c4"], "key_concepts": ["面試"], "prerequisites": [], "estimated_questions": 2, "teaching_goal": "g6"}
    ],
    "chunk_roles": {"c1": "core", "c2": "core", "c3": "core", "c4": "core"},
    "summary": "s"
  }
````"""

        class _Resp:
            def __init__(self, content):
                self.content = content

        class _LLM:
            async def chat(self, messages, system_prompt=None):
                return _Resp(content)

        agent = _make_agent(_LLM())
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "source_chunks": [
                    {"chunk_id": "c1", "text": "t1", "order_index": 0},
                    {"chunk_id": "c2", "text": "t2", "order_index": 1},
                    {"chunk_id": "c3", "text": "t3", "order_index": 2},
                    {"chunk_id": "c4", "text": "t4", "order_index": 3},
                ],
                "required_outline": {
                    "required_stage_titles": [
                        "API 風格選型框架", "案例：QR Code Generator",
                        "案例：Airbnb Booking", "案例：Webhook Platform",
                        "案例：ChatGPT Tasks", "面試應答與總結",
                    ],
                    "named_cases": [
                        "QR Code Generator", "Airbnb Booking",
                        "Webhook Platform", "ChatGPT Tasks",
                    ],
                },
            },
        )
        result = await agent.run(ctx)
        titles = [s["title"] for s in result["stages"]]
        self.assertEqual(len(titles), 6)
        self.assertIn("案例：Airbnb Booking", titles)
        self.assertIn("案例：ChatGPT Tasks", titles)

    async def test_outline_mode_still_merges_duplicate_topic_stages(self):
        content = """{
          "stages": [
            {"stage_id": 1, "node_id": "1.1", "title": "REST 基礎", "source_chunk_ids": ["a1"], "key_concepts": ["REST"], "prerequisites": [], "estimated_questions": 2, "teaching_goal": "g1"},
            {"stage_id": 2, "node_id": "1.2", "title": "REST 基礎", "source_chunk_ids": ["b1"], "key_concepts": ["REST", "HTTP"], "prerequisites": [], "estimated_questions": 2, "teaching_goal": "g2"}
          ],
          "chunk_roles": {"a1": "core", "b1": "core"},
          "summary": "s"
        }"""

        class _Resp:
            def __init__(self, content):
                self.content = content

        class _LLM:
            async def chat(self, messages, system_prompt=None):
                return _Resp(content)

        agent = _make_agent(_LLM())
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "source_chunks": [
                    {"chunk_id": "a1", "text": "source A", "order_index": 0, "source_label": "A", "source_index": 0},
                    {"chunk_id": "b1", "text": "source B", "order_index": 1, "source_label": "B", "source_index": 1},
                ],
                "required_outline": {"required_stage_titles": ["REST 基礎"]},
            },
        )
        result = await agent.run(ctx)
        self.assertEqual(len(result["stages"]), 1)
        self.assertEqual(result["stages"][0]["source_chunk_ids"], ["a1", "b1"])
        self.assertEqual(result["stages"][0]["key_concepts"], ["REST", "HTTP"])


if __name__ == "__main__":
    unittest.main()
