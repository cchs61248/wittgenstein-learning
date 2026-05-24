"""Global curriculum coverage verifier."""
from __future__ import annotations

import re
from typing import Any

from ..utils.fuzzy_match import concept_overlap_score, similarity
from ..utils.small_curriculum import (
    compact_orphan_limit,
    filter_missing_named_cases,
)


_ENUM_PREFIX_RE = re.compile(r"[（(][一二三四五六七八九十\d]+[）)]")


def _topic_core(title: str) -> str:
    """提取標題的「主題核心名詞」：剝編號後，取冒號 / 連字號分隔的最後一段，
    並截掉「與 / 及 / 和」之後的修飾尾。

    範例：
    - 「借錢工具選型（一）：信用貸款」 → 「信用貸款」
    - 「借錢工具（二）：信用貸款與波浪操作」 → 「信用貸款」
    - 「借錢工具選型（二）：房屋貸款」 → 「房屋貸款」
    """
    s = _ENUM_PREFIX_RE.sub("", title).strip()
    # 取最後一個冒號 / 破折號後的部分（main topic）
    for sep in ("：", ":", "—", "—", "-"):
        if sep in s:
            s = s.rsplit(sep, 1)[-1].strip()
    # 截掉「與 / 及 / 和」修飾尾（保留前端主題名）
    for conj in ("與", "及", "和"):
        if conj in s:
            s = s.split(conj, 1)[0].strip()
    return s


def _duplicate_titles(
    stages: list[dict],
    threshold: float = 0.92,
    kc_overlap_threshold: float = 0.6,
) -> list[str]:
    """偵測重複 stage：
    (a) 標題字面相似度 ≥ 0.92（沿用原邏輯）
    (b) 「（N）」編號剝離後主題核心名詞相同 + key_concepts overlap ≥ 0.6
        — 處理 sess_live_2834df87 「（一）信用貸款」+「（二）信用貸款與波浪操作」
        這種同主題不同編號的 splitter 過拆失敗。
    """
    dupes: list[str] = []
    titles = [(s.get("title") or "").strip() for s in stages]
    cores = [_topic_core(t) for t in titles]
    for i in range(len(titles)):
        if not titles[i]:
            continue
        for j in range(i + 1, len(titles)):
            if not titles[j]:
                continue
            if (
                stages[i].get("kind") == "follow_up_orphan"
                and stages[j].get("kind") == "follow_up_orphan"
            ):
                continue
            if similarity(titles[i], titles[j]) >= threshold:
                dupes.append(f"{titles[i]} ~ {titles[j]}")
                continue
            if cores[i] and cores[i] == cores[j]:
                kc_i = [str(x) for x in stages[i].get("key_concepts") or []]
                kc_j = [str(x) for x in stages[j].get("key_concepts") or []]
                if concept_overlap_score(kc_i, kc_j) >= kc_overlap_threshold:
                    dupes.append(f"{titles[i]} ~ {titles[j]} (enum-stripped)")
    return dupes


def verify_global_coverage(
    stages: list[dict],
    source_chunks: list[dict],
    required_outline: dict | None = None,
) -> dict[str, Any]:
    referenced: set[str] = set()
    for s in stages:
        for cid in s.get("source_chunk_ids") or []:
            referenced.add(cid)
    all_ids = {c["chunk_id"] for c in source_chunks}
    orphans = sorted(all_ids - referenced)
    outline = required_outline or {}
    named_cases = [str(c) for c in outline.get("named_cases") or []]
    missing_cases = filter_missing_named_cases(named_cases, stages, source_chunks)
    duplicate_titles = _duplicate_titles(stages)
    orphan_limit = compact_orphan_limit(source_chunks)
    aligned = (
        not missing_cases
        and not duplicate_titles
        and len(orphans) <= orphan_limit
    )
    issues: list[str] = []
    if missing_cases:
        issues.append(f"missing named_cases: {missing_cases}")
    if duplicate_titles:
        issues.append(f"duplicate titles: {duplicate_titles[:5]}")
    if len(orphans) > orphan_limit:
        issues.append(f"orphan chunks: {len(orphans)} > {orphan_limit}")
    return {
        "aligned": aligned,
        "missing_options": missing_cases,
        "duplicate_titles": duplicate_titles,
        "orphan_chunk_ids": orphans[:20],
        "reason": "; ".join(issues) or "global coverage check",
    }
