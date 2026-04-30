import logging
import time
import uuid
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

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
        ("wl.llm",          "llm.log"),
        ("wl.agents",       "agents.log"),
        ("wl.orchestrator", "orchestrator.log"),
        ("wl.ws",           "ws.log"),
    ):
        log = logging.getLogger(name)
        if not log.handlers:
            log.addHandler(_file_handler(fname))
        log.propagate = True   # INFO+ 仍會往上傳到 root → console


# ── convenience getters ────────────────────────────────────────────────────

def llm_logger() -> logging.Logger:
    return logging.getLogger("wl.llm")


def agents_logger() -> logging.Logger:
    return logging.getLogger("wl.agents")


def orchestrator_logger() -> logging.Logger:
    return logging.getLogger("wl.orchestrator")


def ws_logger() -> logging.Logger:
    return logging.getLogger("wl.ws")


# ── helpers ────────────────────────────────────────────────────────────────

def new_call_id() -> str:
    return uuid.uuid4().hex[:8]


def fmt_elapsed(t0: float) -> str:
    return f"{time.perf_counter() - t0:.2f}s"
