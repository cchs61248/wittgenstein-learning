"""upload_gc 與 abandon_generating_stub 清理行為測試（asyncpg 版）。"""
import json
import os
import unittest

from backend.db.database import close_db, init_db, get_db
from backend.files.upload_gc import collect_referenced_file_ids, gc_unreferenced_uploads
from backend.files.upload_store import save_upload


class TestUploadGC(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db(os.environ["DATABASE_URL"], reset=True)

    async def asyncTearDown(self):
        await close_db()

    # upload_dir monkeypatching is done per-test since IsolatedAsyncioTestCase
    # doesn't support pytest monkeypatch directly; we patch via the module attribute.

    async def _patch_upload_dir(self, tmp_path):
        import backend.files.upload_store as us
        import backend.files.upload_gc as ugc
        upload_path = tmp_path / "uploads"
        upload_path.mkdir()
        self._orig_us = us.UPLOAD_DIR
        self._orig_ugc = ugc.UPLOAD_DIR
        us.UPLOAD_DIR = upload_path
        ugc.UPLOAD_DIR = upload_path
        return upload_path

    async def _unpatch_upload_dir(self):
        import backend.files.upload_store as us
        import backend.files.upload_gc as ugc
        us.UPLOAD_DIR = self._orig_us
        ugc.UPLOAD_DIR = self._orig_ugc

    async def _insert_user_and_session(self, session_id: str, file_ids: list[str]) -> None:
        db = await get_db()
        await db.execute(
            "INSERT INTO users (user_id, email, password_hash) VALUES ($1, $2, $3) "
            "ON CONFLICT (user_id) DO NOTHING",
            "test-user-gc", "gc@test.local", "hash",
        )
        await db.execute(
            """INSERT INTO sessions
               (session_id, user_id, content_hash, total_stages, status, title,
                source_file_ids_json)
               VALUES ($1, $2, $3, 0, 'generating', '生成中…', $4)
               ON CONFLICT (session_id) DO UPDATE SET source_file_ids_json = EXCLUDED.source_file_ids_json""",
            session_id, "test-user-gc", "hash-gc", json.dumps(file_ids),
        )

    async def test_gc_deletes_unreferenced_only(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as td:
            upload_dir = await self._patch_upload_dir(pathlib.Path(td))
            try:
                keep_id = save_upload("keep.txt", "text/plain", b"keep")
                orphan_id = save_upload("orphan.txt", "text/plain", b"orphan")

                await self._insert_user_and_session("s1-gc", [keep_id])

                result = await gc_unreferenced_uploads(max_age_hours=0)
                self.assertEqual(result["deleted_count"], 1)
                self.assertIn(orphan_id, result["deleted_ids"])
                self.assertNotIn(keep_id, result["deleted_ids"])
                self.assertTrue((upload_dir / f"{keep_id}.bin").exists())
                self.assertFalse((upload_dir / f"{orphan_id}.bin").exists())
            finally:
                await self._unpatch_upload_dir()

    async def test_gc_respects_max_age_for_recent_orphans(self):
        import tempfile, pathlib
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as td:
            upload_dir = await self._patch_upload_dir(pathlib.Path(td))
            try:
                orphan_id = save_upload("recent.txt", "text/plain", b"x")
                meta_path = upload_dir / f"{orphan_id}.meta.json"
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                meta["uploaded_at"] = datetime.now(timezone.utc).isoformat()
                meta_path.write_text(json.dumps(meta), encoding="utf-8")

                result = await gc_unreferenced_uploads(max_age_hours=24)
                self.assertEqual(result["deleted_count"], 0)
                self.assertTrue((upload_dir / f"{orphan_id}.bin").exists())
            finally:
                await self._unpatch_upload_dir()

    async def test_collect_referenced_file_ids(self):
        await self._insert_user_and_session("s1-ref", ["upl_a", "upl_b"])
        await self._insert_user_and_session("s2-ref", ["upl_b", "upl_c"])

        refs = await collect_referenced_file_ids()
        self.assertIn("upl_a", refs)
        self.assertIn("upl_b", refs)
        self.assertIn("upl_c", refs)


if __name__ == "__main__":
    unittest.main()
