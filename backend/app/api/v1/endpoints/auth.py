from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import get_current_user
from app.db.session import get_db_session
from app.models.user import User
from app.schemas.auth import AuthUserOut, LoginRequest, LoginResponse
from app.security.auth import create_access_token, verify_password
from app.services.users import get_user_by_email

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, session: AsyncSession = Depends(get_db_session)) -> LoginResponse:
    user = await get_user_by_email(session, payload.email)
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    token = create_access_token(subject=str(user.id), role=user.role.value)
    user_out = AuthUserOut(id=str(user.id), email=user.email, role=user.role)
    return LoginResponse(access_token=token, user=user_out)


@router.get("/me", response_model=AuthUserOut)
async def me(user: User = Depends(get_current_user)) -> AuthUserOut:
    return AuthUserOut(id=str(user.id), email=user.email, role=user.role)
