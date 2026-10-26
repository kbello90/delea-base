"""Team members against the local stack: invite (email in Mailpit), accept, resend, role change,
remove, the membership re-check, roles and demo tenants. Every write runs on a throwaway tenant
(make_tenant); the seed tenants are only read."""

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.api.v1.members import (
    ALREADY_ACCEPTED,
    ALREADY_INVITED,
    ALREADY_MEMBER,
    CANNOT_INVITE,
    DEMO,
    NOT_YOURSELF,
)
from app.core.security import ACCESS_HEADER, NO_MEMBERSHIP, TokenClaims
from app.db.session import create_pool, open_pool, user_session

from .support import (
    PASSWORD,
    assert_access,
    bearer,
    mail_links,
    refresh,
    run_async,
    set_password,
    sign_in,
    sign_in_session,
    unverified_claims,
    verify_token,
    wait_for_links,
)

pytestmark = pytest.mark.integration

NEW_PASSWORD = "accepted-invite-pass"  # 10+ characters (auth.minimum_password_length)


def invite(client: TestClient, tenant, email: str, role: str = "staff") -> httpx.Response:
    return client.post(
        "/api/v1/members", json={"email": email, "app_role": role}, headers=tenant.headers("owner")
    )


def new_email(tenant, name: str) -> str:
    return tenant.emails["owner"].replace("-owner-", f"-{name}-")


def members_by_email(client: TestClient, tenant) -> dict[str, dict]:
    response = client.get("/api/v1/members", headers=tenant.headers("owner"))
    assert response.status_code == 200
    return {m["email"]: m for m in response.json()}


def accept(settings, email: str, link: str) -> str:
    """Accept an invite the way /accept-invite does; returns a fresh access token."""
    verified = verify_token(settings, "invite", link)
    assert verified.status_code == 200, verified.text
    assert set_password(settings, verified.json()["access_token"], NEW_PASSWORD).status_code == 200
    return sign_in(settings.supabase_url, settings.supabase_anon_key, email, NEW_PASSWORD)


# ---------------------------------------------------------------------------
# List (seed tenant A, read only)
# ---------------------------------------------------------------------------
def test_owner_lists_members_with_email_role_and_status(client: TestClient, users) -> None:
    response = client.get("/api/v1/members", headers=users["owner_a"].headers)
    assert response.status_code == 200
    listed = {m["email"]: m for m in response.json()}
    for name, role in (("owner_a", "owner"), ("staff_a", "staff"), ("cpa_a", "cpa")):
        member = listed[users[name].email]
        assert (member["app_role"], member["status"], member["invite_expired"]) == (
            role,
            "active",
            False,
        )
    assert users["owner_b"].email not in listed


def test_only_the_owner_reaches_the_members_endpoints(client: TestClient, users) -> None:
    some_member = users["staff_a"].user_id
    # Owner B lists their own team; acting on tenant A's members is a 404 (test_change_role).
    assert_access(client, users, "GET", "/api/v1/members", {"owner_a": 200, "owner_b": 200})
    # Non-owners are refused before anything happens; the owner's calls are tested below.
    for method, path, body in (
        ("POST", "/api/v1/members", {"email": "x@test.local", "app_role": "staff"}),
        ("POST", f"/api/v1/members/{some_member}/resend-invite", None),
        ("PATCH", f"/api/v1/members/{some_member}", {"app_role": "cpa"}),
        ("DELETE", f"/api/v1/members/{some_member}", None),
    ):
        for name in ("staff_a", "cpa_a", "no_membership"):
            response = client.request(method, path, json=body, headers=users[name].headers)
            assert response.status_code == 403, (method, path, name)
        assert client.request(method, path, json=body).status_code == 401


def test_staff_cannot_invite(client: TestClient, make_tenant, db) -> None:
    """No layer lets staff invite: no email goes out and no membership is written."""
    tenant = make_tenant(roles=("owner", "staff"))
    email = new_email(tenant, "by-staff")
    response = client.post(
        "/api/v1/members",
        json={"email": email, "app_role": "staff"},
        headers=tenant.headers("staff"),
    )
    assert response.status_code == 403
    assert mail_links(email, "invited") == []
    assert (
        db.execute(
            "select count(*) from public.tenant_members where tenant_id = %s", (tenant.id,)
        ).fetchone()[0]
        == 2
    )


def test_an_invite_cannot_land_in_another_tenant(client: TestClient, make_tenant, db) -> None:
    """The tenant comes from the token only: naming another one is refused, and that tenant
    gets no member."""
    mine, other = make_tenant(), make_tenant()
    email = new_email(mine, "cross")
    response = client.post(
        "/api/v1/members",
        json={"email": email, "app_role": "staff", "tenant_id": other.id},
        headers=mine.headers("owner"),
    )
    assert response.status_code in (403, 422)
    assert (
        db.execute(
            "select count(*) from public.tenant_members where tenant_id = %s", (other.id,)
        ).fetchone()[0]
        == 1
    )


