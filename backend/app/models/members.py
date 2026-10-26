"""Team members and password reset (TRD §6.10).

Owners invite staff or an accountant (cpa); owners themselves come only from provisioning, so
`owner` is not an accepted role here (the database refuses it too). Request models forbid
unknown fields.
"""

import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.core.security import AppRole

InvitableRole = Literal["staff", "cpa"]
MemberStatus = Literal["invited", "active"]

_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")


def _email(value: str) -> str:
    value = value.strip().lower()
    if not _EMAIL.fullmatch(value):
        raise ValueError("must be an email address like name@example.com")
    return value


Email = Annotated[str, Field(max_length=254), AfterValidator(_email)]


class MemberInvite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Email
    app_role: InvitableRole


class MemberRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_role: InvitableRole


class MemberOut(BaseModel):
    user_id: UUID
    email: str
    app_role: AppRole
    # invited: the invite hasn't been accepted yet; invite_expired: its link has lapsed (24 h).
    status: MemberStatus
    invite_expired: bool
    invited_at: datetime | None
    last_sign_in_at: datetime | None
    created_at: datetime


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Email


class PasswordResetAccepted(BaseModel):
    detail: str
