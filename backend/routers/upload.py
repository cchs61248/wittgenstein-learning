from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, HttpUrl

from ..auth.utils import decode_token_active
from ..files.upload_store import save_upload
from ..utils.url_fetcher import fetch_url_content

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
    if not await decode_token_active(authorization.removeprefix("Bearer ")):
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


class UrlUploadRequest(BaseModel):
    url: str


@router.post("/url")
async def upload_url(
    body: UrlUploadRequest,
    authorization: Optional[str] = Header(None),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未授權")
    if not await decode_token_active(authorization.removeprefix("Bearer ")):
        raise HTTPException(status_code=401, detail="Token 無效")

    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="請提供完整的 http/https 網址")

    try:
        title, text = fetch_url_content(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"擷取失敗：{e}")

    if not text.strip():
        raise HTTPException(status_code=422, detail="無法從該網址擷取到文字內容")

    raw = text.encode("utf-8")
    file_id = save_upload(
        filename=f"{title[:80]}.txt",
        mime_type="text/plain; charset=utf-8",
        raw=raw,
        extra_meta={"source_url": url, "source_type": "url"},
    )

    return {
        "file_id": file_id,
        "title": title,
        "url": url,
        "char_count": len(text),
    }
