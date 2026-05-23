"""Live integration: V2 curriculum pipeline with real LLM (manual only).

NOT collected by pytest. Do not import from backend/tests.

Run (requires API keys in backend/.env):

    $env:RUN_LLM_TESTS="1"
    .\\backend\\.venv\\Scripts\\python.exe backend\\tools\\live_small_file_curriculum_test.py [path]

Stage 1 (teacher + MC questions) after start_session:

    .\\backend\\.venv\\Scripts\\python.exe backend\\tools\\live_small_file_curriculum_test.py [path] --full-v2 --run-stage1

Cleanup sess_live_* without LLM (also run after manual verification):

    .\\backend\\.venv\\Scripts\\python.exe backend\\tools\\live_small_file_curriculum_test.py --cleanup-all

Sessions are deleted by default when the run finishes. Use --keep only if you still
need the session in DB for inspect_session.py; delete afterward with --cleanup-all.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import uuid
from dataclasses import dataclass
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


@dataclass
class ChunkProbeResult:
    chunk_count: int
    section_title_count: int
    toc_chunk_count: int
    small_file: bool


@dataclass
class LiveRunResult:
    session_id: str
    chunk_count: int
    stage_count: int
    global_aligned: bool
    explanation_chars: int = 0
    question_count: int = 0
    deleted: bool = True


def probe_source_chunks(source_path: Path) -> tuple[list[dict], ChunkProbeResult]:
    from backend.utils.text_extractor import extract_text
    from backend.utils.chunker import build_source_chunks
    from backend.utils.small_curriculum import (
        is_small_file,
        is_toc_cn_epub_chunk,
        is_toc_listicle_chunk,
    )

    raw_bytes = source_path.read_bytes()
    text = extract_text(source_path.name, raw_bytes)
    source_id = hashlib.sha256(source_path.name.encode()).hexdigest()[:12]
    source_chunks: list[dict] = []
    for i, c in enumerate(build_source_chunks(text)):
        source_chunks.append({
            **c,
            "chunk_id": f"chunk_{i:04d}",
            "order_index": i,
            "source_label": source_path.name,
            "source_index": 0,
            "source_id": source_id,
        })
    toc_n = sum(
        1 for c in source_chunks
        if is_toc_cn_epub_chunk(c) or is_toc_listicle_chunk(c)
    )
    titled = sum(1 for c in source_chunks if (c.get("section_title") or "").strip())
    probe = ChunkProbeResult(
        chunk_count=len(source_chunks),
        section_title_count=titled,
        toc_chunk_count=toc_n,
        small_file=is_small_file(source_chunks),
    )
    return source_chunks, probe


async def run_live_curriculum(
    source_path: Path,
    *,
    full_v2: bool,
    keep: bool,
    run_stage1: bool,
    user_id: str = DEFAULT_USER_ID,
) -> LiveRunResult:
    if full_v2:
        os.environ["SMALL_FILE_CHUNK_THRESHOLD"] = "0"
    source_chunks, probe = probe_source_chunks(source_path)
    print(
        f"file={source_path.name}  chunks={probe.chunk_count}  "
        f"titled={probe.section_title_count}  toc={probe.toc_chunk_count}  "
        f"full_v2={full_v2}",
    )

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

    llm = create_provider(LLMProviderType.MONICA, model="gemini-3-flash")
    orch = LearningOrchestrator(llm)
    session_id = f"{LIVE_SESSION_PREFIX}{uuid.uuid4().hex[:8]}"
    explanation_chars = 0
    question_count = 0
    stage_count = 0
    global_aligned = False

    try:
        await orch.start_session(
            session_id=session_id,
            user_id=user_id,
            source_chunks=source_chunks,
            target_depth="standard",
            question_mode="multiple_choice",
            provider_name="monica",
            model_name="gemini-3-flash",
            emit=emit,
            source_file_ids=[],
        )
        stages = orch._pending_stages or []
        stage_count = len(stages)
        print(f"\n=== STAGES {stage_count} session={session_id} ===")
        for s in stages[:8]:
            kc = s.get("key_concepts") or []
            print(
                f"  {s.get('stage_id')}: {(s.get('title') or '')[:40]}  "
                f"kc={kc[:3]}",
            )
        if stage_count > 8:
            print(f"  ... +{stage_count - 8} more")
        g = verify_global_coverage(stages, source_chunks, None)
        global_aligned = bool(g.get("aligned"))
        print("\n=== GLOBAL VERIFY ===", json.dumps(g, ensure_ascii=False)[:400])

        if run_stage1 and stages:
            print(f"\n=== RUN STAGE 1 session={session_id} ===")

            async def stage_emit(msg: dict) -> None:
                nonlocal explanation_chars
                if msg.get("type") == "explanation_chunk":
                    explanation_chars += len(
                        (msg.get("payload") or {}).get("chunk") or "",
                    )

            await orch.run_stage(
                session_id=session_id,
                user_id=user_id,
                stages=stages,
                stage_index=0,
                question_mode="multiple_choice",
                emit=stage_emit,
            )
            from backend.memory import session_memory
            qs = await session_memory.get_stage_questions(
                session_id, stages[0]["stage_id"],
            )
            question_count = len(qs)
            print(f"explanation_chars={explanation_chars}  questions={question_count}")
    finally:
        deleted = True
        if not keep:
            deleted = await __import__(
                "backend.memory.session_memory", fromlist=["delete_session"],
            ).delete_session(session_id, user_id)
            print(f"\n=== CLEANUP === session={session_id} deleted={deleted}")
        else:
            print(f"\n=== KEEP === session={session_id}")

    return LiveRunResult(
        session_id=session_id,
        chunk_count=probe.chunk_count,
        stage_count=stage_count,
        global_aligned=global_aligned,
        explanation_chars=explanation_chars,
        question_count=question_count,
        deleted=deleted,
    )


def _require_live_llm_opt_in() -> None:
    if os.getenv("RUN_LLM_TESTS") == "1":
        return
    print(
        "Refusing to call live LLM: set RUN_LLM_TESTS=1\n"
        "  PowerShell: $env:RUN_LLM_TESTS=\"1\"\n"
        "  (--cleanup-all does not need this flag)",
        file=sys.stderr,
    )
    sys.exit(2)


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
    run_stage1: bool,
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

    _require_live_llm_opt_in()

    if source_path is None or not source_path.exists():
        print("File missing:", source_path)
        await close_db()
        return

    await init_db(str(ROOT / "data" / "learning.db"))
    try:
        await run_live_curriculum(
            source_path,
            full_v2=full_v2,
            keep=keep,
            run_stage1=run_stage1,
        )
    finally:
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
        "--run-stage1",
        action="store_true",
        help="After start_session, run stage 1 (teacher + MC questions)",
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
        run_stage1=args.run_stage1,
    ))