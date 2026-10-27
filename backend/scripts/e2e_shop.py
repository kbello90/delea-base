"""A throwaway shop for the end-to-end run (e2e/). Local stack only.

    python backend/scripts/e2e_shop.py create --port 5175   # prints the shop as JSON
    python backend/scripts/e2e_shop.py delete <tenant id>
    python backend/scripts/e2e_shop.py card-paid <tenant id> <W- ref> --api http://127.0.0.1:8001

create provisions a demo tenant with an owner (a random password, printed once for this run),
a storefront domain e2e-<run>.localhost:<port>, checkout switched on (ships for $6, free over
$150, pickup, bank transfer and pay on pickup, payment instructions) and two products: a
necklace and a ring whose size is a required question. delete removes the tenant (it is a
demo tenant, so nothing else can be removed this way) and the owner's account.
Never the seed tenants A and B. The shop is provisioned as a demo tenant and then made an
ordinary one, so its owner can connect Stripe (the fake gateway; demo shops can't connect);
delete makes it a demo tenant again first.

card-paid is what Stripe would send when the shopper pays: a checkout.session.completed for
the order's open card session, signed with the end-to-end run's own made-up webhook secret
(E2E_STRIPE_WEBHOOK_SECRET), posted to the local API. Nothing reaches Stripe.
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import uuid
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from psycopg import AsyncConnection  # noqa: E402

from app.core.auth_admin import AuthAdmin  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.services.provisioning import (  # noqa: E402
    Member,
    TenantSpec,
    delete_demo_tenant,
    provision_tenant,
)
from showcase.config import is_local_url  # noqa: E402


def _refuse_remote(settings) -> None:
    if not (is_local_url(settings.database_url) and is_local_url(settings.supabase_url)):
        sys.exit("e2e_shop.py only runs against the local stack")


async def create(port: int) -> dict:
    settings = get_settings()
    _refuse_remote(settings)
    run = uuid.uuid4().hex[:8]
    email = f"e2e-owner-{run}@e2e.delea.test"
    password = secrets.token_urlsafe(18)
    admin = AuthAdmin(settings.supabase_url, settings.supabase_service_role_key)
    owner_id = await admin.ensure_user(email, password)
    domain = f"e2e-{run}.localhost:{port}"
    tenant_id = uuid.uuid4()
    async with await AsyncConnection.connect(settings.database_url, autocommit=True) as conn:
        await provision_tenant(
            conn,
            TenantSpec(
                tenant_id=tenant_id,
                name=f"E2E Shop {run}",
                business_name="E2E Jewelry",
                is_demo=True,
                default_tax_rate_bps=800,
                members=(Member(owner_id, "owner"),),
                domains=((domain, "storefront"),),
                storefront_enabled=True,
            ),
        )
        await conn.execute(
            "update public.storefront_settings set ship_enabled = true, ship_flat_cents = 600, "
            "free_ship_over_cents = 15000, pickup_enabled = true, "
            "pickup_note = 'Studio, Tuesday to Saturday, 11am to 6pm', "
            "bank_transfer_enabled = true, pay_on_pickup_enabled = true, "
            "manual_payment_instructions = 'Transfer the total to E2E Jewelry, account 000 "
            "(test). Use your order number as the reference.', contact_email = 'hello@e2e.test' "
            "where tenant_id = %s",
            (tenant_id,),
        )
        await conn.execute(
            "update public.brand_config set hero_eyebrow = 'Made to order', "
            "collection_title = 'The collection', "
            "hero_title = 'Handcrafted pieces in 14K gold and 925 silver' where tenant_id = %s",
            (tenant_id,),
        )
        cur = await conn.execute(
            "insert into public.products (tenant_id, sku, name, description, metal, price_cents, "
            "stock_qty, is_published, sort_order) values "
            "(%s, 'E2E-NECK', 'Custom Initial Necklace', 'A fine chain.', 'gold_14k', 9800, 10, "
            "true, 1), "
            "(%s, 'E2E-RING', 'Halo Ring', 'A halo of small stones.', 'gold_14k', 14500, 5, "
            "true, 2) returning id, sku",
            (tenant_id, tenant_id),
        )
        ids = {sku: str(pid) for pid, sku in await cur.fetchall()}
        await conn.execute(
            "insert into public.product_personalization_fields "
            "(tenant_id, product_id, position, label, kind, required, choices, max_length) values "
            "(%s, %s, 1, 'Ring size', 'choice', true, '{5,6,7,8}', null), "
            "(%s, %s, 2, 'Engraving', 'text', false, null, 10)",
            (tenant_id, ids["E2E-RING"], tenant_id, ids["E2E-RING"]),
        )
        await conn.execute("update public.tenants set is_demo = false where id = %s", (tenant_id,))
        # A fresh run on this machine: the checkout's per-address and per-email counters
        # (hashes only) from earlier runs would refuse its orders. Local stack only.
        await conn.execute("delete from public.public_rate_hits")
    return {
        "tenant_id": str(tenant_id),
        "origin": f"http://{domain}",
        "owner_email": email,
        "owner_password": password,
        "owner_id": owner_id,
        "ring_id": ids["E2E-RING"],
        "necklace_id": ids["E2E-NECK"],
    }


async def delete(tenant_id: str) -> None:
    settings = get_settings()
    _refuse_remote(settings)
    admin = AuthAdmin(settings.supabase_url, settings.supabase_service_role_key)
    async with await AsyncConnection.connect(settings.database_url, autocommit=True) as conn:
        cur = await conn.execute(
            "select user_id::text from public.tenant_members where tenant_id = %s", (tenant_id,)
        )
        users = [row[0] for row in await cur.fetchall()]
        await conn.execute("update public.tenants set is_demo = true where id = %s", (tenant_id,))
        await delete_demo_tenant(conn, uuid.UUID(tenant_id))
    for user_id in users:
        await admin.delete_user(user_id)


async def card_paid(tenant_id: str, order_ref: str, api: str) -> str:
    settings = get_settings()
    _refuse_remote(settings)
    secret = os.environ["E2E_STRIPE_WEBHOOK_SECRET"]
    async with await AsyncConnection.connect(settings.database_url, autocommit=True) as conn:
        cur = await conn.execute(
            "select p.id, p.external_ref, p.amount_cents, p.currency, a.external_account_id "
            "from public.payments p join public.invoices i on i.id = p.invoice_id "
            "join public.payment_accounts a on a.tenant_id = p.tenant_id "
            "where i.tenant_id = %s and i.order_ref = %s and p.provider = 'stripe' "
            "and p.status = 'pending' and p.external_ref is not null "
            "order by p.created_at desc limit 1",
            (tenant_id, order_ref),
        )
        row = await cur.fetchone()
    if row is None:
        sys.exit(f"no open card session for {order_ref}")
    payment_id, session_id, cents, currency, account = row
    body = json.dumps(
        {
            "id": f"evt_e2e_{uuid.uuid4().hex[:20]}",
            "object": "event",
            "type": "checkout.session.completed",
            "account": account,
            "livemode": False,
            "data": {
                "object": {
                    "id": session_id,
                    "object": "checkout.session",
                    "payment_status": "paid",
                    "amount_total": cents,
                    "currency": currency.lower(),
                    "payment_intent": f"pi_e2e_{uuid.uuid4().hex[:20]}",
                    "metadata": {"tenant_id": tenant_id, "payment_id": str(payment_id)},
                }
            },
        }
    ).encode()
    t = int(time.time())
    sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{api}/api/v1/stripe/webhook",
            content=body,
            headers={"Stripe-Signature": f"t={t},v1={sig}", "Content-Type": "application/json"},
        )
    response.raise_for_status()
    return session_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("create")
    make.add_argument("--port", type=int, default=5175)
    remove = sub.add_parser("delete")
    remove.add_argument("tenant_id")
    paid = sub.add_parser("card-paid")
    paid.add_argument("tenant_id")
    paid.add_argument("order_ref")
    paid.add_argument("--api", default="http://127.0.0.1:8001")
    args = parser.parse_args()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        if args.command == "create":
            print(json.dumps(runner.run(create(args.port))))
        elif args.command == "card-paid":
            print(runner.run(card_paid(args.tenant_id, args.order_ref, args.api)))
        else:
            runner.run(delete(args.tenant_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
