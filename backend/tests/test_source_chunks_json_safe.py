"""Regression: get_source_chunks() 回傳的 chunk dict 必須 JSON-serializable。

背景（docs/CURRICULUM_RELIABILITY_STATUS.md §9 P1）：source_chunks 從 DB 重載時
（resume/checkpoint build），`created_at`（timestamptz）會以 datetime 物件留在 chunk
dict 內。build-phase agent（SplitterVerifier / ContentOutline）把整個 chunk dict 丟進
`json.dumps` 組 LLM payload，datetime 不可序列化 → verifier fail-open（live ×2：
sess_omttc9dky / sess_w8is4cac5）。修法在 loader 邊界把 created_at 轉成 ISO 字串。

L1：loader 投影 helper（無 DB）。
L2：真實 DB round-trip 後 chunk 可 json.dumps。
L3：loader 產出餵給 SplitterVerifier / ContentOutline 不再 TypeError。
"""
import json
import os
import unittest
from datetime import datetime, timezone

from backend.db.database import init_db, close_db, get_db
from backend.memory import session_memory
from backend.memory.session_memory import _json_safe_source_chunk
from backend.agents.base_agent import AgentContext
from backend.agents.splitter_verifier import SplitterVerifierAgent
from backend.agents.content_outline import ContentOutlineAgent


def _fake_llm(response_dict: dict):
    response_json = json.dumps(response_dict, ensure_ascii=False)

    class _Resp:
        def __init__(self, content):
            self.content = content

    class _LLM:
        async def chat(self, messages, system_prompt=None):
            return _Resp(response_json)

    return _LLM()


def _make_agent(cls, llm):
    agent = cls.__new__(cls)
    agent.llm = llm
    agent._messages = []
    agent.token_counter = None
    return agent


# ── L1：loader 投影 helper（無 DB）──────────────────────────────

class TestJsonSafeSourceChunk(unittest.TestCase):
    def test_datetime_created_at_becomes_iso_string(self):
        dt = datetime(2026, 6, 10, 7, 24, 48, 791735, tzinfo=timezone.utc)
        out = _json_safe_source_chunk({"chunk_id": "c0", "text": "x", "created_at": dt})
        self.assertEqual(out["created_at"], dt.isoformat())
        self.assertIsInstance(out["created_at"], str)
        # 整個 chunk 必須可序列化
        json.dumps(out, ensure_ascii=False)

    def test_non_datetime_created_at_passthrough(self):
        # 已是字串 / None 不應被改動
        s = _json_safe_source_chunk({"chunk_id": "c0", "created_at": "2026-06-10T00:00:00"})
        self.assertEqual(s["created_at"], "2026-06-10T00:00:00")
        n = _json_safe_source_chunk({"chunk_id": "c1", "created_at": None})
        self.assertIsNone(n["created_at"])

    def test_missing_created_at_ok(self):
        out = _json_safe_source_chunk({"chunk_id": "c0", "text": "x"})
        self.assertNotIn("created_at", out)
        json.dumps(out, ensure_ascii=False)


# ── L2 / L3：真實 DB round-trip + agent 端對端 ──────────────────

class TestSourceChunksJsonSafeWithDB(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db(os.environ["DATABASE_URL"], reset=True)
        db = await get_db()
        await db.execute(
            "INSERT INTO users (user_id, email, password_hash) VALUES ($1, $2, $3) "
            "ON CONFLICT (user_id) DO NOTHING",
            "u1", "u1@test.local", "hash",
        )
        await db.execute(
            "INSERT INTO sessions (session_id, user_id, content_hash) VALUES ($1, $2, $3)",
            "sess_json_safe", "u1", "hash123",
        )
        await session_memory.insert_source_chunks(
            "sess_json_safe",
            [
                {"chunk_id": "chunk_0000", "order_index": 0, "text": "RAG intro",
                 "section_title": "RAG", "source_index": 0, "source_label": "doc.pdf"},
                {"chunk_id": "chunk_0001", "order_index": 1, "text": "knowledge cutoff",
                 "section_title": "Cutoff", "source_index": 0, "source_label": "doc.pdf"},
            ],
        )

    async def asyncTearDown(self):
        await close_db()

    async def test_db_loaded_chunks_are_json_serializable(self):
        chunks = await session_memory.get_source_chunks("sess_json_safe")
        self.assertEqual(len(chunks), 2)
        # DB 寫入時 created_at 走 default now()（timestamptz）→ loader 必須轉成 str
        for c in chunks:
            self.assertIsInstance(c.get("created_at"), str)
        # 整批可序列化（不拋 TypeError）
        json.dumps(chunks, ensure_ascii=False)

    async def test_splitter_verifier_no_typeerror_on_db_chunks(self):
        chunks = await session_memory.get_source_chunks("sess_json_safe")
        agent = _make_agent(SplitterVerifierAgent, _fake_llm({
            "aligned": True, "missing_options": [], "issue_chunk_ids": [], "reason": "ok",
        }))
        ctx = AgentContext(
            session_id="sess_json_safe", user_id="u1",
            task_payload={"source_chunks": chunks, "stages": [
                {"stage_id": 1, "title": "RAG", "key_concepts": ["RAG"]},
            ]},
        )
        # 修法前此處會在 json.dumps(source_chunks) 拋 TypeError → fail-open
        result = await agent.run(ctx)
        self.assertTrue(result["aligned"])

    async def test_content_outline_no_typeerror_on_db_chunks(self):
        """latent twin：content_outline.py 同樣 json.dumps 整個 source_chunks。"""
        chunks = await session_memory.get_source_chunks("sess_json_safe")
        agent = _make_agent(ContentOutlineAgent, _fake_llm({
            "required_stage_titles": [], "named_cases": [],
            "framework_sections": [], "summary_sections": [], "must_cover_chunks": [],
        }))
        ctx = AgentContext(
            session_id="sess_json_safe", user_id="u1",
            task_payload={"source_chunks": chunks},
        )
        result = await agent.run(ctx)
        self.assertEqual(result["named_cases"], [])


if __name__ == "__main__":
    unittest.main()
