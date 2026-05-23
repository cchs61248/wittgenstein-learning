"""Deterministic helpers for small-file curriculum (≤ N chunks).

Targets API Design.pdf-style inputs: few chunks, multiple named cases,
without redundant post-process stages or orphan chunks.
"""
from __future__ import annotations

import os
import re
from typing import Any

from .fuzzy_match import similarity

DEFAULT_SMALL_FILE_CHUNK_THRESHOLD = 50
DEFAULT_SMALL_FILE_TEXT_CHARS = 12_000
CASE_MATCH_THRESHOLD = 0.72
_PAREN_SUFFIX_RE = re.compile(r"\s*[\(（].*?[\)）]\s*$")
_COMPOUND_SPLIT_RE = re.compile(r"[與和、/及／]")
_TOPIC_ALIASES: dict[str, list[str]] = {
    "信用貸款": ["信用貸款", "信貸"],
    "信貸": ["信用貸款", "信貸"],
    "房屋貸款": ["房屋貸款", "房貸"],
    "房貸": ["房屋貸款", "房貸"],
    "無本分期": ["無本分期", "零支付", "零支付手法"],
    "零支付": ["零支付", "零支付手法", "無本分期"],
}


def _topic_tokens(topic: str) -> list[str]:
    t = str(topic).strip()
    if not t:
        return []
    tokens = [t]
    for key, aliases in _TOPIC_ALIASES.items():
        if t == key or t in aliases:
            for alias in aliases:
                if alias not in tokens:
                    tokens.append(alias)
            if key not in tokens:
                tokens.append(key)
    return tokens


def _chinese_compound_suffixes(name: str) -> list[str]:
    parts = [p.strip() for p in _COMPOUND_SPLIT_RE.split(name) if p.strip()]
    if len(parts) < 2:
        return []
    suffixes: list[str] = []
    for part in parts:
        if len(part) >= 2:
            suffixes.append(part[-2:])
    return suffixes


def _compound_name_covered(case_name: str, haystack: str) -> bool:
    """「粉絲小雅與作家小蝶」可被「小雅與小蝶」覆蓋。"""
    suffixes = _chinese_compound_suffixes(case_name)
    return len(suffixes) >= 2 and all(s in haystack for s in suffixes)


def _compound_topic_part_covered(part: str, stages: list[dict]) -> bool:
    part = part.strip()
    if not part:
        return True
    if topic_covered_in_stages(part, stages):
        return True
    loan_terms = ("信貸", "信用貸款", "房貸", "房屋貸款", "股票質押")
    pay_terms = ("無本分期", "零支付", "零支付手法")
    has_loan = any(t in part for t in loan_terms)
    has_pay = any(t in part for t in pay_terms)
    if has_loan and has_pay:
        loan_ok = any(
            topic_covered_in_stages(t, stages)
            for t in loan_terms
            if t in part or (t in ("信貸", "信用貸款") and "信貸" in part)
            or (t in ("房貸", "房屋貸款") and "房貸" in part)
        )
        pay_ok = topic_covered_in_stages("無本分期", stages)
        return loan_ok and pay_ok
    return False


def _slash_topics_covered(topic: str, stages: list[dict]) -> bool:
    if "/" not in topic and "／" not in topic:
        return False
    parts = [p.strip() for p in re.split(r"[/／]", topic) if p.strip()]
    if len(parts) < 2:
        return False
    return all(_compound_topic_part_covered(part, stages) for part in parts)


def topic_covered_in_stage(
    stage: dict,
    topic: str,
    *,
    threshold: float = CASE_MATCH_THRESHOLD,
) -> bool:
    haystack = _stage_metadata_text(stage)
    for token in _topic_tokens(topic):
        if token in haystack:
            return True
        title = (stage.get("title") or "").strip()
        if similarity(token, title) >= threshold:
            return True
        for concept in stage.get("key_concepts") or []:
            cstr = str(concept)
            if token in cstr or similarity(token, cstr) >= threshold:
                return True
    return False


