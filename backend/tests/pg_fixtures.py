"""PostgreSQL 測試共用：session 級 testcontainer + DSN 注入。"""
import asyncio
import atexit
import os
import re

import asyncpg
import pytest
from testcontainers.postgres import PostgresContainer

# Windows Docker Desktop 上 Ryuk reaper sidecar 會嘗試綁 port 8080 並常失敗；
# 停用後沒有自動回收器，因此本 fixture 自行負責清理：
#   - 正常結束 → finally: pg.stop()
#   - 直譯器離開（例外/soft exit）→ atexit backstop
#   - 行程被 SIGKILL（session 中斷等）→ 兩者都救不了，容器會殘留
# 殘留容器可一行清掉（容器都帶 label wl-test=1）：
#   docker rm -f $(docker ps -aq --filter "label=wl-test=1")
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")


def pg_exec(sql: str, *args) -> None:
    """以「獨立短命連線」對測試 DB 執行一句 SQL（給 TestClient 類測試用）。

    TestClient 啟動後，app 的 asyncpg pool 綁在 TestClient 自己的 portal event loop；
    測試端若改用 get_db() 的 pool 跨 loop 操作會 loop 不符而 hang。此 helper 每次開一條
    全新連線、在自己的 asyncio.run loop 內跑完即關，完全不碰 app pool，避免跨 loop 問題。
    """
    async def _run() -> None:
        conn = await asyncpg.connect(os.environ["DATABASE_URL"])
        try:
            await conn.execute(sql, *args)
        finally:
            await conn.close()

    asyncio.run(_run())


def _asyncpg_dsn(container: PostgresContainer) -> str:
    """testcontainers 回 SQLAlchemy 風格 URL（postgresql+psycopg2:// 之類）；asyncpg 要純 postgresql://。"""
    url = container.get_connection_url()
    # 去掉任何 +driver（psycopg2 / psycopg / asyncpg）
    return re.sub(r"^postgresql\+[a-z0-9]+://", "postgresql://", url)


@pytest.fixture(scope="session", autouse=True)
def _pg_container():
    """整個測試 session 共用一個 postgres 容器；設 DATABASE_URL + WL_TEST_ENV。

    Ryuk 已停用，故自行用 finally + atexit 雙保險清理容器（見檔頭說明）。
    """
    pg = PostgresContainer("postgres:16-alpine").with_kwargs(labels={"wl-test": "1"})
    pg.start()

    def _cleanup() -> None:
        try:
            pg.stop()
        except Exception:
            pass

    atexit.register(_cleanup)
    try:
        dsn = _asyncpg_dsn(pg)
        os.environ["DATABASE_URL"] = dsn
        os.environ["WL_TEST_ENV"] = "1"
        yield dsn
    finally:
        _cleanup()
