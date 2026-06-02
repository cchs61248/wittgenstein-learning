"""Deterministic helpers for small-file curriculum (≤ N chunks).

Targets API Design.pdf-style inputs: few chunks, multiple named cases,
without redundant post-process stages or orphan chunks.
"""
from __future__ import annotations

import os
import re
import unicodedata

from .fuzzy_match import concept_jaccard, similarity

DEFAULT_SMALL_FILE_CHUNK_THRESHOLD = 50
# ≤50 chunks: run finalize orphan recovery even on forced full V2 path.
COMPACT_FINALIZE_CHUNK_MAX = 50
# ≤30 chunks: global verifier requires zero orphan chunks.
COMPACT_ZERO_ORPHAN_CHUNK_MAX = 30
CASE_MATCH_THRESHOLD = 0.72
DUPLICATE_TITLE_THRESHOLD = 0.85  # default fallback
# P0b-1: cross-source stage merge when key_concepts jaccard ≥ THRESHOLD
DEFAULT_CONCEPT_OVERLAP_THRESHOLD = 0.6
# Bulk orphan attach: cap per-stage growth so narrative EPUBs don't mash 20+ chunks into one stage.
# P2b: raised 3 -> 5 to absorb more orphans into thematically-near stages
# instead of dumping them into generic「補充段落（N）」overflow stages.
ORPHAN_BULK_MAX_ATTACH_PER_STAGE = 5
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


def duplicate_title_threshold() -> float:
    raw = os.getenv("STAGE_TITLE_MERGE_THRESHOLD", str(DUPLICATE_TITLE_THRESHOLD))
    try:
        v = float(raw)
        if 0.0 < v <= 1.0:
            return v
    except (TypeError, ValueError):
        pass
    return DUPLICATE_TITLE_THRESHOLD


def concept_overlap_threshold() -> float:
    """P0b-1: env-tuned threshold for cross-source stage merge via concept jaccard."""
    raw = os.getenv("STAGE_CONCEPT_OVERLAP_THRESHOLD", str(DEFAULT_CONCEPT_OVERLAP_THRESHOLD))
    try:
        v = float(raw)
        if 0.0 < v <= 1.0:
            return v
    except (TypeError, ValueError):
        pass
    return DEFAULT_CONCEPT_OVERLAP_THRESHOLD


def is_small_file(source_chunks: list[dict]) -> bool:
    return len(source_chunks) <= DEFAULT_SMALL_FILE_CHUNK_THRESHOLD


def source_count(source_chunks: list[dict]) -> int:
    if not source_chunks:
        return 1
    return len({
        c.get("source_id") or f"src_{c.get('source_index', 0)}"
        for c in source_chunks
        if isinstance(c, dict)
    }) or 1


def choose_postprocess_mode(n_sources: int, same_material: bool | None) -> str:
    """Phase 1: pick the stage post-processing mode.

    - single source（含單檔，無論 same_material）→ 保留 splitter 邊界，不合併。
    - multi source + same_material is True → 只協調順序/命名（Phase 2+），暫不合併。
    - multi source + same_material 非 True（False / None legacy）→ 受控跨教材合併。

    `same_material is True` 是刻意嚴格比對：多 source 但旗標缺失（NULL legacy）
    保守視為不同教材，避免漏掉必要合併（設計文件 18.1）。
    """
    if n_sources <= 1:
        return "single_source_finalize_only"
    if same_material is True:
        return "same_material_coordinate_only"
    return "cross_material_merge_and_coordinate"


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
    threshold: float | None = None,
) -> list[dict]:
    """合併標題相同或高度相似的 stage。threshold None → 用 env / 預設 0.85。"""
    if threshold is None:
        threshold = duplicate_title_threshold()
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


def merge_by_concept_overlap(
    stages: list[dict],
    *,
    threshold: float | None = None,
) -> list[dict]:
    """P0b-1: Cross-source merge by key_concepts jaccard.

    `merge_duplicate_topic_stages` only catches near-identical titles. Per-source
    splitter often invents different prefixes for the same topic across chapters
    (e.g. "借錢外掛（一）：信用貸款" vs "借錢工具解析（一）：股票質押") so the
    titles never collide. This pass merges stages whose key_concepts overlap
    ≥ THRESHOLD (default 0.6) regardless of title wording.

    `_merge_stage_into` keeps the EARLIER stage's title (target), so the first
    occurrence's prefix wins — this stabilises the final naming.
    """
    if threshold is None:
        threshold = concept_overlap_threshold()
    if len(stages) <= 1:
        return stages

    result: list[dict] = []
    for stage in stages:
        kc = stage.get("key_concepts") or []
        merged = False
        if kc:
            for existing in result:
                ekc = existing.get("key_concepts") or []
                if not ekc:
                    continue
                if concept_jaccard(kc, ekc) >= threshold:
                    _merge_stage_into(existing, stage)
                    merged = True
                    break
        if not merged:
            result.append(dict(stage))
    return _renumber_stages(result)


