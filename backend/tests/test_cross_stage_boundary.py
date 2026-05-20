"""跨 stage chunk overlap 衝突修正 — Commit 1 regression。

行為驗證：
- Teacher system prompt 含「跨章節 chunk 邊界」規則
- DriftVerifier prompt 含「next_stage_concepts 豁免」規則
- context_builder 把 next_stage_concepts 注入 next_lesson_requirements
- teacher._build_prompt_params 把 next_stage_concepts 轉成 text 放進 prompt params
- drift_verifier user message 注入 next_stage_concepts 給 LLM
"""
import unittest
from unittest.mock import AsyncMock, patch

from backend.orchestrator.context_builder import build_adaptive_context
from backend.agents.teacher import TeacherAgent
from backend.agents.drift_verifier import DriftVerifierAgent
from backend.agents.base_agent import AgentContext
from backend.utils.prompt_templates import SYSTEM_PROMPTS
from backend.utils.token_counter import TokenCounter
from unittest.mock import MagicMock


class TestTeacherPromptHasCrossStageRule(unittest.TestCase):
    def test_teacher_system_prompt_has_cross_stage_boundary_rule(self):
        prompt = SYSTEM_PROMPTS["teacher"]
        self.assertIn("跨章節 chunk 邊界", prompt)
        self.assertIn("next_stage_concepts", prompt)
        # 範例必須說明「禁止完整展開」
        self.assertIn("禁止完整展開", prompt)


class TestDriftVerifierPromptHasExemption(unittest.TestCase):
    def test_drift_verifier_prompt_has_next_stage_concepts_exemption(self):
        prompt = SYSTEM_PROMPTS["drift_verifier"]
        self.assertIn("next_stage_concepts", prompt)
        # 必須明確說「不計入」教學必要元素
        self.assertIn("不計入", prompt)


class TestContextBuilderInjectsNextStageConcepts(unittest.IsolatedAsyncioTestCase):
    async def _run_ctx(self, stage_idx: int, stages: list[dict]) -> dict:
        with patch(
            "backend.orchestrator.context_builder.session_memory.get_source_chunks",
            new=AsyncMock(return_value=[]),
        ), patch(
            "backend.orchestrator.context_builder.session_memory.get_recent_qa_summary",
            new=AsyncMock(return_value=[]),
        ), patch(
            "backend.orchestrator.context_builder.session_memory.get_last_decision_record",
            new=AsyncMock(return_value=None),
        ), patch(
            "backend.orchestrator.context_builder.session_memory.get_source_signature",
            new=AsyncMock(return_value=None),
        ), patch(
            "backend.orchestrator.context_builder.longterm_memory.get_user_mastery_map",
            new=AsyncMock(return_value={}),
        ), patch(
            "backend.orchestrator.context_builder.longterm_memory.get_concept_mastery_map",
            new=AsyncMock(return_value={}),
        ), patch(
            "backend.orchestrator.context_builder.longterm_memory.get_misconceptions",
            new=AsyncMock(return_value=[]),
        ):
            return await build_adaptive_context(
                session_id="s1",
                user_id="u1",
                stage=stages[stage_idx],
                current_attempt=1,
                stages=stages,
            )

    async def test_next_stage_concepts_from_following_stage(self):
        """build_adaptive_context 應從 stages[i+1].key_concepts 抓 next_stage_concepts。"""
        stages = [
            {"stage_id": 1, "key_concepts": ["Hash Modulo", "Cache Miss"], "source_chunk_ids": []},
            {"stage_id": 2, "key_concepts": ["Hash Ring", "順時針尋找"], "source_chunk_ids": []},
            {"stage_id": 3, "key_concepts": ["Ownership Map"], "source_chunk_ids": []},
        ]
        ctx = await self._run_ctx(0, stages)
        self.assertEqual(
            ctx["next_lesson_requirements"]["next_stage_concepts"],
            ["Hash Ring", "順時針尋找"],
        )

    async def test_last_stage_has_empty_next_stage_concepts(self):
        """最後一個 stage 沒有下一節 → next_stage_concepts 為空 list。"""
        stages = [
            {"stage_id": 1, "key_concepts": ["A"], "source_chunk_ids": []},
            {"stage_id": 2, "key_concepts": ["B"], "source_chunk_ids": []},
        ]
        ctx = await self._run_ctx(1, stages)
        self.assertEqual(ctx["next_lesson_requirements"]["next_stage_concepts"], [])

    async def test_forbidden_future_excludes_next_stage_concepts(self):
        """B1 衝突修法：forbidden_future_concepts 不應包含 next_stage_concepts 中的概念，
        避免下一節概念同時落入「禁提」與「可一句帶過」兩個清單給 Teacher 衝突指令。"""
        stages = [
            {"stage_id": 1, "key_concepts": ["Hash Modulo"], "source_chunk_ids": []},
            {"stage_id": 2, "key_concepts": ["Hash Ring", "順時針尋找"], "source_chunk_ids": []},
            {"stage_id": 3, "key_concepts": ["Ownership Map", "Virtual Node"], "source_chunk_ids": []},
        ]
        ctx = await self._run_ctx(0, stages)
        forbidden = ctx["next_lesson_requirements"]["forbidden_future_concepts"]
        next_stage = ctx["next_lesson_requirements"]["next_stage_concepts"]
        self.assertEqual(next_stage, ["Hash Ring", "順時針尋找"])
        # next_stage 概念不該出現在 forbidden_future
        self.assertNotIn("Hash Ring", forbidden)
        self.assertNotIn("順時針尋找", forbidden)
        # 下下節概念仍應在 forbidden_future
        self.assertIn("Ownership Map", forbidden)
        self.assertIn("Virtual Node", forbidden)


