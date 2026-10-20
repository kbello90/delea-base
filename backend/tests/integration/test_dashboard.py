"""Dashboard summary: role matrix through the API, and every formula checked against rows
inserted inside a rolled-back transaction (so nothing is left behind, counters included).

The numbering trigger always stamps paid_at = now(), so the formula test disables it inside
its own transaction (as the table owner) to insert backdated paid invoices. The DDL is
rolled back with everything else.
"""

import uuid
from datetime import timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.security import TokenClaims
from app.db.session import apply_user_scope
from app.services.dashboard import fetch_summary

from .support import TENANT_A, TENANT_B, TestUser, assert_access

pytestmark = pytest.mark.integration

SUMMARY = "/api/v1/dashboard/summary"


def test_role_matrix(client: TestClient, users: dict[str, TestUser]) -> None:
    assert_access(client, users, "GET", SUMMARY, {"owner_a": 200, "owner_b": 200})


def test_api_matches_the_service_and_is_per_tenant(
    client: TestClient, users: dict[str, TestUser]
) -> None:
    a = client.get(SUMMARY, headers=users["owner_a"].headers).json()
    b = client.get(SUMMARY, headers=users["owner_b"].headers).json()
    assert a["currency_unit"] == "cents" and a["period_basis"] == "UTC"
    for period in ("mtd", "ytd"):
        for summary in (a, b):
            p = summary[period]
            assert p["net_profit_cents"] == p["revenue_cents"] - p["expenses_cents"]
    # Seed: A has one paid invoice (Sarah, subtotal 9800, tax 784); B one (32000, tax 1760).
    assert a["ytd"]["revenue_cents"] >= 9800 and b["ytd"]["revenue_cents"] >= 32000
    assert a["ytd"]["revenue_cents"] != b["ytd"]["revenue_cents"]
    assert a["low_stock_products"] == 1  # Engagement Ring: 2 < 3
    assert a["low_stock_materials"] == 1  # plating solution: 3 < 5
    assert a["invoice_counts"]["quote"] == 1 and a["invoice_counts"]["paid"] == 1


