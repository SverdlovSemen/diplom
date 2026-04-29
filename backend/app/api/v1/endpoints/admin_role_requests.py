from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import get_current_user, require_admin
from app.db.session import get_db_session
from app.models.admin_role_request import AdminRoleRequestStatus
from app.models.user import User
from app.schemas.admin_role_request import (
    AdminRoleRequestCreateOut,
    AdminRoleRequestOut,
    AdminRoleRequestReviewIn,
)
from app.services.admin_role_requests import (
    AdminRoleRequestConflictError,
    AdminRoleRequestInvalidStateError,
    AdminRoleRequestNotFoundError,
    create_admin_role_request,
    get_my_latest_request,
    list_admin_role_requests,
    review_admin_role_request,
)

router = APIRouter()


def _to_out(*, request, user: User) -> AdminRoleRequestOut:
    return AdminRoleRequestOut(
        id=request.id,
        user_id=request.user_id,
        user_email=user.email,
        status=request.status,
        review_comment=request.review_comment,
        reviewed_by=request.reviewed_by,
        reviewed_at=request.reviewed_at,
        created_at=request.created_at,
        updated_at=request.updated_at,
    )


@router.post("/", response_model=AdminRoleRequestCreateOut, status_code=status.HTTP_201_CREATED)
async def api_create_admin_role_request(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> AdminRoleRequestCreateOut:
    try:
        req = await create_admin_role_request(session, user)
    except AdminRoleRequestConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    return AdminRoleRequestCreateOut.model_validate(req, from_attributes=True)


@router.get("/me", response_model=AdminRoleRequestOut | None)
async def api_get_my_admin_role_request(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> AdminRoleRequestOut | None:
    row = await get_my_latest_request(session, user.id)
    if row is None:
        return None
    req, req_user = row
    return _to_out(request=req, user=req_user)


@router.get("/", response_model=list[AdminRoleRequestOut], dependencies=[Depends(require_admin)])
async def api_list_admin_role_requests(
    status_filter: AdminRoleRequestStatus | None = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_db_session),
) -> list[AdminRoleRequestOut]:
    rows = await list_admin_role_requests(session, status=status_filter)
    return [_to_out(request=req, user=user) for req, user in rows]


@router.post(
    "/{request_id}/approve",
    response_model=AdminRoleRequestOut,
    dependencies=[Depends(require_admin)],
)
async def api_approve_admin_role_request(
    request_id: uuid.UUID,
    payload: AdminRoleRequestReviewIn,
    reviewer: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> AdminRoleRequestOut:
    try:
        req, target_user = await review_admin_role_request(
            session,
            request_id=request_id,
            reviewer=reviewer,
            approve=True,
            review_comment=payload.review_comment,
        )
    except AdminRoleRequestNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except AdminRoleRequestInvalidStateError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    return _to_out(request=req, user=target_user)


@router.post(
    "/{request_id}/reject",
    response_model=AdminRoleRequestOut,
    dependencies=[Depends(require_admin)],
)
async def api_reject_admin_role_request(
    request_id: uuid.UUID,
    payload: AdminRoleRequestReviewIn,
    reviewer: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> AdminRoleRequestOut:
    try:
        req, target_user = await review_admin_role_request(
            session,
            request_id=request_id,
            reviewer=reviewer,
            approve=False,
            review_comment=payload.review_comment,
        )
    except AdminRoleRequestNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except AdminRoleRequestInvalidStateError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    return _to_out(request=req, user=target_user)
