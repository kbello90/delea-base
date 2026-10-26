"""Public password reset against the local stack: the email (Mailpit) and its link, the same
answer for every address, nothing for demo accounts, and the abuse limits."""

import hashlib
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.api.v1 import password_reset
from app.api.v1.password_reset import ACCEPTED, IP_LIMIT, TOO_MANY

from .support import mail_links, set_password, sign_in, verify_token, wait_for_links

pytestmark = pytest.mark.integration

PATH = "/api/v1/auth/password-reset"
# Starlette's TestClient connects from this address.
CLIENT_IP_HASH = hashlib.sha256(b"testclient").hexdigest()


@pytest.fixture(autouse=True)
def _fresh_limits(db: psycopg.Connection) -> Iterator[None]:
    """Each test starts with no recorded requests from the test client."""
    db.execute("delete from public.password_reset_attempts where ip_hash = %s", (CLIENT_IP_HASH,))
    yield
    db.execute("delete from public.password_reset_attempts where ip_hash = %s", (CLIENT_IP_HASH,))


def ask(client: TestClient, email: str):
    return client.post(PATH, json={"email": email})


def test_a_reset_email_arrives_and_its_link_sets_a_new_password(
    client: TestClient, settings, make_tenant
) -> None:
    tenant = make_tenant(roles=("owner", "staff"))
    email = tenant.emails["staff"]

    response = ask(client, email.upper())
    assert (response.status_code, response.json()["detail"]) == (202, ACCEPTED)
    (link,) = wait_for_links(email, "Reset your DELEA password")
    assert link.startswith("http://localhost:5173/reset-password?token_hash=")
    assert "type=recovery" in link

    verified = verify_token(settings, "recovery", link)
    assert verified.status_code == 200
    new_password = "a-brand-new-password"
    assert set_password(settings, verified.json()["access_token"], new_password).status_code == 200
    sign_in(settings.supabase_url, settings.supabase_anon_key, email, new_password)
    assert verify_token(settings, "recovery", link).status_code == 403  # used once


def test_the_answer_is_the_same_for_an_unknown_address(client: TestClient, make_tenant) -> None:
    tenant = make_tenant()
    unknown = tenant.emails["owner"].replace("-owner-", "-nobody-")
    known = ask(client, tenant.emails["owner"])
    other = ask(client, unknown)
    assert (other.status_code, other.json()) == (known.status_code, known.json())
    wait_for_links(tenant.emails["owner"], "Reset")
    assert mail_links(unknown, "Reset") == []


def test_both_answers_do_the_same_work_and_send_afterwards(
    client: TestClient, make_tenant, monkeypatch
) -> None:
    """The request itself never looks the address up: it records the attempt (hashes only) and
    hands the address to a background task, for known and unknown addresses alike. That task is
    the only code that reads auth.users, so the response time can't depend on the account."""
    tenant = make_tenant()
    queued: list[str] = []

    async def record(pool, admin, email: str) -> None:
        queued.append(email)

    monkeypatch.setattr(password_reset, "send_password_reset", record)
    unknown = tenant.emails["owner"].replace("-owner-", "-nobody-")
    for email in (tenant.emails["owner"], unknown):
        assert ask(client, email).json()["detail"] == ACCEPTED
    assert queued == [tenant.emails["owner"], unknown]


def test_no_email_for_a_demo_account(client: TestClient, make_tenant) -> None:
    tenant = make_tenant(is_demo=True)
    assert ask(client, tenant.emails["owner"]).status_code == 202
    # A known address on a normal tenant, asked right after, does arrive: the demo one was
    # skipped, not merely slow.
    other = make_tenant()
    assert ask(client, other.emails["owner"]).status_code == 202
    wait_for_links(other.emails["owner"], "Reset")
    assert mail_links(tenant.emails["owner"], "Reset") == []


def test_per_email_cooldown(client: TestClient, make_tenant) -> None:
    tenant = make_tenant()
    email = tenant.emails["owner"]
    first, second = ask(client, email), ask(client, email)
    assert first.json() == second.json() and second.status_code == 202
    wait_for_links(email, "Reset")
    assert len(mail_links(email, "Reset")) == 1


def test_per_ip_rate_limit(client: TestClient) -> None:
    for n in range(IP_LIMIT):
        assert ask(client, f"it-limit-{n}@test.local").status_code == 202
    response = ask(client, "it-limit-over@test.local")
    assert (response.status_code, response.json()["detail"]) == (429, TOO_MANY)


def test_the_request_shape_is_checked(client: TestClient) -> None:
    assert client.post(PATH, json={"email": "nope"}).status_code == 422
    assert client.post(PATH, json={"email": "a@b.co", "redirect": "x"}).status_code == 422


