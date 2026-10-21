"""Invoice routes, including order_status transitions (TRD §3.1).

Roles (RLS: invoices owner all, staff select/insert/update, cpa select; line items owner and
staff, writes only while the invoice is a quote):
- read, list, CSV export: owner, staff, cpa (cpa can't read line items: `items` is null)
- create, replace items, change status: owner, staff
- tax-rate override, refund, cancel, delete: owner only (API rules; delete is quotes-only in
  the database too)

Money is never accepted from the client: prices come from the products, totals from
app/services/pricing.py, the tax rate from public.current_tax_rate_bps() (or the owner's
override, stored as the snapshot). Every query also filters on the caller's tenant.
"""

import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any
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
from app.db.sql_helpers import like_pattern
from app.models.invoice import (
    InvoiceCreate,
    InvoiceItemsReplace,
    InvoiceLineOut,
    InvoiceOut,
    InvoiceStatusChange,
    LineItemIn,
    OrderStatus,
)
from app.services.invoices import customer_display_name, due_date_for
from app.services.pricing import (
    CustomerPricing,
    LineRequest,
    PricedQuote,
    PricingError,
    ProductPrice,
    price_quote,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/invoices", tags=["invoices"])

ReadRoles = Depends(require_role("owner", "staff", "cpa"))
Writers = Depends(require_role("owner", "staff"))
OwnerOnly = Depends(require_role("owner"))

_COLUMNS = (
    "id, tenant_id, quote_number, invoice_number, order_status::text as order_status, "
    "customer_id, customer_name, source, subtotal_cents, tax_rate_bps, tax_cents, total_cents, "
    "due_date, notes, paid_at, refunded_at, cancelled_at, created_at, updated_at"
)
_LINE_COLUMNS = "id, product_id, description, qty, unit_price_cents, line_total_cents"


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
        if claims.app_role != "cpa":  # cpa can't read line items (RLS)
            await cur.execute(
                f"select {_LINE_COLUMNS} from public.invoice_line_items "
                "where invoice_id = %s and tenant_id = %s order by id",
                (invoice_id, claims.tenant_id),
            )
            invoice.items = [InvoiceLineOut.model_validate(r) for r in await cur.fetchall()]
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
            "line_total_cents) values (%s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    invoice_id,
                    claims.tenant_id,
                    line.product_id,
                    line.description,
                    line.qty,
                    line.unit_price_cents,
                    line.line_total_cents,
                )
                for line in quote.lines
            ],
        )


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


def invoice_filters(
    status_: Annotated[OrderStatus | None, Query(alias="status")] = None,
    customer_id: UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: Annotated[str | None, Query(min_length=1, max_length=50)] = None,
) -> InvoiceFilters:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "date_from is after date_to")
    return InvoiceFilters(status_, customer_id, date_from, date_to, q)


Filters = Annotated[InvoiceFilters, Depends(invoice_filters)]

# Dates are UTC calendar dates of created_at, inclusive. q matches either document number.
_WHERE = """
     where tenant_id = %(tenant_id)s
       and (%(status)s::text is null or order_status::text = %(status)s)
       and (%(customer_id)s::uuid is null or customer_id = %(customer_id)s)
       and (%(date_from)s::date is null or (created_at at time zone 'UTC')::date >= %(date_from)s)
       and (%(date_to)s::date is null or (created_at at time zone 'UTC')::date <= %(date_to)s)
       and (%(pattern)s::text is null
            or quote_number ilike %(pattern)s or invoice_number ilike %(pattern)s)
"""


