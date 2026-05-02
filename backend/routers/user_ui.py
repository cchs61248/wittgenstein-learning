from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth.utils import decode_token_active
from ..memory import user_ui_state

router = APIRouter(prefix="/user", tags=["user"])


class UiStatePutBody(BaseModel):
    layoutBySession: dict[str, Any] = Field(default_factory=dict)
    bookshelfOrder: list[str] = Field(default_factory=list)


@router.get("/ui-state")
async def get_ui_state_endpoint(token: str = Query(...)):
    payload = await decode_token_active(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 無效")
    return await user_ui_state.get_ui_state(payload["sub"])


@router.put("/ui-state")
async def put_ui_state_endpoint(body: UiStatePutBody, token: str = Query(...)):
    payload = await decode_token_active(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 無效")
    await user_ui_state.put_ui_state(
        payload["sub"],
        body.layoutBySession,
        body.bookshelfOrder,
    )
    return {"ok": True}
