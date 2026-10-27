"""Dashboard summary: one SQL statement, run in the caller's user session (RLS applies).

Approved formulas (all money in integer cents; periods are UTC calendar month / year to date,
since tenants have no timezone setting yet):

- revenue:     net of refunds. sum(subtotal_cents) of invoices paid in the period (paid_at),
               excluding cancelled invoices, minus the revenue share of every refund given in
               the period (received_at), in whatever month the invoice was paid. A refund's
               share is its amount x subtotal / total, rounded half away from zero, so a full
               refund removes exactly the subtotal. Only refunds of paid invoices count, given
               on or after they were paid: returning a deposit is not a sale undone. Tax is not
               revenue. A refund doesn't restock.
- tax_reserve: sum(tax_cents) of the same invoices, minus the tax share of the same refunds
               (amount x tax / total).
- deposits held: money received on open quotes (not cancelled): customer deposits, a
               liability until the invoice is paid in full. Not revenue. Current, not per
               period.
- expenses:    sum(amount_cents) of all expenses dated in the period, up to today;
               deductible_expenses counts only is_tax_deductible ones.
- net_profit:  revenue - expenses (pre income tax; tax collected excluded from both).
- low stock:   products with stock_qty < low_stock_threshold; materials with
               qty_on_hand < reorder_threshold.
- monthly trend: the last 6 UTC calendar months (oldest first, the current month to date),
               each with revenue and expenses by the same rules as above.
- invoice counts: by order_status over invoices that are neither refunded nor cancelled, plus
               separate refunded and cancelled counts (both set counts as cancelled).
"""

from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row

# Each refund of a paid invoice, given on or after it was paid, with its revenue and tax shares.
REFUND_SHARES = """
    select p.received_at,
           round(p.amount_cents::numeric * i.subtotal_cents / i.total_cents)::bigint as revenue,
           round(p.amount_cents::numeric * i.tax_cents / i.total_cents)::bigint as tax
    from public.payments p
    join public.invoices i on i.id = p.invoice_id and i.tenant_id = p.tenant_id
    where p.tenant_id = %(tenant_id)s
      and p.kind = 'refund'
      and p.status = 'succeeded'
      and i.paid_at is not null
      and p.received_at >= i.paid_at
      and i.cancelled_at is null
      and i.total_cents > 0
"""

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
   and i.cancelled_at is null
),
refunds as (
  select
    coalesce(sum(r.revenue) filter (where r.received_at >= b.month_start), 0) as mtd_revenue,
    coalesce(sum(r.revenue), 0) as ytd_revenue,
    coalesce(sum(r.tax) filter (where r.received_at >= b.month_start), 0) as mtd_tax,
    coalesce(sum(r.tax), 0) as ytd_tax
  from bounds b
  left join ({REFUND_SHARES}) r
    on r.received_at >= b.year_start
),
deposits as (
  select coalesce(sum(d.held), 0)::bigint as held_cents, count(*) as held_count
  from (
    select public.invoice_amount_paid(i.id) as held
    from public.invoices i
    where i.tenant_id = %(tenant_id)s
      and i.order_status = 'quote'
      and i.cancelled_at is null
  ) d
  where d.held > 0
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
  s.mtd_revenue - r.mtd_revenue as mtd_revenue, s.ytd_revenue - r.ytd_revenue as ytd_revenue,
  s.mtd_tax - r.mtd_tax as mtd_tax, s.ytd_tax - r.ytd_tax as ytd_tax,
  x.mtd_expenses, x.ytd_expenses, x.mtd_deductible, x.ytd_deductible,
  (select count(*) from public.products p
    where p.tenant_id = %(tenant_id)s and p.stock_qty < p.low_stock_threshold)
    as low_stock_products,
  (select count(*) from public.materials m
    where m.tenant_id = %(tenant_id)s and m.qty_on_hand < m.reorder_threshold)
    as low_stock_materials,
  c.quote, c.paid, c.in_production, c.shipped, c.delivered, c.refunded, c.cancelled,
  d.held_cents, d.held_count,
  b.month_start, b.year_start
from sales s, refunds r, spend x, counts c, deposits d, bounds b
""".replace("{REFUND_SHARES}", REFUND_SHARES)


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
        "deposits_held_cents": r["held_cents"],
        "deposits_held_count": r["held_count"],
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


MONTHS_IN_TREND = 6

MONTHLY_SQL = """
with bounds as (
  select
    date_trunc('month', now() at time zone 'UTC') as current_month,
    (now() at time zone 'UTC')::date as today
),
months as (
  select m as month_start, m + interval '1 month' as month_end
  from bounds b,
       generate_series(b.current_month - make_interval(months => %(back)s), b.current_month,
                       interval '1 month') as m
)
select
  m.month_start::date as month_start,
  coalesce((
    select sum(i.subtotal_cents)
    from public.invoices i
    where i.tenant_id = %(tenant_id)s
      and i.paid_at >= m.month_start at time zone 'UTC'
      and i.paid_at < m.month_end at time zone 'UTC'
      and i.cancelled_at is null
  ), 0)::bigint - coalesce((
    select sum(r.revenue)
    from ({REFUND_SHARES}) r
    where r.received_at >= m.month_start at time zone 'UTC'
      and r.received_at < m.month_end at time zone 'UTC'
  ), 0)::bigint as revenue_cents,
  coalesce((
    select sum(e.amount_cents)
    from public.expenses e, bounds b
    where e.tenant_id = %(tenant_id)s
      and e.expense_date >= m.month_start::date
      and e.expense_date < m.month_end::date
      and e.expense_date <= b.today
  ), 0)::bigint as expenses_cents
from months m
order by m.month_start
""".replace("{REFUND_SHARES}", REFUND_SHARES)


async def fetch_monthly(conn: AsyncConnection, tenant_id: str) -> dict[str, Any]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(MONTHLY_SQL, {"tenant_id": tenant_id, "back": MONTHS_IN_TREND - 1})
        rows = await cur.fetchall()
    return {"currency_unit": "cents", "period_basis": "UTC", "months": rows}
