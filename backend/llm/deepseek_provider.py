import os
from typing import Optional
from openai import AsyncOpenAI
from .base_provider import BaseLLMProvider, LLMMessage, LLMResponse
from .openai_provider import OpenAIProvider


class DeepSeekProvider(OpenAIProvider):
    def __init__(
        self,
        model: str = "deepseek-v4-flash",
        temperature: float = 0.7,
        max_tokens: int = 32768,
        reasoning_effort: Optional[str] = None,
    ):
        BaseLLMProvider.__init__(self, model, temperature, max_tokens)
        self._reasoning_effort = reasoning_effort
        self._client = AsyncOpenAI(
            base_url="https://api.deepseek.com",
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        )

    @property
    def context_window(self) -> int:
        return 1_000_000

    async def _do_chat(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        extra: dict = {}
        if self._reasoning_effort:
            extra["reasoning_effort"] = self._reasoning_effort
        response = await self._client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=self._to_openai_messages(messages, system_prompt),
            **extra,
        )
        choice = response.choices[0]
        # deepseek 推理模型將思考過程放在 reasoning_content，content 可能為空
        content = choice.message.content or ""
        if not content:
            reasoning = getattr(choice.message, "reasoning_content", None)
            content = reasoning or ""
        return LLMResponse(
            content=content,
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
            model=response.model,
            finish_reason=choice.finish_reason or "stop",
        )
