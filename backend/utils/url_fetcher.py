"""
URL 內容擷取工具。
支援：公開網頁（readability-lxml 抽正文）、YouTube 影片（字幕）。
"""
import re
import httpx
from readability import Document
from bs4 import BeautifulSoup


_MAX_CHARS = 500_000
_YOUTUBE_RE = re.compile(r"(?:youtube\.com/watch\?.*v=|youtu\.be/)([A-Za-z0-9_-]{11})")
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.8,en-US;q=0.5,en;q=0.3",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


def _extract_video_id(url: str) -> str | None:
    m = _YOUTUBE_RE.search(url)
    return m.group(1) if m else None


def fetch_url_content(url: str) -> tuple[str, str]:
    """
    回傳 (title, text)。
    text 最多 _MAX_CHARS 字元。
    """
    video_id = _extract_video_id(url)
    if video_id:
        return _fetch_youtube(video_id, url)
    return _fetch_webpage(url)


def _fetch_youtube(video_id: str, original_url: str) -> tuple[str, str]:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        raise RuntimeError("youtube-transcript-api 未安裝，請執行 pip install youtube-transcript-api")

    # 不同版本的 youtube-transcript-api 暴露的例外類別可能不同；用可選匯入確保相容。
    try:  # pragma: no cover
        from youtube_transcript_api import TranscriptsDisabled  # type: ignore
    except Exception:  # pragma: no cover
        TranscriptsDisabled = Exception  # type: ignore
    try:  # pragma: no cover
        from youtube_transcript_api import NoTranscriptFound  # type: ignore
    except Exception:  # pragma: no cover
        NoTranscriptFound = Exception  # type: ignore
    try:  # pragma: no cover
        from youtube_transcript_api import CouldNotRetrieveTranscript  # type: ignore
    except Exception:  # pragma: no cover
        CouldNotRetrieveTranscript = Exception  # type: ignore

    try:
        # youtube-transcript-api 介面在不同版本可能不同：
        # 本專案已實測（1.2.4）為 instance 方法：YouTubeTranscriptApi().fetch(...)
        api = YouTubeTranscriptApi()
        entries = api.fetch(
            video_id,
            languages=["zh-TW", "zh-Hant", "zh", "en"],
        )
        text = " ".join((e.text or "") for e in entries).strip()
        if not text:
            raise ValueError("字幕內容為空")
    except TranscriptsDisabled:
        raise ValueError("此 YouTube 影片未提供字幕，無法擷取內容")
    except NoTranscriptFound:
        raise ValueError("找不到可用字幕（此影片可能只有自動字幕被關閉或無支援語言）")
    except CouldNotRetrieveTranscript:
        raise ValueError("無法取得字幕（可能是地區限制、需要登入或 YouTube 暫時阻擋）")
    except Exception as e:
        raise ValueError(f"YouTube 字幕擷取失敗：{e}")

    title = f"YouTube 影片（{video_id}）"
    return title, text[:_MAX_CHARS]


def _fetch_webpage(url: str) -> tuple[str, str]:
    try:
        with httpx.Client(follow_redirects=True, timeout=20, headers=_HEADERS) as client:
            resp = client.get(url)
            resp.raise_for_status()
    except httpx.TimeoutException:
        raise ValueError(f"連線逾時：{url}")
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        if code == 403:
            raise ValueError(
                f"此網站拒絕自動擷取（HTTP 403），通常是 Cloudflare 或登入保護。"
                f"請直接複製網頁文字後選「貼上純文字」方式加入。"
            )
        raise ValueError(f"HTTP {code}：{url}")
    except Exception as e:
        raise ValueError(f"無法連線至 {url}：{e}")

    content_type = resp.headers.get("content-type", "")
    if "text" not in content_type and "html" not in content_type:
        raise ValueError(f"不支援的內容類型：{content_type}（僅支援 HTML 網頁）")

    doc = Document(resp.text)
    title = doc.title() or url

    soup = BeautifulSoup(doc.summary(), "lxml")
    text = soup.get_text(separator="\n", strip=True)

    if not text.strip():
        raise ValueError("網頁內容為空或無法解析（可能需要登入或為動態頁面）")

    return title, text[:_MAX_CHARS]
