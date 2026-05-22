"""Source provenance persistence tests."""
import unittest

from backend.main import _build_source_chunks_from_payload


class TestSourceProvenance(unittest.IsolatedAsyncioTestCase):
    async def test_chunks_get_stable_source_id(self):
        async def emit(_msg):
            pass

        p = {
            "sources": [
                {"type": "text", "label": "書A", "content": "第一章\n\n內容A" * 20},
                {"type": "text", "label": "書B", "content": "第二章\n\n內容B" * 20},
            ]
        }
        result = await _build_source_chunks_from_payload(p, emit)
        self.assertIsNotNone(result)
        chunks, _ = result
        ids = {c.get("source_id") for c in chunks}
        self.assertEqual(len(ids), 2)
        for c in chunks:
            self.assertIn("source_label", c)
            self.assertIn("source_index", c)


if __name__ == "__main__":
    unittest.main()
