"""Abandon a stuck generating session (marks abandoned, clears source_file_ids_json)."""
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.config import DATABASE_URL
from backend.db.database import init_db, close_db, get_db


async def _run(session_id: str, dsn: str, *, delete_checkpoint: bool) -> None:
    await init_db(dsn)
    try:
        db = await get_db()
        before = await db.fetchval(
            "SELECT status FROM sessions WHERE session_id = $1", session_id
        )
        if before is None:
            print(f"{session_id}: NOT FOUND")
            return
        print(f"{session_id}: before status={before}")
        if delete_checkpoint:
            await db.execute(
                "DELETE FROM curriculum_checkpoints WHERE session_id = $1",
                session_id,
            )
            print(f"{session_id}: checkpoint deleted")
        await db.execute(
            "UPDATE sessions SET status = 'abandoned', source_file_ids_json = '[]' "
            "WHERE session_id = $1 AND status = 'generating'",
            session_id,
        )
        after = await db.fetchval(
            "SELECT status FROM sessions WHERE session_id = $1", session_id
        )
        print(f"{session_id}: after status={after if after is not None else 'NOT FOUND'}")
    finally:
        await close_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Abandon a stuck generating session")
    parser.add_argument("session_id")
    parser.add_argument(
        "--dsn",
        default=os.getenv("DATABASE_URL", DATABASE_URL),
        help="PostgreSQL DSN (default: $DATABASE_URL or config)",
    )
    parser.add_argument(
        "--delete-checkpoint",
        action="store_true",
        help="Also delete curriculum_checkpoints row",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.session_id, args.dsn, delete_checkpoint=args.delete_checkpoint))
