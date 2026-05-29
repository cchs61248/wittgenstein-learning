"""LLM global concurrency limiter tests."""
import asyncio
import unittest
from unittest.mock import MagicMock, patch

from backend.llm.base_provider import BaseLLMProvider, LLMMessage, LLMResponse, MessageRole
from backend.llm.concurrency import llm_slot, reset_concurrency_state_for_tests


class _StubProvider(BaseLLMProvider):
    calls = 0

    @property
    def context_window(self) -> int:
        return 8192

    async def _do_chat(self, messages, system_prompt=None) -> LLMResponse:
        type(self).calls += 1
        await asyncio.sleep(0.01)
        return LLMResponse(
            content="ok",
            input_tokens=1,
            output_tokens=1,
            model=self.model,
            finish_reason="stop",
        )

    async def _do_stream_chat(self, messages, system_prompt=None):
        yield "a"


class TestLocalLlmConcurrency(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reset_concurrency_state_for_tests()

    async def test_max_three_concurrent_local(self):
        with patch("backend.llm.concurrency.LLM_MAX_CONCURRENT", 3), patch(
            "backend.llm.concurrency._redis_available", return_value=False
        ):
            active = 0
            peak = 0
            lock = asyncio.Lock()

            async def worker():
                nonlocal active, peak
                async with llm_slot(purpose="test"):
                    async with lock:
                        active += 1
                        peak = max(peak, active)
                    await asyncio.sleep(0.05)
                    async with lock:
                        active -= 1

            await asyncio.gather(*[worker() for _ in range(8)])
            self.assertLessEqual(peak, 3)
            self.assertEqual(active, 0)

    async def test_wait_timeout_raises(self):
        with patch("backend.llm.concurrency.LLM_MAX_CONCURRENT", 1), patch(
            "backend.llm.concurrency.LLM_SLOT_WAIT_TIMEOUT_S", 0.1
        ), patch("backend.llm.concurrency._redis_available", return_value=False):
            async with llm_slot():
                with self.assertRaises(TimeoutError):
                    async with llm_slot():
                        pass


class TestRedisLlmConcurrency(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reset_concurrency_state_for_tests()

    async def test_redis_acquire_release_called(self):
        from backend.llm import concurrency as mod
        import unittest.mock

        mock_redis = unittest.mock.AsyncMock()
        mock_redis.ping = unittest.mock.AsyncMock(return_value=True)
        # concurrency uses register_script + script callables (not client.eval directly).
        mock_acq = unittest.mock.AsyncMock(return_value=1)
        mock_rel = unittest.mock.AsyncMock(return_value=1)
        mock_renew = unittest.mock.AsyncMock(return_value=1)

        def _register_script(lua: str):
            if "ZREMRANGEBYSCORE" in lua:
                return mock_acq
            if "ZSCORE" in lua:
                return mock_renew
            return mock_rel

        mock_redis.register_script = unittest.mock.MagicMock(side_effect=_register_script)

        with (
            patch("backend.llm.concurrency.LLM_MAX_CONCURRENT", 2),
            patch("backend.llm.concurrency.REDIS_URL", "redis://localhost:6380/0"),
            # Avoid real sync Redis ping; force distributed path for this test.
            patch("backend.llm.concurrency._redis_available", return_value=True),
            patch("redis.asyncio.Redis.from_url", return_value=mock_redis),
        ):
            mod.reset_concurrency_state_for_tests()
            async with mod.llm_slot(purpose="redis-test"):
                pass
        self.assertGreaterEqual(mock_acq.await_count, 1)
        self.assertGreaterEqual(mock_rel.await_count, 1)

    async def test_redis_lease_renewal_runs_while_holding_slot(self):
        from backend.llm import concurrency as mod
        import unittest.mock

        mock_redis = unittest.mock.AsyncMock()
        mock_redis.ping = unittest.mock.AsyncMock(return_value=True)
        mock_acq = unittest.mock.AsyncMock(return_value=1)
        mock_rel = unittest.mock.AsyncMock(return_value=1)
        mock_renew = unittest.mock.AsyncMock(return_value=1)

        def _register_script(lua: str):
            if "ZREMRANGEBYSCORE" in lua:
                return mock_acq
            if "ZSCORE" in lua:
                return mock_renew
            return mock_rel

        mock_redis.register_script = unittest.mock.MagicMock(side_effect=_register_script)

        with (
            patch("backend.llm.concurrency.LLM_MAX_CONCURRENT", 2),
            patch("backend.llm.concurrency.LLM_SLOT_RENEW_INTERVAL_S", 0.02),
            patch("backend.llm.concurrency.REDIS_URL", "redis://localhost:6380/0"),
            patch("backend.llm.concurrency._redis_available", return_value=True),
            patch("redis.asyncio.Redis.from_url", return_value=mock_redis),
        ):
            mod.reset_concurrency_state_for_tests()
            async with mod.llm_slot(purpose="renew-test"):
                await asyncio.sleep(0.06)
        self.assertGreaterEqual(mock_renew.await_count, 1)

    async def test_disabled_when_max_zero(self):
        with patch("backend.llm.concurrency.LLM_MAX_CONCURRENT", 0):
            async with llm_slot():
                pass


class TestRedisProbeCache(unittest.TestCase):
    def setUp(self):
        reset_concurrency_state_for_tests()

    def test_failure_probe_cached_then_retries_after_ttl(self):
        from backend.llm import concurrency as mod

        mod.reset_concurrency_state_for_tests()
        fake_t = [0.0]

        def mono() -> float:
            return fake_t[0]

        mock_r = MagicMock()
        mock_r.ping.side_effect = [ConnectionError("down"), True]
        mock_r.close = MagicMock()
        url_calls = {"n": 0}

        def from_url(*args, **kwargs):
            url_calls["n"] += 1
            return mock_r

        with patch("redis.Redis.from_url", side_effect=from_url), patch(
            "backend.llm.concurrency.time.monotonic", side_effect=mono
        ), patch("backend.llm.concurrency.LLM_MAX_CONCURRENT", 2):
            self.assertFalse(mod._redis_available())
            self.assertEqual(url_calls["n"], 1)

            fake_t[0] = mod._REDIS_PROBE_FAIL_TTL_S - 1.0
            self.assertFalse(mod._redis_available())
            self.assertEqual(url_calls["n"], 1)

            fake_t[0] = mod._REDIS_PROBE_FAIL_TTL_S + 0.1
            self.assertTrue(mod._redis_available())
            self.assertEqual(url_calls["n"], 2)

    def test_success_probe_rechecked_after_ok_ttl(self):
        from backend.llm import concurrency as mod

        mod.reset_concurrency_state_for_tests()
        fake_t = [0.0]

        def mono() -> float:
            return fake_t[0]

        mock_r = MagicMock()
        mock_r.ping.return_value = True
        mock_r.close = MagicMock()
        url_calls = {"n": 0}

        def from_url(*args, **kwargs):
            url_calls["n"] += 1
            return mock_r

        with patch("redis.Redis.from_url", side_effect=from_url), patch(
            "backend.llm.concurrency.time.monotonic", side_effect=mono
        ), patch("backend.llm.concurrency.LLM_MAX_CONCURRENT", 2):
            self.assertTrue(mod._redis_available())
            self.assertEqual(url_calls["n"], 1)

            fake_t[0] = mod._REDIS_PROBE_OK_TTL_S - 1.0
            self.assertTrue(mod._redis_available())
            self.assertEqual(url_calls["n"], 1)

            fake_t[0] = mod._REDIS_PROBE_OK_TTL_S + 0.1
            self.assertTrue(mod._redis_available())
            self.assertEqual(url_calls["n"], 2)


class TestProviderIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reset_concurrency_state_for_tests()
        _StubProvider.calls = 0

    async def test_chat_respects_concurrency(self):
        p = _StubProvider(model="m")
        with patch("backend.llm.concurrency.LLM_MAX_CONCURRENT", 1), patch(
            "backend.llm.concurrency._redis_available", return_value=False
        ):
            async def one():
                await p.chat([LLMMessage(role=MessageRole.USER, content="hi")])

            await asyncio.gather(*[one() for _ in range(2)])
        self.assertEqual(_StubProvider.calls, 2)
