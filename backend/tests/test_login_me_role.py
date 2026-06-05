import asyncio
import os

import pytest
from fastapi.testclient import TestClient

from backend.db.database import close_db, get_db, init_db
from backend.main import app
from .pg_fixtures import pg_exec


@pytest.fixture
def client():
    async def _setup():
        await init_db(os.environ["DATABASE_URL"], reset=True)
        db = await get_db()
        await db.execute(
            "INSERT INTO email_whitelist (email, role) VALUES ($1, $2)"
            " ON CONFLICT (email) DO NOTHING",
            "boss@example.com", "admin",
        )
        await close_db()

    asyncio.run(_setup())
    with TestClient(app) as c:
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
    # 用獨立連線刪資料，避免跨 app pool 的 event loop 不符
    pg_exec("DELETE FROM email_whitelist WHERE email = $1", "boss@example.com")
    resp = client.post(
        "/auth/login", json={"email": "boss@example.com", "password": "pw123456"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "user"
