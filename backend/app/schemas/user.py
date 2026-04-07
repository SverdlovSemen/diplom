from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr


UserRole = Literal["admin", "viewer"]


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    role: UserRole
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

