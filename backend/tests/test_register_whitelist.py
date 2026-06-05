import asyncio
import tempfile
from pathlib import Path
import os as _os

_TMP_ROOT = tempfile.mkdtemp(prefix="wl_register_test_")
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
                ("reg_allowed@example.com", "user"),
            )
            await db.execute(
                "INSERT OR IGNORE INTO email_whitelist (email, role) VALUES (?, ?)",
                ("reg_boss@example.com", "admin"),
            )
            await db.commit()
        asyncio.get_event_loop().run_until_complete(_seed())
        yield c


def test_register_blocked_when_not_whitelisted(client):
    resp = client.post(
        "/auth/register",
        json={"email": "stranger@example.com", "password": "pw123456"},
    )
    assert resp.status_code == 403, resp.text


def test_register_ok_when_whitelisted_returns_role(client):
    resp = client.post(
        "/auth/register",
        json={"email": "reg_allowed@example.com", "password": "pw123456"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["role"] == "user"
    assert body["email"] == "reg_allowed@example.com"


def test_register_admin_role_propagates(client):
    resp = client.post(
        "/auth/register",
        json={"email": "reg_boss@example.com", "password": "pw123456"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["role"] == "admin"
