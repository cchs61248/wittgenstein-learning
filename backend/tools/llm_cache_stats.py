"""CLI for LLM cache stats and eviction."""
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.config import DATABASE_URL
from backend.db.database import init_db, close_db, get_db
from backend.memory import llm_cache


async def _stats(content_hash: str | None, dsn: str) -> None:
    await init_db(dsn)
    try:
        if content_hash:
            stats = await llm_cache.stats_by_content_hash(content_hash)
            print(f"content_hash={content_hash}  entries={stats['entries']}  hits={stats['total_hits']}")
        else:
            db = await get_db()
            row = await db.fetchrow(
                "SELECT COUNT(*) AS entries, COALESCE(SUM(hit_count), 0) AS hits "
                "FROM llm_result_cache"
            )
            print(f"total entries={row['entries']}  total hits={row['hits']}")
    finally:
        await close_db()


async def _evict(days: int, dsn: str) -> None:
    await init_db(dsn)
    try:
        n = await llm_cache.evict_older_than(days)
        print(f"evicted {n} entries older than {days} days")
    finally:
        await close_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLM cache maintenance")
    parser.add_argument("--content-hash", default=None)
    parser.add_argument("--evict-days", type=int, default=None)
    parser.add_argument(
        "--dsn",
        default=os.getenv("DATABASE_URL", DATABASE_URL),
        help="PostgreSQL DSN (default: $DATABASE_URL or config)",
    )
    args = parser.parse_args()
    if args.evict_days is not None:
        asyncio.run(_evict(args.evict_days, args.dsn))
    else:
        asyncio.run(_stats(args.content_hash, args.dsn))
