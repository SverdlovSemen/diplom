from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole
from app.security.auth import hash_password


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email.lower().strip()))
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    role: UserRole = UserRole.viewer,
) -> User:
    user = User(
        email=email.lower().strip(),
        hashed_password=hash_password(password),
        role=role,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def set_user_role(session: AsyncSession, user: User, role: UserRole) -> User:
    user.role = role
    await session.commit()
    await session.refresh(user)
    return user