# P3b: deterministic ordering applied AFTER LLM consolidator. LLM may violate
# "（一） must precede （二）" or reading-order constraints; this layer enforces.
_ORDINAL_MAP = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}
_ORDINAL_RE = re.compile(r"[（(]([一二三四五六七八九十]+|\d+)[）)]")
# P4a: «（續）» / «（續 N）» comes from _append_orphan_overflow_stages — overflow
# of the SAME base stage. Must stay glued to base regardless of chunk_id span.
_FOLLOWUP_RE = re.compile(r"^(.+?)（續(?:\s*(\d+))?）$")


def _stage_min_chunk(stage: dict) -> str:
    """Smallest chunk_id string for ordering. Zero-padded chunk_NNNN sorts lexicographically."""
    cids = stage.get("source_chunk_ids") or []
    return min(cids) if cids else "chunk_zzzz"


def _extract_ordinal_group(title: str) -> tuple[str, int] | None:
    """Parse '借錢工具（二）：股票質押' -> ('借錢工具', 2). None if no ordinal marker."""
    if not title:
        return None
    m = _ORDINAL_RE.search(title)
    if not m:
        return None
    raw = m.group(1)
    n = int(raw) if raw.isdigit() else _ORDINAL_MAP.get(raw)
    if n is None:
        return None
    prefix = title[:m.start()].strip().rstrip("：:").strip()
    if not prefix:
        return None
    return prefix, n


def _extract_followup(stage: dict) -> tuple[str, int] | None:
    """Detect '案例：肥羊與診所護士實戰（續 2）' style overflow stages.

    Returns (base_title, batch_num) so we can glue back to the base stage.
    Trigger conditions (either):
      - kind == follow_up_orphan with title matching the （續 N） pattern
      - title matches the （續 N） pattern regardless of kind
    """
    title = (stage.get("title") or "").strip()
    if not title:
        return None
    m = _FOLLOWUP_RE.match(title)
    if not m:
        return None
    base = m.group(1).strip()
    if not base:
        return None
    batch = int(m.group(2)) if m.group(2) else 1
    return base, batch


def enforce_stage_ordering(stages: list[dict]) -> list[dict]:
    """Force stages into reading order. Runs after consolidator since LLM may violate.

    Rules:
    1. Stages sharing a '（N）' prefix form an ordinal group, sorted by N ascending.
    2. Each group's position = min(first_chunk_id) of its members.
    3. Non-group stages position by their own first_chunk_id.
    4. Stages with '（續）' / '（續 N）' suffix glue to their base stage (P4a):
       base goes through normal ordering, follow-ups are appended immediately
       after, sorted by batch number ascending.
    5. node_id re-assigned via `_renumber_stages` (groups stay contiguous).

    Singleton groups (only '（一）' with no sibling) degrade to non-group ordering.
    Follow-ups whose base title cannot be matched degrade to standalone stages.
    """
    if len(stages) <= 1:
        return stages

    # P4a: separate follow-up overflow stages first; they attach to base later.
    followups_by_base: dict[str, list[tuple[int, dict]]] = {}
    orderable: list[dict] = []
    for s in stages:
        fu = _extract_followup(s)
        if fu is not None:
            base_title, batch = fu
            followups_by_base.setdefault(base_title, []).append((batch, s))
        else:
            orderable.append(s)

    groups: dict[str, list[tuple[int, dict]]] = {}
    singles: list[dict] = []
    for s in orderable:
        key = _extract_ordinal_group(s.get("title") or "")
        if key is None:
            singles.append(s)
            continue
        prefix, n = key
        groups.setdefault(prefix, []).append((n, s))

    # Build (sort_key, [stage,...]) buckets
    buckets: list[tuple[str, list[dict]]] = []
    for prefix, members in groups.items():
        if len(members) <= 1:
            singles.extend(s for _, s in members)
            continue
        members.sort(key=lambda pair: pair[0])
        group_stages = [s for _, s in members]
        buckets.append((min(_stage_min_chunk(s) for s in group_stages), group_stages))
    for s in singles:
        buckets.append((_stage_min_chunk(s), [s]))

    buckets.sort(key=lambda b: b[0])
    flat: list[dict] = []
    for _, group_stages in buckets:
        flat.extend(group_stages)

    # P4a: now insert each follow-up right after its base (matched by title).
    # Unmatched follow-ups fall through to the tail sorted by their own chunk_id.
    if followups_by_base:
        matched_bases: set[str] = set()
        inserted: list[dict] = []
        for s in flat:
            inserted.append(s)
            base_title = (s.get("title") or "").strip()
            fus = followups_by_base.get(base_title)
            if fus:
                fus.sort(key=lambda pair: pair[0])
                inserted.extend(f for _, f in fus)
                matched_bases.add(base_title)
        # Append unmatched follow-ups at end, ordered by their own min chunk_id
        unmatched: list[dict] = []
        for base, fus in followups_by_base.items():
            if base in matched_bases:
                continue
            for _, f in sorted(fus, key=lambda pair: pair[0]):
                unmatched.append(f)
        unmatched.sort(key=_stage_min_chunk)
        inserted.extend(unmatched)
        flat = inserted

    return _renumber_stages(flat)