def topic_covered_in_stages(topic: str, stages: list[dict]) -> bool:
    if _slash_topics_covered(topic, stages):
        return True
    return any(topic_covered_in_stage(s, topic) for s in stages)


def verifier_miss_covered(
    miss: str,
    stages: list[dict],
    source_chunks: list[dict],
    *,
    threshold: float = CASE_MATCH_THRESHOLD,
) -> bool:
    miss_str = str(miss).strip()
    if not miss_str:
        return True
    if case_covered_in_stages(miss_str, stages, source_chunks, threshold=threshold):
        return True
    if topic_covered_in_stages(miss_str, stages):
        return True
    return False


def filter_false_verifier_misses(
    missing_options: list[str],
    stages: list[dict],
    source_chunks: list[dict],
    *,
    threshold: float = CASE_MATCH_THRESHOLD,
) -> list[str]:
    """Filter LLM verifier false positives (topic/case already present in stages)."""
    return [
        str(m)
        for m in missing_options
        if not verifier_miss_covered(str(m), stages, source_chunks, threshold=threshold)
    ]


def normalize_case_name(case_name: str) -> str:
    """「Airbnb Booking (GraphQL/BFF 案例)」→「Airbnb Booking」"""
    s = str(case_name).strip()
    s = _PAREN_SUFFIX_RE.sub("", s).strip()
    return s


def _case_tokens(case_name: str) -> list[str]:
    case_str = str(case_name).strip()
    if not case_str:
        return []
    normalized = normalize_case_name(case_str)
    tokens = [case_str]
    if normalized and normalized not in tokens:
        tokens.append(normalized)
    main = normalized.split("(")[0].strip() if normalized else case_str.split("(")[0].strip()
    if main and main not in tokens:
        tokens.append(main)
    parts = main.split()
    if len(parts) >= 2:
        pair = " ".join(parts[:2])
        if pair not in tokens:
            tokens.append(pair)
    if len(parts) >= 1 and len(parts[0]) >= 5:
        if parts[0] not in tokens:
            tokens.append(parts[0])
    return tokens


def best_chunk_for_case(
    case_name: str,
    chunks_by_id: dict[str, dict],
    *,
    prefer_unclaimed: set[str] | None = None,
    intro_chunk_id: str | None = None,
) -> str | None:
    """Pick chunk whose text best matches case name (not just first hit)."""
    prefer_unclaimed = prefer_unclaimed or set()
    tokens = _case_tokens(case_name)
    best_id: str | None = None
    best_score = 0.0
    for cid, chunk in chunks_by_id.items():
        text = str(chunk.get("text") or "")
        if not text:
            continue
        if intro_chunk_id and cid == intro_chunk_id:
            if not any(token in text for token in tokens):
                continue
        score = 0.0
        for token in tokens:
            if token in text:
                score += 2.0 + text.count(token) * 0.1
        if score <= 0:
            continue
        if cid not in prefer_unclaimed:
            score += 0.25
        if score > best_score:
            best_score = score
            best_id = cid
    return best_id


def small_file_chunk_threshold() -> int:
    raw = os.getenv("SMALL_FILE_CHUNK_THRESHOLD", str(DEFAULT_SMALL_FILE_CHUNK_THRESHOLD))
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_SMALL_FILE_CHUNK_THRESHOLD


def small_file_text_char_threshold() -> int:
    raw = os.getenv("SMALL_FILE_TEXT_CHARS", str(DEFAULT_SMALL_FILE_TEXT_CHARS))
    try:
        return max(1000, int(raw))
    except ValueError:
        return DEFAULT_SMALL_FILE_TEXT_CHARS


def is_small_file(source_chunks: list[dict]) -> bool:
    return len(source_chunks) <= small_file_chunk_threshold()


def chunks_lookup(source_chunks: list[dict]) -> dict[str, dict]:
    return {
        c["chunk_id"]: c
        for c in source_chunks
        if isinstance(c, dict) and c.get("chunk_id")
    }


def _stage_metadata_text(stage: dict) -> str:
    return " ".join([
        str(stage.get("title") or ""),
        " ".join(str(c) for c in stage.get("key_concepts") or []),
        str(stage.get("teaching_goal") or ""),
    ])


