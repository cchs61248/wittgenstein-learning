"""
URL 內容擷取工具。
支援：公開網頁（readability-lxml 抽正文）、YouTube 影片（字幕）。
"""
import re
import httpx
import tempfile
from pathlib import Path
from urllib.parse import urljoin
from readability import Document
from bs4 import BeautifulSoup
from typing import Callable, Literal


_MAX_CHARS = 500_000
_MIN_READABILITY_TEXT_CHARS = 300
_YOUTUBE_RE = re.compile(r"(?:youtube\.com/watch\?.*v=|youtu\.be/)([A-Za-z0-9_-]{11})")
_NOISE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^\s*(enroll now|browse academy|explore all courses)\b",
        r"^\s*(copyright|all rights reserved)\b",
        r"^\s*(discord|github|light|english)\s*$",
        r"^\s*use code\s+\w+\s+for\s+\d+% off\b",
        r"^\s*use code\s*$",
        r"^\s*for\s+\d+% off!?\s*$",
        r"^\s*(opens in a new tab)\s*$",
        r"^\s*\(opens in a new tab\)\s*$",
        r"^\s*(course|related learning)\s*$",
        r"^\s*(beginner|intermediate|advanced)\s*$",
        r"^\s*\d+\s*(hours?|mins?|minutes?)\s*$",
    ]
]
_MAIN_CONTENT_STOP_HEADINGS = [
    "related learning",
    "explore all courses",
    "more from",
    "recommended",
]
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


class YoutubeTranscriptUnavailable(Exception):
    def __init__(self, *, video_id: str, original_url: str, transcript_error: str) -> None:
        super().__init__(transcript_error)
        self.video_id = video_id
        self.original_url = original_url
        self.transcript_error = transcript_error


ProgressCallback = Callable[[Literal["download", "transcribe"], float], None]


def fetch_url_content(
    url: str,
    *,
    youtube_asr_mode: Literal["auto", "defer"] = "auto",
    progress_callback: ProgressCallback | None = None,
) -> tuple[str, str]:
    """
    回傳 (title, text)。
    text 最多 _MAX_CHARS 字元。
    """
    video_id = _extract_video_id(url)
    if video_id:
        return _fetch_youtube(
            video_id,
            url,
            youtube_asr_mode=youtube_asr_mode,
            progress_callback=progress_callback,
        )
    return _fetch_webpage(url)


def _fetch_youtube(
    video_id: str,
    original_url: str,
    *,
    youtube_asr_mode: Literal["auto", "defer"],
    progress_callback: ProgressCallback | None,
) -> tuple[str, str]:
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

    transcript_error = ""
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
        transcript_error = "此 YouTube 影片未提供字幕"
    except NoTranscriptFound:
        transcript_error = "找不到可用字幕（此影片可能只有自動字幕被關閉或無支援語言）"
    except CouldNotRetrieveTranscript:
        transcript_error = "無法取得字幕（可能是地區限制、需要登入或 YouTube 暫時阻擋）"
    except Exception as e:
        transcript_error = f"YouTube 字幕擷取失敗：{e}"

    if transcript_error:
        if youtube_asr_mode == "defer":
            raise YoutubeTranscriptUnavailable(
                video_id=video_id,
                original_url=original_url,
                transcript_error=transcript_error,
            )

        try:
            title, text = _transcribe_youtube_audio(video_id, original_url, progress_callback=progress_callback)
            return title, text[:_MAX_CHARS]
        except Exception as asr_err:
            meta_title, meta_text = _fetch_youtube_metadata(video_id)
            if meta_text:
                note = f"[提示] 字幕/ASR 不可用：{transcript_error}；ASR 錯誤：{asr_err}"
                merged = f"{note}\n\n{meta_text}".strip()
                return meta_title, merged[:_MAX_CHARS]
            raise ValueError(f"{transcript_error}；ASR 轉寫失敗：{asr_err}")

    title = f"YouTube 影片（{video_id}）"
    return title, text[:_MAX_CHARS]


def _fetch_youtube_metadata(video_id: str) -> tuple[str, str]:
    # oEmbed 提供公開影片標題與作者，作為無字幕/無法 ASR 時的最小 fallback。
    title = f"YouTube 影片（{video_id}）"
    text_lines: list[str] = []
    try:
        oembed = httpx.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            timeout=10,
        )
        if oembed.status_code == 200:
            payload = oembed.json()
            title = payload.get("title") or title
            author = payload.get("author_name")
            if author:
                text_lines.append(f"作者：{author}")
            text_lines.append(f"影片網址：https://www.youtube.com/watch?v={video_id}")
    except Exception:
        pass
    return title, "\n".join(text_lines).strip()