def enforce_followup_adjacency_only(stages: list[dict]) -> list[dict]:
    """T-FOLLOWUP-ADJACENCY: pull each '（續 N）' follow-up to sit immediately after its
    base stage, WITHOUT re-sorting base/non-follow-up stages.

    Unlike ``enforce_stage_ordering`` (which rebuilds base order by min chunk), this only
    moves follow-up stages and preserves the received relative order of every
    non-follow-up stage. That makes it safe to run *after* a pedagogical reorder: the
    planner decides base order, follow-ups are then re-attached to their base. Calling
    full ``enforce_stage_ordering`` post-planner would re-sort bases by chunk and clobber
    the applied teaching order (the T4e failure mode).

    Matching uses ``_extract_followup`` → (base_title, batch). Sibling follow-ups are
    ordered by batch ascending. Follow-ups whose base title is absent degrade to the tail
    in their original relative order. Stage content is never mutated; the caller renumbers
    if this becomes the persisted order. Returns the input list unchanged (same object)
    when there is nothing to re-attach.
    """
    if len(stages) <= 1:
        return stages

    followups_by_base: dict[str, list[tuple[int, dict]]] = {}
    base_seq: list[dict] = []
    for s in stages:
        fu = _extract_followup(s)
        if fu is None:
            base_seq.append(s)
        else:
            followups_by_base.setdefault(fu[0], []).append((fu[1], s))

    if not followups_by_base:
        return stages  # no-op: no follow-ups to re-attach

    out: list[dict] = []
    matched: set[str] = set()
    for s in base_seq:
        out.append(s)
        title = (s.get("title") or "").strip()
        fus = followups_by_base.get(title)
        if fus and title not in matched:
            out.extend(f for _, f in sorted(fus, key=lambda pair: pair[0]))
            matched.add(title)

    # Unmatched follow-ups (base title absent): keep original relative order at the tail.
    if len(out) != len(stages):
        for s in stages:
            fu = _extract_followup(s)
            if fu is not None and fu[0] not in matched:
                out.append(s)
    return out


def followup_adjacency_violations(stages: list[dict]) -> list[dict]:
    """Deterministic check for T-FOLLOWUP-ADJACENCY.

    Returns follow-up stages whose base chain is broken (``not_adjacent_to_base``) or
    whose same-base sibling batch order is not strictly ascending within a contiguous
    run (``followup_batch_out_of_order``). Earlier-batch siblings of the same base are
    allowed between a base and a later follow-up. Empty list means the invariant holds.
    Unmatched follow-ups (base title absent) are treated as degraded, not violations.
    """
    base_titles: set[str] = set()
    for s in stages:
        if _extract_followup(s) is None:
            t = (s.get("title") or "").strip()
            if t:
                base_titles.add(t)

    violations: list[dict] = []
    last_batch_by_base: dict[str, int] = {}  # last batch seen in the current contiguous run
    for i, s in enumerate(stages):
        fu = _extract_followup(s)
        if fu is None:
            # base / non-follow-up: a new base resets its run anchor
            t = (s.get("title") or "").strip()
            last_batch_by_base.pop(t, None)
            continue
        base_title, batch = fu
        if base_title not in base_titles:
            continue  # degraded (no base) — not a violation
        prev = stages[i - 1] if i > 0 else None
        prev_ok = False
        if prev is not None:
            prev_fu = _extract_followup(prev)
            if prev_fu is None:
                prev_ok = (prev.get("title") or "").strip() == base_title
            else:
                prev_ok = prev_fu[0] == base_title
        if not prev_ok:
            violations.append({
                "stage_id": s.get("stage_id"),
                "title": (s.get("title") or "").strip(),
                "base_title": base_title,
                "reason": "not_adjacent_to_base",
            })
            # chain broken — drop run anchor so siblings after the break aren't
            # double-flagged for batch order against a stale predecessor.
            last_batch_by_base.pop(base_title, None)
            continue
        last = last_batch_by_base.get(base_title)
        if last is not None and batch <= last:
            violations.append({
                "stage_id": s.get("stage_id"),
                "title": (s.get("title") or "").strip(),
                "base_title": base_title,
                "reason": "followup_batch_out_of_order",
            })
        last_batch_by_base[base_title] = batch
    return violations


_MEDIUM_XMAT_MAX_CHUNKS = 30   # consolidator/Phase-4 chunk gate; below this neither runs
_MEDIUM_XMAT_MIN_SOURCES = 3


