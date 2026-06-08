import asyncio
import os

import pytest
from fastapi.testclient import TestClient

from backend.db.database import close_db, get_db, init_db
from backend.main import app


@pytest.fixture
def client():
    # reset schema + seed，然後 close_db 釋放 pool；TestClient lifespan 會在自己的
    # event loop 上建立全新 pool（seed 資料已落 DB，與用哪個 pool 無關）。
    async def _setup():
        await init_db(os.environ["DATABASE_URL"], reset=True)
        db = await get_db()
        await db.execute(
            "INSERT INTO email_whitelist (email, role) VALUES ($1, $2)"
            " ON CONFLICT (email) DO NOTHING",
            "reg_allowed@example.com", "user",
        )
        await db.execute(
            "INSERT INTO email_whitelist (email, role) VALUES ($1, $2)"
            " ON CONFLICT (email) DO NOTHING",
            "reg_boss@example.com", "admin",
        )
        await close_db()

    asyncio.run(_setup())
    with TestClient(app) as c:
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
