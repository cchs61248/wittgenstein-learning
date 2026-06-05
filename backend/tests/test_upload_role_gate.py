import asyncio
import tempfile
from pathlib import Path
import os as _os

_TMP_ROOT = tempfile.mkdtemp(prefix="wl_upload_role_test_")
_os.environ["DB_PATH"] = str(Path(_TMP_ROOT) / "test.db")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.auth.utils import create_token  # noqa: E402
from backend.db.database import get_db  # noqa: E402
from backend.main import app  # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path):
    upload_path = tmp_path / "uploads"
    upload_path.mkdir()
    monkeypatch.setattr("backend.files.upload_store.UPLOAD_DIR", upload_path)
    monkeypatch.setattr("backend.files.upload_gc.UPLOAD_DIR", upload_path)
    with TestClient(app) as c:
        async def _seed():
            db = await get_db()
            for uid, email, role in [
                ("u_admin", "admin@x", "admin"),
                ("u_user", "user@x", "user"),
            ]:
                await db.execute(
                    "INSERT OR REPLACE INTO users (user_id, email, password_hash, session_version) "
                    "VALUES (?,?,?,?)", (uid, email, "h", 1),
                )
                await db.execute(
                    "INSERT OR REPLACE INTO email_whitelist (email, role) VALUES (?,?)",
                    (email, role),
                )
            await db.commit()
        asyncio.get_event_loop().run_until_complete(_seed())
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
