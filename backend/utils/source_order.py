"""SourceOrderResolver — 同教材多 source 依確定性章節訊號重排（Phase 2）。"""
from __future__ import annotations

import logging
import re

_log = logging.getLogger("wl.source_order")

_CN_DIGIT = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _cn_chapter_no(s: str) -> int | None:
    """中文數字 一..二十 → int；超出範圍或無法解析 → None。"""
    s = (s or "").strip()
    if not s:
        return None
    if s in _CN_DIGIT:
        return _CN_DIGIT[s]
    if len(s) == 2 and s[0] == "十" and s[1] in _CN_DIGIT and _CN_DIGIT[s[1]] < 10:
        return 10 + _CN_DIGIT[s[1]]  # 十一..十九
    if s == "二十":
        return 20
    return None


_EXT_RE = re.compile(r"\.(pdf|txt|md|markdown|epub|docx?|rtf)$", re.IGNORECASE)
# 「第」前綴可選：實務上「2章」「1章」常省略「第」（live sess_j1ilhdohb）
_FN_CN_RE = re.compile(r"第?\s*([一二三四五六七八九十百\d]+)\s*[章節篇課堂]")
_FN_CHAPTER_RE = re.compile(r"(?:chapter|ch)\.?\s*(\d+)", re.IGNORECASE)
_FN_PART_RE = re.compile(r"part\s*(\d+)", re.IGNORECASE)
_FN_PAREN_RE = re.compile(r"[（(](\d+)[）)]")
_FN_TAIL_RE = re.compile(r"(\d+)\s*$")


def _arabic_or_cn(num_str: str) -> int | None:
    if num_str.isdigit():
        return int(num_str)
    return _cn_chapter_no(num_str)


def _detect_from_filename(label: str) -> tuple[int, str] | None:
    """去副檔名後依序試 pattern，第一個命中即停。回傳 (chapter_no, 'filename_regex')。"""
    name = _EXT_RE.sub("", label or "").strip()
    if not name:
        return None
    m = _FN_CN_RE.search(name)
    if m:
        n = _arabic_or_cn(m.group(1))
        if n is not None:
            return (n, "filename_regex")
    m = _FN_CHAPTER_RE.search(name)
    if m:
        return (int(m.group(1)), "filename_regex")
    m = _FN_PART_RE.search(name)
    if m:
        return (int(m.group(1)), "filename_regex")
    m = _FN_PAREN_RE.search(name)  # 括號數字優先於裸結尾數字
    if m:
        return (int(m.group(1)), "filename_regex")
    m = _FN_TAIL_RE.search(name)
    if m:
        return (int(m.group(1)), "filename_regex")
    return None


# 「第」前綴可選（同 _FN_CN_RE）：內文標題「2章」「1章」也須命中
_HEADING_CN_RE = re.compile(r"第?\s*([一二三四五六七八九十百\d]+)\s*[章節]")


def _detect_from_heading(text: str) -> tuple[int, str] | None:
    """掃前 500 字內第一個「第X章/第X節」標題（章節標題位於開頭，500 字足夠）。"""
    head = (text or "")[:500]
    m = _HEADING_CN_RE.search(head)
    if m:
        n = _arabic_or_cn(m.group(1))
        if n is not None:
            return (n, "content_heading")
    return None


def _detect_chapter_no(info: dict) -> tuple[int | None, str | None]:
    """單一 source 的 cascade：epub_chapter_index → filename → content_heading。"""
    hint = info.get("chapter_hint") or {}
    ci = hint.get("chapter_index")
    if ci is not None:
        try:
            return (int(ci), "epub_chapter_index")
        except (TypeError, ValueError):
            pass
    fn = _detect_from_filename(info.get("label") or "")
    if fn is not None:
        return fn
    hd = _detect_from_heading(info.get("text") or "")
    if hd is not None:
        return hd
    return (None, None)


def resolve_source_order(source_infos: list[dict]) -> tuple[list[dict], dict]:
    """同教材多 source 依確定性章節訊號重排，回傳 (reordered_infos, OrderDecision)。

    保守：全部 source 都有相異 chapter_no 才重排；否則維持上傳序。任何例外吞掉、回原序。
    OrderDecision = {applied, certain, signal: list[str]|None, order: list[str]|None, reason}
    """
    try:
        detected = [(info, *_detect_chapter_no(info)) for info in source_infos]
        nos = [no for _, no, _ in detected]
        all_have = all(n is not None for n in nos)
        distinct = len(set(nos)) == len(nos)

        if all_have and distinct:
            ordered = sorted(detected, key=lambda t: t[1])
            # 字母序，僅供 debug/觀察；不代表訊號優先級
            signals = sorted({sig for _, _, sig in ordered})
            return (
                [info for info, _, _ in ordered],
                {"applied": True, "certain": True, "signal": signals,
                 "order": [info.get("label") for info, _, _ in ordered],
                 "reason": None},
            )

        if not all_have:
            n_missing = sum(1 for n in nos if n is None)
            reason = "missing chapter_no for %d/%d sources" % (n_missing, len(nos))
        else:
            reason = "duplicate chapter_no detected"
        return (list(source_infos),
                {"applied": False, "certain": False, "signal": None,
                 "order": None, "reason": reason})
    except Exception as e:  # 排序邏輯絕不擋下 ingest
        _log.warning("resolve_source_order error: %s", e)
        return (list(source_infos),
                {"applied": False, "certain": False, "signal": None,
                 "order": None, "reason": "resolver_error: %s" % e})
