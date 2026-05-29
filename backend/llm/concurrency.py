"""Global LLM call concurrency limiting (Redis distributed semaphore + local fallback)."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ..config import (
    LLM_MAX_CONCURRENT,
    LLM_SLOT_LEASE_S,
    LLM_SLOT_WAIT_TIMEOUT_S,
    REDIS_URL,
)

logger = logging.getLogger(__name__)

REDIS_SLOT_ZSET_KEY = "wittgenstein:llm:global_slots"

# Sync Redis availability probe: avoid hammering Redis when down; re-check periodically when up.
_REDIS_PROBE_OK_TTL_S = 300
_REDIS_PROBE_FAIL_TTL_S = 30

# While holding a slot, bump ZSET score to now+LLM_SLOT_LEASE_S this often (long LLM calls).
LLM_SLOT_RENEW_INTERVAL_S = 60.0

# Atomic: prune expired holders, grant slot if under cap (score = expiry unix time).
_ACQUIRE_LUA = """
local key = KEYS[1]
local maxc = tonumber(ARGV[1])
local lease = tonumber(ARGV[2])
local token = ARGV[3]
local t = redis.call('TIME')
local now = tonumber(t[1])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
local n = redis.call('ZCARD', key)
if n < maxc then
  redis.call('ZADD', key, now + lease, token)
  return 1
end
return 0
"""

_RELEASE_LUA = """
return redis.call('ZREM', KEYS[1], ARGV[1])
"""

# Atomically extend lease for an existing holder (ignores missing token so we never ZADD a stray member).
_RENEW_LUA = """
local key = KEYS[1]
local lease = tonumber(ARGV[1])
local token = ARGV[2]
if not redis.call('ZSCORE', key, token) then
  return 0
end
local t = redis.call('TIME')
local now = tonumber(t[1])
redis.call('ZADD', key, now + lease, token)
return 1
"""

_local_sem: asyncio.Semaphore | None = None
_redis_client: object | None = None
_redis_scripts: tuple[object, object, object] | None = None
# (last probe result, monotonic time when this cache entry expires and we re-probe).
_redis_probe_cached: tuple[bool, float] | None = None


def _redis_available() -> bool:
    """Return True if Redis should be used for slot coordination (sync probe, cached).

    Tests may patch this to force the in-process asyncio.Semaphore path.
    """
    global _redis_probe_cached
    if LLM_MAX_CONCURRENT <= 0:
        return False
    now_m = time.monotonic()
    if _redis_probe_cached is not None:
        usable, until = _redis_probe_cached
        if now_m < until:
            return usable
    try:
        import redis as redis_sync

        r = redis_sync.Redis.from_url(
            REDIS_URL,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        try:
            r.ping()
        finally:
            r.close()
        _redis_probe_cached = (True, time.monotonic() + _REDIS_PROBE_OK_TTL_S)
    except Exception:
        logger.info("Redis unavailable for LLM slots; using local semaphore", exc_info=False)
        _redis_probe_cached = (False, time.monotonic() + _REDIS_PROBE_FAIL_TTL_S)
    return _redis_probe_cached[0]


def _get_local_semaphore() -> asyncio.Semaphore:
    global _local_sem
    if _local_sem is None:
        _local_sem = asyncio.Semaphore(LLM_MAX_CONCURRENT)
    return _local_sem


async def _get_redis_async():
    global _redis_client, _redis_scripts
    import redis.asyncio as aioredis

    if _redis_client is None:
        _redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
        _redis_scripts = None
    if _redis_scripts is None:
        acq = _redis_client.register_script(_ACQUIRE_LUA)
        rel = _redis_client.register_script(_RELEASE_LUA)
        ren = _redis_client.register_script(_RENEW_LUA)
        _redis_scripts = (acq, rel, ren)
    return _redis_client, _redis_scripts


async def _try_acquire_redis_once(token: str) -> bool:
    _, (acq_script, _, _) = await _get_redis_async()
    ok = await acq_script(
        keys=[REDIS_SLOT_ZSET_KEY],
        args=[str(LLM_MAX_CONCURRENT), str(float(LLM_SLOT_LEASE_S)), token],
    )
    return bool(ok)


async def _release_redis(token: str) -> None:
    _, (_, rel_script, _) = await _get_redis_async()
    await rel_script(keys=[REDIS_SLOT_ZSET_KEY], args=[token])


async def _renew_redis_lease(token: str) -> None:
    _, (_, _, ren_script) = await _get_redis_async()
    await ren_script(
        keys=[REDIS_SLOT_ZSET_KEY],
        args=[str(float(LLM_SLOT_LEASE_S)), token],
    )


async def _renew_lease_loop(token: str) -> None:
    try:
        while True:
            await asyncio.sleep(LLM_SLOT_RENEW_INTERVAL_S)
            try:
                await _renew_redis_lease(token)
            except Exception:
                logger.warning(
                    "Failed to renew LLM Redis slot lease token=%s",
                    token,
                    exc_info=True,
                )
    except asyncio.CancelledError:
        pass


def reset_concurrency_state_for_tests() -> None:
    """Reset module singletons (unit tests)."""
    global _local_sem, _redis_client, _redis_scripts, _redis_probe_cached
    _local_sem = None
    _redis_scripts = None
    _redis_probe_cached = None
    _redis_client = None


@asynccontextmanager
async def llm_slot(*, purpose: str = "") -> AsyncIterator[None]:
    """Limit concurrent LLM calls process- or cluster-wide (when Redis is up)."""
    if LLM_MAX_CONCURRENT <= 0:
        yield
        return

    if _redis_available():
        loop = asyncio.get_running_loop()
        deadline = loop.time() + LLM_SLOT_WAIT_TIMEOUT_S
        token = str(uuid.uuid4())
        try:
            while loop.time() < deadline:
                if await _try_acquire_redis_once(token):
                    renew_task = asyncio.create_task(_renew_lease_loop(token))
                    try:
                        yield
                    finally:
                        renew_task.cancel()
                        await asyncio.gather(renew_task, return_exceptions=True)
                        try:
                            await _release_redis(token)
                        except Exception:
                            logger.warning(
                                "Failed to release LLM Redis slot token=%s purpose=%r",
                                token,
                                purpose,
                                exc_info=True,
                            )
                    return
                await asyncio.sleep(0.05)
        except Exception:
            logger.warning(
                "Redis LLM slot error; purpose=%r — propagating",
                purpose,
                exc_info=True,
            )
            raise
        raise TimeoutError(
            f"LLM slot wait timeout ({LLM_SLOT_WAIT_TIMEOUT_S}s) purpose={purpose!r}"
        )

    sem = _get_local_semaphore()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=LLM_SLOT_WAIT_TIMEOUT_S)
    except asyncio.TimeoutError as e:
        raise TimeoutError(
            f"LLM slot wait timeout ({LLM_SLOT_WAIT_TIMEOUT_S}s) purpose={purpose!r}"
        ) from e
    try:
        yield
    finally:
        sem.release()
