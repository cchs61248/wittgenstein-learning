"""Bundle asyncio.Task + Event per in-flight generation key for cancellation.

Used by `_wait_or_lookup_cache` (waiters) and `cancel_generation` (frontend
stop button). The previous `_active_generations: dict[str, asyncio.Event]`
model could not cancel — only wait — so we attach the task here too.

Phase 3 Task B3 加 async 版本 (register_async / finish_async / cancel_async)
搭配 DB-backed inflight_lock，支援跨 worker dedup。同步版本保留以供既有
unit tests 使用。
"""
import asyncio
from dataclasses import dataclass
from typing import Optional

from ..db import inflight_lock


@dataclass
class _GenerationHandle:
    key: str
    task: asyncio.Task
    event: asyncio.Event


_registry: dict[str, _GenerationHandle] = {}


def register(key: str, task: asyncio.Task) -> _GenerationHandle:
    """Bind a task to a key. Auto-removes from registry when task completes."""
    event = asyncio.Event()
    handle = _GenerationHandle(key=key, task=task, event=event)
    _registry[key] = handle

    def _on_done(_t: asyncio.Task) -> None:
        event.set()
        # 若 finish() 沒被外部呼叫（例外狀況），仍清掉自身
        if _registry.get(key) is handle:
            _registry.pop(key, None)

    task.add_done_callback(_on_done)
    return handle


def get_active(key: str) -> Optional[_GenerationHandle]:
    return _registry.get(key)


def finish(key: str) -> None:
    """正常完成路徑呼叫 — task 通常已結束，主要清 registry。"""
    h = _registry.pop(key, None)
    if h:
        h.event.set()


async def cancel(key: str) -> bool:
    """Cancel the task registered at `key`. Returns True if found."""
    h = _registry.get(key)
    if not h:
        return False
    if not h.task.done():
        h.task.cancel()
    h.event.set()
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
    ok = await inflight_lock.acquire(
        key, session_id=session_id, kind=kind, meta_json=meta_json
    )
    if not ok:
        return None

    event = asyncio.Event()
    handle = _GenerationHandle(key=key, task=task, event=event)
    _registry[key] = handle

    def _on_done(_t: asyncio.Task) -> None:
        event.set()
        if _registry.get(key) is handle:
            _registry.pop(key, None)
        # 同步 callback 內 fire-and-forget release（loop 還在跑）
        asyncio.create_task(inflight_lock.release(key))

    task.add_done_callback(_on_done)
    return handle


async def finish_async(key: str) -> None:
    """正常完成路徑：clear local registry + release DB lock。"""
    h = _registry.pop(key, None)
    if h:
        h.event.set()
    await inflight_lock.release(key)


async def cancel_async(key: str) -> bool:
    """
    Cancel task at `key` + release DB lock。
    回傳 True 表 local registry 有命中；False 表沒命中（但仍嘗試 release
    DB lock，因為可能是別 worker hold — 雖然當前設計沒做跨 worker 取消）。
    """
    h = _registry.get(key)
    if h:
        if not h.task.done():
            h.task.cancel()
        h.event.set()
    await inflight_lock.release(key)
    return h is not None
