"""Deterministic helpers for small-file curriculum (≤ N chunks).

Targets API Design.pdf-style inputs: few chunks, multiple named cases,
without redundant post-process stages or orphan chunks.
"""
from __future__ import annotations

import os
import re
import unicodedata
from typing import Any

from .fuzzy_match import similarity

DEFAULT_SMALL_FILE_CHUNK_THRESHOLD = 50
DEFAULT_SMALL_FILE_TEXT_CHARS = 12_000
# ≤50 chunks: run finalize orphan recovery even on forced full V2 path.
COMPACT_FINALIZE_CHUNK_MAX = 50
# ≤30 chunks: global verifier requires zero orphan chunks.
COMPACT_ZERO_ORPHAN_CHUNK_MAX = 30
CASE_MATCH_THRESHOLD = 0.72
DUPLICATE_TITLE_THRESHOLD = 0.92
# Bulk orphan attach: cap per-stage growth so narrative EPUBs don't mash 20+ chunks into one stage.
ORPHAN_BULK_MAX_ATTACH_PER_STAGE = 3
ORPHAN_STAGE_MAX_CHUNKS = 14
STAGE_MAX_KEY_CONCEPTS = 8
# Split mash-up stages that hit kc cap but still pack too many chunks (e.g. 8 kc + 11 chunks).
KC_HEAVY_SPLIT_CHUNK_THRESHOLD = 7
ORPHAN_OVERFLOW_BATCH_SIZE = 6
_PAREN_SUFFIX_RE = re.compile(r"\s*[\(（].*?[\)）]\s*$")
_GRADE_LABEL_RE = re.compile(r"[\(（]?\s*[AB]級[^\)）]*[\)）]?")
_RULE_MISS_RE = re.compile(r"法則\s*(\d+)")
_RULE_RANGE_RE = re.compile(r"法則\s*(\d+)\s*[-–—~～]\s*(\d+)")
_COMPOUND_SPLIT_RE = re.compile(r"[與和、/及／]")
_TOPIC_ALIASES: dict[str, list[str]] = {
    "信用貸款": ["信用貸款", "信貸"],
    "信貸": ["信用貸款", "信貸"],
    "房屋貸款": ["房屋貸款", "房貸"],
    "房貸": ["房屋貸款", "房貸"],
    "無本分期": ["無本分期", "零支付", "零支付手法"],
    "零支付": ["零支付", "零支付手法", "無本分期"],
    "風林火山": ["風林火山", "肥羊波浪", "波浪理論", "蛛網交易"],
    "肥羊派流買法": ["肥羊派流", "肥羊派流買法", "流買法"],
    "股票質押": ["股票質押", "質押"],
    "反向操作": ["反向操作", "逆勢操作", "逆勢操作心法", "逆勢而為"],
    "逆勢操作": ["逆勢操作", "反向操作", "逆勢操作心法", "逆勢而為"],
}
_PAREN_INNER_RE = re.compile(r"[\(（]([^\)）]+)[\)）]")
_CN_ENUM_LABEL_RE = re.compile(r"[（(][一二三四五六七八九十百千万\d]+[）)]")
_COLON_SUFFIX_RE = re.compile(r"^.+?[：:]\s*(.+)$")
_GRADE_MISS_RE = re.compile(r"([SAB])\s*級")
_CASE_PREFIX_RE = re.compile(r"^案例\s*實務?[：:]\s*|^案例[：:]\s*", re.IGNORECASE)
_VS_SPLIT_RE = re.compile(r"\s+vs\s+", re.IGNORECASE)
_ARABIC_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_CN_DIGITS = "零一二三四五六七八九"


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
    for key in _TOPIC_ALIASES:
        if key in t and key not in tokens:
            tokens.extend(x for x in _topic_tokens(key) if x not in tokens)
    return tokens


