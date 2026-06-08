import json

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..auth.utils import decode_token_active
from ..memory import session_memory
from ..orchestrator.learning_orchestrator import _markdown_for_client_from_persisted, build_progress_table

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("/active")
async def get_active_session(token: str = Query(...)):
    payload = await decode_token_active(token)
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
                "kind": s.get("kind"),
                "source_stage_id": s.get("source_stage_id"),
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
    payload = await decode_token_active(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 無效")
    sessions = await session_memory.get_user_sessions(payload["sub"])
    return {"sessions": sessions}


@router.get("/{session_id}/stages/{stage_id}/explanation")
async def get_persisted_stage_explanation(session_id: str, stage_id: int, token: str = Query(...)):
    """回傳資料庫中該章已持久化的講解 Markdown（與 session_snapshot 相同轉換邏輯），供前端回顧時不必重整。"""
    payload = await decode_token_active(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 無效")
    session = await session_memory.get_session(session_id)
    if not session or session["user_id"] != payload["sub"]:
        raise HTTPException(status_code=404, detail="Session 不存在")
    stages: list[dict] = json.loads(session["stages_json"] or "[]")
    if not stages:
        raise HTTPException(status_code=404, detail="此 Session 尚無章節資料")
    idx = next((i for i, s in enumerate(stages) if int(s["stage_id"]) == int(stage_id)), -1)
    if idx < 0:
        raise HTTPException(status_code=404, detail="章節不存在於此 Session")
    raw = await session_memory.get_stage_explanation(session_id, stage_id)
    if not (raw or "").strip():
        return {"stage_id": stage_id, "explanation": ""}
    progress_md = build_progress_table(stages, idx)
    display = _markdown_for_client_from_persisted(raw, progress_md)
    return {"stage_id": stage_id, "explanation": display}


@router.get("/{session_id}/stages/{stage_id}/qa_history")
async def get_persisted_stage_qa_history(session_id: str, stage_id: int, token: str = Query(...)):
    """回傳該章已持久化之答題紀錄（與 session_snapshot 之 stage_qa_histories 單章格式一致）。"""
    payload = await decode_token_active(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 無效")
    session = await session_memory.get_session(session_id)
    if not session or session["user_id"] != payload["sub"]:
        raise HTTPException(status_code=404, detail="Session 不存在")
    stages: list[dict] = json.loads(session["stages_json"] or "[]")
    if not any(int(s["stage_id"]) == int(stage_id) for s in stages):
        raise HTTPException(status_code=404, detail="章節不存在於此 Session")
    rows = await session_memory.get_stage_qa_records(session_id, stage_id)
    records = [
        {
            "question_id": r["question_id"],
            "question_text": r["question_text"],
            "question_type": r.get("question_type") or "understand",
            "user_answer": r.get("user_answer") or "",
            "score": float(r["score"]) if r.get("score") is not None else 0.0,
            "feedback_text": r.get("feedback") or "",
        }
        for r in rows
    ]
    return {"stage_id": stage_id, "records": records}


@router.get("/{session_id}")
async def get_session_detail(session_id: str, token: str = Query(...)):
    payload = await decode_token_active(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 無效")
    session = await session_memory.get_session(session_id)
    if not session or session["user_id"] != payload["sub"]:
        raise HTTPException(status_code=404, detail="Session 不存在")
    stages: list[dict] = json.loads(session["stages_json"] or "[]")
    status: str = session["status"]
    if not stages:
        if status == "generating":
            return {"session": {
                "session_id": session["session_id"],
                "status": "generating",
                "stages": [],
                "current_stage_id": None,
                "total_stages": 0,
                "provider": session.get("provider_name"),
                "model": session.get("model_name"),
                "question_mode": session.get("question_mode") or "short_answer",
            }}
        return {"session": None}
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
                "kind": s.get("kind"),
                "source_stage_id": s.get("source_stage_id"),
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
    payload = await decode_token_active(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 無效")
    if not body.title.strip():
        raise HTTPException(status_code=422, detail="標題不可為空")
    await session_memory.update_session_title(session_id, payload["sub"], body.title)
    return {"ok": True}


@router.delete("/{session_id}")
async def delete_session_endpoint(session_id: str, token: str = Query(...)):
    payload = await decode_token_active(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 無效")
    ok = await session_memory.delete_session(session_id, payload["sub"])
    if not ok:
        raise HTTPException(status_code=404, detail="Session 不存在")
    return {"ok": True}


@router.post("/{session_id}/retry")
async def retry_session_endpoint(session_id: str, token: str = Query(...)):
    payload = await decode_token_active(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 無效")
    row = await session_memory.get_session(session_id)
    if not row or row.get("user_id") != payload["sub"]:
        raise HTTPException(status_code=404, detail="找不到 session")
    from ..config import CURRICULUM_USE_ARQ
    from ..jobs.regenerate import regenerate_failed_session, RegenerateError
    try:
        return await regenerate_failed_session(session_id, use_arq=CURRICULUM_USE_ARQ)
    except RegenerateError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/{session_id}/dismiss")
async def dismiss_session_endpoint(session_id: str, token: str = Query(...)):
    payload = await decode_token_active(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 無效")
    row = await session_memory.get_session(session_id)
    if not row or row.get("user_id") != payload["sub"]:
        raise HTTPException(status_code=404, detail="找不到 session")
    if row.get("status") != "failed":
        raise HTTPException(status_code=409, detail=f"not_failed:{row.get('status')}")
    await session_memory.abandon_failed_session(session_id)
    return {"status": "abandoned", "session_id": session_id}


@router.delete("/{session_id}/tutor/{record_id}")
async def delete_tutor_record_endpoint(session_id: str, record_id: int, token: str = Query(...)):
    payload = await decode_token_active(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 無效")
    ok = await session_memory.delete_tutor_record(record_id, session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="紀錄不存在")
    return {"ok": True}
