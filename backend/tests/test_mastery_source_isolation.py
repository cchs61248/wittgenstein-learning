"""concept_mastery source_signature 跨教材隔離 — DB-level regression。

行為驗證：
- update_concept_mastery 寫入時帶 source_signature → DB 記錄該值
- get_user_mastery_map(source_signature="book_A") 只回 book_A 寫入的概念
- get_user_mastery_map(source_signature=None) 退回 legacy 不過濾行為
- session_memory.get_source_signature 優先 content_hash，fallback file_ids
"""
import json
import os
import tempfile
import unittest

from backend.db.database import init_db, close_db, get_db
from backend.memory import longterm_memory, session_memory


class TestMasterySourceIsolation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test.db")
        await init_db(self._db_path)
        # 建立 user record（concept_mastery 有 FK constraint）
        db = await get_db()
        await db.execute(
            "INSERT INTO users (user_id, email, password_hash) VALUES (?, ?, ?)",
            ("u1", "u1@test", "x"),
        )
        await db.commit()

    async def asyncTearDown(self):
        await close_db()
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)

    async def _write(self, concept: str, score: float, sig: str | None):
        await longterm_memory.update_concept_mastery(
            user_id="u1",
            concept_name=concept,
            new_score=score,
            source_signature=sig,
        )

    async def test_signature_filter_excludes_other_books(self):
        """book_A 寫入的概念，不應出現在 book_B signature 的 query 結果。"""
        await self._write("A_concept_1", 0.95, "book_A.pdf")
        await self._write("A_concept_2", 0.90, "book_A.pdf")
        await self._write("B_concept_1", 0.99, "book_B.pdf")

        only_a = await longterm_memory.get_user_mastery_map(
            "u1", threshold=0.8, source_signature="book_A.pdf"
        )
        self.assertEqual(set(only_a.keys()), {"A_concept_1", "A_concept_2"})

        only_b = await longterm_memory.get_user_mastery_map(
            "u1", threshold=0.8, source_signature="book_B.pdf"
        )
        self.assertEqual(set(only_b.keys()), {"B_concept_1"})

    async def test_none_signature_returns_all_legacy_behavior(self):
        """source_signature=None 應退回不過濾行為（含未標記出處的舊資料）。"""
        await self._write("A_concept", 0.95, "book_A.pdf")
        await self._write("B_concept", 0.95, "book_B.pdf")
        await self._write("legacy_concept", 0.95, None)

        all_high = await longterm_memory.get_user_mastery_map(
            "u1", threshold=0.8, source_signature=None
        )
        self.assertEqual(
            set(all_high.keys()),
            {"A_concept", "B_concept", "legacy_concept"},
        )

    async def test_threshold_still_respected_with_signature(self):
        """signature 過濾不影響 threshold：低 mastery 概念即使同 signature 也不回。"""
        await self._write("low", 0.5, "book_A.pdf")
        await self._write("high", 0.9, "book_A.pdf")
        result = await longterm_memory.get_user_mastery_map(
            "u1", threshold=0.8, source_signature="book_A.pdf"
        )
        self.assertEqual(set(result.keys()), {"high"})

    async def test_signature_preserved_on_update_when_caller_omits(self):
        """老 caller 未傳 signature → UPDATE 不能把已標記的 signature 改成 NULL。"""
        await self._write("c1", 0.5, "book_A.pdf")
        # 不傳 source_signature 模擬未升級的 caller
        await longterm_memory.update_concept_mastery(
            user_id="u1", concept_name="c1", new_score=0.9, source_signature=None
        )
        # 用 book_A 撈仍應撈到（signature 沒被清掉）
        result = await longterm_memory.get_user_mastery_map(
            "u1", threshold=0.0, source_signature="book_A.pdf"
        )
        self.assertIn("c1", result)


class TestGetSourceSignature(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test.db")
        await init_db(self._db_path)
        db = await get_db()
        await db.execute(
            "INSERT INTO users (user_id, email, password_hash) VALUES (?, ?, ?)",
            ("u1", "u1@test", "x"),
        )
        await db.commit()

    async def asyncTearDown(self):
        await close_db()
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)

    async def _make_session(self, sess_id: str, file_ids: list[str] | None):
        db = await get_db()
        await db.execute(
            """INSERT INTO sessions (session_id, user_id, content_hash, total_stages,
                source_file_ids_json) VALUES (?, ?, ?, ?, ?)""",
            (sess_id, "u1", "h", 0, json.dumps(file_ids) if file_ids is not None else "[]"),
        )
        await db.commit()

    async def _make_session_with_hash(
        self, sess_id: str, content_hash: str, file_ids: list[str] | None
    ):
        db = await get_db()
        await db.execute(
            """INSERT INTO sessions (session_id, user_id, content_hash, total_stages,
                source_file_ids_json) VALUES (?, ?, ?, ?, ?)""",
            (
                sess_id, "u1", content_hash, 0,
                json.dumps(file_ids) if file_ids is not None else "[]",
            ),
        )
        await db.commit()

    async def test_content_hash_preferred_over_file_ids(self):
        await self._make_session_with_hash("s1", "abc123hash", ["fid_book_A"])
        sig = await session_memory.get_source_signature("s1")
        self.assertEqual(sig, "abc123hash")

    async def test_multi_file_signature_sorted_joined_when_no_hash(self):
        """無 content_hash 時 fallback：sorted(file_ids) join '|'。"""
        db = await get_db()
        await db.execute(
            """INSERT INTO sessions (session_id, user_id, content_hash, total_stages,
                source_file_ids_json) VALUES (?, ?, ?, ?, ?)""",
            ("s2", "u1", "", 0, json.dumps(["fid_B", "fid_A"])),
        )
        await db.commit()
        sig = await session_memory.get_source_signature("s2")
        self.assertEqual(sig, "fid_A|fid_B")

    async def test_empty_file_ids_returns_none(self):
        await self._make_session_with_hash("s3", "", [])
        sig = await session_memory.get_source_signature("s3")
        self.assertIsNone(sig)

    async def test_missing_session_returns_none(self):
        sig = await session_memory.get_source_signature("nonexistent")
        self.assertIsNone(sig)


class TestPromptRules(unittest.TestCase):
    """新加的 prompt rule（並列方案完整性 + 命名格式禁止中英括弧）落地驗證。"""

    def test_teacher_prompt_includes_enumeration_completeness(self):
        from backend.utils.prompt_templates import SYSTEM_PROMPTS
        prompt = SYSTEM_PROMPTS["teacher"]
        # 重構後措辭：【並列方案與決策框架】段
        self.assertIn("並列方案", prompt)
        self.assertIn("教材列了 N 種", prompt)
        # 規則語意：宣告 N 種後必須依序展開每一種
        self.assertIn("依序展開每一種", prompt)

    def test_qg_prompt_forbids_chinese_english_bracket_mix(self):
        from backend.utils.prompt_templates import SYSTEM_PROMPTS
        prompt = SYSTEM_PROMPTS["question_generator"]
        # 重構後措辭：禁止自創「中文 (English縮寫)」格式
        self.assertIn("中文 (English縮寫)", prompt)
        # 規則語意：清單裡若已有該格式才可照原樣使用，不主動補英文縮寫
        self.assertIn("照原樣使用", prompt)


if __name__ == "__main__":
    unittest.main()