def case_covered_in_stage(
    stage: dict,
    case_name: str,
    chunks_by_id: dict[str, dict],
    *,
    threshold: float = CASE_MATCH_THRESHOLD,
) -> bool:
    title = (stage.get("title") or "").strip()
    haystack = _stage_metadata_text(stage)
    for token in _case_tokens(case_name):
        if token in haystack or token in title:
            return True
        if similarity(token, title) >= threshold:
            return True
        for concept in stage.get("key_concepts") or []:
            if similarity(token, str(concept)) >= threshold:
                return True
        if "案例" in title and token in title:
            return True
    if _compound_name_covered(case_name, haystack):
        return True
    if topic_covered_in_stage(stage, case_name, threshold=threshold):
        return True
    if _case_covered_via_case_stage_chunk(title, stage, chunks_by_id, case_name):
        return True
    return False


def _case_covered_via_case_stage_chunk(
    title: str,
    stage: dict,
    chunks_by_id: dict[str, dict],
    case_name: str,
) -> bool:
    """案例 stage 的 chunk 原文含 case 英文名，且標題/key_concepts 指向同一主題。"""
    if "案例" not in title and "實務" not in title:
        return False
    main = normalize_case_name(case_name)
    words = [w for w in main.split() if len(w) >= 4]
    if not words and len(main) >= 4:
        words = [main]
    if not words:
        return False
    meta = title + " " + " ".join(str(c) for c in stage.get("key_concepts") or [])
    for cid in stage.get("source_chunk_ids") or []:
        text = str(chunks_by_id.get(cid, {}).get("text") or "")
        if not any(w in text for w in words):
            continue
        if any(w in title for w in words):
            return True
        if "Limiter" in text and "限流" in meta:
            return True
        if "BuildMoat" in text and ("BuildMoat" in meta or "素材" in meta):
            return True
    return False


def case_covered_in_stages(
    case_name: str,
    stages: list[dict],
    source_chunks: list[dict],
    *,
    threshold: float = CASE_MATCH_THRESHOLD,
) -> bool:
    by_id = chunks_lookup(source_chunks)
    return any(
        case_covered_in_stage(s, case_name, by_id, threshold=threshold)
        for s in stages
    )


def filter_missing_named_cases(
    named_cases: list[str],
    stages: list[dict],
    source_chunks: list[dict],
    *,
    threshold: float = CASE_MATCH_THRESHOLD,
) -> list[str]:
    return [
        str(case)
        for case in named_cases
        if not case_covered_in_stages(str(case), stages, source_chunks, threshold=threshold)
    ]


def zero_region_overlaps(regions: list[dict]) -> None:
    """Single-region small files do not need neighbor overlap context."""
    if len(regions) != 1:
        return
    region = regions[0]
    region["overlap_before"] = 0
    region["overlap_after"] = 0


_INTRO_TITLE_RE = re.compile(
    r"框架|選型|導論|概述|總覽|introduction|overview|framework",
    re.IGNORECASE,
)


def prune_intro_chunk_sharing(
    stages: list[dict],
    source_chunks: list[dict],
) -> list[dict]:
    """Keep the first chunk only on the intro/framework stage when over-shared."""
    if len(stages) < 3 or not source_chunks:
        return stages

    ordered = sorted(source_chunks, key=lambda c: c.get("order_index", 0))
    intro_chunk_id = ordered[0].get("chunk_id")
    if not intro_chunk_id:
        return stages

    referrers = [
        i
        for i, s in enumerate(stages)
        if intro_chunk_id in (s.get("source_chunk_ids") or [])
    ]
    if len(referrers) <= 1:
        return stages

    keeper_idx = referrers[0]
    for i in referrers:
        title = stages[i].get("title") or ""
        if _INTRO_TITLE_RE.search(title):
            keeper_idx = i
            break

    by_id = chunks_lookup(source_chunks)
    out: list[dict] = []
    for i, stage in enumerate(stages):
        s = dict(stage)
        ids = list(s.get("source_chunk_ids") or [])
        if intro_chunk_id in ids and i != keeper_idx:
            ids = [cid for cid in ids if cid != intro_chunk_id]
        s["source_chunk_ids"] = ids
        s["source_chunks"] = [
            {
                "chunk_id": cid,
                "quote": by_id[cid].get("text") or "",
                "note": by_id[cid].get("source_id") or "",
            }
            for cid in ids
            if cid in by_id
        ]
        out.append(s)
    return out


