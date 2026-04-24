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
CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
