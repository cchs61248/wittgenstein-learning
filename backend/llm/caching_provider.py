"""Caching wrapper for curriculum pipeline LLM chat calls."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import AsyncGenerator, Optional

from ..config import CURRICULUM_PROMPT_VERSION, LLM_CACHE_ENABLED
from ..memory import llm_cache
from .base_provider import BaseLLMProvider, LLMMessage, LLMResponse
from .cache_context import get_agent_name, get_content_hash, get_region_id

_log = logging.getLogger("wl.llm.cache")

NON_CACHEABLE_AGENTS = frozenset({
    "TeacherAgent",
    "QuestionGeneratorAgent",
    "EvaluatorAgent",
    "DriftVerifierAgent",
})


def build_cache_key(
    *,
    scope: str,
    prompt_version: str,
    model: str,
    agent_name: str,
    region_id: str | None,
    system_prompt: str | None,
    messages: list[LLMMessage],
) -> str:
    payload = {
        "scope": scope,
        "prompt_version": prompt_version,
        "model": model,
        "agent": agent_name,
        "region_id": region_id or "",
        "system": system_prompt or "",
        "messages": [(m.role.value, m.content) for m in messages],
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def maybe_wrap_curriculum_llm(
    llm: BaseLLMProvider,
    *,
    content_hash: str | None = None,
) -> BaseLLMProvider:
    from .cache_context import set_content_hash

    if content_hash:
        set_content_hash(content_hash)
    if LLM_CACHE_ENABLED:
        return CachingLLMProvider(llm, scope="curriculum")
    return llm


class CachingLLMProvider(BaseLLMProvider):
    """Delegate to inner provider; cache curriculum agent chat() results in SQLite."""

    def __init__(self, inner: BaseLLMProvider, *, scope: str = "curriculum", enabled: bool = True):
        super().__init__(inner.model, inner.temperature, inner.max_tokens)
        self._inner = inner
        self._scope = scope
        self._enabled = enabled

    @property
    def context_window(self) -> int:
        return self._inner.context_window

    async def chat(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        if not self._enabled or not LLM_CACHE_ENABLED:
            return await self._inner.chat(messages, system_prompt)
        agent = get_agent_name()
        if not agent or agent in NON_CACHEABLE_AGENTS:
            return await self._inner.chat(messages, system_prompt)
        key = build_cache_key(
            scope=self._scope,
            prompt_version=CURRICULUM_PROMPT_VERSION,
            model=self._inner.model,
            agent_name=agent,
            region_id=get_region_id(),
            system_prompt=system_prompt,
            messages=messages,
        )
        cached = await llm_cache.get(key)
        if cached:
            await llm_cache.record_hit(key)
            _log.info("llm_cache HIT  agent=%s  key=%s", agent, key[:12])
            return cached
        resp = await self._inner.chat(messages, system_prompt)
        await llm_cache.put(
            key,
            agent_name=agent,
            model_name=self._inner.model,
            prompt_version=CURRICULUM_PROMPT_VERSION,
            result=resp,
            content_hash=get_content_hash(),
            region_id=get_region_id(),
            scope=self._scope,
        )
        _log.info("llm_cache MISS  agent=%s  key=%s", agent, key[:12])
        return resp

    async def stream_chat(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        async for chunk in self._inner.stream_chat(messages, system_prompt):
            yield chunk

    async def _do_chat(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        return await self._inner._do_chat(messages, system_prompt)

    async def _do_stream_chat(
        self,
        messages: list[LLMMessage],
        system_prompt: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        async for chunk in self._inner._do_stream_chat(messages, system_prompt):
            yield chunk
