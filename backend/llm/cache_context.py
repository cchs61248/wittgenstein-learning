"""Context variables for curriculum LLM cache key construction."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

_agent: ContextVar[str | None] = ContextVar("llm_cache_agent", default=None)
_region: ContextVar[str | None] = ContextVar("llm_cache_region", default=None)
_content_hash: ContextVar[str | None] = ContextVar("llm_cache_content_hash", default=None)


def get_agent_name() -> str | None:
    return _agent.get()


def get_region_id() -> str | None:
    return _region.get()


def get_content_hash() -> str | None:
    return _content_hash.get()


def set_content_hash(content_hash: str | None) -> None:
    _content_hash.set(content_hash)


@contextmanager
def llm_cache_context(*, agent_name: str, region_id: str | None = None):
    token_a = _agent.set(agent_name)
    token_r = _region.set(region_id)
    try:
        yield
    finally:
        _agent.reset(token_a)
        _region.reset(token_r)
