-- AI assistant read path (Schema §7, TRD §3.2). Read-only is enforced by grants, not prompts.
--
-- Views live in the ai schema, are owned by the migration role, and are NOT security_invoker:
-- they read the base tables with the owner's rights, so ai_readonly needs (and has) no grants
-- on any public table. Each view filters on the tenant set for the current transaction:
--   set local role ai_readonly;
--   select set_config('app.tenant_id', '<tenant uuid from the validated JWT>', true);
-- An unset app.tenant_id matches nothing, so a missing tenant context returns zero rows.
-- Excluded on purpose: company_settings, tenant_members, and customer contact details.
-- Months are bucketed in UTC.

-- Roles are cluster-wide and can outlive a database reset, so create it only if missing.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'ai_readonly') then
    create role ai_readonly nologin noinherit;
  end if;
end
$$;

create schema ai;
revoke all on schema ai from public;
grant usage on schema ai to ai_readonly;

create view ai.inventory_status as
  select
    p.id as product_id,
    p.sku,
    p.name,
    p.metal,
    p.price_cents,
    p.sale_price_cents,
    p.sale_ends_at,
    p.stock_qty,
    p.low_stock_threshold,
    p.stock_qty < p.low_stock_threshold as is_low,
    p.is_published
  from public.products p
  where p.tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid;

create view ai.materials_status as
  select
    m.id as material_id,
    m.name,
    m.unit,
    m.unit_cost_cents,
    m.qty_on_hand,
    m.reorder_threshold,
    m.qty_on_hand < m.reorder_threshold as is_low
  from public.materials m
  where m.tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid;

-- MTD/YTD totals per order_status. Refunded and cancelled invoices are excluded.
create view ai.sales_summary as
  select
    i.order_status,
    count(*)::integer as invoice_count,
    sum(i.total_cents)::bigint as total_cents,
    coalesce(sum(i.total_cents) filter (
      where coalesce(i.paid_at, i.created_at) >= date_trunc('month', now() at time zone 'UTC') at time zone 'UTC'
    ), 0)::bigint as mtd_total_cents,
    coalesce(sum(i.total_cents) filter (
      where coalesce(i.paid_at, i.created_at) >= date_trunc('year', now() at time zone 'UTC') at time zone 'UTC'
    ), 0)::bigint as ytd_total_cents
  from public.invoices i
  where i.tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
    and i.refunded_at is null
    and i.cancelled_at is null
  group by i.order_status;

create view ai.expense_summary as
  select
    date_trunc('month', e.expense_date)::date as month,
    e.category,
    count(*)::integer as expense_count,
    sum(e.amount_cents)::bigint as total_cents,
    coalesce(sum(e.amount_cents) filter (where e.is_tax_deductible), 0)::bigint as deductible_cents
  from public.expenses e
  where e.tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
  group by 1, 2;

-- Tax collected on paid invoices by month (the demo's "tax reserve" figure).
create view ai.tax_summary as
  select
    date_trunc('month', i.paid_at at time zone 'UTC')::date as month,
    count(*)::integer as invoice_count,
    sum(i.subtotal_cents)::bigint as subtotal_cents,
    sum(i.tax_cents)::bigint as tax_cents,
    sum(i.total_cents)::bigint as total_cents
  from public.invoices i
  where i.tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid
    and i.paid_at is not null
    and i.refunded_at is null
    and i.cancelled_at is null
  group by 1;

-- Views are not exposed to API roles.
revoke all on all tables in schema ai from public, anon, authenticated;

-- SELECT on the ai views only. No INSERT/UPDATE/DELETE/EXECUTE grants exist for ai_readonly.
grant select on all tables in schema ai to ai_readonly;
