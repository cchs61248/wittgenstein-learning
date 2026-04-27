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
PASS_THRESHOLD: float = float(os.getenv("PASS_THRESHOLD", "0.75"))
MAX_STAGE_ATTEMPTS: int = int(os.getenv("MAX_STAGE_ATTEMPTS", "3"))
_cors_env = os.getenv("CORS_ORIGINS", "")
if _cors_env:
    CORS_ORIGINS: list[str] = [o.strip() for o in _cors_env.split(",") if o.strip()]
else:
    # 開發預設：允許 Vite dev server 常用埠號範圍
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
