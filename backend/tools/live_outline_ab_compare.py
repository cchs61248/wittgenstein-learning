"""A/B compare: small-file path with vs without SMALL_FILE_FORCE_OUTLINE.

Manual / regression tool (not collected by pytest).

Usage:
    $env:RUN_LLM_TESTS = "1"
    .\\backend\\.venv\\Scripts\\python.exe backend\\tools\\live_outline_ab_compare.py

    .\\backend\\.venv\\Scripts\\python.exe backend\\tools\\live_outline_ab_compare.py --spec api_design
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
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

from backend.tools.golden_curriculum_sources import available_sources
from backend.tools.live_small_file_curriculum_test import (
    DEFAULT_USER_ID,
    LIVE_SESSION_PREFIX,
    _apply_small_file_threshold,
    probe_source_chunks,
)


@dataclass
class AbRow:
    spec_id: str
    label: str
    force_outline: bool
    session_id: str
    chunk_count: int
    stage_count: int
    curriculum_llm_calls: int
    missing_named_cases: int
    global_aligned: bool


async def _run_one(spec_id: str, path: Path, label: str, *, force_outline: bool) -> AbRow:
    _apply_small_file_threshold(full_v2=False)
    os.environ["CURRICULUM_PIPELINE_V2"] = "1"
    os.environ["SMALL_FILE_FORCE_OUTLINE"] = "1" if force_outline else "0"

    source_chunks, probe = probe_source_chunks(path)
    quality_warnings: dict = {}
    session_id = f"{LIVE_SESSION_PREFIX}ab_{uuid.uuid4().hex[:8]}"

    async def emit(msg: dict) -> None:
        if msg.get("type") == "knowledge_map":
            qw = (msg.get("payload") or {}).get("quality_warnings") or {}
            quality_warnings.update(qw)

    from backend.llm.provider_factory import create_provider, LLMProviderType
    from backend.orchestrator.learning_orchestrator import LearningOrchestrator
    from backend.agents.global_curriculum_verifier import verify_global_coverage

    llm = create_provider(LLMProviderType.MONICA, model="gemini-3-flash")
    orch = LearningOrchestrator(llm)
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
        gverify = verify_global_coverage(stages, source_chunks, None)
        missing = gverify.get("missing_options") or []
        print(
            f"{spec_id} outline={'ON' if force_outline else 'OFF'}  "
            f"stages={len(stages)}  llm={quality_warnings.get('curriculum_llm_calls')}  "
            f"missing={len(missing)}  aligned={gverify.get('aligned')}",
        )
        return AbRow(
            spec_id=spec_id,
            label=label,
            force_outline=force_outline,
            session_id=session_id,
            chunk_count=probe.chunk_count,
            stage_count=len(stages),
            curriculum_llm_calls=int(quality_warnings.get("curriculum_llm_calls") or 0),
            missing_named_cases=len(missing),
            global_aligned=bool(gverify.get("aligned")),
        )
    finally:
        from backend.memory import session_memory
        await session_memory.delete_session(session_id, DEFAULT_USER_ID)


async def main_async(spec_ids: list[str] | None) -> None:
    specs = [
        (s, p) for s, p in available_sources()
        if not s.full_v2 and (not spec_ids or s.id in spec_ids)
    ]
    if not specs:
        print("No small-file golden sources found on disk.")
        return

    rows: list[AbRow] = []
    for spec, path in specs[:3]:
        rows.append(await _run_one(spec.id, path, spec.label, force_outline=False))
        rows.append(await _run_one(spec.id, path, spec.label, force_outline=True))

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=[
            "spec_id", "label", "force_outline", "session_id", "chunk_count",
            "stage_count", "curriculum_llm_calls", "missing_named_cases", "global_aligned",
        ],
    )
    writer.writeheader()
    for r in rows:
        writer.writerow({
            "spec_id": r.spec_id,
            "label": r.label,
            "force_outline": r.force_outline,
            "session_id": r.session_id,
            "chunk_count": r.chunk_count,
            "stage_count": r.stage_count,
            "curriculum_llm_calls": r.curriculum_llm_calls,
            "missing_named_cases": r.missing_named_cases,
            "global_aligned": r.global_aligned,
        })

    print("\n=== CSV ===")
    print(buf.getvalue())
    print("=== DECISION (2026-05) ===")
    print(
        "Maintain outline skip for small_file (SMALL_FILE_FORCE_OUTLINE=0). "
        "Rate Limiter + API Design samples: OFF-path global_aligned=true with "
        "2-4 curriculum LLM calls; force outline adds +1 call without consistent "
        "missing_named_cases gain. Revisit only if named_cases coverage <90% on "
        "case-dense handbooks."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Outline skip A/B for small-file path")
    parser.add_argument(
        "--spec", action="append", dest="specs",
        help="Golden spec id (repeatable). Default: first 3 full_v2=False sources.",
    )
    args = parser.parse_args()
    if os.getenv("RUN_LLM_TESTS") != "1":
        print("Set RUN_LLM_TESTS=1 for live LLM runs.", file=sys.stderr)
        sys.exit(2)
    asyncio.run(main_async(args.specs))


if __name__ == "__main__":
    main()
