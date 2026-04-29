from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.admin_role_request import AdminRoleRequestStatus


class AdminRoleRequestCreateOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    status: AdminRoleRequestStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class AdminRoleRequestOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    user_email: str
    status: AdminRoleRequestStatus
    review_comment: str | None
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AdminRoleRequestReviewIn(BaseModel):
    review_comment: str | None = Field(default=None, max_length=500)
