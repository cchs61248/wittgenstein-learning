"""Manually resume a generating curriculum session from checkpoint."""
import argparse
import asyncio
import os
import sys
from pathlib import Path

# Allow running as script from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.config import DATABASE_URL, DEFAULT_PROVIDER
from backend.db.database import init_db, close_db
from backend.llm.provider_factory import create_provider
from backend.orchestrator.curriculum_resume import resume_generating_session_background


async def _main(session_id: str, dsn: str) -> None:
    await init_db(dsn)
    try:
        await resume_generating_session_background(
            session_id,
            lambda provider, model: create_provider(
                provider or DEFAULT_PROVIDER, model=model,
            ),
        )
        print(f"resume triggered for {session_id}")
    finally:
        await close_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Resume curriculum pipeline from checkpoint")
    parser.add_argument("session_id")
    parser.add_argument(
        "--dsn",
        default=os.getenv("DATABASE_URL", DATABASE_URL),
        help="PostgreSQL DSN (default: $DATABASE_URL or config)",
    )
    args = parser.parse_args()
    asyncio.run(_main(args.session_id, args.dsn))
