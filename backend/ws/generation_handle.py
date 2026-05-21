"""Bundle asyncio.Task + Event per in-flight generation key for cancellation.

Used by `_wait_or_lookup_cache` (waiters) and `cancel_generation` (frontend
stop button). The previous `_active_generations: dict[str, asyncio.Event]`
model could not cancel — only wait — so we attach the task here too.

Phase 3 Task B3 加 async 版本 (register_async / finish_async / cancel_async)
搭配 DB-backed inflight_lock，支援跨 worker dedup。同步版本保留以供既有
unit tests 使用。
"""
import asyncio
import time
from dataclasses import dataclass
from typing import Optional

from ..db import inflight_lock
from ..utils.logger import ws_logger


@dataclass
class _GenerationHandle:
    key: str
    task: asyncio.Task
    event: asyncio.Event


_registry: dict[str, _GenerationHandle] = {}


def register(key: str, task: asyncio.Task) -> _GenerationHandle:
    """Bind a task to a key. Auto-removes from registry when task completes."""
    log = ws_logger()
    event = asyncio.Event()
    handle = _GenerationHandle(key=key, task=task, event=event)
    _registry[key] = handle
    log.debug("generation_handle register  key=%s", key)

    def _on_done(_t: asyncio.Task) -> None:
        event.set()
        # 若 finish() 沒被外部呼叫（例外狀況），仍清掉自身
        if _registry.get(key) is handle:
            _registry.pop(key, None)
            log.debug("generation_handle auto_clear  key=%s", key)

    task.add_done_callback(_on_done)
    return handle


def get_active(key: str) -> Optional[_GenerationHandle]:
    return _registry.get(key)


def _local_handles_for_session(session_id: str) -> list[_GenerationHandle]:
    prefix = f"{session_id}:"
    out: list[_GenerationHandle] = []
    for key, handle in _registry.items():
        if key == session_id or key.startswith(prefix):
            out.append(handle)
    return out


async def wait_for_session_idle(session_id: str, timeout_s: float = 300) -> bool:
    """
    等待此 session 所有 inflight 任務結束（含 :answer:、:start 等子 key）。
    同 worker 等本地 Task event；跨 worker 輪詢 DB inflight_locks。
    回傳 True 表示已 idle，False 表示 timeout 時仍有 lock。
    """
    log = ws_logger()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        local = _local_handles_for_session(session_id)
        if local:
            remaining = max(0.05, deadline - time.monotonic())
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(h.event.wait() for h in local)),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                log.debug(
                    "wait_for_session_idle local timeout  session=%s  keys=%s",
                    session_id,
                    [h.key for h in local],
                )
        if not local and not await inflight_lock.has_session_inflight(session_id):
            return True
        await asyncio.sleep(0.2)
    still = await inflight_lock.has_session_inflight(session_id)
    log.warning(
        "wait_for_session_idle timeout  session=%s  db_inflight=%s  local_keys=%s",
        session_id,
        still,
        [h.key for h in _local_handles_for_session(session_id)],
    )
    return not still and not _local_handles_for_session(session_id)


def finish(key: str) -> None:
    """正常完成路徑呼叫 — task 通常已結束，主要清 registry。"""
    log = ws_logger()
    h = _registry.pop(key, None)
    if h:
        h.event.set()
        log.debug("generation_handle finish  key=%s", key)


async def cancel(key: str) -> bool:
    """Cancel the task registered at `key`. Returns True if found."""
    log = ws_logger()
    h = _registry.get(key)
    if not h:
        log.debug("generation_handle cancel miss  key=%s", key)
        return False
    if not h.task.done():
        h.task.cancel()
    h.event.set()
    log.info("generation_handle cancel  key=%s  task_was_done=%s", key, h.task.done())
    return True


async def register_async(
    key: str,
    task: asyncio.Task,
    *,
    session_id: str,
    kind: str,
    meta_json: Optional[str] = None,
) -> Optional[_GenerationHandle]:
    """
    Phase 3 Task B3：先 acquire DB inflight_lock，成功才註冊本地 handle。

    回傳 None 表 DB 已有 lock（同 worker 或別 worker 正在跑），呼叫端應
    cancel 剛建立的 task 並重跑 cache lookup（前一 task 應已寫 DB）。

    done_callback 內 fire-and-forget release DB lock，保險防漏：
    若某條路徑沒呼叫 finish_async/cancel_async，DB lock 仍會在 task 結束
    時自動釋放。
    """
    log = ws_logger()
    ok = await inflight_lock.acquire(
        key, session_id=session_id, kind=kind, meta_json=meta_json
    )
    if not ok:
        log.debug(
            "generation_handle register_async lock_busy  key=%s  session=%s  kind=%s",
            key, session_id, kind,
        )
        return None

    event = asyncio.Event()
    handle = _GenerationHandle(key=key, task=task, event=event)
    _registry[key] = handle
    log.debug(
        "generation_handle register_async ok  key=%s  session=%s  kind=%s",
        key, session_id, kind,
    )

    def _on_done(_t: asyncio.Task) -> None:
        event.set()
        if _registry.get(key) is handle:
            _registry.pop(key, None)
        # 同步 callback 內 fire-and-forget release（loop 還在跑）
        release_task = asyncio.create_task(inflight_lock.release(key))

        def _on_release_done(rt: asyncio.Task) -> None:
            exc = rt.exception()
            if exc is not None:
                log.error(
                    "generation_handle on_done release failed  key=%s",
                    key, exc_info=exc,
                )

        release_task.add_done_callback(_on_release_done)

    task.add_done_callback(_on_done)
    return handle


async def finish_async(key: str) -> None:
    """正常完成路徑：clear local registry + release DB lock。"""
    log = ws_logger()
    h = _registry.pop(key, None)
    if h:
        h.event.set()
    await inflight_lock.release(key)
    log.debug("generation_handle finish_async  key=%s  had_handle=%s", key, h is not None)


async def cancel_async(key: str) -> bool:
    """
    Cancel task at `key` + release DB lock。
    回傳 True 表 local registry 有命中；False 表沒命中（但仍嘗試 release
    DB lock，因為可能是別 worker hold — 雖然當前設計沒做跨 worker 取消）。
    """
    log = ws_logger()
    h = _registry.get(key)
    if h:
        if not h.task.done():
            h.task.cancel()
        h.event.set()
    await inflight_lock.release(key)
    log.info(
        "generation_handle cancel_async  key=%s  had_local_handle=%s",
        key, h is not None,
    )
    return h is not None
