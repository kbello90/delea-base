"""Expense ledger routes.

Read (list, summary, get, CSV export): owner and cpa. Write: owner only. Staff have no access
(RLS: owner all, cpa select). require_role gives a clear 403 up front, but RLS (user session
as `authenticated`) is the enforcement. Every query also filters on the caller's tenant.

The list, the summary and the CSV share one filter parser (expense_filters), so the totals
always describe exactly the rows the list pages through. Receipts have their own routes
(expense_receipts.py); a deleted expense's receipt file is removed after the delete commits.
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.api.deps import CurrentClaims, DbConn, get_pool, require_role
from app.core import storage
from app.core.csv_export import cents_to_decimal, csv_stream
from app.core.errors import NotFoundError
from app.core.security import TokenClaims
from app.db.session import user_session
from app.db.sql_helpers import like_pattern, patch_changes, update_statement
from app.models.expense import (
    NULLABLE_FIELDS,
    ExpenseCategory,
    ExpenseCreate,
    ExpenseOut,
    ExpenseSummary,
    ExpenseUpdate,
)
from app.services.currency import fetch_currency

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/expenses", tags=["expenses"])

ReadRoles = Depends(require_role("owner", "cpa"))
OwnerOnly = Depends(require_role("owner"))

COLUMNS = (
    "id, tenant_id, expense_date, description, category::text as category, amount_cents, "
    "is_tax_deductible, receipt_path is not null as has_receipt, receipt_content_type, "
    "receipt_size_bytes, receipt_uploaded_at, created_at, updated_at"
)
_CASTS = {"category": "public.expense_category"}


@dataclass(frozen=True)
class ExpenseFilters:
    date_from: date | None
    date_to: date | None
    category: ExpenseCategory | None
    tax_deductible: bool | None
    q: str | None


def expense_filters(
    date_from: date | None = None,
    date_to: date | None = None,
    category: ExpenseCategory | None = None,
    tax_deductible: bool | None = None,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> ExpenseFilters:
    """The one filter parser for list, summary and CSV."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "date_from is after date_to")
    return ExpenseFilters(date_from, date_to, category, tax_deductible, q)


Filters = Annotated[ExpenseFilters, Depends(expense_filters)]

# Inclusive date range on expense_date; exact category; q matches the description.
_WHERE = """
     where tenant_id = %(tenant_id)s
       and (%(date_from)s::date is null or expense_date >= %(date_from)s)
       and (%(date_to)s::date is null or expense_date <= %(date_to)s)
       and (%(category)s::text is null or category::text = %(category)s)
       and (%(deductible)s::boolean is null or is_tax_deductible = %(deductible)s)
       and (%(pattern)s::text is null or description ilike %(pattern)s)
"""


def _params(claims: TokenClaims, f: ExpenseFilters) -> dict[str, Any]:
    return {
        "tenant_id": claims.tenant_id,
        "date_from": f.date_from,
        "date_to": f.date_to,
        "category": f.category.value if f.category else None,
        "deductible": f.tax_deductible,
        "pattern": like_pattern(f.q) if f.q else None,
    }


@router.get("", response_model=list[ExpenseOut], dependencies=[ReadRoles])
async def list_expenses(
    claims: CurrentClaims,
    conn: DbConn,
    filters: Filters,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ExpenseOut]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select {COLUMNS} from public.expenses {_WHERE} "
            "order by expense_date desc, created_at desc, id "
            "limit %(limit)s offset %(offset)s",
            {**_params(claims, filters), "limit": limit, "offset": offset},
        )
        return [ExpenseOut.model_validate(row) for row in await cur.fetchall()]


# Declared before /{expense_id} so "summary" is never parsed as an id.
@router.get("/summary", response_model=ExpenseSummary, dependencies=[ReadRoles])
async def expense_summary(claims: CurrentClaims, conn: DbConn, filters: Filters) -> ExpenseSummary:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select count(*)::int as count, coalesce(sum(amount_cents), 0)::bigint as total_cents, "
            "coalesce(sum(amount_cents) filter (where is_tax_deductible), 0)::bigint "
            f"as deductible_cents from public.expenses {_WHERE}",
            _params(claims, filters),
        )
        row = await cur.fetchone()
    return ExpenseSummary.model_validate(row)