# ---------------------------------------------------------------------------
# Behind a proxy: the limit keys on the real client and a spoofed header doesn't dodge it.
# The API runs behind uvicorn's ProxyHeadersMiddleware (what `uvicorn --proxy-headers
# --forwarded-allow-ips=<proxy>` installs), configured to trust one proxy address.
# ---------------------------------------------------------------------------
PROXY = "10.0.0.1"
REAL_CLIENT = "203.0.113.7"
OTHER_CLIENT = "203.0.113.8"
UNTRUSTED_PEER = "192.0.2.50"


def _ip_hash(ip: str) -> str:
    return hashlib.sha256(ip.encode()).hexdigest()


@pytest.fixture
def behind_proxy(db: psycopg.Connection) -> Iterator:
    """A client for the API behind a trusted proxy at PROXY, connecting from `peer`. One per
    test: the app's database pool lives on the client's event loop."""
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    from app.main import app

    from .support import LOOP_OPTIONS

    ips = (PROXY, REAL_CLIENT, OTHER_CLIENT, UNTRUSTED_PEER)
    hashes = [_ip_hash(ip) for ip in ips]
    db.execute("delete from public.password_reset_attempts where ip_hash = any(%s)", (hashes,))
    opened: list[TestClient] = []
    # This client runs the app's lifespan, which replaces app.state.db_pool with its own pool
    # and closes it on exit; the session-wide `client` fixture's pool is put back afterwards.
    shared_pool = getattr(app.state, "db_pool", None)

    def connect(peer: str) -> TestClient:
        wrapped = ProxyHeadersMiddleware(app, trusted_hosts=PROXY)
        client = TestClient(wrapped, client=(peer, 50000), backend_options=LOOP_OPTIONS)
        client.__enter__()
        opened.append(client)
        return client

    yield connect
    for client in opened:
        client.__exit__(None, None, None)
    app.state.db_pool = shared_pool
    db.execute("delete from public.password_reset_attempts where ip_hash = any(%s)", (hashes,))


def test_behind_the_trusted_proxy_the_limit_keys_on_the_real_client(behind_proxy, db) -> None:
    proxy = behind_proxy(PROXY)
    for n in range(IP_LIMIT):
        sent = proxy.post(
            PATH,
            json={"email": f"it-proxy-{n}@test.local"},
            headers={"X-Forwarded-For": REAL_CLIENT},
        )
        assert sent.status_code == 202
    over = proxy.post(
        PATH, json={"email": "it-proxy-over@test.local"}, headers={"X-Forwarded-For": REAL_CLIENT}
    )
    assert over.status_code == 429
    # Another client behind the same proxy has its own budget: the proxy's address isn't the key.
    other = proxy.post(
        PATH, json={"email": "it-proxy-other@test.local"}, headers={"X-Forwarded-For": OTHER_CLIENT}
    )
    assert other.status_code == 202
    assert (
        db.execute(
            "select count(*) from public.password_reset_attempts where ip_hash = %s",
            (_ip_hash(PROXY),),
        ).fetchone()[0]
        == 0
    )


def test_a_spoofed_forwarded_header_cannot_dodge_the_limit(behind_proxy) -> None:
    """Through the proxy: the client sends its own X-Forwarded-For with a fresh fake address each
    time, and the proxy appends the real one. The rightmost untrusted address is the key."""
    proxy = behind_proxy(PROXY)
    for n in range(IP_LIMIT):
        spoofed = {"X-Forwarded-For": f"198.51.100.{n}, {REAL_CLIENT}"}
        assert (
            proxy.post(
                PATH, json={"email": f"it-spoof-{n}@test.local"}, headers=spoofed
            ).status_code
            == 202
        )
    spoofed = {"X-Forwarded-For": f"198.51.100.99, {REAL_CLIENT}"}
    assert (
        proxy.post(PATH, json={"email": "it-spoof-over@test.local"}, headers=spoofed).status_code
        == 429
    )


def test_a_forwarded_header_from_an_untrusted_peer_is_ignored(behind_proxy) -> None:
    """Straight to the API from an address that isn't the proxy: the header doesn't count."""
    direct = behind_proxy(UNTRUSTED_PEER)
    for n in range(IP_LIMIT):
        fake = {"X-Forwarded-For": f"198.51.100.{n + 10}"}
        assert (
            direct.post(PATH, json={"email": f"it-direct-{n}@test.local"}, headers=fake).status_code
            == 202
        )
    fake = {"X-Forwarded-For": "198.51.100.200"}
    assert (
        direct.post(PATH, json={"email": "it-direct-over@test.local"}, headers=fake).status_code
        == 429
    )
