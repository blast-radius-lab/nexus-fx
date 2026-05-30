from datetime import datetime, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..auth.jwt_handler import create_token
from ..models import User, get_session

router = APIRouter(prefix="/api/auth")


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/register")
async def register(req: RegisterRequest, session: AsyncSession = Depends(get_session)):
    existing = await session.execute(
        select(User).where(User.username == req.username)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already taken")

    password_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
    user = User(
        username=req.username,
        password_hash=password_hash,
        email=req.email,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    token = create_token(str(user.id), user.username)
    return {"token": token, "user_id": str(user.id), "username": user.username}


@router.post("/login")
async def login(req: LoginRequest, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(User).where(User.username == req.username)
    )
    user = result.scalar_one_or_none()

    if not user or not bcrypt.checkpw(req.password.encode(), user.password_hash.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    user.last_login = datetime.now(timezone.utc)
    await session.commit()

    token = create_token(str(user.id), user.username)
    return {"token": token, "user_id": str(user.id), "username": user.username}


@router.get("/me")
async def me(current_user: dict = Depends(get_current_user)):
    return current_user
