"""Upload blob 垃圾回收：清理未被任何 session 引用的孤兒檔案。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .upload_store import UPLOAD_DIR, delete_upload, iter_upload_metas  # noqa: F401  # UPLOAD_DIR re-exported as test monkeypatch target


def _parse_uploaded_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def collect_referenced_file_ids(db_path: str | Path) -> set[str]:
    """從 sessions.source_file_ids_json 收集所有仍被引用的 file_id。"""
    referenced: set[str] = set()
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("SELECT source_file_ids_json FROM sessions")
        for (raw,) in cur.fetchall():
            try:
                ids = json.loads(raw or "[]")
            except Exception:
                continue
            if not isinstance(ids, list):
                continue
            for fid in ids:
                if isinstance(fid, str) and fid:
                    referenced.add(fid)
    finally:
        conn.close()
    return referenced


def find_gc_candidates(
    referenced: set[str],
    *,
    max_age_hours: float | None = None,
) -> list[str]:
    """
    回傳可 GC 的 file_id 列表。

    - 不在 referenced 集合內
    - 若 max_age_hours 有值，僅包含 uploaded_at 早於該時限者；
      無 uploaded_at 的舊檔視為可立即 GC
    """
    now = datetime.now(timezone.utc)
    candidates: list[str] = []
    for file_id, meta in iter_upload_metas():
        if file_id in referenced:
            continue
        if max_age_hours is not None:
            uploaded_at = _parse_uploaded_at(meta.get("uploaded_at"))
            if uploaded_at is not None:
                age_hours = (now - uploaded_at).total_seconds() / 3600
                if age_hours < max_age_hours:
                    continue
        candidates.append(file_id)
    return candidates


def gc_unreferenced_uploads(
    db_path: str | Path,
    *,
    max_age_hours: float | None = 0,
    dry_run: bool = False,
) -> dict:
    """
    刪除未被 session 引用的 upload blob。

    max_age_hours:
      - 0 或 None：立即刪除所有孤兒（啟動一次性清理用）
      - >0：僅刪除超過該時數的孤兒（避免誤刪剛上傳、尚未開 session 的檔案）
    """
    referenced = collect_referenced_file_ids(db_path)
    age_limit = None if max_age_hours in (None, 0) else max_age_hours
    candidates = find_gc_candidates(referenced, max_age_hours=age_limit)
    deleted: list[str] = []
    for fid in candidates:
        if dry_run:
            deleted.append(fid)
            continue
        if delete_upload(fid):
            deleted.append(fid)
    return {
        "referenced_count": len(referenced),
        "candidate_count": len(candidates),
        "deleted_count": len(deleted),
        "deleted_ids": deleted,
        "dry_run": dry_run,
        "max_age_hours": max_age_hours,
    }
