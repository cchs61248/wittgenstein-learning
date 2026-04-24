from typing import Any, Literal, Optional
from pydantic import BaseModel


class WSMessage(BaseModel):
    type: str
    payload: dict[str, Any] = {}


class StartSessionPayload(BaseModel):
    user_id: str
    content: str
    provider: Literal["claude", "openai", "gemini"] = "claude"
    target_depth: Literal["beginner", "intermediate", "advanced"] = "intermediate"


class SubmitAnswerPayload(BaseModel):
    session_id: str
    question_id: str
    answer: str


class ResumeSessionPayload(BaseModel):
    session_id: str
