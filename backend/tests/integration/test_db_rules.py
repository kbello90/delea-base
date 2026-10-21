"""Database rules from migration 20261020001200, exercised through the backend's sessions:
current_tax_rate_bps() and the stock decrement under concurrency."""

import asyncio
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import errors

from app.core.config import Settings
from app.core.errors import map_db_error
from app.core.security import TokenClaims
from app.db.session import apply_user_scope, create_pool, open_pool, user_session

from .support import TENANT_A, TENANT_B

pytestmark = pytest.mark.integration


def claims(tenant: str, app_role: str) -> TokenClaims:
    return TokenClaims(sub=str(uuid.uuid4()), tenant_id=tenant, app_role=app_role)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tenant", "app_role", "rate"),
    [(TENANT_A, "owner", 800), (TENANT_A, "staff", 800), (TENANT_B, "owner", 550)],
)
async def test_tax_rate_for_owner_and_staff(
    settings: Settings, tenant: str, app_role: str, rate: int
) -> None:
    pool = create_pool(settings)
    await open_pool(pool)
    try:
        async with user_session(pool, claims(tenant, app_role)) as conn:
            cur = await conn.execute("select public.current_tax_rate_bps()")
            assert (await cur.fetchone())[0] == rate
    finally:
        await pool.close()


@pytest.mark.anyio
async def test_tax_rate_refused_for_cpa(settings: Settings) -> None:
    pool = create_pool(settings)
    await open_pool(pool)
    try:
        with pytest.raises(errors.InsufficientPrivilege) as info:
            async with user_session(pool, claims(TENANT_A, "cpa")) as conn:
                await conn.execute("select public.current_tax_rate_bps()")
    finally:
        await pool.close()
    mapped = map_db_error(info.value)
    assert (mapped.status_code, mapped.detail) == (403, "only owner or staff can read the tax rate")


# ---------------------------------------------------------------------------
# Two simultaneous sales of the last unit: exactly one succeeds, stock never goes negative.
# ---------------------------------------------------------------------------
@pytest.fixture
def last_unit(db: psycopg.Connection) -> Iterator[dict]:
    """A product with stock 1 and two quotes that each need it. Committed (two sessions must
    see them); removed afterwards, and tenant A's document counters restored."""
    counters = db.execute(
        "select next_quote_number, next_invoice_number from public.company_settings "
        "where tenant_id = %s",
        (TENANT_A,),
    ).fetchone()
    sku = f"IT-LAST-{uuid.uuid4().hex[:6].upper()}"
    product_id = db.execute(
        "insert into public.products (tenant_id, sku, name, metal, price_cents, stock_qty) "
        "values (%s, %s, 'IT last unit', 'other', 1000, 1) returning id",
        (TENANT_A, sku),
    ).fetchone()[0]
    invoices = []
    for n in (1, 2):
        invoice_id = db.execute(
            "insert into public.invoices (tenant_id, customer_name) values (%s, %s) returning id",
            (TENANT_A, f"IT-race-{n}"),
        ).fetchone()[0]
        db.execute(
            "insert into public.invoice_line_items (invoice_id, tenant_id, product_id, "
            "description, qty, unit_price_cents, line_total_cents) "
            "values (%s, %s, %s, 'IT last unit', 1, 1000, 1000)",
            (invoice_id, TENANT_A, product_id),
        )
        invoices.append(invoice_id)
    yield {"sku": sku, "product_id": product_id, "invoices": invoices}
    db.execute("delete from public.invoices where customer_name like 'IT-race-%'")
    db.execute("delete from public.products where id = %s", (product_id,))
    db.execute(
        "update public.company_settings set next_quote_number = %s, next_invoice_number = %s "
        "where tenant_id = %s",
        (*counters, TENANT_A),
    )


async def _wait_until_blocked(settings: Settings, pid: int) -> None:
    async with await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True) as w:
        for _ in range(200):
            cur = await w.execute(
                "select wait_event_type from pg_stat_activity where pid = %s", (pid,)
            )
            row = await cur.fetchone()
            if row and row[0] == "Lock":
                return
            await asyncio.sleep(0.05)
    raise AssertionError("the second sale never waited on the product lock")


@pytest.mark.anyio
async def test_concurrent_sales_cannot_oversell(settings: Settings, db, last_unit: dict) -> None:
    first, second = last_unit["invoices"]
    url = settings.database_url
    async with (
        await psycopg.AsyncConnection.connect(url, autocommit=True) as c1,
        await psycopg.AsyncConnection.connect(url, autocommit=True) as c2,
    ):
        second_result: dict = {}

        async def second_sale() -> None:
            try:
                async with c2.transaction():
                    await apply_user_scope(c2, claims(TENANT_A, "staff"))
                    await c2.execute(
                        "update public.invoices set order_status = 'paid' where id = %s", (second,)
                    )
                second_result["ok"] = True
            except psycopg.Error as exc:
                second_result["error"] = exc

        async with c1.transaction():
            await apply_user_scope(c1, claims(TENANT_A, "owner"))
            await c1.execute(
                "update public.invoices set order_status = 'paid' where id = %s", (first,)
            )
            task = asyncio.create_task(second_sale())
            await _wait_until_blocked(settings, c2.info.backend_pid)
        # c1 committed here; the second sale now sees stock 0.
        await asyncio.wait_for(task, timeout=10)

    assert "error" in second_result, "the second sale of the last unit must fail"
    error = second_result["error"]
    assert isinstance(error, errors.RaiseException)
    mapped = map_db_error(error)
    assert mapped.status_code == 409
    assert (
        mapped.detail == f"insufficient stock for SKU {last_unit['sku']}: 0 available, 1 requested"
    )

    stock = db.execute(
        "select stock_qty from public.products where id = %s", (last_unit["product_id"],)
    ).fetchone()[0]
    statuses = db.execute(
        "select order_status::text from public.invoices where id = any(%s) order by customer_name",
        ([first, second],),
    ).fetchall()
    assert stock == 0
    assert [s for (s,) in statuses] == ["paid", "quote"]
