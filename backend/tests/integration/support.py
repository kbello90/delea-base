"""Shared constants and helpers for the integration tests."""

import asyncio
import html
import re
import time
import uuid
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt

from app.core.security import TokenClaims

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
TEST_SKU_PREFIX = "IT-"
PASSWORD = "local-integration-only-9f2c"  # local throwaway users only

START_HINT = (
    "start it with `npx supabase@2.121.0 start` and put the keys from "
    "`npx supabase@2.121.0 status -o env` in backend/.env (see backend/README.md)"
)

# psycopg's async mode cannot run on Windows' default Proactor loop.
LOOP_OPTIONS = {"loop_factory": asyncio.SelectorEventLoop}


@dataclass(frozen=True)
class TestUser:
    __test__ = False  # not a test class

    name: str
    user_id: str
    email: str
    token: str
    tenant_id: str | None
    app_role: str | None

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    @property
    def claims(self) -> TokenClaims:
        assert self.tenant_id and self.app_role
        return TokenClaims(sub=self.user_id, tenant_id=self.tenant_id, app_role=self.app_role)


def run_async(coro):
    """Run a coroutine to completion on a selector loop (psycopg async needs one on Windows)."""
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(coro)


def sign_in(supabase_url: str, anon_key: str, email: str, password: str) -> str:
    """A fresh access token (the hook reads tenant_members at this moment)."""
    response = httpx.post(
        f"{supabase_url}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": anon_key},
        json={"email": email, "password": password},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def unverified_claims(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


def new_sku() -> str:
    return f"{TEST_SKU_PREFIX}{uuid.uuid4().hex[:10].upper()}"


def product_payload(**overrides: object) -> dict:
    payload: dict[str, object] = {
        "sku": new_sku(),
        "name": "Integration Test Ring",
        "metal": "silver_925",
        "price_cents": 12500,
        "stock_qty": 4,
        "is_published": True,
        "sort_order": 99,
    }
    payload.update(overrides)
    return payload


# The five test identities, in a fixed order for readable failures.
IDENTITIES = ("owner_a", "staff_a", "cpa_a", "owner_b", "no_membership")


def assert_access(client, users: dict, method: str, path: str, expected: dict, json=None) -> None:
    """Call `path` as every identity and compare the status codes with `expected`.

    `expected` maps identity -> status; identities left out must get 403. Writes that succeed
    are the caller's to clean up (test rows carry the IT- prefix).
    """
    got = {}
    for name in IDENTITIES:
        body = json(name) if callable(json) else json
        response = client.request(method, path, json=body, headers=users[name].headers)
        got[name] = response.status_code
    want = {name: expected.get(name, 403) for name in IDENTITIES}
    assert got == want, f"{method} {path}: got {got}, want {want}"


def force_currency(db, tenant_id: str, currency: str) -> None:
    """Test setup only: set a tenant's currency even though it has data. The currency guard
    (migration 20261023000200) refuses that for every caller; it is tested on its own in
    pgTAP, so here triggers are switched off for this one statement."""
    with db.transaction():
        db.execute("set local session_replication_role = replica")
        db.execute(
            "update public.company_settings set currency = %s where tenant_id = %s",
            (currency, tenant_id),
        )


# ---------------------------------------------------------------------------
# Email (Mailpit, the local stack's mail catcher: supabase/config.toml [local_smtp])
# ---------------------------------------------------------------------------
MAILPIT_URL = "http://127.0.0.1:55324"
_LINK = re.compile(r'href="([^"]+)"')


def mail_links(to: str, subject_part: str) -> list[str]:
    """The first link of every message to `to` whose subject contains `subject_part`, newest
    first."""
    found = httpx.get(
        f"{MAILPIT_URL}/api/v1/search", params={"query": f"to:{to}"}, timeout=10
    ).json()["messages"]
    links = []
    for message in found:
        if subject_part not in message["Subject"]:
            continue
        body = httpx.get(f"{MAILPIT_URL}/api/v1/message/{message['ID']}", timeout=10).json()
        links.append(html.unescape(_LINK.search(body["HTML"]).group(1)))
    return links


def wait_for_links(to: str, subject_part: str, count: int = 1, timeout: float = 10) -> list[str]:
    """Wait until at least `count` matching messages arrived; newest first."""
    deadline = time.monotonic() + timeout
    while True:
        links = mail_links(to, subject_part)
        if len(links) >= count or time.monotonic() > deadline:
            assert len(links) >= count, f"expected {count} '{subject_part}' email(s) to {to}"
            return links
        time.sleep(0.3)


def token_hash(link: str) -> str:
    return parse_qs(urlsplit(link).query)["token_hash"][0]


def verify_token(settings, kind: str, link: str) -> httpx.Response:
    """What the admin's /accept-invite and /reset-password pages do with the link."""
    return httpx.post(
        f"{settings.supabase_url}/auth/v1/verify",
        headers={"apikey": settings.supabase_anon_key},
        json={"type": kind, "token_hash": token_hash(link)},
        timeout=10,
    )


def set_password(settings, access_token: str, password: str) -> httpx.Response:
    return httpx.put(
        f"{settings.supabase_url}/auth/v1/user",
        headers={"apikey": settings.supabase_anon_key, "Authorization": f"Bearer {access_token}"},
        json={"password": password},
        timeout=10,
    )


def sign_in_session(supabase_url: str, anon_key: str, email: str, password: str) -> dict:
    """The whole session (access and refresh tokens)."""
    response = httpx.post(
        f"{supabase_url}/auth/v1/token",
        params={"grant_type": "password"},
        headers={"apikey": anon_key},
        json={"email": email, "password": password},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def refresh(supabase_url: str, anon_key: str, refresh_token: str) -> httpx.Response:
    return httpx.post(
        f"{supabase_url}/auth/v1/token",
        params={"grant_type": "refresh_token"},
        headers={"apikey": anon_key},
        json={"refresh_token": refresh_token},
        timeout=10,
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass(frozen=True)
class ThrowawayTenant:
    id: str
    business_name: str
    emails: dict[str, str]
    user_ids: dict[str, str]
    tokens: dict[str, str]

    def headers(self, role: str) -> dict[str, str]:
        return bearer(self.tokens[role])
