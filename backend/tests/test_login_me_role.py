import asyncio
import tempfile
from pathlib import Path
import os as _os

_TMP_ROOT = tempfile.mkdtemp(prefix="wl_login_role_test_")
_os.environ["DB_PATH"] = str(Path(_TMP_ROOT) / "test.db")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.db.database import get_db  # noqa: E402
from backend.main import app  # noqa: E402


@pytest.fixture
def client():
    with TestClient(app) as c:
        async def _seed():
            db = await get_db()
            await db.execute(
                "INSERT OR IGNORE INTO email_whitelist (email, role) VALUES (?, ?)",
                ("boss@example.com", "admin"),
            )
            await db.commit()
        asyncio.get_event_loop().run_until_complete(_seed())
        c.post("/auth/register", json={"email": "boss@example.com", "password": "pw123456"})
        yield c


def test_login_returns_role(client):
    resp = client.post(
        "/auth/login", json={"email": "boss@example.com", "password": "pw123456"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "admin"


def test_me_returns_role(client):
    login = client.post(
        "/auth/login", json={"email": "boss@example.com", "password": "pw123456"}
    )
    token = login.json()["access_token"]
    resp = client.get(f"/auth/me?token={token}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "admin"


def test_login_role_downgrades_when_removed_from_whitelist(client):
    async def _remove():
        db = await get_db()
        await db.execute("DELETE FROM email_whitelist WHERE email = ?", ("boss@example.com",))
        await db.commit()
    asyncio.get_event_loop().run_until_complete(_remove())
    resp = client.post(
        "/auth/login", json={"email": "boss@example.com", "password": "pw123456"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "user"
