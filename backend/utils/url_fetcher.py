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
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; WittgensteinBot/1.0)"}


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
        from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
    except ImportError:
        raise RuntimeError("youtube-transcript-api 未安裝，請執行 pip install youtube-transcript-api")

    try:
        transcripts = YouTubeTranscriptApi.list_transcripts(video_id)
        try:
            t = transcripts.find_transcript(["zh-TW", "zh-Hant", "zh", "en"])
        except NoTranscriptFound:
            t = next(iter(transcripts))
        entries = t.fetch()
        text = " ".join(e.text for e in entries)
    except TranscriptsDisabled:
        raise ValueError("此 YouTube 影片未提供字幕，無法擷取內容")
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
        raise ValueError(f"HTTP {e.response.status_code}：{url}")
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
