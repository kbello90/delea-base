"""Dashboard summary: one SQL statement, run in the caller's user session (RLS applies).

Approved formulas (all money in integer cents; periods are UTC calendar month / year to date,
since tenants have no timezone setting yet):

- revenue:     sum(subtotal_cents) of invoices paid in the period (paid_at), excluding refunded
               and cancelled invoices. Tax is not revenue.
- tax_reserve: sum(tax_cents) of the same invoices.
- expenses:    sum(amount_cents) of all expenses dated in the period, up to today;
               deductible_expenses counts only is_tax_deductible ones.
- net_profit:  revenue - expenses (pre income tax; tax collected excluded from both).
- low stock:   products with stock_qty < low_stock_threshold; materials with
               qty_on_hand < reorder_threshold.
- invoice counts: by order_status over invoices that are neither refunded nor cancelled, plus
               separate refunded and cancelled counts (both set counts as cancelled).
"""

from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row

SUMMARY_SQL = """
with bounds as (
  select
    date_trunc('month', now() at time zone 'UTC') at time zone 'UTC' as month_start,
    date_trunc('year', now() at time zone 'UTC') at time zone 'UTC' as year_start,
    date_trunc('month', now() at time zone 'UTC')::date as month_start_date,
    date_trunc('year', now() at time zone 'UTC')::date as year_start_date,
    (now() at time zone 'UTC')::date as today
),
sales as (
  select
    coalesce(sum(i.subtotal_cents) filter (where i.paid_at >= b.month_start), 0) as mtd_revenue,
    coalesce(sum(i.subtotal_cents), 0) as ytd_revenue,
    coalesce(sum(i.tax_cents) filter (where i.paid_at >= b.month_start), 0) as mtd_tax,
    coalesce(sum(i.tax_cents), 0) as ytd_tax
  from bounds b
  left join public.invoices i
    on i.tenant_id = %(tenant_id)s
   and i.paid_at >= b.year_start
   and i.refunded_at is null
   and i.cancelled_at is null
),
spend as (
  select
    coalesce(sum(e.amount_cents) filter (where e.expense_date >= b.month_start_date), 0)
      as mtd_expenses,
    coalesce(sum(e.amount_cents), 0) as ytd_expenses,
    coalesce(sum(e.amount_cents) filter (
      where e.expense_date >= b.month_start_date and e.is_tax_deductible), 0)
      as mtd_deductible,
    coalesce(sum(e.amount_cents) filter (where e.is_tax_deductible), 0) as ytd_deductible
  from bounds b
  left join public.expenses e
    on e.tenant_id = %(tenant_id)s
   and e.expense_date >= b.year_start_date
   and e.expense_date <= b.today
),
counts as (
  select
    count(*) filter (where cancelled_at is null and refunded_at is null
                       and order_status = 'quote') as quote,
    count(*) filter (where cancelled_at is null and refunded_at is null
                       and order_status = 'paid') as paid,
    count(*) filter (where cancelled_at is null and refunded_at is null
                       and order_status = 'in_production') as in_production,
    count(*) filter (where cancelled_at is null and refunded_at is null
                       and order_status = 'shipped') as shipped,
    count(*) filter (where cancelled_at is null and refunded_at is null
                       and order_status = 'delivered') as delivered,
    count(*) filter (where refunded_at is not null and cancelled_at is null) as refunded,
    count(*) filter (where cancelled_at is not null) as cancelled
  from public.invoices
  where tenant_id = %(tenant_id)s
)
select
  s.mtd_revenue, s.ytd_revenue, s.mtd_tax, s.ytd_tax,
  x.mtd_expenses, x.ytd_expenses, x.mtd_deductible, x.ytd_deductible,
  (select count(*) from public.products p
    where p.tenant_id = %(tenant_id)s and p.stock_qty < p.low_stock_threshold)
    as low_stock_products,
  (select count(*) from public.materials m
    where m.tenant_id = %(tenant_id)s and m.qty_on_hand < m.reorder_threshold)
    as low_stock_materials,
  c.quote, c.paid, c.in_production, c.shipped, c.delivered, c.refunded, c.cancelled,
  b.month_start, b.year_start
from sales s, spend x, counts c, bounds b
"""


def _period(revenue: int, tax: int, expenses: int, deductible: int) -> dict[str, int]:
    return {
        "revenue_cents": revenue,
        "tax_reserve_cents": tax,
        "expenses_cents": expenses,
        "deductible_expenses_cents": deductible,
        "net_profit_cents": revenue - expenses,
    }


async def fetch_summary(conn: AsyncConnection, tenant_id: str) -> dict[str, Any]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(SUMMARY_SQL, {"tenant_id": tenant_id})
        r = await cur.fetchone()
    assert r is not None  # the aggregate query always returns one row
    return {
        "currency_unit": "cents",
        "period_basis": "UTC",
        "month_start": r["month_start"],
        "year_start": r["year_start"],
        "mtd": _period(r["mtd_revenue"], r["mtd_tax"], r["mtd_expenses"], r["mtd_deductible"]),
        "ytd": _period(r["ytd_revenue"], r["ytd_tax"], r["ytd_expenses"], r["ytd_deductible"]),
        "low_stock_products": r["low_stock_products"],
        "low_stock_materials": r["low_stock_materials"],
        "invoice_counts": {
            status: r[status]
            for status in (
                "quote",
                "paid",
                "in_production",
                "shipped",
                "delivered",
                "refunded",
                "cancelled",
            )
        },
    }
