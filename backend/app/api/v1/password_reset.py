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
            cur = await conn.execute("select public.api_account_is_demo(%s)", (email,))
            (is_demo,) = await cur.fetchone()
        if is_demo is None or is_demo:  # no account, or a demo account
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
        # One check at a time per IP, so parallel requests can't slip past the limit
        # (public.api_password_reset_attempt: lock, count, cooldown, insert, clean-up).
        cur = await conn.execute(
            "select public.api_password_reset_attempt(%s, %s, %s, %s, %s, %s)",
            (email_hash, ip_hash, IP_LIMIT, IP_WINDOW, EMAIL_COOLDOWN, KEEP),
        )
        (outcome,) = await cur.fetchone()
    if outcome == "too_many":
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY)
    cooling_down = outcome == "cooling_down"
    if not cooling_down:
        background.add_task(send_password_reset, pool, admin, body.email)
    return PasswordResetAccepted(detail=ACCEPTED)