# ---------------------------------------------------------------------------
# Invite and accept
# ---------------------------------------------------------------------------
def test_invite_email_accept_and_sign_in(client: TestClient, settings, make_tenant) -> None:
    tenant = make_tenant()
    email = new_email(tenant, "staff")

    response = invite(client, tenant, email)
    assert response.status_code == 201, response.text
    assert (response.json()["status"], response.json()["app_role"]) == ("invited", "staff")

    (link,) = wait_for_links(email, "invited")
    assert link.startswith("http://localhost:5173/accept-invite?token_hash=")
    assert "type=invite" in link
    assert members_by_email(client, tenant)[email]["status"] == "invited"

    token = accept(settings, email, link)
    claims = unverified_claims(token)
    assert (claims["tenant_id"], claims["app_role"]) == (tenant.id, "staff")
    assert client.get("/api/v1/products", headers=bearer(token)).status_code == 200
    assert members_by_email(client, tenant)[email]["status"] == "active"

    reused = verify_token(settings, "invite", link)
    assert reused.status_code == 403
    assert reused.json()["error_code"] == "otp_expired"


def test_invite_email_names_the_business(make_tenant, client: TestClient) -> None:
    tenant = make_tenant()
    email = new_email(tenant, "cpa")
    assert invite(client, tenant, email, "cpa").status_code == 201
    wait_for_links(email, tenant.business_name)


def test_invite_refusals(client: TestClient, users, make_tenant) -> None:
    tenant = make_tenant(roles=("owner", "staff"))
    pending = new_email(tenant, "pending")
    assert invite(client, tenant, pending).status_code == 201

    cases = [
        (tenant.emails["staff"], 409, ALREADY_MEMBER),
        (pending.upper(), 409, ALREADY_INVITED),
        (users["staff_a"].email, 409, CANNOT_INVITE),  # an account in another tenant
        (users["no_membership"].email, 409, CANNOT_INVITE),  # an account without a membership
    ]
    for email, code, detail in cases:
        response = invite(client, tenant, email)
        assert (response.status_code, response.json()["detail"]) == (code, detail), email
    # The neutral refusal never says where the account belongs.
    assert "Jewelry" not in CANNOT_INVITE and "tenant" not in CANNOT_INVITE.lower()

    assert invite(client, tenant, new_email(tenant, "boss"), role="owner").status_code == 422
    assert invite(client, tenant, "not-an-email").status_code == 422


def test_resend_invite_sends_a_new_link(client: TestClient, settings, make_tenant) -> None:
    tenant = make_tenant(roles=("owner", "staff"))
    email = new_email(tenant, "resend")
    created = invite(client, tenant, email).json()
    wait_for_links(email, "invited")

    response = client.post(
        f"/api/v1/members/{created['user_id']}/resend-invite", headers=tenant.headers("owner")
    )
    assert response.status_code == 202, response.text
    newest, first = wait_for_links(email, "invited", count=2)
    assert newest != first
    # The 24-hour window (invite_expired) restarts with the new link.
    assert response.json()["invited_at"] > created["invited_at"]
    assert verify_token(settings, "invite", first).status_code == 403
    accept(settings, email, newest)

    again = client.post(
        f"/api/v1/members/{created['user_id']}/resend-invite", headers=tenant.headers("owner")
    )
    assert (again.status_code, again.json()["detail"]) == (409, ALREADY_ACCEPTED)


# ---------------------------------------------------------------------------
# Role change and removal
# ---------------------------------------------------------------------------
def test_change_role(client: TestClient, users, make_tenant) -> None:
    tenant = make_tenant(roles=("owner", "staff"))
    staff_id = tenant.user_ids["staff"]
    path = f"/api/v1/members/{staff_id}"
    owner = tenant.headers("owner")

    response = client.patch(path, json={"app_role": "cpa"}, headers=owner)
    assert (response.status_code, response.json()["app_role"]) == (200, "cpa")
    assert client.patch(path, json={"app_role": "owner"}, headers=owner).status_code == 422

    mine = f"/api/v1/members/{tenant.user_ids['owner']}"
    response = client.patch(mine, json={"app_role": "staff"}, headers=owner)
    assert (response.status_code, response.json()["detail"]) == (409, NOT_YOURSELF)

    elsewhere = f"/api/v1/members/{users['staff_a'].user_id}"
    assert client.patch(elsewhere, json={"app_role": "cpa"}, headers=owner).status_code == 404
    assert client.delete(elsewhere, headers=owner).status_code == 404
    assert client.get("/api/v1/products", headers=users["staff_a"].headers).status_code == 200


def test_remove_member_deletes_their_account_and_sessions(
    client: TestClient, settings, make_tenant
) -> None:
    tenant = make_tenant(roles=("owner", "staff"))
    session = sign_in_session(
        settings.supabase_url, settings.supabase_anon_key, tenant.emails["staff"], PASSWORD
    )
    staff_id = tenant.user_ids["staff"]

    response = client.delete(f"/api/v1/members/{staff_id}", headers=tenant.headers("owner"))
    assert response.status_code == 204
    assert tenant.emails["staff"] not in members_by_email(client, tenant)

    # The Auth user is gone, so its refresh token is too.
    admin = httpx.get(
        f"{settings.supabase_url}/auth/v1/admin/users/{staff_id}",
        headers={
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
        },
    )
    assert admin.status_code == 404
    refreshed = refresh(settings.supabase_url, settings.supabase_anon_key, session["refresh_token"])
    assert refreshed.status_code in (400, 401, 403, 404)

    mine = f"/api/v1/members/{tenant.user_ids['owner']}"
    response = client.delete(mine, headers=tenant.headers("owner"))
    assert (response.status_code, response.json()["detail"]) == (409, NOT_YOURSELF)

    # Inviting the same address again works like a first invite.
    assert invite(client, tenant, tenant.emails["staff"]).status_code == 201


