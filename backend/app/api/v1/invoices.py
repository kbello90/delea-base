"""Invoice routes, including order_status transitions (TRD §3.1).

Roles (RLS: invoices owner all, staff select/insert/update, cpa select; line items owner and
staff, writes only while the invoice is a quote):
- read, list, CSV export: owner, staff, cpa (cpa can't read line items: `items` is null)
- create, preview, replace items, change status, edit notes / due date: owner, staff
- tax-rate override, refund, cancel, delete, correcting a payment method: owner only (API
  rules; delete is quotes-only and a set payment method is owner-only in the database too)

A cancelled or refunded invoice keeps its status, a cancelled one can't be refunded and a
refunded one can't be cancelled: checked here for a clear message, and enforced by the
database (invoices_08_closed_guard).

Payments (Slice B1, TRD §6.13): every payment is a row in public.payments. An invoice leaves
quote only when its payments cover its total (invoices_09_paid_in_full_guard), so "pay now"
and a status move out of quote first record a payment for the balance. A smaller payment is a
deposit: the invoice stays a quote, and becomes paid (or in production) when the balance
reaches zero (public.invoice_settle).

Money is never accepted from the client: prices come from the products, totals from
app/services/pricing.py, the tax rate from public.current_tax_rate_bps() (or the owner's
override, stored as the snapshot). Every query also filters on the caller's tenant.
"""

import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.api.deps import CurrentClaims, DbConn, get_pool, require_role
from app.core.csv_export import cents_to_decimal, csv_stream
from app.core.errors import ConflictError, NotFoundError
from app.core.security import TokenClaims
from app.db.session import user_session
from app.db.sql_helpers import like_pattern, patch_changes, update_statement
from app.models.invoice import (
    INVOICE_NULLABLE_FIELDS,
    InvoiceCountsOut,
    InvoiceCreate,
    InvoiceItemsReplace,
    InvoiceLineOut,
    InvoiceOut,
    InvoicePreview,
    InvoicePreviewOut,
    InvoiceStatusChange,
    InvoiceUpdate,
    LineItemIn,
    OrderContactOut,
    OrderStatus,
    PaymentCreate,
    PaymentMethod,
    PaymentOut,
    PreviewLineOut,
    StockShortage,
)
from app.services.card_checkout import CardSessionOpen, close_open_card_sessions
from app.services.currency import fetch_currency
from app.services.invoices import customer_display_name, due_date_for
from app.services.pricing import (
    CustomerPricing,
    LineRequest,
    PricedQuote,
    PricingError,
    ProductPrice,
    price_quote,
)
from app.services.stripe_service import StripeGateway, gateway_dependency

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/invoices", tags=["invoices"])

Gateway = Annotated[StripeGateway | None, Depends(gateway_dependency)]
Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]

CARD_SESSION_OPEN = (
    "A card payment is open for this order and couldn't be closed. Try again in a minute."
)


async def _close_card_sessions(
    pool: AsyncConnectionPool, gateway: StripeGateway | None, claims: TokenClaims, invoice_id: UUID
) -> None:
    """Before money is recorded by hand: the order's open card Checkout Sessions are expired
    at Stripe, so the customer can't also pay by card (Slice B2)."""
    try:
        await close_open_card_sessions(pool, gateway, claims.tenant_id, invoice_id)
    except CardSessionOpen as exc:
        raise ConflictError(CARD_SESSION_OPEN) from exc


ReadRoles = Depends(require_role("owner", "staff", "cpa"))
Writers = Depends(require_role("owner", "staff"))
OwnerOnly = Depends(require_role("owner"))

_COLUMNS = (
    "id, tenant_id, quote_number, invoice_number, order_status::text as order_status, "
    "customer_id, customer_name, source, subtotal_cents, tax_rate_bps, tax_cents, total_cents, "
    "due_date, notes, payment_method::text as payment_method, paid_at, refunded_at, "
    "cancelled_at, created_at, updated_at, shipping_cents, fulfillment, payment_choice, order_ref, "
    "production_started_at, public.invoice_amount_paid(id) as amount_paid_cents, "
    "total_cents - public.invoice_amount_paid(id) as balance_cents, "
    "(order_status = 'quote' and cancelled_at is null and refunded_at is null and total_cents > 0 "
    "and public.invoice_amount_paid(id) >= total_cents) as stock_short, "
    "(select count(distinct p.method) > 1 from public.payments p where p.invoice_id = invoices.id "
    "and p.kind = 'payment' and p.status = 'succeeded') as mixed_methods"
)
_LINE_COLUMNS = (
    "id, product_id, description, qty, unit_price_cents, line_total_cents, personalization"
)
_PAYMENT_COLUMNS = (
    "id, kind, provider, method::text as method, status, amount_cents, currency, received_at, "
    "note, refund_of, voided_at"
)
_CONTACT_COLUMNS = (
    "first_name, last_name, email, phone, ship_line1, ship_line2, ship_city, ship_state, "
    "ship_postal_code, ship_country::text as ship_country, note"
)


