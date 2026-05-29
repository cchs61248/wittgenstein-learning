import logging
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_LOGS_DIR = Path(__file__).parent.parent / "logs"
SEP = "=" * 72
DASH = "─" * 60


def _file_handler(filename: str) -> TimedRotatingFileHandler:
    _LOGS_DIR.mkdir(exist_ok=True)
    h = TimedRotatingFileHandler(
        filename=str(_LOGS_DIR / filename),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        utc=False,
    )
    h.setLevel(logging.DEBUG)
    h.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  [%(name)s]\n%(message)s\n",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    return h


def _console_handler() -> logging.StreamHandler:
    h = logging.StreamHandler()
    h.setLevel(logging.INFO)
    h.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    ))
    return h


def setup_logging() -> None:
    """初始化所有專案 logger，應在應用程式啟動時呼叫一次。"""
    root = logging.getLogger("wl")
    root.setLevel(logging.DEBUG)
    if not root.handlers:
        root.addHandler(_console_handler())

    for name, fname in (
        ("wl.llm",                    "llm.log"),
        ("wl.agents",                 "agents.log"),
        ("wl.orchestrator",           "orchestrator.log"),
        ("wl.orchestrator.v2",        "orchestrator.log"),
        ("wl.orchestrator.v2.health", "orchestrator.log"),
        ("wl.ws",                     "ws.log"),
        ("wl.ingest",                 "ingest.log"),
        ("wl.jobs.curriculum",        "curriculum.log"),
    ):
        log = logging.getLogger(name)
        if not log.handlers:
            log.addHandler(_file_handler(fname))
        log.propagate = True   # INFO+ 仍會往上傳到 root → console


# ── convenience getters ────────────────────────────────────────────────────

def llm_logger() -> logging.Logger:
    return logging.getLogger("wl.llm")


def orchestrator_logger() -> logging.Logger:
    return logging.getLogger("wl.orchestrator")


def ws_logger() -> logging.Logger:
    return logging.getLogger("wl.ws")


def ingest_logger() -> logging.Logger:
    return logging.getLogger("wl.ingest")


# ── helpers ────────────────────────────────────────────────────────────────

def text_preview(text: str, *, max_chars: int = 120) -> str:
    """單行摘要，供 ingest / upload log 使用。"""
    one_line = " ".join(text.split())
    if len(one_line) <= max_chars:
        return one_line
    return one_line[: max_chars - 1] + "…"


def fmt_elapsed(t0: float) -> str:
    return f"{time.perf_counter() - t0:.2f}s"
