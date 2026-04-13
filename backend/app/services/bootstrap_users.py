from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.user import User, UserRole
from app.security.auth import hash_password

logger = logging.getLogger("app.bootstrap_users")


async def _ensure_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    role: UserRole,
) -> bool:
    normalized_email = email.strip().lower()
    if not normalized_email or not password:
        return False
    existing = await session.execute(select(User).where(User.email == normalized_email))
    if existing.scalar_one_or_none() is not None:
        return False
    session.add(
        User(
            email=normalized_email,
            hashed_password=hash_password(password),
            role=role,
        )
    )
    return True


async def seed_users_if_missing(session: AsyncSession) -> None:
    created = []
    if await _ensure_user(
        session,
        email=settings.seed_admin_email,
        password=settings.seed_admin_password,
        role=UserRole.admin,
    ):
        created.append(("admin", settings.seed_admin_email.strip().lower()))

    if settings.seed_viewer_enabled and await _ensure_user(
        session,
        email=settings.seed_viewer_email,
        password=settings.seed_viewer_password,
        role=UserRole.viewer,
    ):
        created.append(("viewer", settings.seed_viewer_email.strip().lower()))

    if created:
        await session.commit()
        logger.info("Seeded users", extra={"created_users": created})
