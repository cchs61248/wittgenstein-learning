import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..utils.text_extractor import extract_text


_ROOT = Path(__file__).resolve().parent.parent.parent
UPLOAD_DIR = _ROOT / "data" / "uploads"
_META_SUFFIX = ".meta.json"
_TEXT_SUFFIX = ".text"
PLAIN_SUFFIXES = {".txt", ".md"}
BINARY_SUFFIXES = {".pdf", ".docx", ".pptx", ".html", ".htm", ".epub"}


def _ensure_dir() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def is_plain_upload(filename: str) -> bool:
    return Path(filename).suffix.lower() in PLAIN_SUFFIXES


def _blob_path(file_id: str) -> Path:
    return UPLOAD_DIR / f"{file_id}.bin"


def _meta_path(file_id: str) -> Path:
    return UPLOAD_DIR / f"{file_id}{_META_SUFFIX}"


def _text_path(file_id: str) -> Path:
    return UPLOAD_DIR / f"{file_id}{_TEXT_SUFFIX}"


def iter_upload_metas() -> list[tuple[str, dict]]:
    """回傳 (file_id, meta_dict)；不含 raw bytes。"""
    _ensure_dir()
    items: list[tuple[str, dict]] = []
    for meta_path in UPLOAD_DIR.glob(f"upl_*{_META_SUFFIX}"):
        file_id = meta_path.name[: -len(_META_SUFFIX)]
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        items.append((file_id, meta))
    return items


def load_upload_meta(file_id: str) -> dict:
    meta_path = _meta_path(file_id)
    if not meta_path.exists():
        raise FileNotFoundError(file_id)
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _write_meta(file_id: str, meta: dict) -> None:
    _meta_path(file_id).write_text(
        json.dumps(meta, ensure_ascii=False),
        encoding="utf-8",
    )


def _validate_char_count(text: str, max_chars: int) -> int:
    count = len(text)
    if count == 0:
        raise ValueError("無法從檔案抽取到文字內容")
    if count > max_chars:
        raise ValueError(f"解析後文字超過 {max_chars:,} 字上限（目前 {count:,} 字）")
    return count


def save_upload_plain(
    filename: str,
    mime_type: str,
    raw: bytes,
    text: str,
    *,
    max_chars: int,
    extra_meta: dict | None = None,
) -> tuple[str, int]:
    """純文字上傳：.bin 即 UTF-8 文字，不另建 .text sidecar。"""
    char_count = _validate_char_count(text, max_chars)
    _ensure_dir()
    file_id = f"upl_{uuid.uuid4().hex}"
    _blob_path(file_id).write_bytes(raw)
    meta = {
        "file_id": file_id,
        "filename": filename,
        "mime_type": mime_type or "text/plain; charset=utf-8",
        "size": len(raw),
        "char_count": char_count,
        "storage_kind": "plain",
        "text_ready": True,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra_meta:
        meta.update(extra_meta)
    _write_meta(file_id, meta)
    return file_id, char_count


def save_upload_binary(
    filename: str,
    mime_type: str,
    raw: bytes,
    text: str,
    *,
    max_chars: int,
    extra_meta: dict | None = None,
) -> tuple[str, int]:
    """二進位上傳：.bin 存原始檔，.text 存解析後 UTF-8 純文字。"""
    char_count = _validate_char_count(text, max_chars)
    _ensure_dir()
    file_id = f"upl_{uuid.uuid4().hex}"
    _blob_path(file_id).write_bytes(raw)
    _text_path(file_id).write_text(text, encoding="utf-8")
    meta = {
        "file_id": file_id,
        "filename": filename,
        "mime_type": mime_type or "application/octet-stream",
        "size": len(raw),
        "char_count": char_count,
        "storage_kind": "binary",
        "text_ready": True,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra_meta:
        meta.update(extra_meta)
    _write_meta(file_id, meta)
    return file_id, char_count


def save_upload(
    filename: str,
    mime_type: str,
    raw: bytes,
    extra_meta: dict | None = None,
) -> str:
    """Legacy 入口：只寫 .bin + meta，不解析。新程式碼請用 save_upload_plain/binary。"""
    _ensure_dir()
    file_id = f"upl_{uuid.uuid4().hex}"
    _blob_path(file_id).write_bytes(raw)
    meta = {
        "file_id": file_id,
        "filename": filename,
        "mime_type": mime_type or "application/octet-stream",
        "size": len(raw),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra_meta:
        meta.update(extra_meta)
    _write_meta(file_id, meta)
    return file_id


def load_upload(file_id: str) -> dict:
    blob_path = _blob_path(file_id)
    meta_path = _meta_path(file_id)
    if not blob_path.exists() or not meta_path.exists():
        raise FileNotFoundError(file_id)

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["raw"] = blob_path.read_bytes()
    return meta


def load_upload_text(file_id: str) -> str:
    """讀取可供 chunk 使用的純文字；purge 後或檔案缺失時 raise FileNotFoundError。"""
    meta = load_upload_meta(file_id)
    if meta.get("purged"):
        raise FileNotFoundError(file_id)

    storage_kind = meta.get("storage_kind")
    text_path = _text_path(file_id)
    blob_path = _blob_path(file_id)

    if storage_kind == "binary" or (storage_kind is None and text_path.exists()):
        if not text_path.exists():
            raise FileNotFoundError(file_id)
        return text_path.read_text(encoding="utf-8")

    if storage_kind == "plain" or blob_path.exists():
        if not blob_path.exists():
            raise FileNotFoundError(file_id)
        return blob_path.read_bytes().decode("utf-8")

    # Legacy：無 storage_kind 且無 .text
    uploaded = load_upload(file_id)
    return extract_text(uploaded["filename"], uploaded["raw"])


def delete_upload(file_id: str) -> bool:
    """刪除 .bin、.text sidecar 與 meta。冪等。"""
    removed = False
    for p in (_blob_path(file_id), _text_path(file_id), _meta_path(file_id)):
        try:
            p.unlink()
            removed = True
        except FileNotFoundError:
            pass
    return removed


def purge_upload_files(file_id: str) -> bool:
    """chunk 寫入 DB 後釋放磁碟；語意同 delete_upload。"""
    return delete_upload(file_id)
