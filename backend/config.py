import os
from pathlib import Path
from dotenv import load_dotenv

# 明確指向 backend/.env，無論從哪個目錄啟動 uvicorn 都能正確載入
_env_file = Path(__file__).parent / ".env"
load_dotenv(_env_file)

# DB_PATH 若為相對路徑，以 backend/ 為基準解析（.env 的寫法慣例）
_raw_db = os.getenv("DB_PATH")
if _raw_db and not Path(_raw_db).is_absolute():
    DB_PATH: str = str((Path(__file__).parent / _raw_db).resolve())
elif _raw_db:
    DB_PATH = _raw_db
else:
    DB_PATH = str(Path(__file__).parent.parent / "data" / "learning.db")
DEFAULT_PROVIDER: str = os.getenv("DEFAULT_PROVIDER", "claude")
_cors_env = os.getenv("CORS_ORIGINS", "")
if _cors_env:
    CORS_ORIGINS: list[str] = [o.strip() for o in _cors_env.split(",") if o.strip()]
else:
    # 開發預設：允許 Vite dev server 常用埠號（不可與 allow_credentials 並用 "*"）
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

# Quick Tunnel 每次子網域不同，用 regex 白名單；設 CORS_ORIGIN_REGEX= 可關閉
_raw_cors_regex = os.getenv("CORS_ORIGIN_REGEX")
if _raw_cors_regex is None:
    CORS_ORIGIN_REGEX: str | None = r"https://.*\.trycloudflare\.com"
elif not _raw_cors_regex.strip():
    CORS_ORIGIN_REGEX = None
else:
    CORS_ORIGIN_REGEX = _raw_cors_regex.strip()

# 未被 session 引用的 upload 超過此時數後，啟動時自動 GC（0 = 立即刪除所有孤兒）
UPLOAD_ORPHAN_MAX_AGE_HOURS: float = float(os.getenv("UPLOAD_ORPHAN_MAX_AGE_HOURS", "24"))

# 上傳解析後允許的最大字元數（與 URL 擷取一致）
UPLOAD_MAX_CHAR_COUNT: int = int(os.getenv("UPLOAD_MAX_CHAR_COUNT", "500000"))

REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CURRICULUM_USE_ARQ: bool = os.getenv("CURRICULUM_USE_ARQ", "0").strip().lower() in (
    "1", "true", "yes",
)
ARQ_MAX_JOBS: int = int(os.getenv("ARQ_MAX_JOBS", "1"))
ARQ_JOB_TIMEOUT_S: int = int(os.getenv("ARQ_JOB_TIMEOUT_S", "7200"))

LLM_CACHE_ENABLED: bool = os.getenv("LLM_CACHE_ENABLED", "0").strip().lower() in (
    "1", "true", "yes",
)
CURRICULUM_PROMPT_VERSION: str = os.getenv("CURRICULUM_PROMPT_VERSION", "1")
LLM_CACHE_EVICT_DAYS: int = int(os.getenv("LLM_CACHE_EVICT_DAYS", "90"))
