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


# ── L2: orchestrator 整合 ──────────────────────────────────────

from unittest.mock import patch
from backend.orchestrator.learning_orchestrator import LearningOrchestrator


class TestOrchestratorPassesForbiddenFutureToDriftVerifier(unittest.IsolatedAsyncioTestCase):
    """`_verify_grounding` helper 內部應從 stages 計算 forbidden_future_concepts
    並塞進 task_payload，與既有 next_stage_concepts 行為一致。
    """

    async def test_forbidden_future_computed_from_stages_beyond_next(self):
        """stages=[1,2,3,4]、當前 stage=1，forbidden_future 應為 stages[2,3] 的 key_concepts
        排除已在 key_concepts_here 與 next_stage 內的概念。
        """
        captured = {"payload": None}

        async def _fake_run(ctx):
            captured["payload"] = ctx.task_payload
            return {
                "aligned": True, "issues": [], "missing_evidence": [],
                "revision_hint": "", "claim_checks": [], "unsupported_claims": [],
            }

        orch = LearningOrchestrator.__new__(LearningOrchestrator)
        orch.drift_verifier = MagicMock()
        orch.drift_verifier.run = AsyncMock(side_effect=_fake_run)
        orch._normalize_stage_source_chunks = lambda s: s.get("source_chunks", [])

        stages = [
            {"stage_id": 1, "key_concepts": ["A1", "A2"], "source_chunks": []},
            {"stage_id": 2, "key_concepts": ["B1", "B2"], "source_chunks": []},
            {"stage_id": 3, "key_concepts": ["C1", "C2"], "source_chunks": []},
            {"stage_id": 4, "key_concepts": ["D1"], "source_chunks": []},
        ]
        await orch._verify_grounding(
            session_id="s1", user_id="u1", stage=stages[0],
            content_type="explanation", candidate_text="...",
            full_explanation="...", stages=stages,
        )
        payload = captured["payload"]
        self.assertEqual(set(payload["next_stage_concepts"]), {"B1", "B2"})
        self.assertEqual(set(payload["forbidden_future_concepts"]), {"C1", "C2", "D1"})

    async def test_forbidden_future_empty_when_no_stages(self):
        """stages=None 時、forbidden_future 應為空 list（與 next_stage 一致行為）。"""
        captured = {"payload": None}

        async def _fake_run(ctx):
            captured["payload"] = ctx.task_payload
            return {
                "aligned": True, "issues": [], "missing_evidence": [],
                "revision_hint": "", "claim_checks": [], "unsupported_claims": [],
            }

        orch = LearningOrchestrator.__new__(LearningOrchestrator)
        orch.drift_verifier = MagicMock()
        orch.drift_verifier.run = AsyncMock(side_effect=_fake_run)
        orch._normalize_stage_source_chunks = lambda s: []

        await orch._verify_grounding(
            session_id="s1", user_id="u1",
            stage={"stage_id": 1, "key_concepts": []},
            content_type="explanation", candidate_text="...",
            full_explanation="...", stages=None,
        )
        payload = captured["payload"]
        self.assertEqual(payload["forbidden_future_concepts"], [])

    async def test_forbidden_future_truncated_to_10(self):
        """forbidden_future > 10 個概念時、應截前 10 筆（避免 prompt 膨脹）。"""
        captured = {"payload": None}

        async def _fake_run(ctx):
            captured["payload"] = ctx.task_payload
            return {
                "aligned": True, "issues": [], "missing_evidence": [],
                "revision_hint": "", "claim_checks": [], "unsupported_claims": [],
            }

        orch = LearningOrchestrator.__new__(LearningOrchestrator)
        orch.drift_verifier = MagicMock()
        orch.drift_verifier.run = AsyncMock(side_effect=_fake_run)
        orch._normalize_stage_source_chunks = lambda s: []

        stages = [
            {"stage_id": 1, "key_concepts": ["A1"], "source_chunks": []},
            {"stage_id": 2, "key_concepts": ["B1"], "source_chunks": []},
        ]
        for i in range(15):
            stages.append({"stage_id": 3 + i, "key_concepts": [f"F{i}"], "source_chunks": []})

        await orch._verify_grounding(
            session_id="s1", user_id="u1", stage=stages[0],
            content_type="explanation", candidate_text="...",
            full_explanation="...", stages=stages,
        )
        payload = captured["payload"]
        self.assertEqual(len(payload["forbidden_future_concepts"]), 10)
        self.assertEqual(payload["forbidden_future_concepts"], [f"F{i}" for i in range(10)])


