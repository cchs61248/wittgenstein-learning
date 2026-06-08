import asyncio
import logging
import os
from pathlib import Path

import asyncpg

_log = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None
_SCHEMA_PATH = Path(__file__).parent / "schema.sql"
_init_lock = asyncio.Lock()


async def get_db() -> asyncpg.Pool:
    """回傳目前的 asyncpg pool。

    注意：不要把回傳的 pool 跨 init_db(reset=...) 邊界快取；reset 會關閉並重建 pool，
    舊參考會變成已關閉的 pool。慣例是每次使用前都重新 `await get_db()`。
    """
    if _pool is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _pool


async def _create_pool(dsn: str) -> asyncpg.Pool:
    from ..config import DB_POOL_MIN_SIZE, DB_POOL_MAX_SIZE
    # statement_cache_size=0：避免 DROP SCHEMA/DDL 後 cached plan 失效；
    # 此設定在 production 亦永久生效（停用 prepared-statement 快取），
    # 本服務 DB QPS 低、以 JSON blob 讀寫為主，re-plan 成本可忽略，並順帶相容 pgbouncer。
    return await asyncpg.create_pool(
        dsn,
        min_size=DB_POOL_MIN_SIZE,
        max_size=DB_POOL_MAX_SIZE,
        statement_cache_size=0,
    )


async def _apply_schema(pool: asyncpg.Pool) -> None:
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    async with pool.acquire() as conn:
        await conn.execute(sql)


async def _reset_schema(pool: asyncpg.Pool) -> None:
    """測試用：drop & recreate public schema（owner 用連線當下的 current_user，不硬編）。"""
    async with pool.acquire() as conn:
        await conn.execute(
            "DROP SCHEMA IF EXISTS public CASCADE; "
            "CREATE SCHEMA public; "
            "GRANT ALL ON SCHEMA public TO CURRENT_USER; "
            "GRANT ALL ON SCHEMA public TO public;"
        )


async def init_db(dsn: str, *, reset: bool = False) -> None:
    global _pool

    if reset and os.getenv("WL_TEST_ENV") != "1":
        raise RuntimeError("reset=True is only allowed in test environment (WL_TEST_ENV=1)")

    async with _init_lock:
        if _pool is not None and not reset:
            return  # idempotent

        if reset and _pool is not None:
            await close_db()

        # 啟動 retry/backoff：API 可能比 PG 早起來
        last_exc: Exception | None = None
        for attempt in range(10):
            try:
                _pool = await _create_pool(dsn)
                break
            except (OSError, asyncpg.PostgresError) as exc:
                last_exc = exc
                wait = min(0.5 * (attempt + 1), 5.0)
                _log.warning("init_db connect attempt %d failed (%s); retrying in %.1fs", attempt + 1, exc, wait)
                await asyncio.sleep(wait)
        else:
            _pool = None
            raise RuntimeError(f"Could not connect to PostgreSQL after retries: {last_exc}") from last_exc

        if reset:
            await _reset_schema(_pool)
        await _apply_schema(_pool)


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
