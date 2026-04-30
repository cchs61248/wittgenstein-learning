import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

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
        "provider": session.get("provider_name"),
        "model": session.get("model_name"),
        "question_mode": session.get("question_mode") or "short_answer",
        "stages": [
            {
                "stage_id": s["stage_id"],
                "node_id": s.get("node_id", ""),
                "title": s["title"],
                "source_chunks": s.get("source_chunks", []),
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


# ── 書櫃相關端點 ──────────────────────────────────────────────────────────────

@router.get("/list")
async def list_sessions(token: str = Query(...)):
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 無效")
    sessions = await session_memory.get_user_sessions(payload["sub"])
    return {"sessions": sessions}


@router.get("/{session_id}")
async def get_session_detail(session_id: str, token: str = Query(...)):
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 無效")
    session = await session_memory.get_session(session_id)
    if not session or session["user_id"] != payload["sub"]:
        raise HTTPException(status_code=404, detail="Session 不存在")
    stages: list[dict] = json.loads(session["stages_json"] or "[]")
    if not stages:
        return {"session": None}
    status: str = session["status"]
    result: dict = {
        "session_id": session["session_id"],
        "status": status,
        "current_stage_id": session["current_stage_id"],
        "total_stages": session["total_stages"],
        "provider": session.get("provider_name"),
        "model": session.get("model_name"),
        "question_mode": session.get("question_mode") or "short_answer",
        "stages": [
            {
                "stage_id": s["stage_id"],
                "node_id": s.get("node_id", ""),
                "title": s["title"],
                "source_chunks": s.get("source_chunks", []),
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


class TitleUpdate(BaseModel):
    title: str


@router.patch("/{session_id}/title")
async def update_title(session_id: str, body: TitleUpdate, token: str = Query(...)):
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 無效")
    if not body.title.strip():
        raise HTTPException(status_code=422, detail="標題不可為空")
    await session_memory.update_session_title(session_id, payload["sub"], body.title)
    return {"ok": True}


@router.delete("/{session_id}")
async def delete_session_endpoint(session_id: str, token: str = Query(...)):
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 無效")
    ok = await session_memory.delete_session(session_id, payload["sub"])
    if not ok:
        raise HTTPException(status_code=404, detail="Session 不存在")
    return {"ok": True}
