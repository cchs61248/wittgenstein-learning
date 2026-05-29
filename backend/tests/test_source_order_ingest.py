import unittest
from unittest.mock import AsyncMock

from backend.main import _build_source_chunks_from_payload


class TestIngestReorder(unittest.IsolatedAsyncioTestCase):
    async def _build(self, sources, same_material=True):
        p = {"sources": sources, "same_material": same_material}
        return await _build_source_chunks_from_payload(p, AsyncMock())

    async def test_same_material_reorders_by_filename(self):
        out = await self._build([
            {"type": "text", "label": "第二章.txt", "content": "第二章內容。" * 40},
            {"type": "text", "label": "第一章.txt", "content": "第一章內容。" * 40},
        ])
        self.assertIsNotNone(out)
        chunks, _file_ids, decision = out
        self.assertTrue(decision["applied"])
        first_chunk = min(chunks, key=lambda c: c["order_index"])
        self.assertEqual(first_chunk["source_label"], "第一章.txt")

    async def test_cross_material_does_not_reorder(self):
        out = await self._build([
            {"type": "text", "label": "第二章.txt", "content": "第二章內容。" * 40},
            {"type": "text", "label": "第一章.txt", "content": "第一章內容。" * 40},
        ], same_material=False)
        chunks, _file_ids, decision = out
        self.assertFalse(decision["applied"])
        first_chunk = min(chunks, key=lambda c: c["order_index"])
        self.assertEqual(first_chunk["source_label"], "第二章.txt")

    async def test_single_source_no_reorder(self):
        out = await self._build([
            {"type": "text", "label": "第二章.txt", "content": "只有一份。" * 40},
        ])
        _chunks, _file_ids, decision = out
        self.assertFalse(decision["applied"])


if __name__ == "__main__":
    unittest.main()
