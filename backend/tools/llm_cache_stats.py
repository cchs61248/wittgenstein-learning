# TODO(pg): 此離線運維腳本待改 asyncpg
"""CLI for LLM cache stats and eviction."""
import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.config import DB_PATH
from backend.db.database import init_db, close_db
from backend.memory import llm_cache


async def _stats(content_hash: str | None, db_path: Path) -> None:
    await init_db(str(db_path))
    try:
        if content_hash:
            stats = await llm_cache.stats_by_content_hash(content_hash)
            print(f"content_hash={content_hash}  entries={stats['entries']}  hits={stats['total_hits']}")
        else:
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(hit_count),0) FROM llm_result_cache"
            ).fetchone()
            conn.close()
            print(f"total entries={row[0]}  total hits={row[1]}")
    finally:
        await close_db()


async def _evict(days: int, db_path: Path) -> None:
    await init_db(str(db_path))
    try:
        n = await llm_cache.evict_older_than(days)
        print(f"evicted {n} entries older than {days} days")
    finally:
        await close_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLM cache maintenance")
    parser.add_argument("--content-hash", default=None)
    parser.add_argument("--evict-days", type=int, default=None)
    parser.add_argument("--db", type=Path, default=Path(DB_PATH))
    args = parser.parse_args()
    if args.evict_days is not None:
        asyncio.run(_evict(args.evict_days, args.db))
    else:
        asyncio.run(_stats(args.content_hash, args.db))
