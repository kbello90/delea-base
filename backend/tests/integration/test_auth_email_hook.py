"""The Send Email Hook against the local stack (hosted-demo plan step 4, TRD §6.6).

The local stack keeps Auth's own mailer (the hook isn't switched on in config.toml: it would make
every local invite depend on an API running on a fixed port). So these tests call the endpoint
the way Supabase does, with signed payloads, and real one-time tokens from Auth's admin
generate_link (which sends nothing): an email that goes out lands in Mailpit, and its link is
then verified by Auth exactly as the admin's /accept-invite and /reset-password pages do. The
full path, Auth calling the hook, is proven on the hosted project after the API is deployed.

Throwaway tenants and users only; everything is deleted afterwards.
"""

import base64
import json
import logging
import time
import uuid
from collections.abc import Iterator

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core.auth_admin import AuthAdmin
from app.core.config import Settings, get_settings
from app.core.standard_webhooks import sign

from .support import MAILPIT_URL, PASSWORD, mail_links, run_async, verify_token, wait_for_links

pytestmark = pytest.mark.integration

URL = "/api/v1/auth/email-hook"
KEY = b"integration-only-hook-secret-32b"
SECRET = "v1,whsec_" + base64.b64encode(KEY).decode()


@pytest.fixture(autouse=True)
def _hook_on(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "send_email_hook_secret", SECRET)
    monkeypatch.setattr(get_settings(), "email_provider", "mailpit")