# ---------------------------------------------------------------------------
# Loading and pricing helpers (all inside the caller's user session)
# ---------------------------------------------------------------------------
async def _load(conn: AsyncConnection, claims: TokenClaims, invoice_id: UUID) -> InvoiceOut:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select {_COLUMNS} from public.invoices where id = %s and tenant_id = %s",
            (invoice_id, claims.tenant_id),
        )
        row = await cur.fetchone()
        if row is None:
            raise NotFoundError("Invoice not found")
        invoice = InvoiceOut.model_validate(row)
        await cur.execute(
            f"select {_PAYMENT_COLUMNS} from public.payments "
            "where invoice_id = %s and tenant_id = %s order by received_at, created_at, id",
            (invoice_id, claims.tenant_id),
        )
        invoice.payments = [PaymentOut.model_validate(r) for r in await cur.fetchall()]
        if claims.app_role != "cpa":  # cpa can't read line items (RLS)
            await cur.execute(
                f"select {_LINE_COLUMNS} from public.invoice_line_items "
                "where invoice_id = %s and tenant_id = %s order by position",
                (invoice_id, claims.tenant_id),
            )
            invoice.items = [InvoiceLineOut.model_validate(r) for r in await cur.fetchall()]
            if invoice.source == "storefront":
                await cur.execute(
                    f"select {_CONTACT_COLUMNS} from public.order_contacts "
                    "where invoice_id = %s and tenant_id = %s",
                    (invoice_id, claims.tenant_id),
                )
                contact = await cur.fetchone()
                invoice.contact = OrderContactOut.model_validate(contact) if contact else None
    return invoice


async def _customer(
    conn: AsyncConnection, claims: TokenClaims, customer_id: UUID | None
) -> dict[str, Any] | None:
    if customer_id is None:
        return None
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, customer_type::text as customer_type, first_name, last_name, "
            "company_name, tax_exempt, wholesale_discount_bps, "
            "payment_terms::text as payment_terms "
            "from public.customers where id = %s and tenant_id = %s",
            (customer_id, claims.tenant_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Customer not found")
    return row


def _customer_pricing(customer: dict[str, Any] | None) -> CustomerPricing:
    if customer is None:
        return CustomerPricing()
    return CustomerPricing(
        is_wholesale=customer["customer_type"] == "wholesale",
        wholesale_discount_bps=customer["wholesale_discount_bps"],
        tax_exempt=customer["tax_exempt"],
    )


async def _price(
    conn: AsyncConnection,
    claims: TokenClaims,
    customer: dict[str, Any] | None,
    items: Sequence[LineItemIn],
    default_rate_bps: int,
    override_bps: int | None,
) -> tuple[PricedQuote, date]:
    """Price the lines from the caller's own products (404 for any other) at the database's
    current time. Returns the quote and today's date (UTC) for due-date defaults."""
    ids = list(dict.fromkeys(item.product_id for item in items))
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select now() as now, (now() at time zone 'UTC')::date as today",
        )
        clock = await cur.fetchone()
        await cur.execute(
            "select id, name, price_cents, sale_price_cents, sale_ends_at "
            "from public.products where tenant_id = %s and id = any(%s)",
            (claims.tenant_id, ids),
        )
        products = {row["id"]: row for row in await cur.fetchall()}
    for product_id in ids:
        if product_id not in products:
            raise NotFoundError(f"Product {product_id} not found")
    lines = [
        LineRequest(
            ProductPrice(
                product_id=item.product_id,
                name=products[item.product_id]["name"],
                price_cents=products[item.product_id]["price_cents"],
                sale_price_cents=products[item.product_id]["sale_price_cents"],
                sale_ends_at=products[item.product_id]["sale_ends_at"],
            ),
            item.qty,
        )
        for item in items
    ]
    try:
        quote = price_quote(
            lines,
            _customer_pricing(customer),
            default_tax_rate_bps=default_rate_bps,
            now=clock["now"],
            tax_rate_override_bps=override_bps,
        )
    except PricingError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return quote, clock["today"]


async def _insert_lines(
    conn: AsyncConnection, claims: TokenClaims, invoice_id: UUID, quote: PricedQuote
) -> None:
    async with conn.cursor() as cur:
        await cur.executemany(
            "insert into public.invoice_line_items "
            "(invoice_id, tenant_id, product_id, description, qty, unit_price_cents, "
            "line_total_cents, position) values (%s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    invoice_id,
                    claims.tenant_id,
                    line.product_id,
                    line.description,
                    line.qty,
                    line.unit_price_cents,
                    line.line_total_cents,
                    position,
                )
                for position, line in enumerate(quote.lines, start=1)
            ],
        )


async def _stock_shortages(
    conn: AsyncConnection, claims: TokenClaims, items: Sequence[LineItemIn]
) -> list[StockShortage]:
    """Products the lines need more of than is in stock right now (quantities summed per
    product, as the database checks when the invoice is paid). Made-to-order products are
    never short: the database neither checks nor decrements them."""
    needed: dict[UUID, int] = {}
    for item in items:
        needed[item.product_id] = needed.get(item.product_id, 0) + item.qty
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, sku, stock_qty from public.products "
            "where tenant_id = %s and id = any(%s) and not made_to_order order by sku",
            (claims.tenant_id, list(needed)),
        )
        rows = await cur.fetchall()
    return [
        StockShortage(
            product_id=row["id"],
            sku=row["sku"],
            available=row["stock_qty"],
            needed=needed[row["id"]],
        )
        for row in rows
        if row["stock_qty"] < needed[row["id"]]
    ]


