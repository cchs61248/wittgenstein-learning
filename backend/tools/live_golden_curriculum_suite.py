"""Golden curriculum suite — chunk probe (+ optional live LLM) per content archetype.

Run chunk-only (no API keys):

    .\\backend\\.venv\\Scripts\\python.exe backend\\tools\\live_golden_curriculum_suite.py

Live LLM full V2 (+ optional stage 1):

    $env:RUN_LLM_TESTS="1"
    .\\backend\\.venv\\Scripts\\python.exe backend\\tools\\live_golden_curriculum_suite.py --llm --full-v2

    $env:RUN_LLM_TESTS="1"
    .\\backend\\.venv\\Scripts\\python.exe backend\\tools\\live_golden_curriculum_suite.py --llm --full-v2 --run-stage1

Sessions are always deleted after each --llm run (no --keep). See docs/content_archetypes.md.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / "backend" / ".env")

from backend.tools.golden_curriculum_sources import GOLDEN_SOURCES, GoldenSource, available_sources
from backend.tools.live_small_file_curriculum_test import (
    cleanup_live_sessions,
    probe_source_chunks,
    run_live_curriculum,
    _require_live_llm_opt_in,
)


@dataclass
class RowResult:
    spec: GoldenSource
    path: Path | None
    status: str  # ok | skip | fail
    chunks: int = 0
    titled: int = 0
    toc: int = 0
    stages: int = 0
    global_ok: bool | None = None
    detail: str = ""


def _check_probe(spec: GoldenSource, path: Path) -> RowResult:
    _, probe = probe_source_chunks(path)
    fail_reasons: list[str] = []
    if probe.chunk_count < spec.min_chunks:
        fail_reasons.append(f"chunks {probe.chunk_count} < {spec.min_chunks}")
    if spec.min_section_titles and probe.section_title_count < spec.min_section_titles:
        fail_reasons.append(
            f"titles {probe.section_title_count} < {spec.min_section_titles}",
        )
    if probe.toc_chunk_count > 0:
        fail_reasons.append(f"toc_chunks={probe.toc_chunk_count}")
    status = "fail" if fail_reasons else "ok"
    return RowResult(
        spec=spec,
        path=path,
        status=status,
        chunks=probe.chunk_count,
        titled=probe.section_title_count,
        toc=probe.toc_chunk_count,
        detail="; ".join(fail_reasons),
    )


async def _run_llm_row(spec: GoldenSource, path: Path, *, full_v2: bool, run_stage1: bool) -> RowResult:
    from backend.db.database import init_db, close_db
    from backend.utils.logger import setup_logging

    setup_logging()
    await init_db(str(ROOT / "data" / "learning.db"))
    row = _check_probe(spec, path)
    if row.status == "fail":
        await close_db()
        return row
    try:
        print(f"\n{'=' * 60}\nLLM  {spec.id}  {path.name}\n{'=' * 60}")
        result = await run_live_curriculum(
            path,
            full_v2=full_v2,
            keep=False,
            run_stage1=run_stage1,
        )
        row.stages = result.stage_count
        row.global_ok = result.global_aligned
        if not result.global_aligned:
            row.status = "fail"
            row.detail = (row.detail + "; global_verify=false").strip("; ")
        if run_stage1 and result.question_count < 1:
            row.status = "fail"
            row.detail = (row.detail + "; stage1_no_questions").strip("; ")
    finally:
        await close_db()
    return row


def _print_summary(rows: list[RowResult]) -> None:
    print("\n=== GOLDEN SUITE SUMMARY ===")
    print(f"{'id':<16} {'arch':<18} {'status':<6} chunks titled toc  stages global  detail")
    for r in rows:
        g = "" if r.global_ok is None else ("Y" if r.global_ok else "N")
        st = r.stages or ""
        print(
            f"{r.spec.id:<16} {r.spec.archetype:<18} {r.status:<6} "
            f"{r.chunks:>6} {r.titled:>6} {r.toc:>3}  {st!s:>6} {g:>6}  {r.detail}",
        )
    skipped = [r for r in rows if r.status == "skip"]
    failed = [r for r in rows if r.status == "fail"]
    ok = [r for r in rows if r.status == "ok"]
    print(f"\nok={len(ok)}  skip={len(skipped)}  fail={len(failed)}")
    if skipped:
        print("skipped:", ", ".join(r.spec.id for r in skipped))


async def main(
    *,
    use_llm: bool,
    full_v2: bool,
    run_stage1: bool,
    cleanup_all: bool,
) -> int:
    if cleanup_all:
        from backend.db.database import init_db, close_db
        from backend.utils.logger import setup_logging

        setup_logging()
        await init_db(str(ROOT / "data" / "learning.db"))
        deleted = await cleanup_live_sessions()
        print(f"Cleaned up {len(deleted)} live test session(s)")
        await close_db()
        return 0

    rows: list[RowResult] = []
    avail = {spec.id: (spec, path) for spec, path in available_sources()}
    for spec in GOLDEN_SOURCES:
        if spec.id not in avail:
            rows.append(RowResult(spec=spec, path=None, status="skip", detail="file not found"))
            continue
        _, path = avail[spec.id]
        if use_llm:
            _require_live_llm_opt_in()
            rows.append(await _run_llm_row(spec, path, full_v2=full_v2, run_stage1=run_stage1))
        else:
            rows.append(_check_probe(spec, path))

    _print_summary(rows)
    return 1 if any(r.status == "fail" for r in rows) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Golden curriculum regression suite")
    parser.add_argument("--llm", action="store_true", help="Run live LLM pipeline per book")
    parser.add_argument("--full-v2", action="store_true", help="Force full V2 (with --llm)")
    parser.add_argument("--run-stage1", action="store_true", help="Run stage 1 per book (with --llm)")
    parser.add_argument("--cleanup-all", action="store_true", help="Delete all sess_live_* sessions")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(
        use_llm=args.llm,
        full_v2=args.full_v2,
        run_stage1=args.run_stage1,
        cleanup_all=args.cleanup_all,
    )))
