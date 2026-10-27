"""A throwaway shop for the end-to-end run (e2e/). Local stack only.

    python backend/scripts/e2e_shop.py create --port 5175   # prints the shop as JSON
    python backend/scripts/e2e_shop.py delete <tenant id>

create provisions a demo tenant with an owner (a random password, printed once for this run),
a storefront domain e2e-<run>.localhost:<port>, checkout switched on (ships for $6, free over
$150, pickup, bank transfer and pay on pickup, payment instructions) and two products: a
necklace and a ring whose size is a required question. delete removes the tenant (it is a
demo tenant, so nothing else can be removed this way) and the owner's account.
Never the seed tenants A and B.
"""

import argparse
import asyncio
import json
import secrets
import sys
import uuid
from pathlib import Path

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
        await delete_demo_tenant(conn, uuid.UUID(tenant_id))
    for user_id in users:
        await admin.delete_user(user_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("create")
    make.add_argument("--port", type=int, default=5175)
    remove = sub.add_parser("delete")
    remove.add_argument("tenant_id")
    args = parser.parse_args()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        if args.command == "create":
            print(json.dumps(runner.run(create(args.port))))
        else:
            runner.run(delete(args.tenant_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