@pytest.mark.anyio
async def test_formulas(settings: Settings) -> None:
    owner = TokenClaims(sub=str(uuid.uuid4()), tenant_id=TENANT_A, app_role="owner")
    async with await psycopg.AsyncConnection.connect(
        settings.database_url, autocommit=True
    ) as aconn:
        async with aconn.transaction(force_rollback=True):
            # As the table owner: let the test set paid_at itself (rolled back with the rest).
            await aconn.execute("alter table public.invoices disable trigger invoices_30_numbering")
            now = (await (await aconn.execute("select now()")).fetchone())[0]
            today = (
                await (await aconn.execute("select (now() at time zone 'UTC')::date")).fetchone()
            )[0]

            # Baseline and final reading both go through the owner's user session (RLS on),
            # exactly like the endpoint. Tenant A's rows are inserted as that owner too.
            await apply_user_scope(aconn, owner)
            before = await fetch_summary(aconn, TENANT_A)
            month_start = before["month_start"]
            year_start = before["year_start"]
            last_month = month_start - timedelta(days=1)  # previous month (maybe last year)
            last_year = year_start - timedelta(days=1)

            rows = _Rows(aconn)
            await rows.invoice(TENANT_A, 10000, 800, "paid", now)
            await rows.invoice(TENANT_A, 20000, 1600, "shipped", last_month)
            await rows.invoice(TENANT_A, 40000, 3200, "delivered", last_year)
            await rows.invoice(TENANT_A, 80000, 6400, "paid", now, refunded=True)
            await rows.invoice(TENANT_A, 160000, 12800, "paid", now, refunded=True, cancelled=True)
            await rows.invoice(TENANT_A, 320000, 25600, "quote")
            await rows.expense(TENANT_A, today, 1000)
            await rows.expense(TENANT_A, today, 2000, deductible=False)
            await rows.expense(TENANT_A, last_month.date(), 4000)
            await rows.expense(TENANT_A, last_year.date(), 8000)
            await rows.expense(TENANT_A, today + timedelta(days=40), 16000)  # future: no
            await aconn.execute(
                "insert into public.products (tenant_id, sku, name, metal, price_cents, "
                "stock_qty, low_stock_threshold) values "
                "(%s, 'IT-LOW', 'IT low', 'other', 1, 1, 3), "
                "(%s, 'IT-EDGE', 'IT edge', 'other', 1, 3, 3)",
                (TENANT_A, TENANT_A),
            )
            await aconn.execute(
                "insert into public.materials (tenant_id, name, unit, unit_cost_cents, "
                "qty_on_hand, reorder_threshold) values (%s, 'IT-mat', 'g', 1, 1, 2)",
                (TENANT_A,),
            )

            # Tenant B's rows arrive after the baseline, so any leak would show in the deltas.
            await aconn.execute("reset role")
            await rows.invoice(TENANT_B, 640000, 51200, "paid", now)
            await rows.expense(TENANT_B, today, 32000)
            await aconn.execute(
                "insert into public.products (tenant_id, sku, name, metal, price_cents, "
                "stock_qty, low_stock_threshold) values (%s, 'IT-B-LOW', 'IT b', 'other', 1, 0, 3)",
                (TENANT_B,),
            )

            await apply_user_scope(aconn, owner)
            after = await fetch_summary(aconn, TENANT_A)

    same_year = last_month.year == month_start.year
    want_mtd = {
        "revenue_cents": 10000,
        "tax_reserve_cents": 800,
        "expenses_cents": 3000,
        "deductible_expenses_cents": 1000,
    }
    want_ytd = {
        "revenue_cents": 10000 + (20000 if same_year else 0),
        "tax_reserve_cents": 800 + (1600 if same_year else 0),
        "expenses_cents": 3000 + (4000 if same_year else 0),
        "deductible_expenses_cents": 1000 + (4000 if same_year else 0),
    }
    for period, want in (("mtd", want_mtd), ("ytd", want_ytd)):
        for key, delta in want.items():
            assert after[period][key] - before[period][key] == delta, (period, key)
        assert after[period]["net_profit_cents"] == (
            after[period]["revenue_cents"] - after[period]["expenses_cents"]
        )

    assert after["low_stock_products"] - before["low_stock_products"] == 1
    assert after["low_stock_materials"] - before["low_stock_materials"] == 1

    counts_delta = {
        k: after["invoice_counts"][k] - before["invoice_counts"][k] for k in after["invoice_counts"]
    }
    assert counts_delta == {
        "quote": 1,
        "paid": 1,
        "in_production": 0,
        "shipped": 1,
        "delivered": 1,
        "refunded": 1,
        "cancelled": 1,
    }


class _Rows:
    """Inserts the test rows on the test's own connection (inside its rolled-back transaction)."""

    def __init__(self, conn: psycopg.AsyncConnection) -> None:
        self.conn = conn

    async def invoice(
        self, tenant, subtotal, tax, status, paid_at=None, refunded=False, cancelled=False
    ) -> None:
        await self.conn.execute(
            """
            insert into public.invoices
              (tenant_id, customer_name, order_status, subtotal_cents, tax_cents, total_cents,
               paid_at, refunded_at, cancelled_at)
            values (%s, %s, %s, %s, %s, %s, %s,
                    case when %s then now() end, case when %s then now() end)
            """,
            (
                tenant,
                f"IT-{uuid.uuid4().hex[:6]}",
                status,
                subtotal,
                tax,
                subtotal + tax,
                paid_at,
                refunded,
                cancelled,
            ),
        )

    async def expense(self, tenant, day, amount, deductible=True) -> None:
        await self.conn.execute(
            "insert into public.expenses (tenant_id, expense_date, description, amount_cents, "
            "is_tax_deductible) values (%s, %s, %s, %s, %s)",
            (tenant, day, f"IT-{uuid.uuid4().hex[:6]}", amount, deductible),
        )
