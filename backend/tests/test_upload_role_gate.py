import asyncio
import os

import pytest
from fastapi.testclient import TestClient

from backend.auth.utils import create_token
from backend.db.database import close_db, get_db, init_db
from backend.main import app


@pytest.fixture
def client(monkeypatch, tmp_path):
    upload_path = tmp_path / "uploads"
    upload_path.mkdir()
    monkeypatch.setattr("backend.files.upload_store.UPLOAD_DIR", upload_path)
    monkeypatch.setattr("backend.files.upload_gc.UPLOAD_DIR", upload_path)

    # reset schema + seed，再 close_db；TestClient lifespan 會在自己的 event loop 建新 pool。
    async def _setup():
        await init_db(os.environ["DATABASE_URL"], reset=True)
        db = await get_db()
        for uid, email, role in [
            ("u_admin", "admin@x", "admin"),
            ("u_user", "user@x", "user"),
        ]:
            await db.execute(
                "INSERT INTO users (user_id, email, password_hash, session_version) "
                "VALUES ($1,$2,$3,$4) ON CONFLICT (user_id) DO NOTHING",
                uid, email, "h", 1,
            )
            await db.execute(
                "INSERT INTO email_whitelist (email, role) VALUES ($1,$2) "
                "ON CONFLICT (email) DO NOTHING",
                email, role,
            )
        await close_db()

    asyncio.run(_setup())
    with TestClient(app) as c:
        yield c


def _token(uid, email):
    return create_token(uid, email, session_version=1)


def test_upload_file_forbidden_for_user(client):
    resp = client.post(
        "/upload",
        files={"file": ("a.txt", b"hello world", "text/plain")},
        headers={"Authorization": f"Bearer {_token('u_user', 'user@x')}"},
    )
    assert resp.status_code == 403, resp.text


def test_upload_file_allowed_for_admin(client):
    resp = client.post(
        "/upload",
        files={"file": ("a.txt", b"hello world content", "text/plain")},
        headers={"Authorization": f"Bearer {_token('u_admin', 'admin@x')}"},
    )
    assert resp.status_code == 200, resp.text


def test_upload_url_forbidden_for_user(client):
    resp = client.post(
        "/upload/url",
        json={"url": "https://example.com"},
        headers={"Authorization": f"Bearer {_token('u_user', 'user@x')}"},
    )
    assert resp.status_code == 403, resp.text


def test_youtube_asr_forbidden_for_user(client):
    resp = client.post(
        "/upload/youtube/asr/stream",
        json={"url": "https://youtube.com/watch?v=x"},
        headers={"Authorization": f"Bearer {_token('u_user', 'user@x')}"},
    )
    assert resp.status_code == 403, resp.text
