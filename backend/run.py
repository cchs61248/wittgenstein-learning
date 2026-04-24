import sys
import os

# 將 wittgenstein-learning/ 加入模組搜尋路徑，讓 backend.* 相對匯入正常運作
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import app  # noqa: F401, E402

__all__ = ["app"]
