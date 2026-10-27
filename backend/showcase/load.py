"""Load (or reload) the showcase tenant: products with their photos, and a generated history.

Runs on an administrative database connection (the loader's own, never the API's runtime
role) plus the Storage and Auth admin APIs with the service-role key. Steps:

1. Read and validate showcase/products.yaml, check every listed photo exists, and process
   every photo with process_product_image (the API's own service) before touching anything.
2. Create or update the member users (confirmed; no email is sent). Refuse a user who
   belongs to another tenant (the access token hook would pick that membership instead).
3. Remove the previous showcase: its Storage objects, then its rows (delete_demo_tenant,
   which refuses any tenant that isn't a demo tenant).
4. In ONE transaction: provision the tenant (is_demo), insert products and upload their
   variants, materials, customers, then each invoice through the same path the API uses
   (insert as quote, lines, status changes: the triggers number them, decrement stock and
   emit events, which are stamped with their backdated time and marked processed at once),
   refunds, cancellations, expenses. Then the one backdating step: under
   session_replication_role = replica, only this tenant's invoices get their historical
   timestamps and document numbers. Counters are set, and the integrity checks run; any
   failure rolls everything back (and removes the uploaded objects).
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from psycopg import AsyncConnection

from app.core import storage
from app.core.auth_admin import AuthAdmin
from app.core.config import Settings
from app.services.product_images import ImageRejected, ProcessedImage, process_product_image
from app.services.provisioning import (
    Member,
    ProvisioningError,
    TenantSpec,
    delete_demo_tenant,
    provision_tenant,
    refuse_unless_demo,
    remove_tenant_storage,
)
from showcase import integrity
from showcase.catalog import CatalogError, ProductEntry, check_photos, load_catalog
from showcase.history import SEED, TAX_RATE_BPS, History, SaleProduct, generate, stable_id

SHOWCASE_TENANT_ID = uuid.UUID("5ca1ab1e-0000-4000-8000-de1ea5000001")
SHOWCASE_STOREFRONT_DOMAIN = "showcase.localhost:5174"


@dataclass(frozen=True)
class ShowcaseConfig:
    database_url: str  # the loader's administrative connection
    settings: Settings  # Supabase URL and service-role key (Storage and Auth admin)
    catalog_path: Path
    photos_dir: Path
    members: tuple[tuple[str, str], ...]  # (email, app_role)
    password: str
    now: datetime
    tenant_id: uuid.UUID = SHOWCASE_TENANT_ID
    tenant_name: str = "DELEA Showcase"
    business_name: str = "DELEA Jewelry"
    seed: int = SEED
    # The local storefront host (`pnpm --filter @delea/storefront dev`); None on a hosted
    # target, whose storefront domain is added by hand (infra/scripts/storefront-domain.md).
    storefront_domain: str | None = SHOWCASE_STOREFRONT_DOMAIN


@dataclass(frozen=True)
class LoadReport:
    tenant_id: uuid.UUID
    products: int
    images: int
    materials: int
    customers: int
    invoices: int
    expenses: int
    removed_objects: int


def prepare(
    config: ShowcaseConfig,
) -> tuple[list[ProductEntry], dict[tuple[str, str], ProcessedImage]]:
    """Validate the catalog and process every photo. Nothing is written."""
    entries = load_catalog(config.catalog_path)
    check_photos(entries, config.photos_dir)
    processed: dict[tuple[str, str], ProcessedImage] = {}
    problems = []
    for entry in entries:
        for name in entry.images:
            data = (config.photos_dir / entry.folder / name).read_bytes()
            try:
                processed[(entry.sku, name)] = process_product_image(data)
            except ImageRejected as exc:
                problems.append(f"{entry.sku}: {name}: {exc}")
    if problems:
        raise CatalogError(problems)
    return entries, processed


def _sale_products(tenant_id: uuid.UUID, entries: list[ProductEntry]) -> list[SaleProduct]:
    return [
        SaleProduct(
            product_id=stable_id(tenant_id, "product", e.sku),
            sku=e.sku,
            name=e.name,
            price_cents=e.price_cents,
            sale_price_cents=e.sale_price_cents,
            published=e.published,
        )
        for e in entries
    ]


async def _ensure_members(config: ShowcaseConfig, conn: AsyncConnection) -> list[Member]:
    admin = AuthAdmin(config.settings.supabase_url, config.settings.supabase_service_role_key)
    members = []
    for email, role in config.members:
        user_id = await admin.ensure_user(email, config.password)
        cur = await conn.execute(
            "select tenant_id from public.tenant_members where user_id = %s and tenant_id <> %s",
            (user_id, config.tenant_id),
        )
        if await cur.fetchone():
            raise ProvisioningError(
                f"{email} already belongs to another tenant; use a different showcase email"
            )
        members.append(Member(user_id, role))
    return members


async def _stamp_events(conn: AsyncConnection, tenant_id: uuid.UUID, at: datetime) -> None:
    """Date the events the last write emitted and mark them processed: nothing is pending
    for a worker, ever, in a demo tenant."""
    await conn.execute(
        "update public.automation_events set created_at = %s, processed_at = %s "
        "where tenant_id = %s and processed_at is null",
        (at, at, tenant_id),
    )


async def _insert_catalog(
    conn: AsyncConnection,
    config: ShowcaseConfig,
    entries: list[ProductEntry],
    processed: dict[tuple[str, str], ProcessedImage],
    history: History,
    uploaded: list[str],
) -> int:
    tid = config.tenant_id
    images = 0
    for index, entry in enumerate(entries):
        product_id = stable_id(tid, "product", entry.sku)
        created = history.tenant_created_at + timedelta(days=1, hours=index)
        await conn.execute(
            "insert into public.products (id, tenant_id, sku, name, description, metal, "
            "price_cents, sale_price_cents, stock_qty, low_stock_threshold, is_published, "
            "sort_order, created_at, updated_at) "
            "values (%s, %s, %s, %s, %s, %s::public.metal_type, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                product_id,
                tid,
                entry.sku,
                entry.name,
                entry.description,
                entry.metal,
                entry.price_cents,
                entry.sale_price_cents,
                entry.stock + history.units_sold.get(product_id, 0),
                entry.low_stock_threshold,
                entry.published,
                index,
                created,
                created,
            ),
        )
        for position, name in enumerate(entry.images):
            image = processed[(entry.sku, name)]
            image_id = stable_id(tid, "image", f"{entry.sku}/{name}")
            large = f"{tid}/{product_id}/{image_id}-1200.webp"
            thumb = f"{tid}/{product_id}/{image_id}-400.webp"
            for path, data in ((large, image.large), (thumb, image.thumb)):
                await storage.upload(storage.IMAGES_BUCKET, path, data, "image/webp")
                uploaded.append(path)
            await conn.execute(
                "insert into public.product_images (id, tenant_id, product_id, position, "
                "is_primary, large_path, thumb_path, width, height, created_at) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    image_id,
                    tid,
                    product_id,
                    position,
                    position == 0,
                    large,
                    thumb,
                    image.width,
                    image.height,
                    created,
                ),
            )
            images += 1
    for m in history.materials:
        await conn.execute(
            "insert into public.materials (id, tenant_id, name, unit, unit_cost_cents, "
            "qty_on_hand, reorder_threshold, created_at, updated_at) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                m.id,
                tid,
                m.name,
                m.unit,
                m.unit_cost_cents,
                m.qty_on_hand,
                m.reorder_threshold,
                m.created_at,
                m.created_at,
            ),
        )
    for c in history.customers:
        await conn.execute(
            "insert into public.customers (id, tenant_id, customer_type, first_name, last_name, "
            "email, phone, address_line1, city, state, postal_code, country, marketing_opt_in, "
            "company_name, tax_exempt, resale_certificate_number, wholesale_discount_bps, "
            "payment_terms, created_at, updated_at) values (%s, %s, %s::public.customer_type, "
            "%s, %s, %s, %s, %s, %s, %s, %s, 'US', false, %s, %s, %s, %s, "
            "%s::public.payment_terms, %s, %s)",
            (
                c.id,
                tid,
                c.customer_type,
                c.first_name,
                c.last_name,
                c.email,
                c.phone,
                c.address_line1,
                c.city,
                c.state,
                c.postal_code,
                c.company_name,
                c.tax_exempt,
                c.resale_certificate_number,
                c.wholesale_discount_bps,
                c.payment_terms,
                c.created_at,
                c.created_at,
            ),
        )
    return images


async def _insert_sales(conn: AsyncConnection, tid: uuid.UUID, history: History) -> None:
    for inv in history.invoices:
        q = inv.quote
        await conn.execute(
            "insert into public.invoices (id, tenant_id, customer_id, customer_name, source, "
            "subtotal_cents, tax_rate_bps, tax_cents, total_cents, due_date, created_at, "
            "updated_at) values (%s, %s, %s, %s, 'admin', %s, %s, %s, %s, %s, %s, %s)",
            (
                inv.id,
                tid,
                inv.customer.id if inv.customer else None,
                inv.customer_name,
                q.subtotal_cents,
                q.tax_rate_bps,
                q.tax_cents,
                q.total_cents,
                inv.due_date,
                inv.created_at,
                inv.created_at,
            ),
        )
        for n, line in enumerate(q.lines):
            await conn.execute(
                "insert into public.invoice_line_items (id, invoice_id, tenant_id, product_id, "
                "description, qty, unit_price_cents, line_total_cents, position) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    stable_id(tid, "line", f"{inv.id}/{n}"),
                    inv.id,
                    tid,
                    line.product_id,
                    line.description,
                    line.qty,
                    line.unit_price_cents,
                    line.line_total_cents,
                    n + 1,
                ),
            )
        await _stamp_events(conn, tid, inv.created_at)
        if inv.steps:
            # Paid in full when it left quote (an invoice can't leave quote otherwise).
            await conn.execute(
                "insert into public.payments (id, tenant_id, invoice_id, kind, method, "
                "amount_cents, currency, received_at, created_at) "
                "values (%s, %s, %s, 'payment', %s::public.payment_method, %s, 'USD', %s, %s)",
                (
                    stable_id(tid, "payment", str(inv.id)),
                    tid,
                    inv.id,
                    inv.payment_method,
                    q.total_cents,
                    inv.steps[0][1],
                    inv.steps[0][1],
                ),
            )
        for status, at in inv.steps:
            # The first step is the payment, which records how it was paid.
            await conn.execute(
                "update public.invoices set order_status = %s::public.order_status, "
                "payment_method = coalesce(payment_method, %s::public.payment_method) "
                "where id = %s and tenant_id = %s",
                (status, inv.payment_method, inv.id, tid),
            )
            await _stamp_events(conn, tid, at)
        if inv.refunded_at is not None:  # refunded in full, after it was paid
            await conn.execute(
                "insert into public.payments (id, tenant_id, invoice_id, kind, method, "
                "amount_cents, currency, received_at, created_at, refund_of) "
                "values (%s, %s, %s, 'refund', %s::public.payment_method, %s, 'USD', %s, %s, %s)",
                (
                    stable_id(tid, "refund", str(inv.id)),
                    tid,
                    inv.id,
                    inv.payment_method,
                    q.total_cents,
                    inv.refunded_at,
                    inv.refunded_at,
                    stable_id(tid, "payment", str(inv.id)),
                ),
            )
        for column, at in (("refunded_at", inv.refunded_at), ("cancelled_at", inv.cancelled_at)):
            if at is not None:
                await conn.execute(
                    f"update public.invoices set {column} = now() where id = %s and tenant_id = %s",
                    (inv.id, tid),
                )
                await _stamp_events(conn, tid, at)
    for e in history.expenses:
        await conn.execute(
            "insert into public.expenses (id, tenant_id, expense_date, description, category, "
            "amount_cents, is_tax_deductible, created_at, updated_at) "
            "values (%s, %s, %s, %s, %s::public.expense_category, %s, %s, %s, %s)",
            (
                e.id,
                tid,
                e.expense_date,
                e.description,
                e.category,
                e.amount_cents,
                e.is_tax_deductible,
                e.expense_date,
                e.expense_date,
            ),
        )


async def _backdate(conn: AsyncConnection, tid: uuid.UUID, history: History) -> None:
    """The one step that bypasses triggers (session_replication_role = replica, which also
    skips foreign-key checks): this tenant's invoices get their historical timestamps and
    document numbers in date order. Every statement is scoped to the tenant; the integrity
    checks re-validate everything afterwards."""
    values = []
    for inv in history.invoices:
        stamps = [inv.created_at, *(at for _, at in inv.steps), inv.refunded_at, inv.cancelled_at]
        updated = max(t for t in stamps if t is not None)
        values.append(
            (inv.id, inv.created_at, inv.paid_at, inv.refunded_at, inv.cancelled_at, updated)
        )
    await conn.execute("set local session_replication_role = replica")
    try:
        async with conn.cursor() as cur:
            await cur.executemany(
                "update public.invoices set created_at = %s, paid_at = %s, refunded_at = %s, "
                "cancelled_at = %s, updated_at = %s where id = %s and tenant_id = %s",
                [(c, p, r, x, u, i, tid) for i, c, p, r, x, u in values],
            )
        # Renumber in date order. Temporary numbers first: the unique constraints are not
        # deferrable, and two invoices may swap places.
        await conn.execute(
            "update public.invoices set quote_number = 'TMP-Q-' || id, "
            "invoice_number = case when invoice_number is null then null else 'TMP-I-' || id end "
            "where tenant_id = %s",
            (tid,),
        )
        await conn.execute(
            """
            with q as (
              select id, created_at, row_number() over (order by created_at, id) as n
                from public.invoices where tenant_id = %(t)s
            )
            update public.invoices i
               set quote_number = 'QTE-' || to_char(q.created_at at time zone 'UTC', 'YYYY')
                                  || '-' || lpad(q.n::text, greatest(3, length(q.n::text)), '0')
              from q where i.id = q.id and i.tenant_id = %(t)s
            """,
            {"t": tid},
        )
        await conn.execute(
            """
            with q as (
              select id, paid_at, row_number() over (order by paid_at, id) as n
                from public.invoices where tenant_id = %(t)s and paid_at is not null
            )
            update public.invoices i
               set invoice_number = 'INV-' || to_char(q.paid_at at time zone 'UTC', 'YYYY')
                                    || '-' || lpad(q.n::text, greatest(3, length(q.n::text)), '0')
              from q where i.id = q.id and i.tenant_id = %(t)s
            """,
            {"t": tid},
        )
    finally:
        await conn.execute("set local session_replication_role = origin")
    await conn.execute(
        "update public.company_settings set "
        "next_quote_number = (select count(*) + 1 from public.invoices where tenant_id = %(t)s), "
        "next_invoice_number = (select count(*) + 1 from public.invoices "
        "                        where tenant_id = %(t)s and paid_at is not null) "
        "where tenant_id = %(t)s",
        {"t": tid},
    )


async def load_showcase(config: ShowcaseConfig, expected_hook=None) -> LoadReport:
    """Remove and rebuild the showcase tenant. `expected_hook` lets tests alter what the
    integrity checks expect (to prove they catch a difference)."""
    entries, processed = prepare(config)
    tid = config.tenant_id
    history = generate(tid, _sale_products(tid, entries), config.now, config.seed)
    expected = integrity.Expected(
        stock_by_sku={e.sku: e.stock for e in entries},
        images_by_sku={e.sku: len(e.images) for e in entries},
    )
    if expected_hook:
        expected = expected_hook(expected)

    async with await AsyncConnection.connect(config.database_url, autocommit=True) as conn:
        await refuse_unless_demo(conn, tid)  # before anything else is touched
        members = await _ensure_members(config, conn)
        removed = await remove_tenant_storage(conn, tid)
        await delete_demo_tenant(conn, tid)

        uploaded: list[str] = []
        try:
            async with conn.transaction():
                await provision_tenant(
                    conn,
                    TenantSpec(
                        tenant_id=tid,
                        name=config.tenant_name,
                        business_name=config.business_name,
                        is_demo=True,
                        default_tax_rate_bps=TAX_RATE_BPS,
                        members=tuple(members),
                        created_at=history.tenant_created_at,
                        domains=(
                            ((config.storefront_domain, "storefront"),)
                            if config.storefront_domain
                            else ()
                        ),
                        storefront_enabled=True,
                    ),
                )
                images = await _insert_catalog(conn, config, entries, processed, history, uploaded)
                await _insert_sales(conn, tid, history)
                await _backdate(conn, tid, history)
                await integrity.run_all(conn, tid, expected)
        except BaseException:
            if uploaded:
                await asyncio.shield(storage.remove(storage.IMAGES_BUCKET, uploaded))
            raise

    return LoadReport(
        tenant_id=tid,
        products=len(entries),
        images=images,
        materials=len(history.materials),
        customers=len(history.customers),
        invoices=len(history.invoices),
        expenses=len(history.expenses),
        removed_objects=removed,
    )
