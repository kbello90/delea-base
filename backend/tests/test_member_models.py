"""Team member and password-reset request models (no database)."""

import pytest
from pydantic import ValidationError

from app.models.members import MemberInvite, MemberRoleUpdate, PasswordResetRequest


def test_email_is_trimmed_and_lowercased() -> None:
    invite = MemberInvite(email="  Ana.Lopez+Shop@Example.COM ", app_role="staff")
    assert invite.email == "ana.lopez+shop@example.com"


@pytest.mark.parametrize("email", ["", "ana", "ana@", "@example.com", "ana@example", "a b@c.de"])
def test_bad_emails_are_refused(email: str) -> None:
    with pytest.raises(ValidationError):
        MemberInvite(email=email, app_role="staff")


@pytest.mark.parametrize("role", ["staff", "cpa"])
def test_staff_and_cpa_can_be_invited(role: str) -> None:
    assert MemberInvite(email="a@b.co", app_role=role).app_role == role


@pytest.mark.parametrize("model", [MemberInvite, MemberRoleUpdate])
def test_nobody_is_invited_as_or_made_owner(model) -> None:
    fields = {"email": "a@b.co"} if model is MemberInvite else {}
    with pytest.raises(ValidationError):
        model(app_role="owner", **fields)


def test_unknown_fields_are_refused() -> None:
    with pytest.raises(ValidationError):
        MemberInvite(email="a@b.co", app_role="staff", tenant_id="x")
    with pytest.raises(ValidationError):
        PasswordResetRequest(email="a@b.co", redirect_to="https://evil.example")