_SUMMARY_HINTS = ("面試", "checklist", "本章", "總結", "重點", "話術", "應答")


def _chunk_order_index(chunk_id: str, source_chunks: list[dict]) -> int:
    for c in source_chunks:
        if c.get("chunk_id") == chunk_id:
            return int(c.get("order_index", 0))
    return 0


def _attach_chunk_to_stage(stage: dict, chunk_id: str, by_id: dict[str, dict]) -> dict:
    s = dict(stage)
    ids = list(s.get("source_chunk_ids") or [])
    if chunk_id not in ids:
        ids.append(chunk_id)
    ids.sort(key=lambda cid: int(by_id.get(cid, {}).get("order_index", 0)))
    s["source_chunk_ids"] = ids
    s["source_chunks"] = [
        {
            "chunk_id": cid,
            "quote": by_id[cid].get("text") or "",
            "note": by_id[cid].get("source_id") or "",
        }
        for cid in ids
        if cid in by_id
    ]
    return s


def ensure_orphan_chunks_attached(
    stages: list[dict],
    source_chunks: list[dict],
) -> list[dict]:
    """Attach unreferenced chunks without duplicating named-case stages."""
    if not stages:
        return stages

    referenced: set[str] = set()
    for s in stages:
        referenced.update(s.get("source_chunk_ids") or [])

    all_ids = [
        c["chunk_id"]
        for c in sorted(source_chunks, key=lambda x: x.get("order_index", 0))
        if c.get("chunk_id")
    ]
    orphans = [cid for cid in all_ids if cid not in referenced]
    if not orphans:
        return stages

    by_id = chunks_lookup(source_chunks)
    out = [dict(s) for s in stages]

    if len(orphans) == 1:
        cid = orphans[0]
        text = str(by_id.get(cid, {}).get("text") or "")
        if any(h.lower() in text.lower() for h in _SUMMARY_HINTS):
            last = dict(out[-1])
            out[-1] = _attach_chunk_to_stage(last, cid, by_id)
            if "總結" not in (out[-1].get("title") or "") and "面試" not in (out[-1].get("title") or ""):
                out[-1]["title"] = "面試應答與總結"
                out[-1].setdefault("key_concepts", [])
                for kw in ("面試", "總結"):
                    if kw not in out[-1]["key_concepts"]:
                        out[-1]["key_concepts"].append(kw)
            return out

    if len(orphans) > 3:
        for oid in orphans:
            oidx = _chunk_order_index(oid, source_chunks)
            best_i = 0
            best_dist = 10**9
            for i, stage in enumerate(out):
                ids = stage.get("source_chunk_ids") or []
                if not ids:
                    continue
                anchor = min(_chunk_order_index(cid, source_chunks) for cid in ids)
                dist = abs(oidx - anchor)
                if dist < best_dist:
                    best_dist = dist
                    best_i = i
            out[best_i] = _attach_chunk_to_stage(out[best_i], oid, by_id)
        return out

    next_stage_id = max((s.get("stage_id") or 0) for s in out) + 1
    last_node = out[-1].get("node_id", "1.1")
    try:
        chapter = int(str(last_node).split(".")[0]) + 1
    except ValueError:
        chapter = len(out) + 1
    orphan_meta = [
        {
            "chunk_id": cid,
            "quote": by_id[cid].get("text") or "",
            "note": by_id[cid].get("source_id") or "",
        }
        for cid in orphans
        if cid in by_id
    ]
    out.append({
        "stage_id": next_stage_id,
        "node_id": f"{chapter}.1",
        "title": "章節總結與補充內容",
        "key_concepts": ["章節總結", "補充內容"],
        "source_chunk_ids": orphans,
        "source_chunks": orphan_meta,
        "prerequisites": [],
        "estimated_questions": 2,
        "teaching_goal": "補充：未被前面節點覆蓋的章節總結、面試話術與重點整理",
        "kind": "follow_up_orphan",
    })
    return out


