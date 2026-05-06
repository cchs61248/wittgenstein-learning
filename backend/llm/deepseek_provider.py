import os
from openai import AsyncOpenAI
from .base_provider import BaseLLMProvider
from .openai_provider import OpenAIProvider


class DeepSeekProvider(OpenAIProvider):
    def __init__(self, model: str = "deepseek-v4-flash", temperature: float = 0.7, max_tokens: int = 4096):
        BaseLLMProvider.__init__(self, model, temperature, max_tokens)
        self._client = AsyncOpenAI(
            base_url="https://api.deepseek.com",
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        )

    @property
    def context_window(self) -> int:
        return 1_000_000
