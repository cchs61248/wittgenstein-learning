import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..auth.utils import decode_token
from ..memory import session_memory

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("/active")
async def get_active_session(token: str = Query(...)):
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 無效")

    session = await session_memory.get_user_active_session(payload["sub"])
    if not session:
        return {"session": None}

    stages: list[dict] = json.loads(session["stages_json"] or "[]")
    if not stages:
        # 舊的 session 沒有 stages_json，視為無效
        return {"session": None}

    status: str = session["status"]
    result: dict = {
        "session_id": session["session_id"],
        "status": status,
        "current_stage_id": session["current_stage_id"],
        "total_stages": session["total_stages"],
        "stages": [
            {
                "stage_id": s["stage_id"],
                "node_id": s.get("node_id", ""),
                "title": s["title"],
            }
            for s in stages
        ],
    }

    if status == "pending_confirmation":
        raw_map = session.get("pending_map_json")
        result["pending_map"] = json.loads(raw_map) if raw_map else None
    else:
        statuses = await session_memory.get_stage_statuses(session["session_id"])
        result["stage_statuses"] = {str(k): v for k, v in statuses.items()}

    return {"session": result}
