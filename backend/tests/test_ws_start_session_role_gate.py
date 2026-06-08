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
        for uid, email, role in [
            ("u_user", "user@x", "user"),
            ("u_admin", "admin@x", "admin"),
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


def test_user_start_session_gets_forbidden_event(client):
    token = create_token("u_user", "user@x", session_version=1)
    with client.websocket_connect(f"/ws/sess_test1?token={token}") as ws:
        ws.send_json({
            "type": "start_session",
            "payload": {"file_ids": ["nonexistent"]},
        })
        msg = ws.receive_json()
        assert msg["type"] == "forbidden", msg
        assert "管理員" in msg["payload"]["message"]


def test_admin_start_session_not_forbidden(client):
    """admin 送 start_session 不應被 forbidden 擋下；因未提供檔案，會走到一般 error
    （而非 forbidden），藉此證明閘門只擋非 admin。"""
    token = create_token("u_admin", "admin@x", session_version=1)
    with client.websocket_connect(f"/ws/sess_admin1?token={token}") as ws:
        ws.send_json({
            "type": "start_session",
            "payload": {"file_ids": []},
        })
        msg = ws.receive_json()
        assert msg["type"] != "forbidden", msg
