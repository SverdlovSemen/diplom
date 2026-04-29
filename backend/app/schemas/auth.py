from __future__ import annotations

from pydantic import BaseModel, EmailStr
from pydantic import Field

from app.models.user import UserRole


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class AuthUserOut(BaseModel):
    id: str
    email: EmailStr
    role: UserRole


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUserOut
