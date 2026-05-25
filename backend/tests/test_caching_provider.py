"""CachingLLMProvider behavior tests."""
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from backend.db.database import init_db, close_db
from backend.llm.base_provider import LLMMessage, LLMResponse, MessageRole
from backend.llm.cache_context import llm_cache_context
from backend.llm.caching_provider import CachingLLMProvider


class _InnerProvider:
    model = "m"
    temperature = 0.7
    max_tokens = 4096
    context_window = 8192

    def __init__(self):
        self._do_chat = AsyncMock(return_value=LLMResponse(
            content="hello", input_tokens=1, output_tokens=2,
            model="m", finish_reason="stop",
        ))

    async def chat(self, messages, system_prompt=None):
        return await self._do_chat(messages, system_prompt)

    async def stream_chat(self, messages, system_prompt=None):
        yield "x"


class TestCachingProvider(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.mkdtemp()
        await init_db(os.path.join(self._tmpdir, "test.db"))

    async def asyncTearDown(self):
        await close_db()

    async def test_cache_hit_skips_underlying(self):
        inner = _InnerProvider()
        wrapped = CachingLLMProvider(inner, scope="curriculum", enabled=True)
        msgs = [LLMMessage(role=MessageRole.USER, content="hi")]
        with llm_cache_context(agent_name="ContentOutlineAgent"):
            with patch("backend.llm.caching_provider.LLM_CACHE_ENABLED", True):
                await wrapped.chat(msgs, system_prompt="sys")
                await wrapped.chat(msgs, system_prompt="sys")
        self.assertEqual(inner._do_chat.await_count, 1)

    async def test_no_agent_bypasses_cache(self):
        inner = _InnerProvider()
        wrapped = CachingLLMProvider(inner, scope="curriculum", enabled=True)
        msgs = [LLMMessage(role=MessageRole.USER, content="hi")]
        with patch("backend.llm.caching_provider.LLM_CACHE_ENABLED", True):
            await wrapped.chat(msgs, system_prompt="sys")
            await wrapped.chat(msgs, system_prompt="sys")
        self.assertEqual(inner._do_chat.await_count, 2)