def _normalize_theme_title(title: str) -> str:
    """Collapse a stage title to its theme root for duplicate-theme detection.

    Strips '（續 N）' follow-up suffix, ordinal markers '（N）/（一）', and the detail
    clause after a '：' / ':' separator, so two same-topic stages from different
    sources (e.g. '出場策略四條鐵律：與人生週期同步調整' vs '出場策略四條鐵律：建立投資憲法')
    collapse to the same key.
    """
    t = (title or "").strip()
    m = _FOLLOWUP_RE.match(t)
    if m:
        t = m.group(1).strip()
    og = _extract_ordinal_group(t)
    if og:
        t = og[0]
    for sep in ("：", ":"):
        if sep in t:
            t = t.split(sep, 1)[0].strip()
            break
    return t


def detect_medium_cross_material_gap(
    *,
    same_material: bool,
    source_count: int,
    chunk_count: int,
    stages: list[dict],
) -> dict | None:
    """T-MID-XMAT Phase 1 warn-only detector (pure / deterministic / no LLM / no mutation).

    Returns a diagnostic payload when a cross-material curriculum falls in the "medium"
    gap: several short sources but < 30 chunks, so neither the global StageConsolidator
    (needs chunks>=30) nor the Phase 4 planner reorder (gated on chunks>=30) runs and the
    sources are never cross-organised. Returns None outside the gap.

    Payload carries structural facts plus a deterministic duplicate-theme signal
    (non-follow-up stages whose normalized theme title collides — likely the same topic
    from different sources that was never merged).
    """
    if same_material:
        return None
    if source_count < _MEDIUM_XMAT_MIN_SOURCES:
        return None
    if chunk_count >= _MEDIUM_XMAT_MAX_CHUNKS:
        return None

    theme_to_ids: dict[str, list] = {}
    for s in stages:
        if _extract_followup(s) is not None:
            continue  # follow-up siblings legitimately share their base theme
        key = _normalize_theme_title(s.get("title") or "")
        if not key:
            continue
        theme_to_ids.setdefault(key, []).append(s.get("stage_id"))
    duplicate_theme_groups = [
        {"theme": k, "stage_ids": ids}
        for k, ids in theme_to_ids.items()
        if len(ids) >= 2
    ]

    stage_count = len(stages)
    return {
        "type": "medium_cross_material_gap",
        "source_count": source_count,
        "chunk_count": chunk_count,
        "stage_count": stage_count,
        "stage_per_source": round(stage_count / source_count, 2) if source_count else 0,
        "consolidator_skipped": True,
        "planner_reorder_skipped": True,
        "duplicate_theme_groups": duplicate_theme_groups,
    }


# --- generic_kc_collapse: cross-stage umbrella key_concept degradation detector ---
# Warn-only, deterministic, no LLM, no mutation. Distinct from the stage-local hygiene
# audits (malformed_key_concept / meta_only_key_concepts): this is a CROSS-STAGE ratio
# signal — the splitter degrading specific concepts into broad umbrella terms across the
# curriculum (live root: sess_tkfe20227, kc 退化成傘狀詞). Pure whitelist match keeps it
# conservative (prefer false-negatives); extend _GENERIC_UMBRELLA_KC when live misses show.
_GENERIC_UMBRELLA_KC = frozenset({
    # 概念 / 觀念 umbrella
    "概念", "基本概念", "核心概念", "重要概念", "主要概念", "相關概念", "重點概念",
    "觀念", "基本觀念", "核心觀念", "重要觀念",
    # 內容 umbrella
    "內容", "主要內容", "核心內容", "重要內容", "相關內容", "補充內容",
    # 知識 umbrella
    "知識", "相關知識", "基礎知識", "背景知識", "基本知識",
    # 原理 / 架構 umbrella
    "基本原理", "核心原理", "基本架構", "整體架構",
    # 重點 / 要點 umbrella
    "重點", "要點", "重點整理", "核心重點",
    # 介紹 / 概述 umbrella
    "簡介", "概述", "概論", "介紹", "導論", "總覽",
    # 方法 / 策略 bare umbrella
    "基本方法", "核心方法", "基本策略", "核心策略",
})

_GENERIC_STAGE_MIN_KC = 2        # need >=2 kc to assess a stage for Rule A
_GENERIC_STAGE_MIN_GENERIC = 2   # Rule A needs at least this many generic kc in the stage
_GENERIC_STAGE_RATIO = 0.5       # Rule A: generic fraction within a stage
_GENERIC_CURRICULUM_RATIO = 0.3  # Rule B: generic fraction across the curriculum


def _is_generic_umbrella_kc(kc: str) -> bool:
    return (kc or "").strip() in _GENERIC_UMBRELLA_KC