def _params(claims: TokenClaims, f: InvoiceFilters) -> dict[str, Any]:
    return {
        "tenant_id": claims.tenant_id,
        "status": f.status.value if f.status else None,
        "customer_id": f.customer_id,
        "date_from": f.date_from,
        "date_to": f.date_to,
        "pattern": like_pattern(f.q) if f.q else None,
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
    "due_date",
    "subtotal",
    "tax_rate_bps",
    "tax",
    "total",
    "refunded_at",
    "cancelled_at",
    "source",
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

    async def rows() -> AsyncIterator[tuple]:
        try:
            async with user_session(pool, claims) as conn:
                cur = conn.cursor()
                async for r in cur.stream(
                    "select quote_number, invoice_number, order_status::text, customer_name, "
                    "created_at, paid_at, due_date, subtotal_cents, tax_rate_bps, tax_cents, "
                    f"total_cents, refunded_at, cancelled_at, source from public.invoices {_WHERE} "
                    "order by created_at, id",
                    _params(claims, filters),
                ):
                    yield (
                        *r[:7],
                        cents_to_decimal(r[7]),
                        r[8],
                        cents_to_decimal(r[9]),
                        cents_to_decimal(r[10]),
                        *r[11:],
                    )
        except Exception:
            logger.exception("Invoice CSV export failed mid-stream")
            raise

    return StreamingResponse(
        csv_stream(CSV_HEADER, rows()),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="invoices.csv"'},
    )


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
    no lines, no document number used.
    """
    if body.tax_rate_bps is not None and claims.app_role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only the owner can override the tax rate")

    customer = await _customer(conn, claims, body.customer_id)
    default_rate = 0
    if body.tax_rate_bps is None:
        cur = await conn.execute("select public.current_tax_rate_bps()")
        (default_rate,) = await cur.fetchone()
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
        # The database assigns the invoice number and decrements stock (or refuses: 409).
        await conn.execute(
            "update public.invoices set order_status = 'paid' where id = %s and tenant_id = %s",
            (invoice_id, claims.tenant_id),
        )
    return await _load(conn, claims, invoice_id)


@router.put("/{invoice_id}/items", response_model=InvoiceOut, dependencies=[Writers])
async def replace_items(
    invoice_id: UUID, body: InvoiceItemsReplace, claims: CurrentClaims, conn: DbConn
) -> InvoiceOut:
    """Replace a quote's lines and recompute its totals in one transaction. The stored tax
    rate snapshot is kept (an owner override stays in force)."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select order_status::text as order_status, customer_id, tax_rate_bps "
            "from public.invoices where id = %s and tenant_id = %s for update",
            (invoice_id, claims.tenant_id),
        )
        invoice = await cur.fetchone()
    if invoice is None:
        raise NotFoundError("Invoice not found")
    if invoice["order_status"] != OrderStatus.quote:
        raise ConflictError("only quotes can be edited")

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


@router.post("/{invoice_id}/status", response_model=InvoiceOut, dependencies=[Writers])
async def change_status(
    invoice_id: UUID, body: InvoiceStatusChange, claims: CurrentClaims, conn: DbConn
) -> InvoiceOut:
    """Move the status forward (skips allowed). The database enforces the order and, when an
    invoice leaves quote, assigns its number and decrements stock; its messages pass through
    (backward: 422; insufficient stock: 409, with nothing changed)."""
    cur = await conn.execute(
        "update public.invoices set order_status = %s::public.order_status "
        "where id = %s and tenant_id = %s returning id",
        (body.status.value, invoice_id, claims.tenant_id),
    )
    if await cur.fetchone() is None:
        raise NotFoundError("Invoice not found")
    return await _load(conn, claims, invoice_id)


async def _lock_for_owner_action(
    conn: AsyncConnection, claims: TokenClaims, invoice_id: UUID
) -> dict[str, Any]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select paid_at, refunded_at, cancelled_at from public.invoices "
            "where id = %s and tenant_id = %s for update",
            (invoice_id, claims.tenant_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Invoice not found")
    return row


@router.post("/{invoice_id}/refund", response_model=InvoiceOut, dependencies=[OwnerOnly])
async def refund_invoice(invoice_id: UUID, claims: CurrentClaims, conn: DbConn) -> InvoiceOut:
    """Mark a paid invoice refunded (no restock in v1). The status enum is unchanged."""
    row = await _lock_for_owner_action(conn, claims, invoice_id)
    if row["paid_at"] is None:
        raise ConflictError("only paid invoices can be refunded")
    if row["refunded_at"] is not None:
        raise ConflictError("this invoice is already refunded")
    await conn.execute(
        "update public.invoices set refunded_at = now() where id = %s and tenant_id = %s",
        (invoice_id, claims.tenant_id),
    )
    return await _load(conn, claims, invoice_id)


@router.post("/{invoice_id}/cancel", response_model=InvoiceOut, dependencies=[OwnerOnly])
async def cancel_invoice(invoice_id: UUID, claims: CurrentClaims, conn: DbConn) -> InvoiceOut:
    """Mark an invoice cancelled (no restock in v1). The status enum is unchanged."""
    row = await _lock_for_owner_action(conn, claims, invoice_id)
    if row["cancelled_at"] is not None:
        raise ConflictError("this invoice is already cancelled")
    await conn.execute(
        "update public.invoices set cancelled_at = now() where id = %s and tenant_id = %s",
        (invoice_id, claims.tenant_id),
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
