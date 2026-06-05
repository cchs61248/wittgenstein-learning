"""POST /upload 對 .epub 的多章節切分行為（Task B1）。

EPUB 走章節切分路徑：回 {epub_chapters, total_chapters, parent_filename}；
每個章節各自落地為一個 file_id，filename 為 NNN_<title>.txt。
"""
import asyncio
import re
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 在 import backend.main / TestClient 前先指好 DB 路徑與 upload 目錄，
# 避免 lifespan startup 用到專案 data/ 真實 DB。
_TMP_ROOT = tempfile.mkdtemp(prefix="wl_upload_epub_test_")
_DB_PATH = str(Path(_TMP_ROOT) / "test.db")
import os as _os
_os.environ["DB_PATH"] = _DB_PATH

from backend.auth.utils import create_token  # noqa: E402
from backend.db.database import get_db  # noqa: E402
from backend.main import app  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "sample_toc.epub"


@pytest.fixture
def client_and_token(monkeypatch, tmp_path):
    # upload_store 與 upload_gc 都要指向暫存目錄，避免污染專案 data/uploads
    upload_path = tmp_path / "uploads"
    upload_path.mkdir()
    monkeypatch.setattr("backend.files.upload_store.UPLOAD_DIR", upload_path)
    monkeypatch.setattr("backend.files.upload_gc.UPLOAD_DIR", upload_path)

    with TestClient(app) as client:
        # 在 lifespan startup 完成 init_db 後插入測試 user
        async def _seed():
            db = await get_db()
            await db.execute(
                "INSERT OR REPLACE INTO users (user_id, email, password_hash, session_version) "
                "VALUES (?, ?, ?, ?)",
                ("u_epub_test", "epub@test", "hash", 1),
            )
            await db.execute(
                "INSERT OR REPLACE INTO email_whitelist (email, role) VALUES (?, ?)",
                ("epub@test", "admin"),
            )
            await db.commit()

        asyncio.get_event_loop().run_until_complete(_seed())
        token = create_token("u_epub_test", "epub@test", session_version=1)
        yield client, token


def test_upload_epub_returns_multiple_chapters(client_and_token):
    client, token = client_and_token
    with FIXTURE.open("rb") as f:
        resp = client.post(
            "/upload",
            files={"file": ("sample.epub", f, "application/epub+zip")},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_chapters"] == 3
    assert len(body["epub_chapters"]) == 3
    assert body["parent_filename"] == "sample.epub"
    seen_ids: set[str] = set()
    for chap in body["epub_chapters"]:
        assert "file_id" in chap
        assert chap["file_id"] not in seen_ids, "chapter file_ids must be unique"
        seen_ids.add(chap["file_id"])
        assert re.match(r"^\d{3}_.+\.txt$", chap["filename"]), chap["filename"]
        assert chap["char_count"] > 0
        assert chap["size"] > 0
        assert chap["mime_type"] == "text/plain; charset=utf-8"


def test_upload_malformed_epub_returns_422(client_and_token):
    client, token = client_and_token
    resp = client.post(
        "/upload",
        files={"file": ("bad.epub", b"not an epub at all", "application/epub+zip")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422
    assert "EPUB" in resp.json()["detail"]
