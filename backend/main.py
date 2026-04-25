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
from .routers.session import router as session_router
from .auth.utils import decode_token
from .llm.provider_factory import create_provider
from .llm.file_adapter import create_provider_file_ref
from .orchestrator.learning_orchestrator import LearningOrchestrator
from .memory.working_memory import get_working_memory
from .files.upload_store import load_upload


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
app.include_router(session_router)


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
        try:
            await ws_manager.send(session_id, msg)
        except (WebSocketDisconnect, RuntimeError):
            pass

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type: str = msg.get("type", "")
            p: dict = msg.get("payload", {})

            if msg_type == "start_session":
                try:
                    provider_name: str = p.get("provider", DEFAULT_PROVIDER)
                    model: str | None = p.get("model") or None
                    uploaded_file_id: str | None = p.get("uploaded_file_id") or None
                    raw_content: str = p.get("content", "")
                    if not uploaded_file_id and not raw_content.strip():
                        await emit({"type": "error", "payload": {"message": "請先上傳檔案或提供文字內容"}})
                        continue

                    provider_file_ref: dict | None = None
                    if uploaded_file_id:
                        try:
                            uploaded = load_upload(uploaded_file_id)
                        except FileNotFoundError:
                            await emit({"type": "error", "payload": {"message": "找不到已上傳檔案，請重新上傳"}})
                            continue
                        pref = await create_provider_file_ref(
                            provider=provider_name,
                            filename=uploaded["filename"],
                            mime_type=uploaded["mime_type"],
                            raw=uploaded["raw"],
                        )
                        provider_file_ref = {
                            "filename": pref.filename,
                            "mime_type": pref.mime_type,
                            "openai_file_id": pref.openai_file_id,
                            "gemini_file_uri": pref.gemini_file_uri,
                            "claude_file_id": pref.claude_file_id,
                            "monica_file_data": pref.monica_file_data,
                        }

                    llm = create_provider(provider_name, model=model)
                    orchestrator = LearningOrchestrator(llm)
                    # 儲存 orchestrator 以便後續 handle_answer 使用
                    _orchestrators[session_id] = orchestrator
                    await orchestrator.start_session(
                        session_id=session_id,
                        user_id=user_id,
                        raw_content=raw_content,
                        provider_file_ref=provider_file_ref,
                        target_depth=p.get("target_depth", "intermediate"),
                        emit=emit,
                    )
                except Exception as e:
                    await emit({"type": "error", "payload": {"message": f"啟動會話失敗：{e}"}})

            elif msg_type == "confirm_map":
                orch = _orchestrators.get(session_id)
                if orch:
                    try:
                        await orch.confirm_session(
                            session_id=session_id,
                            user_id=user_id,
                            emit=emit,
                        )
                    except Exception as e:
                        await emit({"type": "error", "payload": {"message": f"確認知識地圖失敗：{e}"}})

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

            elif msg_type == "resume_session":
                try:
                    provider_name: str = p.get("provider", DEFAULT_PROVIDER)
                    model: str | None = p.get("model") or None
                    session_id_to_resume: str = p.get("session_id", session_id)
                    llm = create_provider(provider_name, model=model)
                    orchestrator = LearningOrchestrator(llm)
                    _orchestrators[session_id_to_resume] = orchestrator
                    await orchestrator.resume_session(
                        session_id=session_id_to_resume,
                        user_id=user_id,
                        emit=emit,
                    )
                except Exception as e:
                    await emit({"type": "error", "payload": {"message": f"恢復會話失敗：{e}"}})

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


frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    # 必須最後掛載，避免 StaticFiles 攔截 /ws/* 等非 HTTP 路由
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
