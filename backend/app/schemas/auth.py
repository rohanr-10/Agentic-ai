"""Auth & user schemas."""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.enums import UserRole

# Deliberately conservative (RFC-5322-lite); we do not depend on
# email-validator to keep the dependency footprint small.
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)

COMMON_PASSWORDS = {
    "password", "password1", "12345678", "123456789", "qwerty123",
    "letmein123", "password123", "admin12345", "welcome123", "changeme123",
}


def validate_email_format(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 320 or not _EMAIL_RE.match(value):
        raise ValueError("invalid email address format")
    return value


class RegisterIn(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=120)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return validate_email_format(v)


class LoginIn(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return v.strip().lower()


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    display_name: str
    role: UserRole
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class UserUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=512)


class LogoutIn(BaseModel):
    refresh_token: str | None = Field(default=None, max_length=512)


class PasswordResetRequestIn(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return v.strip().lower()


class PasswordResetConfirmIn(BaseModel):
    token: str = Field(min_length=10, max_length=512)
    new_password: str = Field(min_length=1, max_length=256)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


def validate_password_strength(password: str, *, min_length: int) -> str:
    if len(password) < min_length:
        raise ValueError(f"password must be at least {min_length} characters long")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValueError("password must contain at least one letter and one digit")
    lowered = password.lower()
    if any(lowered.startswith(common) or common in lowered for common in COMMON_PASSWORDS):
        raise ValueError("this password is too common, choose a stronger one")
    return password
