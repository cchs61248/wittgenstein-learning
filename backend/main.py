import asyncio
import json
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .config import DB_PATH, CORS_ORIGINS, CORS_ORIGIN_REGEX, DEFAULT_PROVIDER
from .db.database import init_db, close_db
from .auth.router import router as auth_router
from .routers.upload import router as upload_router
from .routers.session import router as session_router
from .routers.learner import router as learner_router
from .routers.user_ui import router as user_ui_router
from .auth.utils import decode_token_active
from .llm.provider_factory import create_provider
from .orchestrator.learning_orchestrator import LearningOrchestrator
from .memory.working_memory import get_working_memory, delete_working_memory
from .memory import session_memory
from .files.upload_store import load_upload
from .utils.text_extractor import extract_text
from .utils.chunker import build_source_chunks
from .utils.logger import setup_logging, ws_logger
from .ws.connection_manager import WebSocketManager


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
    allow_origin_regex=CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(upload_router)
app.include_router(session_router)
app.include_router(learner_router)
app.include_router(user_ui_router)


ws_manager = WebSocketManager()


async def _build_source_chunks_from_payload(
    p: dict,
    emit,
) -> list[dict] | None:
    """
    從 start_session payload 組裝 source_chunks。
    支援新格式（sources 陣列）與舊格式（uploaded_file_id / content）。
    回傳 None 表示已向客戶端送出錯誤，呼叫方應 continue。
    """
    sources_raw: list[dict] = p.get("sources") or []

    if sources_raw:
        # 新格式：多來源陣列
        source_infos: list[dict] = []
        for i, src in enumerate(sources_raw):
            src_type = src.get("type", "file")
            label = src.get("label") or f"來源 {i + 1}"
            if src_type == "text":
                text = src.get("content", "").strip()
                if not text:
                    continue
            else:
                file_id = src.get("file_id")
                if not file_id:
                    continue
                try:
                    uploaded = load_upload(file_id)
                except FileNotFoundError:
                    await emit({"type": "error", "payload": {"message": f"找不到已上傳檔案（{label}），請重新上傳"}})
                    return None
                label = label or uploaded.get("filename", label)
                text = extract_text(uploaded["filename"], uploaded["raw"])
            if text:
                source_infos.append({"label": label, "text": text, "index": i})

        if not source_infos:
            await emit({"type": "error", "payload": {"message": "資料源內容為空，請確認上傳的檔案或文字"}})
            return None
    else:
        # 舊格式：向下相容
        uploaded_file_id: str | None = p.get("uploaded_file_id") or None
        raw_content: str = p.get("content", "")
        if not uploaded_file_id and not raw_content.strip():
            await emit({"type": "error", "payload": {"message": "請先上傳檔案或提供文字內容"}})
            return None

        if uploaded_file_id:
            try:
                uploaded = load_upload(uploaded_file_id)
            except FileNotFoundError:
                await emit({"type": "error", "payload": {"message": "找不到已上傳檔案，請重新上傳"}})
                return None
            text = extract_text(uploaded["filename"], uploaded["raw"])
            label = uploaded.get("filename", "上傳的檔案")
        else:
            text = raw_content
            label = "貼上的文字"
        source_infos = [{"label": label, "text": text, "index": 0}]

    # 每個來源獨立 chunking，全域重新編號，附上來源 metadata
    all_chunks: list[dict] = []
    global_offset = 0
    for src_info in source_infos:
        chunks = build_source_chunks(src_info["text"])
        for c in chunks:
            c["chunk_id"] = f"chunk_{global_offset:04d}"
            c["order_index"] = global_offset
            c["source_label"] = src_info["label"]
            c["source_index"] = src_info["index"]
            global_offset += 1
        all_chunks.extend(chunks)

    if not all_chunks:
        await emit({"type": "error", "payload": {"message": "無法從文件中抽取內容，請確認檔案格式"}})
        return None

    return all_chunks


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(...),
    client_id: str | None = Query(default=None),
):
    payload = await decode_token_active(token)
    if not payload:
        await websocket.close(code=4001)
        return

    user_id: str = payload["sub"]
    _ws_log = ws_logger()
    _ws_log.info("WS CONNECT  session=%s  user=%s", session_id, user_id)
    effective_client_id = client_id or f"legacy-{session_id}"
    await ws_manager.connect(session_id, user_id, effective_client_id, websocket)

    async def emit(msg: dict) -> None:
        try:
            msg_type = msg.get("type", "?")
            log_fn = _ws_log.debug if msg_type == "explanation_chunk" else _ws_log.info
            log_fn("WS OUT  session=%s  type=%s", session_id, msg_type)
            _ws_log.debug(
                "WS OUT PAYLOAD  session=%s  type=%s\n%s",
                session_id, msg_type,
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

                    source_chunks = await _build_source_chunks_from_payload(p, emit)
                    if source_chunks is None:
                        continue  # emit already sent

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
                # 若此 session 有上一輪未完成的生成，先等它結束再開始
                _prev_gen = _active_generations.get(session_id)
                if _prev_gen:
                    try:
                        await asyncio.wait_for(_prev_gen.wait(), timeout=300)
                    except asyncio.TimeoutError:
                        pass
                _gen_evt: asyncio.Event | None = asyncio.Event()
                _active_generations[session_id] = _gen_evt
                try:
                    await orch.confirm_session(
                        session_id=session_id,
                        user_id=user_id,
                        emit=emit,
                    )
                except Exception as e:
                    await emit({"type": "error", "payload": {"message": f"確認知識地圖失敗：{e}"}})
                finally:
                    _gen_evt.set()
                    _active_generations.pop(session_id, None)

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
                session_id_to_resume: str = p.get("session_id", session_id)
                _resume_gen_evt: asyncio.Event | None = None
                try:
                    # 若此 session 有上一輪未完成的生成，先等它結束再 resume
                    _prev_resume_gen = _active_generations.get(session_id_to_resume)
                    if _prev_resume_gen:
                        try:
                            await asyncio.wait_for(_prev_resume_gen.wait(), timeout=300)
                        except asyncio.TimeoutError:
                            pass
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
                    _resume_gen_evt = asyncio.Event()
                    _active_generations[session_id_to_resume] = _resume_gen_evt
                    await orchestrator.resume_session(
                        session_id=session_id_to_resume,
                        user_id=user_id,
                        emit=emit,
                    )
                except Exception as e:
                    await emit({"type": "error", "payload": {"message": f"恢復會話失敗：{e}"}})
                finally:
                    if _resume_gen_evt:
                        _resume_gen_evt.set()
                        _active_generations.pop(session_id_to_resume, None)

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

    except (WebSocketDisconnect, RuntimeError):
        _ws_log.info("WS DISCONNECT  session=%s  user=%s", session_id, user_id)
        ws_manager.disconnect(session_id, websocket)
        # 只有在此 WS 確實是當前連線（disconnect 後 session 已無 WS）時才清除資源，
        # 避免把後繼裝置的 orchestrator / working memory 一起清掉。
        if not ws_manager.has_active_ws(session_id):
            _orchestrators.pop(session_id, None)
            delete_working_memory(session_id)


# 會話級 orchestrator 暫存（單 process 內有效）
_orchestrators: dict[str, LearningOrchestrator] = {}

# 追蹤正在生成的 session（session_id → Event），避免斷線重連時並發起多個 TeacherAgent
_active_generations: dict[str, asyncio.Event] = {}


@app.get("/health")
async def health():
    return {"status": "ok"}


frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    # 必須最後掛載，避免 StaticFiles 攔截 /ws/* 等非 HTTP 路由
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
