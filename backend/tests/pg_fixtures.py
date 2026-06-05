"""PostgreSQL 測試共用：session 級 testcontainer + DSN 注入。"""
import os
import re

import pytest
from testcontainers.postgres import PostgresContainer

# Windows Docker Desktop 上 Ryuk reaper sidecar 會嘗試綁 port 8080 並常失敗；
# 停用後容器仍正常，測試結束時由 pytest fixture teardown 負責清理。
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")


def _asyncpg_dsn(container: PostgresContainer) -> str:
    """testcontainers 回 SQLAlchemy 風格 URL（postgresql+psycopg2:// 之類）；asyncpg 要純 postgresql://。"""
    url = container.get_connection_url()
    # 去掉任何 +driver（psycopg2 / psycopg / asyncpg）
    return re.sub(r"^postgresql\+[a-z0-9]+://", "postgresql://", url)


@pytest.fixture(scope="session", autouse=True)
def _pg_container():
    """整個測試 session 共用一個 postgres 容器；設 DATABASE_URL + WL_TEST_ENV。"""
    with PostgresContainer("postgres:16-alpine") as pg:
        dsn = _asyncpg_dsn(pg)
        os.environ["DATABASE_URL"] = dsn
        os.environ["WL_TEST_ENV"] = "1"
        yield dsn
