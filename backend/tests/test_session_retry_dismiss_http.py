"""retry / dismiss endpoint 的 HTTP 層測試（TestClient + testcontainers）。

只覆蓋路由層決策（auth / ownership / 409），不觸發真實生成：
retry 對「無 source_chunks 的 failed session」會在 regenerate 前段就 raise
RegenerateError("no_source_chunks") → 409，故不會進到 enqueue/LLM。
"""
import asyncio
import os

import pytest
from fastapi.testclient import TestClient

from backend.auth.utils import create_token
from backend.db.database import close_db, get_db, init_db
from backend.main import app


@pytest.fixture
def client():
    async def _setup():
        await init_db(os.environ["DATABASE_URL"], reset=True)
        db = await get_db()
        for uid, email in [("u_owner", "owner@x"), ("u_other", "other@x")]:
            await db.execute(
                "INSERT INTO users (user_id, email, password_hash, session_version) "
                "VALUES ($1,$2,$3,$4) ON CONFLICT (user_id) DO NOTHING",
                uid, email, "h", 1,
            )
        await db.execute(
            "INSERT INTO sessions (session_id, user_id, content_hash, status) "
            "VALUES ($1,$2,$3,$4)",
            "sess_failed", "u_owner", "h", "failed",
        )
        await db.execute(
            "INSERT INTO sessions (session_id, user_id, content_hash, status) "
            "VALUES ($1,$2,$3,$4)",
            "sess_gen", "u_owner", "h", "generating",
        )
        await close_db()

    asyncio.run(_setup())
    with TestClient(app) as c:
        yield c


def _tok(uid, email):
    return create_token(uid, email, session_version=1)


def test_dismiss_non_failed_returns_409(client):
    resp = client.post(f"/sessions/sess_gen/dismiss?token={_tok('u_owner', 'owner@x')}")
    assert resp.status_code == 409, resp.text


def test_dismiss_wrong_owner_returns_404(client):
    resp = client.post(f"/sessions/sess_failed/dismiss?token={_tok('u_other', 'other@x')}")
    assert resp.status_code == 404, resp.text


def test_dismiss_failed_returns_200_abandoned(client):
    resp = client.post(f"/sessions/sess_failed/dismiss?token={_tok('u_owner', 'owner@x')}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "abandoned"


def test_retry_no_chunks_returns_409(client):
    # failed session 但無 source_chunks → RegenerateError("no_source_chunks") → 409
    resp = client.post(f"/sessions/sess_failed/retry?token={_tok('u_owner', 'owner@x')}")
    assert resp.status_code == 409, resp.text


def test_retry_wrong_owner_returns_404(client):
    resp = client.post(f"/sessions/sess_failed/retry?token={_tok('u_other', 'other@x')}")
    assert resp.status_code == 404, resp.text
