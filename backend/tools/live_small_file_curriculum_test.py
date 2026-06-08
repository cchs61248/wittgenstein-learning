"""Live integration: V2 curriculum pipeline with real LLM (manual only).

NOT collected by pytest. Do not import from backend/tests.

Run (requires API keys in backend/.env):

    $env:RUN_LLM_TESTS="1"
    .\\backend\\.venv\\Scripts\\python.exe backend\\tools\\live_small_file_curriculum_test.py [path ...]

    Multi-source (same session, mirrors frontend sources[]):

    .\\backend\\.venv\\Scripts\\python.exe backend\\tools\\live_small_file_curriculum_test.py `
      ch1.txt ch2.txt ch3.txt --full-v2 --run-stage1 --keep

    Stage 1 (teacher + MC questions) after start_session:

    .\\backend\\.venv\\Scripts\\python.exe backend\\tools\\live_small_file_curriculum_test.py [path] --full-v2 --run-stage1

Cleanup sess_live_* without LLM (also run after manual verification):

    .\\backend\\.venv\\Scripts\\python.exe backend\\tools\\live_small_file_curriculum_test.py --cleanup-all

In-process 測試會直接寫 data/learning.db。若 Docker worker 在跑，請先：
    docker compose stop curriculum-worker
或改用 Docker worker + live_arq_verify.py（長教材 Arq 模式）。

檢查 worker 狀態：
    .\\backend\\.venv\\Scripts\\python.exe backend\\tools\\check_curriculum_workers.py

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

from backend.config import DATABASE_URL

DSN = os.getenv("DATABASE_URL", DATABASE_URL)

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
    source_count: int
    stage_count: int
    global_aligned: bool
    explanation_chars: int = 0
    question_count: int = 0
    deleted: bool = True


def _chunk_probe(source_chunks: list[dict]) -> ChunkProbeResult:
    from backend.utils.small_curriculum import (
        is_small_file,
        is_toc_cn_epub_chunk,
        is_toc_listicle_chunk,
    )

    toc_n = sum(
        1 for c in source_chunks
        if is_toc_cn_epub_chunk(c) or is_toc_listicle_chunk(c)
    )
    titled = sum(1 for c in source_chunks if (c.get("section_title") or "").strip())
    return ChunkProbeResult(
        chunk_count=len(source_chunks),
        section_title_count=titled,
        toc_chunk_count=toc_n,
        small_file=is_small_file(source_chunks),
    )


def probe_source_chunks(source_path: Path) -> tuple[list[dict], ChunkProbeResult]:
    chunks, _ = probe_multi_source_chunks([source_path])
    return chunks, _chunk_probe(chunks)


def probe_multi_source_chunks(
    source_paths: list[Path],
) -> tuple[list[dict], list[dict]]:
    """Merge multiple files into one chunk list (same logic as main._build_source_chunks)."""
    from backend.utils.text_extractor import extract_text
    from backend.utils.chunker import build_source_chunks

    if not source_paths:
        return [], []

    all_chunks: list[dict] = []
    per_source: list[dict] = []
    global_offset = 0

    for idx, source_path in enumerate(source_paths):
        label = source_path.name
        source_id = hashlib.sha256(f"{label}:{idx}".encode()).hexdigest()[:12]
        raw_bytes = source_path.read_bytes()
        text = extract_text(source_path.name, raw_bytes)
        chunks = build_source_chunks(text)
        n = 0
        for c in chunks:
            all_chunks.append({
                **c,
                "chunk_id": f"chunk_{global_offset:04d}",
                "order_index": global_offset,
                "source_label": label,
                "source_index": idx,
                "source_id": source_id,
            })
            global_offset += 1
            n += 1
        per_source.append({"label": label, "index": idx, "chunks": n})

    return all_chunks, per_source


def _env_file_value(key: str) -> str | None:
    if not _env_path.exists():
        return None
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(";") or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == key:
            return v.strip()
    return None


async def run_live_curriculum(
    source_paths: list[Path],
    *,
    full_v2: bool,
    keep: bool,
    run_stage1: bool,
    user_id: str = DEFAULT_USER_ID,
) -> LiveRunResult:
    source_chunks, per_source = probe_multi_source_chunks(source_paths)
    probe = _chunk_probe(source_chunks)
    print(
        f"sources={len(source_paths)}  chunks={probe.chunk_count}  "
        f"titled={probe.section_title_count}  toc={probe.toc_chunk_count}  "
        f"full_v2={full_v2}",
    )
    for s in per_source:
        print(f"  [{s['index']}] {s['label'][:60]}  chunks={s['chunks']}")

    events: list[dict] = []

    async def emit(msg: dict) -> None:
        events.append(msg)
        t = msg.get("type")
        if t in ("region_done", "reduce_done", "composer_done", "session_generating"):
            print("EVENT", t, json.dumps(msg.get("payload", {}), ensure_ascii=False)[:300])

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
        source_count=len(source_paths),
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
    source_paths: list[Path],
    *,
    full_v2: bool,
    keep: bool,
    cleanup_all: bool,
    run_stage1: bool,
    force_in_process: bool,
) -> None:
    from backend.db.database import init_db, close_db
    from backend.utils.logger import setup_logging
    from backend.tools.curriculum_worker_guard import DbContentionError, assert_no_db_contention

    setup_logging()
    await init_db(DSN)

    if cleanup_all:
        deleted = await cleanup_live_sessions()
        print(f"Cleaned up {len(deleted)} live test session(s)")
        for sid in deleted:
            print(f"  deleted {sid}")
        await close_db()
        return

    _require_live_llm_opt_in()

    if not cleanup_all:
        try:
            assert_no_db_contention(allow_in_process=force_in_process)
        except DbContentionError as e:
            print(e, file=sys.stderr)
            await close_db()
            return

    missing = [p for p in source_paths if not p.exists()]
    if missing:
        for p in missing:
            print("File missing:", p)
        await close_db()
        return

    try:
        await run_live_curriculum(
            source_paths,
            full_v2=full_v2,
            keep=keep,
            run_stage1=run_stage1,
        )
    finally:
        await close_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live V2 curriculum test")
    parser.add_argument(
        "sources",
        nargs="*",
        default=[str(DEFAULT_PDF)],
        help="One or more paths (PDF/txt/epub); multi-path = same session",
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
    parser.add_argument(
        "--force-in-process",
        action="store_true",
        help="Skip worker contention check (stop Docker/local arq first)",
    )
    args = parser.parse_args()
    paths = sorted(Path(p) for p in args.sources)
    asyncio.run(main(
        paths,
        full_v2=args.full_v2,
        keep=args.keep,
        cleanup_all=args.cleanup_all,
        run_stage1=args.run_stage1,
        force_in_process=args.force_in_process,
    ))