async def _preview(
    conn: AsyncConnection,
    claims: TokenClaims,
    customer: dict[str, Any] | None,
    items: Sequence[LineItemIn],
    default_rate_bps: int,
    override_bps: int | None,
) -> InvoicePreviewOut:
    quote, _ = await _price(conn, claims, customer, items, default_rate_bps, override_bps)
    return InvoicePreviewOut(
        lines=[
            PreviewLineOut(
                product_id=line.product_id,
                description=line.description,
                qty=line.qty,
                unit_price_cents=line.unit_price_cents,
                line_total_cents=line.line_total_cents,
            )
            for line in quote.lines
        ],
        subtotal_cents=quote.subtotal_cents,
        tax_rate_bps=quote.tax_rate_bps,
        tax_cents=quote.tax_cents,
        total_cents=quote.total_cents,
        stock_shortages=await _stock_shortages(conn, claims, items),
    )


async def _default_rate(conn: AsyncConnection, override_bps: int | None) -> int:
    """The tenant's tax rate, read only when there is no owner override."""
    if override_bps is not None:
        return 0
    cur = await conn.execute("select public.current_tax_rate_bps()")
    (rate,) = await cur.fetchone()
    return rate


def _check_payment_method(*, paying: bool, payment_method: PaymentMethod | None) -> None:
    """Leaving quote is the payment: it needs a method, and nothing else may send one."""
    if paying and payment_method is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "a payment method is required to take payment"
        )
    if not paying and payment_method is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "the payment method is recorded only when a quote is paid",
        )


async def _take_balance(
    conn: AsyncConnection, claims: TokenClaims, invoice_id: UUID, method: PaymentMethod | None
) -> None:
    """Record a payment for whatever is still due, so the move out of quote that follows is
    paid in full. Nothing is recorded when nothing is due (a zero total, or a quote already
    paid by its deposits). Called in the same transaction as the move: if the move fails
    (not enough stock), the payment goes with it."""
    cur = await conn.execute(
        "select public.invoice_balance(id) from public.invoices where id = %s and tenant_id = %s",
        (invoice_id, claims.tenant_id),
    )
    (balance,) = await cur.fetchone()
    if balance > 0 and method is not None:
        await _insert_payment(conn, claims, invoice_id, "payment", balance, method, None)


async def _insert_payment(
    conn: AsyncConnection,
    claims: TokenClaims,
    invoice_id: UUID,
    kind: str,
    amount_cents: int,
    method: PaymentMethod,
    note: str | None,
) -> None:
    """The database sets the currency, the time and who recorded it, and refuses an amount
    beyond what is due (payments) or was received (refunds)."""
    await conn.execute(
        "insert into public.payments (tenant_id, invoice_id, kind, method, amount_cents, "
        "currency, note) values (%s, %s, %s, %s::public.payment_method, %s, "
        "public.current_tenant_currency(), %s)",
        (claims.tenant_id, invoice_id, kind, method, amount_cents, note),
    )


def _refuse_override_unless_owner(claims: TokenClaims, override_bps: int | None) -> None:
    if override_bps is not None and claims.app_role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only the owner can override the tax rate")


# ---------------------------------------------------------------------------
# List and CSV export filters
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class InvoiceFilters:
    status: OrderStatus | None
    customer_id: UUID | None
    date_from: date | None
    date_to: date | None
    q: str | None
    source: Literal["admin", "storefront"] | None
    awaiting_payment: bool | None


def invoice_filters(
    status_: Annotated[OrderStatus | None, Query(alias="status")] = None,
    customer_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
    source: Literal["admin", "storefront"] | None = None,
    awaiting_payment: Annotated[
        bool | None,
        Query(description="true: storefront orders still waiting for payment (not cancelled)"),
    ] = None,
) -> InvoiceFilters:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "date_from is after date_to")
    return InvoiceFilters(status_, customer_id, date_from, date_to, q, source, awaiting_payment)


Filters = Annotated[InvoiceFilters, Depends(invoice_filters)]

# Dates are UTC calendar dates of created_at, inclusive. q matches either document number or
# a storefront order's reference.
_WHERE = """
     where tenant_id = %(tenant_id)s
       and (%(status)s::text is null or order_status::text = %(status)s)
       and (%(customer_id)s::uuid is null or customer_id = %(customer_id)s)
       and (%(date_from)s::date is null or (created_at at time zone 'UTC')::date >= %(date_from)s)
       and (%(date_to)s::date is null or (created_at at time zone 'UTC')::date <= %(date_to)s)
       and (%(pattern)s::text is null
            or quote_number ilike %(pattern)s or invoice_number ilike %(pattern)s
            or order_ref ilike %(pattern)s)
       and (%(source)s::text is null or source = %(source)s)
       and (%(awaiting)s::boolean is not true
            or (source = 'storefront' and order_status = 'quote' and cancelled_at is null
                and public.invoice_amount_paid(id) = 0))
"""


