"""Team members: list, invite, resend an invite, change a role, remove (TRD §6.10). Owner only.

Rules (the database enforces them too: tenant_members_guard, migration 20261025000100):
- Owners invite staff or an accountant (cpa). Owners come only from provisioning: nobody is
  invited as, or promoted to, owner.
- A business keeps at least one owner. The owner can't change or remove their own membership.
- Nothing changes in a demo tenant (409 here, 42501 in the database).
- One account holds one membership (the access token hook reads one). An email that already
  has an account elsewhere can't be invited; the message never says where it belongs.

Invite: Auth creates the user and sends the email (link to the admin's /accept-invite page),
then the membership is inserted in the owner's RLS session; if that fails, the new Auth user
is deleted again. Remove: the membership is deleted (guard checked) and committed; then, if the
account has no membership left, its Auth user is deleted too, which ends its sessions and
refresh tokens everywhere.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.api.deps import AuthAdminClient, CurrentClaims, DbConn, get_pool, require_role
from app.core.auth_admin import AuthAdminError, EmailExists
from app.core.errors import ConflictError, NotFoundError
from app.core.security import TokenClaims
from app.db.session import user_session
from app.models.members import MemberInvite, MemberOut, MemberRoleUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/members", tags=["members"])

OwnerOnly = Depends(require_role("owner"))
Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]

DEMO = "Team changes are turned off in the demo."
CANNOT_INVITE = (
    "This email can't be invited. Ask them for a different address "
    "(for example name+yourbusiness@gmail.com)."
)
ALREADY_MEMBER = "This email is already on your team."
ALREADY_INVITED = "This email is already invited. Use Resend invite to send a new link."
NOT_YOURSELF = "You can't change or remove your own membership."
ALREADY_ACCEPTED = "This invite was already accepted."
NOT_FOUND = "Team member not found"


def _email_unavailable() -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE, "Email invites are temporarily unavailable"
    )


async def _tenant(conn: AsyncConnection, claims: TokenClaims) -> tuple[bool, str]:
    """(is_demo, business name) of the caller's tenant."""
    cur = await conn.execute(
        "select t.is_demo, b.business_name from public.tenants t "
        "join public.brand_config b on b.tenant_id = t.id where t.id = %s",
        (claims.tenant_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Business not found")
    return row[0], row[1]


async def _refuse_demo(conn: AsyncConnection, claims: TokenClaims) -> str:
    is_demo, business_name = await _tenant(conn, claims)
    if is_demo:
        raise ConflictError(DEMO)
    return business_name


async def _members(conn: AsyncConnection, user_id: UUID | None = None) -> list[MemberOut]:
    query = "select * from public.tenant_member_list()"
    params: tuple = ()
    if user_id is not None:
        query += " where user_id = %s"
        params = (user_id,)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, params)
        return [MemberOut.model_validate(row) for row in await cur.fetchall()]


async def _member(conn: AsyncConnection, user_id: UUID) -> MemberOut:
    found = await _members(conn, user_id)
    if not found:
        raise NotFoundError(NOT_FOUND)
    return found[0]


def _not_yourself(claims: TokenClaims, user_id: UUID) -> None:
    if str(user_id) == claims.sub:
        raise ConflictError(NOT_YOURSELF)


@router.get("", response_model=list[MemberOut], dependencies=[OwnerOnly])
async def list_members(conn: DbConn) -> list[MemberOut]:
    return await _members(conn)


@router.post(
    "", response_model=MemberOut, status_code=status.HTTP_201_CREATED, dependencies=[OwnerOnly]
)
async def invite_member(
    body: MemberInvite, claims: CurrentClaims, pool: Pool, admin: AuthAdminClient
) -> MemberOut:
    async with user_session(pool, claims) as conn:
        business_name = await _refuse_demo(conn, claims)
        cur = await conn.execute(
            "select confirmed, member_here from public.account_memberships(%s)", (body.email,)
        )
        account = await cur.fetchone()
    if account is not None:
        confirmed, member_here = account
        if member_here:
            raise ConflictError(ALREADY_MEMBER if confirmed else ALREADY_INVITED)
        raise ConflictError(CANNOT_INVITE)

    try:
        user_id = await admin.invite(body.email, {"business_name": business_name})
    except EmailExists:
        raise ConflictError(CANNOT_INVITE) from None
    except AuthAdminError as exc:
        logger.warning("Invite failed: %s", exc)
        raise _email_unavailable() from exc

    try:
        async with user_session(pool, claims) as conn:
            await conn.execute(
                "insert into public.tenant_members (user_id, tenant_id, app_role, invited_by) "
                "values (%s, %s, %s::public.app_role, %s)",
                (user_id, claims.tenant_id, body.app_role, claims.sub),
            )
            return await _member(conn, UUID(user_id))
    except BaseException:
        # The account was created for this invite only; don't leave it behind.
        try:
            await admin.delete_user(user_id)
        except AuthAdminError:
            logger.exception("Could not delete Auth user %s after a failed invite", user_id)
        raise


@router.post(
    "/{user_id}/resend-invite",
    response_model=MemberOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[OwnerOnly],
)
async def resend_invite(
    user_id: UUID, claims: CurrentClaims, pool: Pool, admin: AuthAdminClient
) -> MemberOut:
    """A new invite email with a new link (the previous one stops working)."""
    async with user_session(pool, claims) as conn:
        business_name = await _refuse_demo(conn, claims)
        member = await _member(conn, user_id)
    if member.status != "invited":
        raise ConflictError(ALREADY_ACCEPTED)
    try:
        await admin.invite(member.email, {"business_name": business_name})
    except EmailExists:
        raise ConflictError(ALREADY_ACCEPTED) from None
    except AuthAdminError as exc:
        logger.warning("Resend invite failed: %s", exc)
        raise _email_unavailable() from exc
    async with user_session(pool, claims) as conn:
        return await _member(conn, user_id)


@router.patch("/{user_id}", response_model=MemberOut, dependencies=[OwnerOnly])
async def change_role(
    user_id: UUID, body: MemberRoleUpdate, claims: CurrentClaims, conn: DbConn
) -> MemberOut:
    _not_yourself(claims, user_id)
    await _refuse_demo(conn, claims)
    cur = await conn.execute(
        "update public.tenant_members set app_role = %s::public.app_role "
        "where tenant_id = %s and user_id = %s returning user_id",
        (body.app_role, claims.tenant_id, user_id),
    )
    if await cur.fetchone() is None:
        raise NotFoundError(NOT_FOUND)
    return await _member(conn, user_id)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[OwnerOnly])
async def remove_member(
    user_id: UUID, claims: CurrentClaims, pool: Pool, admin: AuthAdminClient
) -> Response:
    _not_yourself(claims, user_id)
    async with user_session(pool, claims) as conn:
        await _refuse_demo(conn, claims)
        member = await _member(conn, user_id)
        await conn.execute(
            "delete from public.tenant_members where tenant_id = %s and user_id = %s",
            (claims.tenant_id, user_id),
        )
        # Seen inside this transaction: the membership just deleted is gone.
        cur = await conn.execute(
            "select member_elsewhere from public.account_memberships(%s)", (member.email,)
        )
        row = await cur.fetchone()
        orphaned = row is not None and not row[0]
    if orphaned:
        # After the commit: Auth's delete cascades to tenant_members, which this transaction
        # had locked. A failure leaves an account without a membership, which can't use the
        # API (no claims at its next sign-in).
        try:
            await admin.delete_user(str(user_id))
        except AuthAdminError:
            logger.exception("Could not delete Auth user %s after removing them", user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
