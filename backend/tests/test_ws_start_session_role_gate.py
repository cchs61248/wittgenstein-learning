import asyncio
import tempfile
from pathlib import Path
import os as _os

_TMP_ROOT = tempfile.mkdtemp(prefix="wl_ws_role_test_")
_os.environ["DB_PATH"] = str(Path(_TMP_ROOT) / "test.db")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.auth.utils import create_token  # noqa: E402
from backend.db.database import get_db  # noqa: E402
from backend.main import app  # noqa: E402


@pytest.fixture
def client():
    with TestClient(app) as c:
        async def _seed():
            db = await get_db()
            await db.execute(
                "INSERT OR REPLACE INTO users (user_id, email, password_hash, session_version) "
                "VALUES (?,?,?,?)", ("u_user", "user@x", "h", 1),
            )
            await db.execute(
                "INSERT OR REPLACE INTO email_whitelist (email, role) VALUES (?,?)",
                ("user@x", "user"),
            )
            await db.execute(
                "INSERT OR REPLACE INTO users (user_id, email, password_hash, session_version) "
                "VALUES (?,?,?,?)", ("u_admin", "admin@x", "h", 1),
            )
            await db.execute(
                "INSERT OR REPLACE INTO email_whitelist (email, role) VALUES (?,?)",
                ("admin@x", "admin"),
            )
            await db.commit()
        asyncio.get_event_loop().run_until_complete(_seed())
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