def reassign_case_stage_chunks(
    stages: list[dict],
    source_chunks: list[dict],
) -> list[dict]:
    """Reassign each 案例 stage to the chunk that actually contains its case name."""
    by_id = chunks_lookup(source_chunks)
    ordered = sorted(source_chunks, key=lambda c: c.get("order_index", 0))
    intro_cid = ordered[0].get("chunk_id") if ordered else None
    out: list[dict] = []
    for stage in stages:
        s = dict(stage)
        title = s.get("title") or ""
        if "案例" not in title and s.get("kind") != "follow_up_case":
            out.append(s)
            continue
        # Extract case hint from title after 案例
        case_hint = title
        for prefix in ("案例實務：", "案例：", "案例實務:", "案例:"):
            if prefix in title:
                case_hint = title.split(prefix, 1)[-1].strip()
                break
        case_hint = normalize_case_name(case_hint)
        cid = best_chunk_for_case(case_hint, by_id, intro_chunk_id=intro_cid)
        if cid:
            s["source_chunk_ids"] = [cid]
            s["source_chunks"] = [{
                "chunk_id": cid,
                "quote": by_id[cid].get("text") or "",
                "note": by_id[cid].get("source_id") or "",
            }]
        out.append(s)
    return out


def trim_intro_stage_first_chunk_only(
    stages: list[dict],
    source_chunks: list[dict],
) -> list[dict]:
    """Framework/intro stage keeps only the first chunk (chunk_0000)."""
    if not stages or not source_chunks:
        return stages
    ordered = sorted(source_chunks, key=lambda c: c.get("order_index", 0))
    first_cid = ordered[0].get("chunk_id")
    if not first_cid:
        return stages
    by_id = chunks_lookup(source_chunks)
    out: list[dict] = []
    for stage in stages:
        s = dict(stage)
        title = s.get("title") or ""
        if _INTRO_TITLE_RE.search(title) and first_cid in (s.get("source_chunk_ids") or []):
            s["source_chunk_ids"] = [first_cid]
            s["source_chunks"] = [{
                "chunk_id": first_cid,
                "quote": by_id[first_cid].get("text") or "",
                "note": by_id[first_cid].get("source_id") or "",
            }]
        out.append(s)
    return out


def normalize_small_file_stages(
    stages: list[dict],
    source_chunks: list[dict],
) -> list[dict]:
    """Prune overlap + reassign case chunks (safe before global verify)."""
    stages = trim_intro_stage_first_chunk_only(stages, source_chunks)
    stages = prune_intro_chunk_sharing(stages, source_chunks)
    stages = reassign_case_stage_chunks(stages, source_chunks)
    return stages


def finalize_small_file_stages(
    stages: list[dict],
    source_chunks: list[dict],
) -> list[dict]:
    """Full post-compose normalization including orphan attach."""
    stages = normalize_small_file_stages(stages, source_chunks)
    stages = ensure_orphan_chunks_attached(stages, source_chunks)
    return stages


def candidates_to_stages_flat(
    candidates: list[dict],
    chunks_lookup_text: dict[str, str],
) -> list[dict]:
    """Skip reducer LLM — one candidate → one stage."""
    from .curriculum_reducer import outcomes_to_stages

    outcomes = [
        {
            "outcome_id": f"lo_{i + 1:03d}",
            "title": c.get("title", ""),
            "teaching_goal": c.get("teaching_goal", ""),
            "key_concepts": c.get("key_concepts") or [],
            "primary_evidence": {
                "source_id": c.get("source_id", ""),
                "chunk_ids": c.get("source_chunk_ids") or [],
            },
            "supporting_evidence": [],
            "merge_decision": "split",
            "merge_confidence": 1.0,
        }
        for i, c in enumerate(candidates)
    ]
    return outcomes_to_stages(outcomes, chunks_lookup=chunks_lookup_text)
