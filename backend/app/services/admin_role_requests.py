from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Select, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_role_request import AdminRoleRequest, AdminRoleRequestStatus
from app.models.user import User, UserRole


class AdminRoleRequestConflictError(Exception):
    pass


class AdminRoleRequestNotFoundError(Exception):
    pass


class AdminRoleRequestInvalidStateError(Exception):
    pass


def _list_requests_stmt() -> Select[tuple[AdminRoleRequest, User]]:
    return (
        select(AdminRoleRequest, User)
        .join(User, User.id == AdminRoleRequest.user_id)
        .order_by(desc(AdminRoleRequest.created_at))
    )


async def create_admin_role_request(session: AsyncSession, user: User) -> AdminRoleRequest:
    if user.role == UserRole.admin:
        raise AdminRoleRequestConflictError("У вас уже есть права администратора")

    existing_pending = await session.execute(
        select(AdminRoleRequest).where(
            AdminRoleRequest.user_id == user.id,
            AdminRoleRequest.status == AdminRoleRequestStatus.pending,
        )
    )
    if existing_pending.scalar_one_or_none() is not None:
        raise AdminRoleRequestConflictError("Заявка уже отправлена и ожидает рассмотрения")

    req = AdminRoleRequest(user_id=user.id, status=AdminRoleRequestStatus.pending)
    session.add(req)
    await session.commit()
    await session.refresh(req)
    return req


async def get_my_latest_request(session: AsyncSession, user_id: uuid.UUID) -> tuple[AdminRoleRequest, User] | None:
    result = await session.execute(
        _list_requests_stmt().where(AdminRoleRequest.user_id == user_id).limit(1)
    )
    row = result.first()
    if row is None:
        return None
    return row[0], row[1]


async def list_admin_role_requests(
    session: AsyncSession,
    *,
    status: AdminRoleRequestStatus | None = None,
) -> list[tuple[AdminRoleRequest, User]]:
    stmt = _list_requests_stmt()
    if status is not None:
        stmt = stmt.where(AdminRoleRequest.status == status)
    result = await session.execute(stmt)
    return list(result.all())


async def review_admin_role_request(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    reviewer: User,
    approve: bool,
    review_comment: str | None = None,
) -> tuple[AdminRoleRequest, User]:
    result = await session.execute(
        select(AdminRoleRequest, User)
        .join(User, User.id == AdminRoleRequest.user_id)
        .where(AdminRoleRequest.id == request_id)
    )
    row = result.first()
    if row is None:
        raise AdminRoleRequestNotFoundError("Заявка не найдена")
    req, target_user = row

    if req.status != AdminRoleRequestStatus.pending:
        raise AdminRoleRequestInvalidStateError("Заявка уже рассмотрена")

    req.status = AdminRoleRequestStatus.approved if approve else AdminRoleRequestStatus.rejected
    req.reviewed_by = reviewer.id
    req.review_comment = review_comment
    req.reviewed_at = datetime.now(timezone.utc)

    if approve:
        target_user.role = UserRole.admin

    await session.commit()
    await session.refresh(req)
    await session.refresh(target_user)
    return req, target_user