@pytest.fixture
def accounts(settings: Settings) -> Iterator:
    """Make extra Auth users (or invite links for new ones); delete them afterwards."""
    admin = AuthAdmin(settings.supabase_url, settings.supabase_service_role_key)
    made: list[str] = []

    def confirmed(prefix: str) -> tuple[str, str]:
        email = f"it-hook-{prefix}-{uuid.uuid4().hex[:8]}@test.local"
        user_id = run_async(admin.ensure_user(email, PASSWORD))
        made.append(user_id)
        return user_id, email

    def generate(kind: str, email: str) -> tuple[str, str]:
        """(user id, token hash) from Auth's admin generate_link: a real token, no email."""
        response = httpx.post(
            f"{settings.supabase_url}/auth/v1/admin/generate_link",
            headers={
                "apikey": settings.supabase_service_role_key,
                "Authorization": f"Bearer {settings.supabase_service_role_key}",
            },
            json={"type": kind, "email": email},
            timeout=10,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        hashed = body.get("hashed_token") or body["properties"]["hashed_token"]
        user_id = body.get("id") or body["user"]["id"]
        if user_id not in made:
            made.append(user_id)
        return user_id, hashed

    yield confirmed, generate
    for user_id in made:
        run_async(admin.delete_user(user_id))


def payload(user_id: str, email: str, kind: str, token_hash: str = "", tenant_id=None) -> dict:
    metadata = {"business_name": "Ignored Name"}
    if tenant_id is not None:
        metadata["tenant_id"] = str(tenant_id)
    return {
        "user": {"id": user_id, "email": email, "user_metadata": metadata, "aud": "authenticated"},
        "email_data": {
            "token": "123456",
            "token_hash": token_hash or uuid.uuid4().hex,
            "redirect_to": "https://evil.example/steal",
            "email_action_type": kind,
            "site_url": "https://evil.example",
            "token_new": "",
            "token_hash_new": "",
        },
    }


def deliver(
    client: TestClient, body: dict, *, at: int | None = None, key: bytes = KEY, wid: str = ""
) -> httpx.Response:
    raw = json.dumps(body).encode()
    wid = wid or f"msg_{uuid.uuid4().hex}"
    stamp = str(at if at is not None else int(time.time()))
    headers = {
        "content-type": "application/json",
        "webhook-id": wid,
        "webhook-timestamp": stamp,
        "webhook-signature": sign(key, wid, stamp, raw),
    }
    return client.post(URL, content=raw, headers=headers)


def nothing_to(address: str) -> bool:
    found = httpx.get(
        f"{MAILPIT_URL}/api/v1/search", params={"query": f"to:{address}"}, timeout=10
    ).json()
    return found["messages_count"] == 0


def message_to(address: str) -> dict:
    found = httpx.get(
        f"{MAILPIT_URL}/api/v1/search", params={"query": f"to:{address}"}, timeout=10
    ).json()["messages"]
    assert len(found) == 1, f"{len(found)} messages to {address}"
    return httpx.get(f"{MAILPIT_URL}/api/v1/message/{found[0]['ID']}", timeout=10).json()


# ---------------------------------------------------------------------------
# What is sent, with links Auth accepts
# ---------------------------------------------------------------------------
def test_an_invite_for_a_real_shop_arrives_and_its_link_accepts_the_invite(
    client, make_tenant, accounts, settings
) -> None:
    shop = make_tenant()
    _, generate = accounts
    email = f"it-hook-invitee-{uuid.uuid4().hex[:8]}@test.local"
    user_id, token = generate("invite", email)

    response = deliver(client, payload(user_id, email, "invite", token, shop.id))
    assert (response.status_code, response.json()) == (200, {})

    message = message_to(email)
    assert message["Subject"] == f"You're invited to {shop.business_name} on DELEA"
    assert message["From"]["Name"] == "DELEA"
    assert message["From"]["Address"] == f"notifications@{settings.email_from_domain}"
    link = wait_for_links(email, "invited")[0]
    assert link.startswith(f"{settings.admin_app_url.rstrip('/')}/accept-invite?token_hash=")
    assert "evil.example" not in message["HTML"] + message["Text"]  # redirect_to is ignored
    assert verify_token(settings, "invite", link).status_code == 200


def test_a_reset_for_a_member_arrives_and_its_link_works(
    client, make_tenant, accounts, settings
) -> None:
    shop = make_tenant(roles=("owner", "staff"))
    _, generate = accounts
    email = shop.emails["staff"]
    user_id, token = generate("recovery", email)
    assert user_id == shop.user_ids["staff"]

    assert deliver(client, payload(user_id, email, "recovery", token)).status_code == 200

    message = message_to(email)
    assert (message["Subject"], message["From"]["Name"]) == ("Reset your DELEA password", "DELEA")
    link = wait_for_links(email, "Reset")[0]
    assert "/reset-password?token_hash=" in link
    assert verify_token(settings, "recovery", link).status_code == 200


# ---------------------------------------------------------------------------
# What is refused: 200, nothing sent, the reason logged (never the token or the address)
# ---------------------------------------------------------------------------
def test_refusals_answer_200_send_nothing_and_log_only_the_reason(
    client, make_tenant, accounts, caplog
) -> None:
    real = make_tenant()
    demo = make_tenant(is_demo=True, roles=("owner",))
    confirmed, _ = accounts
    leftover_id, leftover = confirmed("leftover")
    invitee_id, invitee = confirmed("invitee")
    member_id, member = real.user_ids["owner"], real.emails["owner"]
    demo_id, demo_email = demo.user_ids["owner"], demo.emails["owner"]

    cases = [
        (demo_id, demo_email, "recovery", None, "demo"),
        (leftover_id, leftover, "recovery", None, "no membership"),
        (invitee_id, invitee, "invite", demo.id, "demo"),
        (invitee_id, invitee, "invite", None, "unknown shop"),
        (member_id, member, "magiclink", real.id, "type not used"),
        (member_id, member, "email_change", real.id, "type not used"),
        (member_id, member, "signup", real.id, "type not used"),
        (member_id, member, "reauthentication", real.id, "type not used"),
        (str(uuid.uuid4()), "nobody@test.local", "recovery", None, "no account"),
    ]
    caplog.set_level(logging.INFO, logger="app.api.v1.auth_email_hook")
    secret_token = "TOKENHASH-" + uuid.uuid4().hex
    for user_id, email, kind, tenant_id, reason in cases:
        caplog.clear()
        response = deliver(client, payload(user_id, email, kind, secret_token, tenant_id))
        assert (response.status_code, response.json()) == (200, {}), (kind, reason)
        logged = caplog.text
        assert f"reason={reason}" in logged and f"type={kind}" in logged, (kind, logged)
        assert secret_token not in logged and email not in logged
    for address in (demo_email, leftover, invitee, member, "nobody@test.local"):
        assert nothing_to(address), address


def test_a_real_member_asking_for_a_magic_link_gets_nothing(client, make_tenant) -> None:
    shop = make_tenant()
    body = payload(shop.user_ids["owner"], shop.emails["owner"], "magiclink", tenant_id=shop.id)
    assert deliver(client, body).status_code == 200
    assert nothing_to(shop.emails["owner"])


# ---------------------------------------------------------------------------
# The request itself
# ---------------------------------------------------------------------------
def test_without_a_secret_the_endpoint_does_not_exist(client, make_tenant, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "send_email_hook_secret", "")
    shop = make_tenant()
    body = payload(shop.user_ids["owner"], shop.emails["owner"], "recovery")
    response = deliver(client, body)
    assert response.status_code == 404
    assert nothing_to(shop.emails["owner"])


@pytest.mark.parametrize("tamper", ["wrong key", "old", "future", "no signature", "changed body"])
def test_an_unsigned_or_stale_request_is_refused_and_sends_nothing(
    client, make_tenant, tamper
) -> None:
    shop = make_tenant()
    email = shop.emails["owner"]
    body = payload(shop.user_ids["owner"], email, "recovery")
    if tamper == "wrong key":
        response = deliver(client, body, key=b"another-secret-of-the-same-size!")
    elif tamper == "old":
        response = deliver(client, body, at=int(time.time()) - 301)
    elif tamper == "future":
        response = deliver(client, body, at=int(time.time()) + 301)
    else:
        raw = json.dumps(body).encode()
        wid, stamp = "msg_x", str(int(time.time()))
        headers = {
            "webhook-id": wid,
            "webhook-timestamp": stamp,
            "content-type": "application/json",
        }
        if tamper == "changed body":
            headers["webhook-signature"] = sign(KEY, wid, stamp, raw)
            raw = raw.replace(b"recovery", b"invite\x20\x20")
        response = client.post(URL, content=raw, headers=headers)
    assert response.status_code == 401, tamper
    assert response.headers["content-type"].startswith("application/json")
    assert nothing_to(email)


def test_an_oversized_or_broken_body_is_refused(client) -> None:
    big = {"user": {"id": "x", "email": "x@test.local"}, "email_data": {"pad": "a" * 70_000}}
    assert deliver(client, big).status_code == 400
    raw = b"not json"
    wid, stamp = "msg_y", str(int(time.time()))
    headers = {
        "webhook-id": wid,
        "webhook-timestamp": stamp,
        "webhook-signature": sign(KEY, wid, stamp, raw),
    }
    assert client.post(URL, content=raw, headers=headers).status_code == 400


def test_a_provider_outage_is_a_503_so_auth_retries(client, make_tenant, monkeypatch) -> None:
    shop = make_tenant(roles=("owner", "staff"))
    monkeypatch.setattr(get_settings(), "mailpit_url", "http://127.0.0.1:9")  # nothing listens
    body = payload(shop.user_ids["staff"], shop.emails["staff"], "recovery")
    started = time.monotonic()
    response = deliver(client, body)
    assert response.status_code == 503
    assert time.monotonic() - started < 5  # inside Auth's 5 s budget
    assert nothing_to(shop.emails["staff"])


def test_email_off_is_a_503_not_a_silent_success(client, make_tenant, monkeypatch) -> None:
    shop = make_tenant(roles=("owner", "staff"))
    monkeypatch.setattr(get_settings(), "email_provider", "off")
    body = payload(shop.user_ids["staff"], shop.emails["staff"], "recovery")
    assert deliver(client, body).status_code == 503


# ---------------------------------------------------------------------------
# The invite carries the shop, so the hook can refuse a demo shop
# ---------------------------------------------------------------------------
def test_an_owner_invite_records_the_shop_in_the_new_users_metadata(
    client, make_tenant, db: psycopg.Connection
) -> None:
    shop = make_tenant()
    email = f"it-hook-member-{uuid.uuid4().hex[:8]}@test.local"
    response = client.post(
        "/api/v1/members",
        json={"email": email, "app_role": "staff"},
        headers=shop.headers("owner"),
    )
    assert response.status_code == 201, response.text
    (metadata,) = db.execute(
        "select raw_user_meta_data from auth.users where email = %s", (email,)
    ).fetchone()
    assert metadata["tenant_id"] == shop.id
    assert metadata["business_name"] == shop.business_name
    # Auth's own mailer (the local stack has no hook) still sends it, as before.
    assert mail_links(email, "invited")
