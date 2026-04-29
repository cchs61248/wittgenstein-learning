from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Header, HTTPException, UploadFile

from ..auth.utils import decode_token
from ..files.upload_store import save_upload

router = APIRouter(prefix="/upload", tags=["upload"])

_ALLOWED = {".txt", ".md", ".pdf", ".docx", ".doc", ".pptx", ".html", ".htm"}
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post("")
async def upload_file(
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(None),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未授權")
    if not decode_token(authorization.removeprefix("Bearer ")):
        raise HTTPException(status_code=401, detail="Token 無效")

    filename = file.filename or "unknown"
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED:
        raise HTTPException(
            status_code=400,
            detail=f"不支援的格式：{suffix}，請上傳 .txt .md .pdf .docx",
        )

    raw = await file.read()
    if len(raw) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="檔案超過 10 MB 上限")

    file_id = save_upload(
        filename=filename,
        mime_type=file.content_type or "application/octet-stream",
        raw=raw,
    )

    return {
        "file_id": file_id,
        "filename": filename,
        "size": len(raw),
        "mime_type": file.content_type or "application/octet-stream",
    }
