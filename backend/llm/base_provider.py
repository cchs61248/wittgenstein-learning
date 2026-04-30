import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import AsyncGenerator, Optional


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass
class LLMMessage:
    role: MessageRole
    content: str
    attachment: Optional[dict] = None


@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int
    model: str
    finish_reason: str  # "stop" | "length" | "error"


_SEP = "=" * 72
_DASH = "─" * 60


def _fmt_messages(messages: list[LLMMessage]) -> str:
    parts = []
    for m in messages:
        parts.append(f"[{m.role.value.upper()}]\n{m.content}")
    return f"\n{_DASH}\n".join(parts)


class BaseLLMProvider(ABC):
    def __init__(self, model: str, temperature: float = 0.7, max_tokens: int = 4096):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    # ── public API with logging wrappers ───────────────────────────────────

    async def chat(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        from ..utils.logger import llm_logger
        log = llm_logger()
        call_id = uuid.uuid4().hex[:8]
        provider = type(self).__name__

        log.debug(
            "%s\nLLM CHAT REQUEST  call_id=%s  provider=%s  model=%s\n"
            "── SYSTEM PROMPT ──\n%s\n"
            "── MESSAGES ──\n%s\n%s",
            _SEP, call_id, provider, self.model,
            system_prompt or "(none)",
            _fmt_messages(messages),
            _SEP,
        )

        t0 = time.perf_counter()
        try:
            response = await self._do_chat(messages, system_prompt)
        except Exception as exc:
            log.error(
                "LLM CHAT ERROR  call_id=%s  provider=%s  model=%s\n%s",
                call_id, provider, self.model, exc,
                exc_info=True,
            )
            raise

        elapsed = time.perf_counter() - t0
        log.debug(
            "%s\nLLM CHAT RESPONSE  call_id=%s  provider=%s  model=%s\n"
            "  elapsed=%.2fs  in_tokens=%d  out_tokens=%d  finish=%s\n"
            "── CONTENT ──\n%s\n%s",
            _SEP, call_id, provider, self.model,
            elapsed, response.input_tokens, response.output_tokens,
            response.finish_reason, response.content, _SEP,
        )
        log.info(
            "LLM chat  call_id=%s  provider=%s  model=%s  "
            "in=%d  out=%d  elapsed=%.2fs",
            call_id, provider, self.model,
            response.input_tokens, response.output_tokens, elapsed,
        )
        return response

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        from ..utils.logger import llm_logger
        log = llm_logger()
        call_id = uuid.uuid4().hex[:8]
        provider = type(self).__name__

        log.debug(
            "%s\nLLM STREAM REQUEST  call_id=%s  provider=%s  model=%s\n"
            "── SYSTEM PROMPT ──\n%s\n"
            "── MESSAGES ──\n%s\n%s",
            _SEP, call_id, provider, self.model,
            system_prompt or "(none)",
            _fmt_messages(messages),
            _SEP,
        )
        log.info(
            "LLM stream start  call_id=%s  provider=%s  model=%s",
            call_id, provider, self.model,
        )

        t0 = time.perf_counter()
        chunks: list[str] = []
        try:
            async for chunk in self._do_stream_chat(messages, system_prompt):
                chunks.append(chunk)
                yield chunk
        except Exception as exc:
            log.error(
                "LLM STREAM ERROR  call_id=%s  provider=%s  model=%s\n%s",
                call_id, provider, self.model, exc,
                exc_info=True,
            )
            raise
        finally:
            elapsed = time.perf_counter() - t0
            full = "".join(chunks)
            log.debug(
                "%s\nLLM STREAM RESPONSE  call_id=%s  provider=%s  model=%s\n"
                "  elapsed=%.2fs  chars=%d\n"
                "── FULL CONTENT ──\n%s\n%s",
                _SEP, call_id, provider, self.model,
                elapsed, len(full), full, _SEP,
            )
            log.info(
                "LLM stream end  call_id=%s  provider=%s  model=%s  "
                "chars=%d  elapsed=%.2fs",
                call_id, provider, self.model, len(full), elapsed,
            )

    # ── abstract implementation hooks ─────────────────────────────────────

    @abstractmethod
    async def _do_chat(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str] = None,
    ) -> LLMResponse: ...

    @abstractmethod
    async def _do_stream_chat(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str] = None,
    ) -> AsyncGenerator[str, None]: ...

    @property
    @abstractmethod
    def context_window(self) -> int: ...
