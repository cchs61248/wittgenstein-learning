"""QG 題目分布均勻化（方案 A：硬 quota） — Commit 3 regression。

行為驗證：
- system prompt 含分布規則段
- user message 在 len(key_concepts) >= 2 時注入「每概念 quota」區塊
- 補強節點（key_concepts 只 1 個）不注入 quota（avoid noise）
- QG 回傳後若違規（單一概念超量），log warning（不 retry）
"""
import logging
import unittest
from unittest.mock import AsyncMock, MagicMock

from backend.agents.base_agent import AgentContext
from backend.agents.question_generator import QuestionGeneratorAgent
from backend.utils.token_counter import TokenCounter


def _make_agent(llm_response_content: str) -> QuestionGeneratorAgent:
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(content=llm_response_content))
    agent = QuestionGeneratorAgent(llm, TokenCounter())
    return agent


def _ctx(stage: dict, **extra) -> AgentContext:
    payload = {
        "stage": stage,
        "num_questions": 6,
        "attempt_number": 1,
        "question_mode": "multiple_choice",
        **extra,
    }
    return AgentContext(session_id="s1", user_id="u1", task_payload=payload)


def _qs_json(concepts_per_q: list[str]) -> str:
    items = []
    for i, kc in enumerate(concepts_per_q, 1):
        items.append(
            '{"question_id":"q_%d","text":"t","type":"understand",'
            '"answer_mode":"multiple_choice",'
            '"options":[{"id":"A","text":"a"},{"id":"B","text":"b"},'
            '{"id":"C","text":"c"},{"id":"D","text":"d"}],'
            '"correct_option_id":"A","difficulty":"easy",'
            '"evidence_chunk_ids":["chunk_0001"],'
            '"key_concepts_tested":["%s"],"expected_answer_hints":["h"]}'
            % (i, kc)
        )
    return '{"questions":[' + ",".join(items) + "]}"


class TestPromptHasDistributionRule(unittest.TestCase):
    def test_qg_system_prompt_has_distribution_rule(self):
        from backend.utils.prompt_templates import SYSTEM_PROMPTS
        prompt = SYSTEM_PROMPTS["question_generator"]
        # 重構後措辭：【題目分配優先序】段
        self.assertIn("題目分配優先序", prompt)
        self.assertIn("每個概念至少出 1 題", prompt)


class TestUserMessageQuotaBlock(unittest.IsolatedAsyncioTestCase):
    async def test_multi_concept_stage_injects_quota_block(self):
        """stage 有 3 個 key_concepts、6 題 → user message 應含 quota 區塊。"""
        agent = _make_agent(_qs_json(["A"] * 6))  # LLM 隨便回，這裡只看 user msg
        stage = {
            "stage_id": 3,
            "title": "節點變動的影響範圍",
            "key_concepts": ["局部重分配", "Ownership Map", "擴容成本"],
            "content": "...",
            "source_chunks": [{"chunk_id": "chunk_0001", "quote": "..."}],
        }
        await agent.run(_ctx(stage))

        # 抓 LLM 收到的 user message
        call_args = agent.llm.chat.await_args
        messages = call_args.args[0]
        user_msg = next(m.content for m in messages if m.role.value == "user")

        self.assertIn("本階段關鍵概念與配額", user_msg)
        self.assertIn("局部重分配", user_msg)
        self.assertIn("Ownership Map", user_msg)
        self.assertIn("擴容成本", user_msg)
        # ceil(6/3) + 1 = 3，3 個 concept、6 題建議 2/2/2 分配
        self.assertIn("至少 1 題", user_msg)
        self.assertIn("最多 3 題", user_msg)
        self.assertIn("2/2/2", user_msg)

    async def test_single_concept_remediation_skips_quota_block(self):
        """補強 stage 通常 key_concepts 只 1 個 → 不注入 quota（避免 noise）。"""
        agent = _make_agent(_qs_json(["資料搬遷"] * 6))
        stage = {
            "stage_id": 9,
            "title": "補強：資料搬遷",
            "key_concepts": ["資料搬遷"],
            "content": "...",
            "source_chunks": [{"chunk_id": "chunk_0001", "quote": "..."}],
        }
        await agent.run(_ctx(stage))

        call_args = agent.llm.chat.await_args
        messages = call_args.args[0]
        user_msg = next(m.content for m in messages if m.role.value == "user")

        self.assertNotIn("本階段關鍵概念與配額", user_msg)


class TestDistributionViolationWarning(unittest.IsolatedAsyncioTestCase):
    async def test_distribution_violation_logs_warning(self):
        """QG 回傳 4/6 都壓同概念，超過 ceil(6/3)+1=3 → log warning。"""
        # 4 題擴容成本（超量）、1 局部重分配、1 Ownership Map
        bad_distribution = _qs_json([
            "擴容成本", "擴容成本", "擴容成本", "擴容成本",
            "局部重分配", "Ownership Map",
        ])
        agent = _make_agent(bad_distribution)
        stage = {
            "stage_id": 3,
            "title": "節點變動的影響範圍",
            "key_concepts": ["局部重分配", "Ownership Map", "擴容成本"],
            "content": "...",
            "source_chunks": [{"chunk_id": "chunk_0001", "quote": "..."}],
        }
        with self.assertLogs("wl.agents", level="WARNING") as cm:
            await agent.run(_ctx(stage))
        joined = "\n".join(cm.output)
        self.assertIn("QG distribution violation", joined)
        self.assertIn("擴容成本", joined)

    async def test_distribution_within_quota_no_warning(self):
        """2/2/2 分配 → 不 log warning。"""
        good_distribution = _qs_json([
            "局部重分配", "局部重分配",
            "Ownership Map", "Ownership Map",
            "擴容成本", "擴容成本",
        ])
        agent = _make_agent(good_distribution)
        stage = {
            "stage_id": 3,
            "title": "節點變動的影響範圍",
            "key_concepts": ["局部重分配", "Ownership Map", "擴容成本"],
            "content": "...",
            "source_chunks": [{"chunk_id": "chunk_0001", "quote": "..."}],
        }
        # 截獲 wl.agents logger 不要產生 WARNING
        logger = logging.getLogger("wl.agents")
        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record):
                if record.levelno >= logging.WARNING:
                    records.append(record)

        h = _Capture()
        logger.addHandler(h)
        try:
            await agent.run(_ctx(stage))
        finally:
            logger.removeHandler(h)

        violations = [r for r in records if "distribution violation" in r.getMessage()]
        self.assertEqual(violations, [], f"不該有 distribution warning，實際 = {[r.getMessage() for r in violations]}")


if __name__ == "__main__":
    unittest.main()
