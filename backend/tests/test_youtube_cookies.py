from pathlib import Path

from backend.utils.youtube_cookies import json_cookies_to_netscape, materialize_ytdlp_cookiefile


def test_json_cookies_to_netscape():
    netscape = json_cookies_to_netscape(
        [
            {
                "domain": ".youtube.com",
                "expirationDate": 1780365566,
                "name": "CONSISTENCY",
                "path": "/",
                "secure": True,
                "value": "abc",
            }
        ]
    )
    assert ".youtube.com\tTRUE\t/\tTRUE\t1780365566\tCONSISTENCY\tabc" in netscape


def test_materialize_from_default_secrets(tmp_path, monkeypatch):
    monkeypatch.delenv("YOUTUBE_COOKIES_FILE", raising=False)
    src = Path(__file__).resolve().parents[1] / "secrets" / "youtube_cookies.json"
    if not src.is_file():
        return
    cookiefile = materialize_ytdlp_cookiefile(tmp_path)
    assert cookiefile is not None
    assert Path(cookiefile).read_text(encoding="utf-8").startswith("# Netscape HTTP Cookie File")
