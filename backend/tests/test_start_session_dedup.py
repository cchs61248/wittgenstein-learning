import asyncio
import unittest
from unittest.mock import AsyncMock

from backend import main as main_module


class TestWaitOrLookupCacheHelper(unittest.IsolatedAsyncioTestCase):
    """Verify the _wait_or_lookup_cache helper handles the four states correctly."""

    async def asyncSetUp(self):
        main_module._active_generations.clear()

    async def asyncTearDown(self):
        main_module._active_generations.clear()

    async def test_no_prev_returns_false(self):
        """無舊任務：直接 return False，不查 cache。"""
        cache = AsyncMock(return_value={"x": 1})
        emit = AsyncMock()
        hit = await main_module._wait_or_lookup_cache(
            "k1", timeout_s=1.0, cache_lookup=cache, emit_cached=emit,
        )
        self.assertFalse(hit)
        cache.assert_not_called()
        emit.assert_not_called()

    async def test_prev_completes_cache_hit_emits_and_returns_true(self):
        """舊任務完成 → cache 命中 → emit + return True。"""
        evt = asyncio.Event()
        main_module._active_generations["k2"] = evt
        evt.set()  # 已完成

        cache = AsyncMock(return_value={"result": "cached"})
        emit = AsyncMock()
        hit = await main_module._wait_or_lookup_cache(
            "k2", timeout_s=1.0, cache_lookup=cache, emit_cached=emit,
        )
        self.assertTrue(hit)
        cache.assert_awaited_once()
        emit.assert_awaited_once_with({"result": "cached"})

    async def test_prev_completes_cache_miss_returns_false(self):
        """舊任務完成 → cache miss → return False（呼叫端跑新任務）。"""
        evt = asyncio.Event()
        main_module._active_generations["k3"] = evt
        evt.set()

        cache = AsyncMock(return_value=None)
        emit = AsyncMock()
        hit = await main_module._wait_or_lookup_cache(
            "k3", timeout_s=1.0, cache_lookup=cache, emit_cached=emit,
        )
        self.assertFalse(hit)
        emit.assert_not_called()

    async def test_timeout_then_cache_hit(self):
        """舊任務未完成 → wait 超時 → 查 cache 命中。"""
        evt = asyncio.Event()
        main_module._active_generations["k4"] = evt
        # 不 set()，模擬 task 還在跑

        cache = AsyncMock(return_value={"recovered": True})
        emit = AsyncMock()
        hit = await main_module._wait_or_lookup_cache(
            "k4", timeout_s=0.05, cache_lookup=cache, emit_cached=emit,
        )
        self.assertTrue(hit)
        emit.assert_awaited_once()

    async def test_no_cache_lookup_returns_false_after_wait(self):
        """無 cache_lookup → wait 完直接 return False。"""
        evt = asyncio.Event()
        main_module._active_generations["k5"] = evt
        evt.set()

        hit = await main_module._wait_or_lookup_cache(
            "k5", timeout_s=1.0, cache_lookup=None, emit_cached=None,
        )
        self.assertFalse(hit)


if __name__ == "__main__":
    unittest.main()
