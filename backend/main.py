import asyncio
import hashlib
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from .config import (
    DB_PATH,
    CORS_ORIGINS,
    CORS_ORIGIN_REGEX,
    DEFAULT_PROVIDER,
    UPLOAD_ORPHAN_MAX_AGE_HOURS,
    CURRICULUM_USE_ARQ,
    REDIS_URL,
    LLM_CACHE_EVICT_DAYS,
)
from .db.database import init_db, close_db
from .db.inflight_lock import cleanup_stale as inflight_cleanup_stale
from .db.inflight_lock import cleanup_dead_worker_locks as inflight_cleanup_dead_workers
from .db.inflight_lock import release as inflight_release
from .auth.router import router as auth_router
from .routers.upload import router as upload_router
from .routers.session import router as session_router
from .routers.learner import router as learner_router
from .routers.user_ui import router as user_ui_router
from .auth.utils import decode_token_active, get_role_by_email
from .llm.provider_factory import create_provider
from .orchestrator.learning_orchestrator import LearningOrchestrator
from .memory.working_memory import get_working_memory, delete_working_memory
from .memory import session_memory
from .files.upload_store import load_upload_meta, load_upload_text
from .files.upload_gc import gc_unreferenced_uploads
from .utils.chunker import build_source_chunks
from .utils.source_order import resolve_source_order
from .utils.logger import ingest_logger, setup_logging, ws_logger
from .ws.connection_manager import WebSocketManager
from .ws.generation_handle import (
    get_active as _gen_get,
    register_async as _gen_register_async,
    finish_async as _gen_finish_async,
    cancel_async as _gen_cancel_async,
    wait_for_session_idle as _wait_for_session_idle,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    ws_logger().info("Wittgenstein Learning System starting up")
    await init_db(DB_PATH)
    # 清掉前次 worker 強制關閉時殘留的孤兒 inflight locks（Phase 3 Task B2）
    try:
        n_dead = await inflight_cleanup_dead_workers()
        if n_dead:
            ws_logger().info(f"inflight_locks: cleaned {n_dead} dead-worker entries on startup")
        n = await inflight_cleanup_stale(max_age_s=600)
        if n:
            ws_logger().info(f"inflight_locks: cleaned {n} stale entries on startup")
    except Exception as e:
        ws_logger().warning(f"inflight_locks cleanup_stale failed on startup: {e}")
    # 清理未被 session 引用的 upload 孤兒（含上傳後未開 session、失敗 session 遺留）
    try:
        gc_result = gc_unreferenced_uploads(
            DB_PATH,
            max_age_hours=UPLOAD_ORPHAN_MAX_AGE_HOURS,
        )
        if gc_result["deleted_count"]:
            ws_logger().info(
                "upload_gc: deleted %d orphan blobs (referenced=%d, max_age_h=%s)",
                gc_result["deleted_count"],
                gc_result["referenced_count"],
                UPLOAD_ORPHAN_MAX_AGE_HOURS,
            )
    except Exception as e:
        ws_logger().warning(f"upload_gc failed on startup: {e}")
    if LLM_CACHE_EVICT_DAYS > 0:
        try:
            from .memory import llm_cache
            n = await llm_cache.evict_older_than(LLM_CACHE_EVICT_DAYS)
            if n:
                ws_logger().info("llm_cache: evicted %d entries older than %d days", n, LLM_CACHE_EVICT_DAYS)
        except Exception as e:
            ws_logger().warning(f"llm_cache eviction failed on startup: {e}")
    # 自動續跑：in-process（CURRICULUM_USE_ARQ=0）由 API 排程；Arq 模式由 worker startup 處理
    if not CURRICULUM_USE_ARQ:
        try:
            from .memory import curriculum_checkpoint as ckpt
            from .orchestrator.curriculum_resume import resume_generating_session_background

            for sid in await ckpt.list_resumable_sessions():
                ws_logger().info("curriculum: scheduling background resume  session=%s", sid)
                asyncio.create_task(
                    resume_generating_session_background(
                        sid,
                        lambda provider, model: create_provider(
                            provider or DEFAULT_PROVIDER, model=model,
                        ),
                    )
                )
        except Exception as e:
            ws_logger().warning(f"curriculum auto-resume failed on startup: {e}")
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


def _peek_user_id(request: Request) -> str | None:
    """從 Authorization: Bearer ... 解出 sub 給 log 用；失敗回 None。
    不驗 session_version（純 best-effort，給審計追蹤用）。"""
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    from .auth.utils import decode_token
    payload = decode_token(auth.split(" ", 1)[1].strip())
    return payload.get("sub") if payload else None


@app.exception_handler(HTTPException)
async def _log_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    log = ws_logger()
    user_id = _peek_user_id(request)
    log_fn = log.warning if exc.status_code >= 500 else log.info
    log_fn(
        "HTTP %s  %s %s  user=%s  detail=%r",
        exc.status_code, request.method, request.url.path,
        user_id, exc.detail,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=exc.headers,
    )


@app.exception_handler(Exception)
async def _log_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    log = ws_logger()
    user_id = _peek_user_id(request)
    log.error(
        "UNHANDLED  %s %s  user=%s",
        request.method, request.url.path, user_id,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
    )


ws_manager = WebSocketManager()
_ingest_log = ingest_logger()


async def _build_source_chunks_from_payload(
    p: dict,
    emit,
) -> tuple[list[dict], list[str], dict] | None:
    """
    從 start_session payload 組裝 source_chunks。
    支援新格式（sources 陣列）與舊格式（uploaded_file_id / content）。
    回傳 (all_chunks, file_ids, order_decision)；file_ids 供 generating stub 追蹤，chunk 入庫後即 purge。
    回傳 None 表示已向客戶端送出錯誤，呼叫方應 continue。
    """
    order_decision = {"applied": False, "certain": False, "signal": None,
                      "order": None, "reason": "not_initialized"}
    sources_raw: list[dict] = p.get("sources") or []
    file_ids: list[str] = []

    if sources_raw:
        # 新格式：多來源陣列
        source_infos: list[dict] = []
        for i, src in enumerate(sources_raw):
            src_type = src.get("type", "file")
            label = src.get("label") or f"來源 {i + 1}"
            chapter_hint: dict = {}
            if src_type == "text":
                text = src.get("content", "").strip()
                if not text:
                    continue
            else:
                file_id = src.get("file_id")
                if not file_id:
                    continue
                try:
                    meta = load_upload_meta(file_id)
                    text = load_upload_text(file_id)
                except FileNotFoundError:
                    await emit({
                        "type": "error",
                        "payload": {
                            "message": f"找不到已上傳檔案（{label}），可能已釋放，請重新上傳",
                        },
                    })
                    return None
                file_ids.append(file_id)
                label = label or meta.get("filename", label)
                # P1b: forward EPUB chapter metadata into chunks so per-source split
                # can hint Splitter with chapter context.
                if meta.get("source_type") == "epub_chapter":
                    chapter_hint = {
                        "epub_filename": meta.get("epub_filename"),
                        "chapter_index": meta.get("chapter_index"),
                        "chapter_title": meta.get("chapter_title"),
                    }
                    chapter_hint = {k: v for k, v in chapter_hint.items() if v is not None}
            if text:
                source_infos.append({
                    "label": label, "text": text, "index": i,
                    "chapter_hint": chapter_hint,
                })

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
                meta = load_upload_meta(uploaded_file_id)
                text = load_upload_text(uploaded_file_id)
            except FileNotFoundError:
                await emit({
                    "type": "error",
                    "payload": {"message": "找不到已上傳檔案，可能已釋放，請重新上傳"},
                })
                return None
            file_ids.append(uploaded_file_id)
            label = meta.get("filename", "上傳的檔案")
        else:
            text = raw_content
            label = "貼上的文字"
        source_infos = [{"label": label, "text": text, "index": 0}]

    same_material = bool(p.get("same_material", True))
    if same_material and len(source_infos) > 1:
        source_infos, order_decision = resolve_source_order(source_infos)
    else:
        order_decision = {"applied": False, "certain": False, "signal": None,
                          "order": None, "reason": "single_source_or_cross_material"}

    # 每個來源獨立 chunking，全域重新編號，附上來源 metadata
    all_chunks: list[dict] = []
    global_offset = 0
    for src_info in source_infos:
        label = src_info["label"]
        idx = src_info["index"]
        chapter_hint: dict = src_info.get("chapter_hint") or {}
        source_id = hashlib.sha256(f"{label}:{idx}".encode()).hexdigest()[:12]
        chunks = build_source_chunks(src_info["text"])
        for c in chunks:
            c["chunk_id"] = f"chunk_{global_offset:04d}"
            c["order_index"] = global_offset
            c["source_label"] = label
            c["source_index"] = idx
            c["source_id"] = source_id
            # P1b: propagate EPUB chapter metadata onto every chunk so per-source
            # split can pick it up from subset[0] later.
            for k, v in chapter_hint.items():
                c.setdefault(k, v)
            global_offset += 1
        all_chunks.extend(chunks)

    if not all_chunks:
        await emit({"type": "error", "payload": {"message": "無法從文件中抽取內容，請確認檔案格式"}})
        return None

    sample_titles = [
        (c.get("section_title") or c["chunk_id"]) for c in all_chunks[:5]
    ]
    _ingest_log.info(
        "source_chunks built  count=%d  sources=%d  sample_titles=%s",
        len(all_chunks),
        len(source_infos),
        sample_titles,
    )

    return all_chunks, file_ids, order_decision


async def _build_orchestrator_for_session(
    session_id: str,
    p: dict | None = None,
) -> LearningOrchestrator:
    """
    Phase 3 Task C1 — stateless：每個 WS 訊息進來都從 DB 重建 orchestrator，
    不再保留 _orchestrators in-memory pool。

    優先順序：
    1. payload (p) 內帶的 provider / model
    2. DB 內 session row 的 provider_name / model_name（resume_session 走這路徑）
    3. DEFAULT_PROVIDER fallback
    """
    p = p or {}
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
    return LearningOrchestrator(llm)


async def _wait_or_lookup_cache(
    key: str,
    timeout_s: float,
    cache_lookup,    # Optional[Callable[[], Awaitable[Optional[dict]]]]
    emit_cached,     # Optional[Callable[[dict], Awaitable[None]]]
) -> bool:
    """
    依序：
    1. 無條件 cache lookup（歷史命中：例如 tutor 同問題已答過、submit 同題已評過）。
       命中即 emit_cached 並回傳 True。
    2. cache miss 且 registry 有相同 key 的舊任務：wait 最多 timeout_s，
       完成後（或 timeout）再查一次 cache；命中即 emit + return True。
    3. 都沒命中：回傳 False（呼叫端應繼續跑新任務）。
    """
    async def _try_cache() -> bool:
        if cache_lookup is None:
            return False
        try:
            cached = await cache_lookup()
        except Exception as e:
            ws_logger().warning("dedup cache lookup failed for key=%s: %s", key, e)
            return False
        if cached is None:
            return False
        if emit_cached:
            await emit_cached(cached)
        return True

    # 步驟 1：先查歷史 cache（不論有無 inflight）
    if await _try_cache():
        return True

    # 步驟 2：若 inflight 任務還在跑，等對方完成後再查一次
    prev = _gen_get(key)
    if not prev:
        return False
    try:
        await asyncio.wait_for(prev.event.wait(), timeout=timeout_s)
    except asyncio.TimeoutError:
        pass
    return await _try_cache()


async def _lookup_answer_cache(session_id: str, question_id: str) -> dict | None:
    """
    submit_answer 的 cache lookup helper（module-level，方便測試）。

    只在「當前 stage」內查 cache。questioner 在補強/重教章節可能重用與原章節相同的
    question_id（例如 stage 8 與 stage 11 同名 q_cb_1），跨 stage 共享 cache 會讓
    新章節答題誤命中舊紀錄，導致 handle_answer 整個跳過、不發下一題、不發 stage_decision。
    """
    session_row = await session_memory.get_session(session_id)
    if not session_row:
        return None
    current_stage_id = session_row.get("current_stage_id")
    if current_stage_id is None:
        return None
    all_qa = await session_memory.get_all_stage_qa_records(session_id)
    records = all_qa.get(int(current_stage_id), [])
    return next((x for x in records if x["question_id"] == question_id), None)


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
                if await get_role_by_email(payload.get("email", "")) != "admin":
                    _ws_log.info(
                        "start_session denied non_admin  session=%s  user=%s",
                        session_id, user_id,
                    )
                    await emit({
                        "type": "forbidden",
                        "payload": {"message": "僅管理員可新增教材"},
                    })
                    continue
                _start_key = f"{session_id}:start"

                async def _cache_lookup():
                    row = await session_memory.get_session(session_id)
                    if row and row.get("stages_json"):
                        return {"session_row": row}
                    return None

                async def _emit_cached(_cached):
                    # 已存在，靜默忽略重複觸發；resume_session 路徑會接手
                    await emit({"type": "session_generating", "payload": {"session_id": session_id}})

                hit = await _wait_or_lookup_cache(
                    _start_key, timeout_s=300,
                    cache_lookup=_cache_lookup, emit_cached=_emit_cached,
                )
                if hit:
                    continue

                provider_name: str = p.get("provider", DEFAULT_PROVIDER)
                model: str | None = p.get("model") or None

                built = await _build_source_chunks_from_payload(p, emit)
                if built is None:
                    continue  # emit already sent
                source_chunks, source_file_ids, order_decision = built
                _ingest_log.info(
                    "start_session: ready for curriculum  session=%s  chunks=%d  "
                    "upload_files=%d",
                    session_id,
                    len(source_chunks),
                    len(source_file_ids),
                )

                target_depth = p.get("target_depth", "intermediate")
                question_mode = p.get("question_mode", "short_answer")
                same_material: bool = bool(p.get("same_material", True))

                if CURRICULUM_USE_ARQ:
                    from arq import create_pool
                    from arq.connections import RedisSettings
                    from .jobs.session_prepare import prepare_curriculum_session
                    from .jobs.enqueue import enqueue_curriculum_job
                    from .orchestrator.curriculum_pipeline_v2 import _build_sources_manifest
                    from .utils.content_hash import compute_content_hash

                    content_hash = compute_content_hash(source_chunks)
                    sources_manifest = _build_sources_manifest(source_chunks)
                    try:
                        await prepare_curriculum_session(
                            session_id=session_id,
                            user_id=user_id,
                            source_chunks=source_chunks,
                            content_hash=content_hash,
                            target_depth=target_depth,
                            question_mode=question_mode,
                            provider_name=provider_name,
                            model_name=model,
                            source_file_ids=source_file_ids,
                            sources_json=sources_manifest,
                            same_material=same_material,
                            order_decision=order_decision,
                        )
                        pool = await create_pool(RedisSettings.from_dsn(REDIS_URL))
                        try:
                            job_id = await enqueue_curriculum_job(pool, session_id)
                            await emit({
                                "type": "session_generating",
                                "payload": {"session_id": session_id},
                            })
                            if job_id is None:
                                _ws_log.info(
                                    "start_session: curriculum already in flight  session=%s",
                                    session_id,
                                )
                        finally:
                            await pool.close()
                    except Exception as e:
                        from .db.inflight_lock import release as _release_inflight
                        await _release_inflight(_start_key)
                        await emit({
                            "type": "error",
                            "payload": {"message": f"排程教材分析失敗：{e}"},
                        })
                    continue

                llm = create_provider(provider_name, model=model)
                orchestrator = LearningOrchestrator(llm)

                async def _run_start():
                    try:
                        await orchestrator.start_session(
                            session_id=session_id,
                            user_id=user_id,
                            source_chunks=source_chunks,
                            source_file_ids=source_file_ids,
                            target_depth=target_depth,
                            question_mode=question_mode,
                            provider_name=provider_name,
                            model_name=model,
                            emit=emit,
                            same_material=same_material,
                            order_decision=order_decision,
                        )
                    except asyncio.CancelledError:
                        _ws_log.info(
                            "start_session cancelled (shutdown?)  session=%s  checkpoint preserved",
                            session_id,
                        )
                        raise
                    except Exception as e:
                        await emit({"type": "error", "payload": {"message": f"啟動會話失敗：{e}"}})
                    finally:
                        await _gen_finish_async(_start_key)

                task = asyncio.create_task(_run_start())
                # 不 await task — dispatcher loop 必須能接收 cancel_generation 等後續訊息
                handle = await _gen_register_async(
                    _start_key, task, session_id=session_id, kind="start_session"
                )
                if handle is None:
                    # race lost：另一個 worker / connection 已在跑同個 start_session；
                    # 無 cache_lookup 可用，cancel 剛建的 task + log + 略過
                    task.cancel()
                    _ws_log.warning("start_session: race lost for key=%s", _start_key)

            elif msg_type == "confirm_map":
                _confirm_key = session_id

                async def _confirm_cache():
                    row = await session_memory.get_session(session_id)
                    if row and row.get("status") and row["status"] != "pending_confirmation":
                        return {"row": row}
                    return None

                async def _confirm_emit(_cached):
                    # 上一輪 confirm 已完成 — 不重跑，靜默忽略（resume_session 會接手還原 UI）
                    pass

                if await _wait_or_lookup_cache(
                    _confirm_key, 300, _confirm_cache, _confirm_emit,
                ):
                    continue

                orch = await _build_orchestrator_for_session(session_id, p)

                async def _run_confirm():
                    try:
                        await orch.confirm_session(
                            session_id=session_id,
                            user_id=user_id,
                            emit=emit,
                        )
                    except Exception as e:
                        await emit({"type": "error", "payload": {"message": f"確認知識地圖失敗：{e}"}})
                    finally:
                        await _gen_finish_async(_confirm_key)

                task = asyncio.create_task(_run_confirm())
                handle = await _gen_register_async(
                    _confirm_key, task, session_id=session_id, kind="confirm_map"
                )
                if handle is None:
                    # race lost：重跑 cache lookup（前一 task 應已寫 DB）
                    task.cancel()
                    if not await _wait_or_lookup_cache(
                        _confirm_key, 300, _confirm_cache, _confirm_emit,
                    ):
                        _ws_log.warning("confirm_map: race lost, no cache hit for key=%s", _confirm_key)

            elif msg_type == "submit_answer":
                orch = await _build_orchestrator_for_session(session_id, p)
                _question_id = p["question_id"]
                _answer_key = f"{session_id}:answer:{_question_id}"

                async def _ans_cache():
                    return await _lookup_answer_cache(session_id, _question_id)

                async def _ans_emit(r):
                    await emit({
                        "type": "feedback",
                        "payload": {
                            "question_id": _question_id,
                            "score": r["score"],
                            "feedback_text": r["feedback"],
                            "needs_clarification": False,
                            "clarification_question": None,
                        },
                    })

                if await _wait_or_lookup_cache(_answer_key, 60, _ans_cache, _ans_emit):
                    continue

                async def _run_answer():
                    try:
                        await orch.handle_answer(
                            session_id=session_id,
                            user_id=user_id,
                            question_id=_question_id,
                            answer=p["answer"],
                            emit=emit,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        await emit({"type": "error", "payload": {"message": f"評分失敗：{e}"}})
                    finally:
                        await _gen_finish_async(_answer_key)

                task = asyncio.create_task(_run_answer())
                handle = await _gen_register_async(
                    _answer_key, task, session_id=session_id, kind="submit_answer"
                )
                if handle is None:
                    task.cancel()
                    if not await _wait_or_lookup_cache(
                        _answer_key, 60, _ans_cache, _ans_emit,
                    ):
                        _ws_log.warning("submit_answer: race lost, no cache hit for key=%s", _answer_key)

            elif msg_type == "resume_session":
                session_id_to_resume: str = p.get("session_id", session_id)
                # reload 後可能殘留 dead worker 的 resume lock；先清再 wait
                await inflight_cleanup_dead_workers()
                # 只等真正生成中的 task（submit_answer/run_stage），不等 resume 自身 lock
                await _wait_for_session_idle(
                    session_id_to_resume,
                    timeout_s=120,
                    exclude_kinds=("resume_session",),
                )

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

                async def _run_resume():
                    try:
                        await orchestrator.resume_session(
                            session_id=session_id_to_resume,
                            user_id=user_id,
                            emit=emit,
                        )
                    except Exception as e:
                        await emit({"type": "error", "payload": {"message": f"恢復會話失敗：{e}"}})
                    finally:
                        await _gen_finish_async(session_id_to_resume)

                task = asyncio.create_task(_run_resume())
                handle = await _gen_register_async(
                    session_id_to_resume, task,
                    session_id=session_id_to_resume, kind="resume_session",
                )
                if handle is None:
                    # stale resume lock（reload 中斷）— 強制釋放後重試一次
                    await inflight_release(session_id_to_resume)
                    task = asyncio.create_task(_run_resume())
                    handle = await _gen_register_async(
                        session_id_to_resume, task,
                        session_id=session_id_to_resume, kind="resume_session",
                    )
                if handle is None:
                    task.cancel()
                    _ws_log.warning("resume_session: race lost for key=%s", session_id_to_resume)
                    await emit({
                        "type": "error",
                        "payload": {"message": "恢復會話失敗：另一個恢復任務正在進行，請稍後再試"},
                    })

            elif msg_type == "request_hint":
                await emit({
                    "type": "hint",
                    "payload": {"text": "提示功能即將開放"},
                })

            elif msg_type == "ask_tutor":
                orch = await _build_orchestrator_for_session(session_id, p)
                raw_sid = p.get("stage_id")
                _ask_question = p.get("question", "").strip()
                _ask_stage_id = int(raw_sid) if raw_sid is not None else None
                _tutor_key = f"{session_id}:tutor"

                async def _tutor_cache():
                    wm_chk = get_working_memory(session_id)
                    sid_chk = _ask_stage_id if _ask_stage_id is not None else wm_chk.current_stage_id
                    all_tutor = await session_memory.get_all_tutor_records(session_id)
                    cached = next(
                        (r for r in all_tutor.get(sid_chk, []) if r["question"] == _ask_question),
                        None,
                    )
                    if not cached:
                        return None
                    return {"record": cached, "stage_id": sid_chk}

                async def _tutor_emit(found):
                    cached = found["record"]
                    _payload: dict = {
                        "question": _ask_question,
                        "answer": cached["answer"],
                        "in_scope": cached["in_scope"],
                        "stage_id": found["stage_id"],
                    }
                    if "id" in cached:
                        _payload["id"] = cached["id"]
                    await emit({"type": "tutor_reply", "payload": _payload})

                if await _wait_or_lookup_cache(_tutor_key, 60, _tutor_cache, _tutor_emit):
                    continue

                async def _run_tutor():
                    try:
                        await orch.handle_student_question(
                            session_id=session_id,
                            question=_ask_question,
                            stage_id=_ask_stage_id,
                            emit=emit,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        await emit({"type": "error", "payload": {"message": f"AI 回答失敗：{e}"}})
                    finally:
                        await _gen_finish_async(_tutor_key)

                task = asyncio.create_task(_run_tutor())
                handle = await _gen_register_async(
                    _tutor_key, task, session_id=session_id, kind="ask_tutor"
                )
                if handle is None:
                    task.cancel()
                    if not await _wait_or_lookup_cache(
                        _tutor_key, 60, _tutor_cache, _tutor_emit,
                    ):
                        _ws_log.warning("ask_tutor: race lost, no cache hit for key=%s", _tutor_key)

            elif msg_type == "cancel_generation":
                target_key = p.get("key")
                cancelled_keys: list[str] = []
                if not target_key:
                    # 沒指定 key 就嘗試取消該 session 任何 in-flight 的常見來源
                    for k in (f"{session_id}:start", session_id, f"{session_id}:tutor"):
                        if await _gen_cancel_async(k):
                            cancelled_keys.append(k)
                    if not cancelled_keys:
                        _ws_log.info("cancel_generation: no in-flight task for session=%s", session_id)
                else:
                    if await _gen_cancel_async(target_key):
                        cancelled_keys.append(target_key)
                    else:
                        _ws_log.info("cancel_generation: key=%s not found", target_key)
                if any(k.endswith(":start") or k == session_id for k in cancelled_keys):
                    await session_memory.abandon_generating_stub(session_id)
                for k in cancelled_keys:
                    kind = "ask_tutor" if k.endswith(":tutor") else "other"
                    await emit({
                        "type": "generation_cancelled",
                        "payload": {"key": k, "kind": kind},
                    })

    except (WebSocketDisconnect, RuntimeError):
        _ws_log.info("WS DISCONNECT  session=%s  user=%s", session_id, user_id)
        ws_manager.disconnect(session_id, websocket)
        # 只有在此 WS 確實是當前連線（disconnect 後 session 已無 WS）時才清除資源，
        # 避免把後繼裝置的 working memory 一起清掉。
        # Phase 3 Task C1：orchestrator 已 stateless 化（每訊息重建），無需 pop。
        if not ws_manager.has_active_ws(session_id):
            delete_working_memory(session_id)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/config")
async def get_config():
    return {"default_provider": DEFAULT_PROVIDER.lower()}


frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    # 必須最後掛載，避免 StaticFiles 攔截 /ws/* 等非 HTTP 路由
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
