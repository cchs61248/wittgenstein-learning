from enum import Enum
from typing import Optional
from .base_provider import BaseLLMProvider
from .claude_provider import ClaudeProvider
from .openai_provider import OpenAIProvider
from .gemini_provider import GeminiProvider
from .monica_provider import MonicaProvider


class LLMProviderType(str, Enum):
    OPENAI = "openai"
    CLAUDE = "claude"
    GEMINI = "gemini"
    MONICA = "monica"


_DEFAULTS: dict[LLMProviderType, tuple[str, type]] = {
    LLMProviderType.CLAUDE:  ("claude-sonnet-4-6", ClaudeProvider),
    LLMProviderType.OPENAI:  ("gpt-5.4-mini", OpenAIProvider),
    LLMProviderType.GEMINI:  ("gemini-3-flash-preview", GeminiProvider),
    LLMProviderType.MONICA:  ("claude-4.6-sonnet", MonicaProvider),
}


def create_provider(
    provider_type: LLMProviderType | str,
    model: Optional[str] = None,
    **kwargs,
) -> BaseLLMProvider:
    ptype = LLMProviderType(provider_type)
    default_model, cls = _DEFAULTS[ptype]
    return cls(model=model or default_model, **kwargs)
