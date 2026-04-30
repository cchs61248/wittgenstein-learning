import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from ..llm.base_provider import BaseLLMProvider, LLMMessage, MessageRole
from ..utils.token_counter import TokenCounter


@dataclass
class AgentContext:
    session_id: str
    user_id: str
    task_payload: dict[str, Any]
    max_context_tokens: int = 4000


class BaseAgent(ABC):
    def __init__(self, llm: BaseLLMProvider, token_counter: TokenCounter):
        self.llm = llm
        self.token_counter = token_counter
        self._messages: list[LLMMessage] = []

    @property
    def _log(self) -> logging.Logger:
        return logging.getLogger("wl.agents")

    def _add_message(self, role: MessageRole, content: str) -> None:
        self._messages.append(LLMMessage(role=role, content=content))

    def _token_usage(self) -> int:
        return sum(self.token_counter.count(m.content) for m in self._messages)

    def _within_budget(self, budget: int) -> bool:
        return self._token_usage() < budget

    def _reset(self) -> None:
        self._messages = []

    def _log_start(self, ctx: AgentContext, **extra: Any) -> float:
        """呼叫 run() 前記錄 agent 開始，回傳開始時間。"""
        extra_str = "  ".join(f"{k}={v}" for k, v in extra.items())
        self._log.info(
            "%s START  session=%s  %s",
            type(self).__name__, ctx.session_id, extra_str,
        )
        self._log.debug(
            "%s CONTEXT  session=%s\n%s",
            type(self).__name__, ctx.session_id,
            json.dumps(ctx.task_payload, ensure_ascii=False, default=str),
        )
        return time.perf_counter()

    def _log_end(self, ctx: AgentContext, t0: float, result: dict[str, Any]) -> None:
        """呼叫 run() 結束後記錄結果。"""
        elapsed = time.perf_counter() - t0
        self._log.info(
            "%s END  session=%s  elapsed=%.2fs",
            type(self).__name__, ctx.session_id, elapsed,
        )
        self._log.debug(
            "%s RESULT  session=%s\n%s",
            type(self).__name__, ctx.session_id,
            json.dumps(result, ensure_ascii=False, default=str),
        )

    @abstractmethod
    async def run(self, ctx: AgentContext) -> dict[str, Any]: ...
