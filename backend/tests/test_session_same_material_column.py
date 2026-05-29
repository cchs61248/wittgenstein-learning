"""sessions.same_material 欄位與 create_generating_stub 連通測試。"""
import os
import tempfile
import unittest

from backend.db.database import init_db, close_db, get_db
from backend.memory import session_memory


async def _ensure_user(user_id: str = "u1") -> None:
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO users (user_id, email, password_hash) VALUES (?, ?, ?)",
        (user_id, f"{user_id}@test.local", "hash"),
    )
    await db.commit()


class TestSessionSameMaterialColumn(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "test.db")
        await init_db(self._db_path)
        await _ensure_user()

    async def asyncTearDown(self):
        await close_db()
        if os.path.exists(self._db_path):
            os.unlink(self._db_path)

    async def test_create_session_with_same_material_true(self):
        await session_memory.create_generating_stub(
            "sess_true", "u1", "hash_t",
            same_material=True,
        )
        row = await session_memory.get_session("sess_true")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.get("same_material"), 1)

    async def test_create_session_with_same_material_false(self):
        await session_memory.create_generating_stub(
            "sess_false", "u1", "hash_f",
            same_material=False,
        )
        row = await session_memory.get_session("sess_false")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.get("same_material"), 0)

    async def test_create_session_same_material_defaults_to_null(self):
        # 沒傳 same_material → DB 欄位應為 NULL（legacy 行為）
        await session_memory.create_generating_stub(
            "sess_none", "u1", "hash_n",
        )
        row = await session_memory.get_session("sess_none")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertIsNone(row.get("same_material"))

    async def test_alter_table_is_idempotent(self):
        # 第一次 init 已在 asyncSetUp 完成；再 init 一次同一個 DB 不應拋錯。
        # 模擬 worker / server 重啟後重跑 init_db 的情境。
        await close_db()
        await init_db(self._db_path)
        await _ensure_user()
        # 而且重 init 後仍能成功 INSERT + 帶 same_material
        await session_memory.create_generating_stub(
            "sess_after_reinit", "u1", "hash_r",
            same_material=True,
        )
        row = await session_memory.get_session("sess_after_reinit")
        assert row is not None
        self.assertEqual(row.get("same_material"), 1)

    async def test_update_path_writes_same_material_on_retry(self):
        # 第一次未提供（NULL），第二次再呼叫帶 True → UPDATE 分支應補寫。
        await session_memory.create_generating_stub(
            "sess_retry", "u1", "hash_x",
        )
        row1 = await session_memory.get_session("sess_retry")
        assert row1 is not None
        self.assertIsNone(row1.get("same_material"))

        await session_memory.create_generating_stub(
            "sess_retry", "u1", "hash_x",
            same_material=False,
        )
        row2 = await session_memory.get_session("sess_retry")
        assert row2 is not None
        self.assertEqual(row2.get("same_material"), 0)
