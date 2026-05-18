import asyncio
import unittest
from unittest.mock import AsyncMock

from backend import main as main_module
from backend.ws import generation_handle as gh


def _register_running(key: str) -> asyncio.Task:
    async def _block():
        await asyncio.sleep(3600)

    task = asyncio.create_task(_block())
    gh.register(key, task)
    return task


def _register_done(key: str) -> asyncio.Task:
    async def _block():
        await asyncio.sleep(3600)

    task = asyncio.create_task(_block())
    gh.register(key, task)
    handle = gh.get_active(key)
    if handle:
        handle.event.set()
    return task


async def _cleanup():
    for h in list(gh._registry.values()):
        if not h.task.done():
            h.task.cancel()
            try:
                await h.task
            except (asyncio.CancelledError, BaseException):
                pass
    gh._registry.clear()


class TestConfirmMapDbFallback(unittest.IsolatedAsyncioTestCase):
    """Verify the cache_lookup pattern for confirm_map works when wait times out."""

    async def asyncSetUp(self):
        await _cleanup()

    async def asyncTearDown(self):
        await _cleanup()

    async def test_no_prev_cache_miss_returns_false_so_handler_runs(self):
        """No prev + cache miss (session 仍是 pending_confirmation) → handler 要跑。"""
        cache = AsyncMock(return_value=None)
        emit = AsyncMock()
        hit = await main_module._wait_or_lookup_cache(
            "sess_X", timeout_s=0.05, cache_lookup=cache, emit_cached=emit,
        )
        self.assertFalse(hit)
        cache.assert_awaited_once()  # 新行為：無條件先 cache lookup
        emit.assert_not_called()

    async def test_no_prev_cache_hit_skips_handler(self):
        """No prev + cache 命中（session 已 confirmed）→ emit cached + skip handler。"""
        cache = AsyncMock(return_value={"row": {"status": "active"}})
        emit = AsyncMock()
        hit = await main_module._wait_or_lookup_cache(
            "sess_X2", timeout_s=0.05, cache_lookup=cache, emit_cached=emit,
        )
        self.assertTrue(hit)
        cache.assert_awaited_once()
        emit.assert_awaited_once_with({"row": {"status": "active"}})

    async def test_prev_timeout_then_cache_hit_skips_handler(self):
        """Prev still running → timeout → cache says already done → emit and return True."""
        _register_running("sess_Y")  # Don't set — simulates running

        cache = AsyncMock(return_value={"row": {"status": "active"}})
        emit = AsyncMock()
        hit = await main_module._wait_or_lookup_cache(
            "sess_Y", timeout_s=0.05, cache_lookup=cache, emit_cached=emit,
        )
        self.assertTrue(hit)
        cache.assert_awaited_once()
        emit.assert_awaited_once_with({"row": {"status": "active"}})

    async def test_prev_completes_cache_pending_confirmation_returns_false(self):
        """Prev completes but DB still says pending_confirmation → cache returns None → False."""
        _register_done("sess_Z")

        async def cache():
            # Mimics the actual lookup logic
            row = {"status": "pending_confirmation"}
            if row.get("status") and row["status"] != "pending_confirmation":
                return {"row": row}
            return None

        emit = AsyncMock()
        hit = await main_module._wait_or_lookup_cache(
            "sess_Z", timeout_s=1.0, cache_lookup=cache, emit_cached=emit,
        )
        self.assertFalse(hit)
        emit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
