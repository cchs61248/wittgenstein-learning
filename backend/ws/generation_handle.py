"""Bundle asyncio.Task + Event per in-flight generation key for cancellation.

Used by `_wait_or_lookup_cache` (waiters) and `cancel_generation` (frontend
stop button). The previous `_active_generations: dict[str, asyncio.Event]`
model could not cancel — only wait — so we attach the task here too.
"""
import asyncio
from dataclasses import dataclass
from typing import Optional


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
