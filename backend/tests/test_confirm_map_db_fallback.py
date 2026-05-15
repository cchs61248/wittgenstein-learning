import asyncio
import unittest
from unittest.mock import AsyncMock

from backend import main as main_module


class TestConfirmMapDbFallback(unittest.IsolatedAsyncioTestCase):
    """Verify the cache_lookup pattern for confirm_map works when wait times out."""

    async def asyncSetUp(self):
        main_module._active_generations.clear()

    async def asyncTearDown(self):
        main_module._active_generations.clear()

    async def test_no_prev_returns_false_so_handler_runs(self):
        """No prev generation → helper returns False → caller runs the original handler."""
        cache = AsyncMock(return_value={"row": {"status": "active"}})
        emit = AsyncMock()
        hit = await main_module._wait_or_lookup_cache(
            "sess_X", timeout_s=0.05, cache_lookup=cache, emit_cached=emit,
        )
        self.assertFalse(hit)
        cache.assert_not_called()  # No prev event, cache never queried

    async def test_prev_timeout_then_cache_hit_skips_handler(self):
        """Prev still running → timeout → cache says already done → emit and return True."""
        evt = asyncio.Event()
        main_module._active_generations["sess_Y"] = evt  # Don't set — simulates running

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
        evt = asyncio.Event()
        main_module._active_generations["sess_Z"] = evt
        evt.set()

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
