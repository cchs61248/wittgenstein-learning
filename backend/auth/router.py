import uuid
from fastapi import APIRouter, HTTPException, status
from ..db.database import get_db
from .models import UserRegister, UserLogin, TokenOut, UserOut
from .utils import hash_password, verify_password, create_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
async def register(body: UserRegister):
    db = await get_db()

    async with db.execute("SELECT user_id FROM users WHERE email = ?", (body.email,)) as cur:
        if await cur.fetchone():
            raise HTTPException(status_code=409, detail="Email 已被使用")

    user_id = str(uuid.uuid4())
    pw_hash = hash_password(body.password)
    await db.execute(
        "INSERT INTO users (user_id, email, password_hash) VALUES (?, ?, ?)",
        (user_id, body.email, pw_hash),
    )
    await db.commit()

    token = create_token(user_id, body.email)
    return TokenOut(access_token=token, user_id=user_id, email=body.email)


@router.post("/login", response_model=TokenOut)
async def login(body: UserLogin):
    db = await get_db()

    async with db.execute(
        "SELECT user_id, password_hash FROM users WHERE email = ?", (body.email,)
    ) as cur:
        row = await cur.fetchone()

    if not row or not verify_password(body.password, row[1]):
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")

    token = create_token(row[0], body.email)
    return TokenOut(access_token=token, user_id=row[0], email=body.email)


@router.get("/me", response_model=UserOut)
async def me(token: str):
    from .utils import decode_token
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token 無效")
    return UserOut(user_id=payload["sub"], email=payload["email"])
