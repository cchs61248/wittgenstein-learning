import io
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Header, HTTPException, UploadFile

from ..auth.utils import decode_token

router = APIRouter(prefix="/upload", tags=["upload"])

_ALLOWED = {".txt", ".md", ".pdf", ".docx", ".doc"}
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

    content = _extract_text(raw, suffix)
    if not content.strip():
        raise HTTPException(status_code=422, detail="無法從檔案中提取文字內容")

    return {"content": content, "filename": filename, "char_count": len(content)}


def _extract_text(raw: bytes, suffix: str) -> str:
    if suffix in (".txt", ".md"):
        return raw.decode("utf-8", errors="replace")

    if suffix == ".pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(raw))
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n\n".join(p for p in pages if p.strip())
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"PDF 解析失敗：{e}")

    if suffix in (".docx", ".doc"):
        try:
            import docx
            doc = docx.Document(io.BytesIO(raw))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except Exception as e:
            if suffix == ".doc":
                raise HTTPException(
                    status_code=422,
                    detail="舊版 .doc 格式不支援，請另存為 .docx 後再上傳",
                )
            raise HTTPException(status_code=422, detail=f"Word 檔案解析失敗：{e}")

    return ""