def _params(claims: TokenClaims, f: InvoiceFilters) -> dict[str, Any]:
    return {
        "tenant_id": claims.tenant_id,
        "status": f.status.value if f.status else None,
        "customer_id": f.customer_id,
        "date_from": f.date_from,
        "date_to": f.date_to,
        "pattern": like_pattern(f.q) if f.q else None,
        "source": f.source,
        "awaiting": f.awaiting_payment,
    }


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
@router.get("", response_model=list[InvoiceOut], dependencies=[ReadRoles])
async def list_invoices(
    claims: CurrentClaims,
    conn: DbConn,
    filters: Filters,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[InvoiceOut]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select {_COLUMNS} from public.invoices {_WHERE} "
            "order by created_at desc, id limit %(limit)s offset %(offset)s",
            {**_params(claims, filters), "limit": limit, "offset": offset},
        )
        return [InvoiceOut.model_validate(row) for row in await cur.fetchall()]


CSV_HEADER = (
    "quote_number",
    "invoice_number",
    "order_status",
    "customer_name",
    "created_at",
    "paid_at",
    "payment_method",
    "due_date",
    "currency",
    "subtotal",
    "tax_rate_bps",
    "tax",
    "total",
    "refunded_at",
    "cancelled_at",
    "source",
    "shipping",  # last, so the columns before it keep their positions (Slice A2)
)


# Declared before /{invoice_id} so "export.csv" is never parsed as an id.
@router.get("/export.csv", dependencies=[ReadRoles])
async def export_invoices_csv(
    claims: CurrentClaims,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    filters: Filters,
) -> StreamingResponse:
    """Stream the filtered invoices as CSV (no line items, no paging). Amounts are exact
    decimal strings. The rows are read in their own user session that stays open while the
    response streams; an error after streaming has started truncates the file and is logged.
    """

    # Read before streaming starts, so a refusal is a proper 403 rather than a cut-off file.
    async with user_session(pool, claims) as conn:
        currency = await fetch_currency(conn)

    async def rows() -> AsyncIterator[tuple]:
        try:
            async with user_session(pool, claims) as conn:
                cur = conn.cursor()
                async for r in cur.stream(
                    "select quote_number, invoice_number, order_status::text, customer_name, "
                    "created_at, paid_at, case when (select count(distinct p.method) from "
                    "public.payments p where p.invoice_id = invoices.id and p.kind = 'payment' "
                    "and p.status = 'succeeded') > 1 then 'mixed' else payment_method::text end, "
                    "due_date, subtotal_cents, "
                    "tax_rate_bps, tax_cents, total_cents, refunded_at, cancelled_at, source, "
                    f"shipping_cents from public.invoices {_WHERE} order by created_at, id",
                    _params(claims, filters),
                ):
                    yield (
                        *r[:8],
                        currency,
                        cents_to_decimal(r[8]),
                        r[9],
                        cents_to_decimal(r[10]),
                        cents_to_decimal(r[11]),
                        *r[12:15],
                        cents_to_decimal(r[15]),
                    )
        except Exception:
            logger.exception("Invoice CSV export failed mid-stream")
            raise

    return StreamingResponse(
        csv_stream(CSV_HEADER, rows()),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="invoices.csv"'},
    )


# Declared before /{invoice_id}. Owner and staff: they confirm payments; the CPA doesn't.
@router.get("/counts", response_model=InvoiceCountsOut, dependencies=[Writers])
async def invoice_counts(claims: CurrentClaims, conn: DbConn) -> InvoiceCountsOut:
    """What the Invoices nav badge shows: storefront orders with nothing paid yet. An order
    holding a deposit has its own tag and balance instead."""
    cur = await conn.execute(
        "select count(*) from public.invoices where tenant_id = %s and source = 'storefront' "
        "and order_status = 'quote' and cancelled_at is null "
        "and public.invoice_amount_paid(id) = 0",
        (claims.tenant_id,),
    )
    (awaiting,) = await cur.fetchone()
    return InvoiceCountsOut(awaiting_payment_web=awaiting)


@router.get("/{invoice_id}", response_model=InvoiceOut, dependencies=[ReadRoles])
async def get_invoice(invoice_id: UUID, claims: CurrentClaims, conn: DbConn) -> InvoiceOut:
    return await _load(conn, claims, invoice_id)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
