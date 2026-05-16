import asyncio
import unittest

from backend.ws.generation_handle import register, cancel, get_active


class TestCancelGeneration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from backend.ws import generation_handle as gh
        gh._registry.clear()

    async def test_cancel_marks_task_cancelled(self):
        cancelled_seen = asyncio.Event()

        async def work():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled_seen.set()
                raise

        task = asyncio.create_task(work())
        register("k", task)
        await asyncio.sleep(0.01)  # 讓 task 進入 sleep

        ok = await cancel("k")
        self.assertTrue(ok)
        await asyncio.sleep(0.05)
        self.assertTrue(cancelled_seen.is_set())
        self.assertIsNone(get_active("k"))


if __name__ == "__main__":
    unittest.main()
