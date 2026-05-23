"""Live integration: V2 curriculum pipeline with Monica gemini-3-flash."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / "backend" / ".env")

_env_path = ROOT / "backend" / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith(";") or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

DEFAULT_PDF = Path(r"C:\Users\dqaiot\Downloads\API Design.pdf")
DEFAULT_USER_ID = "326ba07e-46ae-498d-8935-5085e66ecc9d"
LIVE_SESSION_PREFIX = "sess_live_"


async def cleanup_live_sessions(user_id: str = DEFAULT_USER_ID) -> list[str]:
    """Delete all sess_live_* sessions and related rows for a user."""
    from backend.db.database import get_db
    from backend.memory import session_memory

    db = await get_db()
    async with db.execute(
        "SELECT session_id FROM sessions WHERE user_id = ? AND session_id LIKE ?",
        (user_id, f"{LIVE_SESSION_PREFIX}%"),
    ) as cur:
        rows = await cur.fetchall()
    deleted: list[str] = []
    for row in rows:
        sid = row["session_id"]
        if await session_memory.delete_session(sid, user_id):
            deleted.append(sid)
    return deleted


async def main(
    source_path: Path | None,
    *,
    full_v2: bool,
    keep: bool,
    cleanup_all: bool,
) -> None:
    from backend.db.database import init_db, close_db

    await init_db(str(ROOT / "data" / "learning.db"))

    if cleanup_all:
        deleted = await cleanup_live_sessions()
        print(f"Cleaned up {len(deleted)} live test session(s)")
        for sid in deleted:
            print(f"  deleted {sid}")
        await close_db()
        return

    if source_path is None or not source_path.exists():
        print("File missing:", source_path)
        await close_db()
        return
    if full_v2:
        os.environ["SMALL_FILE_CHUNK_THRESHOLD"] = "0"
    from backend.utils.text_extractor import extract_text
    from backend.utils.chunker import build_source_chunks
    import hashlib

    raw_bytes = source_path.read_bytes()
    text = extract_text(source_path.name, raw_bytes)
    print(f"file={source_path.name}  text_len={len(text)}  full_v2={full_v2}")
    source_id = hashlib.sha256(source_path.name.encode()).hexdigest()[:12]
    source_chunks = []
    for i, c in enumerate(build_source_chunks(text)):
        source_chunks.append({
            **c,
            "chunk_id": f"chunk_{i:04d}",
            "order_index": i,
            "source_label": source_path.name,
            "source_index": 0,
            "source_id": source_id,
        })
    print(f"chunks={len(source_chunks)}  small_file={__import__('backend.utils.small_curriculum', fromlist=['is_small_file']).is_small_file(source_chunks)}")
    events: list[dict] = []

    async def emit(msg: dict) -> None:
        events.append(msg)
        t = msg.get("type")
        if t in ("region_done", "reduce_done", "composer_done", "session_generating"):
            print("EVENT", t, json.dumps(msg.get("payload", {}), ensure_ascii=False)[:300])

    os.environ["CURRICULUM_PIPELINE_V2"] = "1"
    os.environ.setdefault("MACRO_REGION_USE_LLM", "0")

    from backend.llm.provider_factory import create_provider, LLMProviderType
    from backend.orchestrator.learning_orchestrator import LearningOrchestrator
    from backend.agents.global_curriculum_verifier import verify_global_coverage
    from collections import Counter

    llm = create_provider(LLMProviderType.MONICA, model="gemini-3-flash")
    orch = LearningOrchestrator(llm)
    session_id = f"{LIVE_SESSION_PREFIX}{uuid.uuid4().hex[:8]}"

    try:
        await orch.start_session(
            session_id=session_id,
            user_id=DEFAULT_USER_ID,
            source_chunks=source_chunks,
            target_depth="standard",
            question_mode="multiple_choice",
            provider_name="monica",
            model_name="gemini-3-flash",
            emit=emit,
            source_file_ids=[],
        )

        stages = orch._pending_stages or []
        print(f"\n=== STAGES {len(stages)} session={session_id} ===")
        for s in stages:
            kc = s.get("key_concepts") or []
            print(f"  {s.get('stage_id')}: {s.get('title')}  chunks={s.get('source_chunk_ids')}  kc={kc[:4]}")
        g = verify_global_coverage(stages, source_chunks, None)
        print("\n=== GLOBAL VERIFY ===", json.dumps(g, ensure_ascii=False, indent=2))
        refs = Counter(cid for s in stages for cid in (s.get("source_chunk_ids") or []))
        print("=== CHUNK REF COUNTS ===", dict(refs))
    finally:
        if not keep:
            ok = await __import__(
                "backend.memory.session_memory", fromlist=["delete_session"]
            ).delete_session(session_id, DEFAULT_USER_ID)
            print(f"\n=== CLEANUP === session={session_id} deleted={ok}")
        else:
            print(f"\n=== KEEP === session={session_id} (--keep)")
        await close_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live V2 curriculum test")
    parser.add_argument(
        "source",
        nargs="?",
        default=str(DEFAULT_PDF),
        help="Path to PDF/txt/epub extract (default: API Design.pdf)",
    )
    parser.add_argument(
        "--full-v2",
        action="store_true",
        help="Force full V2 path (disable small-file shortcut)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep session in DB after test (default: delete)",
    )
    parser.add_argument(
        "--cleanup-all",
        action="store_true",
        help="Delete all sess_live_* sessions for the test user, then exit",
    )
    args = parser.parse_args()
    asyncio.run(main(
        Path(args.source) if args.source else None,
        full_v2=args.full_v2,
        keep=args.keep,
        cleanup_all=args.cleanup_all,
    ))