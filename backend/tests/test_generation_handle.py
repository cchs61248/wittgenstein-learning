import asyncio
import unittest

from backend.ws.generation_handle import (
    _GenerationHandle,
    register,
    cancel,
    finish,
    get_active,
)


class TestGenerationHandle(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from backend.ws import generation_handle as gh
        gh._registry.clear()

    async def test_register_and_finish(self):
        async def work():
            await asyncio.sleep(0.01)
            return "ok"

        task = asyncio.create_task(work())
        handle = register("k1", task)
        self.assertIs(get_active("k1"), handle)
        await task
        finish("k1")
        self.assertIsNone(get_active("k1"))

    async def test_cancel_propagates(self):
        async def slow():
            await asyncio.sleep(10)

        task = asyncio.create_task(slow())
        handle = register("k2", task)
        ok = await cancel("k2")
        self.assertTrue(ok)
        # 給 event loop 一拍時間讓 cancellation 真正生效
        await asyncio.sleep(0)
        self.assertTrue(task.cancelled() or task.done())
        # cancel 也應該 set event 讓 waiters 解除
        self.assertTrue(handle.event.is_set())

    async def test_cancel_nonexistent_returns_false(self):
        ok = await cancel("nope")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
