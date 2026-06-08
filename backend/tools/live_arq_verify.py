"""Live verification: 155-chunk curriculum via Arq worker.

Usage (from wittgenstein-learning/):
  Terminal 1 — Docker worker（唯一 DB writer）:
    docker compose up -d
    docker compose logs -f curriculum-worker

  Terminal 2 — prepare + enqueue + monitor（本機只寫 enqueue，不跑 in-process pipeline）:
    ..\\.venv\\Scripts\\python.exe backend/tools/check_curriculum_workers.py
    ..\\.venv\\Scripts\\python.exe backend/tools/live_arq_verify.py

  API（CURRICULUM_USE_ARQ=1）可另開 uvicorn；勿同時跑本機 python -m arq。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.config import DATABASE_URL
from backend.db.database import get_db

SESSION_ID = os.getenv("LIVE_VERIFY_SESSION", "sess_l8gco0mn6")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6380/0")
LIVE_PROVIDER = os.getenv("LIVE_VERIFY_PROVIDER", "monica")
LIVE_MODEL = os.getenv("LIVE_VERIFY_MODEL", "gpt-5.5")
POLL_S = int(os.getenv("LIVE_VERIFY_POLL_S", "30"))
MAX_WAIT_S = int(os.getenv("LIVE_VERIFY_MAX_WAIT_S", "600"))  # 10 min sample

DSN = os.getenv("DATABASE_URL", DATABASE_URL)


async def _prepare_session() -> dict:
    db = await get_db()
    row = await db.fetchrow(
        "SELECT * FROM sessions WHERE session_id = $1", SESSION_ID,
    )
    if not row:
        raise SystemExit(f"Session {SESSION_ID} not found")
    n_chunks = await db.fetchval(
        "SELECT COUNT(*) FROM source_chunks WHERE session_id = $1", SESSION_ID,
    )
    if n_chunks < 100:
        raise SystemExit(f"Session {SESSION_ID} has only {n_chunks} chunks (need ~155)")

    provider = (
        os.getenv("LIVE_VERIFY_PROVIDER")
        or row["provider_name"]
        or os.getenv("DEFAULT_PROVIDER", "monica")
    )
    model = os.getenv("LIVE_VERIFY_MODEL") or row["model_name"] or LIVE_MODEL
    meta = {
        "user_id": row["user_id"],
        "target_depth": row["target_depth"] or "intermediate",
        "question_mode": row["question_mode"] or "short_answer",
        "provider_name": provider.lower(),
        "model_name": model,
    }
    # UPDATE session + upsert checkpoint 原子化（交易內一律用同一個 conn）
    async with db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """UPDATE sessions SET status = 'generating', title = '生成中…（live verify）',
                   provider_name = $1, model_name = $2
                   WHERE session_id = $3""",
                provider.lower(), model, SESSION_ID,
            )
            await conn.execute(
                """INSERT INTO curriculum_checkpoints
                   (session_id, content_hash, pipeline_meta_json, completed_region_ids_json,
                    all_candidates_json, summary_parts_json, meter_json)
                   VALUES ($1, $2, $3, '[]', '[]', '[]', '{}')
                   ON CONFLICT (session_id) DO UPDATE SET
                    content_hash = EXCLUDED.content_hash,
                    pipeline_meta_json = EXCLUDED.pipeline_meta_json,
                    completed_region_ids_json = '[]',
                    all_candidates_json = '[]',
                    summary_parts_json = '[]',
                    updated_at = now()""",
                SESSION_ID, row["content_hash"], json.dumps(meta, ensure_ascii=False),
            )
    return {
        "session_id": SESSION_ID,
        "chunks": n_chunks,
        "content_hash": row["content_hash"],
        "provider": meta["provider_name"],
        "model": meta["model_name"],
        "user_id": row["user_id"],
    }


async def _clear_stale_arq_job(pool, session_id: str) -> None:
    """Remove failed/completed Arq keys so fixed _job_id can be re-enqueued."""
    job_id = f"curriculum:{session_id}"
    deleted = await pool.delete(f"arq:job:{job_id}", f"arq:result:{job_id}")
    if deleted:
        print(f"Cleared stale Arq keys for job_id={job_id}  deleted={deleted}", flush=True)


async def _enqueue() -> str | None:
    from arq import create_pool
    from arq.connections import RedisSettings
    from backend.db.inflight_lock import release
    from backend.jobs.enqueue import enqueue_curriculum_job, inflight_key

    # 清掉上次失敗 job 可能殘留的 lock（DB pool 已由 main 初始化）
    await release(inflight_key(SESSION_ID))
    pool = await create_pool(RedisSettings.from_dsn(REDIS_URL))
    try:
        await _clear_stale_arq_job(pool, SESSION_ID)
        return await enqueue_curriculum_job(pool, SESSION_ID)
    finally:
        await pool.aclose()


async def _read_progress() -> dict:
    db = await get_db()
    status = await db.fetchval(
        "SELECT status FROM sessions WHERE session_id = $1", SESSION_ID,
    )
    ckpt = await db.fetchrow(
        "SELECT completed_region_ids_json, regions_json, last_region_id, updated_at "
        "FROM curriculum_checkpoints WHERE session_id = $1",
        SESSION_ID,
    )
    done = 0
    total = 0
    last_rid = None
    updated = None
    if ckpt:
        try:
            done_ids = json.loads(ckpt["completed_region_ids_json"] or "[]")
            done = len(done_ids)
            regions = json.loads(ckpt["regions_json"] or "[]")
            total = len(regions) if regions else 0
        except Exception:
            pass
        last_rid = ckpt["last_region_id"]
        updated = ckpt["updated_at"]
    return {
        "status": status,
        "regions_done": done,
        "regions_total": total,
        "last_region_id": last_rid,
        "checkpoint_updated": updated,
    }


async def _monitor(job_id: str | None) -> None:
    print(
        f"Monitoring session={SESSION_ID}  job_id={job_id}  poll={POLL_S}s  max={MAX_WAIT_S}s",
        flush=True,
    )
    t0 = time.time()
    last_done = -1
    while time.time() - t0 < MAX_WAIT_S:
        prog = await _read_progress()
        status = prog["status"]
        done = prog["regions_done"]
        total = prog["regions_total"]
        if done != last_done:
            print(
                f"[{time.strftime('%H:%M:%S')}] status={status}  "
                f"regions={done}/{total or '?'}  last={prog['last_region_id']}  "
                f"ckpt_updated={prog['checkpoint_updated']}",
                flush=True,
            )
            last_done = done
        if status == "pending_confirmation":
            print("SUCCESS: pipeline completed → pending_confirmation", flush=True)
            return
        if status not in ("generating", None):
            print(f"STOP: unexpected status={status}", flush=True)
            return
        await asyncio.sleep(POLL_S)
    print(
        f"TIMEOUT after {MAX_WAIT_S}s — worker may still be running; check logs/orchestrator.log",
        flush=True,
    )


async def main() -> None:
    from backend.db.database import init_db, close_db
    from backend.tools.curriculum_worker_guard import DbContentionError, assert_docker_worker_ready

    try:
        assert_docker_worker_ready()
    except DbContentionError as e:
        raise SystemExit(str(e)) from e

    await init_db(DSN)
    try:
        info = await _prepare_session()
        print("Prepared session:", json.dumps(info, ensure_ascii=False), flush=True)
        try:
            job_id = await _enqueue()
        except Exception as e:
            raise SystemExit(f"Enqueue failed (is Redis up at {REDIS_URL}?): {e}") from e
        if job_id is None:
            print(
                "Enqueue skipped — job already in flight (inflight lock held). "
                "Worker may already be processing.",
                flush=True,
            )
        else:
            print(f"Enqueued job_id={job_id}", flush=True)
        await _monitor(job_id)
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