def detect_generic_kc_collapse(stages: list[dict]) -> dict | None:
    """Warn-only detector for umbrella/generic key_concept collapse (pure / no mutation).

    Scans non-follow-up stages (follow-ups copy their base kc and would double-count).
    Fires when either:
      Rule A — a stage's key_concepts are dominated by generic umbrella terms
               (>=2 generic kc and generic fraction >= 0.5), or
      Rule B — the curriculum-wide generic fraction >= 0.3.
    Returns None when neither rule fires (no warning noise). Material-independent:
    kc collapse is a splitter-quality issue regardless of same_material / chunk count.
    """
    total_kc = 0
    generic_total = 0
    collapsed_stages: list[dict] = []
    for s in stages or []:
        if _extract_followup(s) is not None:
            continue  # follow-up siblings copy base kc — exclude from scan
        kcs = [str(k).strip() for k in (s.get("key_concepts") or []) if str(k).strip()]
        if not kcs:
            continue
        generic = [k for k in kcs if _is_generic_umbrella_kc(k)]
        total_kc += len(kcs)
        generic_total += len(generic)
        if (
            len(kcs) >= _GENERIC_STAGE_MIN_KC
            and len(generic) >= _GENERIC_STAGE_MIN_GENERIC
            and len(generic) / len(kcs) >= _GENERIC_STAGE_RATIO
        ):
            collapsed_stages.append({
                "stage_id": s.get("stage_id"),
                "title": s.get("title"),
                "generic_key_concepts": generic,
                "kc_count": len(kcs),
                "generic_ratio": round(len(generic) / len(kcs), 2),
            })

    if total_kc == 0:
        return None
    generic_ratio = round(generic_total / total_kc, 2)
    curriculum_collapse = (generic_total / total_kc) >= _GENERIC_CURRICULUM_RATIO

    if not collapsed_stages and not curriculum_collapse:
        return None

    return {
        "type": "generic_kc_collapse",
        "stage_count": len(stages),
        "total_kc": total_kc,
        "generic_kc_total": generic_total,
        "generic_ratio": generic_ratio,
        "collapsed_stages": collapsed_stages,
        "curriculum_collapse": curriculum_collapse,
    }


def merge_singleton_chunk_stages(stages: list[dict]) -> list[dict]:
    """P4c: middle stages with exactly 1 chunk are merged into the previous stage.

    Why: LLM consolidator/splitter sometimes leaves a single chunk as its own stage
    when it could naturally fold into the adjacent topic (observed in sess_pmulzyche
    where stage 1 = chunk_0000 alone). 1-chunk stages are too thin for a useful
    teaching round (6 questions on 1 chunk = artificial padding).

    Head and tail singletons are preserved: chunk_0000 is often a 序章/intro that
    legitimately stands alone, and the final chunk is often a closing remark.
    Follow-up overflow stages (kind=follow_up_orphan) are also preserved — they
    were intentionally split out by `_append_orphan_overflow_stages`.

    Caller responsibility: run AFTER `enforce_stage_ordering` so "previous" means
    "previous in reading order".
    """
    if len(stages) <= 2:
        return stages

    n = len(stages)
    out: list[dict] = [dict(stages[0])]  # head always kept

    for i in range(1, n - 1):
        s = stages[i]
        cids = s.get("source_chunk_ids") or []
        if len(cids) != 1 or (s.get("kind") in ("follow_up_orphan", "summary")):
            out.append(dict(s))
            continue
        # Singleton in the middle: merge into the just-appended previous stage
        prev = out[-1]
        _merge_stage_into(prev, s)

    out.append(dict(stages[-1]))  # tail always kept
    return _renumber_stages(out)


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


# Longest-first so "的綜合總結" / "的總結" win over the bare "總結" tail.
_SUMMARY_TITLE_SUFFIXES = ("的綜合總結", "綜合總結", "的總結", "總結", "概述", "導論")


def _strip_summary_suffix(text: str) -> str:
    """Drop a trailing summary/meta suffix so the concept body survives.

    e.g. "提升 LLM 正確率的綜合總結" -> "提升 LLM 正確率".
    Never strips when the title *is* the suffix (len guard) so "總結" alone
    falls through to the meta fallback below.
    """
    for suf in _SUMMARY_TITLE_SUFFIXES:
        if text.endswith(suf) and len(text) > len(suf):
            return text[: -len(suf)].strip()
    return text


def _summary_kc_from_title(title: str) -> str:
    """Summary stage 無 kc 時，從 title 抽可教學概念，避免一律 meta「章節總結」。

    PR1 Mode 1（root cause fix）：**不再裸切 title[:8]**。舊版對無分隔符的標題做
    ``title[:8]`` 字元硬切，會把混合 ASCII+CJK 的概念切在詞中間（live:
    「提升 LLM 正確率的綜合總結」→「提升 LLM 正」）。改為：先剝除 summary 後綴，
    再以「真正的主題:細節分隔符」取 head（**不含 ASCII '-'，避免拆壞 Auto-CoT /
    Zero-shot 這類術語**），最後回傳完整片語、絕不產生 malformed 半詞。長度上限是
    display-only 的考量，不該污染 QG/Evaluator/DB 用的 canonical 概念名。
    """
    title = (title or "").strip()
    if not title:
        return _SUMMARY_KC_FALLBACK
    cleaned = _strip_summary_suffix(title)
    # 真正的「主題：細節」/全形破折號 / 同級連接詞分隔符。
    # 刻意排除 ASCII '-'：它常出現在術語內部（Auto-CoT / GPT-4 / Zero-shot），
    # 拆它會製造壞概念名。
    for sep in ("：", "—", "與", "及", "和"):
        if sep in cleaned:
            head = cleaned.split(sep, 1)[0].strip()
            if head and head not in _META_ONLY_KC and len(head) >= 2:
                cleaned = head
                break
    cleaned = cleaned.strip()
    if not cleaned or cleaned in _META_ONLY_KC or cleaned.startswith("章節總結"):
        return _SUMMARY_KC_FALLBACK
    return cleaned


