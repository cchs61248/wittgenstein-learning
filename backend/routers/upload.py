from pathlib import Path
import asyncio
import json
import re
import time
import zipfile
from typing import Optional

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from pydantic import BaseModel
from fastapi.responses import JSONResponse, StreamingResponse

from ..auth.utils import decode_token_active, get_role_by_email
from ..config import UPLOAD_MAX_CHAR_COUNT, UPLOAD_MAX_FILE_MB
from ..files.upload_store import (
    is_plain_upload,
    save_upload_binary,
    save_upload_plain,
)
from ..utils.epub_splitter import split_epub_by_toc
from ..utils.text_extractor import extract_text
from ..utils.url_fetcher import YoutubeTranscriptUnavailable, fetch_url_content
from ..utils.logger import fmt_elapsed, ingest_logger, text_preview

router = APIRouter(prefix="/upload", tags=["upload"])
_log = ingest_logger()

_ALLOWED = {".txt", ".md", ".pdf", ".docx", ".pptx", ".html", ".htm", ".epub"}
_MAX_BYTES = UPLOAD_MAX_FILE_MB * 1024 * 1024


def _sanitize_chapter_filename(name: str, max_len: int = 80) -> str:
    """章節檔名清洗：移除 Windows 不合法字元、壓縮空白、截長。

    來源：aaron/epub/split_epub.py 同名函式（保留在 upload.py，依 plan §4.1）。
    """
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return (name or "untitled")[:max_len]


