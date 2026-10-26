"""Auth admin client: how Auth's answers to invite and recover are mapped (no network)."""

import httpx
import pytest

from app.core.auth_admin import AuthAdmin, AuthAdminError, EmailExists

from .integration.support import run_async


def admin_answering(status: int, body: dict) -> tuple[AuthAdmin, list[httpx.Request]]:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(status, json=body)

    admin = AuthAdmin("http://auth.test", "service-key")
    object.__setattr__(
        admin,
        "_client",
        lambda: httpx.AsyncClient(
            base_url="http://auth.test/auth/v1",
            headers={"apikey": "service-key"},
            transport=httpx.MockTransport(handler),
        ),
    )
    return admin, sent


def test_invite_returns_the_user_id_and_sends_the_metadata() -> None:
    admin, sent = admin_answering(200, {"id": "u-1"})
    assert run_async(admin.invite("a@b.co", {"business_name": "Atelier"})) == "u-1"
    assert sent[0].url.path == "/auth/v1/invite"
    assert sent[0].read() == b'{"email":"a@b.co","data":{"business_name":"Atelier"}}'


def test_invite_of_a_confirmed_account_raises_email_exists() -> None:
    admin, _ = admin_answering(422, {"error_code": "email_exists", "msg": "exists"})
    with pytest.raises(EmailExists):
        run_async(admin.invite("a@b.co", {}))


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (429, {"error_code": "over_email_send_rate_limit"}),
        (500, {}),
        (422, {"error_code": "validation_failed"}),
    ],
)
def test_other_invite_failures_raise_auth_admin_error(status: int, body: dict) -> None:
    admin, _ = admin_answering(status, body)
    with pytest.raises(AuthAdminError) as info:
        run_async(admin.invite("a@b.co", {}))
    assert not isinstance(info.value, EmailExists)


def test_recover() -> None:
    admin, sent = admin_answering(200, {})
    run_async(admin.send_recovery("a@b.co"))
    assert sent[0].url.path == "/auth/v1/recover"
    failing, _ = admin_answering(500, {})
    with pytest.raises(AuthAdminError):
        run_async(failing.send_recovery("a@b.co"))