# --- PR1b: build-time key-concept hygiene warnings (warn-only, stage-local) ---
# Audit-only. These helpers NEVER mutate stages/key_concepts; they only surface
# suspicious concept names into quality_warnings so QG/DB pollution is observable.
# Meta/filler labels that are never teachable key_concepts. Summary-class plus
# chapter-supplement-class (T-META-KC-SUPPLEMENT): "章節補充" slipped this set in
# sess_k73w3v6ah while "章節總結" was caught in sess_u2ccjo94t — same filler class.
# Exact-match only (see _is_meta_only_key_concepts): real concepts that merely
# contain 補充 (e.g. 補充保費, 營養補充品) are NOT flagged.
_META_ONLY_KEY_CONCEPTS = {
    "章節總結", "綜合總結", "總結", "概述", "導論",
    "章節補充", "補充說明", "補充內容",
}

# Root cause of the live regression was title[:8]; the malformed band brackets it.
_MALFORMED_KC_MIN_LEN = 6
_MALFORMED_KC_MAX_LEN = 9


def _contains_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def _contains_ascii_alnum(text: str) -> bool:
    return any(ch.isascii() and ch.isalnum() for ch in text)


def _is_likely_malformed_key_concept(stage_title: str, key_concept: str) -> bool:
    """High-confidence audit: a kc that looks hard-truncated from its stage title.

    All conditions must hold (warn-only, prefer false-negatives over false alarms):
      1. _strip_summary_suffix(title) starts with kc (strict prefix)
      2. stripped title is >= 2 chars longer than kc (not just the dropped suffix)
      3. 6 <= len(kc) <= 9 (brackets the legacy title[:8] cut)
      4. kc mixes CJK + ASCII (protects Auto-CoT / Zero-shot CoT / RAG原理)
    """
    kc = (key_concept or "").strip()
    if not kc:
        return False
    stripped = _strip_summary_suffix((stage_title or "").strip())
    if not stripped or not stripped.startswith(kc):
        return False
    if len(stripped) - len(kc) < 2:
        return False
    if not (_MALFORMED_KC_MIN_LEN <= len(kc) <= _MALFORMED_KC_MAX_LEN):
        return False
    return _contains_cjk(kc) and _contains_ascii_alnum(kc)


def _is_meta_only_key_concepts(key_concepts) -> bool:
    kcs = [str(k).strip() for k in (key_concepts or []) if str(k).strip()]
    if not kcs:
        return False
    return all(k in _META_ONLY_KEY_CONCEPTS for k in kcs)


def collect_key_concept_hygiene_warnings(stages: list[dict]) -> list[dict]:
    """Build-time, warn-only audit of final stage key_concepts.

    Returns a list of warning dicts; NEVER mutates stages. Stage-local only —
    no cross-stage / cross-material ratio logic (that is the separate, future
    `generic_kc_collapse` domain-umbrella detector).
    """
    warnings: list[dict] = []
    for idx, stage in enumerate(stages or []):
        title = str(stage.get("title") or "")
        kcs = [str(k).strip() for k in (stage.get("key_concepts") or []) if str(k).strip()]
        for kc in kcs:
            if _is_likely_malformed_key_concept(title, kc):
                warnings.append({
                    "type": "malformed_key_concept",
                    "stage_index": idx,
                    "stage_title": title,
                    "key_concept": kc,
                    "reason": "likely_hard_truncated_title_prefix",
                })
        if _is_meta_only_key_concepts(kcs):
            warnings.append({
                "type": "meta_only_key_concepts",
                "stage_index": idx,
                "stage_title": title,
                "key_concepts": kcs,
                "reason": "all_key_concepts_are_meta_labels",
            })
    return warnings


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
            # _summary_kc_from_title strips summary suffixes + splits on genuine
            # "：" / "—" / "與" separators and returns a complete phrase (no bare
            # title[:8] hard-trim that would mangle mixed ASCII+CJK names).
            s["key_concepts"] = [_summary_kc_from_title(title)]
        else:
            s["key_concepts"] = [_SUMMARY_KC_FALLBACK]
        out.append(s)
    return out


