import os
from datetime import datetime, timedelta, timezone
from typing import Optional
import bcrypt
from jose import JWTError, jwt
from ..db.database import get_db

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = int(os.getenv("JWT_EXPIRE_DAYS", "7"))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_token(user_id: str, email: str, session_version: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRE_DAYS)
    return jwt.encode(
        {"sub": user_id, "email": email, "sv": session_version, "exp": expire},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None


async def decode_token_active(token: str) -> Optional[dict]:
    payload = decode_token(token)
    if not payload:
        return None

    user_id = payload.get("sub")
    sv = payload.get("sv")
    if not user_id or sv is None:
        return None

    db = await get_db()
    row = await db.fetchrow(
        "SELECT session_version FROM users WHERE user_id = $1",
        user_id,
    )
    if not row:
        return None
    if int(row["session_version"]) != int(sv):
        return None
    return payload


async def get_role_by_email(email: str) -> str:
    """即時查 email_whitelist 回傳角色；查無回 'user'（最小權限 fail-safe）。

    角色不寫進 JWT，每次受保護請求即時查表，admin 改 DB 後下一個請求即生效。
    """
    db = await get_db()
    row = await db.fetchrow(
        "SELECT role FROM email_whitelist WHERE email = $1", email
    )
    if not row:
        return "user"
    return str(row["role"])


async def is_email_whitelisted(email: str) -> bool:
    """email 是否在白名單內（供註冊閘門）。"""
    db = await get_db()
    row = await db.fetchrow(
        "SELECT 1 FROM email_whitelist WHERE email = $1", email
    )
    return row is not None