def _rule_ranges_in_haystack(haystack: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for m in _RULE_RANGE_RE.finditer(haystack):
        a, b = int(m.group(1)), int(m.group(2))
        ranges.append((min(a, b), max(a, b)))
    return ranges


def _rule_number_from_label(label: str) -> int | None:
    m = _RULE_MISS_RE.search(str(label))
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _numbered_rule_covered(case_name: str, haystack: str) -> bool:
    num = _rule_number_from_label(case_name)
    if num is None:
        return False
    for start, end in _rule_ranges_in_haystack(haystack):
        if start <= num <= end:
            return True
    # 法則 1：朋友 / 法則1 / 法則 1　朋友
    normalized = re.sub(r"[　：:\s]+", " ", str(case_name).strip())
    if normalized and normalized in haystack.replace("　", " "):
        return True
    rule_pat = re.compile(rf"法則\s*0*{num}(?:\s|[　：:]|$)")
    return bool(rule_pat.search(haystack))


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


def _vs_parallel_covered(label: str, stages: list[dict]) -> bool:
    """「無風險資產 vs 有風險資產」— 兩側主題皆已在 stages 中。"""
    base = _PAREN_INNER_RE.sub("", label).strip()
    parts = _VS_SPLIT_RE.split(base)
    if len(parts) != 2:
        return False
    return all(_compound_topic_part_covered(p.strip(), stages) for p in parts)


def _及_compound_covered(label: str, stages: list[dict]) -> bool:
    """「高股息及高收益商品」— 任一侧已在 stages 即視為覆蓋。"""
    if "及" not in label:
        return False
    parts = [p.strip() for p in label.split("及") if len(p.strip()) >= 2]
    return len(parts) >= 2 and any(topic_covered_in_stages(p, stages) for p in parts)


def _case_entities_covered(case_name: str, stages: list[dict]) -> bool:
    """「荷蘭皇家石油與殼牌石油」— 與/和 兩側實體皆出現在 stage 即視為覆蓋。"""
    core = _CASE_PREFIX_RE.sub("", str(case_name).strip())
    core = _PAREN_INNER_RE.sub("", core).strip()
    parts = [p.strip() for p in re.split(r"[與和]", core) if len(p.strip()) >= 3]
    if len(parts) < 2:
        return False
    return all(
        any(part in _stage_metadata_text(s) for s in stages)
        for part in parts[:2]
    )


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


def _case_part_covered_globally(
    part: str,
    stages: list[dict],
    chunks_by_id: dict[str, dict],
    *,
    threshold: float = CASE_MATCH_THRESHOLD,
) -> bool:
    """單一 case 子句是否已在任意 stage（title/kc/chunk 原文）出現。"""
    part = part.strip()
    if not part:
        return False
    if topic_covered_in_stages(part, stages):
        return True
    probes: list[str] = list(_topic_tokens(part))
    year_match = _ARABIC_YEAR_RE.search(part)
    if year_match:
        probes.extend(_year_cn_variants(year_match.group(0)))
    for kw in ("崩盤", "逆勢", "反向", "市場"):
        if kw in part and kw not in probes:
            probes.append(kw)
    probes = [p for p in dict.fromkeys(probes) if len(p) >= 2]
    for stage in stages:
        hay = _stage_metadata_text(stage)
        if any(p in hay for p in probes):
            return True
        for concept in stage.get("key_concepts") or []:
            cstr = str(concept)
            for p in probes:
                if similarity(p, cstr) >= threshold:
                    return True
    for stage in stages:
        for cid in stage.get("source_chunk_ids") or []:
            text = str(chunks_by_id.get(cid, {}).get("text") or "")
            if any(p in text for p in probes):
                return True
            if ("逆勢" in part or "反向" in part) and (
                "逆勢" in text or "反向" in text
            ):
                return True
    return False


def _case_parts_covered_globally(
    case_name: str,
    stages: list[dict],
    chunks_by_id: dict[str, dict],
    *,
    threshold: float = CASE_MATCH_THRESHOLD,
) -> bool:
    """「A與B」型 named case — 兩側在任意 stage 合併覆蓋即可（orphan attach 後也適用）。"""
    core = normalize_case_name(case_name)
    parts = [p.strip() for p in re.split(r"[與和]", core) if len(p.strip()) >= 2]
    if len(parts) < 2:
        return False
    return all(
        _case_part_covered_globally(part, stages, chunks_by_id, threshold=threshold)
        for part in parts[:2]
    )


def _paren_enumeration_covered(label: str, stages: list[dict]) -> bool:
    """括號內 a、b、c 列舉 — 各子項已在 stages 中即視為覆蓋。"""
    for m in _PAREN_INNER_RE.finditer(label):
        parts = [
            p.strip()
            for p in re.split(r"[、,，/／]", m.group(1))
            if len(p.strip()) >= 2
        ]
        if len(parts) >= 2 and all(topic_covered_in_stages(p, stages) for p in parts):
            return True
    return False


def _paren_alias_covered(label: str, stages: list[dict]) -> bool:
    """括號內別名（如 風林火山四戰術）出現在 stage 即視為覆蓋。"""
    for m in _PAREN_INNER_RE.finditer(label):
        alias = m.group(1).strip()
        if len(alias) < 3:
            continue
        probes = [alias] + _topic_tokens(alias)
        for probe in probes:
            if len(probe) < 2:
                continue
            if topic_covered_in_stages(probe, stages):
                return True
            for stage in stages:
                hay = _stage_metadata_text(stage)
                if probe in hay or (len(probe) >= 4 and probe[:4] in hay):
                    return True
    return False


def _colon_suffix_covered(label: str, stages: list[dict]) -> bool:
    """「炒股方式 1：一次全買」→ 檢查冒號後子標題。"""
    m = _COLON_SUFFIX_RE.match(label.strip())
    if not m:
        return False
    suffix = m.group(1).strip()
    return len(suffix) >= 2 and topic_covered_in_stages(suffix, stages)


def _grade_bucket_covered(label: str, stages: list[dict]) -> bool:
    """「S級金控（中信金、玉山金…）」— 等級 + 多數具名標的已覆蓋。"""
    gm = _GRADE_MISS_RE.search(label)
    if not gm:
        return False
    grade = gm.group(1)
    grade_ok = any(
        f"{grade}級" in _stage_metadata_text(s)
        or f"{grade} 級" in (s.get("title") or "")
        for s in stages
    )
    if not grade_ok:
        return False
    inner = _PAREN_INNER_RE.search(label)
    if not inner:
        return True
    names = [
        n.strip()
        for n in re.split(r"[、,，]", inner.group(1))
        if len(n.strip()) >= 2
    ]
    if not names:
        return True
    hits = sum(1 for n in names if topic_covered_in_stages(n, stages))
    return hits >= max(1, (len(names) + 1) // 2)


def _miss_decomposition_tokens(miss: str) -> list[str]:
    """拆解 verifier miss label 為可逐一比對的子 token。"""
    miss = str(miss).strip()
    if not miss:
        return []
    tokens: list[str] = []
    for t in _case_tokens(miss):
        if t not in tokens:
            tokens.append(t)
    for t in _topic_tokens(miss):
        if t not in tokens:
            tokens.append(t)
    base = _PAREN_INNER_RE.sub("", miss).strip()
    base = re.sub(r"\s*\d+\s*種.*$", "", base).strip()
    if base and base not in tokens:
        tokens.append(base)
    m = _COLON_SUFFIX_RE.match(miss)
    if m:
        suffix = m.group(1).strip()
        if suffix and suffix not in tokens:
            tokens.append(suffix)
    for m in _PAREN_INNER_RE.finditer(miss):
        for part in re.split(r"[、,，/／]", m.group(1)):
            part = part.strip()
            if len(part) >= 2 and part not in tokens:
                tokens.append(part)
    gm = _GRADE_MISS_RE.search(miss)
    if gm:
        grade = f"{gm.group(1)}級"
        if grade not in tokens:
            tokens.append(grade)
    return tokens


def _enum_label_miss_requires_title_match(miss: str, stages: list[dict]) -> bool:
    """R9: 並列編號 miss 須 stage title 含相同（N）標記，不可僅靠 kc 模糊覆蓋。"""
    m = _CN_ENUM_LABEL_RE.search(str(miss))
    if not m:
        return False
    label = m.group(0)
    return not any(label in (s.get("title") or "") for s in stages)


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
    if _enum_label_miss_requires_title_match(miss_str, stages):
        return False
    if case_covered_in_stages(miss_str, stages, source_chunks, threshold=threshold):
        return True
    if any(_numbered_rule_covered(miss_str, _stage_metadata_text(s)) for s in stages):
        return True
    if topic_covered_in_stages(miss_str, stages):
        return True
    if _vs_parallel_covered(miss_str, stages):
        return True
    if _及_compound_covered(miss_str, stages):
        return True
    if _case_entities_covered(miss_str, stages):
        return True
    if _paren_enumeration_covered(miss_str, stages):
        return True
    if _paren_alias_covered(miss_str, stages):
        return True
    if _colon_suffix_covered(miss_str, stages):
        return True
    if _grade_bucket_covered(miss_str, stages):
        return True
    for token in _miss_decomposition_tokens(miss_str):
        if topic_covered_in_stages(token, stages):
            return True
        if case_covered_in_stages(token, stages, source_chunks, threshold=threshold):
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


def pending_enum_label_misses(missing_options: list[str], stages: list[dict]) -> list[str]:
    """R9 enum gaps that must trigger reroll even when other misses are false positives."""
    return [
        str(m)
        for m in missing_options
        if _enum_label_miss_requires_title_match(str(m), stages)
    ]


def normalize_case_name(case_name: str) -> str:
    """「Airbnb Booking (GraphQL/BFF 案例)」→「Airbnb Booking」"""
    s = str(case_name).strip()
    s = _PAREN_SUFFIX_RE.sub("", s).strip()
    return s


def _year_cn_variants(year_str: str) -> list[str]:
    """「1987」→ 阿拉伯數字 + 中文數字變體（chunk 原文常用「一九八七年」）。"""
    year_str = str(year_str).strip()
    if not year_str.isdigit():
        return [year_str] if year_str else []
    variants = [year_str, f"{year_str}年"]
    cn = "".join(_CN_DIGITS[int(ch)] for ch in year_str if ch.isdigit())
    if cn:
        variants.extend([cn, f"{cn}年"])
    return variants


def _case_year_in_stage_chunks(
    stage: dict,
    case_name: str,
    chunks_by_id: dict[str, dict],
) -> bool:
    """案例 stage 的 chunk 原文含 outline 年份（阿拉伯/中文數字皆可）。"""
    title = (stage.get("title") or "").strip()
    if "案例" not in title and "逆勢" not in title and "操作" not in title:
        return False
    years = _ARABIC_YEAR_RE.findall(str(case_name))
    if not years:
        return False
    probes: list[str] = []
    for year in years:
        probes.extend(_year_cn_variants(year))
    if not probes:
        return False
    for cid in stage.get("source_chunk_ids") or []:
        text = str(chunks_by_id.get(cid, {}).get("text") or "")
        if any(p in text for p in probes):
            return True
    return False


def _case_and_parts_covered_in_stage(
    stage: dict,
    case_name: str,
    chunks_by_id: dict[str, dict],
    *,
    threshold: float = CASE_MATCH_THRESHOLD,
) -> bool:
    """「1987 年市場崩盤與反向操作」— 與/和 兩側分別對齊 title/kc/chunk。"""
    core = normalize_case_name(case_name)
    parts = [p.strip() for p in re.split(r"[與和]", core) if len(p.strip()) >= 2]
    if len(parts) < 2:
        return False

    def _part_ok(part: str) -> bool:
        if topic_covered_in_stage(stage, part, threshold=threshold):
            return True
        if _ARABIC_YEAR_RE.search(part) and _case_year_in_stage_chunks(
            stage, part, chunks_by_id,
        ):
            return True
        return False

    return all(_part_ok(part) for part in parts[:2])


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
    if "的" in case_str:
        for part in case_str.split("的"):
            part = part.strip()
            if len(part) >= 3 and part not in tokens:
                tokens.append(part)
    if _GRADE_LABEL_RE.search(case_str):
        base = _GRADE_LABEL_RE.sub("", case_str).strip()
        if base and base not in tokens:
            tokens.append(base)
        for m in re.finditer(r"([AB])級", case_str):
            grade = f"{m.group(1)}級"
            if grade not in tokens:
                tokens.append(grade)
    for part in re.split(r"[與和]", main):
        part = part.strip()
        if len(part) >= 3 and part not in tokens:
            tokens.append(part)
    exp = re.sub(r"(實驗|案例|研究)$", "", main).strip()
    if len(exp) >= 4 and exp not in tokens:
        tokens.append(exp)
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
        if is_toc_listicle_chunk(chunk) or is_toc_cn_epub_chunk(chunk) or is_epub_nav_junk_chunk(chunk):
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


def source_count(source_chunks: list[dict]) -> int:
    if not source_chunks:
        return 1
    return len({
        c.get("source_id") or f"src_{c.get('source_index', 0)}"
        for c in source_chunks
        if isinstance(c, dict)
    }) or 1


def use_single_split_path(source_chunks: list[dict]) -> bool:
    return is_small_file(source_chunks) and source_count(source_chunks) <= 1


def use_per_source_split_path(source_chunks: list[dict]) -> bool:
    return is_small_file(source_chunks) and source_count(source_chunks) > 1


def is_compact_curriculum(source_chunks: list[dict]) -> bool:
    """Small-file path or short PDFs/epubs that need finalize even when full V2 is forced."""
    return is_small_file(source_chunks) or len(source_chunks) <= COMPACT_FINALIZE_CHUNK_MAX


def compact_orphan_limit(source_chunks: list[dict]) -> int:
    """Max allowed orphan chunks for global coverage verify."""
    n = len(source_chunks)
    if is_small_file(source_chunks) or n <= COMPACT_ZERO_ORPHAN_CHUNK_MAX:
        return 0
    return max(5, n // 20)


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
    if _case_entities_covered(case_name, [stage]):
        return True
    if topic_covered_in_stage(stage, case_name, threshold=threshold):
        return True
    if _case_and_parts_covered_in_stage(
        stage, case_name, chunks_by_id, threshold=threshold,
    ):
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
    probes: list[str] = list(words)
    for part in re.split(r"[與和]", main):
        part = part.strip()
        if len(part) >= 2:
            probes.append(part)
            probes.extend(_topic_tokens(part))
    for year in _ARABIC_YEAR_RE.findall(case_name):
        probes.extend(_year_cn_variants(year))
    probes = [p for p in dict.fromkeys(probes) if len(p) >= 2]
    if not probes:
        return False
    meta = title + " " + " ".join(str(c) for c in stage.get("key_concepts") or [])
    for cid in stage.get("source_chunk_ids") or []:
        text = str(chunks_by_id.get(cid, {}).get("text") or "")
        if not any(p in text for p in probes):
            continue
        if any(p in title for p in probes):
            return True
        for probe in probes:
            if topic_covered_in_stage(stage, probe):
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
    if any(
        case_covered_in_stage(s, case_name, by_id, threshold=threshold)
        for s in stages
    ):
        return True
    return _case_parts_covered_globally(
        case_name, stages, by_id, threshold=threshold,
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
    r"框架|選型|導論|概述|總覽|introduction|overview|framework|"
    r"前言|序言|導言|緒論|投資心法|富人思維|行為財務學|決策偏誤",
    re.IGNORECASE,
)


def _is_intro_framework_stage(stage: dict) -> bool:
    """Intro / framework stage — orphan attach and kc expansion should not bloat these."""
    if (stage.get("kind") or "").strip().lower() in ("framework", "intro"):
        return True
    return bool(_INTRO_TITLE_RE.search(stage.get("title") or ""))


def _normalize_title_for_merge(title: str) -> str:
    t = _CASE_PREFIX_RE.sub("", (title or "").strip())
    t = _PAREN_SUFFIX_RE.sub("", t).strip()
    return " ".join(t.lower().split())


def _renumber_stages(stages: list[dict]) -> list[dict]:
    for j, stage in enumerate(stages):
        stage["stage_id"] = j + 1
        chapter = (j // 3) + 1
        section = (j % 3) + 1
        stage["node_id"] = f"{chapter}.{section}"
    return stages


def _merge_stage_into(target: dict, incoming: dict) -> None:
    existing_ids = set(target.get("source_chunk_ids") or [])
    new_ids = [
        cid for cid in (incoming.get("source_chunk_ids") or [])
        if cid not in existing_ids
    ]
    target["source_chunk_ids"] = list(target.get("source_chunk_ids") or []) + new_ids

    existing_chunk_ids = {
        sc.get("chunk_id") for sc in (target.get("source_chunks") or [])
    }
    target["source_chunks"] = list(target.get("source_chunks") or []) + [
        sc for sc in (incoming.get("source_chunks") or [])
        if sc.get("chunk_id") not in existing_chunk_ids
    ]
    target["key_concepts"] = list(dict.fromkeys(
        list(target.get("key_concepts") or []) +
        list(incoming.get("key_concepts") or [])
    ))
    target["prerequisites"] = list(dict.fromkeys(
        list(target.get("prerequisites") or []) +
        list(incoming.get("prerequisites") or [])
    ))
    target["estimated_questions"] = max(
        int(target.get("estimated_questions", 2) or 2),
        int(incoming.get("estimated_questions", 2) or 2),
    )
    if incoming.get("teaching_goal") and not target.get("teaching_goal"):
        target["teaching_goal"] = incoming["teaching_goal"]


def merge_duplicate_topic_stages(
    stages: list[dict],
    *,
    threshold: float = DUPLICATE_TITLE_THRESHOLD,
) -> list[dict]:
    """合併標題相同或高度相似的 stage（對齊 global verifier duplicate check）。"""
    if len(stages) <= 1:
        return stages

    result: list[dict] = []
    for stage in stages:
        title = (stage.get("title") or "").strip()
        if not title:
            result.append(dict(stage))
            continue

        merged = False
        norm_title = _normalize_title_for_merge(title)
        for existing in result:
            existing_title = (existing.get("title") or "").strip()
            norm_existing = _normalize_title_for_merge(existing_title)
            title_match = (
                existing_title
                and (
                    similarity(title, existing_title) >= threshold
                    or (
                        norm_title
                        and norm_existing
                        and (
                            norm_title == norm_existing
                            or similarity(norm_title, norm_existing) >= threshold
                        )
                    )
                )
            )
            if title_match:
                _merge_stage_into(existing, stage)
                merged = True
                break
        if not merged:
            result.append(dict(stage))
    return _renumber_stages(result)


def merge_empty_chunk_stages(stages: list[dict]) -> list[dict]:
    """Merge stages with no source_chunk_ids into adjacent stage (avoid empty stages)."""
    if not stages:
        return stages
    out: list[dict] = []
    pending: dict | None = None
    for stage in stages:
        s = dict(stage)
        if s.get("source_chunk_ids"):
            if pending:
                _merge_stage_into(s, pending)
                pending = None
            out.append(s)
            continue
        if out:
            _merge_stage_into(out[-1], s)
        else:
            pending = s
    if pending:
        if out:
            _merge_stage_into(out[0], pending)
        else:
            out.append(pending)
    return _renumber_stages(out)


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


def _stage_min_chunk_order(stage: dict, source_chunks: list[dict]) -> int:
    ids = stage.get("source_chunk_ids") or []
    if not ids:
        return 10**9
    return min(_chunk_order_index(cid, source_chunks) for cid in ids)


def sort_stages_by_chunk_order(
    stages: list[dict],
    source_chunks: list[dict],
) -> list[dict]:
    """Deterministic re-sort by earliest assigned chunk order_index (small + large paths)."""
    if len(stages) <= 1:
        return stages
    ordered = sorted(
        [dict(s) for s in stages],
        key=lambda s: (_stage_min_chunk_order(s, source_chunks), s.get("stage_id") or 0),
    )
    return _renumber_stages(ordered)


_SUMMARY_KC_FALLBACK = "章節總結"
_META_ONLY_KC = frozenset({"章節總結", "本章重點", "總結", "重點", "補充內容", "本章"})


def _normalize_kc_text(text: str) -> str:
    """NFKC：PDF 相容字（如 ⾯）與標準字（面）統一後再比對。"""
    return unicodedata.normalize("NFKC", text or "")


def _summary_kc_from_title(title: str) -> str:
    """Summary stage 無 kc 時，從 title 抽可教學概念，避免一律 meta「章節總結」。"""
    title = (title or "").strip()
    if not title:
        return _SUMMARY_KC_FALLBACK
    for sep in ("與", "及", "和", "：", "—", "-"):
        if sep in title:
            head = title.split(sep, 1)[0].strip()
            if head and head not in _META_ONLY_KC and len(head) >= 2:
                return head[:8]
    cleaned = title[:8]
    if cleaned in _META_ONLY_KC or cleaned.startswith("章節總結"):
        return _SUMMARY_KC_FALLBACK
    return cleaned


def ensure_empty_key_concepts(stages: list[dict]) -> list[dict]:
    """When prune drops all kc but stage still has chunks, inject minimal teachable kc."""
    out: list[dict] = []
    for stage in stages:
        s = dict(stage)
        kcs = [str(kc).strip() for kc in (s.get("key_concepts") or []) if kc]
        if kcs or not (s.get("source_chunk_ids") or []):
            out.append(s)
            continue
        title = (s.get("title") or "").strip()
        kind = s.get("kind") or ""
        if kind in ("follow_up_orphan", "summary") or any(
            h in title for h in _SUMMARY_HINTS
        ):
            s["key_concepts"] = [_summary_kc_from_title(title)]
            s.setdefault("kind", "summary")
        elif title:
            s["key_concepts"] = [title[:8]]
        else:
            s["key_concepts"] = [_SUMMARY_KC_FALLBACK]
        out.append(s)
    return out


def finalize_curriculum_stages(
    stages: list[dict],
    source_chunks: list[dict],
) -> list[dict]:
    """Common post-pipeline finalize: chunk-order sort + empty-kc fallback (all paths)."""
    stages = sort_stages_by_chunk_order(stages, source_chunks)
    stages = ensure_empty_key_concepts(stages)
    return stages


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


def _kc_base_name(kc: str) -> str:
    """「巴菲特 (Warren Buffett)」→「巴菲特」— 括號別名 dedupe 用。"""
    return _PAREN_SUFFIX_RE.sub("", str(kc).strip()).strip()


def dedupe_key_concept_aliases(stages: list[dict]) -> list[dict]:
    """同 stage 內合併括號別名 kc（如「中信金」/「中信金 (2891)」保留較短者）。"""
    out: list[dict] = []
    for stage in stages:
        s = dict(stage)
        kcs = [str(kc).strip() for kc in (s.get("key_concepts") or []) if kc]
        if len(kcs) <= 1:
            out.append(s)
            continue
        best_by_base: dict[str, str] = {}
        for kc in kcs:
            base = _kc_base_name(kc)
            if not base:
                continue
            prev = best_by_base.get(base)
            if prev is None:
                best_by_base[base] = kc
                continue
            prefer_new = (
                len(kc) < len(prev)
                or ("(" not in kc and "（" not in kc and ("(" in prev or "（" in prev))
            )
            if prefer_new:
                best_by_base[base] = kc
        ordered: list[str] = []
        seen: set[str] = set()
        for kc in kcs:
            base = _kc_base_name(kc)
            chosen = best_by_base.get(base, kc)
            if chosen not in seen:
                ordered.append(chosen)
                seen.add(chosen)
        s["key_concepts"] = ordered
        out.append(s)
    return out


def prune_phantom_key_concepts(
    stages: list[dict],
    source_chunks: list[dict],
) -> list[dict]:
    """移除 stage 指派 chunks 中無文字證據的 key_concept（如 outline 幻覺「台塑四寶案例」）。"""
    if not stages:
        return stages
    by_id = chunks_lookup(source_chunks)
    out: list[dict] = []
    for stage in stages:
        s = dict(stage)
        ids = list(s.get("source_chunk_ids") or [])
        kcs = [str(kc).strip() for kc in (s.get("key_concepts") or []) if kc]
        if not kcs or not ids:
            out.append(s)
            continue
        kept = [kc for kc in kcs if _kc_covered_in_chunks(kc, ids, by_id)]
        if kept:
            s["key_concepts"] = kept
        elif len(kcs) == 1:
            s["key_concepts"] = kcs
        else:
            s["key_concepts"] = []
        out.append(s)
    return out


def split_kc_heavy_stages(
    stages: list[dict],
    source_chunks: list[dict],
    *,
    max_chunks: int = KC_HEAVY_SPLIT_CHUNK_THRESHOLD,
    min_kc: int = STAGE_MAX_KEY_CONCEPTS,
) -> list[dict]:
    """Split stages at kc cap that still pack too many chunks (reducer / orphan mash-up)."""
    if not stages:
        return stages
    out: list[dict] = []
    changed = False
    for stage in stages:
        ids = list(stage.get("source_chunk_ids") or [])
        kcs = list(stage.get("key_concepts") or [])
        if len(kcs) >= min_kc and len(ids) > max_chunks:
            parts = split_oversized_stages([stage], source_chunks, max_chunks=max_chunks)
            out.extend(parts)
            if len(parts) > 1:
                changed = True
        else:
            out.append(stage)
    return _renumber_stages(out) if changed else out


def split_oversized_stages(
    stages: list[dict],
    source_chunks: list[dict],
    *,
    max_chunks: int = ORPHAN_STAGE_MAX_CHUNKS,
) -> list[dict]:
    """將 chunk 數超過上限的 stage 分批為 follow-up 節點（含 reducer mega-stage）。"""
    if max_chunks <= 0 or not stages:
        return stages
    by_id = chunks_lookup(source_chunks)
    out: list[dict] = []
    split_batch = 0
    for stage in stages:
        s = dict(stage)
        ids = sorted(
            list(s.get("source_chunk_ids") or []),
            key=lambda cid: _chunk_order_index(cid, source_chunks),
        )
        if len(ids) <= max_chunks:
            out.append(s)
            continue
        title = (s.get("title") or "補充段落").strip()
        base_kcs = list(s.get("key_concepts") or [])[:STAGE_MAX_KEY_CONCEPTS]
        for batch_idx in range(0, len(ids), max_chunks):
            batch = ids[batch_idx : batch_idx + max_chunks]
            batch_num = batch_idx // max_chunks + 1
            chunk_meta = [
                {
                    "chunk_id": cid,
                    "quote": by_id[cid].get("text") or "",
                    "note": by_id[cid].get("source_id") or "",
                }
                for cid in batch
                if cid in by_id
            ]
            if batch_num == 1:
                part = dict(s)
                part["source_chunk_ids"] = batch
                part["source_chunks"] = chunk_meta
                out.append(part)
            else:
                split_batch += 1
                out.append({
                    "stage_id": s.get("stage_id"),
                    "node_id": f"{s.get('node_id', 'x')}.split{split_batch}",
                    "title": f"{title}（續 {batch_num}）",
                    "key_concepts": base_kcs or ["章節補充"],
                    "source_chunk_ids": batch,
                    "source_chunks": chunk_meta,
                    "prerequisites": list(s.get("prerequisites") or []),
                    "estimated_questions": int(s.get("estimated_questions", 2) or 2),
                    "teaching_goal": s.get("teaching_goal") or f"續：{title}",
                    "kind": s.get("kind") or "follow_up_orphan",
                })
    return _renumber_stages(out)


def trim_stage_key_concepts(
    stages: list[dict],
    max_kc: int = STAGE_MAX_KEY_CONCEPTS,
) -> list[dict]:
    """Cap key_concepts per stage after orphan attach / reducer mash-up."""
    if max_kc <= 0:
        return stages
    out: list[dict] = []
    for stage in stages:
        s = dict(stage)
        kcs = list(s.get("key_concepts") or [])
        if len(kcs) > max_kc:
            s["key_concepts"] = kcs[:max_kc]
        out.append(s)
    return out


def _append_orphan_overflow_stages(
    stages: list[dict],
    orphan_ids: list[str],
    by_id: dict[str, dict],
    batch_size: int = ORPHAN_OVERFLOW_BATCH_SIZE,
) -> list[dict]:
    """Split excess orphans into dedicated follow-up stages instead of one mega-stage."""
    if not orphan_ids:
        return stages
    out = [dict(s) for s in stages]
    next_stage_id = max((s.get("stage_id") or 0) for s in out) + 1
    batch_num = 0
    for i in range(0, len(orphan_ids), batch_size):
        batch = orphan_ids[i : i + batch_size]
        batch_num += 1
        orphan_meta = [
            {
                "chunk_id": cid,
                "quote": by_id[cid].get("text") or "",
                "note": by_id[cid].get("source_id") or "",
            }
            for cid in batch
            if cid in by_id
        ]
        out.append({
            "stage_id": next_stage_id,
            "node_id": f"orphan.{batch_num}",
            "title": f"補充段落（{batch_num}）",
            "key_concepts": ["章節補充"],
            "source_chunk_ids": batch,
            "source_chunks": orphan_meta,
            "prerequisites": [],
            "estimated_questions": 2,
            "teaching_goal": "補充：未被前面節點覆蓋的段落（orphan overflow 分批）",
            "kind": "follow_up_orphan",
        })
        next_stage_id += 1
    return out


def _find_best_orphan_attach_stage(
    stages: list[dict],
    orphan_order_index: int,
    source_chunks: list[dict],
    attach_counts: list[int],
) -> int | None:
    """Nearest non-intro stage with capacity; None → use overflow batch."""
    best_i: int | None = None
    best_dist = 10**9
    for i, stage in enumerate(stages):
        if _is_intro_framework_stage(stage):
            continue
        ids = stage.get("source_chunk_ids") or []
        if not ids:
            continue
        anchor = min(_chunk_order_index(cid, source_chunks) for cid in ids)
        dist = abs(orphan_order_index - anchor)
        stage_ids = stage.get("source_chunk_ids") or []
        at_chunk_cap = len(stage_ids) >= ORPHAN_STAGE_MAX_CHUNKS
        at_attach_cap = attach_counts[i] >= ORPHAN_BULK_MAX_ATTACH_PER_STAGE
        if at_chunk_cap or at_attach_cap:
            continue
        if dist < best_dist:
            best_dist = dist
            best_i = i
    return best_i


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
            return trim_stage_key_concepts(out)

    if len(orphans) > 3:
        attach_counts = [0] * len(out)
        overflow: list[str] = []
        for oid in orphans:
            oidx = _chunk_order_index(oid, source_chunks)
            best_i = _find_best_orphan_attach_stage(out, oidx, source_chunks, attach_counts)
            if best_i is None:
                overflow.append(oid)
            else:
                out[best_i] = _attach_chunk_to_stage(out[best_i], oid, by_id)
                attach_counts[best_i] += 1
        if overflow:
            out = _append_orphan_overflow_stages(out, overflow, by_id)
        return trim_stage_key_concepts(out)

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
    return trim_stage_key_concepts(out)


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


_KC_ENGLISH_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
# 中文 KC 子串 → chunk 內常見英文 anchor（PDF 常只抽英文術語）
_KC_CN_EN_ANCHORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("路由層", ("routing layer",)),
    ("架構決策", ("managed database", "partition ownership", "rebalancing", "infrastructure-heavy")),
    ("面試", ("interview",)),
)
_INTRO_KC_MAX_FORWARD_SPAN = 4


def _kc_covered_in_chunks(
    kc: str,
    chunk_ids: list[str],
    by_id: dict[str, dict],
) -> bool:
    """Return True when key concept text or anchor terms appear in assigned chunks."""
    if not kc:
        return True
    combined_raw = " ".join(str(by_id.get(cid, {}).get("text") or "") for cid in chunk_ids)
    if not combined_raw:
        return False
    combined = _normalize_kc_text(combined_raw)
    combined_lower = combined.lower()
    kc_stripped = _normalize_kc_text(kc.strip())
    if kc_stripped and kc_stripped in combined:
        return True
    for term in _KC_ENGLISH_TERM_RE.findall(kc):
        if term.lower() in combined_lower:
            return True
    cn = re.sub(r"[\s\(\)（）]", "", kc_stripped)
    for n in (4, 3, 2):
        if len(cn) >= n and cn[:n] in combined:
            return True
    for cn_key, en_phrases in _KC_CN_EN_ANCHORS:
        if _normalize_kc_text(cn_key) in cn:
            for phrase in en_phrases:
                if phrase.lower() in combined_lower:
                    return True
    return False


def _minimal_contiguous_chunks_for_kc(
    anchor_cid: str,
    key_concepts: list[str],
    source_chunks: list[dict],
    *,
    max_forward_span: int = _INTRO_KC_MAX_FORWARD_SPAN,
) -> list[str]:
    """Expand forward from anchor chunk until key_concepts are evidenced (contiguous span)."""
    ordered = sorted(source_chunks, key=lambda c: c.get("order_index", 0))
    ordered_ids = [c["chunk_id"] for c in ordered if c.get("chunk_id")]
    if anchor_cid not in ordered_ids:
        return [anchor_cid] if anchor_cid else []
    by_id = chunks_lookup(source_chunks)
    kcs = [kc for kc in (key_concepts or []) if kc]
    start_idx = ordered_ids.index(anchor_cid)
    end_idx = start_idx
    for _ in range(max_forward_span):
        span_ids = ordered_ids[start_idx : end_idx + 1]
        if not kcs or all(_kc_covered_in_chunks(kc, span_ids, by_id) for kc in kcs):
            break
        if end_idx + 1 >= len(ordered_ids):
            break
        end_idx += 1
    return ordered_ids[start_idx : end_idx + 1]


def _attach_source_chunks_meta(stage: dict, chunk_ids: list[str], by_id: dict[str, dict]) -> dict:
    s = dict(stage)
    s["source_chunk_ids"] = chunk_ids
    s["source_chunks"] = [
        {
            "chunk_id": cid,
            "quote": by_id[cid].get("text") or "",
            "note": by_id[cid].get("source_id") or "",
        }
        for cid in chunk_ids
        if cid in by_id
    ]
    return s


def ensure_key_concept_chunk_coverage(
    stages: list[dict],
    source_chunks: list[dict],
    *,
    max_neighbor_distance: int = 3,
) -> list[dict]:
    """Attach missing chunks when key_concepts only appear in unassigned source text."""
    if not stages or not source_chunks:
        return stages
    by_id = chunks_lookup(source_chunks)
    ordered_ids = [
        c["chunk_id"]
        for c in sorted(source_chunks, key=lambda x: x.get("order_index", 0))
        if c.get("chunk_id")
    ]
    out: list[dict] = []
    for stage in stages:
        s = dict(stage)
        if _is_intro_framework_stage(s):
            out.append(s)
            continue
        ids = list(s.get("source_chunk_ids") or [])
        kcs = [kc for kc in (s.get("key_concepts") or []) if kc]
        for kc in kcs:
            if _kc_covered_in_chunks(kc, ids, by_id):
                continue
            anchor_cid: str | None = None
            for cid in ordered_ids:
                if _kc_covered_in_chunks(kc, [cid], by_id):
                    anchor_cid = cid
                    break
            if not anchor_cid or anchor_cid in ids:
                continue
            if ids:
                stage_indices = [_chunk_order_index(cid, source_chunks) for cid in ids]
                anchor_idx = _chunk_order_index(anchor_cid, source_chunks)
                lo, hi = min(stage_indices), max(stage_indices)
                if anchor_idx < lo:
                    if lo - anchor_idx <= max_neighbor_distance:
                        ids.insert(0, anchor_cid)
                elif anchor_idx > hi:
                    if anchor_idx - hi <= max_neighbor_distance:
                        ids.append(anchor_cid)
            else:
                ids.append(anchor_cid)
        ids = sorted(set(ids), key=lambda c: _chunk_order_index(c, source_chunks))
        out.append(_attach_source_chunks_meta(s, ids, by_id))
    return out


def trim_intro_stage_first_chunk_only(
    stages: list[dict],
    source_chunks: list[dict],
) -> list[dict]:
    """Intro/framework stage keeps only the document's first chunk (no forward kc expansion)."""
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
        if _is_intro_framework_stage(s) and first_cid in (s.get("source_chunk_ids") or []):
            out.append(_attach_source_chunks_meta(s, [first_cid], by_id))
        else:
            out.append(s)
    return out


def normalize_stages_pre_verify(
    stages: list[dict],
    source_chunks: list[dict],
) -> list[dict]:
    """Prune intro overlap, dedupe titles, reassign case chunks (before global verify)."""
    stages = trim_intro_stage_first_chunk_only(stages, source_chunks)
    stages = ensure_key_concept_chunk_coverage(stages, source_chunks)
    stages = prune_phantom_key_concepts(stages, source_chunks)
    stages = dedupe_key_concept_aliases(stages)
    stages = split_oversized_stages(stages, source_chunks)
    stages = split_kc_heavy_stages(stages, source_chunks)
    stages = prune_intro_chunk_sharing(stages, source_chunks)
    stages = merge_duplicate_topic_stages(stages)
    stages = merge_empty_chunk_stages(stages)
    stages = reassign_case_stage_chunks(stages, source_chunks)
    return stages


def normalize_small_file_stages(
    stages: list[dict],
    source_chunks: list[dict],
) -> list[dict]:
    """Alias for normalize_stages_pre_verify (small + full V2 paths)."""
    return normalize_stages_pre_verify(stages, source_chunks)


def finalize_small_file_stages(
    stages: list[dict],
    source_chunks: list[dict],
) -> list[dict]:
    """Full post-compose normalization including orphan attach."""
    stages = normalize_small_file_stages(stages, source_chunks)
    stages = ensure_orphan_chunks_attached(stages, source_chunks)
    stages = split_oversized_stages(stages, source_chunks)
    stages = split_kc_heavy_stages(stages, source_chunks)
    stages = dedupe_key_concept_aliases(stages)
    stages = prune_phantom_key_concepts(stages, source_chunks)
    stages = ensure_empty_key_concepts(stages)
    return trim_stage_key_concepts(stages)


_TOC_RULE_LINE_RE = re.compile(r"^\s*法則\s*\d+")
_CN_TOC_SECTION_RE = re.compile(r"^第[一二三四五六七八九十百零\d]+節")


_EPUB_NAV_JUNK_HINTS = (
    "發表新回應",
    "書籍首頁",
    "回書籍",
    "回上一頁",
    "下一頁",
    "書刊介紹",
    "購買紙本",
    "電子書籍",
    "版權聲明",
    "前往購買",
    "博客來",
)


def is_epub_nav_junk_chunk(chunk: dict) -> bool:
    """EPUB 尾端導航 / 留言區 junk（非正文段落）。"""
    text = str(chunk.get("text") or "").strip()
    if not text or len(text) > 500:
        return False
    hits = sum(1 for hint in _EPUB_NAV_JUNK_HINTS if hint in text)
    if hits >= 2:
        return True
    if hits >= 1 and len(text) < 150:
        return True
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return hits >= 1 and len(lines) <= 4 and len(text) < 250


def filter_epub_nav_junk_chunks(source_chunks: list[dict]) -> list[dict]:
    """Drop nav junk chunks and re-index order_index / chunk_id."""
    kept = [
        c for c in source_chunks
        if isinstance(c, dict) and c.get("chunk_id") and not is_epub_nav_junk_chunk(c)
    ]
    if len(kept) == len(source_chunks):
        return source_chunks
    out: list[dict] = []
    for i, chunk in enumerate(kept):
        c = dict(chunk)
        c["chunk_id"] = f"chunk_{i:04d}"
        c["order_index"] = i
        out.append(c)
    return out


def is_toc_cn_epub_chunk(chunk: dict) -> bool:
    """目次 chunk：含「目錄」且多行 第X節 標題、缺正文段落。"""
    text = str(chunk.get("text") or "")
    if "目錄" not in text[:1200]:
        return False
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 8:
        return False
    section_lines = sum(1 for ln in lines if _CN_TOC_SECTION_RE.match(ln))
    return section_lines >= 8 and section_lines / len(lines) >= 0.2


def is_toc_listicle_chunk(chunk: dict) -> bool:
    """目次 chunk：多行「法則 N」標題、無 section_title、缺正文段落。"""
    if (chunk.get("section_title") or "").strip():
        return False
    text = str(chunk.get("text") or "")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 5:
        return False
    rule_lines = sum(1 for ln in lines if _TOC_RULE_LINE_RE.match(ln))
    return rule_lines >= 5 and rule_lines / len(lines) >= 0.35


def prune_toc_listicle_chunks(
    stages: list[dict],
    source_chunks: list[dict],
) -> list[dict]:
    """Remove TOC-only listicle chunks from stages (avoid duplicate 目次 stages)."""
    toc_ids = {
        c["chunk_id"]
        for c in source_chunks
        if c.get("chunk_id") and (
            is_toc_listicle_chunk(c)
            or is_toc_cn_epub_chunk(c)
            or is_epub_nav_junk_chunk(c)
        )
    }
    if not toc_ids:
        return stages

    by_id = chunks_lookup(source_chunks)
    out: list[dict] = []
    for stage in stages:
        s = dict(stage)
        ids = [cid for cid in (s.get("source_chunk_ids") or []) if cid not in toc_ids]
        if not ids:
            continue
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
    return out if out else stages


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