class TestTeacherBuildPromptParams(unittest.TestCase):
    def test_teacher_prompt_params_include_next_stage_concepts_text(self):
        agent = TeacherAgent(MagicMock(), TokenCounter())
        payload = {
            "stage": {"stage_id": 1, "key_concepts": ["X"], "title": "t"},
            "adaptive_context": {
                "learner_state": {
                    "mastery_map": {}, "misconceptions": [], "recent_qa_summary": []
                },
                "next_lesson_requirements": {
                    "must_reinforce": [],
                    "forbidden_future_concepts": [],
                    "next_stage_concepts": ["Hash Ring", "順時針尋找"],
                },
            },
        }
        params = agent._build_prompt_params(payload)
        self.assertIn("next_stage_concepts_text", params)
        self.assertIn("Hash Ring", params["next_stage_concepts_text"])
        self.assertIn("順時針尋找", params["next_stage_concepts_text"])

    def test_teacher_prompt_params_empty_next_stage_shows_none(self):
        agent = TeacherAgent(MagicMock(), TokenCounter())
        payload = {
            "stage": {"stage_id": 1, "key_concepts": ["X"], "title": "t"},
            "adaptive_context": {
                "learner_state": {
                    "mastery_map": {}, "misconceptions": [], "recent_qa_summary": []
                },
                "next_lesson_requirements": {
                    "must_reinforce": [],
                    "forbidden_future_concepts": [],
                    "next_stage_concepts": [],
                },
            },
        }
        params = agent._build_prompt_params(payload)
        self.assertEqual(params["next_stage_concepts_text"], "無")


class TestDriftVerifierPayloadAcceptsNextStageConcepts(unittest.IsolatedAsyncioTestCase):
    async def test_drift_verifier_user_message_includes_next_stage_concepts(self):
        """DriftVerifier 從 payload 取出 next_stage_concepts，注入 LLM user message。"""
        llm = MagicMock()
        llm.chat = AsyncMock(return_value=MagicMock(content='{"aligned": true, "issues": []}'))
        agent = DriftVerifierAgent(llm, TokenCounter())
        ctx = AgentContext(
            session_id="s1",
            user_id="u1",
            task_payload={
                "content_type": "explanation",
                "source_chunks": [{"chunk_id": "chunk_0001", "text": "..."}],
                "candidate_text": "Hash Modulo 的講解...",
                "full_explanation": "",
                "next_stage_concepts": ["Hash Ring", "順時針尋找"],
            },
        )
        await agent.run(ctx)
        # 抓 LLM user message
        messages = llm.chat.await_args.args[0]
        user_msg = next(m.content for m in messages if m.role.value == "user")
        self.assertIn("next_stage_concepts", user_msg)
        self.assertIn("Hash Ring", user_msg)


if __name__ == "__main__":
    unittest.main()
