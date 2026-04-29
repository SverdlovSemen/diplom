from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import get_current_user
from app.db.session import get_db_session
from app.models.user import User
from app.schemas.auth import AuthUserOut, LoginRequest, LoginResponse, RegisterRequest
from app.security.auth import create_access_token, verify_password
from app.services.users import create_user, get_user_by_email

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_db_session)) -> LoginResponse:
    user = await get_user_by_email(session, payload.email)
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверная почта или пароль")
    token = create_access_token(subject=str(user.id), role=user.role.value)
    user_out = AuthUserOut(id=str(user.id), email=user.email, role=user.role)
    return LoginResponse(access_token=token, user=user_out)


@router.post("/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, session: AsyncSession = Depends(get_db_session)) -> LoginResponse:
    existing = await get_user_by_email(session, payload.email)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователь с такой почтой уже зарегистрирован",
        )
    try:
        user = await create_user(session, email=str(payload.email), password=payload.password)
    except IntegrityError as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Пользователь с такой почтой уже зарегистрирован",
        ) from e
    token = create_access_token(subject=str(user.id), role=user.role.value)
    user_out = AuthUserOut(id=str(user.id), email=user.email, role=user.role)
    return LoginResponse(access_token=token, user=user_out)


@router.get("/me", response_model=AuthUserOut)
async def me(user: User = Depends(get_current_user)) -> AuthUserOut:
    return AuthUserOut(id=str(user.id), email=user.email, role=user.role)
