import json
import uuid
from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent.parent
_UPLOAD_DIR = _ROOT / "data" / "uploads"
_META_SUFFIX = ".meta.json"


def _ensure_dir() -> None:
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def save_upload(
    filename: str,
    mime_type: str,
    raw: bytes,
    extra_meta: dict | None = None,
) -> str:
    _ensure_dir()
    file_id = f"upl_{uuid.uuid4().hex}"
    blob_path = _UPLOAD_DIR / f"{file_id}.bin"
    meta_path = _UPLOAD_DIR / f"{file_id}{_META_SUFFIX}"

    blob_path.write_bytes(raw)
    meta = {
        "file_id": file_id,
        "filename": filename,
        "mime_type": mime_type or "application/octet-stream",
        "size": len(raw),
    }
    if extra_meta:
        meta.update(extra_meta)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return file_id


def load_upload(file_id: str) -> dict:
    _ensure_dir()
    blob_path = _UPLOAD_DIR / f"{file_id}.bin"
    meta_path = _UPLOAD_DIR / f"{file_id}{_META_SUFFIX}"
    if not blob_path.exists() or not meta_path.exists():
        raise FileNotFoundError(file_id)

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["raw"] = blob_path.read_bytes()
    return meta


def delete_upload(file_id: str) -> bool:
    """刪除磁碟上的 upload blob 與 meta。回傳是否確實刪到任一檔案。"""
    blob_path = _UPLOAD_DIR / f"{file_id}.bin"
    meta_path = _UPLOAD_DIR / f"{file_id}{_META_SUFFIX}"
    removed = False
    for p in (blob_path, meta_path):
        try:
            p.unlink()
            removed = True
        except FileNotFoundError:
            pass
    return removed