async def _require_admin_or_403(authorization: Optional[str]) -> None:
    """驗 token 且角色須為 admin，否則拋對應 HTTPException。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未授權")
    payload = await decode_token_active(authorization.removeprefix("Bearer "))
    if not payload:
        raise HTTPException(status_code=401, detail="Token 無效")
    if await get_role_by_email(payload.get("email", "")) != "admin":
        raise HTTPException(status_code=403, detail="僅管理員可新增教材")


def _save_parsed_upload(
    filename: str,
    mime_type: str,
    raw: bytes,
    text: str,
    extra_meta: dict | None = None,
) -> tuple[str, int]:
    if is_plain_upload(filename):
        return save_upload_plain(
            filename,
            mime_type,
            raw,
            text,
            max_chars=UPLOAD_MAX_CHAR_COUNT,
            extra_meta=extra_meta,
        )
    return save_upload_binary(
        filename,
        mime_type,
        raw,
        text,
        max_chars=UPLOAD_MAX_CHAR_COUNT,
        extra_meta=extra_meta,
    )


@router.post("")
async def upload_file(
    file: UploadFile = File(...),
    authorization: Optional[str] = Header(None),
):
    await _require_admin_or_403(authorization)

    filename = file.filename or "unknown"
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED:
        raise HTTPException(
            status_code=400,
            detail=f"不支援的格式：{suffix}，請上傳 .txt .md .pdf .docx .pptx .html .htm .epub",
        )

    raw = await file.read()
    if len(raw) > _MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"檔案超過 {UPLOAD_MAX_FILE_MB} MB 上限",
        )

    mime_type = file.content_type or "application/octet-stream"

    if suffix == ".epub":
        try:
            chapters = await asyncio.to_thread(split_epub_by_toc, raw)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except (KeyError, zipfile.BadZipFile, UnicodeDecodeError) as e:
            raise HTTPException(status_code=422, detail=f"EPUB 解析失敗：{e}")

        epub_chapters: list[dict] = []
        for idx, (title, chapter_text) in enumerate(chapters, 1):
            safe_title = _sanitize_chapter_filename(title)
            chapter_name = f"{idx:03d}_{safe_title}.txt"
            chunk_bytes = chapter_text.encode("utf-8")
            meta = {
                "source_type": "epub_chapter",
                "epub_filename": filename,
                "chapter_index": idx,
                "chapter_title": title,
            }
            try:
                chap_file_id, chap_chars = await asyncio.to_thread(
                    lambda: _save_parsed_upload(
                        chapter_name,
                        "text/plain; charset=utf-8",
                        chunk_bytes,
                        chapter_text,
                        extra_meta=meta,
                    )
                )
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))
            epub_chapters.append({
                "file_id": chap_file_id,
                "filename": chapter_name,
                "char_count": chap_chars,
                "size": len(chunk_bytes),
                "mime_type": "text/plain; charset=utf-8",
            })
            _log.info(
                "upload EPUB chapter  parent=%s  chapter=%s  chars=%d",
                filename, chapter_name, chap_chars,
            )
        return {
            "epub_chapters": epub_chapters,
            "total_chapters": len(epub_chapters),
            "parent_filename": filename,
        }

    try:
        t0 = time.perf_counter()
        text = await asyncio.to_thread(extract_text, filename, raw)
        elapsed = fmt_elapsed(t0)
        file_id, char_count = await asyncio.to_thread(
            _save_parsed_upload,
            filename,
            mime_type,
            raw,
            text,
        )
        if suffix == ".pdf":
            _log.info(
                "upload PDF → markdown  file_id=%s  file=%s  bytes=%d  chars=%d  "
                "elapsed=%s  preview=%r",
                file_id,
                filename,
                len(raw),
                char_count,
                elapsed,
                text_preview(text),
            )
        else:
            _log.info(
                "upload extracted  file_id=%s  file=%s  suffix=%s  bytes=%d  "
                "chars=%d  elapsed=%s",
                file_id,
                filename,
                suffix,
                len(raw),
                char_count,
                elapsed,
            )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"無法解析檔案內容：{e}")

    return {
        "file_id": file_id,
        "filename": filename,
        "size": len(raw),
        "char_count": char_count,
        "mime_type": mime_type,
    }


class UrlUploadRequest(BaseModel):
    url: str


@router.post("/url")
async def upload_url(
    body: UrlUploadRequest,
    authorization: Optional[str] = Header(None),
):
    await _require_admin_or_403(authorization)

    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="請提供完整的 http/https 網址")

    try:
        title, text = fetch_url_content(url, youtube_asr_mode="defer")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except YoutubeTranscriptUnavailable as e:
        return JSONResponse(
            status_code=409,
            content={
                "asr_required": True,
                "video_id": e.video_id,
                "url": e.original_url,
                "title": f"YouTube 影片（{e.video_id}）",
                "reason": e.transcript_error,
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"擷取失敗：{e}")

    if not text.strip():
        raise HTTPException(status_code=422, detail="無法從該網址擷取到文字內容")

    raw = text.encode("utf-8")
    try:
        file_id, char_count = _save_parsed_upload(
            filename=f"{title[:80]}.txt",
            mime_type="text/plain; charset=utf-8",
            raw=raw,
            text=text,
            extra_meta={"source_url": url, "source_type": "url"},
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {
        "file_id": file_id,
        "title": title,
        "url": url,
        "char_count": char_count,
    }


class YoutubeAsrRequest(BaseModel):
    url: str


@router.post("/youtube/asr/stream")
async def youtube_asr_stream(
    body: YoutubeAsrRequest,
    authorization: Optional[str] = Header(None),
):
    await _require_admin_or_403(authorization)

    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="請提供完整的 http/https 網址")

    loop = asyncio.get_running_loop()
    q: asyncio.Queue[dict] = asyncio.Queue()

    def _push(msg: dict) -> None:
        loop.call_soon_threadsafe(q.put_nowait, msg)

    progress_callback = lambda stage, pct: _push(
        {
            "type": "progress",
            "stage": stage,
            "progress": pct,
        }
    )

    def _run_asr() -> None:
        try:
            title, text = fetch_url_content(url, youtube_asr_mode="auto", progress_callback=progress_callback)
            raw = text.encode("utf-8")
            file_id, char_count = _save_parsed_upload(
                filename=f"{title[:80]}.txt",
                mime_type="text/plain; charset=utf-8",
                raw=raw,
                text=text,
                extra_meta={"source_url": url, "source_type": "url"},
            )
            _push(
                {
                    "type": "done",
                    "file_id": file_id,
                    "title": title,
                    "url": url,
                    "char_count": char_count,
                }
            )
        except ValueError as e:
            _push({"type": "error", "message": str(e)})
        except Exception as e:
            _push({"type": "error", "message": str(e)})

    async def _gen():
        worker = asyncio.create_task(asyncio.to_thread(_run_asr))
        try:
            while True:
                msg = await q.get()
                yield json.dumps(msg, ensure_ascii=False) + "\n"
                if msg.get("type") in {"done", "error"}:
                    break
            await worker
        finally:
            if not worker.done():
                worker.cancel()

    return StreamingResponse(_gen(), media_type="application/x-ndjson")