# --- Issue B: deterministic orphan-enumerator title cleanup (same_material only) ---
# Title-only hygiene for per-source splitter naming drift: removes a leading local
# sequence marker (模式X / 主題：（X）) when the final title set has no loose "一/1"
# sibling for that series. Conservative by construction: structure / chunks /
# key_concepts are never touched, and sibling matching is intentionally LENIENT so a
# missed sibling never produces a wrong strip (preserving an orphan marker is the
# acceptable error; stripping a legitimate one is not).
_TITLE_MODE_ENUM_RE = re.compile(
    r"^\s*模式\s*(?P<num>[二三四五六七八九十])\s*[：:、\-]\s*(?P<rest>.+?)\s*$"
)
_TITLE_PREFIX_PAREN_ENUM_RE = re.compile(
    r"^\s*(?P<prefix>[^：:\n]{2,12}?)\s*[：:]\s*[（(]\s*(?P<num>[二三四五六七八九十])\s*[）)]\s*(?P<rest>.*?)\s*$"
)
# Formal chapter / rule / lesson numbering + continuation markers — never cleaned.
_TITLE_PROTECTED_RE = re.compile(
    r"(法則|第\s*[0-9一二三四五六七八九十]+\s*[章堂課節回部篇講集卷]|續|Chapter|Rule|Lesson|Part)",
    re.IGNORECASE,
)
_MODE_ONE_SIBLING_RES = [
    re.compile(r"模式\s*[一1１]"),
    re.compile(r"模式\s*[（(]\s*[一1１]\s*[）)]"),
    re.compile(r"模式\s*之\s*[一1１]"),
    re.compile(r"第\s*[一1１]\s*種?\s*模式"),
]
_TITLE_CLEANUP_MIN_LEN = 4
_TITLE_CLEANUP_BANNED = {
    "補充內容", "核心概念", "章節總結", "章節補充", "總結與補充", "模式",
}


def _title_is_protected(title: str) -> bool:
    return bool(_TITLE_PROTECTED_RE.search(title or ""))


def _has_mode_one_sibling(all_titles: list[str]) -> bool:
    return any(pat.search(t or "") for t in all_titles for pat in _MODE_ONE_SIBLING_RES)


def _has_prefix_one_sibling(prefix: str, all_titles: list[str]) -> bool:
    p = re.escape((prefix or "").strip())
    if not p:
        return False
    pats = [
        re.compile(rf"{p}\s*[：:]\s*[（(]\s*[一1１]\s*[）)]"),
        re.compile(rf"{p}\s*[：:]\s*第?\s*[一1１]"),
        re.compile(rf"{p}\s*[（(]\s*[一1１]\s*[）)]"),
        re.compile(rf"{p}\s*之\s*[一1１]"),
    ]
    return any(pat.search(t or "") for t in all_titles for pat in pats)


def _is_valid_cleaned_title(new_title: str, old_title: str) -> bool:
    t = (new_title or "").strip()
    if not t or t == (old_title or "").strip():
        return False
    if len(t) < _TITLE_CLEANUP_MIN_LEN:
        return False
    if t in _TITLE_CLEANUP_BANNED:
        return False
    return True


