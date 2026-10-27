"""The shop's Stripe account (Slice B2, TRD §6.14). Owner only.

GET /settings/payments says whether card payments are on. POST .../onboarding-link makes the
shop's connected account if it has none (the four Standard-equivalent controller settings,
decision K1) and returns a Stripe-hosted onboarding link; Stripe sends the owner back to the
admin, whose next GET reads the account from Stripe and stores what it says. Writes go through
payments_writer (decision K4); reads through the owner's own session (RLS).
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.api.deps import CurrentClaims, DbConn, get_pool, require_role
from app.core.config import get_settings
from app.db.session import payments_session
from app.models.stripe import OnboardingLinkOut, PaymentAccountOut, PaymentSettingsOut
from app.services.stripe_service import StripeGateway, StripeUnavailable, gateway_dependency

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings/payments", tags=["settings"])

OwnerOnly = Depends(require_role("owner"))
Gateway = Annotated[StripeGateway | None, Depends(gateway_dependency)]
Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]

NOT_CONFIGURED = "Card payments aren't set up on this server yet."
DEMO_REFUSED = "A demo shop uses the showcase's Stripe test account and can't connect its own."


async def _shop(conn, tenant_id: str) -> dict[str, Any]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select t.is_demo, a.external_account_id as account_id, a.charges_enabled, "
            "a.details_submitted, a.connected_at, a.disconnected_at "
            "from public.tenants t left join public.payment_accounts a "
            "on a.tenant_id = t.id and a.provider = 'stripe' where t.id = %s",
            (tenant_id,),
        )
        row = await cur.fetchone()
    assert row is not None  # the caller's own tenant
    return row


def _out(row: dict[str, Any], gateway: StripeGateway | None) -> PaymentSettingsOut:
    account = PaymentAccountOut.model_validate(row) if row["account_id"] else None
    return PaymentSettingsOut(
        stripe_configured=gateway is not None,
        is_demo=row["is_demo"],
        account=account,
        card_payments=bool(
            gateway is not None
            and account is not None
            and account.charges_enabled
            and account.disconnected_at is None
        ),
    )


@router.get("", response_model=PaymentSettingsOut, dependencies=[OwnerOnly])
async def payment_settings(
    claims: CurrentClaims, conn: DbConn, pool: Pool, gateway: Gateway
) -> PaymentSettingsOut:
    """The shop's Stripe account. One that can't take charges yet is read from Stripe again
    (the owner may have just come back from onboarding); Stripe being unreachable only means
    the stored state is shown."""
    row = await _shop(conn, claims.tenant_id)
    if (
        gateway is not None
        and row["account_id"]
        and not row["charges_enabled"]
        and not (row["disconnected_at"])
    ):
        try:
            state = await gateway.retrieve_account(row["account_id"])
        except StripeUnavailable:
            return _out(row, gateway)
        async with payments_session(pool) as writer:
            await writer.execute(
                "select public.stripe_apply_account(null, null, %s, %s, %s, %s, false)",
                (state.id, state.livemode, state.charges_enabled, state.details_submitted),
            )
        row = {
            **row,
            "charges_enabled": state.charges_enabled,
            "details_submitted": state.details_submitted,
        }
    return _out(row, gateway)


@router.post("/stripe/onboarding-link", response_model=OnboardingLinkOut, dependencies=[OwnerOnly])
async def onboarding_link(
    claims: CurrentClaims, conn: DbConn, pool: Pool, gateway: Gateway
) -> OnboardingLinkOut:
    if gateway is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NOT_CONFIGURED)
    row = await _shop(conn, claims.tenant_id)
    if row["is_demo"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, DEMO_REFUSED)
    account_id = row["account_id"] if not row["disconnected_at"] else None
    try:
        if account_id is None:
            # One account per shop: a retry within Stripe's idempotency window returns the
            # same account. After a disconnect, a new key makes a new one.
            key = f"delea:account:{claims.tenant_id}:{row['account_id'] or 'first'}"
            created = await gateway.create_account(tenant_id=claims.tenant_id, idempotency_key=key)
            async with payments_session(pool) as writer:
                cur = await writer.execute(
                    "select public.stripe_link_account(%s, %s)", (claims.tenant_id, created)
                )
                (account_id,) = await cur.fetchone()
        admin = get_settings().admin_app_url.rstrip("/")
        url = await gateway.account_link(
            account_id,
            refresh_url=f"{admin}/settings?stripe=refresh",
            return_url=f"{admin}/settings?stripe=return",
        )
    except StripeUnavailable as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Stripe couldn't be reached. Please try again."
        ) from exc
    return OnboardingLinkOut(url=url)