CSV_HEADER = (
    "expense_date",
    "description",
    "category",
    "currency",
    "amount",
    "is_tax_deductible",
    "has_receipt",
)


@router.get("/export.csv", dependencies=[ReadRoles])
async def export_expenses_csv(
    claims: CurrentClaims,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    filters: Filters,
) -> StreamingResponse:
    """Stream the filtered expenses as CSV (no paging). Amounts are decimal strings. Receipts
    appear only as has_receipt: a link would expire before the file is opened.

    The rows are read in their own user session that stays open while the response streams.
    An error after streaming has started can't change the status code: the stream is cut
    short and the error is logged.
    """

    # Read before streaming starts, so a refusal is a proper 403 rather than a cut-off file.
    async with user_session(pool, claims) as conn:
        currency = await fetch_currency(conn)

    async def rows() -> AsyncIterator[tuple]:
        try:
            async with user_session(pool, claims) as conn:
                cur = conn.cursor()
                async for r in cur.stream(
                    "select expense_date, description, category::text, amount_cents, "
                    "is_tax_deductible, receipt_path is not null "
                    f"from public.expenses {_WHERE} order by expense_date, created_at, id",
                    _params(claims, filters),
                ):
                    yield (r[0], r[1], r[2], currency, cents_to_decimal(r[3]), r[4], r[5])
        except Exception:
            logger.exception("Expense CSV export failed mid-stream")
            raise

    return StreamingResponse(
        csv_stream(CSV_HEADER, rows()),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="expenses.csv"'},
    )


@router.get("/{expense_id}", response_model=ExpenseOut, dependencies=[ReadRoles])
async def get_expense(expense_id: UUID, claims: CurrentClaims, conn: DbConn) -> ExpenseOut:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select {COLUMNS} from public.expenses where id = %s and tenant_id = %s",
            (expense_id, claims.tenant_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Expense not found")
    return ExpenseOut.model_validate(row)


@router.post(
    "", response_model=ExpenseOut, status_code=status.HTTP_201_CREATED, dependencies=[OwnerOnly]
)
async def create_expense(body: ExpenseCreate, claims: CurrentClaims, conn: DbConn) -> ExpenseOut:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            insert into public.expenses
              (tenant_id, expense_date, description, category, amount_cents, is_tax_deductible)
            values
              (%(tenant_id)s, %(expense_date)s, %(description)s,
               %(category)s::public.expense_category, %(amount_cents)s, %(is_tax_deductible)s)
            returning {COLUMNS}
            """,
            {**body.model_dump(), "tenant_id": claims.tenant_id},
        )
        row = await cur.fetchone()
    return ExpenseOut.model_validate(row)


@router.patch("/{expense_id}", response_model=ExpenseOut, dependencies=[OwnerOnly])
async def update_expense(
    expense_id: UUID, body: ExpenseUpdate, claims: CurrentClaims, conn: DbConn
) -> ExpenseOut:
    patch_changes(body, NULLABLE_FIELDS)
    changes = body.model_dump(exclude_unset=True)  # keep date values typed
    query = update_statement("expenses", changes, ExpenseUpdate, COLUMNS, casts=_CASTS)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, {**changes, "key": expense_id, "tenant_id": claims.tenant_id})
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Expense not found")
    return ExpenseOut.model_validate(row)


async def remove_receipt_files(paths: list[str]) -> None:
    """Background task (after the transaction committed): delete receipt files; log failures.
    A failure leaves an orphan for the Milestone B orphan sweep (TRD §6.8)."""
    try:
        await storage.remove(storage.RECEIPTS_BUCKET, paths)
    except Exception:
        logger.exception("Could not remove %d receipt file(s)", len(paths))


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[OwnerOnly])
async def delete_expense(
    expense_id: UUID, claims: CurrentClaims, conn: DbConn, background: BackgroundTasks
) -> Response:
    cur = await conn.execute(
        "delete from public.expenses where id = %s and tenant_id = %s returning receipt_path",
        (expense_id, claims.tenant_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Expense not found")
    # The file goes once the delete has committed (the DbConn transaction commits before the
    # response, and tasks run after it).
    if row[0]:
        background.add_task(remove_receipt_files, [row[0]])
    return Response(status_code=status.HTTP_204_NO_CONTENT)