def cleanup_orphan_enumerator_titles(
    stages: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Issue B: title-only deterministic cleanup of orphan sequence markers.

    Removes a leading local enumerator (模式二：X / 主題：（二）X) when the final
    stage-title set has no loose 「一/1」 sibling for that series. Structure,
    source_chunk_ids, key_concepts and teaching_goal are never modified. Sibling
    matching is lenient (errs toward「sibling present → do not strip」). Intended for
    same_material curricula, which have no global naming-coordination pass.

    Returns ``(cleaned_stages, warnings)``.
    """
    all_titles = [str(s.get("title", "") or "") for s in stages]
    cleaned: list[dict] = []
    warnings: list[dict] = []

    for stage in stages:
        old_title = str(stage.get("title", "") or "")
        if _title_is_protected(old_title):
            cleaned.append(stage)
            continue

        new_title = None
        pattern = None

        m = _TITLE_MODE_ENUM_RE.match(old_title)
        if m:
            rest = m.group("rest").strip()
            if rest and not _has_mode_one_sibling(all_titles):
                new_title = rest
                pattern = "mode_cn_number"

        if new_title is None:
            m = _TITLE_PREFIX_PAREN_ENUM_RE.match(old_title)
            if m:
                prefix = m.group("prefix").strip()
                rest = m.group("rest").strip()
                candidate = f"{prefix}：{rest}" if rest else prefix
                if not _has_prefix_one_sibling(prefix, all_titles):
                    new_title = candidate
                    pattern = "prefix_paren_cn_number"

        if new_title is not None and _is_valid_cleaned_title(new_title, old_title):
            s = dict(stage)
            s["title"] = new_title
            cleaned.append(s)
            warnings.append({
                "stage_id": stage.get("stage_id"),
                "old_title": old_title,
                "new_title": new_title,
                "reason": "removed_orphan_enumerator",
                "pattern": pattern,
            })
        else:
            cleaned.append(stage)

    return cleaned, warnings


def finalize_curriculum_stages(
    stages: list[dict],
    source_chunks: list[dict],
) -> list[dict]:
    """Common post-pipeline finalize: chunk-order sort + empty-kc fallback (all paths)."""
    stages = sort_stages_by_chunk_order(stages, source_chunks)
    stages = ensure_empty_key_concepts(stages)
    # T-FOLLOWUP-ADJACENCY: the chunk-order sort above can scatter '（續 N）' follow-ups
    # away from their base (a base may aggregate an early chunk while its continuations
    # are late-only). Re-attach follow-ups to their base without disturbing base reading
    # order, then renumber so persisted stage_id / node_id match. No-op when there are
    # no follow-ups (covers same_material / flag-off / compact paths too).
    adjacent = enforce_followup_adjacency_only(stages)
    if adjacent is not stages:
        stages = _renumber_stages(adjacent)
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
    """Split excess orphans into dedicated follow-up stages instead of one mega-stage.

    P2b: title is derived from the first chunk's chapter_title / source_label
    when available, so EPUB chapter context isn't lost in the bucket.
    """
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
        # P2b: prefer chapter-aware title; fall back to generic numbering.
        first_meta = by_id.get(batch[0], {}) if batch else {}
        chap_title = first_meta.get("chapter_title") or first_meta.get("source_label") or ""
        chap_title = str(chap_title).strip()
        if chap_title:
            # cap length and dedupe across batches by appending batch num only when many overflows
            short = chap_title[:14]
            title = f"補充：{short}" if batch_num == 1 else f"補充：{short}（{batch_num}）"
        else:
            title = f"補充段落（{batch_num}）"
        out.append({
            "stage_id": next_stage_id,
            "node_id": f"orphan.{batch_num}",
            "title": title,
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


def fold_interior_orphan_chunks(
    stages: list[dict],
    source_chunks: list[dict],
) -> list[dict]:
    """Fold orphan chunks that sit *inside* the reading span into the adjacent stage.

    An orphan is "interior" when some stage owns a chunk that comes after it in
    reading order — it reads as part of the surrounding narrative, so it belongs in
    a neighbouring content stage rather than a generic summary/filler stage.
    Preference: the preceding content stage (largest max-order ≤ orphan order); if
    none exists, the nearest following content stage. Intro/framework stages are
    skipped as targets so the overview isn't bloated. Trailing orphans (after every
    stage's chunks) are left untouched — those are genuine closing remarks handled
    by the summary path.

    sess_vtfl3q4il: chunk_0008 sat between stage [5,6,7] and stage [9]; this folds it
    into the [5,6,7] stage instead of spawning a「章節總結與補充內容」filler stage.
    """
    if not stages or not source_chunks:
        return stages

    order = {
        c["chunk_id"]: int(c.get("order_index", 0))
        for c in source_chunks
        if isinstance(c, dict) and c.get("chunk_id")
    }
    referenced: set[str] = set()
    for s in stages:
        referenced.update(s.get("source_chunk_ids") or [])
    orphans = sorted(
        (cid for cid in order if cid not in referenced),
        key=lambda cid: order[cid],
    )
    if not orphans:
        return stages

    by_id = chunks_lookup(source_chunks)
    out = [dict(s) for s in stages]

    def _smax(s: dict) -> int:
        ids = s.get("source_chunk_ids") or []
        return max((order.get(c, -1) for c in ids), default=-1)

    def _smin(s: dict) -> int:
        ids = s.get("source_chunk_ids") or []
        return min((order.get(c, 10**9) for c in ids), default=10**9)

    changed = False
    for cid in orphans:
        oidx = order[cid]
        if not any(_smax(s) > oidx for s in out):
            continue  # trailing orphan — leave for the summary path

        target_i: int | None = None
        # 1. a stage whose chunk span already brackets the orphan position owns it
        #    (e.g. chunk_0008 between 1.2's chunk_0007 and chunk_0009)
        for i, s in enumerate(out):
            if _is_intro_framework_stage(s) or not s.get("source_chunk_ids"):
                continue
            if _smin(s) < oidx < _smax(s):
                target_i = i
                break

        # 2. else the preceding content stage (largest max-order ≤ orphan order)
        best_max = -1
        if target_i is None:
            for i, s in enumerate(out):
                if _is_intro_framework_stage(s) or not s.get("source_chunk_ids"):
                    continue
                smax = _smax(s)
                if smax <= oidx and smax > best_max:
                    best_max = smax
                    target_i = i

        if target_i is None:  # no preceding content stage → nearest following
            best_min = 10**9
            for i, s in enumerate(out):
                if _is_intro_framework_stage(s) or not s.get("source_chunk_ids"):
                    continue
                smin = _smin(s)
                if smin > oidx and smin < best_min:
                    best_min = smin
                    target_i = i

        if target_i is None:
            continue
        out[target_i] = _attach_chunk_to_stage(out[target_i], cid, by_id)
        changed = True

    return trim_stage_key_concepts(out) if changed else stages


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