def _transcribe_youtube_audio(
    video_id: str,
    original_url: str,
    *,
    progress_callback: ProgressCallback | None,
) -> tuple[str, str]:
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        raise RuntimeError("缺少 yt-dlp，請安裝：pip install yt-dlp")

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError("缺少 faster-whisper，請安裝：pip install faster-whisper")

    if progress_callback:
        # download 0% → 100% → transcribe 0% → 100%
        progress_callback("download", 0.0)
        progress_callback("transcribe", 0.0)

    with tempfile.TemporaryDirectory(prefix="yt_asr_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        outtmpl = str(tmp_path / f"{video_id}.%(ext)s")

        def _hook(d: dict) -> None:
            if not progress_callback:
                return
            if d.get("status") != "downloading":
                return
            downloaded = d.get("downloaded_bytes")
            total = d.get("total_bytes_estimate") or d.get("total_bytes")
            if not downloaded or not total:
                return
            try:
                pct = max(0.0, min(1.0, float(downloaded) / float(total)))
            except Exception:
                return
            progress_callback("download", pct)

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "noplaylist": True,
            "progress_hooks": [_hook],
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(original_url, download=True)

        if progress_callback:
            progress_callback("download", 1.0)

        audio_path = tmp_path / f"{video_id}.mp3"
        if not audio_path.exists():
            raise RuntimeError("音訊下載失敗（找不到轉換後 mp3）")

        model = WhisperModel("small", device="cpu", compute_type="int8")
        segments, info2 = model.transcribe(
            str(audio_path),
            language=None,
            vad_filter=True,
            beam_size=3,
        )

        duration = None
        try:
            duration = (info or {}).get("duration") or (info2 or {}).duration
        except Exception:
            duration = (info or {}).get("duration")
        if duration is not None:
            try:
                duration = float(duration)
            except Exception:
                duration = None

        transcript_parts: list[str] = []
        last_pct = -1.0
        for seg in segments:
            if not seg:
                continue
            text_part = (seg.text or "").strip()
            if text_part:
                transcript_parts.append(text_part)

            if progress_callback:
                pct = None
                if duration and duration > 0 and getattr(seg, "end", None) is not None:
                    pct = max(0.0, min(1.0, float(seg.end) / duration))
                elif duration:
                    pct = 0.0
                if pct is not None and abs(pct - last_pct) >= 0.01:
                    progress_callback("transcribe", pct)
                    last_pct = pct

        if progress_callback:
            progress_callback("transcribe", 1.0)

        transcript = " ".join(transcript_parts).strip()
        if not transcript:
            raise RuntimeError("ASR 轉寫結果為空")

        title = (info or {}).get("title") or f"YouTube 影片（{video_id}，ASR）"
        return title, transcript


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

    summary_html = doc.summary()
    text = _extract_markdownish_text(summary_html, base_url=url)

    # 某些站點（特別是高度動態頁）readability 可能只抓到 banner；
    # 當內容過短時改用全文清洗抽取，提升命中率。
    if len(text.strip()) < _MIN_READABILITY_TEXT_CHARS:
        text = _fallback_extract_text(resp.text, base_url=url)

    text = _clean_extracted_text(text)

    if not text.strip():
        raise ValueError("網頁內容為空或無法解析（可能需要登入或為動態頁面）")

    return title, text[:_MAX_CHARS]


def _fallback_extract_text(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript", "svg", "canvas", "iframe"]):
        tag.decompose()

    candidate = (
        soup.select_one("main")
        or soup.select_one("article")
        or soup.select_one("[role='main']")
        or soup.body
        or soup
    )

    for tag in candidate.select("nav, header, footer, aside, form, button"):
        tag.decompose()

    text = _extract_markdownish_text(str(candidate), base_url=base_url)
    return _clean_extracted_text(text)


def _extract_markdownish_text(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript", "svg", "canvas", "iframe"]):
        tag.decompose()

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        label = " ".join(a.get_text(" ", strip=True).split())
        if not href:
            continue
        abs_href = urljoin(base_url, href)
        if label:
            a.replace_with(f"[{label}]({abs_href})")
        else:
            a.replace_with(abs_href)

    lines: list[str] = []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"]):
        raw = el.get_text(" ", strip=True)
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        if el.name and el.name.startswith("h"):
            level = min(int(el.name[1]), 3)
            lines.append(f"{'#' * level} {line}")
        elif el.name == "li":
            lines.append(f"- {line}")
        else:
            lines.append(line)

    if lines:
        return "\n".join(lines)
    return soup.get_text(separator="\n", strip=True)


def _clean_extracted_text(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln and len(ln.strip()) >= 2]

    cleaned: list[str] = []
    seen: set[str] = set()
    for ln in lines:
        normalized = re.sub(r"\s+", " ", ln).strip()
        if not normalized:
            continue
        if normalized.lower() in seen:
            continue
        if any(p.search(normalized) for p in _NOISE_PATTERNS):
            continue
        cleaned.append(normalized)
        seen.add(normalized.lower())

    return _apply_strict_main(cleaned)


def _apply_strict_main(lines: list[str]) -> str:
    """
    strict_main：盡量只保留主文章，遇到常見延伸閱讀區塊標題就截斷。
    """
    kept: list[str] = []
    for line in lines:
        lower = line.lower().strip()
        heading = lower.lstrip("#").strip()
        if any(stop in heading for stop in _MAIN_CONTENT_STOP_HEADINGS):
            break
        kept.append(line)
    return "\n".join(kept)
