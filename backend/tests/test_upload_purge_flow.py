"""purge_source_uploads 與 abandon 整合測試（asyncpg 版）。"""
import json
import os
import unittest

from backend.db.database import close_db, init_db, get_db
from backend.files.upload_store import save_upload_binary
from backend.memory import session_memory


class TestUploadPurgeFlow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db(os.environ["DATABASE_URL"], reset=True)

    async def asyncTearDown(self):
        await close_db()

    async def _patch_upload_dir(self, tmp_path):
        import backend.files.upload_store as us
        self._orig_us = us.UPLOAD_DIR
        us.UPLOAD_DIR = tmp_path
        return tmp_path

    async def _unpatch_upload_dir(self):
        import backend.files.upload_store as us
        us.UPLOAD_DIR = self._orig_us

    async def test_purge_source_uploads_clears_disk_and_db_refs(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            upload_dir = await self._patch_upload_dir(pathlib.Path(td))
            try:
                fid, _ = save_upload_binary(
                    "book.epub", "application/epub+zip", b"raw", "hello", max_chars=500_000
                )
                db = await get_db()
                await db.execute(
                    "INSERT INTO users (user_id, email, password_hash) VALUES ($1, $2, $3)",
                    "u1", "u1@test", "hash",
                )
                await db.execute(
                    """INSERT INTO sessions
                       (session_id, user_id, content_hash, total_stages, status, title,
                        source_file_ids_json)
                       VALUES ($1, $2, $3, 0, 'generating', '生成中…', $4)""",
                    "sess1", "u1", "hash", json.dumps([fid]),
                )

                await session_memory.purge_source_uploads("sess1", [fid])

                assert not (upload_dir / f"{fid}.bin").exists()
                assert not (upload_dir / f"{fid}.text").exists()
                row = await db.fetchrow(
                    "SELECT source_file_ids_json FROM sessions WHERE session_id = $1",
                    "sess1",
                )
                assert json.loads(row["source_file_ids_json"]) == []
            finally:
                await self._unpatch_upload_dir()

    async def test_abandon_deletes_source_chunks(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            upload_dir = await self._patch_upload_dir(pathlib.Path(td))
            try:
                fid, _ = save_upload_binary(
                    "book.epub", "application/epub+zip", b"raw", "hello", max_chars=500_000
                )
                db = await get_db()
                await db.execute(
                    "INSERT INTO users (user_id, email, password_hash) VALUES ($1, $2, $3)",
                    "u1", "u1@test", "hash",
                )
                await db.execute(
                    """INSERT INTO sessions
                       (session_id, user_id, content_hash, total_stages, status, title,
                        source_file_ids_json)
                       VALUES ($1, $2, $3, 0, 'generating', '生成中…', $4)""",
                    "sess2", "u1", "hash", json.dumps([fid]),
                )
                await session_memory.insert_source_chunks(
                    "sess2",
                    [{"chunk_id": "chunk_0000", "order_index": 0, "text": "hello"}],
                )

                await session_memory.abandon_generating_stub("sess2")

                count = await db.fetchval(
                    "SELECT COUNT(*) FROM source_chunks WHERE session_id = $1",
                    "sess2",
                )
                assert count == 0
            finally:
                await self._unpatch_upload_dir()


if __name__ == "__main__":
    unittest.main()