@router.post(
    "", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED, dependencies=[Writers]
)
async def create_invoice(body: InvoiceCreate, claims: CurrentClaims, conn: DbConn) -> InvoiceOut:
    """Create a quote and its lines in one transaction; `pay_now` also moves it to paid.

    If anything fails (including insufficient stock on pay_now), nothing is kept: no invoice,
    no lines, no document number used. `pay_now` needs a payment method; a quote has none.
    """
    _refuse_override_unless_owner(claims, body.tax_rate_bps)
    _check_payment_method(paying=body.pay_now, payment_method=body.payment_method)

    customer = await _customer(conn, claims, body.customer_id)
    default_rate = await _default_rate(conn, body.tax_rate_bps)
    quote, today = await _price(conn, claims, customer, body.items, default_rate, body.tax_rate_bps)

    due_date = body.due_date or due_date_for(customer and customer["payment_terms"], today)
    cur = await conn.execute(
        "insert into public.invoices (tenant_id, customer_id, customer_name, source, "
        "subtotal_cents, tax_rate_bps, tax_cents, total_cents, due_date, notes) "
        "values (%s, %s, %s, 'admin', %s, %s, %s, %s, %s, %s) returning id",
        (
            claims.tenant_id,
            body.customer_id,
            customer_display_name(customer),
            quote.subtotal_cents,
            quote.tax_rate_bps,
            quote.tax_cents,
            quote.total_cents,
            due_date,
            body.notes,
        ),
    )
    (invoice_id,) = await cur.fetchone()
    await _insert_lines(conn, claims, invoice_id, quote)

    if body.pay_now:
        # Paid in full now; the database assigns the invoice number and decrements stock (or
        # refuses: 409, and the payment goes too).
        await _take_balance(conn, claims, invoice_id, body.payment_method)
        await conn.execute(
            "update public.invoices set order_status = 'paid', "
            "payment_method = %s::public.payment_method where id = %s and tenant_id = %s",
            (body.payment_method, invoice_id, claims.tenant_id),
        )
    return await _load(conn, claims, invoice_id)


@router.post("/preview", response_model=InvoicePreviewOut, dependencies=[Writers])
async def preview_invoice(
    body: InvoicePreview, claims: CurrentClaims, conn: DbConn
) -> InvoicePreviewOut:
    """Price lines exactly as create would, and list any stock shortages. Writes nothing and
    uses no document number. The tax override is owner-only here too."""
    _refuse_override_unless_owner(claims, body.tax_rate_bps)
    customer = await _customer(conn, claims, body.customer_id)
    default_rate = await _default_rate(conn, body.tax_rate_bps)
    return await _preview(conn, claims, customer, body.items, default_rate, body.tax_rate_bps)


@router.put("/{invoice_id}/items", response_model=InvoiceOut, dependencies=[Writers])
async def replace_items(
    invoice_id: UUID, body: InvoiceItemsReplace, claims: CurrentClaims, conn: DbConn
) -> InvoiceOut:
    """Replace a quote's lines and recompute its totals in one transaction. The stored tax
    rate snapshot is kept (an owner override stays in force)."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select order_status::text as order_status, customer_id, tax_rate_bps, source, "
            "production_started_at from public.invoices where id = %s and tenant_id = %s "
            "for update",
            (invoice_id, claims.tenant_id),
        )
        invoice = await cur.fetchone()
    if invoice is None:
        raise NotFoundError("Invoice not found")
    if invoice["order_status"] != OrderStatus.quote:
        raise ConflictError("only quotes can be edited")
    _refuse_website_order(invoice)
    _refuse_if_in_production(invoice)

    customer = None
    if invoice["customer_id"] is not None:
        customer = await _customer(conn, claims, invoice["customer_id"])
    quote, _ = await _price(conn, claims, customer, body.items, invoice["tax_rate_bps"], None)

    await conn.execute(
        "delete from public.invoice_line_items where invoice_id = %s and tenant_id = %s",
        (invoice_id, claims.tenant_id),
    )
    await _insert_lines(conn, claims, invoice_id, quote)
    await conn.execute(
        "update public.invoices set subtotal_cents = %s, tax_rate_bps = %s, tax_cents = %s, "
        "total_cents = %s where id = %s and tenant_id = %s",
        (
            quote.subtotal_cents,
            quote.tax_rate_bps,
            quote.tax_cents,
            quote.total_cents,
            invoice_id,
            claims.tenant_id,
        ),
    )
    return await _load(conn, claims, invoice_id)


@router.post(
    "/{invoice_id}/items/preview", response_model=InvoicePreviewOut, dependencies=[Writers]
)
async def preview_items(
    invoice_id: UUID, body: InvoiceItemsReplace, claims: CurrentClaims, conn: DbConn
) -> InvoicePreviewOut:
    """Price replacement lines for a quote as PUT /items would (its customer, its stored tax
    rate). Writes nothing."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select order_status::text as order_status, customer_id, tax_rate_bps, source, "
            "production_started_at from public.invoices where id = %s and tenant_id = %s",
            (invoice_id, claims.tenant_id),
        )
        invoice = await cur.fetchone()
    if invoice is None:
        raise NotFoundError("Invoice not found")
    if invoice["order_status"] != OrderStatus.quote:
        raise ConflictError("only quotes can be edited")
    _refuse_website_order(invoice)
    _refuse_if_in_production(invoice)
    customer = await _customer(conn, claims, invoice["customer_id"])
    return await _preview(conn, claims, customer, body.items, invoice["tax_rate_bps"], None)


WEBSITE_ORDER_LOCKED = (
    "Website orders can't be edited: the shopper agreed to these lines and this total. "
    "Cancel it and ask the shopper to order again."
)


def _refuse_website_order(row: dict[str, Any]) -> None:
    """A storefront order's lines (and personalization answers) stay as the shopper placed
    them. Revisited in Slice C for the answers only."""
    if row["source"] == "storefront":
        raise ConflictError(WEBSITE_ORDER_LOCKED)


IN_PRODUCTION_LOCKED = "Production has started, so the items can't change."


