from typing import AsyncGenerator, Optional
import anthropic
from .base_provider import BaseLLMProvider, LLMMessage, LLMResponse, MessageRole


class ClaudeProvider(BaseLLMProvider):
    def __init__(self, model: str = "claude-sonnet-4-6", temperature: float = 0.7, max_tokens: int = 4096):
        super().__init__(model, temperature, max_tokens)
        self._client = anthropic.AsyncAnthropic()

    @property
    def context_window(self) -> int:
        return 200_000

    def _to_anthropic_messages(self, messages: list[LLMMessage]) -> list[dict]:
        formatted: list[dict] = []
        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                continue
            if msg.role == MessageRole.USER and msg.attachment and msg.attachment.get("claude_file_id"):
                formatted.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": msg.content},
                            {
                                "type": "document",
                                "source": {
                                    "type": "file",
                                    "file_id": msg.attachment["claude_file_id"],
                                },
                            },
                        ],
                    }
                )
            else:
                formatted.append({"role": msg.role.value, "content": msg.content})
        return formatted

    async def chat(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": self._to_anthropic_messages(messages),
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        response = await self._client.messages.create(**kwargs)
        return LLMResponse(
            content=response.content[0].text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
            finish_reason=response.stop_reason or "stop",
        )

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": self._to_anthropic_messages(messages),
        }
        if system_prompt:
            kwargs["system"] = system_prompt

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text
