"""upload_store 純文字 sidecar 與 load_upload_text 測試。"""
import json

import pytest

from backend.files.upload_store import (
    delete_upload,
    load_upload_text,
    save_upload_binary,
    save_upload_plain,
)


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.files.upload_store.UPLOAD_DIR", tmp_path)
    return tmp_path


def test_save_binary_and_load_text(upload_dir):
    fid, count = save_upload_binary(
        "book.epub",
        "application/epub+zip",
        b"raw-bytes",
        "hello world",
        max_chars=500_000,
    )
    assert count == 11
    assert load_upload_text(fid) == "hello world"
    assert (upload_dir / f"{fid}.text").exists()
    assert delete_upload(fid)


def test_save_plain_uses_bin_only(upload_dir):
    raw = "純文字內容".encode("utf-8")
    fid, count = save_upload_plain(
        "note.txt",
        "text/plain",
        raw,
        "純文字內容",
        max_chars=500_000,
    )
    assert count == 5
    assert not (upload_dir / f"{fid}.text").exists()
    assert load_upload_text(fid) == "純文字內容"
    delete_upload(fid)


def test_char_count_limit(upload_dir):
    with pytest.raises(ValueError, match="500,000"):
        save_upload_binary(
            "big.pdf",
            "application/pdf",
            b"x",
            "a" * 500_001,
            max_chars=500_000,
        )


def test_empty_text_rejected(upload_dir):
    with pytest.raises(ValueError, match="無法從檔案抽取"):
        save_upload_plain("empty.txt", "text/plain", b"", "", max_chars=500_000)


def test_delete_upload_removes_all_artifacts(upload_dir):
    fid, _ = save_upload_binary(
        "a.epub", "application/epub+zip", b"1", "text", max_chars=500_000
    )
    delete_upload(fid)
    assert not list(upload_dir.iterdir())
