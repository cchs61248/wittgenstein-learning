import os
from typing import Optional
from openai import AsyncOpenAI
from .base_provider import BaseLLMProvider, LLMMessage, MessageRole
from .openai_provider import OpenAIProvider


class MonicaProvider(OpenAIProvider):
    def __init__(self, model: str = "claude-4.6-sonnet", temperature: float = 0.7, max_tokens: int = 4096):
        BaseLLMProvider.__init__(self, model, temperature, max_tokens)
        base_url = os.getenv("MONICA_BASE_URL", "http://localhost:8001/v1")
        api_key = os.getenv("MONICA_API_KEY", "monica")
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    def _to_openai_messages(
        self, messages: list[LLMMessage], system_prompt: Optional[str]
    ) -> list[dict]:
        result = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                continue
            monica_data = msg.attachment and msg.attachment.get("monica_file_data")
            if monica_data and msg.role == MessageRole.USER:
                result.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": msg.content},
                        {
                            "type": "file",
                            "file": {
                                "filename": msg.attachment.get("filename", "file"),
                                "file_data": monica_data,
                            },
                        },
                    ],
                })
            else:
                result.append({"role": msg.role.value, "content": msg.content})
        return result
