"""upload_gc 與 abandon_generating_stub 清理行為測試。"""
import json
import sqlite3
from pathlib import Path

import pytest

from backend.files.upload_gc import collect_referenced_file_ids, gc_unreferenced_uploads
from backend.files.upload_store import save_upload


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    upload_path = tmp_path / "uploads"
    upload_path.mkdir()
    monkeypatch.setattr("backend.files.upload_store.UPLOAD_DIR", upload_path)
    monkeypatch.setattr("backend.files.upload_gc.UPLOAD_DIR", upload_path)
    return upload_path


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            source_file_ids_json TEXT DEFAULT '[]'
        )"""
    )
    conn.commit()
    conn.close()
    return path


def test_gc_deletes_unreferenced_only(upload_dir, db_path):
    keep_id = save_upload("keep.txt", "text/plain", b"keep")
    orphan_id = save_upload("orphan.txt", "text/plain", b"orphan")

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO sessions (session_id, source_file_ids_json) VALUES (?, ?)",
        ("s1", json.dumps([keep_id])),
    )
    conn.commit()
    conn.close()

    result = gc_unreferenced_uploads(db_path, max_age_hours=0)
    assert result["deleted_count"] == 1
    assert orphan_id in result["deleted_ids"]
    assert keep_id not in result["deleted_ids"]
    assert (upload_dir / f"{keep_id}.bin").exists()
    assert not (upload_dir / f"{orphan_id}.bin").exists()


def test_gc_respects_max_age_for_recent_orphans(upload_dir, db_path, monkeypatch):
    from datetime import datetime, timezone

    orphan_id = save_upload("recent.txt", "text/plain", b"x")
    meta_path = upload_dir / f"{orphan_id}.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["uploaded_at"] = datetime.now(timezone.utc).isoformat()
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    result = gc_unreferenced_uploads(db_path, max_age_hours=24)
    assert result["deleted_count"] == 0
    assert (upload_dir / f"{orphan_id}.bin").exists()


def test_collect_referenced_file_ids(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO sessions (session_id, source_file_ids_json) VALUES (?, ?)",
        ("s1", json.dumps(["upl_a", "upl_b"])),
    )
    conn.execute(
        "INSERT INTO sessions (session_id, source_file_ids_json) VALUES (?, ?)",
        ("s2", json.dumps(["upl_b", "upl_c"])),
    )
    conn.commit()
    conn.close()

    refs = collect_referenced_file_ids(db_path)
    assert refs == {"upl_a", "upl_b", "upl_c"}
