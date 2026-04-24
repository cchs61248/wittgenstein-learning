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

    def _add_message(self, role: MessageRole, content: str) -> None:
        self._messages.append(LLMMessage(role=role, content=content))

    def _token_usage(self) -> int:
        return sum(self.token_counter.count(m.content) for m in self._messages)

    def _within_budget(self, budget: int) -> bool:
        return self._token_usage() < budget

    def _reset(self) -> None:
        self._messages = []

    @abstractmethod
    async def run(self, ctx: AgentContext) -> dict[str, Any]: ...
