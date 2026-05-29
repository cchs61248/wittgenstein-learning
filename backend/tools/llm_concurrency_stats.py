"""Print current Redis LLM semaphore occupancy."""
import asyncio
import sys
from pathlib import Path

# Repo root (wittgenstein-learning/), same pattern as backend/tools/llm_cache_stats.py
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.config import LLM_MAX_CONCURRENT, REDIS_URL
from backend.llm.concurrency import REDIS_SLOT_ZSET_KEY


async def main() -> None:
    if LLM_MAX_CONCURRENT <= 0:
        print("LLM_MAX_CONCURRENT=0 (limiter disabled)")
        return
    from redis.asyncio import Redis
    import time

    r = Redis.from_url(REDIS_URL, decode_responses=True)
    now = time.time()
    key = REDIS_SLOT_ZSET_KEY
    await r.zremrangebyscore(key, "-inf", now)
    n = await r.zcard(key)
    print(f"LLM slots: {n}/{LLM_MAX_CONCURRENT} in use (redis key={key})")
    await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
