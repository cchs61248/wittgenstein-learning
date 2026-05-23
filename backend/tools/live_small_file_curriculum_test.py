"""Live integration: small-file V2 pipeline with Monica gemini-3-flash."""
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


async def main(pdf_path: Path) -> None:
    if not pdf_path.exists():
        print("PDF missing:", pdf_path)
        return
    from backend.utils.text_extractor import extract_text
    from backend.utils.chunker import build_source_chunks
    import hashlib

    pdf_bytes = pdf_path.read_bytes()
    text = extract_text(pdf_path.name, pdf_bytes)
    print(f"PDF={pdf_path.name}  text_len={len(text)}")
    source_id = hashlib.sha256(pdf_path.name.encode()).hexdigest()[:12]
    source_chunks = []
    for i, c in enumerate(build_source_chunks(text)):
        source_chunks.append({
            **c,
            "chunk_id": f"chunk_{i:04d}",
            "order_index": i,
            "source_label": pdf_path.name,
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

    from backend.db.database import init_db, close_db
    from backend.llm.provider_factory import create_provider, LLMProviderType
    from backend.orchestrator.learning_orchestrator import LearningOrchestrator
    from backend.agents.global_curriculum_verifier import verify_global_coverage
    from collections import Counter

    await init_db(str(ROOT / "data" / "learning.db"))

    llm = create_provider(LLMProviderType.MONICA, model="gemini-3-flash")
    orch = LearningOrchestrator(llm)
    session_id = f"sess_live_{uuid.uuid4().hex[:8]}"

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
    finally:
        await close_db()

    stages = orch._pending_stages or []
    print(f"\n=== STAGES {len(stages)} session={session_id} ===")
    for s in stages:
        kc = s.get("key_concepts") or []
        print(f"  {s.get('stage_id')}: {s.get('title')}  chunks={s.get('source_chunk_ids')}  kc={kc[:4]}")
    g = verify_global_coverage(stages, source_chunks, None)
    print("\n=== GLOBAL VERIFY ===", json.dumps(g, ensure_ascii=False, indent=2))
    refs = Counter(cid for s in stages for cid in (s.get("source_chunk_ids") or []))
    print("=== CHUNK REF COUNTS ===", dict(refs))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live small-file curriculum test")
    parser.add_argument(
        "pdf",
        nargs="?",
        default=str(DEFAULT_PDF),
        help="Path to PDF (default: API Design.pdf)",
    )
    args = parser.parse_args()
    asyncio.run(main(Path(args.pdf)))