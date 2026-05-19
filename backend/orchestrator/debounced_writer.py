import time
from typing import Awaitable, Callable

from ..utils.logger import orchestrator_logger

StoreFn = Callable[[str, int, str], Awaitable[None]]


class DebouncedExplanationWriter:
    """
    對 store_stage_explanation 做時間+大小雙閘門 debounce。
    任一達到就寫；flush() 強制寫一次最新狀態（若有未寫資料）。
    """
    def __init__(
        self,
        store_fn: StoreFn,
        session_id: str,
        stage_id: int,
        min_interval_s: float = 0.5,
        min_delta_chars: int = 200,
    ) -> None:
        self._store = store_fn
        self._sid = session_id
        self._stage = stage_id
        self._min_interval_s = min_interval_s
        self._min_delta_chars = min_delta_chars
        self._latest: str = ""
        self._last_written: str = ""
        self._last_write_at: float = time.monotonic()
        self._log = orchestrator_logger()

    async def update(self, full_text: str) -> None:
        self._latest = full_text
        now = time.monotonic()
        delta = len(full_text) - len(self._last_written)
        time_due = (now - self._last_write_at) >= self._min_interval_s
        size_due = delta >= self._min_delta_chars
        if time_due or size_due:
            await self._do_write(reason="time" if time_due else "size")

    async def flush(self) -> None:
        if self._latest != self._last_written:
            await self._do_write(reason="flush")

    async def _do_write(self, reason: str = "?") -> None:
        text = self._latest
        try:
            await self._store(self._sid, self._stage, text)
        except Exception:
            self._log.warning(
                "DebouncedExplanationWriter write failed  session=%s  stage_id=%s  "
                "reason=%s  chars=%d",
                self._sid, self._stage, reason, len(text),
                exc_info=True,
            )
            raise
        self._last_written = text
        self._last_write_at = time.monotonic()
        self._log.debug(
            "DebouncedExplanationWriter write ok  session=%s  stage_id=%s  "
            "reason=%s  chars=%d",
            self._sid, self._stage, reason, len(text),
        )