# ── L3: 行為驗證（mock LLM、驗 agent 處理） ────────────────────

def _fake_llm(response_dict: dict):
    response_json = json.dumps(response_dict, ensure_ascii=False)

    class _Resp:
        def __init__(self, content):
            self.content = content

    class _LLM:
        async def chat(self, messages, system_prompt=None):
            return _Resp(response_json)

    return _LLM()


class TestFarStageCaseExcerptBehavior(unittest.IsolatedAsyncioTestCase):
    """模擬 LLM 對 stage 7 chunk_0021 場景（驗收實 log 衝突案例）的判決——
    forbidden_future_concepts 注入後、agent 應正確 propagate aligned=true。
    """

    async def test_stage_7_chunk_0021_far_stage_aligned(self):
        """A 方案核心目的：信貸（本節）+ 房貸（next_stage 一句帶過）+
        股票質押（forbidden_future、整段豁免）→ aligned=true。
        """
        llm_response = {
            "aligned": True,
            "claim_checks": [],
            "unsupported_claims": [],
            "issues": [],
            "missing_evidence": [],
            "revision_hint": "",
        }
        agent = _make_agent(_fake_llm(llm_response))
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "content_type": "explanation",
                "source_chunks": [
                    {"chunk_id": "chunk_0021",
                     "text": "借錢外掛分為 3 種：信用貸款（永豐軍公教信貸）、"
                             "房屋貸款（中信融資型房貸）、股票質押（元大證金質押）..."},
                ],
                "candidate_text":
                    "本節介紹永豐軍公教信貸的 22 倍月薪上限 [chunk_0021]。"
                    "教材會在下節介紹中信融資型房貸；股票質押則留待後續章節 [chunk_0021]。",
                "full_explanation":
                    "本節介紹永豐軍公教信貸的 22 倍月薪上限 [chunk_0021]。"
                    "教材會在下節介紹中信融資型房貸；股票質押則留待後續章節 [chunk_0021]。",
                "next_stage_concepts": ["中信融資型房貸"],
                "forbidden_future_concepts": ["元大證金質押", "維持率與斷頭線"],
            },
        )
        result = await agent.run(ctx)
        self.assertTrue(result["aligned"])
        self.assertEqual(result["issues"], [])

    async def test_genuine_truncation_still_misaligned_with_empty_forbidden(self):
        """回歸：真正的精簡省略（forbidden_future 為空）仍判 aligned=false、
        不能因為新規則放鬆既有 coverage 檢查。
        """
        llm_response = {
            "aligned": False,
            "claim_checks": [],
            "unsupported_claims": ["精簡省略：教材的台泥虧損 92 億數據沒講解"],
            "issues": ["精簡省略：教材在 chunk_0020 明確列出台泥 2025 前三季虧損 92 億"],
            "missing_evidence": ["台泥 92 億數據"],
            "revision_hint": "請補上台泥案例的具體數據",
        }
        agent = _make_agent(_fake_llm(llm_response))
        ctx = AgentContext(
            session_id="s1", user_id="u1",
            task_payload={
                "content_type": "explanation",
                "source_chunks": [
                    {"chunk_id": "chunk_0020",
                     "text": "台泥 2025 前三季虧損 92 億元，腦袋正常的人都該賣..."},
                ],
                "candidate_text": "教材提到台泥也是賠錢貨 [chunk_0020]。",
                "full_explanation": "教材提到台泥也是賠錢貨 [chunk_0020]。",
                "next_stage_concepts": [],
                "forbidden_future_concepts": [],  # 空清單、不應觸發豁免
            },
        )
        result = await agent.run(ctx)
        self.assertFalse(result["aligned"])
        self.assertIn("台泥", " ".join(result["issues"]))


if __name__ == "__main__":
    unittest.main()
