import asyncio
import unittest
from unittest.mock import AsyncMock

from backend import main as main_module
from backend.ws import generation_handle as gh


def _register_running(key: str) -> asyncio.Task:
    """Register a never-finishing task at `key` so _gen_get returns a handle whose event is unset."""
    async def _block():
        await asyncio.sleep(3600)

    task = asyncio.create_task(_block())
    gh.register(key, task)
    return task


def _register_done(key: str) -> asyncio.Task:
    """Register a task at `key` and immediately set its event (without cancelling — handle stays in registry)."""
    async def _block():
        await asyncio.sleep(3600)

    task = asyncio.create_task(_block())
    gh.register(key, task)
    handle = gh.get_active(key)
    if handle:
        handle.event.set()
    return task


async def _cleanup():
    """Cancel any leftover tasks and clear registry."""
    for h in list(gh._registry.values()):
        if not h.task.done():
            h.task.cancel()
            try:
                await h.task
            except (asyncio.CancelledError, BaseException):
                pass
    gh._registry.clear()


class TestWaitOrLookupCacheHelper(unittest.IsolatedAsyncioTestCase):
    """Verify the _wait_or_lookup_cache helper handles the four states correctly."""

    async def asyncSetUp(self):
        await _cleanup()

    async def asyncTearDown(self):
        await _cleanup()

    async def test_no_prev_cache_miss_returns_false(self):
        """無舊任務 + cache miss：return False。"""
        cache = AsyncMock(return_value=None)
        emit = AsyncMock()
        hit = await main_module._wait_or_lookup_cache(
            "k1", timeout_s=1.0, cache_lookup=cache, emit_cached=emit,
        )
        self.assertFalse(hit)
        cache.assert_awaited_once()
        emit.assert_not_called()

    async def test_no_prev_cache_hit_emits_and_returns_true(self):
        """無舊任務 + cache 命中（歷史命中）：emit + return True。"""
        cache = AsyncMock(return_value={"result": "history"})
        emit = AsyncMock()
        hit = await main_module._wait_or_lookup_cache(
            "k1b", timeout_s=1.0, cache_lookup=cache, emit_cached=emit,
        )
        self.assertTrue(hit)
        cache.assert_awaited_once()
        emit.assert_awaited_once_with({"result": "history"})

    async def test_prev_completes_cache_hit_emits_and_returns_true(self):
        """舊任務完成 → cache 命中 → emit + return True。"""
        _register_done("k2")

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
        _register_done("k3")

        cache = AsyncMock(return_value=None)
        emit = AsyncMock()
        hit = await main_module._wait_or_lookup_cache(
            "k3", timeout_s=1.0, cache_lookup=cache, emit_cached=emit,
        )
        self.assertFalse(hit)
        emit.assert_not_called()

    async def test_timeout_then_cache_hit(self):
        """舊任務未完成 → wait 超時 → 查 cache 命中。"""
        _register_running("k4")  # 不 set()，模擬 task 還在跑

        cache = AsyncMock(return_value={"recovered": True})
        emit = AsyncMock()
        hit = await main_module._wait_or_lookup_cache(
            "k4", timeout_s=0.05, cache_lookup=cache, emit_cached=emit,
        )
        self.assertTrue(hit)
        emit.assert_awaited_once()

    async def test_no_cache_lookup_returns_false_after_wait(self):
        """無 cache_lookup → wait 完直接 return False。"""
        _register_done("k5")

        hit = await main_module._wait_or_lookup_cache(
            "k5", timeout_s=1.0, cache_lookup=None, emit_cached=None,
        )
        self.assertFalse(hit)


if __name__ == "__main__":
    unittest.main()