def _refuse_if_in_production(row: dict[str, Any]) -> None:
    """Same message as the database guard (invoice_line_items_quote_only_guard)."""
    if row["production_started_at"] is not None:
        raise ConflictError(IN_PRODUCTION_LOCKED)


def _refuse_if_closed(row: dict[str, Any]) -> None:
    """Same messages as the database guard (invoices_08_closed_guard)."""
    if row["cancelled_at"] is not None:
        raise ConflictError("This invoice is cancelled, so its status can't change.")
    if row["refunded_at"] is not None:
        raise ConflictError("This invoice is refunded, so its status can't change.")


@router.post("/{invoice_id}/status", response_model=InvoiceOut, dependencies=[Writers])
async def change_status(
    invoice_id: UUID,
    body: InvoiceStatusChange,
    claims: CurrentClaims,
    conn: DbConn,
    pool: Pool,
    gateway: Gateway,
) -> InvoiceOut:
    """Move the status forward (skips allowed). Leaving quote is the payment, so it needs a
    payment method and records a payment for whatever is still due; any other move refuses
    one. A quote whose production has started becomes in production rather than paid. The
    database enforces the order and, when an invoice leaves quote, assigns its number and
    decrements stock; its messages pass through (backward: 422; insufficient stock: 409, with
    nothing changed). Cancelled or refunded invoices keep their status (409)."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select order_status::text as order_status, refunded_at, cancelled_at "
            "from public.invoices where id = %s and tenant_id = %s for update",
            (invoice_id, claims.tenant_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Invoice not found")
    if body.status != row["order_status"]:
        _refuse_if_closed(row)
    paying = row["order_status"] == OrderStatus.quote and body.status != OrderStatus.quote
    _check_payment_method(paying=paying, payment_method=body.payment_method)
    if paying:
        await _close_card_sessions(pool, gateway, claims, invoice_id)
        await _take_balance(conn, claims, invoice_id, body.payment_method)
    await conn.execute(
        "update public.invoices set order_status = %s::public.order_status, "
        "payment_method = coalesce(%s::public.payment_method, payment_method) "
        "where id = %s and tenant_id = %s",
        (body.status.value, body.payment_method, invoice_id, claims.tenant_id),
    )
    return await _load(conn, claims, invoice_id)


@router.patch("/{invoice_id}", response_model=InvoiceOut, dependencies=[Writers])
async def update_invoice(
    invoice_id: UUID, body: InvoiceUpdate, claims: CurrentClaims, conn: DbConn
) -> InvoiceOut:
    """Edit the notes (any status), the due date (quotes only) or, for the owner, the payment
    method of a paid invoice. Money, lines and status have their own endpoints."""
    changes = patch_changes(body, INVOICE_NULLABLE_FIELDS)
    if "payment_method" in changes and claims.app_role != "owner":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "only the owner can change the payment method"
        )
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select order_status::text as order_status from public.invoices "
            "where id = %s and tenant_id = %s for update",
            (invoice_id, claims.tenant_id),
        )
        row = await cur.fetchone()
        if row is None:
            raise NotFoundError("Invoice not found")
        is_quote = row["order_status"] == OrderStatus.quote
        if "due_date" in changes and not is_quote:
            raise ConflictError("The due date can only change while the invoice is a quote.")
        if "payment_method" in changes and is_quote:
            raise ConflictError("A quote has no payment method; it is recorded when it is paid.")
        query = update_statement(
            "invoices",
            changes,
            InvoiceUpdate,
            "id",
            casts={"payment_method": "public.payment_method"},
        )
        await cur.execute(query, {**changes, "key": invoice_id, "tenant_id": claims.tenant_id})
        if "payment_method" in changes:
            # Paid in one payment: the ledger row is corrected too, so the two agree. Paid in
            # several, the invoice's method is only a summary and the rows keep theirs.
            await cur.execute(
                "update public.payments set method = %(m)s::public.payment_method "
                "where invoice_id = %(i)s and tenant_id = %(t)s and provider = 'manual' "
                "and kind = 'payment' and status = 'succeeded' "
                "and (select count(*) from public.payments p where p.invoice_id = %(i)s "
                "     and p.kind = 'payment' and p.status = 'succeeded') = 1",
                {"m": changes["payment_method"], "i": invoice_id, "t": claims.tenant_id},
            )
    return await _load(conn, claims, invoice_id)


async def _lock_for_owner_action(
    conn: AsyncConnection, claims: TokenClaims, invoice_id: UUID
) -> dict[str, Any]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select order_status::text as order_status, paid_at, refunded_at, cancelled_at, "
            "payment_method::text as payment_method, production_started_at, "
            "public.invoice_amount_paid(id) as amount_paid_cents, "
            "exists (select 1 from public.payments p where p.invoice_id = i.id "
            "        and p.provider = 'stripe' and p.status = 'succeeded') as has_card_payment, "
            "(select coalesce(sum(case when p.kind = 'payment' then p.amount_cents "
            "        else -p.amount_cents end), 0) from public.payments p "
            " where p.invoice_id = i.id and p.provider = 'stripe' and p.status = 'succeeded') "
            "  as card_net_cents "
            "from public.invoices i where id = %s and tenant_id = %s for update",
            (invoice_id, claims.tenant_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Invoice not found")
    return row


CARD_REFUNDS_IN_STRIPE = "This invoice was paid by card online. Refund it in Stripe."


@router.post("/{invoice_id}/refund", response_model=InvoiceOut, dependencies=[OwnerOnly])
async def refund_invoice(invoice_id: UUID, claims: CurrentClaims, conn: DbConn) -> InvoiceOut:
    """Refund everything received (no restock in v1): a refund row for the net amount paid.

    - A paid invoice is also marked refunded. The status enum is unchanged.
    - A quote holding a deposit stays an open quote with nothing held, so it can then be
      cancelled (a quote holding money can't be).
    Card payments taken online are refunded in Stripe (Slice B2), never here."""
    row = await _lock_for_owner_action(conn, claims, invoice_id)
    if row["cancelled_at"] is not None:
        raise ConflictError("A cancelled invoice can't be refunded.")
    if row["refunded_at"] is not None:
        raise ConflictError("this invoice is already refunded")
    deposit = row["paid_at"] is None and row["amount_paid_cents"] > 0
    if row["paid_at"] is None and not deposit:
        raise ConflictError("only paid invoices can be refunded")
    if row["has_card_payment"]:
        raise ConflictError(CARD_REFUNDS_IN_STRIPE)
    if row["amount_paid_cents"] > 0:
        await _insert_payment(
            conn,
            claims,
            invoice_id,
            "refund",
            row["amount_paid_cents"],
            await _refund_method(conn, claims, invoice_id),
            None,
        )
    if not deposit:
        await conn.execute(
            "update public.invoices set refunded_at = now() where id = %s and tenant_id = %s",
            (invoice_id, claims.tenant_id),
        )
    return await _load(conn, claims, invoice_id)


async def _refund_method(
    conn: AsyncConnection, claims: TokenClaims, invoice_id: UUID
) -> PaymentMethod:
    """The way the money came in, when it all came in one way; otherwise 'other'."""
    cur = await conn.execute(
        "select case when count(distinct method) = 1 then min(method::text) else 'other' end "
        "from public.payments where invoice_id = %s and tenant_id = %s "
        "and kind = 'payment' and status = 'succeeded'",
        (invoice_id, claims.tenant_id),
    )
    (method,) = await cur.fetchone()
    return PaymentMethod(method or "other")


@router.post("/{invoice_id}/cancel", response_model=InvoiceOut, dependencies=[OwnerOnly])
async def cancel_invoice(invoice_id: UUID, claims: CurrentClaims, conn: DbConn) -> InvoiceOut:
    """Mark an invoice cancelled (no restock in v1). The status enum is unchanged."""
    row = await _lock_for_owner_action(conn, claims, invoice_id)
    if row["cancelled_at"] is not None:
        raise ConflictError("this invoice is already cancelled")
    if row["refunded_at"] is not None:
        raise ConflictError("A refunded invoice can't be cancelled.")
    if row["card_net_cents"] > 0:
        raise ConflictError(CARD_CANCEL_REFUSED)  # the database refuses it too
    if row["order_status"] == OrderStatus.quote and row["amount_paid_cents"] > 0:
        raise ConflictError(DEPOSIT_HELD)  # the database refuses it too
    await conn.execute(
        "update public.invoices set cancelled_at = now() where id = %s and tenant_id = %s",
        (invoice_id, claims.tenant_id),
    )
    return await _load(conn, claims, invoice_id)


DEPOSIT_HELD = "This quote holds a deposit. Refund it before cancelling."
# Card money taken online is in the shop's Stripe account: it is refunded there first; the
# refund webhook records it, then cancel works as before.
CARD_CANCEL_REFUSED = "Refund this payment in Stripe first."


# ---------------------------------------------------------------------------
# Payments and deposits (Slice B1)
# ---------------------------------------------------------------------------
@router.get("/{invoice_id}/payments", response_model=list[PaymentOut], dependencies=[ReadRoles])
async def list_payments(invoice_id: UUID, claims: CurrentClaims, conn: DbConn) -> list[PaymentOut]:
    """The invoice's ledger, oldest first. The CPA reads it too (D15)."""
    invoice = await _load(conn, claims, invoice_id)
    return invoice.payments or []


async def _lock_quote_for_payment(
    conn: AsyncConnection, claims: TokenClaims, invoice_id: UUID
) -> dict[str, Any]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select order_status::text as order_status, cancelled_at, refunded_at, "
            "production_started_at, public.invoice_balance(id) as balance_cents, "
            "public.invoice_amount_paid(id) as amount_paid_cents "
            "from public.invoices where id = %s and tenant_id = %s for update",
            (invoice_id, claims.tenant_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Invoice not found")
    if row["cancelled_at"] is not None:
        raise ConflictError("This invoice is cancelled, so it can't take payments.")
    if row["refunded_at"] is not None:
        raise ConflictError("This invoice is refunded, so it can't take payments.")
    if row["order_status"] != OrderStatus.quote:
        raise ConflictError("This invoice is already paid.")
    return row


@router.post(
    "/{invoice_id}/payments",
    response_model=InvoiceOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Writers],
)
async def record_payment(
    invoice_id: UUID,
    body: PaymentCreate,
    claims: CurrentClaims,
    conn: DbConn,
    pool: Pool,
    gateway: Gateway,
) -> InvoiceOut:
    """Record a payment taken in person or by transfer: a deposit, or the rest. When it brings
    the balance to zero the invoice becomes paid (in production if production had started)
    and stock goes down. If stock is short the payment is still kept and the invoice stays a
    quote with `stock_short` true; complete it with POST /settle after restocking. An open card
    Checkout Session for the order is expired at Stripe first (409 if it can't be)."""
    await _close_card_sessions(pool, gateway, claims, invoice_id)
    row = await _lock_quote_for_payment(conn, claims, invoice_id)
    if row["balance_cents"] <= 0:
        raise ConflictError("Nothing is due on this invoice.")
    if body.amount_cents > row["balance_cents"]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"That is more than the {row['balance_cents'] / 100:.2f} still due.",
        )
    await _insert_payment(
        conn, claims, invoice_id, "payment", body.amount_cents, body.method, body.note
    )
    await conn.execute("select public.invoice_settle(%s)", (invoice_id,))
    return await _load(conn, claims, invoice_id)