def test_a_business_keeps_at_least_one_owner(settings, make_tenant, db) -> None:
    """The database guard, through a real user session (the API never lets the owner act on
    their own membership, so it can't reach this rule by itself)."""
    tenant = make_tenant()
    claims = TokenClaims(sub=tenant.user_ids["owner"], tenant_id=tenant.id, app_role="owner")

    async def remove_own_membership() -> None:
        pool = create_pool(settings)
        await open_pool(pool)
        try:
            async with user_session(pool, claims) as conn:
                await conn.execute(
                    "delete from public.tenant_members where user_id = %s", (claims.sub,)
                )
        finally:
            await pool.close()

    with pytest.raises(psycopg.errors.RaiseException, match="at least one owner"):
        run_async(remove_own_membership())
    assert (
        db.execute(
            "select count(*) from public.tenant_members "
            "where tenant_id = %s and app_role = 'owner'",
            (tenant.id,),
        ).fetchone()[0]
        == 1
    )


# ---------------------------------------------------------------------------
# Membership re-check (a token's claims are fixed for about an hour)
# ---------------------------------------------------------------------------
def assert_membership_refused(response: httpx.Response) -> None:
    assert response.status_code == 401
    assert response.json()["detail"] == "Membership no longer valid"
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


def test_a_removed_members_valid_token_is_refused(client: TestClient, make_tenant) -> None:
    tenant = make_tenant(roles=("owner", "staff"))
    staff = tenant.headers("staff")
    assert client.get("/api/v1/products", headers=staff).status_code == 200

    path = f"/api/v1/members/{tenant.user_ids['staff']}"
    assert client.delete(path, headers=tenant.headers("owner")).status_code == 204
    assert_membership_refused(client.get("/api/v1/products", headers=staff))


def test_a_role_change_takes_effect_on_the_next_request(
    client: TestClient, settings, make_tenant
) -> None:
    tenant = make_tenant(roles=("owner", "staff"))
    old = tenant.headers("staff")
    path = f"/api/v1/members/{tenant.user_ids['staff']}"
    assert client.patch(path, json={"app_role": "cpa"}, headers=tenant.headers("owner")).is_success

    assert_membership_refused(client.get("/api/v1/products", headers=old))
    # What the browser does next: refresh once. The new token carries the new role.
    new = bearer(
        sign_in(settings.supabase_url, settings.supabase_anon_key, tenant.emails["staff"], PASSWORD)
    )
    assert unverified_claims(new["Authorization"][7:])["app_role"] == "cpa"
    assert client.get("/api/v1/invoices", headers=new).status_code == 200
    assert client.get("/api/v1/products", headers=new).status_code == 403


def test_an_inactive_tenant_is_refused(client: TestClient, settings, make_tenant, db) -> None:
    tenant = make_tenant(roles=("owner", "staff"))
    db.execute("update public.tenants set is_active = false where id = %s", (tenant.id,))
    try:
        assert_membership_refused(client.get("/api/v1/products", headers=tenant.headers("staff")))
        assert_membership_refused(client.get("/api/v1/members", headers=tenant.headers("owner")))
        # A new sign-in gets no tenant claims at all: the no-access signal.
        token = sign_in(
            settings.supabase_url, settings.supabase_anon_key, tenant.emails["owner"], PASSWORD
        )
        assert "tenant_id" not in unverified_claims(token)
        response = client.get("/api/v1/products", headers=bearer(token))
        assert response.status_code == 403
        assert response.headers[ACCESS_HEADER] == NO_MEMBERSHIP
    finally:
        db.execute("update public.tenants set is_active = true where id = %s", (tenant.id,))


# ---------------------------------------------------------------------------
# Demo tenants
# ---------------------------------------------------------------------------
def test_team_changes_are_refused_in_a_demo_tenant(client: TestClient, make_tenant) -> None:
    tenant = make_tenant(is_demo=True, roles=("owner", "staff"))
    owner = tenant.headers("owner")
    staff_path = f"/api/v1/members/{tenant.user_ids['staff']}"
    email = new_email(tenant, "demo")

    for response in (
        invite(client, tenant, email),
        client.post(f"{staff_path}/resend-invite", headers=owner),
        client.patch(staff_path, json={"app_role": "cpa"}, headers=owner),
        client.delete(staff_path, headers=owner),
    ):
        assert (response.status_code, response.json()["detail"]) == (409, DEMO)
    assert mail_links(email, "invited") == []
    assert client.get("/api/v1/members", headers=owner).status_code == 200
