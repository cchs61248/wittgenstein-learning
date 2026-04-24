import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx
import google.genai as genai


@dataclass
class ProviderFileRef:
    provider: str
    filename: str
    mime_type: str
    openai_file_id: str | None = None
    gemini_file_uri: str | None = None
    claude_file_id: str | None = None


async def create_provider_file_ref(
    provider: str,
    filename: str,
    mime_type: str,
    raw: bytes,
) -> ProviderFileRef:
    if provider == "openai":
        return await _upload_openai(filename, mime_type, raw)
    if provider == "gemini":
        return await _upload_gemini(filename, mime_type, raw)
    if provider == "claude":
        return await _upload_claude(filename, mime_type, raw)
    raise ValueError(f"未知 provider: {provider}")


async def _upload_openai(filename: str, mime_type: str, raw: bytes) -> ProviderFileRef:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 OPENAI_API_KEY，無法上傳檔案")

    headers = {"Authorization": f"Bearer {api_key}"}
    files = {"file": (filename, raw, mime_type)}
    data = {"purpose": "assistants"}
    async with httpx.AsyncClient(timeout=120) as client:
        res = await client.post(
            "https://api.openai.com/v1/files",
            headers=headers,
            files=files,
            data=data,
        )
    if res.status_code >= 400:
        raise RuntimeError(f"OpenAI 上傳失敗: {res.text}")
    payload = res.json()
    return ProviderFileRef(
        provider="openai",
        filename=filename,
        mime_type=mime_type,
        openai_file_id=payload.get("id"),
    )


async def _upload_gemini(filename: str, mime_type: str, raw: bytes) -> ProviderFileRef:
    client = genai.Client()
    suffix = Path(filename).suffix or ".bin"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name
    try:
        uploaded = await client.aio.files.upload(
            file=tmp_path,
            config={"mime_type": mime_type, "display_name": filename},
        )
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    return ProviderFileRef(
        provider="gemini",
        filename=filename,
        mime_type=mime_type,
        gemini_file_uri=getattr(uploaded, "uri", None),
    )


async def _upload_claude(filename: str, mime_type: str, raw: bytes) -> ProviderFileRef:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 ANTHROPIC_API_KEY，無法上傳檔案")

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "files-api-2025-04-14",
    }
    files = {"file": (filename, raw, mime_type)}
    async with httpx.AsyncClient(timeout=120) as client:
        res = await client.post(
            "https://api.anthropic.com/v1/files",
            headers=headers,
            files=files,
        )
    if res.status_code >= 400:
        raise RuntimeError(f"Claude 上傳失敗: {res.text}")
    payload = res.json()
    return ProviderFileRef(
        provider="claude",
        filename=filename,
        mime_type=mime_type,
        claude_file_id=payload.get("id"),
    )
