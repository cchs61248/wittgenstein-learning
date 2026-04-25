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

    statuses = await session_memory.get_stage_statuses(session["session_id"])

    return {
        "session": {
            "session_id": session["session_id"],
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
            "stage_statuses": {str(k): v for k, v in statuses.items()},
        }
    }
