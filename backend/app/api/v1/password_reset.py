"""Public password reset: POST /auth/password-reset (TRD §6.10). No token needed.

The answer is always the same 202, whether or not the address has an account, and the request
does the same work either way: the abuse checks and one insert, on hashes only. Looking the
account up and asking Auth to send the email happen in a background task after the response.

Abuse limits (public.password_reset_attempts, SHA-256 hashes of the email and the client IP):
- per IP: at most IP_LIMIT requests per IP_WINDOW, then 429;
- per email: a second request within EMAIL_COOLDOWN gets the same 202 but sends nothing.
Auth adds its own limits on top (one recovery email per address per 60 s, and the project's
email rate limit).

No email is sent for an account that belongs to a demo tenant. The client IP is
request.client, which uvicorn takes from X-Forwarded-For only for peers in FORWARDED_ALLOW_IPS.
Behind a proxy that must list the proxy's addresses (never "*", which trusts the leftmost,
client-written entry); otherwise every client shares the proxy's address. TRD §6.10.
"""

import hashlib
import logging
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from psycopg_pool import AsyncConnectionPool

from app.api.deps import AuthAdminClient, get_pool
from app.core.auth_admin import AuthAdmin
from app.db.session import service_session
from app.models.members import PasswordResetAccepted, PasswordResetRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

IP_LIMIT = 5
IP_WINDOW = timedelta(minutes=15)
EMAIL_COOLDOWN = timedelta(seconds=60)
KEEP = timedelta(days=1)

ACCEPTED = "If an account exists for this email, we've sent a link to reset the password."
TOO_MANY = "Too many requests. Wait a few minutes and try again."


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def send_password_reset(pool: AsyncConnectionPool, admin: AuthAdmin, email: str) -> None:
    """Background task: email a reset link if the account exists and isn't a demo account."""
    try:
        async with service_session(pool) as conn:
            cur = await conn.execute(
                "select exists (select 1 from public.tenant_members m "
                "join public.tenants t on t.id = m.tenant_id "
                "where m.user_id = u.id and t.is_demo) "
                "from auth.users u where lower(u.email) = %s",
                (email,),
            )
            row = await cur.fetchone()
        if row is None or row[0]:
            return
        await admin.send_recovery(email)
    except Exception:  # a background task has no caller to report to
        logger.exception("Password reset email could not be sent")


@router.post(
    "/password-reset",
    response_model=PasswordResetAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_password_reset(
    body: PasswordResetRequest,
    request: Request,
    background: BackgroundTasks,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    admin: AuthAdminClient,
) -> PasswordResetAccepted:
    email_hash = _hash(body.email)
    ip_hash = _hash(request.client.host if request.client else "unknown")
    async with service_session(pool) as conn:
        # One check at a time per IP, so parallel requests can't slip past the limit.
        await conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (ip_hash,))
        cur = await conn.execute(
            "select "
            "(select count(*) from public.password_reset_attempts "
            " where ip_hash = %s and created_at > now() - %s), "
            "exists (select 1 from public.password_reset_attempts "
            " where email_hash = %s and created_at > now() - %s)",
            (ip_hash, IP_WINDOW, email_hash, EMAIL_COOLDOWN),
        )
        ip_count, cooling_down = await cur.fetchone()
        if ip_count >= IP_LIMIT:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY)
        await conn.execute(
            "insert into public.password_reset_attempts (email_hash, ip_hash) values (%s, %s)",
            (email_hash, ip_hash),
        )
        await conn.execute(
            "delete from public.password_reset_attempts where created_at < now() - %s", (KEEP,)
        )
    if not cooling_down:
        background.add_task(send_password_reset, pool, admin, body.email)
    return PasswordResetAccepted(detail=ACCEPTED)
