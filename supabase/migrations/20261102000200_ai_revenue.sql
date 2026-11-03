-- The AI read path follows the dashboard's revenue rules (decision R1, amended 2026-11-02):
-- revenue is subtotal + shipping of invoices paid in the period (cancelled ones excluded),
-- minus the revenue share of every refund given in the period (amount x (subtotal + shipping)
-- / total, rounded half away from zero), whatever month the invoice was paid in; only refunds
-- of paid invoices given on or after they were paid count. Tax is not revenue: the tax
-- reserve is their tax minus the tax share of the same refunds. Periods are UTC.
--
-- Before this, ai.sales_summary summed face totals (tax and shipping in), dated unpaid quotes
-- by their creation date, left out fully refunded invoices and ignored partial refunds, and
-- ai.tax_summary left out refunded invoices and ignored partial refunds. Both now agree with
-- the dashboard: the sum of sales_summary's mtd/ytd revenue is the dashboard's, and
-- tax_summary's revenue_cents per month is the dashboard's trend.
--
-- Same rules as the views they replace: owned by the migration role, not security_invoker,
-- filtered on app.tenant_id (unset matches nothing), select for ai_readonly only. They read
-- payments directly (no function calls, which would run with ai_readonly's rights).

drop view ai.sales_summary;
drop view ai.tax_summary;

-- One row per state: quote, paid, in_production, shipped, delivered, and refunded (an
-- invoice refunded in full, whatever its status). Cancelled invoices are left out (never
-- revenue). total_cents is the face value (tax and shipping in), not revenue.
create view ai.sales_summary as
  with b as (
    select date_trunc('month', now() at time zone 'UTC') at time zone 'UTC' as month_start,
           date_trunc('year', now() at time zone 'UTC') at time zone 'UTC' as year_start
  ),
  inv as (
    select i.id, i.paid_at, i.total_cents, i.subtotal_cents + i.shipping_cents as sale_cents,
           case when i.refunded_at is not null then 'refunded' else i.order_status::text end as state,
           coalesce((select sum(case when p.kind = 'payment' then p.amount_cents else -p.amount_cents end)
                       from public.payments p
                      where p.invoice_id = i.id and p.status = 'succeeded'), 0) as paid_cents
      from public.invoices i
     where i.tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
       and i.cancelled_at is null
  ),
  shares as (
    select r.invoice_id, r.received_at,
           round(r.amount_cents::numeric * inv.sale_cents / inv.total_cents)::bigint as revenue
      from public.payments r
      join inv on inv.id = r.invoice_id
     where r.kind = 'refund' and r.status = 'succeeded'
       and inv.paid_at is not null and r.received_at >= inv.paid_at and inv.total_cents > 0
  )
  select
    inv.state as order_status,
    count(*)::integer as invoice_count,
    sum(inv.total_cents)::bigint as total_cents,
    sum(inv.paid_cents)::bigint as amount_paid_cents,
    -- What is still owed: on quotes only (a deposit lowers it).
    sum(case when inv.state = 'quote' then greatest(inv.total_cents - inv.paid_cents, 0) else 0 end)::bigint
      as balance_due_cents,
    (coalesce(sum(inv.sale_cents) filter (where inv.paid_at >= b.month_start), 0)
      - coalesce(sum((select sum(s.revenue) from shares s
                       where s.invoice_id = inv.id and s.received_at >= b.month_start)), 0))::bigint
      as mtd_revenue_cents,
    (coalesce(sum(inv.sale_cents) filter (where inv.paid_at >= b.year_start), 0)
      - coalesce(sum((select sum(s.revenue) from shares s
                       where s.invoice_id = inv.id and s.received_at >= b.year_start)), 0))::bigint
      as ytd_revenue_cents
  from inv cross join b
  group by inv.state;

-- Per UTC month: what was sold (by paid date) and what refunds took back (by refund date).
-- revenue_cents and tax_reserve_cents are the dashboard's figures for that month.
create view ai.tax_summary as
  with inv as (
    select i.id, i.paid_at, i.subtotal_cents, i.shipping_cents, i.tax_cents, i.total_cents
      from public.invoices i
     where i.tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
       and i.paid_at is not null
       and i.cancelled_at is null
  ),
  sold as (
    select date_trunc('month', inv.paid_at at time zone 'UTC')::date as month,
           count(*)::integer as invoice_count,
           sum(inv.subtotal_cents)::bigint as subtotal_cents,
           sum(inv.shipping_cents)::bigint as shipping_cents,
           sum(inv.tax_cents)::bigint as tax_cents,
           sum(inv.total_cents)::bigint as total_cents
      from inv
     group by 1
  ),
  refunded as (
    select date_trunc('month', r.received_at at time zone 'UTC')::date as month,
           sum(r.amount_cents)::bigint as refunded_cents,
           sum(round(r.amount_cents::numeric * (inv.subtotal_cents + inv.shipping_cents)
                     / inv.total_cents))::bigint as revenue,
           sum(round(r.amount_cents::numeric * inv.tax_cents / inv.total_cents))::bigint as tax
      from public.payments r
      join inv on inv.id = r.invoice_id
     where r.kind = 'refund' and r.status = 'succeeded'
       and r.received_at >= inv.paid_at and inv.total_cents > 0
     group by 1
  )
  select
    coalesce(s.month, r.month) as month,
    coalesce(s.invoice_count, 0) as invoice_count,
    coalesce(s.subtotal_cents, 0) as subtotal_cents,
    coalesce(s.shipping_cents, 0) as shipping_cents,
    coalesce(s.tax_cents, 0) as tax_cents,
    coalesce(s.total_cents, 0) as total_cents,
    coalesce(r.refunded_cents, 0) as refunded_cents,
    (coalesce(s.subtotal_cents + s.shipping_cents, 0) - coalesce(r.revenue, 0))::bigint as revenue_cents,
    (coalesce(s.tax_cents, 0) - coalesce(r.tax, 0))::bigint as tax_reserve_cents
  from sold s
  full join refunded r on r.month = s.month;

revoke all on ai.sales_summary, ai.tax_summary from public, anon, authenticated;
grant select on ai.sales_summary, ai.tax_summary to ai_readonly;
