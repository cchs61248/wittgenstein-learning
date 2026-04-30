from typing import AsyncGenerator, Optional
from openai import AsyncOpenAI
from .base_provider import BaseLLMProvider, LLMMessage, LLMResponse, MessageRole


class OpenAIProvider(BaseLLMProvider):
    def __init__(self, model: str = "gpt-4o-mini", temperature: float = 0.7, max_tokens: int = 4096):
        super().__init__(model, temperature, max_tokens)
        self._client = AsyncOpenAI()

    @property
    def context_window(self) -> int:
        return 128_000

    def _to_openai_messages(
        self, messages: list[LLMMessage], system_prompt: Optional[str]
    ) -> list[dict]:
        result = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        for msg in messages:
            if msg.role != MessageRole.SYSTEM:
                if msg.attachment and msg.attachment.get("openai_file_id") and msg.role == MessageRole.USER:
                    result.append(
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": msg.content},
                                {"type": "file", "file": {"file_id": msg.attachment["openai_file_id"]}},
                            ],
                        }
                    )
                else:
                    result.append({"role": msg.role.value, "content": msg.content})
        return result

    def _token_param(self) -> dict:
        # GPT-5 系列改用 max_completion_tokens
        if self.model.startswith("gpt-5"):
            return {"max_completion_tokens": self.max_tokens}
        return {"max_tokens": self.max_tokens}

    async def _do_chat(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        response = await self._client.chat.completions.create(
            model=self.model,
            **self._token_param(),
            temperature=self.temperature,
            messages=self._to_openai_messages(messages, system_prompt),
        )
        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
            model=response.model,
            finish_reason=choice.finish_reason or "stop",
        )

    async def _do_stream_chat(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        stream = await self._client.chat.completions.create(
            model=self.model,
            **self._token_param(),
            temperature=self.temperature,
            messages=self._to_openai_messages(messages, system_prompt),
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