@router.post(
    "/{invoice_id}/payments/{payment_id}/void", response_model=InvoiceOut, dependencies=[OwnerOnly]
)
async def void_payment(
    invoice_id: UUID, payment_id: UUID, claims: CurrentClaims, conn: DbConn
) -> InvoiceOut:
    """Void a payment or refund recorded by mistake, while the invoice is still a quote. It
    stays in the ledger, marked voided, and counts for nothing."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select p.status, p.provider, i.order_status::text as order_status "
            "from public.payments p join public.invoices i on i.id = p.invoice_id "
            "where p.id = %s and p.invoice_id = %s and p.tenant_id = %s for update of p",
            (payment_id, invoice_id, claims.tenant_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Payment not found")
    if row["provider"] != "manual":
        raise ConflictError("Card payments are managed in Stripe.")
    if row["status"] != "succeeded":
        raise ConflictError("Only a recorded payment can be voided.")
    if row["order_status"] != OrderStatus.quote:
        raise ConflictError(
            "This invoice is paid, so its payments can't be voided. Refund it instead."
        )
    await conn.execute(
        "update public.payments set status = 'voided' where id = %s and tenant_id = %s",
        (payment_id, claims.tenant_id),
    )
    return await _load(conn, claims, invoice_id)


@router.post("/{invoice_id}/start-production", response_model=InvoiceOut, dependencies=[Writers])
async def start_production(invoice_id: UUID, claims: CurrentClaims, conn: DbConn) -> InvoiceOut:
    """Mark that work has started on a quote holding a deposit. It can't be undone: from now
    on the lines are locked, and paying the rest makes it in production rather than paid."""
    row = await _lock_quote_for_payment(conn, claims, invoice_id)
    if row["production_started_at"] is not None:
        raise ConflictError("Production has already started.")
    if row["amount_paid_cents"] <= 0:
        raise ConflictError("Record a deposit before starting production.")
    await conn.execute(
        "update public.invoices set production_started_at = now() where id = %s and tenant_id = %s",
        (invoice_id, claims.tenant_id),
    )
    return await _load(conn, claims, invoice_id)


@router.post("/{invoice_id}/settle", response_model=InvoiceOut, dependencies=[Writers])
async def settle_invoice(invoice_id: UUID, claims: CurrentClaims, conn: DbConn) -> InvoiceOut:
    """Complete a quote that is paid in full but was short of stock, once stock is back."""
    row = await _lock_quote_for_payment(conn, claims, invoice_id)
    if row["balance_cents"] > 0:
        raise ConflictError("This invoice isn't paid in full yet.")
    cur = await conn.execute("select public.invoice_settle(%s)", (invoice_id,))
    (outcome,) = await cur.fetchone()
    if outcome == "stock_short":
        async with conn.cursor(row_factory=dict_row) as lines:
            await lines.execute(
                "select product_id, qty from public.invoice_line_items "
                "where invoice_id = %s and tenant_id = %s and product_id is not null",
                (invoice_id, claims.tenant_id),
            )
            items = [
                LineItemIn(product_id=r["product_id"], qty=r["qty"]) for r in await lines.fetchall()
            ]
        short = await _stock_shortages(conn, claims, items)
        raise ConflictError(
            " ".join(
                f"Not enough stock for SKU {s.sku}: {s.available} available, {s.needed} needed."
                for s in short
            )
            or "Not enough stock to complete this order."
        )
    return await _load(conn, claims, invoice_id)


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[OwnerOnly])
async def delete_invoice(invoice_id: UUID, claims: CurrentClaims, conn: DbConn) -> Response:
    """Delete a quote and its lines. Anything past quote is refused by the database with
    'only quotes can be deleted; cancel or refund this invoice instead' (403)."""
    cur = await conn.execute(
        "delete from public.invoices where id = %s and tenant_id = %s returning id",
        (invoice_id, claims.tenant_id),
    )
    if await cur.fetchone() is None:
        raise NotFoundError("Invoice not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
