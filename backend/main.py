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
from .orchestrator.learning_orchestrator import LearningOrchestrator
from .memory.working_memory import get_working_memory, delete_working_memory
from .memory import session_memory
from .files.upload_store import load_upload
from .utils.text_extractor import extract_text
from .utils.chunker import build_source_chunks
from .utils.logger import setup_logging, ws_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    ws_logger().info("Wittgenstein Learning System starting up")
    await init_db(DB_PATH)
    yield
    await close_db()
    ws_logger().info("Wittgenstein Learning System shutting down")


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
        self._sid_to_ws: dict[str, WebSocket] = {}   # session_id → WebSocket
        self._uid_to_sid: dict[str, str] = {}          # user_id → 當前 session_id

    async def connect(self, session_id: str, user_id: str, ws: WebSocket) -> str | None:
        """接受新連線，踢掉同 user_id 的舊連線。回傳被踢掉的舊 session_id（若不同），否則 None。"""
        await ws.accept()

        old_session_id = self._uid_to_sid.get(user_id)
        evicted_session_id: str | None = None

        if old_session_id:
            old_ws = self._sid_to_ws.pop(old_session_id, None)
            if old_ws:
                try:
                    await old_ws.send_text(json.dumps(
                        {"type": "kicked", "payload": {"message": "你已在其他裝置或視窗登入，此連線已中斷。"}},
                        ensure_ascii=False,
                    ))
                    await old_ws.close(code=4002)
                except Exception:
                    pass
            # 只有 session 不同才需要外部清理舊 orchestrator
            if old_session_id != session_id:
                evicted_session_id = old_session_id

        self._sid_to_ws[session_id] = ws
        self._uid_to_sid[user_id] = session_id
        return evicted_session_id

    async def send(self, session_id: str, message: dict) -> None:
        ws = self._sid_to_ws.get(session_id)
        if ws:
            await ws.send_text(json.dumps(message, ensure_ascii=False))

    def disconnect(self, session_id: str, user_id: str, ws: WebSocket) -> None:
        # 只在 _sid_to_ws 中儲存的 WS 與傳入的 ws 是同一個物件時才移除，
        # 避免新連線進來後被舊連線的斷線事件誤刪。
        current = self._sid_to_ws.get(session_id)
        if current is ws:
            self._sid_to_ws.pop(session_id, None)
            if self._uid_to_sid.get(user_id) == session_id:
                self._uid_to_sid.pop(user_id, None)

    def has_active_ws(self, session_id: str) -> bool:
        return session_id in self._sid_to_ws


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
    _ws_log = ws_logger()
    _ws_log.info("WS CONNECT  session=%s  user=%s", session_id, user_id)
    evicted_sid = await ws_manager.connect(session_id, user_id, websocket)
    if evicted_sid:
        _ws_log.info("WS EVICT  old_session=%s  new_session=%s", evicted_sid, session_id)
        _orchestrators.pop(evicted_sid, None)
        delete_working_memory(evicted_sid)

    async def emit(msg: dict) -> None:
        try:
            _ws_log.info(
                "WS OUT  session=%s  type=%s", session_id, msg.get("type", "?")
            )
            _ws_log.debug(
                "WS OUT PAYLOAD  session=%s  type=%s\n%s",
                session_id, msg.get("type", "?"),
                json.dumps(msg, ensure_ascii=False),
            )
            await ws_manager.send(session_id, msg)
        except (WebSocketDisconnect, RuntimeError):
            pass

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type: str = msg.get("type", "")
            p: dict = msg.get("payload", {})
            _ws_log.info("WS IN  session=%s  type=%s", session_id, msg_type)
            _ws_log.debug(
                "WS IN PAYLOAD  session=%s  type=%s\n%s",
                session_id, msg_type,
                json.dumps(msg, ensure_ascii=False),
            )

            if msg_type == "start_session":
                try:
                    provider_name: str = p.get("provider", DEFAULT_PROVIDER)
                    model: str | None = p.get("model") or None
                    uploaded_file_id: str | None = p.get("uploaded_file_id") or None
                    raw_content: str = p.get("content", "")
                    if not uploaded_file_id and not raw_content.strip():
                        await emit({"type": "error", "payload": {"message": "請先上傳檔案或提供文字內容"}})
                        continue

                    # 本地文字抽取 + chunking（後端掌控 source truth）
                    if uploaded_file_id:
                        try:
                            uploaded = load_upload(uploaded_file_id)
                        except FileNotFoundError:
                            await emit({"type": "error", "payload": {"message": "找不到已上傳檔案，請重新上傳"}})
                            continue
                        doc_text = extract_text(uploaded["filename"], uploaded["raw"])
                    else:
                        doc_text = raw_content

                    source_chunks = build_source_chunks(doc_text)
                    if not source_chunks:
                        await emit({"type": "error", "payload": {"message": "無法從文件中抽取內容，請確認檔案格式"}})
                        continue

                    llm = create_provider(provider_name, model=model)
                    orchestrator = LearningOrchestrator(llm)
                    _orchestrators[session_id] = orchestrator
                    await orchestrator.start_session(
                        session_id=session_id,
                        user_id=user_id,
                        source_chunks=source_chunks,
                        target_depth=p.get("target_depth", "intermediate"),
                        question_mode=p.get("question_mode", "short_answer"),
                        provider_name=provider_name,
                        model_name=model,
                        emit=emit,
                    )
                except Exception as e:
                    await emit({"type": "error", "payload": {"message": f"啟動會話失敗：{e}"}})

            elif msg_type == "confirm_map":
                orch = _orchestrators.get(session_id)
                if not orch:
                    # 重整後 in-memory orchestrator 遺失，新建一個並從 DB 恢復
                    session_row = await session_memory.get_session(session_id)
                    provider_name: str = (
                        p.get("provider")
                        or (session_row.get("provider_name") if session_row else None)
                        or DEFAULT_PROVIDER
                    )
                    model: str | None = (
                        p.get("model")
                        or (session_row.get("model_name") if session_row else None)
                        or None
                    )
                    llm = create_provider(provider_name, model=model)
                    orch = LearningOrchestrator(llm)
                    _orchestrators[session_id] = orch
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
                    session_id_to_resume: str = p.get("session_id", session_id)
                    session_row = await session_memory.get_session(session_id_to_resume)
                    provider_name: str = (
                        p.get("provider")
                        or (session_row.get("provider_name") if session_row else None)
                        or DEFAULT_PROVIDER
                    )
                    model: str | None = (
                        p.get("model")
                        or (session_row.get("model_name") if session_row else None)
                        or None
                    )
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

            elif msg_type == "ask_tutor":
                orch = _orchestrators.get(session_id)
                if orch:
                    await orch.handle_student_question(
                        session_id=session_id,
                        question=p.get("question", "").strip(),
                        emit=emit,
                    )

    except WebSocketDisconnect:
        _ws_log.info("WS DISCONNECT  session=%s  user=%s", session_id, user_id)
        ws_manager.disconnect(session_id, user_id, websocket)
        # 只有在此 WS 確實是當前連線（disconnect 後 session 已無 WS）時才清除資源，
        # 避免把後繼裝置的 orchestrator / working memory 一起清掉。
        if not ws_manager.has_active_ws(session_id):
            _orchestrators.pop(session_id, None)
            delete_working_memory(session_id)


# 會話級 orchestrator 暫存（單 process 內有效）
_orchestrators: dict[str, LearningOrchestrator] = {}


@app.get("/health")
async def health():
    return {"status": "ok"}


frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    # 必須最後掛載，避免 StaticFiles 攔截 /ws/* 等非 HTTP 路由
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
