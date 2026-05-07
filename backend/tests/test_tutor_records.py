import tempfile
import unittest
from pathlib import Path

from backend.db.database import close_db, init_db
from backend.memory.session_memory import get_all_tutor_records, insert_tutor_record


class TestTutorRecords(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        db_path = str(Path(self._tmp_dir.name) / "test.db")
        await init_db(db_path)

    async def asyncTearDown(self) -> None:
        await close_db()
        self._tmp_dir.cleanup()

    async def test_insert_and_get_single_stage(self):
        await insert_tutor_record("sess1", 2, "什麼是命題？", "命題是有真假值的陳述。", True)
        result = await get_all_tutor_records("sess1")
        self.assertEqual(list(result.keys()), [2])
        self.assertEqual(len(result[2]), 1)
        self.assertEqual(result[2][0]["question"], "什麼是命題？")
        self.assertEqual(result[2][0]["answer"], "命題是有真假值的陳述。")
        self.assertTrue(result[2][0]["in_scope"])

    async def test_get_empty_session_returns_empty_dict(self):
        result = await get_all_tutor_records("no_such_session")
        self.assertEqual(result, {})

    async def test_multiple_stages_grouped_correctly(self):
        await insert_tutor_record("sess2", 1, "問題A", "回答A", True)
        await insert_tutor_record("sess2", 3, "問題B", "回答B", False)
        await insert_tutor_record("sess2", 1, "問題C", "回答C", True)
        result = await get_all_tutor_records("sess2")
        self.assertIn(1, result)
        self.assertIn(3, result)
        self.assertEqual(len(result[1]), 2)
        self.assertEqual(len(result[3]), 1)
        self.assertEqual(result[1][0]["question"], "問題A")
        self.assertEqual(result[1][1]["question"], "問題C")
        self.assertFalse(result[3][0]["in_scope"])

    async def test_sessions_isolated(self):
        await insert_tutor_record("sessA", 1, "Q", "A", True)
        result = await get_all_tutor_records("sessB")
        self.assertEqual(result, {})

    async def test_in_scope_false_persisted(self):
        await insert_tutor_record("sess3", 1, "超出教材問題", "外部知識回答", False)
        result = await get_all_tutor_records("sess3")
        self.assertFalse(result[1][0]["in_scope"])


if __name__ == "__main__":
    unittest.main()
