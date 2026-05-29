"""purge_source_uploads 與 abandon 整合測試。"""
import asyncio
import json

import pytest

from backend.db.database import close_db, init_db
from backend.files.upload_store import save_upload_binary
from backend.memory import session_memory


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.files.upload_store.UPLOAD_DIR", tmp_path)
    return tmp_path


def test_purge_source_uploads_clears_disk_and_db_refs(upload_dir, tmp_path):
    async def _run():
        db_path = tmp_path / "test.db"
        await init_db(str(db_path))

        fid, _ = save_upload_binary(
            "book.epub", "application/epub+zip", b"raw", "hello", max_chars=500_000
        )
        db = await session_memory.get_db()
        await db.execute(
            "INSERT INTO users (user_id, email, password_hash) VALUES (?, ?, ?)",
            ("u1", "u1@test", "hash"),
        )
        await db.execute(
            """INSERT INTO sessions
               (session_id, user_id, content_hash, total_stages, status, title,
                source_file_ids_json)
               VALUES (?, ?, ?, 0, 'generating', '生成中…', ?)""",
            ("sess1", "u1", "hash", json.dumps([fid])),
        )
        await db.commit()

        await session_memory.purge_source_uploads("sess1", [fid])

        assert not (upload_dir / f"{fid}.bin").exists()
        assert not (upload_dir / f"{fid}.text").exists()
        async with db.execute(
            "SELECT source_file_ids_json FROM sessions WHERE session_id = ?",
            ("sess1",),
        ) as cur:
            row = await cur.fetchone()
        assert json.loads(row[0]) == []
        await close_db()

    asyncio.get_event_loop().run_until_complete(_run())


def test_abandon_deletes_source_chunks(upload_dir, tmp_path):
    async def _run():
        db_path = tmp_path / "test.db"
        await init_db(str(db_path))

        fid, _ = save_upload_binary(
            "book.epub", "application/epub+zip", b"raw", "hello", max_chars=500_000
        )
        db = await session_memory.get_db()
        await db.execute(
            "INSERT INTO users (user_id, email, password_hash) VALUES (?, ?, ?)",
            ("u1", "u1@test", "hash"),
        )
        await db.execute(
            """INSERT INTO sessions
               (session_id, user_id, content_hash, total_stages, status, title,
                source_file_ids_json)
               VALUES (?, ?, ?, 0, 'generating', '生成中…', ?)""",
            ("sess2", "u1", "hash", json.dumps([fid])),
        )
        await db.commit()
        await session_memory.insert_source_chunks(
            "sess2",
            [{"chunk_id": "chunk_0000", "order_index": 0, "text": "hello"}],
        )

        await session_memory.abandon_generating_stub("sess2")

        async with db.execute(
            "SELECT COUNT(*) FROM source_chunks WHERE session_id = ?",
            ("sess2",),
        ) as cur:
            row = await cur.fetchone()
        assert row[0] == 0
        await close_db()

    asyncio.get_event_loop().run_until_complete(_run())
