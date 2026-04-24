import json
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .config import DB_PATH, CORS_ORIGINS, DEFAULT_PROVIDER
from .db.database import init_db, close_db
from .auth.router import router as auth_router
from .routers.upload import router as upload_router
from .auth.utils import decode_token
from .llm.provider_factory import create_provider
from .orchestrator.learning_orchestrator import LearningOrchestrator
from .memory.working_memory import get_working_memory


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db(DB_PATH)
    yield
    await close_db()


app = FastAPI(title="Wittgenstein Learning System", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(upload_router)

frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")


class WebSocketManager:
    def __init__(self):
        self._active: dict[str, WebSocket] = {}

    async def connect(self, session_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._active[session_id] = ws

    async def send(self, session_id: str, message: dict) -> None:
        ws = self._active.get(session_id)
        if ws:
            await ws.send_text(json.dumps(message, ensure_ascii=False))

    def disconnect(self, session_id: str) -> None:
        self._active.pop(session_id, None)


ws_manager = WebSocketManager()


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(...),
):
    payload = decode_token(token)
    if not payload:
        await websocket.close(code=4001)
        return

    user_id: str = payload["sub"]
    await ws_manager.connect(session_id, websocket)

    async def emit(msg: dict) -> None:
        await ws_manager.send(session_id, msg)

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type: str = msg.get("type", "")
            p: dict = msg.get("payload", {})

            if msg_type == "start_session":
                provider_name: str = p.get("provider", DEFAULT_PROVIDER)
                model: str | None = p.get("model") or None
                llm = create_provider(provider_name, model=model)
                orchestrator = LearningOrchestrator(llm)
                # 儲存 orchestrator 以便後續 handle_answer 使用
                _orchestrators[session_id] = orchestrator
                await orchestrator.start_session(
                    session_id=session_id,
                    user_id=user_id,
                    raw_content=p["content"],
                    target_depth=p.get("target_depth", "intermediate"),
                    emit=emit,
                )

            elif msg_type == "submit_answer":
                orch = _orchestrators.get(session_id)
                if orch:
                    await orch.handle_answer(
                        session_id=session_id,
                        user_id=user_id,
                        question_id=p["question_id"],
                        answer=p["answer"],
                        emit=emit,
                    )

            elif msg_type == "request_hint":
                await emit({
                    "type": "hint",
                    "payload": {"text": "提示功能即將開放"},
                })

    except WebSocketDisconnect:
        ws_manager.disconnect(session_id)
        _orchestrators.pop(session_id, None)


# 會話級 orchestrator 暫存（單 process 內有效）
_orchestrators: dict[str, LearningOrchestrator] = {}


@app.get("/health")
async def health():
    return {"status": "ok"}
