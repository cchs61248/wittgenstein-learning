import asyncio
import unittest

from backend.orchestrator.debounced_writer import DebouncedExplanationWriter


class TestDebouncedWriter(unittest.IsolatedAsyncioTestCase):
    async def test_no_writes_below_threshold(self):
        calls = []
        async def fake_store(sid, st, txt): calls.append(txt)

        w = DebouncedExplanationWriter(
            store_fn=fake_store, session_id="s", stage_id=1,
            min_interval_s=10.0, min_delta_chars=10000,
        )
        await w.update("hello")
        self.assertEqual(calls, [])  # 還沒到任一閘門

    async def test_writes_when_size_threshold_crossed(self):
        calls = []
        async def fake_store(sid, st, txt): calls.append(txt)

        w = DebouncedExplanationWriter(
            store_fn=fake_store, session_id="s", stage_id=1,
            min_interval_s=10.0, min_delta_chars=5,
        )
        await w.update("12345")  # 恰好 5 chars
        self.assertEqual(calls, ["12345"])

    async def test_writes_when_time_threshold_crossed(self):
        calls = []
        async def fake_store(sid, st, txt): calls.append(txt)

        w = DebouncedExplanationWriter(
            store_fn=fake_store, session_id="s", stage_id=1,
            min_interval_s=0.05, min_delta_chars=10000,
        )
        await w.update("ab")
        await asyncio.sleep(0.06)
        await w.update("abcd")
        self.assertEqual(calls, ["abcd"])  # 時間到了才寫一次

    async def test_flush_always_writes_latest(self):
        calls = []
        async def fake_store(sid, st, txt): calls.append(txt)

        w = DebouncedExplanationWriter(
            store_fn=fake_store, session_id="s", stage_id=1,
            min_interval_s=10.0, min_delta_chars=10000,
        )
        await w.update("partial")
        await w.flush()
        self.assertEqual(calls, ["partial"])

    async def test_flush_noop_if_already_written(self):
        calls = []
        async def fake_store(sid, st, txt): calls.append(txt)

        w = DebouncedExplanationWriter(
            store_fn=fake_store, session_id="s", stage_id=1,
            min_interval_s=10.0, min_delta_chars=1,
        )
        await w.update("a")  # 大小門檻觸發
        await w.flush()       # 不該再寫一次
        self.assertEqual(calls, ["a"])


if __name__ == "__main__":
    unittest.main()
