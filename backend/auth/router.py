import uuid
from fastapi import APIRouter, HTTPException, status
from ..db.database import get_db
from ..utils.logger import ws_logger
from .models import UserRegister, UserLogin, TokenOut, UserOut
from .utils import hash_password, verify_password, create_token, decode_token, decode_token_active

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(body: UserRegister):
    log = ws_logger()
    db = await get_db()

    async with db.execute("SELECT user_id FROM users WHERE email = ?", (body.email,)) as cur:
        if await cur.fetchone():
            log.info("register conflict  email=%s", body.email)
            raise HTTPException(status_code=409, detail="Email 已被使用")

    user_id = str(uuid.uuid4())
    pw_hash = hash_password(body.password)
    session_version = 1
    await db.execute(
        "INSERT INTO users (user_id, email, password_hash, session_version) VALUES (?, ?, ?, ?)",
        (user_id, body.email, pw_hash, session_version),
    )
    await db.commit()

    log.info("register ok  user_id=%s  email=%s", user_id, body.email)
    token = create_token(user_id, body.email, session_version=session_version)
    return TokenOut(access_token=token, user_id=user_id, email=body.email)


@router.post("/login", response_model=TokenOut)
async def login(body: UserLogin):
    log = ws_logger()
    db = await get_db()

    async with db.execute(
        "SELECT user_id, password_hash FROM users WHERE email = ?", (body.email,)
    ) as cur:
        row = await cur.fetchone()

    if not row:
        log.warning("login fail unknown_email  email=%s", body.email)
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")
    if not verify_password(body.password, row[1]):
        log.warning("login fail bad_password  user_id=%s  email=%s", row[0], body.email)
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")

    await db.execute(
        "UPDATE users SET session_version = session_version + 1 WHERE user_id = ?",
        (row[0],),
    )
    await db.commit()
    async with db.execute("SELECT session_version FROM users WHERE user_id = ?", (row[0],)) as cur:
        sv_row = await cur.fetchone()
    session_version = int(sv_row[0]) if sv_row else 1
    log.info(
        "login ok  user_id=%s  email=%s  session_version=%d",
        row[0], body.email, session_version,
    )
    token = create_token(row[0], body.email, session_version=session_version)
    return TokenOut(access_token=token, user_id=row[0], email=body.email)


@router.get("/me", response_model=UserOut)
async def me(token: str):
    log = ws_logger()
    payload = await decode_token_active(token)
    if not payload:
        # best-effort 分析失敗原因：jwt 解碼可拿到 sub/sv 就表示 token 本身有效
        # 但 session_version 不符（已被別處登入頂掉）
        raw = decode_token(token)
        if raw and raw.get("sub"):
            log.info(
                "auth/me session_version mismatch  user_id=%s  token_sv=%s",
                raw.get("sub"), raw.get("sv"),
            )
        else:
            log.info("auth/me invalid_token  token_len=%d", len(token))
        raise HTTPException(status_code=401, detail="Token 無效")
    return UserOut(user_id=payload["sub"], email=payload["email"])
