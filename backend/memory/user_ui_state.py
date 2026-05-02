"""使用者跨裝置 UI 狀態（版面 prefs、書櫃順序）。"""

import json
from datetime import datetime
from typing import Any

from ..db.database import get_db

DEFAULT_UI_STATE: dict[str, Any] = {"v": 1, "layoutBySession": {}, "bookshelfOrder": []}


async def get_ui_state(user_id: str) -> dict[str, Any]:
    db = await get_db()
    async with db.execute(
        "SELECT ui_state_json FROM user_learning_profile WHERE user_id = ?",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row or row[0] is None or row[0] == "":
        return dict(DEFAULT_UI_STATE)
    try:
        data = json.loads(row[0])
        if not isinstance(data, dict):
            return dict(DEFAULT_UI_STATE)
        out = dict(DEFAULT_UI_STATE)
        out["layoutBySession"] = data.get("layoutBySession") if isinstance(data.get("layoutBySession"), dict) else {}
        bo = data.get("bookshelfOrder")
        out["bookshelfOrder"] = [str(x) for x in bo] if isinstance(bo, list) else []
        return out
    except Exception:
        return dict(DEFAULT_UI_STATE)


async def put_ui_state(user_id: str, layout_by_session: dict[str, Any], bookshelf_order: list[str]) -> None:
    if not isinstance(layout_by_session, dict):
        layout_by_session = {}
    if not isinstance(bookshelf_order, list):
        bookshelf_order = []
    order_clean = [str(x) for x in bookshelf_order]
    state = {"v": 1, "layoutBySession": layout_by_session, "bookshelfOrder": order_clean}
    payload = json.dumps(state, ensure_ascii=False)
    db = await get_db()
    now = datetime.utcnow()
    await db.execute(
        """INSERT INTO user_learning_profile (user_id, ui_state_json, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
             ui_state_json = excluded.ui_state_json,
             updated_at = excluded.updated_at""",
        (user_id, payload, now),
    )
    await db.commit()
