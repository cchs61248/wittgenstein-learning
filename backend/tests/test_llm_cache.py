"""LLM result cache CRUD tests."""
import os
import tempfile
import unittest

from backend.db.database import init_db, close_db
from backend.llm.base_provider import LLMResponse
from backend.memory import llm_cache
from backend.llm.cache_context import llm_cache_context, set_content_hash


class TestLlmCache(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test.db")
        await init_db(self._db_path)

    async def asyncTearDown(self):
        await close_db()
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)

    async def test_cache_roundtrip(self):
        key = "abc123"
        resp = LLMResponse(
            content="ok", input_tokens=1, output_tokens=2,
            model="m", finish_reason="stop",
        )
        await llm_cache.put(
            key, agent_name="ContentSplitterAgent", model_name="m",
            prompt_version="1", result=resp, content_hash="chash",
        )
        hit = await llm_cache.get(key)
        assert hit is not None
        self.assertEqual(hit.content, "ok")
        await llm_cache.record_hit(key)
        row = await llm_cache.get_row(key)
        assert row is not None
        self.assertEqual(row["hit_count"], 1)

    async def test_context_nested_reset(self):
        set_content_hash("h1")
        with llm_cache_context(agent_name="ContentOutlineAgent"):
            from backend.llm.cache_context import get_agent_name
            self.assertEqual(get_agent_name(), "ContentOutlineAgent")
        from backend.llm.cache_context import get_agent_name
        self.assertIsNone(get_agent_name())
