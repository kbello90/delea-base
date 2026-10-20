"""Expense ledger routes.

Read (list, get, CSV export): owner and cpa. Write: owner only. Staff have no access
(RLS: owner all, cpa select). require_role gives a clear 403 up front, but RLS (user session
as `authenticated`) is the enforcement. Every query also filters on the caller's tenant.
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.api.deps import CurrentClaims, DbConn, get_pool, require_role
from app.core.csv_export import cents_to_decimal, csv_stream
from app.core.errors import NotFoundError
from app.core.security import TokenClaims
from app.db.session import user_session
from app.db.sql_helpers import patch_changes, update_statement
from app.models.expense import NULLABLE_FIELDS, ExpenseCreate, ExpenseOut, ExpenseUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/expenses", tags=["expenses"])

ReadRoles = Depends(require_role("owner", "cpa"))
OwnerOnly = Depends(require_role("owner"))

_COLUMNS = (
    "id, tenant_id, expense_date, description, category, amount_cents, is_tax_deductible, "
    "receipt_url, created_at, updated_at"
)


@dataclass(frozen=True)
class ExpenseFilters:
    date_from: date | None
    date_to: date | None
    category: str | None
    tax_deductible: bool | None


def expense_filters(
    date_from: date | None = None,
    date_to: date | None = None,
    category: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    tax_deductible: bool | None = None,
) -> ExpenseFilters:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "date_from is after date_to")
    return ExpenseFilters(date_from, date_to, category, tax_deductible)


Filters = Annotated[ExpenseFilters, Depends(expense_filters)]

# Inclusive date range on expense_date; category is an exact match.
_WHERE = """
     where tenant_id = %(tenant_id)s
       and (%(date_from)s::date is null or expense_date >= %(date_from)s)
       and (%(date_to)s::date is null or expense_date <= %(date_to)s)
       and (%(category)s::text is null or category = %(category)s)
       and (%(deductible)s::boolean is null or is_tax_deductible = %(deductible)s)
"""


def _params(claims: TokenClaims, f: ExpenseFilters) -> dict[str, Any]:
    return {
        "tenant_id": claims.tenant_id,
        "date_from": f.date_from,
        "date_to": f.date_to,
        "category": f.category,
        "deductible": f.tax_deductible,
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
            f"select {_COLUMNS} from public.expenses {_WHERE} "
            "order by expense_date desc, created_at desc, id "
            "limit %(limit)s offset %(offset)s",
            {**_params(claims, filters), "limit": limit, "offset": offset},
        )
        return [ExpenseOut.model_validate(row) for row in await cur.fetchall()]


CSV_HEADER = (
    "expense_date", "description", "category", "amount", "is_tax_deductible", "receipt_url",
)  # fmt: skip


# Declared before /{expense_id} so "export.csv" is never parsed as an id.
@router.get("/export.csv", dependencies=[ReadRoles])
async def export_expenses_csv(
    claims: CurrentClaims,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
    filters: Filters,
) -> StreamingResponse:
    """Stream the filtered expenses as CSV (no paging). Amounts are decimal strings.

    The rows are read in their own user session that stays open while the response streams.
    An error after streaming has started can't change the status code: the stream is cut
    short and the error is logged.
    """

    async def rows() -> AsyncIterator[tuple]:
        try:
            async with user_session(pool, claims) as conn:
                cur = conn.cursor()
                async for r in cur.stream(
                    "select expense_date, description, category, amount_cents, "
                    f"is_tax_deductible, receipt_url from public.expenses {_WHERE} "
                    "order by expense_date, created_at, id",
                    _params(claims, filters),
                ):
                    yield (r[0], r[1], r[2], cents_to_decimal(r[3]), r[4], r[5])
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
            f"select {_COLUMNS} from public.expenses where id = %s and tenant_id = %s",
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
              (tenant_id, expense_date, description, category, amount_cents,
               is_tax_deductible, receipt_url)
            values
              (%(tenant_id)s, %(expense_date)s, %(description)s, %(category)s,
               %(amount_cents)s, %(is_tax_deductible)s, %(receipt_url)s)
            returning {_COLUMNS}
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
    query = update_statement("expenses", changes, ExpenseUpdate, _COLUMNS)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(query, {**changes, "key": expense_id, "tenant_id": claims.tenant_id})
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("Expense not found")
    return ExpenseOut.model_validate(row)


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[OwnerOnly])
async def delete_expense(expense_id: UUID, claims: CurrentClaims, conn: DbConn) -> Response:
    cur = await conn.execute(
        "delete from public.expenses where id = %s and tenant_id = %s returning id",
        (expense_id, claims.tenant_id),
    )
    if await cur.fetchone() is None:
        raise NotFoundError("Expense not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
